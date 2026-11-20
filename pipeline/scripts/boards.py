"""Self-growing corpus of ATS board tokens, harvested from the feeds themselves.

Every feed listing links to the employer's own posting, so a URL like
jobs.ashbyhq.com/{token}/... hands over a board token that is correct by construction.
No slug guessing, and no identity check needed: the employer published the link.

This is what makes startup coverage broad. The feeds list a company only when a
contributor adds one of its postings, but once its board token is known, the board is
scanned directly and every open role on it is seen, including the ones no feed carries.

The corpus only grows, and a board is retired only after repeated definitive 404s.
"""
from __future__ import annotations

import json
import pathlib
import re
import urllib.parse

import ats

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "data" / "boards.json"

# Ashby org slugs are not word characters. They are routinely a bare domain
# (persona.ai, rivianvw.tech) or a percent-encoded display name (Flock%20Safety), so a
# [A-Za-z0-9_-]+ class does not miss them, it silently truncates them -- and a truncated
# slug is a wrong answer rather than a missing one, because the prefix is usually
# another real company: ashby/persona is Persona, the identity company, while the
# posting belonged to Persona AI, the robotics one. Greenhouse and Lever were measured
# against the same feeds and have no slug outside the word-character class, so only
# Ashby is widened here.
URL_PATTERNS = {
    ats.GREENHOUSE: re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ats.ASHBY: re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)"),
    ats.LEVER: re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
}
STOPWORDS = {"embed", "job_board", "jobs", "boards", "v1", "posting-api", "job-board", "www", "api"}

RETIRE_AFTER = 5


def extract(records) -> dict[tuple[str, str], str]:
    """-> {(platform, token): company_name}. The name comes from the feed listing that
    produced the URL, since the board APIs themselves do not return a company name."""
    found: dict[tuple[str, str], str] = {}
    for rec in records:
        url = rec.get("url") if isinstance(rec, dict) else rec
        if not url:
            continue
        name = rec.get("company", "") if isinstance(rec, dict) else ""
        for platform, pattern in URL_PATTERNS.items():
            for token in pattern.findall(url):
                # Store the decoded slug so "Flock%20Safety" and "Flock Safety" are one
                # board rather than two; board_url re-encodes it when it builds the API
                # call.
                token = urllib.parse.unquote(token).strip()
                if token.lower() not in STOPWORDS and len(token) > 2:
                    found.setdefault((platform, token), name)
    return found


def load() -> dict:
    if CORPUS.exists():
        return json.loads(CORPUS.read_text())
    return {"boards": []}


def save(corpus: dict) -> None:
    corpus["boards"].sort(key=lambda b: (b["platform"], b["token"]))
    CORPUS.write_text(json.dumps(corpus, indent=2) + "\n")


def merge(corpus: dict, discovered: dict[tuple[str, str], str], today: str) -> int:
    # Board tokens are case-insensitive. Feed URLs spell Sierra both "Sierra" and "sierra",
    # and keying on the exact spelling scanned six boards twice and put every one of their
    # roles in the pool twice (found 2026-11-02).
    known = {}
    for b in corpus["boards"]:
        known.setdefault((b["platform"], b["token"].lower()), b)
    added = 0
    for (platform, token), name in sorted(discovered.items()):
        existing = known.get((platform, token.lower()))
        if existing is not None:
            # Backfill a name we did not have. Rows discovered from a feed URL whose
            # listing carried no company name rendered as the token, so Xaira Therapeutics
            # appeared as "Xairatherapeutics".
            if name and not (existing.get("company") or "").strip():
                existing["company"] = name
            continue
        row = {"platform": platform, "token": token, "company": name, "first_seen": today,
               "last_ok": None, "consecutive_failures": 0, "status": "active"}
        corpus["boards"].append(row)
        known[(platform, token.lower())] = row
        added += 1
    return added


def active(corpus: dict) -> list[dict]:
    return [b for b in corpus["boards"] if b["status"] == "active"]
