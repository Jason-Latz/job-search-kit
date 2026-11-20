"""Shared ATS access layer.

Uses curl via subprocess rather than a Python HTTP client on purpose: the routine's
container routes outbound HTTPS through an agent proxy with a custom CA bundle, and
curl is the one client empirically proven to work there. Adding requests/urllib would
bet the whole run on Python's TLS trust store picking up that CA.
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

TIMEOUT = 15
MAX_WORKERS = 24

# Several careers hosts return 403 or an empty shell to a default curl agent and the
# real page to a browser one. The board APIs do not care, so this costs nothing there
# and is the difference between a description and a blank entry on the pages that do.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

GREENHOUSE = "greenhouse"
ASHBY = "ashby"
LEVER = "lever"
PLATFORMS = (GREENHOUSE, ASHBY, LEVER)


def board_url(platform: str, token: str, *, content: bool = True) -> str:
    # Tokens are stored decoded, and an Ashby slug can be a display name with a space
    # in it ("Flock Safety"). Encoding here keeps the corpus readable and the URL valid.
    token = urllib.parse.quote(token, safe="")
    if platform == GREENHOUSE:
        suffix = "?content=true" if content else ""
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs{suffix}"
    if platform == ASHBY:
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
    if platform == LEVER:
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    raise ValueError(f"unknown platform: {platform}")


@dataclass
class Response:
    url: str
    status: int
    body: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def transient(self) -> bool:
        """A failure that says nothing about whether the token is correct.

        This distinction is the whole basis of token-cache invalidation: a 404 from an
        ATS API is a definitive 'no such board', while a timeout or 5xx is noise. Only
        the former may ever increment a company's failure counter.
        """
        return self.status == 0 or self.status >= 500 or self.status == 429

    def json(self) -> Any:
        return json.loads(self.body)


def fetch(url: str, *, timeout: int = TIMEOUT) -> Response:
    proc = subprocess.run(
        ["curl", "-sS", "-L", "-m", str(timeout), "-w", "\n%{http_code}",
         "-H", f"User-Agent: {USER_AGENT}", url],
        capture_output=True,
        text=True,
        # A page in Latin-1 is not a failed fetch. Strict decoding raised out of
        # fetch_all and took the whole harvest down with it.
        errors="replace",
    )
    if proc.returncode != 0:
        return Response(url=url, status=0, error=proc.stderr.strip()[:200])
    raw = proc.stdout
    body, _, code = raw.rpartition("\n")
    try:
        status = int(code.strip())
    except ValueError:
        return Response(url=url, status=0, error="unparseable status")
    return Response(url=url, status=status, body=body)


def fetch_all(urls: list[str], *, timeout: int = TIMEOUT,
              workers: int = MAX_WORKERS) -> list[Response]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda u: fetch(u, timeout=timeout), urls))


def fetch_stream(urls: list[str], *, timeout: int = TIMEOUT, workers: int = MAX_WORKERS):
    """Yield (index, Response) as each completes, so the caller can parse and discard.

    A handful of boards return tens of MB inline. Materializing a whole chunk of those
    at once put peak RSS near 1GB; consuming them one at a time keeps only the requests
    actually in flight.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, u, timeout=timeout): i for i, u in enumerate(urls)}
        for fut in as_completed(futures):
            yield futures[fut], fut.result()


def count_jobs(platform: str, payload: Any) -> int:
    if platform == LEVER:
        return len(payload) if isinstance(payload, list) else 0
    if isinstance(payload, dict):
        return len(payload.get("jobs") or [])
    return 0
