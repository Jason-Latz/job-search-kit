"""Regression tests. Every case here is a bug that actually shipped.

These bugs were all silent: nothing errored, roles just quietly never appeared. A test
suite is the only thing that catches that class of failure, so each fix gets a named
case describing the real symptom.

Run with: ./bin/test.sh
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ats            # noqa: E402
import boards         # noqa: E402
import jobdigest      # noqa: E402
import runctl         # noqa: E402

FILTERS = json.loads((ROOT / "data" / "filters.json").read_text())
BLOCKED = FILTERS["blocked_companies"]


def ctx(seen=None, window=7):
    return {
        "filt": FILTERS, "seen": seen or {}, "blocked": BLOCKED,
        "watchlist": {"xaira": "health-bio"},
        "priors": FILTERS.get("vertical_priors", {}),
        "strong": FILTERS.get("tripwire_strong", []),
        "weak": FILTERS.get("tripwire_weak", []),
        "hard": FILTERS.get("hard_excludes", {}), "window": window,
    }


def rec(**kw):
    base = {"source": "feed", "company": "Acme", "title": "Software Engineer Intern",
            "url": "https://example.com/1", "location": "", "published_at": "2026-09-06",
            "published_confidence": "exact", "age_days": 2, "feed_category": "Software",
            "terms": ["Winter 2027"], "degrees": [], "text": ""}
    base.update(kw)
    return base


def run(records, context=None):
    """-> (kept, drop counts)"""
    import collections
    counts, kept, quarantined, urls = collections.Counter(), [], [], set()
    jobdigest.keep_records(records, context or ctx(), counts, kept, quarantined, urls)
    return kept, counts


class Denylist(unittest.TestCase):
    def test_subsidiary_is_blocked(self):
        """'General Dynamics UK' led a shortlist while 'General Dynamics' was listed."""
        self.assertTrue(jobdigest.is_blocked("General Dynamics UK", BLOCKED))
        self.assertTrue(jobdigest.is_blocked("General Dynamics Mission Systems", BLOCKED))

    def test_substring_lookalikes_are_not_blocked(self):
        """Matching substrings both ways blocked Intuit, Mosaic and Upscale AI."""
        for safe in ("Intuit", "Mosaic", "Upscale AI", "Primer", "Quantum Machines"):
            self.assertFalse(jobdigest.is_blocked(safe, BLOCKED), safe)

    def test_shortened_name_caught_via_posting_text(self):
        """Feeds shorten 'Applied Intuition' to 'Applied', which slipped the denylist."""
        kept, counts = run([rec(company="Applied", title="Software Engineer New Grad",
                                terms=[], text="Applied Intuition, Inc. is powering the "
                                               "future of physical AI.")])
        self.assertEqual(kept, [])
        self.assertEqual(counts["defense_quarantined"], 1)

    def test_federal_lab_is_blocked(self):
        """Johns Hopkins APL reached a shortlist as a robotics role."""
        self.assertTrue(jobdigest.is_blocked("Johns Hopkins Applied Physics Laboratory", BLOCKED))


class Tripwire(unittest.TestCase):
    def test_word_boundaries(self):
        """Substring matching quarantined 96 postings on 'nato' inside other words."""
        self.assertEqual(jobdigest.hits("donato senior coordinator", ["nato"]), [])
        self.assertEqual(jobdigest.hits("nato liaison", ["nato"]), ["nato"])

    def test_single_strong_term_quarantines(self):
        kept, counts = run([rec(title="Software Engineer - Active TS/SCI", terms=[])])
        self.assertEqual(kept, [])
        self.assertEqual(counts["defense_quarantined"], 1)

    def test_one_weak_term_is_not_enough(self):
        """Weak terms appear in EEO boilerplate on almost every posting."""
        weak = FILTERS["tripwire_weak"][0]
        kept, _ = run([rec(text=f"We are an equal opportunity employer. {weak} status.")])
        self.assertEqual(len(kept), 1)


class Classification(unittest.TestCase):
    def test_newgrad_matches_title_only(self):
        """Body text is boilerplate: it pulled in a Molten Salt Engineer as new-grad."""
        r = rec(source="ats", title="Molten Salt Engineer", terms=[], feed_category=None,
                text="We hire new grad candidates across all teams.")
        self.assertEqual(jobdigest.classify(r, FILTERS)[0], None)

    def test_intern_title_is_an_internship(self):
        r = rec(source="ats", title="Software Engineering Intern", terms=[], feed_category=None)
        self.assertEqual(jobdigest.classify(r, FILTERS)[0], "internship")

    def test_past_terms_are_ineligible(self):
        self.assertEqual(jobdigest.classify(rec(terms=["Summer 2026"]), FILTERS)[0], None)

    def test_priority_term_recognised(self):
        self.assertEqual(jobdigest.classify(rec(terms=["Winter 2027"]), FILTERS),
                         ("internship", "Winter 2027"))


class CorpusMerge(unittest.TestCase):
    def test_token_case_does_not_duplicate_a_board(self):
        """Sierra was scanned as both 'Sierra' and 'sierra', doubling its roles in the pool."""
        import boards
        corpus = {"boards": [{"platform": "ashby", "token": "Sierra", "company": "Sierra",
                              "status": "active"}]}
        added = boards.merge(corpus, {("ashby", "sierra"): "Sierra",
                                      ("ashby", "Decagon"): "Decagon",
                                      ("ashby", "decagon"): "Decagon"}, "2026-11-02")
        self.assertEqual((added, len(corpus["boards"])), (1, 2))


class OpenLevel(unittest.TestCase):
    """Watchlist startups post plain titles; 231 of their technical roles were being dropped."""

    def ats(self, **kw):
        base = dict(source="ats", company="Chai Discovery", title="Software Engineer, AI Platform",
                    terms=[], feed_category=None, location="San Francisco",
                    board_key=("ashby", "chaidiscovery"), min_years=None)
        base.update(kw)
        return rec(**base)

    def watch_ctx(self):
        c = ctx()
        c["watch_boards"] = {("ashby", "chaidiscovery"): "health-bio"}
        return c

    def test_watchlist_plain_title_is_kept_as_open_level(self):
        kept, _ = run([self.ats()], self.watch_ctx())
        self.assertEqual([(r["category"], r["on_watchlist"]) for r in kept], [("open-level", True)])

    def test_matched_by_board_when_names_differ(self):
        """A corpus board keeps the feed's name ('Cursor'), not the watchlist's ('Anysphere')."""
        kept, _ = run([self.ats(company="Cursor")], self.watch_ctx())
        self.assertEqual(len(kept), 1)

    def test_corpus_board_plain_title_still_dropped(self):
        kept, counts = run([self.ats(company="Bank", board_key=("greenhouse", "bank"))],
                           self.watch_ctx())
        self.assertEqual((kept, counts["not_eligible_category"]), ([], 1))

    def test_experience_ask_drops(self):
        kept, counts = run([self.ats(min_years=5)], self.watch_ctx())
        self.assertEqual((kept, counts["too_experienced"]), ([], 1))

    def test_non_us_location_drops_but_mixed_stays(self):
        kept, counts = run([self.ats(location="Stockholm, Sweden")], self.watch_ctx())
        self.assertEqual((kept, counts["outside_us"]), ([], 1))
        kept, _ = run([self.ats(location="London or New York")], self.watch_ctx())
        self.assertEqual(len(kept), 1)

    def test_hardware_and_wet_lab_reqs_drop(self):
        """The first open-level harvest kept data-center electricians and mRNA scientists."""
        for t in ["Data Center Electrical Engineer", "Scientist, mRNA Analytical Development",
                  "Reservoir Engineer, Modeling"]:
            kept, _ = run([self.ats(title=t)], self.watch_ctx())
            self.assertEqual(kept, [], t)
        kept, _ = run([self.ats(title="Scientist I, Computational Protein Design")], self.watch_ctx())
        self.assertEqual(len(kept), 1)

    def test_watchlist_drops_are_logged_with_reason(self):
        """The worry is that the filter drops solid jobs; every drop has to be auditable."""
        c = self.watch_ctx()
        run([self.ats(min_years=5), self.ats(company="Bank", board_key=("greenhouse", "bank"),
                                             url="https://example.com/2", min_years=5)], c)
        self.assertEqual([d["reason"] for d in c["watch_dropped"]], ["too_experienced"])

    def test_member_of_technical_staff_is_not_senior(self):
        """'staff' dropped Decagon's 'Member of Technical Staff - New Grad (2027 Start)'."""
        kept, _ = run([self.ats(title="Member of Technical Staff - New Grad (2027 Start)")],
                      self.watch_ctx())
        self.assertEqual(len(kept), 1)
        kept, _ = run([self.ats(title="Senior Member of Technical Staff")], self.watch_ctx())
        self.assertEqual(kept, [])

    def test_entry_level_roman_numerals_and_research_titles_kept(self):
        for t in ["ML Scientist I/II, AI for Protein Engineering", "Research Scientist",
                  "Forward Deployment Engineer (Residency Program)", "Partner Deployed Engineer"]:
            kept, _ = run([self.ats(title=t)], self.watch_ctx())
            self.assertEqual(len(kept), 1, t)
        kept, _ = run([self.ats(title="Research Scientist I/II, Immunology")], self.watch_ctx())
        self.assertEqual(kept, [])

    def test_lawyer_reqs_drop(self):
        kept, _ = run([self.ats(title="Legal Engineer (In-House)")], self.watch_ctx())
        self.assertEqual(kept, [])

    def test_min_years_reads_experience_asks_only(self):
        self.assertEqual(jobdigest.min_years("3+ years of professional software experience"), 3)
        self.assertEqual(jobdigest.min_years("1-2 years of industry experience or a strong degree"), 1)
        self.assertIsNone(jobdigest.min_years("We grew 5 years in a row. Great benefits."))
        self.assertEqual(jobdigest.min_years("five or more years of software development experience"), 5)

    def test_graduate_degree_ask_is_flagged(self):
        """Goodfire's 'MS/PhD or equivalent experience' reached an application unflagged."""
        for t in ["MS/PhD or equivalent experience in CS", "a Master's or PhD in biology",
                  "PhD in machine learning preferred"]:
            self.assertEqual(jobdigest.degree_ask(t), "graduate", t)
        self.assertIsNone(jobdigest.degree_ask("BS in Computer Science or equivalent experience"))


