# applications/

One folder per real application: `<company>-<role>/`, lowercase and hyphenated (for
example `acme-software-engineer-new-grad/`). Create one only once you are actually
applying; roles you are watching stay in the tracker. Start from `_template/`.

- `notes.md`: posting link, why it fits, contacts, dates, and a dated log.
- `posting.md`: a saved copy of the job description. Postings disappear.
- `answers.md`: your answers to the form's written questions, one `##` heading per
  question. `apply_triage.py` lists these headings as an answer bank, so later forms
  reuse earlier answers.
- Tailored resume, if any: `Resume.tex` and the `Resume.pdf` that was actually sent
  (`resume/bin/build.sh applications/<dir>/Resume.tex`).

Status (stage, deadline, next action) lives only in the tracker (`pipeline/bin/board.sh`).
Nothing is submitted or sent without your explicit yes.
