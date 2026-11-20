# CLAUDE.md

A job search run with Claude Code. If `profile/about-me.md` still has its template
placeholders, this copy has not been set up: follow `SETUP.md`. Otherwise this is the
user's own private repo; read `PLAYBOOK.md` once per session that does search work.

## Map

| Path | What it is | Start with |
|---|---|---|
| `profile/` | What the user actually did and wants. Source of truth for every resume, form and message | `profile/README.md` |
| `pipeline/` | Daily board scan, digest, tracker, apply triage | `pipeline/CLAUDE.md` |
| `resume/` | LaTeX resume and variants | `resume/README.md` |
| `applications/` | One folder per real application | `applications/README.md` |

## Commands

```bash
pipeline/bin/board.sh                           # open the tracker (status of every role)
pipeline/bin/run-local.sh                       # run the daily digest now
python3 pipeline/scripts/apply_triage.py        # today's roles split into quick vs essay forms
pipeline/bin/test.sh                            # pipeline gate: run before committing pipeline changes
resume/bin/build.sh                             # resume gate: every variant must be one page
```

## Rules

- **Never act outward without a yes in chat.** No submitting applications, sending
  emails or LinkedIn messages, accepting anything, or clicking consent boxes on their
  behalf. Drafts are always fine. Show the exact text before anything is sent.
- **One home per fact.** Role status (stage, deadline, next action) lives only in the
  tracker. Everything else about one application lives in its `applications/<dir>/notes.md`.
  What the user did lives in `profile/`. Don't restate a status anywhere else; link.
- **Write findings down as you go, with the source.** An email, a recruiter call, a
  deadline found on a posting: record it in the right file in the same turn, with where
  it came from and the date. Something only said in chat is lost to the next session.
- **Never invent.** Resumes and messages use only what is in `profile/`. Keep verified
  facts, the user's words, and your interpretations separate, and never promote an
  interpretation to a fact without the user saying so.
- **Tailor in copies.** `resume/variants/` is canonical; a tailored resume goes in that
  application's folder. Pick the variant that matches the company's actual work.
- **Dates go stale.** "No deadline" is true only the day it was checked. Re-open the live
  posting and its application form before planning around it, and date the check.
- **Check their email before trusting a status** if you have access: replies arrive
  between sessions, and they often send their own wording instead of your draft.
- **When the user states a standing requirement** ("always scan X", "never suggest Y"),
  write it into the relevant doc or config in the same turn, not just into chat.
- **Look it up instead of punting.** Answer anything a search can answer (a posting's
  requirements, a company's stage, OA formats) before replying.