class DegreeGate(unittest.TestCase):
    def test_phd_only_is_dropped(self):
        kept, counts = run([rec(degrees=["PhD", "Master's"])])
        self.assertEqual(kept, [])
        self.assertEqual(counts["degree_gated"], 1)

    def test_bachelors_listed_is_kept(self):
        kept, _ = run([rec(degrees=["Bachelor's", "PhD"])])
        self.assertEqual(len(kept), 1)

    def test_unspecified_degrees_are_kept(self):
        kept, _ = run([rec(degrees=[])])
        self.assertEqual(len(kept), 1)


class VerticalLabelling(unittest.TestCase):
    def test_benefits_boilerplate_is_not_biotech(self):
        """Every posting's benefits section says 'health'; this consumed biotech slots."""
        r = rec(company="Tesla", title="Mobile App Intern",
                text="We offer excellent health insurance, dental and vision coverage "
                     "and a wellness stipend to every employee and their family.")
        self.assertNotEqual(jobdigest.guess_vertical(r, FILTERS["vertical_priors"]), "health-bio")

    def test_battery_cells_are_not_biotech(self):
        r = rec(company="Tesla", title="Cell Engineering Intern - Cell Qualification")
        self.assertNotEqual(jobdigest.guess_vertical(r, FILTERS["vertical_priors"]), "health-bio")

    def test_real_biotech_is_labelled(self):
        r = rec(company="Xaira Therapeutics",
                title="AI Scientist Intern, Computational Protein Design")
        self.assertEqual(jobdigest.guess_vertical(r, FILTERS["vertical_priors"]), "health-bio")


