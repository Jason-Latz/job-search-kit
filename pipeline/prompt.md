Rank today's job shortlist and write the picks file.

`bin/run-local.sh` has already run the harvest. It will render and record whatever you
write. Your entire job is judgment: choose the best roles and write them up. Do not
re-run the pipeline, re-fetch boards, or edit anything under `scripts/` or `data/`.

## 1. Read

- `out/shortlist.json` — up to 40 pre-filtered candidates, best first
- `out/harvest-report.json` — run health and every degraded path

## 2. Choose up to 10

Read `../profile/about-me.md` (who I am) and `../profile/search-preferences.md` (what I
want, ranked, and what I never want) first. They decide fit; everything below is how to
read the shortlist's fields.

Rank on technical depth, company quality, role ownership, and fit with my background.

- `category: "open-level"` is a full-time role at a company on my watchlist that is not
  titled "new grad". The pipeline already dropped reqs asking for 4+ years of experience
  and non-US-only locations. `degree_ask: "graduate"` means the posting asks for an MS or
  PhD (often "or equivalent"); if I don't have one, say so in `why` and rank it as a
  reach. `min_years` is the experience the posting asks for (null means it never says);
  3 is a stretch worth considering, not a disqualifier. Read the posting: if it plainly
  wants a mid-level engineer, drop it; if a strong new grad could get it, treat it like
  a new-grad role.
- `employer_openings` is how many roles that employer has open. Small numbers mean a
  startup. A board with thousands of openings is a bank or a retailer, and those roles
  need to be genuinely excellent to earn a slot unless my preferences say I want big
  companies.
- `vertical` is a computed hint, ordered by my preferences file. Verticals lift, they
  never exclude: a strong role outside them still belongs.
- `on_watchlist` means a company I already track. Mild positive signal, not a free pass.
- `published_confidence: "upper_bound"` means the date is when the req was last edited,
  not posted. Say so rather than implying it is new.

Send fewer than 10 if fewer deserve it. Zero is a valid answer and an empty digest still
renders. Never pad.

The shortlist is filtered, not judged. Drop anything that is a weak role, a company not
worth my time, or anything my preferences file rules out that the mechanical gate missed.

## 3. Write `out/picks.json`

A JSON list, best first. Copy `url`, `company`, `title`, `category`, `subtype`,
`published_at`, `published_confidence`, `location`, `employer_openings` straight from the
shortlist entry, and add two of your own:

- `summary` — two sentences on what the role actually does, from the posting text, not
  the title. No marketing language.
- `why` — one sentence on why it fits me, and any caveat worth knowing.

```json
[{"url":"https://...","company":"Acme","title":"Software Engineer Intern",
  "category":"internship","subtype":"Winter 2027","published_at":"2026-09-05",
  "location":"Remote","employer_openings":14,
  "summary":"...","why":"..."}]
```

Write the file even when it is empty: `[]`. A missing file means the run broke, and the
runner treats those two cases differently.

## 4. Say what went wrong

If `harvest-report.json:degraded` is non-empty, the renderer surfaces it automatically.
Do not paper over it, and do not describe a degraded run as a clean one.

## About me

Everything about me lives in `../profile/`. Never invent experience I don't have when
writing `why`.
