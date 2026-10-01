"""Offline unit tests. Run with: python3 -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common  # noqa: E402
import fetch_market  # noqa: E402
import notify_telegram  # noqa: E402

BRIEF = """# Brief
## TL;DR
- Selic unchanged; credit still tight.
- 2026-10 first vehicle: review price.

## What changed this week
Something long here.
"""


class ToFloatTests(unittest.TestCase):
    def test_brazilian_currency(self):
        self.assertEqual(fetch_market.to_float("R$ 80.000,00"), 80000.0)

    def test_dot_decimal(self):
        self.assertEqual(fetch_market.to_float("13.75"), 13.75)

    def test_comma_decimal(self):
        self.assertEqual(fetch_market.to_float("13,75"), 13.75)


class SectionTests(unittest.TestCase):
    def test_extracts_tldr_only(self):
        tldr = notify_telegram.extract_section(BRIEF, "TL;DR")
        self.assertIn("review price", tldr)
        self.assertNotIn("Something long", tldr)

    def test_missing_section_is_empty(self):
        self.assertEqual(notify_telegram.extract_section(BRIEF, "Nope"), "")

    def test_counts_fetch_errors(self):
        text = "# Snapshot\n\n## Fetch errors\n\n- SGS 432: timeout\n- FIPE x: 404\n"
        self.assertEqual(notify_telegram.count_fetch_errors(text), 2)

    def test_message_is_capped(self):
        msg = notify_telegram.build_message("2026-10-05", "## TL;DR\n" + "x" * 10000, 0)
        self.assertLessEqual(len(msg), notify_telegram.MAX_LEN)

    def test_message_mentions_errors(self):
        msg = notify_telegram.build_message("2026-10-05", BRIEF, 3)
        self.assertIn("3 data fetch error", msg)


class FilesTests(unittest.TestCase):
    def test_newest_brief_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name in ["2026-09-28.md", "2026-10-05.md", ".last_run.log", "notes.md"]:
                (d / name).write_text("x")
            self.assertEqual(notify_telegram.newest_brief(d).name, "2026-10-05.md")

    def test_load_env_does_not_override_existing(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("# comment\nTEST_KEY_A=from_file\nTEST_KEY_B='quoted'\n")
            os.environ["TEST_KEY_A"] = "from_env"
            os.environ.pop("TEST_KEY_B", None)
            common.load_env(env)
            self.assertEqual(os.environ["TEST_KEY_A"], "from_env")
            self.assertEqual(os.environ["TEST_KEY_B"], "quoted")


if __name__ == "__main__":
    unittest.main()