class CrossLayerDedup(unittest.TestCase):
    def test_dropped_feed_record_does_not_veto_the_board(self):
        """The same posting arrives from a feed and from its own board with different
        metadata. Marking the URL seen before filtering let the first one win, which is
        what hid Xaira's protein design internship."""
        url = "https://job-boards.greenhouse.io/xairatherapeutics/jobs/1"
        from_feed = rec(url=url, degrees=["PhD"], title="AI Scientist Intern")
        from_board = rec(url=url, source="ats", degrees=[], terms=[], feed_category=None,
                         title="AI Scientist Intern, Computational Protein Design")
        kept, _ = run([from_feed, from_board])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "ats")

    def test_kept_record_claims_the_url(self):
        url = "https://example.com/dup"
        kept, _ = run([rec(url=url), rec(url=url, company="Other")])
        self.assertEqual(len(kept), 1)


class ResponseClassification(unittest.TestCase):
    def test_404_is_definitive_not_transient(self):
        """Only a definitive 404 may retire a board."""
        self.assertFalse(ats.Response("u", 404).transient)

    def test_server_errors_are_transient(self):
        for code in (500, 502, 503, 429, 0):
            self.assertTrue(ats.Response("u", code).transient, code)


class PostingKey(unittest.TestCase):
    def test_application_suffix_and_casing_match(self):
        """Feeds link to {uuid}/application; the board API returns {uuid}."""
        a = "https://jobs.ashbyhq.com/Sierra/02e1c456-8489-4a74-9fe7-af8845b040e4/application"
        b = "https://jobs.ashbyhq.com/sierra/02e1c456-8489-4a74-9fe7-af8845b040e4"
        self.assertEqual(jobdigest.posting_key(a), jobdigest.posting_key(b))

    def test_greenhouse_numeric_ids_match(self):
        a = "https://job-boards.greenhouse.io/acme/jobs/8174099"
        b = "https://boards.greenhouse.io/acme/jobs/8174099?gh_src=x"
        self.assertEqual(jobdigest.posting_key(a), jobdigest.posting_key(b))


class BoardText(unittest.TestCase):
    def test_inline_images_are_dropped(self):
        """Persona AI's postings open with a banner, and Ashby's plain-text field keeps it
        as a data URI: 1,477 of the 1,500 characters the model saw were a PNG."""
        payload = {"jobs": [{"title": "Robotics Intern", "jobUrl": "https://jobs.ashbyhq.com/p/1",
                             "descriptionPlain": "[data:image/png;base64,iVBORw0KGgo+/A==]\n"
                                                 + ROLE}]}
        (job,) = jobdigest.normalize_ats({"platform": ats.ASHBY, "token": "p"}, payload)
        self.assertEqual(job["text"], ROLE)

    def test_non_utf8_page_does_not_raise(self):
        """Strict decoding raised out of fetch_all and aborted the whole harvest."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".html") as f:
            f.write("caf\xe9 \xa9".encode("latin-1"))
            f.flush()
            self.assertIn("caf", ats.fetch("file://" + f.name).body)


class AshbySlugs(unittest.TestCase):
    def test_dotted_slug_is_not_truncated(self):
        """A word-character pattern cut persona.ai to persona, which is a different real
        company's board: Persona AI's robotics roles went missing and Persona's sales
        roles were scanned in their place."""
        url = "https://jobs.ashbyhq.com/persona.ai/1e8d195c-e28e/application?embed=true"
        self.assertEqual(list(boards.extract([url])), [(ats.ASHBY, "persona.ai")])

    def test_encoded_slug_round_trips(self):
        """Flock%20Safety was truncated to Flock, another company's board."""
        url = "https://jobs.ashbyhq.com/Flock%20Safety/abc"
        self.assertEqual(list(boards.extract([url])), [(ats.ASHBY, "Flock Safety")])
        self.assertIn("/job-board/Flock%20Safety?", ats.board_url(ats.ASHBY, "Flock Safety"))


# Long enough to count as a description rather than a teaser.
ROLE = ("Build autonomy software for mining trucks: perception, planning and controls, "
        "shipped to haul trucks running around the clock on real mine sites.")


