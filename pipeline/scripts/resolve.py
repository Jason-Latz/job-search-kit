"""Resolve a company to its ATS board token, with an identity check.

Two strategies, in order of trust:

  1. careers-page discovery -- fetch the company's own site and read the ATS URL out
     of the markup. This is authoritative: the company itself is telling you the token.
  2. slug guessing -- try name-derived tokens against each board API.

Strategy 2 alone is not safe. Probing the 63-company target list found that Greenhouse
token 'figure' is Figure Lending (a fintech) and 'diligent' is Diligent Services --
both would have bound to the wrong company's board and quietly poisoned the digest.
So every hit from either strategy is confirmed against the public board page's <title>,
and only an exact brand match auto-accepts; anything else is held for review.
"""
from __future__ import annotations

import html
import re
import sys

import ats

BOARD_PAGE = {
    ats.GREENHOUSE: "https://job-boards.greenhouse.io/{t}",
    ats.ASHBY: "https://jobs.ashbyhq.com/{t}",
    ats.LEVER: "https://jobs.lever.co/{t}",
}

# Patterns a company's careers page uses to point at its own board.
TOKEN_PATTERNS = [
    (ats.GREENHOUSE, re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)")),
    (ats.GREENHOUSE, re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)")),
    (ats.ASHBY, re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9_-]+)")),
    (ats.LEVER, re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_-]+)")),
]

# Board hosts also appear on their own marketing pages; never accept these as tokens.
TOKEN_STOPWORDS = {
    "embed", "job_board", "jobs", "boards", "v1", "posting-api", "job-board",
    "www", "api", "static", "app", "assets", "images", "img", "css", "js",
}

CAREERS_PATHS = ["/careers", "/jobs", "/company/careers", "/about/careers", "/careers/open-roles", ""]


def _title(body: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    return html.unescape(m.group(1)).strip()[:160] if m else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def brand_of(title: str) -> str:
    """Strip the board-page boilerplate to get just the company's own name."""
    t = re.sub(r"^\s*(jobs|careers|open roles|current openings)\s+(at|@)\s+", "", title, flags=re.I)
    t = re.sub(r"\s*[-|@]\s*(job board|careers|jobs).*$", "", t, flags=re.I)
    t = re.sub(r"\s+(jobs|careers|job board|open roles)\s*$", "", t, flags=re.I)
    return t.strip()


def identity(expected_name: str, title: str) -> str:
    """exact | review | unknown -- only 'exact' may be auto-accepted."""
    if not title:
        return "unknown"
    brand, name = _norm(brand_of(title)), _norm(expected_name)
    if not brand:
        return "unknown"
    return "exact" if brand == name else "review"


def confirm(candidates: list[tuple[str, str, str]]) -> list[dict]:
    """candidates: (company_name, platform, token) -> confirmed records."""
    if not candidates:
        return []
    api = ats.fetch_all([ats.board_url(p, t) for _, p, t in candidates])
    live: list[tuple[tuple[str, str, str], int]] = []
    for cand, r in zip(candidates, api):
        if not r.ok:
            continue
        try:
            live.append((cand, ats.count_jobs(cand[1], r.json())))
        except Exception:
            continue
    if not live:
        return []
    pages = ats.fetch_all([BOARD_PAGE[p].format(t=t) for (_, p, t), _ in live])
    out = []
    for ((name, platform, token), n_jobs), pr in zip(live, pages):
        title = _title(pr.body) if pr.ok else ""
        out.append({
            "name": name, "platform": platform, "token": token, "jobs": n_jobs,
            "page_title": title, "identity": identity(name, title),
        })
    return out


def discover_from_site(name: str, domains: list[str]) -> list[tuple[str, str, str]]:
    """Read ATS tokens straight out of a company's own careers pages."""
    urls = [f"https://{d}{p}" for d in domains for p in CAREERS_PATHS]
    found: set[tuple[str, str, str]] = set()
    for r in ats.fetch_all(urls):
        if not r.ok:
            continue
        for platform, pat in TOKEN_PATTERNS:
            for tok in pat.findall(r.body):
                if tok.lower() not in TOKEN_STOPWORDS and len(tok) > 2:
                    found.add((name, platform, tok))
    return sorted(found)


if __name__ == "__main__":
    company = sys.argv[1]
    doms = sys.argv[2:]
    for rec in confirm(discover_from_site(company, doms)):
        print(rec)
