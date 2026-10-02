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
## Resumo
- Selic estável; crédito ainda apertado.
- 2026-10 primeiro veículo: revisar preço.

## O que mudou na semana
Algo longo aqui.
"""

OLD_BRIEF = """# Brief
## TL;DR
- Selic unchanged; credit still tight.

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
    def test_extracts_resumo_only(self):
        summary = notify_telegram.extract_section(BRIEF, "Resumo")
        self.assertIn("revisar preço", summary)
        self.assertNotIn("Algo longo", summary)

    def test_brief_message_uses_resumo(self):
        msg = notify_telegram.build_brief_message("2026-10-05", BRIEF, 0)
        self.assertIn("Brief semanal 2026-10-05", msg)
        self.assertIn("revisar preço", msg)
        self.assertNotIn("Algo longo", msg)

    def test_brief_message_falls_back_to_tldr(self):
        msg = notify_telegram.build_brief_message("2026-09-28", OLD_BRIEF, 0)
        self.assertIn("Selic unchanged", msg)
        self.assertNotIn("Something long", msg)

    def test_brief_message_without_summary(self):
        msg = notify_telegram.build_brief_message("2026-10-05", "# Brief\n## Outra\nx\n", 0)
        self.assertIn("Resumo não encontrada", msg)

    def test_missing_section_is_empty(self):
        self.assertEqual(notify_telegram.extract_section(BRIEF, "Nope"), "")

    def test_counts_fetch_errors(self):
        text = "# Snapshot\n\n## Fetch errors\n\n- SGS 432: timeout\n- FIPE x: 404\n"
        self.assertEqual(notify_telegram.count_fetch_errors(text), 2)

    def test_message_is_capped(self):
        msg = notify_telegram.build_brief_message("2026-10-05", "## Resumo\n" + "x" * 10000, 0)
        self.assertLessEqual(len(msg), notify_telegram.MAX_LEN)

    def test_message_mentions_errors(self):
        msg = notify_telegram.build_brief_message("2026-10-05", BRIEF, 3)
        self.assertIn("3 erro(s) na coleta", msg)

    def test_research_message_uses_last_entry(self):
        log = "# Log de pesquisa\n\n## 2026-10-03\n- Temas: a\n\n## 2026-10-06\n- Temas: b\n"
        msg = notify_telegram.build_research_message(log)
        self.assertTrue(msg.startswith("Pesquisa"))
        self.assertIn("2026-10-06", msg)
        self.assertIn("Temas: b", msg)
        self.assertNotIn("Temas: a", msg)

    def test_research_message_empty_log(self):
        self.assertIn("vazio", notify_telegram.build_research_message("# Log de pesquisa\n"))

    def test_busy_message_names_job(self):
        self.assertIn("brief semanal", notify_telegram.build_busy_message("brief"))

    def test_failed_message_names_job_and_log(self):
        msg = notify_telegram.build_failed_message("research")
        self.assertIn("pesquisa", msg)
        self.assertIn(".last_research.log", msg)


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

    def test_agent_dir_resolves_subfolder(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Negócios" / "Agente").mkdir(parents=True)
            old = {k: os.environ.get(k) for k in ("VAULT_DIR", "AGENT_DIR")}
            os.environ["VAULT_DIR"] = d
            os.environ["AGENT_DIR"] = "Negócios/Agente/"
            try:
                self.assertEqual(common.agent_dir(), Path(d) / "Negócios" / "Agente")
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_agent_dir_missing_exits(self):
        with tempfile.TemporaryDirectory() as d:
            old = {k: os.environ.get(k) for k in ("VAULT_DIR", "AGENT_DIR")}
            os.environ["VAULT_DIR"] = d
            os.environ["AGENT_DIR"] = "Nope"
            try:
                with self.assertRaises(SystemExit):
                    common.agent_dir()
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


class ConfigTests(unittest.TestCase):
    def test_agent_settings_block_reads_and_shell(self):
        import json
        path = Path(__file__).resolve().parents[1] / "config" / "agent-settings.json"
        perms = json.loads(path.read_text())["permissions"]
        self.assertTrue(perms["blockReadsOutsideWorkingDirectories"])
        self.assertIn("Bash", perms["deny"])
        self.assertIn("Edit(./Deals/**)", perms["deny"])

    def test_agent_settings_language_and_unchanged_denies(self):
        import json
        path = Path(__file__).resolve().parents[1] / "config" / "agent-settings.json"
        settings = json.loads(path.read_text())
        self.assertEqual(settings["language"], "portuguese")
        self.assertEqual(sorted(settings["permissions"]["deny"]), sorted([
            "Bash",
            "Edit(./Deals/**)",
            "Edit(./Templates/**)",
            "Edit(./Market/data/**)",
            "Edit(./Market/watchlist.json)",
            "Edit(./Market/comparables.csv)",
            "Edit(./Market/field-notes.md)",
            "Edit(./CLAUDE.md)",
        ]))
        self.assertNotIn("allow", settings["permissions"])

    def test_captured_data_is_readable_but_not_editable(self):
        import json
        path = Path(__file__).resolve().parents[1] / "config" / "agent-settings.json"
        deny = json.loads(path.read_text())["permissions"]["deny"]
        for name in ("Market/comparables.csv", "Market/field-notes.md"):
            self.assertIn(f"Edit(./{name})", deny)
            self.assertNotIn(f"Read(./{name})", deny)
        # The review job writes these, so nothing may deny them.
        self.assertFalse([rule for rule in deny if "Reviews" in rule or "Knowledge" in rule])


if __name__ == "__main__":
    unittest.main()