def ld_page(*postings) -> str:
    return "".join('<script type="application/ld+json">' + json.dumps(p) + "</script>"
                   for p in postings) + '<body><div id="root"></div></body>'


class PostingText(unittest.TestCase):
    """The shortlist backfill for postings whose page is a client-rendered shell. Ten of
    the 2026-09-09 shortlist's 39 candidates reached the model with no text at all, and
    the model dropped every one of them."""

    def test_workday_description_is_read_from_jsonld(self):
        """Workday pages carry the description only as JobPosting JSON-LD, which
        strip_html deletes with the other script blocks."""
        body = ld_page({"@context": "https://schema.org", "@type": "JobPosting",
                        "description": f"<p>{ROLE}</p>"})
        self.assertEqual(jobdigest.strip_html(body), "")
        self.assertEqual(jobdigest.page_text(body), ROLE)

    def test_jsonld_inside_a_graph_with_a_type_list(self):
        body = ld_page({"@graph": [{"@type": "Organization", "name": "Acme"},
                                   {"@type": ["JobPosting"], "description": ROLE}]})
        self.assertEqual(jobdigest.page_text(body), ROLE)

    def test_first_posting_wins(self):
        """A page listing related jobs must not hand over another job's text."""
        other = ROLE.replace("mining trucks", "a different job")
        body = ld_page([{"@type": "JobPosting", "description": ROLE},
                        {"@type": "JobPosting", "description": other}])
        self.assertEqual(jobdigest.page_text(body), ROLE)

    def test_teaser_and_malformed_blocks_do_not_hide_the_description(self):
        body = ld_page({"@type": "JobPosting", "description": "Join us!"},
                       {"@type": "JobPosting", "description": {"text": "not a string"},
                        "@graph": 7},
                       {"@type": "JobPosting", "description": ROLE})
        self.assertEqual(jobdigest.page_text(body), ROLE)

    def test_page_without_structured_data_falls_back_to_its_text(self):
        self.assertEqual(jobdigest.page_text("<p>Plain posting</p>"), "Plain posting")

    def test_apple_hydration_payload(self):
        job = {"jobSummary": "Graphics software.", "minimumQualifications": "C++."}
        payload = {"loaderData": {"jobDetails": {"jobsData": job}}}
        body = ("<script>window.__staticRouterHydrationData = JSON.parse("
                + json.dumps(json.dumps(payload)) + ");</script>")
        self.assertEqual(jobdigest.page_text(body), "Graphics software. C++.")
        empty = {"loaderData": {"jobDetails": {"jobsData": None}}}
        body = ("<script>window.__staticRouterHydrationData = JSON.parse("
                + json.dumps(json.dumps(empty)) + ");</script>")
        self.assertEqual(jobdigest.page_text(body), "")          # falls through, no raise

    def test_shell_pages_route_to_their_json_endpoint(self):
        cases = {
            "https://apply.workable.com/zaber/j/1F627CC2BB/apply":
                "https://apply.workable.com/api/v1/accounts/zaber/jobs/1F627CC2BB",
            "http://x.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/4722":
                "https://x.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCE"
                "JobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%224722%22,"
                "siteNumber=CX_1",
            "https://contentguru.bamboohr.com/careers/324/":
                "https://contentguru.bamboohr.com/careers/324/detail",
        }
        for page, api in cases.items():
            self.assertEqual(jobdigest.posting_source(page)[0], api, page)
        workday = "https://cat.wd5.myworkdayjobs.com/Careers/job/Peoria/Engineer_R1"
        self.assertEqual(jobdigest.posting_source(workday), (workday, None))

    def test_company_hosted_greenhouse_goes_to_the_embed(self):
        """zipline.com/open-roles?gh_jid= is navigation only, and 295 characters of it
        passed the length check as if it were a description."""
        src, extract = jobdigest.posting_source(
            "https://www.zipline.com/open-roles?gh_jid=7990420003")
        self.assertEqual(src, "https://boards.greenhouse.io/embed/job_app?token=7990420003")
        self.assertIsNone(extract)

    def test_backfill_reads_each_posting_from_its_best_source(self):
        """The fix lives in the harvest's backfill stage, so drive that stage itself."""
        workable = "https://apply.workable.com/zaber/j/1F627CC2BB/apply"
        workday = "https://cat.wd5.myworkdayjobs.com/Careers/job/Peoria/Engineer_R1"
        closed = "https://bmo.wd3.myworkdayjobs.com/External/job/Toronto/Analyst_R2"
        served = {
            "https://apply.workable.com/api/v1/accounts/zaber/jobs/1F627CC2BB":
                ats.Response("", 200, json.dumps({"description": f"<p>{ROLE}</p>"})),
            workday: ats.Response("", 200, ld_page({"@type": "JobPosting",
                                                    "description": ROLE})),
            closed: ats.Response("", 200, '<body><div id="root"></div></body>'),
        }
        requested = []

        def fake_fetch_all(urls, **_):
            requested.extend(urls)
            return [served[u] for u in urls]

        records = [{"url": u, "text": ""} for u in (workable, workday, closed)]
        jobdigest.backfill_pages(records, fetch_all=fake_fetch_all)
        self.assertNotIn(workable, requested)          # the JSON endpoint, not the shell
        self.assertEqual([r["text"] for r in records], [ROLE, ROLE, ""])
        self.assertEqual(records[2]["fetch_status"], 200)

    def test_oracle_reads_only_the_external_fields(self):
        """Internal* repeats the posting for employees; Corporate* is boilerplate."""
        _, extract = jobdigest.posting_source(
            "https://x.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1")
        item = {"ExternalDescriptionStr": "<p>Role.</p>", "InternalDescriptionStr": "Role.",
                "ExternalQualificationsStr": "Python.", "CorporateDescriptionStr": "Blurb."}
        self.assertEqual(jobdigest.strip_html(extract({"items": [item]})), "Role. Python.")
        self.assertEqual(extract({"items": []}).strip(), "")


