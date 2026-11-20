# SETUP.md: instructions for Claude

You are setting up this kit for a new user. Work through the steps in order. Talk to
them in plain language, ask a few questions at a time, and write every answer into a
file as you go so nothing lives only in chat. Read `PLAYBOOK.md` before step 3: it is
why the rules below exist.

## 1. Make their private copy

```bash
gh auth status                       # if not logged in: gh auth login
gh repo create my-job-search --private --template Jason-Latz/job-search-kit --clone
cd my-job-search
```

If they have no GitHub account or don't want one, `git clone` the kit, delete `.git`,
and `git init` a local repo instead (the daily run skips pushing when there is no
remote). **Never put their copy in a public repo**: `pipeline/state/` becomes their
application history and `profile/` their life story.

Check tools: `python3 --version` (3.10+), `claude --version`. Then run
`pipeline/bin/test.sh`; it must pass before anything else.

## 2. Interview: who they are

Ask for their current resume (PDF, Google Doc, LinkedIn export, or pasted text) and read
it fully. Then ask about each job and project, one at a time:

- What did you actually build or own? What was hard? What would you say in an interview?
- Numbers you can defend (users, latency, accuracy, revenue), and which are approximate.
- Anything on the resume you could not comfortably defend in an interview (leave it out).

Write one file per entry in `profile/experience/` from `profile/experience/_template.md`,
keeping **verified facts**, **their own words**, and **your interpretation** in separate
sections. Then fill `profile/about-me.md` (a ~150-word summary the ranking step reads).

## 3. Interview: what they want

Fill `profile/search-preferences.md` from their answers:

- Full-time, internship, or both? Start date and graduation date?
- Roles (SWE, ML, data, forward-deployed, product, research...) and seniority.
- Industries or problems ranked in order, and anything they will never work on.
- Company size and stage; equity vs. salary; cities, remote, relocation; visa needs.
- Their target date for an offer and hours per week they can actually give this.
- Standard form answers (work authorization, sponsorship, pronouns, start date,
  relocation, in-office preference) so they never retype them.

Then tune `pipeline/data/` to match (read the `*_notes` keys first; they explain each
setting and the bug that set it):

- `filters.json`: `priority_intern_terms` and the term lists, `vertical_priors`,
  `vertical_weight` (0 = most wanted), `shortlist_quotas` (big `new-grad` and
  `open-level` for a full-time search, big `internship-*` for an internship search),
  and the `blocked` denylist / tripwire terms. The shipped denylist excludes
  defense, weapons and intelligence work; ask whether they want that kept.
- `companies.json`: the watchlist. The shipped one is AI, health/bio, robotics, climate
  and legal-tech startups. Ask which companies they already love, add them with
  `python3 scripts/resolve.py "Name" domain.com` (it refuses a board whose title doesn't
  match the company), and remove verticals they don't care about. Watchlist companies get
  their plain "Software Engineer" roles scanned, not only "new grad" ones.

- **Graduation year.** The kit was built for the class of 2027. If theirs differs,
  update every year-specific string: `filters.json` (`hard_excludes.grad_window_closed`,
  the intern term lists), the new-grad phrases near the top of `scripts/jobdigest.py`
  and its digest section titles, the "Winter/Spring 2027" sorting in `scripts/store.py`
  and `scripts/tracker.py`, and the Simplify feed URL in `scripts/feeds.py` (e.g.
  `Summer2028-Internships`; check that repo exists first). `grep -rn 2027 pipeline/`
  finds them all; fix the tests that pin those values too.

Run `pipeline/bin/test.sh` again after editing data.

## 4. Resume

Ask whether they want the LaTeX resume (recommended: one file per entry, a build that
fails above one page, tailored copies per application) or to keep their own format.
For LaTeX: `brew install tectonic poppler`, then rewrite `resume/content/` from the
profile (replace the made-up example person entirely), and build with
`resume/bin/build.sh`. It must come out at one page with no overfull lines. Show them the
PNG preview in `resume/build/` and iterate on wording with them. Rules in
`resume/README.md`.

## 5. First run and schedule

```bash
cd pipeline
claude setup-token          # they run this; it prints a long-lived token
```

Have them paste the token into `~/.jobdigest.env` themselves (format in
`bin/jobdigest.env.example`, `chmod 600`). Never ask them to paste the token into chat
and never print it. Then:

```bash
./bin/run-local.sh          # first run: ~10-30 min; writes digests/<date>.html
open digests/latest.html
./bin/board.sh              # the tracker at http://127.0.0.1:8765
./bin/install.sh            # daily 09:00 run via launchd
```

Walk them through the board: star roles they'd apply to, pass on the rest, and use
**Browse all** to work through the backlog that was already open on day one.

Optional daily triage: offer to create a Claude Code scheduled task (desktop app) that
runs `python3 pipeline/scripts/apply_triage.py` each morning and lists that day's
resume-only applications vs. essay applications.

## 6. Hand over

Commit (`git add -A && git commit -m "Set up"` and push to their private remote). Then
tell them, in a few lines: where the digest appears, how to open the board, the one
habit that matters (`PLAYBOOK.md`, "apply within a week"), and that they can just ask
you things like "what should I apply to today?", "tailor my resume for this posting",
"draft a note to the founder", or "prep me for this OA".
