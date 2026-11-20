"""Broad-coverage discovery from structured new-grad / internship feeds.

These are crowdsourced GitHub-hosted trackers, and the previous design banned trackers
outright. That ban was aimed at unstructured WebSearch snippets, which is a different
thing: these carry an `active` flag, a real `date_posted`, and structured `terms`,
`degrees` and `category` fields, and they link to the employer's own ATS posting. A
sample of 40 active listings posted in the last 14 days returned 37 live; the three
failures were bot-protection 403s at Epic and Tesla, not dead postings.

They are still a discovery layer, never a source of truth: every link that reaches the
digest is fetched in-run before it is sent.

Coverage note: the feeds carry ~1,700 companies but only 13 of the 63 watchlist
startups, so they complement the direct ATS scan rather than replacing it.
"""
from __future__ import annotations

import json
import time

import ats

FEEDS = [
    ("new-grad", "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"),
    # Summer2026-Internships and Summer2027-Internships serve byte-identical files
    # (SimplifyJobs unified the repos), so fetching both is 12MB of waste. Verified by
    # md5 on 2026-09-07; if they ever diverge, add the other back.
    ("internships", "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"),
]

TECHNICAL_CATEGORIES = {
    "AI/ML/Data", "Software", "Hardware", "Quant",
    "Software Engineering", "Hardware Engineering",
    "Data Science, AI & Machine Learning",
}


def fetch_feeds() -> tuple[list[dict], list[dict], list[str]]:
    """-> (active records, per-feed status, every listing seen as {url, company}).

    The third value carries closed listings too. A posting that has since been filled
    still names a valid board token, and that token is what the direct scan needs, so
    discarding inactive listings threw away two thirds of the discoverable boards.
    """
    responses = ats.fetch_all([url for _, url in FEEDS], timeout=60)
    records: dict[str, dict] = {}
    status = []
    all_listings: list[dict] = []
    now = time.time()
    for (name, url), r in zip(FEEDS, responses):
        entry = {"feed": name, "status": r.status}
        if not r.ok:
            entry["error"] = r.error or "non-200"
            status.append(entry)
            continue
        try:
            payload = json.loads(r.body)
        except Exception as exc:
            entry["error"] = f"unparseable: {exc}"
            status.append(entry)
            continue
        active = 0
        for item in payload:
            if not item.get("url"):
                continue
            # Carry the company name, not just the URL: the corpus uses it to label
            # boards, and without it a browse list shows raw tokens.
            all_listings.append({"url": item["url"],
                                 "company": item.get("company_name") or ""})
            if not item.get("active"):
                continue
            active += 1
            posted = item.get("date_posted") or 0
            records[item["url"]] = {
                "source": "feed",
                "company": item.get("company_name") or "",
                "title": item.get("title") or "",
                "url": item["url"],
                "location": ", ".join(item.get("locations") or []),
                "published_at": time.strftime("%Y-%m-%d", time.gmtime(posted)) if posted else None,
                "published_confidence": "exact" if posted else "unknown",
                "age_days": int((now - posted) // 86400) if posted else None,
                "feed_category": item.get("category"),
                "terms": item.get("terms") or [],
                "degrees": item.get("degrees") or [],
                "text": "",
            }
        entry["listings_active"] = active
        entry["listings_total"] = len(payload)
        status.append(entry)
    return list(records.values()), status, all_listings