class SeenJobs(unittest.TestCase):
    def test_already_sent_is_excluded(self):
        kept, counts = run([rec(url="https://example.com/sent")],
                           ctx(seen={"https://example.com/sent": {"date_sent": "2026-09-01"}}))
        self.assertEqual(kept, [])
        self.assertEqual(counts["already_sent"], 1)


class Config(unittest.TestCase):
    def test_quotas_do_not_exceed_shortlist(self):
        self.assertLessEqual(sum(FILTERS["shortlist_quotas"].values()), 40)

    def test_no_control_characters_in_source(self):
        """A shell-escaping accident wrote literal backspaces into a regex, silently
        disabling the denylist until it was noticed by eye."""
        for path in (ROOT / "scripts").glob("*.py"):
            body = path.read_text()
            for ch in ("\x08", "\x0c", "\x1b"):
                self.assertNotIn(ch, body, f"{path.name} contains a control character")


class Registry(unittest.TestCase):
    """Data consistency. Xaira had a verified token and was still marked unresolved, so
    its board was never scanned and its best role stayed invisible for a whole day."""

    def setUp(self):
        self.reg = json.loads((ROOT / "data" / "companies.json").read_text())["companies"]

    def test_a_company_with_a_token_is_not_unresolved(self):
        stranded = [c["name"] for c in self.reg
                    if c.get("token") and c["status"] == "unresolved"]
        self.assertEqual(stranded, [], f"resolved but not scanned: {stranded}")

    def test_scannable_companies_have_a_platform_and_token(self):
        broken = [c["name"] for c in self.reg
                  if c["status"] in ("ok", "review") and not (c.get("token") and c.get("platform"))]
        self.assertEqual(broken, [])

    def test_no_duplicate_board_tokens(self):
        pairs = [(c.get("platform"), c.get("token")) for c in self.reg if c.get("token")]
        dupes = [x for x in set(pairs) if pairs.count(x) > 1]
        self.assertEqual(dupes, [])

    def test_corpus_has_no_duplicates(self):
        boards_file = ROOT / "data" / "boards.json"
        if not boards_file.exists():
            self.skipTest("corpus not built yet")
        rows = json.loads(boards_file.read_text())["boards"]
        pairs = [(b["platform"], b["token"]) for b in rows]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_denylist_entries_are_specific_enough(self):
        """A denylist entry of one or two characters would match half the corpus."""
        vague = [b for b in BLOCKED if len(b.strip()) < 3]
        self.assertEqual(vague, [])

    def test_quota_names_match_the_buckets_the_code_emits(self):
        expected = {"biotech", "open-level", "internship-priority", "new-grad", "standing",
                    "internship-other"}
        self.assertEqual(set(FILTERS["shortlist_quotas"]), expected)


class Pool(unittest.TestCase):
    """The browse layer. The daily digest only ever surfaces what is new, so without a
    queryable pool the several thousand roles already open stay unreachable."""

    def setUp(self):
        import tempfile
        import store
        self.store = store
        self.tmp = tempfile.TemporaryDirectory()
        self._db, self._export = store.DB, store.EXPORT
        store.DB = pathlib.Path(self.tmp.name) / "t.db"
        store.EXPORT = pathlib.Path(self.tmp.name) / "t.json"
        self.con = store.connect()
        self.store.replace_pool(self.con, [
            {"url": "u1", "company": "Xaira Therapeutics", "title": "AI Scientist Intern",
             "category": "internship", "subtype": "Winter 2027", "vertical": "health-bio",
             "employer_openings": 22, "age_days": 10, "is_new": False, "on_watchlist": True},
            {"url": "u2", "company": "BigBank", "title": "Analyst Co-op",
             "category": "internship", "subtype": "Winter 2027", "vertical": None,
             "employer_openings": 4000, "age_days": 1, "is_new": True, "on_watchlist": False},
            {"url": "u3", "company": "Acme Robotics", "title": "Robotics Intern",
             "category": "internship", "subtype": "Summer 2027", "vertical": "robotics",
             "employer_openings": 12, "age_days": 3, "is_new": True, "on_watchlist": False},
        ])

    def tearDown(self):
        self.con.close()
        self.store.DB, self.store.EXPORT = self._db, self._export
        self.tmp.cleanup()

    def test_pool_is_replaced_not_appended(self):
        self.store.replace_pool(self.con, [{"url": "only", "company": "X", "title": "Y"}])
        self.assertEqual(self.store.query_pool(self.con)["total"], 1)

    def test_biotech_sorts_first_by_default(self):
        rows = self.store.query_pool(self.con)["rows"]
        self.assertEqual(rows[0]["company"], "Xaira Therapeutics")

    def test_filters(self):
        q = self.store.query_pool
        self.assertEqual(q(self.con, vertical="health-bio")["total"], 1)
        self.assertEqual(q(self.con, subtype="Winter 2027")["total"], 2)
        self.assertEqual(q(self.con, max_openings=60)["total"], 2)
        self.assertEqual(q(self.con, q="robot")["total"], 1)

    def test_starring_removes_a_role_from_browse(self):
        """Catching up means a role you have acted on stops reappearing."""
        self.store.star_from_pool(self.con, "u1")
        self.assertEqual(self.store.query_pool(self.con, vertical="health-bio")["total"], 0)
        self.assertEqual(self.store.counts(self.con)["starred"], 1)

    def test_starring_from_pool_creates_a_tracked_job(self):
        self.store.star_from_pool(self.con, "u1")
        row = self.con.execute(
            "SELECT j.company, t.status FROM jobs j JOIN tracking t ON t.url=j.url "
            "WHERE j.url='u1'").fetchone()
        self.assertEqual(row["company"], "Xaira Therapeutics")
        self.assertEqual(row["status"], "starred")

    def test_unknown_url_raises(self):
        with self.assertRaises(KeyError):
            self.store.star_from_pool(self.con, "nope")

    def test_facets_only_count_untouched_roles(self):
        self.store.star_from_pool(self.con, "u3")
        self.assertNotIn("robotics", self.store.pool_facets(self.con)["vertical"])


