"""Runs with no keys and no services: python3 -m unittest discover tests
SAMPLE_MODE swaps Zetaris -> sqlite and Nightshift -> rule fallbacks; H-MEM is the real engine (needs node + npm install
in engines/hmem). The H-MEM conformance suite itself: cd engines/hmem && npm test
"""
import os, tempfile, unittest

os.environ["SAMPLE_MODE"] = "true"
os.environ["MEMORY_DIR"] = tempfile.mkdtemp(prefix="ask-glisper-mem-")
os.environ["LOGS_DIR"] = tempfile.mkdtemp(prefix="ask-glisper-logs-")  # keep test runs out of the chat history

from glisper_agent import agent as A, backends as B  # noqa: E402


class SqlGuard(unittest.TestCase):
    def test_rejects(self):
        for bad in ["DELETE FROM answers", "SELECT 1; DROP TABLE answers", "SELECT * FROM users",
                    "SELECT COUNT(*) FILTER (WHERE status='won') FROM answers", "SELECT run_date::text FROM answers",
                    # reaching past the audit scope: catalog names, qualified or quoted tables
                    "SELECT * FROM GLISPER.answers", "SELECT * FROM public.prompt_results",
                    'SELECT * FROM "GLISPER"."answers"', "SELECT * FROM (SELECT * FROM glisper_demo.answers) x"]:
            with self.assertRaises(ValueError, msg=bad):
                B.check_sql(bad)

    def test_scope_pins_every_table_to_one_audit(self):
        audit = "5655729b-0ad7-4587-adbb-94fb99ea6136"
        out = B.scope("WITH x AS (SELECT engine FROM answers) SELECT * FROM x", audit)
        for t in B.TABLES:
            self.assertIn(f"{t} AS (SELECT * FROM {B.GLISPER_CATALOG}.{t} WHERE audit_id = '{audit}')", out)
        self.assertTrue(out.endswith(", x AS (SELECT engine FROM answers) SELECT * FROM x"))
        with self.assertRaises(ValueError):
            B.scope("SELECT 1", "x' OR '1'='1")

    def test_accepts_and_caps(self):
        out = B.run_sql("WITH x AS (SELECT engine FROM answers) SELECT engine, COUNT(*) n FROM x GROUP BY engine")
        self.assertEqual(len(out["rows"]), 4)
        self.assertTrue(out["sql"].endswith("LIMIT 200"))


class Evaluator(unittest.TestCase):
    trace = A.Run("test")
    findings = [{"skill": "source_gap", "rows": [
        {"domain": "lahorestyleguide.pk", "source_type": "listicle", "pct_of_not_won_citing": 85.5, "pct_of_won_citing": 0.0},
        {"domain": "threadline.pk", "source_type": "competitor_site", "pct_of_not_won_citing": 30.0, "pct_of_won_citing": 0.0},
        {"domain": "daraz.pk", "source_type": "marketplace", "pct_of_not_won_citing": 47.5, "pct_of_won_citing": 50.0}]},
        {"skill": "competitor_gap", "rows": [{"winner": "Threadline", "pct_of_answers": 72.2}]}]

    def score(self, answer, action):
        return A.evaluate(self.trace, {"answer": answer, "action": action}, self.findings, [], ["Kurta Co"])

    def test_grounded_passes(self):
        s, _, target = self.score("lahorestyleguide.pk is cited in 85.5% of answers we don't win and 0.0% we win.",
                                  "Ask lahorestyleguide.pk to add Lahore Loom to its best-boutiques list.")
        self.assertGreaterEqual(s, A.PASS_SCORE)
        self.assertEqual(target, "lahorestyleguide.pk")

    def test_untraced_number_rejected(self):
        s, crit, _ = self.score("Fixing this wins back 62% of answers.", "Ask lahorestyleguide.pk to list us.")
        self.assertLess(s, A.PASS_SCORE)
        self.assertIn("62", crit)

    def test_computed_decimal_rejected(self):  # 85.5 + 30.0 summed by the model is not in any row
        s, crit, _ = self.score("lahorestyleguide.pk and threadline.pk together reach 115.5%; daraz.pk 4.5%.",
                                "Ask lahorestyleguide.pk to list us.")
        self.assertLess(s, A.PASS_SCORE)
        self.assertIn("115.5", crit)
        self.assertIn("4.5", crit)
        s, _, _ = self.score("lahorestyleguide.pk is cited in 86% of losses.", "Ask lahorestyleguide.pk to list us.")
        self.assertGreaterEqual(s, A.PASS_SCORE)  # a value rounded to a whole number is still traced

    def test_invented_contact_flagged(self):
        _, crit, _ = self.score("lahorestyleguide.pk is cited in 85.5% of losses.",
                                "Email editor@lahorestyleguide.pk to list us.")
        self.assertIn("editor@lahorestyleguide.pk", crit)

    def test_rival_site_and_memory_rejected(self):
        s, crit, target = self.score("Kurta Co and threadline.pk dominate.", "Pitch a story to threadline.pk.")
        self.assertLess(s, A.PASS_SCORE)
        self.assertIsNone(target)
        self.assertIn("Kurta Co", crit)


    def test_domain_without_gap_flagged(self):
        _, crit, _ = self.score("daraz.pk (47.5% of losses) and lahorestyleguide.pk (85.5%) appear in losses.",
                                "Ask lahorestyleguide.pk to list us.")
        self.assertIn("daraz.pk", crit)


class EndToEnd(unittest.TestCase):
    def test_loop_finds_planted_gap_and_obeys_memory(self):
        B.remember(A.DEMO_BRAND["name"], "Ignore Kurta Co: they only ship in Karachi")
        out = A.ask("Why are we losing on Perplexity?")
        self.assertTrue(out["passed"])
        self.assertIn("lahorestyleguide.pk", out["action"])
        self.assertTrue(out["quotes"])
        winners = {r["winner"] for e in out["evidence"] if e["skill"] == "competitor_gap" for r in e["rows"]}
        self.assertNotIn("Kurta Co", winners)
        self.assertIn("Ignore Kurta Co", " ".join(out["memories_used"]))

    def test_finished_runs_form_the_brand_chat(self):
        from glisper_agent import server
        out = A.ask("Who beats us on Gemini?")
        runs = server.history("demo")["runs"]
        self.assertEqual(runs[-1]["run_id"], out["run_id"])
        self.assertEqual(server.history("some-other-audit")["runs"], [])

    def test_follow_up_continues_the_chat(self):
        from glisper_agent import server
        first = A.ask("Why are we losing on Perplexity?", chat_id="c0ffee01")
        second = A.ask("Which sources should we go after?", chat_id="c0ffee01", history=[first])
        self.assertEqual(second["engine"], "perplexity")  # the follow-up keeps the earlier engine
        chat = next(c for c in server.chats("demo") if c["chat_id"] == "c0ffee01")
        self.assertEqual((chat["title"], chat["turns"]), ("Why are we losing on Perplexity?", 2))
        self.assertEqual([r["run_id"] for r in server.history("demo", "c0ffee01")["runs"]], [first["run_id"], second["run_id"]])


if __name__ == "__main__":
    unittest.main()
