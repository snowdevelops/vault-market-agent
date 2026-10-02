"""Offline tests for the sale-review job: which deals need a review, and
run_agent.sh review against a temp vault with a stub claude (from a copy of the
repo, so the real .env, vault and Claude account are never touched)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import deals  # noqa: E402
import notify_telegram  # noqa: E402
import status  # noqa: E402


def note(status_value):
    return f"---\ntype: vehicle-deal\nstatus: {status_value}       # comentário\n---\n# Carro\n"


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        (self.work / "Deals").mkdir()
        (self.work / "Reviews").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name, status_value):
        path = self.work / "Deals" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(note(status_value), encoding="utf-8")

    def names(self):
        return [p.stem for p in deals.pending_reviews(self.work)]

    def test_sold_without_review(self):
        self.add("onix-2019", "sold")
        self.assertEqual(self.names(), ["onix-2019"])

    def test_already_reviewed(self):
        self.add("onix-2019", "sold")
        (self.work / "Reviews" / "onix-2019.md").write_text("x")
        self.assertEqual(self.names(), [])

    def test_none_sold(self):
        for name, value in [("a", "listed"), ("b", "dropped"), ("c", "acquiring"), ("d", "reconditioning")]:
            self.add(name, value)
        self.assertEqual(self.names(), [])

    def test_mixed_and_subfolders(self):
        self.add("a", "sold")
        self.add("b", "listed")
        self.add("2025/c", "Sold")
        (self.work / "Reviews" / "a.md").write_text("x")
        self.assertEqual([p.relative_to(self.work).as_posix() for p in deals.pending_reviews(self.work)],
                         ["Deals/2025/c.md"])

    def test_no_folders(self):
        self.assertEqual(deals.pending_reviews(self.work / "nope"), [])


class NotifyTests(unittest.TestCase):
    def test_review_message_names_files_only(self):
        msg = notify_telegram.build_review_message(["onix-2019", "gol-2015"])
        self.assertIn("Reviews/onix-2019.md", msg)
        self.assertIn("Reviews/gol-2015.md", msg)
        self.assertNotIn("R$", msg)

    def test_compose_review(self):
        self.assertIn("Reviews/a.md", notify_telegram.compose(["review", "a", "--dry-run"]))

    def test_labels(self):
        self.assertIn("revisão de venda", notify_telegram.build_failed_message("review"))
        self.assertIn("Revisão de venda", status.format_status(
            status.record_finish(status.record_start(status.empty_state(), "review", 1), "review", 0), False, lang="pt"))


class PromptTests(unittest.TestCase):
    def test_review_prompt(self):
        text = (REPO / "prompts" / "review.md").read_text(encoding="utf-8")
        for part in ("Reviews/", "Knowledge/playbook.md", "Market/comparables.csv", "preliminar", "10 vendas"):
            self.assertIn(part, text)

    def test_brief_and_question_use_local_data(self):
        for name in ("brief.md", "question.md"):
            text = (REPO / "prompts" / name).read_text(encoding="utf-8")
            for part in ("Knowledge/playbook.md", "Market/comparables.csv", "Market/field-notes.md"):
                self.assertIn(part, text, name)


STUB = r'''#!/usr/bin/env python3
import json, os, re, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
with open(os.environ["STUB_RECORD"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "prompt": prompt}) + "\n")
target = re.search(r"Escreva a revisão em: (.+)$", prompt, re.M).group(1)
if os.environ.get("STUB_WRITE", "1") == "1":
    os.makedirs("Reviews", exist_ok=True)
    open(target, "w").write("# Revisão\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "duration_ms": 1000, "result": "Revisão escrita."}))
'''


@unittest.skipUnless(shutil.which("flock") and shutil.which("bash"), "needs flock and bash")
class RunReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        for part in ("scripts", "config", "prompts"):
            shutil.copytree(REPO / part, self.repo / part, ignore=shutil.ignore_patterns("__pycache__"))
        self.work = self.tmp / "vault" / "Business"
        (self.work / "Deals").mkdir(parents=True)
        self.stub = self.tmp / "claude"
        self.stub.write_text(STUB)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.record = self.tmp / "record.jsonl"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "AGENT_"))}
        self.env.update({"VAULT_DIR": str(self.tmp / "vault"), "AGENT_DIR": "Business",
                         "CLAUDE_BIN": str(self.stub), "STUB_RECORD": str(self.record),
                         "AGENT_SKIP_NOTIFY": "1"})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_review(self, **extra):
        return subprocess.run(["bash", str(self.repo / "scripts" / "run_agent.sh"), "review"],
                              env=dict(self.env, **extra), capture_output=True, text=True, timeout=60)

    def calls(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()] if self.record.exists() else []

    def test_nothing_to_review_costs_nothing(self):
        (self.work / "Deals" / "a.md").write_text(note("listed"))
        proc = self.run_review()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to do", proc.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.repo / ".state" / "status.json").exists())

    def test_reviews_each_sold_deal_once(self):
        (self.work / "Deals" / "onix-2019.md").write_text(note("sold"))
        (self.work / "Deals" / "Strada Freedom.md").write_text(note("sold"))
        (self.work / "Deals" / "gol.md").write_text(note("listed"))
        proc = self.run_review()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual({Path(c["cwd"]).resolve() for c in calls}, {self.work.resolve()})
        self.assertIn("Negócio a revisar: Deals/onix-2019.md", calls[0]["prompt"])
        self.assertIn("Escreva a revisão em: Reviews/Strada Freedom.md", calls[1]["prompt"])
        self.assertTrue((self.work / "Reviews" / "Strada Freedom.md").exists())
        self.assertTrue(calls[0]["prompt"].startswith((REPO / "prompts" / "review.md").read_text(encoding="utf-8")[:40]))
        self.assertTrue((self.work / "Reviews" / "onix-2019.md").exists())
        state = status.load_state(self.repo / ".state" / "status.json")
        self.assertTrue(state["jobs"]["review"]["ok"])
        # Already reviewed: the next run does nothing.
        self.assertIn("nothing to do", self.run_review().stdout)
        self.assertEqual(len(self.calls()), 2)

    def test_missing_review_file_fails_the_run(self):
        (self.work / "Deals" / "onix-2019.md").write_text(note("sold"))
        proc = self.run_review(STUB_WRITE="0")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Reviews/onix-2019.md was not written", (self.work / ".last_review.log").read_text())


if __name__ == "__main__":
    unittest.main()