class BoardFields(unittest.TestCase):
    """Editable per-job fields and the numbers on the board."""

    def setUp(self):
        import tempfile
        import store
        self.store = store
        self.tmp = tempfile.TemporaryDirectory()
        self._db, self._export = store.DB, store.EXPORT
        store.DB = pathlib.Path(self.tmp.name) / "t.db"
        store.EXPORT = pathlib.Path(self.tmp.name) / "t.json"
        self.con = store.connect()
        store.upsert_jobs(self.con, [{"url": "u1", "company": "Xaira", "title": "Intern"}])

    def tearDown(self):
        self.con.close()
        self.store.DB, self.store.EXPORT = self._db, self._export
        self.tmp.cleanup()

    def test_migration_is_idempotent(self):
        self.assertEqual(self.store.migrate(self.con), [])

    def test_unknown_fields_are_ignored_not_written(self):
        r = self.store.update_fields(self.con, "u1", {"priority": 2, "status": "offer",
                                                      "url": "hacked", "bogus": 1})
        self.assertEqual(r["updated"], ["priority"])
        row = self.con.execute("SELECT status, url FROM tracking").fetchone()
        self.assertEqual((row["status"], row["url"]), ("new", "u1"))

    def test_priority_is_clamped(self):
        self.store.update_fields(self.con, "u1", {"priority": 99})
        self.assertEqual(self.con.execute("SELECT priority FROM tracking").fetchone()[0], 3)

    def test_notes_survive_a_status_change(self):
        self.store.update_fields(self.con, "u1", {"notes": "spoke to a founder"})
        self.store.set_status(self.con, "u1", "applied")
        self.assertEqual(self.store.job_detail(self.con, "u1")["notes"], "spoke to a founder")

    def test_unknown_url_raises(self):
        with self.assertRaises(KeyError):
            self.store.update_fields(self.con, "nope", {"priority": 1})

    def test_detail_carries_history(self):
        self.store.set_status(self.con, "u1", "starred")
        self.store.set_status(self.con, "u1", "applied")
        d = self.store.job_detail(self.con, "u1")
        self.assertEqual([e["to_status"] for e in d["events"]], ["starred", "applied"])
        self.assertTrue(d["applied_at"])

    def test_rates_are_none_before_any_application(self):
        self.assertIsNone(self.store.stats(self.con)["response_rate"])

    def test_rates_count_distinct_applications(self):
        self.store.set_status(self.con, "u1", "applied")
        self.store.set_status(self.con, "u1", "interviewing")
        s = self.store.stats(self.con)
        self.assertEqual((s["applied_total"], s["advanced"], s["interview_rate"]), (1, 1, 100))


class BrowserAssets(unittest.TestCase):
    """The tracker page is a string of JavaScript inside a Python file, so a bad edit
    ships a blank page with no Python error at all. That happened once."""

    def _script(self):
        src = (ROOT / "scripts" / "tracker.py").read_text()
        return src[src.index("<script>") + 8:src.index("</script>")]

    def test_javascript_parses(self):
        import shutil
        import subprocess
        import tempfile
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(self._script())
            path = fh.name
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_duplicate_function_definitions(self):
        """A partial replacement once left two copies of several functions."""
        import collections
        import re
        names = re.findall(r"^function (\w+)", self._script(), re.M)
        dupes = [n for n, c in collections.Counter(names).items() if c > 1]
        self.assertEqual(dupes, [])


class FakeClock:
    """Time that only moves when the code under test sleeps or a stubbed step 'runs'."""

    def __init__(self):
        self.t, self.slept = 1000.0, []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def drive(step, results, clock=None, deadline=None, attempts=None, took=10):
    """Run the retry policy over scripted (rc, log text, picks written) results."""
    clock = clock or FakeClock()
    n_attempts, backoff, timeout = runctl.POLICY[step]
    calls, lines = [], []

    def run_once(n, budget):
        calls.append(budget)
        clock.t += took
        return results[min(n - 1, len(results) - 1)]

    out = runctl.retry(step, run_once, attempts=attempts or n_attempts, backoff=backoff,
                       timeout=timeout, deadline=deadline, clock=clock.now,
                       sleep=clock.sleep, log=lines.append)
    return out, calls, clock, lines


