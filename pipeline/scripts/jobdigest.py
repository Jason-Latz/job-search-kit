#!/usr/bin/env python3
"""Job-digest pipeline: everything mechanical, so the model only does judgment.

  harvest              feeds + ATS boards -> filtered, ranked out/shortlist.json
  record --picks FILE  verify links, update state, append the run log

Two discovery layers, because neither is sufficient alone. The structured feeds carry
~1,700 companies but only 13 of the 63 watchlist startups; the direct ATS scan covers
those startups and sees a posting the moment it goes up. Both are deduped by URL.

Design rule: no silent fallbacks. Every degraded path lands in the report as an explicit
field, because the previous design's state layer was a dead letter for 24 consecutive
runs without a single run saying so.
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

import ats
import boards
import feeds
import runctl
import store

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA, STATE, OUT = ROOT / "data", ROOT / "state", ROOT / "out"

CANARIES = [(ats.GREENHOUSE, "databricks"), (ats.ASHBY, "openevidence")]

NEWGRAD_TEXT = ["new grad", "new graduate", "new-grad", "university graduate", "entry level",
                "entry-level", "early career", "early-career", "university recruit",
                "campus hire", "class of 2027", "2027 grad", "recent graduate"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def load(path: pathlib.Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s))).strip()


# Posting pages that are client-rendered shells, mapped to an endpoint that serves the
# posting itself. On the 2026-09-09 pool no sampled posting from these families returned
# a description from the page, and together they hold 428 eligible roles. An extractor
# reads a JSON endpoint; None means the endpoint is a page, read by page_text.
POSTING_APIS = [
    (re.compile(r"https?://apply\.workable\.com/(?P<org>[^/]+)/j/(?P<code>[A-Za-z0-9]+)"),
     "https://apply.workable.com/api/v1/accounts/{org}/jobs/{code}",
     lambda d: " ".join(d.get(k) or "" for k in ("description", "requirements", "benefits"))),
    (re.compile(r"https?://(?P<host>[^/]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[^/]+"
                r"/sites/(?P<site>[^/]+)/job/(?P<id>\d+)"),
     "https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
     "?expand=all&onlyData=true&finder=ById;Id=%22{id}%22,siteNumber={site}",
     # External* only: the Internal* twins repeat the same text for employees, and the
     # Corporate/Organization fields are the same company blurb on every posting.
     lambda d: " ".join((d.get("items") or [{}])[0].get(k) or "" for k in (
         "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))),
    (re.compile(r"https?://(?P<host>[^/]+\.bamboohr\.com)/careers/(?P<id>\d+)"),
     "https://{host}/careers/{id}/detail",
     lambda d: ((d.get("result") or {}).get("jobOpening") or {}).get("description") or ""),
    # A Greenhouse board embedded on the company's own site (zipline.com/open-roles?gh_jid=)
    # names no board token, and its page is navigation only. Greenhouse job ids are
    # global, and the embed endpoint serves any open posting by id alone.
    (re.compile(r".*?[?&]gh_jid=(?P<id>\d+)"),
     "https://boards.greenhouse.io/embed/job_app?token={id}", None),
]

DATA_URI = re.compile(r"\[?data:[\w/+.-]+;base64,[A-Za-z0-9+/=]+\]?")
LD_JSON = re.compile(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>")
APPLE_DATA = re.compile(r'__staticRouterHydrationData = JSON\.parse\(("(?:[^"\\]|\\.)*")\)')


def posting_source(url: str):
    """-> (url to fetch, extractor for its JSON, or None to read it as a page)."""
    for pattern, template, extract in POSTING_APIS:
        m = pattern.match(url)
        if m:
            return template.format(**m.groupdict()), extract
    return url, None


def page_text(body: str) -> str:
    """The description on a posting page, preferring the structured copy.

    Most careers sites embed the posting as schema.org JobPosting JSON-LD for Google
    Jobs, and strip_html deletes it along with every other script block. On Workday,
    a quarter of the eligible pool, that JSON-LD is the only place the description
    exists in the HTML: the stripped page was empty on every sample.
    """
    for blob in LD_JSON.findall(body):
        try:
            data = json.loads(blob, strict=False)
        except ValueError:
            continue
        queue = data if isinstance(data, list) else [data]
        while queue:
            d = queue.pop(0)
            if not isinstance(d, dict):
                continue
            kind, desc = d.get("@type"), d.get("description")
            if (kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind) \
                    and isinstance(desc, str):
                text = strip_html(desc)
                # A one-line teaser is not the description; keep looking.
                if not unusable(text):
                    return text
            if isinstance(d.get("@graph"), list):
                queue.extend(d["@graph"])
    # Apple renders client-side and ships the posting inside the router's hydration
    # payload instead, a JSON document encoded as a JavaScript string literal.
    m = APPLE_DATA.search(body)
    if m:
        try:
            job = json.loads(json.loads(m.group(1)))["loaderData"]["jobDetails"]["jobsData"]
            return strip_html(" ".join(job.get(k) or "" for k in (
                "jobSummary", "description", "responsibilities",
                "minimumQualifications", "preferredQualifications")))
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    return strip_html(body)


def unusable(txt: str) -> bool:
    return len(txt) < 120 or "enable javascript" in txt.lower()


def backfill_pages(records, fetch_all=ats.fetch_all) -> None:
    """Fill each record's missing description from its posting page, in place.

    Pages whose own HTML is a shell are fetched from the endpoint POSTING_APIS names
    instead. Anything that still fails leaves the text empty and the status recorded.
    """
    sources = [posting_source(r["url"]) for r in records]
    for r, (_, extract), resp in zip(records, sources, fetch_all(
            [src for src, _ in sources], timeout=20, workers=8)):
        fetched = ""
        if resp.ok:
            try:
                fetched = strip_html(extract(resp.json())) if extract else page_text(resp.body)
            except Exception:
                fetched = ""
        fetched = fetched[:1500]
        if not unusable(fetched):
            r["text"] = fetched
        r["fetch_status"] = resp.status


def parse_ts(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def hits(blob: str, terms) -> list[str]:
    """Whole-word matching. Substring matching quarantined 96 postings on 'nato'
    appearing inside unrelated words, so word boundaries are load-bearing here."""
    return [t for t in terms if re.search(r"\b" + re.escape(t) + r"\b", blob)]


def normalize_ats(company: dict, payload) -> list[dict]:
    platform, out = company["platform"], []
    raw = payload if platform == ats.LEVER else (payload.get("jobs") or [])
    for j in raw:
        if platform == ats.GREENHOUSE:
            published, conf = parse_ts(j.get("first_published")), "exact"
            if published is None:
                # An old req edited yesterday looks new under updated_at. Say so.
                published, conf = parse_ts(j.get("updated_at")), "upper_bound"
            rec = {"title": j.get("title", ""), "url": j.get("absolute_url", ""),
                   "location": (j.get("location") or {}).get("name", ""),
                   "text": strip_html(j.get("content", ""))}
        elif platform == ats.ASHBY:
            if j.get("isListed") is False:
                continue
            published, conf = parse_ts(j.get("publishedAt")), "exact"
            rec = {"title": j.get("title", ""), "url": j.get("jobUrl", ""),
                   "location": j.get("location") or "", "text": j.get("descriptionPlain") or ""}
        else:
            published, conf = parse_ts(j.get("createdAt")), "exact"
            rec = {"title": j.get("text", ""), "url": j.get("hostedUrl", ""),
                   "location": (j.get("categories") or {}).get("location") or "",
                   "text": j.get("descriptionPlain") or ""}
        if not rec["url"]:
            continue
        # Ashby's plain-text description keeps inline images as data URIs. Persona AI
        # opens every posting with a banner, so its first 1,500 characters were a PNG.
        full = DATA_URI.sub(" ", rec.get("text") or "").strip()
        # Read the experience ask from the whole posting: requirements usually sit past the
        # 2,500 characters kept, so the truncated text would always look like "unstated".
        rec["min_years"] = min_years(full)
        rec["degree_ask"] = degree_ask(full)
        rec["text"] = full[:2500]
        rec.update(source="ats", board_key=(company.get("platform"), company.get("token")),
                   company=(company.get("company") or company.get("name")
                            or company.get("token", "").replace("-", " ").title()),
                   watchlist_vertical=company.get("vertical"),
                   published_at=published.date().isoformat() if published else None,
                   published_confidence=conf if published else "unknown",
                   age_days=(now() - published).days if published else None,
                   feed_category=None, terms=[], degrees=[])
        out.append(rec)
    return out


TECH_STAFF = re.compile(r"\b(?:member of )?technical staff\b")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10}
# Iambic asked for "five or more years of software development experience", which digits
# alone missed (2026-11-02), so spelled-out numbers count too.
YEARS_RE = re.compile(r"\b(\d{1,2}|" + "|".join(NUMBER_WORDS) + r")\s*\+?\s*(?:-|\u2013|to|or more)?"
                      r"\s*(?:\d{1,2})?\s*\+?\s*years?(?:\s+of)?[^.\n]{0,60}?experience", re.I)


def min_years(text: str) -> int | None:
    """Smallest 'N years ... experience' the posting asks for; None when it never says."""
    nums = (NUMBER_WORDS.get(m.group(1).lower()) or int(m.group(1)) for m in YEARS_RE.finditer(text or ""))
    found = [n for n in nums if n < 20]
    return min(found) if found else None


GRAD_RE = re.compile(r"\b(?:m\.?s\.?|master'?s(?: degree)?)\s*(?:/|or)\s*ph\.?\s?d\b"
                     r"|\bph\.?\s?d\.?\s*(?:/|or)\s*(?:m\.?s\.?|master'?s)\b"
                     r"|\bph\.?\s?d\.?(?: degree)? in\b|\b(?:ms|master'?s) degree (?:in|required)\b", re.I)


def degree_ask(text: str) -> str | None:
    """'graduate' when a posting asks for an MS or PhD, even with 'or equivalent'.

    Only 'PhD required' is a hard exclude. Goodfire's 'MS/PhD or equivalent experience'
    passed every gate and reached an application on 2026-11-02, so the ranking step and
    the digest now see the ask instead of finding it in the posting."""
    return "graduate" if GRAD_RE.search(text or "") else None


def outside_us(location: str, filt: dict) -> bool:
    """True when the location names only non-US places. 'SF or London' stays in."""
    loc = words(location)
    if not loc:
        return False
    named = lambda terms: any(re.search(r"\b" + re.escape(words(t)) + r"\b", loc) for t in terms)
    return named(filt.get("non_us_terms", [])) and not named(filt.get("us_terms", []))


def classify(rec: dict, filt: dict) -> tuple[str | None, str]:
    """-> (category, subtype). None means not a role he is eligible for."""
    if rec["source"] == "feed":
        terms = rec.get("terms") or []
        if terms:
            eligible = [t for t in terms if t in filt.get("eligible_intern_terms", [])]
            if not eligible:
                return None, ""
            priority = [t for t in eligible if t in filt.get("priority_intern_terms", [])]
            return "internship", (priority or eligible)[0]
        return "new-grad", ""
    # Title only. Matching new-grad phrases in body text is company boilerplate and
    # pulled in recruiters, a Workplace Experience Manager, and a Molten Salt Engineer.
    title = rec["title"].lower()
    if "intern" in title or "co-op" in title or "coop" in title:
        return "internship", "unspecified"
    if hits(title, NEWGRAD_TEXT):
        return "new-grad", ""
    # Watchlist startups rarely title a req "new grad": Chai, Commure and OpenEvidence post
    # plain "Software Engineer" and hire graduates into it. Requiring the phrase dropped 231
    # of their technical roles on 2026-11-02. Only boards on your watchlist get this reading;
    # applied to the 2,000-board corpus it would flood the pool with mid-level reqs.
    if rec.get("watchlist_vertical"):
        return "open-level", ""
    return None, ""


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
NUMID_RE = re.compile(r"/(\d{5,})(?:/|$)")


def posting_key(url: str) -> str:
    """Identify a posting across URL variants.

    A feed links to .../{uuid}/application while the board API returns .../{uuid}, and
    the two disagree on token casing, so matching whole URLs silently fails. Tracking
    parameters have to go first: ?gh_src=... defeated the numeric-id match and sent the
    whole URL back as the key.
    """
    path = (url or "").split("?")[0].split("#")[0]
    m = UUID_RE.search(path)
    if m:
        return m.group(0).lower()
    m = NUMID_RE.search(path)
    return m.group(1) if m else path.rstrip("/").lower()


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def is_blocked(company: str, blocked: list[str]) -> bool:
    """A blocked name must appear as whole words inside the company name.

    Exact matching let 'General Dynamics UK' through while 'General Dynamics' was
    listed. Matching plain substrings in both directions then over-blocked: Intuit was
    caught by 'Applied Intuition', Mosaic by 'SAIC', Upscale AI by 'Scale AI'."""
    c = words(company)
    if not c:
        return False
    return any(b and re.search(r"\b" + re.escape(b) + r"\b", c) for b in (words(x) for x in blocked))


def guess_vertical(rec: dict, priors: dict) -> str | None:
    # Company and title only. Reading any of the description picks up the benefits
    # section, where "health" appears in almost every posting; that tagged a Tesla
    # mobile-app internship as biotech and consumed reserved biotech slots. Truncating
    # the body was not enough, since benefits copy often sits inside the first 300
    # characters. Real biotech is named by its company: Xaira Therapeutics, Color
    # Health, Foundation Health.
    blob = f'{rec["company"]} {rec["title"]}'.lower()
    best, best_n = None, 0
    for vertical, terms in priors.items():
        n = len(hits(blob, terms))
        if n > best_n:
            best, best_n = vertical, n
    return best


def cmd_harvest(args) -> int:
    reg = load(DATA / "companies.json", {"companies": []})
    filt = load(DATA / "filters.json", {})
    seen = load(STATE / "seen-jobs.json", {"jobs": {}})["jobs"]
    OUT.mkdir(exist_ok=True)

    report: dict = {"run_at": now().isoformat(), "window_days": args.window,
                    "state_loaded": (STATE / "seen-jobs.json").exists(),
                    "seen_count": len(seen), "degraded": []}
    if not report["state_loaded"]:
        report["degraded"].append("no seen-jobs.json on disk: dedup cannot be honored this run")

    # Two canaries on different vendors, so one outage is not a false egress alarm.
    pre = [ats.fetch(ats.board_url(p, t, content=False)) for p, t in CANARIES]
    report["preflight"] = [{"platform": p, "status": r.status} for (p, _), r in zip(CANARIES, pre)]
    if not any(r.ok for r in pre):
        report["degraded"].append("EGRESS BLOCKED: no ATS canary reachable")
        (OUT / "harvest-report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 2

    blocked = filt.get("blocked_companies", [])

    # --- layer 1: broad feeds
    feed_records, feed_status, feed_listings = feeds.fetch_feeds()
    report["feeds"] = feed_status
    if not feed_records:
        report["degraded"].append("all discovery feeds failed: breadth layer is missing this run")

    # --- layer 2: direct board scan over the self-grown corpus
    #
    # Feed URLs hand over the employer's own board token, so the corpus grows itself and
    # every token is correct by construction. Scanning those boards directly is what
    # gives startup coverage: a feed lists a company only when a contributor adds one of
    # its postings, but the board shows every open role on it.
    #
    # Chunked because the full scan moves ~500MB. Records are filtered and their text
    # dropped inside each chunk, so peak memory is one chunk rather than the whole scan.
    corpus = boards.load()
    today = now().date().isoformat()
    discovered = boards.extract(feed_listings)
    for c in reg["companies"]:
        if c["status"] in ("ok", "review") and c.get("token"):
            discovered.setdefault((c["platform"], c["token"]), c["name"])
    report["boards_added"] = boards.merge(corpus, discovered, today)
    scan = [b for b in boards.active(corpus) if not is_blocked(b.get("company") or "", blocked)]
    report.update(boards_in_corpus=len(corpus["boards"]), boards_scanned=len(scan))

    ctx = {
        "filt": filt, "seen": seen, "blocked": blocked,
        "watchlist": {c["name"].lower(): c["vertical"] for c in reg["companies"]},
        # By board too: a corpus board keeps the name the feed gave it ("Cursor"), which
        # need not match the watchlist's ("Anysphere").
        "watch_boards": {(c["platform"], c["token"].lower()): c["vertical"] for c in reg["companies"]
                         if c["status"] in ("ok", "review") and c.get("token")},
        "priors": filt.get("vertical_priors", {}),
        "strong": filt.get("tripwire_strong", []), "weak": filt.get("tripwire_weak", []),
        "hard": filt.get("hard_excludes", {}), "window": args.window,
    }
    counts = collections.Counter()
    kept: list[dict] = []
    quarantined: list[dict] = []
    urls_seen: set[str] = set()

    keep_records(feed_records, ctx, counts, kept, quarantined, urls_seen)
    from_feed = len(kept)
    del feed_records

    board_ok = board_dead = board_transient = 0
    for i in range(0, len(scan), args.chunk):
        chunk = scan[i:i + args.chunk]
        # content=False: Greenhouse descriptions are most of the ~500MB, and the role
        # and seniority gates read the title only anyway (matching body text pulled in
        # recruiters and a Workplace Experience Manager). Full text for the shortlist is
        # re-fetched afterwards, where the late tripwire re-checks it.
        urls = [ats.board_url(b["platform"], b["token"], content=False) for b in chunk]
        records = []
        for idx, r in ats.fetch_stream(urls, workers=args.workers):
            board = chunk[idx]
            if r.ok:
                try:
                    fresh = normalize_ats(board, r.json())
                    board["openings"] = len(fresh)
                    records += fresh
                    board_ok += 1
                    board["last_ok"], board["consecutive_failures"] = today, 0
                except Exception:
                    board_transient += 1
            elif r.transient:
                # 5xx / timeout / 429 says nothing about the token: never penalize it.
                board_transient += 1
            else:
                board["consecutive_failures"] = board.get("consecutive_failures", 0) + 1
                board_dead += 1
                if board["consecutive_failures"] >= boards.RETIRE_AFTER:
                    board["status"] = "retired"
            r.body = ""      # release the payload before the next one arrives
        keep_records(records, ctx, counts, kept, quarantined, urls_seen)
        del records

    # Board size is the best startup signal available, and it costs nothing: a board
    # with 12 openings is a startup, one with 4,000 is a bank. The vertical prior alone
    # let Loblaw and TD Bank co-ops flood the shortlist.
    sizes = {(b["platform"], b["token"]): b.get("openings")
             for b in corpus["boards"] if b.get("openings") is not None}
    for r in kept:
        tok = next(iter(boards.extract([r["url"]])), None)
        r["employer_openings"] = sizes.get(tok) if tok else None
    boards.save(corpus)
    report.update(boards_ok=board_ok, boards_dead=board_dead, boards_transient=board_transient,
                  postings_seen=len(urls_seen),
                  eligible_by_source={"feed": from_feed, "ats": len(kept) - from_feed})

    # Rank so the model reads the most promising first. Verticals are a prior, never a
    # filter: a strong role outside them still reaches the shortlist.
    size_small = filt.get("employer_small_max", 60)
    size_large = filt.get("employer_large_min", 400)
    vweight = filt.get("vertical_weight", {})
    vdefault = filt.get("vertical_weight_default", 3)
    standing_max = filt.get("standing_max_age_days", 90)

    def size_rank(r):
        """Smaller employer first. Unknown sits between small and large rather than last,
        since most feed records never resolve to a board we scanned."""
        n = r.get("employer_openings")
        if n is None:
            return 1
        return 0 if n <= size_small else (1 if n <= size_large else 2)

    def quality(r):
        """Rank inside a bucket. Recency is deliberately last: it is the bucket's job to
        decide whether fresh roles compete with standing ones, not the sort key's."""
        return (vweight.get(r["vertical"], vdefault), not r["priority_term"],
                not r["on_watchlist"], size_rank(r),
                r["age_days"] if r["age_days"] is not None else 9999)

    def worth_standing(r):
        """An older role has to earn its slot: it is not news, so it must be a company we
        track, in a target vertical, or small enough to be worth surfacing."""
        age = r["age_days"]
        if age is None or age > standing_max:
            return False
        return r["on_watchlist"] or r["vertical"] is not None or size_rank(r) == 0

    def bucket(r):
        if r["vertical"] == "health-bio":
            # Biotech gets reserved slots, but an old role still has to be inside the
            # standing window: a 109-day-old req is not something to lead a digest with.
            return "biotech" if (r["is_new"] or worth_standing(r)) else None
        if r["category"] == "open-level":
            # A watchlist company is always worth standing, so its open reqs inside the
            # standing window compete here rather than crowding the generic bucket.
            return "open-level" if (r["is_new"] or worth_standing(r)) else None
        if not r["is_new"]:
            return "standing" if worth_standing(r) else None
        if r["category"] == "new-grad":
            return "new-grad"
        return "internship-priority" if r["priority_term"] else "internship-other"

    quotas = filt.get("shortlist_quotas", {})
    pools = collections.defaultdict(list)
    for r in kept:
        b = bucket(r)
        if b:
            pools[b].append(r)
    for rows in pools.values():
        rows.sort(key=quality)

    shortlist, seen_titles, per_company = [], set(), collections.Counter()

    def take(r):
        """One employer posting six near-identical reqs must not eat the shortlist."""
        key = (norm_name(r["company"]), norm_name(r["title"]))
        if key in seen_titles or per_company[key[0]] >= args.per_company:
            return False
        seen_titles.add(key)
        per_company[key[0]] += 1
        shortlist.append(r)
        return True

    filled = collections.Counter()
    for name, quota in quotas.items():
        for r in pools.get(name, []):
            if filled[name] >= quota or len(shortlist) >= args.limit:
                break
            if take(r):
                filled[name] += 1
    if len(shortlist) < args.limit:      # spill so an underfilled bucket is not wasted
        for r in sorted(kept, key=lambda x: (not x["is_new"], quality(x))):
            if len(shortlist) >= args.limit:
                break
            take(r)
    shortlist.sort(key=lambda r: (not r["is_new"], quality(r)))
    report["shortlist_by_bucket"] = dict(filled)
    report["pool_sizes"] = {k: len(v) for k, v in pools.items()}

    # Descriptions only for what the model will actually read.
    need = [r for r in shortlist if unusable(r["text"])]
    if need:
        # Ashby and Lever posting pages are client-rendered, so fetching the HTML returns
        # a "you need to enable JavaScript" stub. Their board APIs carry the real
        # description, so enrich from the board the posting belongs to and fall back to
        # the page only for URLs that are not on a board we can read.
        wanted = collections.defaultdict(list)
        for r in need:
            tok = next(iter(boards.extract([r["url"]])), None)
            if tok:
                wanted[tok].append(r)
        if wanted:
            keys = list(wanted)
            for (platform, token), resp in zip(
                    keys, ats.fetch_all([ats.board_url(p_, t_) for p_, t_ in keys],
                                        timeout=25, workers=8)):
                if not resp.ok:
                    continue
                try:
                    payload = resp.json()
                except Exception:
                    continue
                text_by_key = {}
                for rec in normalize_ats({"platform": platform, "token": token}, payload):
                    text_by_key[posting_key(rec["url"])] = rec.get("text") or ""
                for r in wanted[(platform, token)]:
                    got = text_by_key.get(posting_key(r["url"]), "")
                    if not unusable(got):
                        r["text"] = got[:1500]

        still = [r for r in need if unusable(r["text"])]
        if still:
            backfill_pages(still)

    # Re-run the tripwire against text the scan never had. The corpus scan filters on
    # company and title alone, so this is the first look at the full posting for most
    # candidates, and it is the last chance to catch defense work before a human reads it.
    final, late = [], []
    for r in shortlist:
        if not r["text"]:
            final.append(r)
            continue
        blob = f'{r["company"]} {r["title"]} {r["text"]}'.lower()
        fs, fw = hits(blob, ctx["strong"]), hits(blob, ctx["weak"])
        if fs or len(fw) >= 2:
            late.append({"company": r["company"], "title": r["title"], "url": r["url"],
                         "matched": (fs + fw)[:5]})
            counts["defense_quarantined"] += 1
        else:
            final.append(r)
    if late:
        quarantined += late
        report["quarantined_on_full_text"] = len(late)

    report.update(filter_counts=dict(counts), quarantined_defense=quarantined,
                  eligible_total=len(kept), eligible_new=sum(1 for r in kept if r["is_new"]),
                  shortlist_size=len(final),
                  shortlist_without_text=sum(1 for r in final if unusable(r["text"])),
                  shortlist_new=sum(1 for r in final if r["is_new"]))
    # The browse UI reads this: the digest only ever shows what is new, so without the
    # full pool the thousands of roles already open stay invisible.
    with store.connect() as con:
        report["pool_written"] = store.replace_pool(con, kept)

    if args.dump_kept:
        (OUT / "kept.json").write_text(json.dumps(
            [{k: r.get(k) for k in ("company", "title", "url", "source", "vertical",
                                    "category", "subtype", "age_days", "is_new",
                                    "on_watchlist", "employer_openings")} for r in kept],
            indent=2))
    dropped = ctx.get("watch_dropped", [])
    (OUT / "watchlist-dropped.json").write_text(json.dumps(dropped, indent=2))
    report["watchlist_dropped"] = dict(collections.Counter(d["reason"] for d in dropped))
    (OUT / "shortlist.json").write_text(json.dumps(final, indent=2))
    (OUT / "harvest-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "quarantined_defense"}, indent=2))
    return 0


def keep_records(records, ctx, counts, kept, quarantined, urls_seen) -> None:
    """Filter one batch in place and append survivors to `kept`.

    Posting text is used here and then dropped, so a 1,780-board scan does not hold
    ~500MB of descriptions. Text for the shortlist is re-fetched later.
    """
    filt, seen, blocked = ctx["filt"], ctx["seen"], ctx["blocked"]
    hard, window = ctx["hard"], ctx["window"]
    for rec in records:
        url = rec.get("url")
        if not url or url in urls_seen:
            continue
        # Deliberately not marked seen yet. The same posting reaches us from both the
        # feed and its own board, and the two carry different metadata; marking it here
        # let whichever arrived first veto the other. A feed listing for Xaira's protein
        # design internship was dropped on feed metadata, which silently suppressed the
        # board's copy of the same role. Only a kept record claims the URL.
        text = rec.get("text") or ""
        blob = f'{rec["company"]} {rec["title"]} {text}'.lower()

        # The feeds shorten names: "Applied Intuition" arrives as "Applied" and slipped
        # the denylist, so check how the posting introduces itself as well.
        if is_blocked(rec["company"], blocked) or is_blocked(text[:300], blocked):
            counts["defense_quarantined"] += 1
            quarantined.append({"company": rec["company"], "title": rec["title"],
                                "url": url, "matched": ["blocked_company"]})
            continue
        fs, fw = hits(blob, ctx["strong"]), hits(blob, ctx["weak"])
        # One strong signal is enough; weak signals need corroboration because they show
        # up in equal-opportunity boilerplate on almost every posting.
        if fs or len(fw) >= 2:
            counts["defense_quarantined"] += 1
            quarantined.append({"company": rec["company"], "title": rec["title"],
                                "url": url, "matched": (fs + fw)[:5]})
            continue
        degrees = rec.get("degrees") or []
        if degrees and "Bachelor's" not in degrees:
            counts["degree_gated"] += 1
            continue
        if hits(blob, hard.get("phd_required", [])):
            counts["degree_gated"] += 1
            continue

        bk = rec.get("board_key") or (None, None)
        wl = (ctx.get("watch_boards", {}).get((bk[0], (bk[1] or "").lower()))
              or ctx["watchlist"].get(rec["company"].lower()))
        if wl and not rec.get("watchlist_vertical"):
            rec["watchlist_vertical"] = wl
        category, subtype = classify(rec, filt)

        def drop(reason):
            # A watchlist company's role dropped by a rule is logged with its reason, so a
            # wrong rule shows up in out/watchlist-dropped.json instead of silently eating
            # solid jobs (2026-11-02).
            counts[reason] += 1
            if wl:
                ctx.setdefault("watch_dropped", []).append(
                    {"company": rec["company"], "title": rec["title"], "url": url,
                     "location": rec.get("location", ""), "reason": reason})
        if category is None:
            drop("term_not_eligible" if rec.get("terms") else "not_eligible_category")
            continue

        title = rec["title"].lower()
        technical = (rec.get("feed_category") in feeds.TECHNICAL_CATEGORIES
                     or bool(hits(title, filt.get("technical_title_terms", []))))
        if not technical:
            drop("not_technical")
            continue
        # "Member of Technical Staff" is every level at a startup, new grad included.
        if hits(TECH_STAFF.sub(" ", title), filt.get("seniority_exclude_terms", [])):
            drop("too_senior")
            continue
        if category == "open-level":
            if (hits(title, filt.get("open_level_title_excludes", []))
                    or not hits(title, filt.get("open_level_title_requires", []))):
                drop("open_level_title")
                continue
            if (rec.get("min_years") or 0) >= filt.get("open_level_max_years", 3):
                drop("too_experienced")
                continue
            if outside_us(rec.get("location", ""), filt):
                drop("outside_us")
                continue
        if category == "new-grad" and hits(blob, hard.get("grad_window_closed", [])):
            drop("grad_window_closed")
            continue
        if url in seen:
            counts["already_sent"] += 1
            continue

        vertical = rec.get("watchlist_vertical") or ctx["watchlist"].get(rec["company"].lower())
        rec.update(category=category, subtype=subtype,
                   vertical=vertical or guess_vertical(rec, ctx["priors"]),
                   on_watchlist=bool(wl),
                   priority_term=subtype in filt.get("priority_intern_terms", []),
                   is_new=rec["age_days"] is not None and rec["age_days"] <= window,
                   # Keep a little text rather than none. Ashby and Lever include the
                   # description in the board payload, and their posting pages are
                   # JS-rendered, so re-fetching the HTML later returns a "you need to
                   # enable JavaScript" stub. 4k records x 700B is a few MB.
                   text=text[:700])
        urls_seen.add(url)
        kept.append(rec)


def cmd_record(args) -> int:
    picks = json.loads(pathlib.Path(args.picks).read_text())
    seen_doc = load(STATE / "seen-jobs.json", {"jobs": {}})
    today = now().date().isoformat()

    results = ats.fetch_all([p["url"] for p in picks]) if picks else []
    dead, live = [], []
    for p, r in zip(picks, results):
        # 403 is bot protection at the employer's own site, not a dead posting: a sample
        # of live listings returned 403 at Epic and Tesla. Only 404/410 means gone.
        if r.status in (404, 410):
            dead.append({"url": p["url"], "status": r.status})
        else:
            live.append(p)
    for p in live:
        seen_doc["jobs"][p["url"]] = {"date_sent": today, "category": p.get("category", ""),
                                      "company": p.get("company", "")}
    STATE.mkdir(exist_ok=True)
    (STATE / "seen-jobs.json").write_text(json.dumps(seen_doc, indent=2) + "\n")
    # Everything sent enters the tracker as 'new' so it can be starred later. Rows that
    # already exist keep whatever status they have.
    with store.connect() as con:
        tracked = store.upsert_jobs(con, live)
        store.export_json(con)

    entry = {"run_at": now().isoformat(), "sent": len(live), "dropped_dead_link": len(dead),
             "tracked": tracked, "seen_total": len(seen_doc["jobs"])}
    with (STATE / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    print(json.dumps({"recorded": entry, "dead_links": dead}, indent=2))
    return 0


DIGEST_CSS = """
:root { --bg:#fbfaf8; --fg:#1a1a19; --muted:#6b6a66; --line:#e5e2dc; --card:#fff; --accent:#7a4a2b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16151a; --fg:#eceaf0; --muted:#9a97a3; --line:#2b2933; --card:#1e1d24; --accent:#d9a273; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",sans-serif; }
.wrap { max-width:760px; margin:0 auto; padding:40px 22px 72px; }
h1 { font-size:23px; margin:0 0 4px; letter-spacing:-.01em; }
.sub { color:var(--muted); font-size:13px; margin-bottom:34px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:.09em; color:var(--accent);
     margin:36px 0 14px; padding-bottom:7px; border-bottom:1px solid var(--line); }
.role { background:var(--card); border:1px solid var(--line); border-radius:9px;
        padding:15px 17px; margin-bottom:11px; }
.role a.t { font-weight:640; color:var(--fg); text-decoration:none; font-size:15px; }
.role a.t:hover { color:var(--accent); }
.co { font-size:13px; color:var(--muted); margin:2px 0 8px; }
.sum { font-size:14px; margin:0 0 8px; }
.why { font-size:13px; color:var(--muted); margin:0; }
.meta { font-size:11.5px; color:var(--muted); margin-top:9px; display:flex; flex-wrap:wrap; gap:5px 12px; }
.tag { border:1px solid var(--line); border-radius:20px; padding:1px 9px; }
.empty { color:var(--muted); font-size:14px; padding:22px 0; }
footer { margin-top:44px; padding-top:16px; border-top:1px solid var(--line);
         font-size:12px; color:var(--muted); }
.warn { border-left:3px solid var(--accent); padding:9px 13px; margin-top:14px;
        background:var(--card); border-radius:0 6px 6px 0; font-size:12.5px; }
"""

SECTIONS = [("Full-Time - New Grad", lambda r: r.get("category") == "new-grad"),
            ("Full-Time - Watchlist Startups (open level)",
             lambda r: r.get("category") == "open-level"),
            ("Winter / Spring 2027 Internships", lambda r: r.get("category") == "internship"
             and r.get("subtype") in ("Winter 2027", "Spring 2027")),
            ("Other Internships", lambda r: r.get("category") == "internship"
             and r.get("subtype") not in ("Winter 2027", "Spring 2027"))]


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def cmd_render(args) -> int:
    picks = json.loads(pathlib.Path(args.picks).read_text()) if pathlib.Path(args.picks).exists() else []
    report = load(OUT / "harvest-report.json", {})
    today = now().date().isoformat()
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    parts = [f"<!doctype html><meta charset='utf-8'><title>Job Digest {today}</title>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             f"<style>{DIGEST_CSS}</style><div class='wrap'>",
             f"<h1>Job Digest</h1><div class='sub'>{today} &middot; {len(picks)} "
             f"role{'s' if len(picks) != 1 else ''}</div>"]

    # A day that produced nothing is said at the top of the next digest, so a gap is
    # visible without reading logs. Render runs before record, so runs.jsonl still ends
    # at the previous delivered digest.
    missed = runctl.missed_lines(STATE)
    for line in missed:
        parts.append(f"<div class='warn'>{esc(line)}</div>")

    if not picks:
        parts.append("<div class='empty'>Nothing new cleared the bar today. "
                     "The run completed normally.</div>")
    for heading, match in SECTIONS:
        rows = [p for p in picks if match(p)]
        if not rows:
            continue
        parts.append(f"<h2>{esc(heading)}</h2>")
        for r in rows:
            tag = (f"<span class='tag'>{esc(r['subtype'])}</span>"
                   if r.get("subtype") else "")
            meta = []
            if r.get("published_at"):
                conf = " (last edited)" if r.get("published_confidence") == "upper_bound" else ""
                meta.append(f"posted {esc(r['published_at'])}{conf}")
            if r.get("location"):
                # Some postings list a dozen offices; three is enough to place the role.
                places = [x.strip() for x in str(r["location"]).split(",") if x.strip()]
                shown = ", ".join(places[:3])
                meta.append(esc(shown + (" +more" if len(places) > 3 else "")))
            if r.get("employer_openings") is not None:
                meta.append(f"{esc(r['employer_openings'])} open roles at this employer")
            parts.append(
                f"<div class='role'><a class='t' href='{esc(r.get('url'))}'>{esc(r.get('title'))}</a>"
                f"<div class='co'>{esc(r.get('company'))}</div>"
                + (f"<p class='sum'>{esc(r.get('summary'))}</p>" if r.get("summary") else "")
                + (f"<p class='why'>{esc(r.get('why'))}</p>" if r.get("why") else "")
                + (f"<div class='meta'>{tag}{' &middot; '.join(meta)}</div>"
                   if (meta or tag) else "")
                + "</div>")

    fc = report.get("filter_counts", {})
    parts.append(
        f"<footer>{report.get('boards_ok', '?')} of {report.get('boards_scanned', '?')} boards read "
        f"&middot; {report.get('postings_seen', '?')} postings seen &middot; "
        f"{report.get('eligible_new', '?')} new and eligible &middot; "
        f"{fc.get('defense_quarantined', 0)} quarantined &middot; "
        f"{report.get('seen_count', 0)} already sent previously</footer>")
    verify = load(OUT / "verify.json", {})
    for line in dict.fromkeys(list(report.get("degraded", [])) + verify.get("warnings", [])):
        parts.append(f"<div class='warn'>{esc(line)}</div>")
    parts.append("</div>")
    out.write_text("".join(parts))
    print(json.dumps({"rendered": str(out), "roles": len(picks),
                      "degraded": report.get("degraded", []), "missed": missed}, indent=2))
    return 0


def cmd_verify(args) -> int:
    """Assert invariants on real output. Unit tests cover the logic; this covers the run.

    Errors block the digest. Warnings travel with it, because a degraded run that says so
    is useful and a silent one is what this repo exists to avoid.
    """
    report = load(OUT / "harvest-report.json", {})
    shortlist = load(OUT / "shortlist.json", [])
    filt = load(DATA / "filters.json", {})
    seen = load(STATE / "seen-jobs.json", {"jobs": {}})["jobs"]
    blocked = filt.get("blocked_companies", [])
    errors, warnings = [], []

    if not report:
        errors.append("no harvest-report.json: harvest did not complete")
        print(json.dumps({"errors": errors, "warnings": warnings}, indent=2))
        return 1

    # --- hard invariants
    leaked = [r["company"] for r in shortlist if is_blocked(r.get("company", ""), blocked)]
    if leaked:
        errors.append(f"denylisted companies reached the shortlist: {sorted(set(leaked))}")

    urls = [r.get("url") for r in shortlist]
    dupes = {u for u in urls if u and urls.count(u) > 1}
    if dupes:
        errors.append(f"duplicate urls in shortlist: {len(dupes)}")

    resent = [r["url"] for r in shortlist if r.get("url") in seen]
    if resent:
        errors.append(f"already-sent roles reached the shortlist: {len(resent)}")

    if not report.get("boards_ok") and not any(
            f.get("listings_active") for f in report.get("feeds", [])):
        errors.append("both discovery layers produced nothing")

    if args.picks:
        picks_path = pathlib.Path(args.picks)
        picks = json.loads(picks_path.read_text()) if picks_path.exists() else None
        if picks is None:
            errors.append(f"{args.picks} missing: the model never wrote its picks")
        else:
            allowed = {r.get("url") for r in shortlist}
            invented = [p.get("url") for p in picks if p.get("url") not in allowed]
            if invented:
                # The one failure mode a static pipeline cannot prevent on its own.
                errors.append(f"picks contain {len(invented)} url(s) absent from the "
                              f"shortlist: {invented[:3]}")
            pu = [p.get("url") for p in picks]
            if len(pu) != len(set(pu)):
                errors.append("picks contain duplicate urls")
            already = [p["url"] for p in picks if p.get("url") in seen]
            if already:
                errors.append(f"picks contain {len(already)} already-sent role(s)")
            missing = [p.get("url") for p in picks
                       if not (p.get("summary") or "").strip()]
            if missing:
                warnings.append(f"{len(missing)} pick(s) have no summary")

    # --- warnings
    blank = [r for r in shortlist if not (r.get("company") or "").strip()]
    if blank:
        warnings.append(f"{len(blank)} shortlist entries have no company name")

    if shortlist:
        unusable = sum(1 for r in shortlist
                       if len(r.get("text") or "") < 120
                       or "enable javascript" in (r.get("text") or "").lower())
        if unusable / len(shortlist) > 0.35:
            warnings.append(f"{unusable}/{len(shortlist)} shortlist entries have no "
                            f"usable description")

    scanned, ok = report.get("boards_scanned") or 0, report.get("boards_ok") or 0
    if scanned and ok / scanned < 0.8:
        warnings.append(f"only {ok} of {scanned} boards responded")

    if report.get("eligible_new", 0) > 50 and len(shortlist) < 10:
        warnings.append(f"{report['eligible_new']} new eligible roles but only "
                        f"{len(shortlist)} on the shortlist")

    if shortlist:
        top = collections.Counter(norm_name(r.get("company", "")) for r in shortlist)
        company, n = top.most_common(1)[0]
        if n > args.per_company:
            warnings.append(f"one employer holds {n} shortlist slots (cap is "
                            f"{args.per_company})")

    warnings += report.get("degraded", [])
    out = {"ok": not errors, "errors": errors, "warnings": warnings,
           "shortlist": len(shortlist)}
    (OUT / "verify.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="jobdigest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest")
    h.add_argument("--window", type=int, default=7)
    h.add_argument("--limit", type=int, default=40, help="candidates handed to the model")
    h.add_argument("--per-company", type=int, default=3, help="max shortlist slots per employer")
    h.add_argument("--workers", type=int, default=12, help="concurrent board fetches")
    h.add_argument("--dump-kept", action="store_true",
                   help="write every eligible role to out/kept.json for diagnosis")
    h.add_argument("--chunk", type=int, default=200, help="boards fetched per memory-bounded chunk")
    h.set_defaults(fn=cmd_harvest)
    r = sub.add_parser("record")
    r.add_argument("--picks", required=True)
    r.set_defaults(fn=cmd_record)
    d = sub.add_parser("render")
    d.add_argument("--picks", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_render)
    v = sub.add_parser("verify")
    v.add_argument("--picks", help="also validate the model's picks against the shortlist")
    v.add_argument("--per-company", type=int, default=3)
    v.set_defaults(fn=cmd_verify)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
