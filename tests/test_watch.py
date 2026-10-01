"""Offline tests for scripts/watch_agent.py (stream-json formatting and following)."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import watch_agent  # noqa: E402

CWD = "/vault/Business"

INIT = {"type": "system", "subtype": "init", "cwd": CWD, "model": "claude-test"}


def tool_use(name, tool_input):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": name, "input": tool_input}]}}


def result(ok=True, text="Pronto."):
    return {"type": "result", "subtype": "success" if ok else "error_during_execution",
            "is_error": not ok, "num_turns": 7, "duration_ms": 125_000, "result": text}


def lines_of(*events):
    return [json.dumps(e, ensure_ascii=False) for e in events]


class FormatEventTests(unittest.TestCase):
    def test_init(self):
        (line,) = watch_agent.format_event(INIT)
        self.assertIn("session started", line)
        self.assertIn("claude-test", line)

    def test_read_path_is_relative_to_agent_folder(self):
        (line,) = watch_agent.format_event(tool_use("Read", {"file_path": f"{CWD}/Knowledge/_index.md"}), CWD)
        self.assertTrue(line.startswith("Read"))
        self.assertIn("Knowledge/_index.md", line)
        self.assertNotIn("/vault", line)

    def test_path_outside_folder_stays_absolute(self):
        (line,) = watch_agent.format_event(tool_use("Read", {"file_path": "/etc/passwd"}), CWD)
        self.assertIn("/etc/passwd", line)

    def test_web_search_shows_query(self):
        (line,) = watch_agent.format_event(tool_use("WebSearch", {"query": "fenauto setembro"}))
        self.assertIn('"fenauto setembro"', line)

    def test_web_fetch_shows_url(self):
        (line,) = watch_agent.format_event(tool_use("WebFetch", {"url": "https://bcb.gov.br/x", "prompt": "p"}))
        self.assertIn("https://bcb.gov.br/x", line)

    def test_grep_with_path(self):
        (line,) = watch_agent.format_event(tool_use("Grep", {"pattern": "Selic", "path": f"{CWD}/Knowledge"}), CWD)
        self.assertIn('"Selic" in Knowledge', line)

    def test_unknown_tool_uses_first_string_input(self):
        (line,) = watch_agent.format_event(tool_use("Mystery", {"n": 3, "thing": "abc"}))
        self.assertIn("Mystery", line)
        self.assertIn("abc", line)

    def test_assistant_text_is_one_short_line(self):
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "a\nb " + "x" * 500}]}}
        (line,) = watch_agent.format_event(event)
        self.assertNotIn("\n", line)
        self.assertLessEqual(len(line), 10 + watch_agent.TEXT_WIDTH)

    def test_tool_error_is_shown(self):
        event = {"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": [{"type": "text", "text": "Permission denied"}]}]}}
        (line,) = watch_agent.format_event(event)
        self.assertIn("error", line)
        self.assertIn("Permission denied", line)

    def test_successful_tool_result_is_quiet(self):
        event = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "lots of text"}]}}
        self.assertEqual(watch_agent.format_event(event), [])

    def test_result_line_and_text(self):
        lines = watch_agent.format_event(result())
        self.assertIn("ok, 7 turns, 2m 05s", lines[0])
        self.assertEqual(lines[-1], "Pronto.")

    def test_failed_result(self):
        lines = watch_agent.format_event(result(ok=False))
        self.assertIn("FAILED (error_during_execution)", lines[0])

    def test_unknown_event_is_ignored(self):
        self.assertEqual(watch_agent.format_event({"type": "stream_event"}), [])
        self.assertEqual(watch_agent.format_event(["not", "a", "dict"]), [])


class FormatLineTests(unittest.TestCase):
    def test_blank_line_is_skipped(self):
        self.assertEqual(watch_agent.format_line("   \n", {}), [])

    def test_non_json_line_is_shown_raw(self):
        (line,) = watch_agent.format_line("Error: not logged in", {})
        self.assertIn("not logged in", line)

    def test_cwd_from_init_is_remembered(self):
        state = {}
        watch_agent.format_line(json.dumps(INIT), state)
        (line,) = watch_agent.format_line(json.dumps(tool_use("Edit", {"file_path": f"{CWD}/Knowledge/a.md"})), state)
        self.assertIn("Knowledge/a.md", line)
        self.assertNotIn(CWD, line)


class SummaryTests(unittest.TestCase):
    NOW = datetime(2026, 10, 1, 6, 41)

    def test_ok_run(self):
        lines = lines_of(INIT, tool_use("WebSearch", {"query": "q"}), tool_use("WebSearch", {"query": "r"}),
                         tool_use("Read", {"file_path": "x"}), result(text="Feito: 2 temas."))
        ok, text = watch_agent.summarize(lines, "research", self.NOW)
        self.assertTrue(ok)
        self.assertIn("job: research", text)
        self.assertIn("2026-10-01T06:41:00", text)
        self.assertIn("tools: 3 (Read 1, WebSearch 2)", text)
        self.assertIn("status: ok", text)
        self.assertTrue(text.endswith("Feito: 2 temas."))

    def test_error_result_fails(self):
        ok, text = watch_agent.summarize(lines_of(INIT, result(ok=False)), "brief", self.NOW)
        self.assertFalse(ok)
        self.assertIn("FAILED", text)

    def test_no_result_fails(self):
        ok, text = watch_agent.summarize(lines_of(INIT) + ["garbage"], "brief", self.NOW)
        self.assertFalse(ok)
        self.assertIn("no result", text)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / ".last_research.jsonl"
            good.write_text("\n".join(lines_of(INIT, result())) + "\n")
            bad = Path(d) / ".last_brief.jsonl"
            bad.write_text("\n".join(lines_of(INIT)) + "\n")
            out = io.StringIO()
            sys_stdout, sys.stdout = sys.stdout, out
            try:
                self.assertEqual(watch_agent.main(["--summary", str(good)]), 0)
                self.assertEqual(watch_agent.main(["--summary", str(bad)]), 1)
                self.assertEqual(watch_agent.main(["--summary", str(Path(d) / "missing.jsonl")]), 1)
            finally:
                sys.stdout = sys_stdout
            self.assertIn("job: research", out.getvalue())


class FollowTests(unittest.TestCase):
    def test_finished_run_replays_and_exits(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".last_research.jsonl"
            path.write_text("\n".join(lines_of(INIT, tool_use("WebSearch", {"query": "selic"}), result())) + "\n")
            out = io.StringIO()
            code = watch_agent.follow(path, out=out, sleep=lambda s: self.fail("should not wait"))
            self.assertEqual(code, 0)
            self.assertIn('"selic"', out.getvalue())
            self.assertIn("done", out.getvalue())

    def test_follows_growing_file_with_split_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".last_research.jsonl"
            path.write_text("")
            search = json.dumps(tool_use("WebSearch", {"query": "preço médio usados"}), ensure_ascii=False)
            pieces = [json.dumps(INIT) + "\n" + search[:30], search[30:] + "\n",
                      json.dumps(result(ok=False)) + "\n"]

            def writer():
                for piece in pieces:
                    time.sleep(0.05)
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(piece)

            thread = threading.Thread(target=writer)
            thread.start()
            out = io.StringIO()
            code = watch_agent.follow(path, out=out, poll=0.01)
            thread.join()
            self.assertEqual(code, 1)
            self.assertIn('"preço médio usados"', out.getvalue())
            # The line split across two writes was parsed whole, not shown as raw text.
            self.assertFalse(any(line.startswith("?") for line in out.getvalue().splitlines()))

    def test_newest_stream_and_job_name(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / ".last_brief.jsonl", Path(d) / ".last_research.jsonl"
            old.write_text("x")
            new.write_text("x")
            os.utime(old, (1, 1))
            self.assertEqual(watch_agent.newest_stream(d), new)
            self.assertEqual(watch_agent.newest_stream(d, "brief"), old)
            self.assertIsNone(watch_agent.newest_stream(d, "nope"))
            self.assertEqual(watch_agent.job_from_path(new), "research")


if __name__ == "__main__":
    unittest.main()