class RunControl(unittest.TestCase):
    """Two of the first eight scheduled runs produced nothing and said nothing."""

    def test_egress_failure_is_retried_then_succeeds(self):
        """2026-10-08: the run began as the Mac woke, no board answered, and it quit."""
        out, calls, clock, _ = drive("harvest", [(2, "", False), (2, "", False), (0, "", False)])
        self.assertEqual((out["result"], out["attempts"]), ("ok", 3))
        self.assertEqual(clock.slept, [30, 120])
        self.assertEqual(runctl.exit_code("harvest", out), 0)

    def test_egress_retries_are_bounded(self):
        out, calls, clock, _ = drive("harvest", [(2, "", False)])
        self.assertEqual(len(calls), runctl.POLICY["harvest"][0])
        self.assertEqual(out["result"], "failed")
        self.assertEqual(runctl.exit_code("harvest", out), 2)

    def test_a_crashing_harvest_is_not_retried(self):
        """A traceback fails the same way every time; retrying only delays the alert."""
        out, calls, clock, _ = drive("harvest", [(1, "Traceback", False)])
        self.assertEqual((len(calls), clock.slept, out["result"]), (1, [], "deterministic"))
        self.assertEqual(runctl.exit_code("harvest", out), 1)

    def test_model_dns_failure_is_retried(self):
        """2026-10-09: 'API Error: Unable to connect to API (ENOTFOUND)', exit 1."""
        err = (1, "API Error: Unable to connect to API (ENOTFOUND)", False)
        out, calls, clock, _ = drive("claude", [err, (0, "wrote picks", True)])
        self.assertEqual((out["result"], out["attempts"], clock.slept), ("ok", 2, [120]))
        out, calls, _, _ = drive("claude", [err])
        self.assertEqual((out["result"], len(calls)), ("failed", runctl.POLICY["claude"][0]))
        self.assertEqual(runctl.exit_code("claude", out), 1)

    def test_expired_login_and_usage_limit_are_not_retried(self):
        for text in ("OAuth token has expired. Please run /login", "Claude usage limit reached"):
            out, calls, clock, _ = drive("claude", [(1, text, False)])
            self.assertEqual((len(calls), clock.slept, out["result"]), (1, [], "deterministic"), text)
        out, _, _, _ = drive("claude", [(1, "Invalid API key - please run /login", False)])
        self.assertEqual(runctl.exit_code("claude", out), 3)

    def test_model_outcomes_that_are_not_failures(self):
        """Picks on disk are the product whatever the exit code; a clean exit with none
        is an empty day. Neither may be retried."""
        self.assertEqual(runctl.classify("claude", 1, "", picks=True)[0], "ok")
        self.assertEqual(runctl.classify("claude", 0, "nothing cleared the bar")[0], "ok")
        self.assertNotEqual(runctl.classify("claude", 1, "catalog in sync")[0], "deterministic")

    def test_deadline_stops_retries_and_caps_each_attempt(self):
        """The ceiling is what keeps a stuck run from overlapping tomorrow's."""
        clock = FakeClock()
        out, calls, clock, _ = drive("harvest", [(2, "", False)], clock=clock,
                                     deadline=clock.t + 100)
        self.assertEqual(calls, [100, 60])          # 10s run + 30s pause, then no room for 120s
        self.assertEqual((out["result"], clock.slept), ("deadline", [30]))
        self.assertLessEqual(clock.t, 1100)
        self.assertNotEqual(runctl.exit_code("harvest", out), 0)

    def test_nothing_runs_after_the_deadline(self):
        clock = FakeClock()
        out, calls, _, _ = drive("claude", [(0, "", True)], clock=clock, deadline=clock.t - 1)
        self.assertEqual((calls, out["result"], out["attempts"]), ([], "deadline", 0))
        self.assertEqual(runctl.exit_code("claude", out), 1)

    def test_every_step_logs_its_duration(self):
        """Runs took 7 to 62 minutes and the log could not say where the time went."""
        _, _, _, lines = drive("harvest", [(2, "", False), (0, "", False)], took=95)
        self.assertIn("[step] harvest attempt 1/4: 95s rc=2 transient "
                      "(no job board was reachable (network or DNS))", lines)
        self.assertEqual(lines[-1], "[step] harvest total: 220s attempts=2 result=ok")

    def test_policies_are_finite_and_fit_the_ceiling(self):
        for step, (attempts, backoff, timeout) in runctl.POLICY.items():
            self.assertTrue(1 <= attempts <= 5, step)
            self.assertEqual(len(backoff), attempts - 1, step)
            self.assertLess(sum(backoff) + timeout, 14400, step)

    def test_a_hung_step_is_killed(self):
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "step.log"
            rc = runctl.attempt(["/bin/sh", "-c", "echo started; sleep 30"], log, 0.3)
            self.assertEqual(rc, runctl.TIMEOUT_RC)
            # The run ceiling is wall-clock and wins over a long awake-time budget.
            past = ["/bin/sh", "-c", "sleep 30"]
            self.assertEqual(runctl.attempt(past, log, 600, append=True, deadline=time.time() - 1),
                             runctl.TIMEOUT_RC)
            self.assertIn("started", log.read_text())
            self.assertEqual(runctl.attempt(["/bin/sh", "-c", "exit 2"], log, 5, append=True), 2)
            self.assertEqual(runctl.attempt(["/nonexistent/claude"], log, 5, append=True), 127)
        self.assertEqual(runctl.classify("claude", runctl.TIMEOUT_RC, "")[0], "transient")


