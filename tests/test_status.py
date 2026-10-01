"""Offline tests for scripts/status.py and the job lock in scripts/run_agent.sh."""
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import status  # noqa: E402

T0 = datetime(2026, 10, 1, 6, 0)
T1 = datetime(2026, 10, 1, 6, 41)


class StateFileTests(unittest.TestCase):
    def test_missing_file_gives_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(status.load_state(Path(d) / "status.json"), {"running": None, "jobs": {}})

    def test_damaged_file_gives_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "status.json"
            for content in ("{not json", "[1, 2]", ""):
                path.write_text(content)
                self.assertEqual(status.load_state(path), {"running": None, "jobs": {}})

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "status.json"
            state = status.record_start(status.empty_state(), "research", 123, T0)
            status.save_state(state, path)
            self.assertEqual(status.load_state(path), state)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["status.json"])  # no temp files left

    def test_start_then_finish(self):
        state = status.record_start(status.empty_state(), "research", 123, T0)
        self.assertEqual(state["running"], {"job": "research", "pid": 123, "started": "2026-10-01T06:00:00"})
        status.record_finish(state, "research", "0", T1)
        self.assertIsNone(state["running"])
        self.assertEqual(state["jobs"]["research"], {
            "started": "2026-10-01T06:00:00", "finished": "2026-10-01T06:41:00", "exit_code": 0, "ok": True})

    def test_start_keeps_previous_result(self):
        state = status.record_finish(status.record_start(status.empty_state(), "brief", 1, T0), "brief", 1, T1)
        status.record_start(state, "brief", 2, T1)
        self.assertFalse(state["jobs"]["brief"]["ok"])
        self.assertEqual(state["running"]["pid"], 2)


class LockTests(unittest.TestCase):
    def test_missing_lock_file_is_not_locked(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(status.is_locked(Path(d) / "agent.lock"))
            self.assertFalse((Path(d) / "agent.lock").exists())

    def test_held_and_released_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "agent.lock"
            with open(lock, "w") as holder:
                self.assertFalse(status.is_locked(lock))
                fcntl.flock(holder, fcntl.LOCK_EX)
                self.assertTrue(status.is_locked(lock))
                fcntl.flock(holder, fcntl.LOCK_UN)
                self.assertFalse(status.is_locked(lock))


class FormatTests(unittest.TestCase):
    def finished_state(self):
        state = status.empty_state()
        status.record_start(state, "research", 10, T0)
        status.record_finish(state, "research", 0, T1)
        return state

    def test_idle_english(self):
        text = status.format_status(self.finished_state(), False, T1, "en")
        self.assertEqual(text.splitlines(), [
            "No job running.",
            "research: last run 2026-10-01 06:00, ok, took 41 min",
            "brief: never run",
        ])

    def test_running_portuguese(self):
        state = self.finished_state()
        status.record_start(state, "brief", 11, datetime(2026, 10, 5, 8, 0))
        text = status.format_status(state, True, datetime(2026, 10, 5, 8, 12), "pt")
        self.assertEqual(text.splitlines(), [
            "Em execução: Brief semanal desde 05/10 08:00 (há 12 min)",
            "Pesquisa: última em 01/10 06:00, ok, levou 41 min",
            "Brief semanal: nunca executado",
        ])

    def test_failed_run(self):
        state = status.record_finish(status.record_start(status.empty_state(), "brief", 1, T0), "brief", 1, T1)
        self.assertIn("brief: last run 2026-10-01 06:00, failed (exit 1)", status.format_status(state, False, T1))
        self.assertIn("falhou (código 1)", status.format_status(state, False, T1, "pt"))

    def test_killed_run_is_reported_when_lock_is_free(self):
        state = status.record_start(status.empty_state(), "research", 10, T0)
        lines = status.format_status(state, False, T1).splitlines()
        self.assertEqual(lines[0], "No job running.")
        self.assertIn("research: run started 2026-10-01 06:00 did not finish", lines[1])

    def test_lock_without_details(self):
        self.assertIn("a job", status.format_status(status.empty_state(), True, T1))


STUB_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
try:
    os.fstat(9)
    fd9_open = True
except OSError:
    fd9_open = False
state = json.load(open(os.environ["STUB_STATE"]))
json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "fd9_open": fd9_open, "state": state},
          open(os.environ["STUB_RECORD"], "w"))
