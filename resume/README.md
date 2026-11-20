# resume/

A one-page LaTeX resume. Needs `brew install tectonic poppler`.

- Wording of a job or project: `content/experience/` or `content/projects/`, one file each.
- Which entries a resume shows, and in what order: `variants/<name>.tex`
  (e.g. `general`, `ml`, `startup`).
- Fonts, margins, spacing: `resume.sty`. Change it here and every variant follows.

```bash
resume/bin/build.sh                         # build every variant into resume/build/
resume/bin/build.sh --watch general         # rebuild on save; open the PDF in Preview
resume/bin/build.sh applications/<dir>/Resume.tex   # a tailored copy; PDF lands beside it
```

The build fails, without touching the last good PDF, if a variant runs past one page,
text runs past the margin, or a bare `%` or `~` would silently eat text. Cut content
instead of shrinking the style.

Rules: content comes only from `../profile/`; tailor in copies, never in `content/`
unless a fact changed; bold the core technical contribution, not the scale; no periods
at the ends of bullets; nothing you can't defend in an interview. The files in
`content/` ship as a made-up example person and get replaced during setup.
