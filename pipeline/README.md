# pipeline: job digest

The discovery engine: scans job boards daily and writes a digest, plus a localhost
application tracker. Run everything below from `pipeline/`. The routine's stored prompt is [`prompt.md`](prompt.md).

## Setup

Two commands, once.

```bash
claude setup-token
```

Paste the token it prints into `~/.jobdigest.env` (see `bin/jobdigest.env.example`):

```bash
install -m 600 /dev/null ~/.jobdigest.env && echo 'export CLAUDE_CODE_OAUTH_TOKEN="PASTE_HERE"' > ~/.jobdigest.env
```

```bash
./bin/install.sh
```

`setup-token` prints a long-lived token rather than installing one, and the interactive
keychain session expires and cannot refresh unattended. launchd also does not inherit
your shell environment, so exporting the token in `.zshrc` is invisible to the 09:00 run.
The runner sources `~/.jobdigest.env` and refuses to start without a token, rather than
failing daily with nobody watching. The ranking step runs as `claude -p`,
and a plain `claude` login expires, which would leave a daily job failing quietly. The
second installs a LaunchAgent that runs at 09:00 daily. Do them in that order: installing
first just schedules a job that fails until the CLI can authenticate.

Run it by hand any time with `./bin/run-local.sh`, or `launchctl kickstart -k
gui/$UID/com.jobsearchkit.digest`.

Output lands in `digests/YYYY-MM-DD.html`, with `digests/latest.html` pointing at the
newest, plus a macOS notification when the digest is non-empty. No SMTP, no credentials.

A sleeping Mac delays the run until it wakes, since launchd fires a missed calendar job
once on wake. A powered-off Mac misses it. An empty day still writes a digest file, so a
missing file means the run did not happen.

## Catching up on what is already open

The daily digest only surfaces what is new, so on day one the several thousand roles
already open would be invisible. Every harvest writes the full eligible pool to SQLite,
and the tracker's **Browse all** tab is a filtered view over it.

```bash
./bin/board.sh
```

Filter chips carry live counts (health-bio, robotics, hard-tech, legal, Winter 2027,
Spring 2027, Startups only) alongside a company/title search. The default ordering is the
same one the digest ranks by: biotech first, then Winter and Spring 2027, then watchlist
companies, then small employers, then recency. Star or pass in one click; anything you
act on leaves the browse list and does not come back, so the pool shrinks as you work
through it.

## The board

`./bin/board.sh` opens a kanban board at `127.0.0.1:8765`. Drag a card between columns to
change its status, or click it for the detail panel.

```
Inbox -> Starred -> Applied -> Interviewing -> Offer
                             -> Rejected
      -> Passed
```

Each job carries priority (0-3), interview stage (phone screen, technical, take-home,
onsite, final), application deadline, next action and its date, contact or referral,
compensation, and free-form notes. `applied_at` is stamped automatically on the first
move to Applied, and every status change is recorded in an `events` table and shown as
history in the detail panel.

The header tracks how the search is actually going: roles tracked, applications sent, the
share that reached an interview, and the share that produced any response at all.

Starring needs somewhere to write, which a static HTML file has no way to do, so the
board is served by a small stdlib server bound to localhost.

`state/jobs.db` is gitignored, since a daily-changing binary makes for useless history.
`state/tracker-export.json` is written on every change and committed instead, carrying
every field, and `store.import_json` rebuilds the database from it.

## Robustness

Every bug found in this repo was silent: nothing errored, roles just quietly never
appeared. Three layers exist to catch that class of failure.

```bash
./bin/test.sh                                            # 32 offline regression tests
python3 scripts/jobdigest.py verify --picks out/picks.json
```

**Regression tests** cover the logic. Each case is a bug that actually shipped, named for
its real symptom: the denylist letting "General Dynamics UK" through, then over-blocking
Intuit; "nato" matching inside unrelated words; body text tagging a Tesla mobile-app
internship as biotech; a feed listing vetoing the same role from its own board.

**Registry tests** cover the data. Xaira had a verified token and was still marked
`unresolved`, so its board was never scanned and its best role stayed invisible for a
day. A company with a token can no longer sit in `unresolved`.

**Invariant checks** cover the run. `verify` refuses to deliver a digest whose shortlist
contains a denylisted company, duplicates, or roles already sent. With `--picks` it also
checks the model's output against the shortlist, which is the one failure a static
pipeline cannot prevent on its own: nothing else stops a model inventing a URL.

`bin/run-local.sh` runs all three as gates. Tests failing, a harvest failing invariants,
or invalid picks each abort the run with a notification rather than sending a wrong
digest. Warnings do not block; they are printed on the digest itself.

## Gotchas

- **No silent fallbacks.** Every degraded path lands in `harvest-report.json:degraded` and
  must be repeated in the email. The previous design's state layer was a dead letter for
  24 consecutive runs and no run ever said so.
- **Never accept a board token without the identity check.** Greenhouse `figure` is Figure
  Lending, not Figure AI; `diligent` is Diligent Services. Both return 200.
- **Careers-page discovery needs a browser User-Agent.** Without one, sites return 403/429
  and the sweep silently finds nothing.
- **Denylist names match as whole words, one direction.** Exact matching let "General
  Dynamics UK" through; bidirectional substring matching then blocked Intuit via "Applied
  Intuition" and Mosaic via "SAIC".
- **Tripwire terms match on word boundaries, and weak terms need two hits.** Naive
  substring matching quarantined 127 of 1,511 postings, 96 of them from `nato` inside
  unrelated words.
- **Role and seniority gates apply to the title only.** New-grad phrases in posting body
  text are company boilerplate, and matching them pulled in recruiters and a Workplace
  Experience Manager.
- **A 404 from an ATS API is definitive; 5xx, timeouts and 429 are not.** Only a 404
  increments a board's failure counter, and a board retires after 5 of them.
- **Rank on board size, not just vertical.** Employer openings is the only free signal
  for "startup vs bank". Without it, Loblaw and TD Bank co-ops flooded the shortlist.
- **Fill per-category quotas, never one global top-N.** A single sort let feed records
  carrying a Winter 2027 term out-rank everything, so the board scan contributed 2 of 39
  slots while yielding 996 eligible roles.
