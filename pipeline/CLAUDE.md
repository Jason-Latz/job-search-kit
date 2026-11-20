# CLAUDE.md: jobs/pipeline/ (job digest + application tracker)

The discovery engine of this repo: scans job boards daily, delivers a digest,
and hosts the application tracker. Paths and commands below are relative to
`jobs/pipeline/`.
See `README.md` for setup and `prompt.md` for the ranking prompt.

## Product contract

Discover broadly across job boards, filter mechanically to an honest shortlist, let the
model rank it, write the digest **every run**, and persist what was sent so nothing is
sent twice.

## Architecture

- `scripts/ats.py` — curl-backed board access. Uses curl rather than a Python HTTP client
  because the routine's container proxies HTTPS through a custom CA and curl is the only
  client proven to work there. `Response.transient` separates 5xx/timeout/429 (says
  nothing about the token) from 404 (definitive: the token is wrong).
- `scripts/feeds.py` — broad discovery from structured new-grad/internship feeds.
  ~1,700 companies. Carries `terms`, `degrees`, `category` as real fields.
- `scripts/resolve.py` — token discovery, gated by an exact board-page brand match.
- `scripts/jobdigest.py` — `harvest` and `record`.
- `data/` — registry, filters, denylist, vertical priors. Tunable without touching code
  or the prompt.
- `scripts/store.py` — SQLite store: tracking, plus the `pool` table holding every
  eligible role, replaced each harvest so the browse UI can catch up on the backlog.
- `scripts/tracker.py` — localhost kanban board: drag between statuses, detail panel with
  priority, interview stage, deadline, next action, contact, compensation and notes, plus
  status history. A static digest file cannot write anywhere, so this is the smallest
  stdlib server that does the job.
- `scripts/runctl.py` — run control: retry policy, per-attempt timeout, run deadline,
  `state/failures.jsonl`, missed-day detection. `run-local.sh` runs harvest, the model
  step and the push through it.
- `state/` — `seen-jobs.json`, `runs.jsonl`, `jobs.db` (gitignored) and
  `tracker-export.json` (the committed, diffable snapshot the db rebuilds from).

## Commands

```bash
./bin/run-local.sh                                        # the whole daily run
./bin/board.sh                                            # open the job tracker
./bin/test.sh                                             # 81 offline regression tests
python3 scripts/runctl.py missed                          # days since the last digest that produced nothing
python3 scripts/jobdigest.py verify --picks out/picks.json # invariant gate
python3 scripts/apply_triage.py [--dry-run]               # open picks split by form effort: quick vs essay
./bin/install.sh                                          # install the LaunchAgent
python3 scripts/jobdigest.py harvest --window 7           # exit 2 = egress blocked
python3 scripts/jobdigest.py render --picks out/picks.json --out digests/x.html
python3 scripts/jobdigest.py record --picks out/picks.json
python3 scripts/resolve.py "Company Name" company.com      # resolve one board token
```

Runs locally under launchd, not in the cloud. Nothing needs pushing to persist, and
there is no container memory ceiling; the streaming scan is kept anyway because it also
makes the run faster.

## Conventions and gotchas

- **Two discovery layers, both required.** The feeds carry ~1,700 companies but only 13 of
  the watchlist startups (at the time); the direct ATS scan covers those and sees a posting sooner.
- **Feeds are discovery, never truth.** Every emitted link is re-fetched by `record`.
- **No silent fallbacks.** Every degraded path lands in `harvest-report.json:degraded` and
  must be repeated in the digest.
- **The digest shows only what is new; Browse all shows everything eligible.** Without
  the pool table the roles already open on day one are unreachable.
- **Write a digest every run, including empty ones.** That is the dead-man's switch: a missing
  digest means the run did not happen. Push notifications stay silent on empty days.
- **Verticals are a ranking prior, never a filter.** Keep coverage broad. The
  `biotech` bucket reserves shortlist slots for health/bio; set it to 0 if irrelevant.
- **Select by bucket quotas, never one global top-N.** `is_new` as the first sort key made
  3,224 of 4,275 eligible roles unreachable. The `standing` bucket is the old prompt's
  backfill, restored and gated to 90 days.
- **A URL is claimed only when a record is kept.** The same posting arrives from both the
  feed and its own board with different metadata; marking it seen before filtering let
  whichever arrived first veto the other.
- **Guess verticals from company and title, not body text.** Every posting's benefits
  section says "health".
- **Never accept a board token without the identity check.** Greenhouse `figure` is Figure
  Lending; `diligent` is Diligent Services. Both return 200.
- **Denylist names match as whole words, one direction.** Exact matching let "General
  Dynamics UK" through; bidirectional substring matching blocked Intuit and Mosaic.
- **Careers-page discovery needs a browser User-Agent**, or sites 403/429 silently.
- **Take a board slug from a URL whole.** Ashby slugs can be domains or encoded names;
  cutting `persona.ai` to `persona` scanned a different company's board.
- **Read a posting's structured copy before stripping its page.** Workday, a quarter of
  the pool, has its description only in JobPosting JSON-LD. Workable, Oracle, BambooHR
  and company-hosted Greenhouse pages are shells, read via `POSTING_APIS` instead. A page
  that passes the length check can still be navigation (Zipline) or an inline image
  (Ashby keeps them as data URIs in its plain text).
- **Tripwire terms match on word boundaries; weak terms need two hits.**
- **Role and seniority gates apply to the title only.**

## Runtime

`bin/run-local.sh` does harvest, then one `claude -p` call for ranking, then render and
record. Delivery is a local HTML file plus a macOS notification: no SMTP, no credentials.
The model step needs an authenticated CLI (`claude setup-token`); an expired session is
detected by name rather than surfacing as a bare exit code.

## Robustness

Three gates, all run by `bin/run-local.sh`, any of which aborts the run with a
notification rather than delivering a wrong digest:

1. `bin/test.sh` — offline regression tests. Every case is a bug that shipped.
2. `verify` — invariants on harvest output (no denylisted company, no duplicates, no
   re-sends, both layers produced something).
3. `verify --picks` — the model's picks against the shortlist. Nothing else can stop a
   model inventing a URL.

Writing the tests immediately caught two live bugs: `posting_key` returned the whole URL
whenever a tracking parameter was present, and truncating body text to 300 characters had
not actually fixed biotech mislabelling, because benefits copy sits inside that.
