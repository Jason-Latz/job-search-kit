"""Daily apply triage: sort recent digest picks by how much effort the application takes.

Apply to the low-effort roles every day and save the essay ones for focused
application blocks. The digest already says which roles fit; this reads each
open pick's actual application form and splits them:

  quick    no free-response beyond standard fields (name, links, work auth...)
  essay    at least one open-ended answer, required or optional (optional ones are
           worth filling too), or a required cover letter
  unknown  the form could not be read (Workday, iCIMS, company sites...): open it to see
  closed   the board no longer lists the posting

Candidates: digest picks first seen in the last --days days, plus every starred role,
whose tracker status is still new or starred. Forms come from Greenhouse
(?questions=true), Ashby (the job board's public GraphQL) and Lever (the /apply page).

A scheduled Claude task can run this daily and summarize it (see SETUP.md).

  python3 scripts/apply_triage.py              # markdown to stdout, JSON to out/
  python3 scripts/apply_triage.py --dry-run    # do not mark roles as surfaced
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from functools import lru_cache
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
HQ = ROOT.parent  # repo root: applications/ lives beside pipeline/
sys.path.insert(0, str(ROOT / "scripts"))
import ats  # noqa: E402

DB = ROOT / "state" / "jobs.db"
SEEN = ROOT / "state" / "apply-triage.json"
OUT = ROOT / "out" / "apply-triage.json"

# Standard fields: answering them takes seconds, whatever the widget.
TRIVIAL = re.compile(
    r"\b(first|last|full|legal|preferred)\s*name|^name\b|e-?mail|phone|resume|\bcv\b|linkedin|"
    r"github|website|portfolio|\burl\b|twitter|location|\bcity\b|address|zip|postal|"
    r"pronoun|gender|race|ethnic|hispanic|veteran|disabilit|how did you (hear|find)|"
    r"where did you (hear|find)|referr|referred|sponsor|visa|authori[sz]|legally|"
    r"citizen|export control|relocat|start date|available to start|earliest|notice period|"
    r"salary|compensation|pay (range|expectation)|school|universit|college|degree|major|"
    r"\bgpa\b|graduat|current (company|employer|title|role)|years of|how many years|"
    r"time ?zone|acknowledg|consent|privacy|i agree|attest|certif|previously (applied|worked)|"
    r"worked (at|for) .* before|former employee|related to|government|non-?compete|"
    r"in[- ]office|on-?site|hybrid|commut|willing to|are you able|18 years|background check|"
    r"additional information|on-campus|which event|security clearance|"
    r"open to (working|relocat)|work (from|in) (our|the) office|found us|"
    r"when (can|could|would) you (be able to )?start|\bbots?\b|\b\w+(st|nd|rd|th) word\b|"
    r"did you use an ai tool|other (roles|paths|companies|opportunities)|weighing|"
    r"google scholar|publications\s*$|where are you (located|based)",
    re.I)
# A short text box only counts as an essay when the label asks an open question.
OPEN_ENDED = re.compile(
    r"\b(why|describe|tell us|explain|share|walk us|example|proud|motivat|excit|interest|"
    r"what (is|are|was|were|would|do|does|draws|makes)|how (do|would|have|did)|"
    r"accomplish|challenge|project|built|favorite)",
    re.I)
LONG_TYPES = {"textarea", "LongText"}
TEXT_TYPES = LONG_TYPES | {"input_text", "String", "text"}


def clean(label: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", label or "")).replace("✱", "").split())


def classify_field(label: str, ftype: str, required: bool) -> str | None:
    """Return 'essay', 'short' or None (standard field, no effort) for one form field."""
    label = clean(label)
    if re.search(r"cover letter", label, re.I):
        return "essay" if required else None
    if ftype not in TEXT_TYPES or not label or TRIVIAL.search(label):
        return None
    if ftype in LONG_TYPES or OPEN_ENDED.search(label) or len(label) > 90:
        return "essay"
    return "short"


def summarize(fields: list[tuple[str, str, bool]]) -> dict:
    """fields: (label, type, required). Splits into the lists the triage reports."""
    out = {"essays_required": [], "essays_optional": [], "short_required": []}
    for label, ftype, required in fields:
        kind = classify_field(label, ftype, required)
        if kind == "essay":
            out["essays_required" if required else "essays_optional"].append(clean(label)[:160])
        elif kind == "short" and required:
            out["short_required"].append(clean(label)[:160])
    out["tier"] = "essay" if out["essays_required"] or out["essays_optional"] else "quick"
    return out


# --- form readers: each returns (fields, apply_url), or raises Closed / Unreadable ---

class Closed(Exception):
    pass


class Unreadable(Exception):
    pass


def _gh_token_for(company: str) -> str | None:
    try:
        boards = json.loads((ROOT / "data" / "boards.json").read_text())["boards"]
    except (OSError, ValueError, KeyError):
        return None
    for b in boards:
        if b.get("platform") == "greenhouse" and (b.get("company") or "").lower() == company.lower():
            return b["token"]
    return None


def greenhouse(url: str, company: str):
    p = urlparse(url)
    m = re.match(r"/([^/]+)/jobs/(\d+)", p.path)
    if "greenhouse.io" in p.netloc and m:
        token, jid = m.groups()
    else:
        jid = (parse_qs(p.query).get("gh_jid") or [None])[0]
        token = _gh_token_for(company)
        if not (jid and token):
            raise Unreadable("company-hosted Greenhouse page with no known board token")
    r = ats.fetch(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}?questions=true")
    if r.status == 404:
        raise Closed()
    if not r.ok:
        raise Unreadable(f"greenhouse HTTP {r.status}")
    j = r.json()
    fields = []
    for q in j.get("questions") or []:
        types = [f.get("type") for f in q.get("fields") or []]
        # Resume and cover letter offer file-or-paste; their type is not the question.
        ftype = "file" if "input_file" in types else (types[0] if types else "")
        fields.append((q.get("label", ""), ftype, bool(q.get("required"))))
    return fields, j.get("absolute_url") or url


ASHBY_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) "
    "{ jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, "
    "jobPostingId: $jobPostingId) { id applicationForm { sections { fieldEntries "
    "{ ... on FormFieldEntry { field isRequired } } } } } }")


# Ashby's GraphQL answers a burst with {"jobPosting": null}, the same reply a closed
# posting gets: the second run of 2026-11-16 called 35 open roles closed. So Ashby calls
# go one at a time, spaced, with retries, and a null is believed only if the public board no
# longer lists the posting either.
ASHBY_SLOTS = threading.Semaphore(1)


@lru_cache(maxsize=None)
def ashby_board_ids(org: str) -> frozenset | None:
    r = ats.fetch(f"https://api.ashbyhq.com/posting-api/job-board/{quote(org)}")
    if not r.ok:
        return None
    try:
        return frozenset(j.get("id") for j in r.json().get("jobs") or [])
    except ValueError:
        return None


def _ashby_post(org: str, jid: str) -> dict:
    body = json.dumps({"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
                       "variables": {"organizationHostedJobsPageName": org, "jobPostingId": jid}})
    with ASHBY_SLOTS:
        proc = subprocess.run(
            ["curl", "-sS", "-m", "20", "-X", "POST", "-H", "content-type: application/json",
             "-H", f"User-Agent: {ats.USER_AGENT}", "--data-binary", "@-",
             "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"],
            input=body, capture_output=True, text=True, errors="replace")
        time.sleep(0.5)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {}


def ashby(url: str, company: str):
    parts = [unquote(s) for s in urlparse(url).path.split("/") if s]
    if len(parts) < 2:
        raise Unreadable("ashby URL without a posting id")
    org, jid = parts[0], parts[1]
    post = None
    for wait in (0, 5, 15, 30):
        time.sleep(wait)
        post = (_ashby_post(org, jid).get("data") or {}).get("jobPosting")
        if post is not None:
            break
    if post is None:
        listed = ashby_board_ids(org)
        if listed is not None and jid not in listed:
            raise Closed()
        raise Unreadable("Ashby did not return the form (rate limit?)")
    fields = []
    for s in (post.get("applicationForm") or {}).get("sections") or []:
        for e in s.get("fieldEntries") or []:
            f = e.get("field") or {}
            fields.append((f.get("title", ""), f.get("type", ""), bool(e.get("isRequired"))))
    return fields, f"https://jobs.ashbyhq.com/{quote(org)}/{jid}/application"


LEVER_Q = re.compile(r'<li class="application-question[^"]*"[^>]*>(.*?)</li>', re.S)


def lever_fields(page: str) -> list[tuple[str, str, bool]]:
    fields = []
    for m in LEVER_Q.finditer(page):
        b = m.group(1)
        lab = re.search(r'class="application-label[^"]*"[^>]*>(.*?)</div>', b, re.S)
        if "<textarea" in b:
            ftype = "textarea"
        elif 'type="file"' in b:
            ftype = "file"
        elif "<select" in b or 'type="radio"' in b or 'type="checkbox"' in b:
            ftype = "select"
        else:
            ftype = "text"
        fields.append((lab.group(1) if lab else "", ftype, "required" in b))
    return fields


def lever(url: str, company: str):
    apply_url = url.rstrip("/") + ("" if url.rstrip("/").endswith("/apply") else "/apply")
    r = ats.fetch(apply_url, timeout=45)  # the page is ~700 KB
    if r.status == 404:
        raise Closed()
    if not r.ok:
        raise Unreadable(f"lever HTTP {r.status}")
    fields = lever_fields(r.body)
    if not fields:
        raise Unreadable("lever page had no application form")
    return fields, apply_url


def reader_for(url: str):
    host = urlparse(url).netloc
    if "greenhouse.io" in host or "gh_jid=" in url:
        return greenhouse
    if "ashbyhq.com" in host:
        return ashby
    if "lever.co" in host:
        return lever
    return None


def platform_name(url: str) -> str:
    host = urlparse(url).netloc
    for key, name in (("myworkdayjobs", "Workday"), ("icims", "iCIMS"), ("workable", "Workable"),
                      ("rippling", "Rippling"), ("applytojob", "JazzHR"), ("amazon.jobs", "Amazon"),
                      ("smartrecruiters", "SmartRecruiters")):
        if key in url:
            return name
    return host


def triage_one(job: dict) -> dict:
    read = reader_for(job["url"])
    if read is None:
        return {**job, "tier": "unknown", "why_unknown": platform_name(job["url"]), "apply_url": job["url"]}
    try:
        fields, apply_url = read(job["url"], job["company"] or "")
    except Closed:
        return {**job, "tier": "closed", "apply_url": job["url"]}
    except Unreadable as e:
        return {**job, "tier": "unknown", "why_unknown": str(e), "apply_url": job["url"]}
    except Exception as e:  # one odd page must not sink the whole triage
        return {**job, "tier": "unknown", "why_unknown": f"{type(e).__name__}: {e}"[:120],
                "apply_url": job["url"]}
    return {**job, **summarize(fields), "apply_url": apply_url}


def candidates(days: int) -> list[dict]:
    import store  # creates the schema on a fresh install, before any harvest has run
    con = store.connect() if DB == store.DB else sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """select j.url, j.company, j.title, j.category, j.location, j.vertical, j.why,
                  j.first_seen, coalesce(t.status, 'new') as status,
                  coalesce(t.priority, 0) as priority, t.deadline
             from jobs j left join tracking t using (url)
            where coalesce(t.status, 'new') in ('new', 'starred')
              and (j.first_seen >= date('now', ?) or t.status = 'starred')""",
        (f"-{days} day",)).fetchall()
    return [{**dict(r), "first_seen": (r["first_seen"] or "")[:10]} for r in rows]


def app_folders(company: str) -> list[str]:
    """Existing applications/<company>-* folders: drafts and answers to reuse."""
    slug = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-")
    if not slug:
        return []
    apps = HQ / "applications"
    return [str(d.relative_to(HQ)) for d in sorted(apps.glob(f"{slug}*")) if d.is_dir()]


def answer_bank() -> list[str]:
    """Headings of every saved answer file: what you have already written."""
    out = []
    apps = HQ / "applications"
    for f in sorted(apps.glob("*/answers.md")):
        if f.parent.name.startswith("_"):
            continue
        heads = [l.lstrip("#").strip() for l in f.read_text().splitlines()
                 if re.match(r"#{2,4} ", l)]
        out.append(f"{f.relative_to(HQ)}: " + " | ".join(heads))
    for d in sorted(apps.glob("*/essays")):
        out.append(f"{d.relative_to(HQ)}/: " + ", ".join(p.stem for p in sorted(d.glob("*.md"))))
    return out


def order(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["status"] != "starred", not r["new_today"],
                                       -(r["priority"] or 0), r["first_seen"] or ""),
                  reverse=False)


def render(rows: list[dict], days: int, today: str) -> str:
    def line(r):
        tags = [t for t in ("NEW" if r["new_today"] else "",
                            "starred" if r["status"] == "starred" else "",
                            f"deadline {r['deadline']}" if r.get("deadline") else "") if t]
        head = f"- {'[' + ', '.join(tags) + '] ' if tags else ''}{r['company']}: {r['title']}"
        head += f" ({r.get('location') or '?'}; seen {r['first_seen']}) {r['apply_url']}"
        extra = []
        if r.get("essays_required"):
            extra.append("required: " + " // ".join(f'"{q}"' for q in r["essays_required"]))
        if r.get("essays_optional"):
            extra.append("optional: " + " // ".join(f'"{q}"' for q in r["essays_optional"]))
        if r.get("short_required"):
            extra.append("short: " + " // ".join(f'"{q}"' for q in r["short_required"]))
        if r.get("folders"):
            extra.append("existing folder: " + ", ".join(r["folders"]))
        if r.get("why_unknown"):
            extra.append(r["why_unknown"])
        if r.get("why"):
            extra.append("fit: " + r["why"])
        return head + "".join(f"\n    {e}" for e in extra)

    by = {t: order([r for r in rows if r["tier"] == t]) for t in ("quick", "essay", "unknown", "closed")}
    out = [f"# Apply triage {today}: {len(rows)} open picks (first seen in the last {days} days, "
           f"or starred), not yet applied or passed"]
    titles = {"quick": "Quick: no written answers (apply today)",
              "essay": "Essay: written answers, required or optional (career block)",
              "unknown": "Form not readable here (open it to judge)",
              "closed": "Closed since the digest (consider marking passed)"}
    for t in ("quick", "essay", "unknown", "closed"):
        out.append(f"\n## {titles[t]}: {len(by[t])}")
        out += [line(r) for r in by[t]] if t != "closed" else \
            [f"- {r['company']}: {r['title']} {r['url']}" for r in by[t]]
    out.append("\n## Answer bank (saved answers by file)")
    out += [f"- {b}" for b in answer_bank()] or ["- (none)"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true", help="do not record roles as surfaced")
    a = ap.parse_args()
    today = dt.date.today().isoformat()
    try:
        seen = json.loads(SEEN.read_text())
    except (OSError, ValueError):
        seen = {}
    jobs = candidates(a.days)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(triage_one, jobs))
    # The same posting can sit in the tracker under two URLs (feed copy, ?embed=true).
    seen_apply, unique = set(), []
    for r in rows:
        key = r["apply_url"].split("?")[0]
        if key not in seen_apply:
            seen_apply.add(key)
            unique.append(r)
    rows = unique
    for r in rows:
        first = (seen.get(r["url"]) or {}).get("first_surfaced")
        r["new_today"] = first is None or first == today
        r["folders"] = app_folders(r["company"])
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"date": today, "days": a.days, "roles": rows}, indent=1))
    if not a.dry_run:
        for r in rows:
            prev = seen.get(r["url"]) or {}
            seen[r["url"]] = {"first_surfaced": prev.get("first_surfaced", today),
                              "last_surfaced": today, "tier": r["tier"]}
            # First day the board stopped listing it, kept for time-open stats.
            closed_at = prev.get("closed_at") or (today if r["tier"] == "closed" else None)
            if closed_at:
                seen[r["url"]]["closed_at"] = closed_at
        SEEN.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n")
    print(render(rows, a.days, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