class MissedDays(unittest.TestCase):
    """A day without a digest has to be visible in the next one, not only in a log."""

    def test_failure_record_is_one_json_line(self):
        import tempfile
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            when = datetime(2026, 10, 8, 14, 39, tzinfo=timezone.utc)
            runctl.record_failure("harvest", 2, "no job board was reachable", attempts=4,
                                  state=tmp, when=when)
            runctl.record_failure("claude", 1, "model CLI exited 1", state=tmp, when=when)
            rows = runctl.read_jsonl(pathlib.Path(tmp) / "failures.jsonl")
        self.assertEqual([r["step"] for r in rows], ["harvest", "claude"])
        self.assertEqual(rows[0], {"date": "2026-10-08", "at": "2026-10-08T14:39:00+00:00",
                                   "step": "harvest", "rc": 2, "attempts": 4,
                                   "reason": "no job board was reachable"})

    def test_gap_is_reported_with_its_reason(self):
        """The real week: digests on Oct 7 and Oct 10, nothing on the 8th or 9th."""
        from datetime import date, timedelta, timezone
        central = timezone(timedelta(hours=-5))
        runs = [{"run_at": "2026-10-06T03:46:20+00:00"}, {"run_at": "2026-10-07T14:36:44+00:00"}]
        failures = [{"date": "2026-10-08", "step": "harvest", "reason": "no job board was reachable"}]
        got = runctl.missed_days(runs, failures, date(2026, 10, 10), tz=central)
        self.assertEqual([(m["date"], m["step"]) for m in got],
                         [("2026-10-08", "harvest"), ("2026-10-09", None)])
        self.assertIn("no run was recorded", got[1]["reason"])   # Mac off: no record at all

    def test_no_gap_no_mention(self):
        from datetime import date, timedelta, timezone
        central = timezone(timedelta(hours=-5))
        runs = [{"run_at": "2026-10-12T14:07:20+00:00"}]
        self.assertEqual(runctl.missed_days(runs, [], date(2026, 10, 13), tz=central), [])
        self.assertEqual(runctl.missed_days([], [], date(2026, 10, 13), tz=central), [])
        # A late-evening Central run is already the next day in UTC; it must count for
        # the local day, or the following morning looks like a gap that never happened.
        late = [{"run_at": "2026-10-06T03:46:20+00:00"}]
        self.assertEqual([m["date"] for m in runctl.missed_days(late, [], date(2026, 10, 7), tz=central)],
                         ["2026-10-06"])
        # An earlier failed attempt today is not a missed day while today can still succeed.
        today_failed = [{"date": "2026-10-13", "step": "claude", "reason": "x"}]
        self.assertEqual(runctl.missed_days(runs, today_failed, date(2026, 10, 13), tz=central), [])

    def test_a_long_outage_is_capped_and_a_torn_line_is_skipped(self):
        import tempfile
        from datetime import date, timezone
        runs = [{"run_at": "2026-08-01T14:00:00+00:00"}]
        got = runctl.missed_days(runs, [], date(2026, 10, 13), tz=timezone.utc)
        self.assertEqual((len(got), got[-1]["date"]), (14, "2026-10-12"))
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "failures.jsonl"
            path.write_text('{"date": "2026-10-08", "step": "harvest"}\n{"date": "2026-10-0')
            self.assertEqual(len(runctl.read_jsonl(path)), 1)

    def test_digest_names_the_missed_day(self):
        import tempfile
        import types
        from datetime import datetime, timedelta
        now = datetime.now().astimezone()
        gap = (now - timedelta(days=1)).date().isoformat()
        saved = jobdigest.STATE, jobdigest.OUT
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            jobdigest.STATE, jobdigest.OUT = tmp / "state", tmp / "out"
            try:
                for d in (jobdigest.STATE, jobdigest.OUT):
                    d.mkdir()
                (jobdigest.STATE / "runs.jsonl").write_text(
                    json.dumps({"run_at": (now - timedelta(days=2)).isoformat()}) + "\n")
                (jobdigest.STATE / "failures.jsonl").write_text(json.dumps(
                    {"date": gap, "step": "harvest", "reason": "no job board was reachable"}) + "\n")
                import contextlib
                import io
                args = types.SimpleNamespace(picks=str(tmp / "none.json"), out=str(tmp / "d.html"))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(jobdigest.cmd_render(args), 0)
                page = (tmp / "d.html").read_text()
            finally:
                jobdigest.STATE, jobdigest.OUT = saved
        self.assertIn(f"No digest on {gap}: no job board was reachable", page)
        self.assertLess(page.index("No digest on"), page.index("Nothing new cleared the bar"))


class RunScript(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / "bin" / "run-local.sh"
        self.src = self.path.read_text()

    def test_script_parses(self):
        import subprocess
        proc = subprocess.run(["bash", "-n", str(self.path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_every_failure_exit_leaves_a_record(self):
        """An `exit N` outside fail() is a lost day nothing reports."""
        import re
        bare = [l for l in self.src.splitlines()
                if re.search(r"(^|;|&&|\|\|)\s*exit\b", l.split("#")[0])
                and "cd \"$REPO\"" not in l and 'exit "$1"' not in l]
        self.assertEqual(bare, [])
        for step in ("harvest", "claude"):
            self.assertIn(f"runctl.py run --step {step} ", self.src)
        self.assertIn("JOBDIGEST_DEADLINE", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