print(json.dumps({"type": "system", "subtype": "init", "cwd": os.getcwd(), "model": "stub"}))
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Read", "input": {"file_path": os.getcwd() + "/Knowledge/_index.md"}}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
                  "duration_ms": 3000, "result": "Sessão concluída."}))
'''


@unittest.skipUnless(shutil.which("flock") and shutil.which("bash"), "needs flock and bash")
class RunAgentTests(unittest.TestCase):
    """Run scripts/run_agent.sh from a copy of the repo against a temp vault and a
    stub claude, so the real .env, vault and Claude account are never touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        for part in ("scripts", "config", "prompts"):
            shutil.copytree(REPO / part, self.repo / part, ignore=shutil.ignore_patterns("__pycache__"))
        self.work = self.tmp / "vault" / "Business"
        (self.work / "Knowledge").mkdir(parents=True)
        self.stub = self.tmp / "claude"
        self.stub.write_text(STUB_CLAUDE)
        self.stub.chmod(self.stub.stat().st_mode | stat.S_IEXEC)
        self.record = self.tmp / "record.json"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "AGENT_"))}
        self.env.update({
            "VAULT_DIR": str(self.tmp / "vault"),
            "AGENT_DIR": "Business",
            "CLAUDE_BIN": str(self.stub),
            "STUB_RECORD": str(self.record),
            "STUB_STATE": str(self.repo / ".state" / "status.json"),
        })

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_agent(self, *extra_env):
        env = dict(self.env, **dict(extra_env))
        return subprocess.run(["bash", str(self.repo / "scripts" / "run_agent.sh"), "research"],
                              env=env, capture_output=True, text=True, timeout=60)

    def test_run_uses_agent_folder_settings_and_records_status(self):
        proc = self.run_agent()
        self.assertEqual(proc.returncode, 0, proc.stderr)

        call = json.loads(self.record.read_text())
        self.assertEqual(Path(call["cwd"]).resolve(), self.work.resolve())
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--settings") + 1], str(self.repo / "config" / "agent-settings.json"))
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", argv)
        self.assertFalse(call["fd9_open"], "the lock must not leak into claude")
        self.assertEqual(call["state"]["running"]["job"], "research")

        events = [json.loads(line) for line in (self.work / ".last_research.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["result"], "Sessão concluída.")
        log = (self.work / ".last_research.log").read_text()
        self.assertIn("status: ok", log)
        self.assertIn("Sessão concluída.", log)

        state = status.load_state(self.repo / ".state" / "status.json")
        self.assertIsNone(state["running"])
        self.assertTrue(state["jobs"]["research"]["ok"])
        self.assertFalse(status.is_locked(self.repo / ".state" / "agent.lock"))

    def test_second_job_gives_up_when_lock_is_held(self):
        (self.repo / ".state").mkdir()
        with open(self.repo / ".state" / "agent.lock", "w") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            proc = self.run_agent(("AGENT_LOCK_WAIT", "1"))
        self.assertEqual(proc.returncode, 75)
        self.assertIn("another agent job is still running", proc.stderr)
        self.assertFalse(self.record.exists(), "claude must not run without the lock")
        self.assertFalse((self.repo / ".state" / "status.json").exists())

    def test_failed_result_marks_run_failed(self):
        self.stub.write_text(STUB_CLAUDE.replace('"is_error": False', '"is_error": True'))
        proc = self.run_agent()
        self.assertEqual(proc.returncode, 1)
        state = status.load_state(self.repo / ".state" / "status.json")
        self.assertEqual(state["jobs"]["research"]["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
