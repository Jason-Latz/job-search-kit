# job-search-kit

A job search run by Claude Code: a daily scan of ~2,000 company job boards, a ranked
digest of the roles that fit you, a local kanban tracker, a daily "apply to these today"
triage, a one-page LaTeX resume with tailored copies, and a playbook of what actually
worked (and what failed) in one new-grad search in fall 2026.

## Use it

Open Claude Code in an empty folder and paste:

> Set up my job search from https://github.com/Jason-Latz/job-search-kit. Follow its SETUP.md.

Claude will copy this into a **private** repo of your own, interview you (background,
what you want, what you won't do), build your profile and resume, tune the scanner to
you, and schedule the daily run. Plan on about an hour, mostly talking.

Requirements: a Mac (the daily run uses launchd and macOS notifications), Python 3.10+,
Claude Code, git, and the GitHub CLI (`gh`). Optional for the resume: `brew install
tectonic poppler`.

## What's inside

| Path | What it is |
|---|---|
| [`PLAYBOOK.md`](PLAYBOOK.md) | The learnings. Read this even if you use nothing else |
| [`SETUP.md`](SETUP.md) | Step-by-step setup your Claude follows |
| [`CLAUDE.md`](CLAUDE.md) | Standing rules your Claude works by in your copy |
| `pipeline/` | Board scanner, filters, ranking prompt, digest, tracker, apply triage, tests |
| `profile/` | What you actually did, in your words: the source for every resume and message |
| `resume/` | LaTeX resume: shared style, one file per entry, variants, a one-page build gate |
| `applications/` | One folder per real application: posting copy, notes, the resume you sent |

Nothing here applies, emails or submits for you. Claude drafts; you send.

## Privacy

Your copy holds your tracker, your notes and your resume. Keep it private. This public
repo contains no one's personal data, only code, templates and an example resume for a
made-up person.
