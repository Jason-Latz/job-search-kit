"""Apply triage: which application-form fields count as essays (2026-11-16).

Cases come from the first live run over 92 picks, where form chores that happen to be
free-text boxes (Goodfire's "How did you hear about us?", bot-check word puzzles,
"Did you use an AI tool?") would otherwise push a quick application into the essay pile.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import apply_triage as at  # noqa: E402


class FieldClassification(unittest.TestCase):
    def kind(self, label, ftype="textarea", required=True):
        return at.classify_field(label, ftype, required)

    def test_standard_fields_take_no_effort(self):
        for label in ["First Name", "Email", "LinkedIn Profile", "How did you hear about us?",
                      "Please can you tell us how you found us?", "When can you start?",
                      "U.S. Work Authorization Status", "Google Scholar or Publications",
                      "Due to the high volume of bot applications, please tell us what is "
                      "the twelfth word in the first paragraph",
                      "We welcome responsible AI use. Did you use an AI tool to tailor your resume?",
                      "What other roles or paths are you weighing right now?",
                      "Export Control and Compliance: please list citizenships"]:
            self.assertIsNone(self.kind(label), label)

    def test_open_questions_are_essays(self):
        self.assertEqual(self.kind("Why do you want to work at Goodfire?"), "essay")
        self.assertEqual(self.kind("What excites you most about Periodic Labs?", "String"), "essay")
        self.assertEqual(self.kind("What AI tools are you currently using today?", "LongText"), "essay")

    def test_cover_letter_counts_only_when_required(self):
        self.assertIsNone(self.kind("Cover Letter", "file", False))
        self.assertEqual(self.kind("Cover Letter", "file", True), "essay")

    def test_non_text_widgets_are_never_essays(self):
        self.assertIsNone(self.kind("Why do you want to work here?", "ValueSelect"))

    def test_optional_essay_makes_role_essay(self):
        # Optional writing questions get filled too, so they cost a focused block.
        s = at.summarize([("Name", "String", True),
                          ("Link a paper you like and write a blurb", "LongText", False)])
        self.assertEqual(s["tier"], "essay")
        self.assertEqual(len(s["essays_optional"]), 1)

    def test_lever_form_parse(self):
        page = ('<li class="application-question custom-question"><div class="application-label">'
                'Why us? <span>✱</span></div><textarea required></textarea></li>'
                '<li class="application-question"><div class="application-label">Pronouns</div>'
                '<input type="checkbox"></li>')
        self.assertEqual(at.lever_fields(page),
                         [("Why us? <span>✱</span>", "textarea", True), ("Pronouns", "select", False)])
        self.assertEqual(at.summarize(at.lever_fields(page))["essays_required"], ["Why us?"])


if __name__ == "__main__":
    unittest.main()
