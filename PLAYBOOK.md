# PLAYBOOK.md: what one search learned

From a new-grad CS search in fall 2026 (aiming at AI/health/bio startups), run almost
entirely through Claude Code. Each lesson cost something to learn. Dates are when it
happened.

## Strategy

**A digest nobody acts on is worth nothing.** After seven weeks the scanner had surfaced
179 good roles and 176 were still untouched in the tracker. Finding roles was never the
bottleneck; applying was. Fixes that worked:

- **Apply within a week to anything that fits and is mostly a resume.** Don't wait for
  a better moment, a better resume line, or "more relevant background". Postings close
  fast: observed lifetimes ran from 4 to 96 days, several under a week.
- **Triage by form effort, daily.** `apply_triage.py` reads each role's actual
  application form and splits resume-only ("quick") from anything with a written
  question ("essay"). Do quick ones every day in a few minutes; batch essays into
  focused blocks. A form with any written question, optional included, is an essay
  application: fill optional questions too.
- **Warm beats cold.** Cold portal applications early in the search got no replies.
  Pair every application you care about with a person: a referral, a short note to a
  founder or hiring manager, an alum. Ask old managers and mentors directly; they
  usually offered intros nobody followed up on.
- **Do the math on timing.** Startups move ~2-4 weeks from first call to offer, so an
  offer by a date needs about a dozen first conversations started ~5 weeks before it.
  Write down the target date and the hours per week, and plan backwards.
- **Fellowships and programs are real doors.** Some (for example AI-lab fellowships)
  convert a large share of fellows to full-time offers. Track them like jobs, and open
  the application form early: one needed three references with written context, which
  can't be done in the last hour.

## Finding roles

- **Two discovery layers, both needed.** Structured new-grad/intern feeds cover ~1,700
  companies; a direct scan of a watchlist's own boards catches the rest and sees
  postings sooner.
- **Startups post plain "Software Engineer" and hire grads into it.** If you only match
  "new grad" titles you miss most of them. Watchlist boards are scanned for plain
  titles, guarded by "3+ years required" and location checks.
- **The watchlist only grows if someone adds to it.** A company the user loved was never
  scanned because nobody added it, and 11 companies named in a research doc had never
  made it into the list. Whenever a company comes up (research, a friend, an event),
  add its board. Mine VC portfolio job boards (YC Work at a Startup, a16z, others) and
  fresh funding rounds (SEC Form D filings) for companies about to hire.
- **Every silent filter bug hid good roles.** Examples that shipped: one global
  "newest first" sort made 3,224 of 4,275 eligible roles unreachable; "staff" as a
  seniority word dropped every "Member of Technical Staff, New Grad" role; marking a URL
  seen before filtering let a worse copy of a posting veto the better one. Use bucket
  quotas, log every drop with its reason, and read the drop log after changing a rule.
- **A job board token can belong to a different company.** Greenhouse `figure` is a
  lending company, not the robotics one. Always check the board's title matches.

## Resume

- **Profile first, resume second.** Keep a file per job/project with verified facts,
  your own words, and interpretations kept apart. Every resume, cover letter and form
  answer draws from it, so nothing gets invented and nothing needs re-interviewing.
- **One page, enforced by the build.** A variant that spills over fails the build
  instead of quietly shrinking the font.
- **Bold the core technical contribution, not the scale.** "custom tokenizer for
  medical codes", not "800,000+ records". Explain internal products in plain words. For forward-deployed
  roles, show repeated customer meetings.
- **Leave out anything you can't defend in an interview**, and anything a recruiter
  can't place without context.
- **Pick the variant that matches the company's real work**, not its buzzwords; a
  healthcare-operations company is not biotech. If none fits, tailor a copy.
- Style: no periods at the ends of bullets, no hedges like "roughly", no "current"
  tags. State supported claims directly.

## Messages

- **Short, informal, direct.** Who you are in one line, real enthusiasm, one concrete
  ask, thanks. No attachments unless asked.
- **Never recite the recipient's work back to them** to prove you read it. It reads as
  box-checking. One short reason you're writing to them is enough.
- **Follow-ups are a short warm bump**: what's new, still interested, one ask. No
  re-introduction or re-pitch.
- Don't put technical terms in your own voice that you can't discuss.
- Profiles (LinkedIn, talent marketplaces): human-readable outcomes first, a compact
  keyword line at the bottom for search.

## Online assessments and interviews

- **Search the OA aggregators the day the invite arrives**, by company name: FastPrep,
  PracHub, GitHub OA-question repos, Reddit r/leetcode and r/csMajors. One search found
  "almost nothing"; on test day those sites turned out to list ~10 recent questions for
  that exact company. File what you find in the application's notes.

## Working with Claude on this

- Record every finding in a file with its source, in the same turn. Chat is forgotten.
- Keep facts, the user's stated preferences, and Claude's proposals separate and dated.
- Re-check anything with a date before planning around it.
- When you state a standing rule ("always scan VC boards"), make Claude write it into
  the config or docs right then. One such request was made "so many times" and never
  recorded, so it was never built.
- Claude drafts and prepares; you click submit and send.
