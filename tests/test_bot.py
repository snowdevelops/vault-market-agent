"""Offline tests for scripts/telegram_bot.py. No network: the Telegram API is faked
and claude is replaced by small stub scripts."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import telegram_bot as bot  # noqa: E402

OWNER = "123456789"


def update(text="oi", chat_id=OWNER, update_id=1, kind="message"):
    return {"update_id": update_id, kind: {"message_id": 10 + update_id, "chat": {"id": int(chat_id)}, "text": text}}


class FakeApi:
    def __init__(self):
        self.calls = []

    def __call__(self, method, params=None, timeout=30):
        self.calls.append((method, dict(params or {})))
        return []

    def sent(self):
        return [p["text"] for m, p in self.calls if m == "sendMessage"]


class ParseCommandTests(unittest.TestCase):
    def test_commands(self):
        for text, name in [("/status", "status"), ("/ultimo", "ultimo"), ("/resumo", "resumo"),
                           ("/pesquisar", "pesquisar"), ("/ajuda", "ajuda")]:
            self.assertEqual(bot.parse_command(text), ("command", name))

    def test_bot_suffix_case_and_arguments(self):
        self.assertEqual(bot.parse_command("/Status@MeuAgenteBot"), ("command", "status"))
        self.assertEqual(bot.parse_command("  /pesquisar agora por favor "), ("command", "pesquisar"))

    def test_aliases(self):
        self.assertEqual(bot.parse_command("/start"), ("command", "ajuda"))
        self.assertEqual(bot.parse_command("/help"), ("command", "ajuda"))

    def test_unknown_command(self):
        self.assertEqual(bot.parse_command("/apagar tudo"), ("unknown", "/apagar"))
        self.assertEqual(bot.parse_command("/"), ("unknown", "/"))

    def test_question(self):
        self.assertEqual(bot.parse_command("  Quanto está a Selic? "), ("question", "Quanto está a Selic?"))
        self.assertEqual(bot.parse_command("O que é /status?"), ("question", "O que é /status?"))

    def test_empty(self):
        self.assertEqual(bot.parse_command("   "), ("empty", ""))
        self.assertEqual(bot.parse_command(None), ("empty", ""))


class ChatFilterTests(unittest.TestCase):
    def test_owner_is_allowed(self):
        self.assertTrue(bot.is_allowed(update(), OWNER))
        self.assertTrue(bot.is_allowed(update(), f" {OWNER}\n"))

    def test_other_chat_is_rejected(self):
        self.assertFalse(bot.is_allowed(update(chat_id="987"), OWNER))

    def test_negative_group_id_must_match_exactly(self):
        self.assertFalse(bot.is_allowed(update(chat_id="-123456789"), OWNER))

    def test_malformed_updates_are_rejected(self):
        self.assertFalse(bot.is_allowed({"update_id": 1}, OWNER))
        self.assertFalse(bot.is_allowed({"update_id": 1, "message": {"text": "x"}}, OWNER))
        self.assertFalse(bot.is_allowed({"update_id": 1, "message": {"chat": {}}}, OWNER))
        self.assertFalse(bot.is_allowed("junk", OWNER))

    def test_edited_message_is_rejected(self):
        self.assertFalse(bot.is_allowed(update(kind="edited_message"), OWNER))

    def test_other_chat_gets_no_reply_and_is_only_counted(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeApi()
            b = bot.Bot(OWNER, d, "claude", state_path=Path(d) / "bot.json", api=api)
            with self.assertLogs("telegram_bot", "INFO") as logs:
                b.handle_update(update("segredo do estranho", chat_id="555"))
                b.handle_update(update("/status", chat_id="555", update_id=2))
            self.assertEqual(api.calls, [])
            self.assertEqual(b.ignored, 2)
            joined = "\n".join(logs.output)
            self.assertIn("total ignored: 2", joined)
            self.assertNotIn("segredo", joined)
            self.assertNotIn("555", joined)


class SplitMessageTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(bot.split_message("  olá  "), ["olá"])

    def test_empty_text(self):
        self.assertEqual(bot.split_message(""), [])

    def test_prefers_paragraph_break(self):
        text = "a" * 30 + "\n\n" + "b" * 30 + "\n" + "c" * 10
        self.assertEqual(bot.split_message(text, 50), ["a" * 30, "b" * 30 + "\n" + "c" * 10])

    def test_falls_back_to_line_then_word(self):
        self.assertEqual(bot.split_message("aaaa bbbb\ncccc dddd eeee", 12), ["aaaa bbbb", "cccc dddd", "eeee"])

    def test_hard_cut_for_one_huge_word(self):
        chunks = bot.split_message("x" * 25, 10)
        self.assertEqual(chunks, ["x" * 10, "x" * 10, "x" * 5])

    def test_no_chunk_over_limit_and_nothing_lost(self):
        words = [f"palavra{i}" for i in range(3000)]
        text = " ".join(words)
        chunks = bot.split_message(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(bot.utf16_len(c) <= bot.MAX_REPLY for c in chunks))
        self.assertEqual(" ".join(chunks).split(), words)

    def test_counts_emoji_as_two_units(self):
        chunks = bot.split_message("🚗" * 30, 20)
        self.assertTrue(all(bot.utf16_len(c) <= 20 for c in chunks))
        self.assertEqual("".join(chunks), "🚗" * 30)

    def test_long_answer_is_sent_in_parts_replying_once(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeApi()
            b = bot.Bot(OWNER, d, "claude", state_path=Path(d) / "bot.json", api=api)
            b.reply("p" * 5000 + "\n\n" + "q" * 3000, reply_to=7)
            sends = [p for m, p in api.calls if m == "sendMessage"]
            self.assertEqual(len(sends), 3)
            self.assertIn('"message_id": 7', sends[0]["reply_parameters"])
            self.assertNotIn("reply_parameters", sends[1])


class RateLimitTests(unittest.TestCase):
    def test_under_limit(self):
        allowed, times = bot.rate_limit([100.0], 200.0, limit=2)
        self.assertTrue(allowed)
        self.assertEqual(times, [100.0, 200.0])

    def test_at_limit_is_refused(self):
        allowed, times = bot.rate_limit([100.0, 150.0], 200.0, limit=2)
        self.assertFalse(allowed)
        self.assertEqual(times, [100.0, 150.0])

    def test_old_entries_expire(self):
        allowed, times = bot.rate_limit([0.0, 10.0, 3700.0], 3700.0, limit=2)
        self.assertTrue(allowed)
        self.assertEqual(times, [3700.0, 3700.0])

    def test_minutes_until_free(self):
        self.assertEqual(bot.minutes_until_free([1000.0, 2000.0], 1000.0 + 3600 - 90), 2)
        self.assertEqual(bot.minutes_until_free([], 0), 0)

    def test_bot_refuses_politely_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "bot.json"
            api = FakeApi()
            b = bot.Bot(OWNER, d, "claude", max_per_hour=2, state_path=state, api=api)
            b.handle_update(update("pergunta 1", update_id=1), now=1000.0)
            b.handle_update(update("pergunta 2", update_id=2), now=1001.0)
            b.handle_update(update("pergunta 3", update_id=3), now=1002.0)
            self.assertEqual(b.questions.qsize(), 2)
            self.assertIn("limite", api.sent()[-1])
            self.assertEqual(bot.load_bot_state(state)["question_times"], [1000.0, 1001.0])
            # The limit survives a restart.
            b2 = bot.Bot(OWNER, d, "claude", max_per_hour=2, state_path=state, api=FakeApi())
            b2.handle_update(update("pergunta 4", update_id=4), now=1003.0)
            self.assertEqual(b2.questions.qsize(), 0)

    def test_second_question_is_told_it_is_queued(self):
        with tempfile.TemporaryDirectory() as d:
            api = FakeApi()
            b = bot.Bot(OWNER, d, "claude", state_path=Path(d) / "bot.json", api=api)
            b.handle_update(update("a", update_id=1), now=1.0)
            b.handle_update(update("b", update_id=2), now=2.0)
            self.assertEqual(api.sent(), ["Na fila: 1 pergunta(s) antes desta."])


class CommandReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.api = FakeApi()
        self.bot = bot.Bot(OWNER, self.work, "claude", state_path=self.work / "bot.json", api=self.api)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ajuda_lists_commands(self):
        self.bot.handle_update(update("/ajuda"))
        for name in bot.COMMANDS:
            self.assertIn(f"/{name}", self.api.sent()[0])

    def test_unknown_command_shows_help(self):
        self.bot.handle_update(update("/xyz"))
        self.assertIn("Comando desconhecido: /xyz", self.api.sent()[0])

    def test_ultimo_reads_last_log_entry(self):
        (self.work / "Research").mkdir()
        (self.work / "Research" / "log.md").write_text("# Log\n\n## 2026-09-29\n- Temas: a\n\n## 2026-10-01\n- Temas: b\n")
        self.bot.handle_update(update("/ultimo"))
        self.assertIn("Temas: b", self.api.sent()[0])
        self.assertNotIn("Temas: a", self.api.sent()[0])

    def test_resumo_reads_newest_brief(self):
        (self.work / "Briefs").mkdir()
        (self.work / "Briefs" / "2026-09-28.md").write_text("## Resumo\n- antigo\n")
        (self.work / "Briefs" / "2026-10-05.md").write_text("## Resumo\n- novo\n\n## Negócios ativos\nR$ 50.000\n")
        self.bot.handle_update(update("/resumo"))
        reply = self.api.sent()[0]
        self.assertIn("2026-10-05", reply)
        self.assertIn("novo", reply)
        self.assertNotIn("50.000", reply)

    def test_resumo_without_briefs(self):
        self.bot.handle_update(update("/resumo"))
        self.assertIn("Ainda não há", self.api.sent()[0])

    def test_non_text_message(self):
        self.bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": {"id": int(OWNER)}, "photo": []}})
        self.assertIn("texto", self.api.sent()[0])


class StateTests(unittest.TestCase):
    def test_offset_saved_before_handling(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "bot.json"

            class Api(FakeApi):
                def __call__(self, method, params=None, timeout=30):
                    super().__call__(method, params, timeout)
                    return [update("/ajuda", update_id=41)] if method == "getUpdates" else []

            b = bot.Bot(OWNER, d, "claude", state_path=state, api=Api())

            def crash(update, now=None):
                raise RuntimeError("boom")
            b.handle_update = crash
            with self.assertLogs("telegram_bot", "ERROR"):
                b.poll_once()
            self.assertEqual(bot.load_bot_state(state)["offset"], 42)

    def test_damaged_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bot.json"
            path.write_text("{oops")
            self.assertEqual(bot.load_bot_state(path), {"offset": None, "question_times": []})
            path.write_text(json.dumps({"offset": "x", "question_times": [1, "y", 2.5]}))
            self.assertEqual(bot.load_bot_state(path), {"offset": None, "question_times": [1, 2.5]})


class QuestionCommandTests(unittest.TestCase):
    def test_command_is_read_only_and_scoped(self):
        cmd = bot.build_question_cmd("/opt/claude")
        self.assertEqual(cmd[:2], ["/opt/claude", "-p"])
        self.assertEqual(cmd[cmd.index("--settings") + 1], str(REPO / "config" / "agent-settings.json"))
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        self.assertEqual(allowed, ["Read", "Glob", "Grep", "WebSearch", "WebFetch"])
        denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
        for tool in ("Edit", "Write", "Bash"):
            self.assertIn(tool, denied)
            self.assertNotIn(tool, allowed)
        self.assertNotIn("bypassPermissions", cmd)

    def test_prompt_contains_question_and_date(self):
        from datetime import date
        prompt = bot.build_question_prompt("Regras.", "  Como está a Selic?  ", date(2026, 10, 1))
        self.assertTrue(prompt.startswith("Regras."))
        self.assertIn("2026-10-01", prompt)
        self.assertTrue(prompt.rstrip().endswith("Como está a Selic?"))

    def test_question_prompt_file_has_privacy_rule(self):
        text = (REPO / "prompts" / "question.md").read_text(encoding="utf-8")
        self.assertIn("300 palavras", text)
        self.assertIn("explicitamente", text)

    def test_parse_claude_json(self):
        self.assertEqual(bot.parse_claude_json('{"result": "Resposta", "is_error": false}'), (True, "Resposta"))
        self.assertEqual(bot.parse_claude_json('{"result": "x", "is_error": true}'), (False, "x"))
        self.assertEqual(bot.parse_claude_json('{"result": ""}'), (False, ""))
        self.assertEqual(bot.parse_claude_json("not json"), (False, ""))

    def test_redact(self):
        self.assertEqual(bot.redact("bad url /bot123:ABC/getUpdates", "123:ABC"), "bad url /bot<token>/getUpdates")
        self.assertEqual(bot.redact("nothing", ""), "nothing")

    def test_child_env_drops_token(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "secret-for-test"
        try:
            env = bot.child_env({"X": "1"})
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN")
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertEqual(env["X"], "1")


class RunQuestionTests(unittest.TestCase):
    """run_question with stub claude scripts instead of the real CLI."""

    def stub(self, d, body):
        path = Path(d) / "claude"
        path.write_text("#!/usr/bin/env python3\nimport json, os, sys, time\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_runs_in_agent_folder_with_prompt_on_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "Business"
            work.mkdir()
            claude = self.stub(d, "prompt = sys.stdin.read()\n"
                                  "print(json.dumps({'result': os.getcwd() + '|' + prompt + '|' + ' '.join(sys.argv[1:]),"
                                  " 'is_error': False}))\n")
            result, answer, _ = bot.run_question(bot.build_question_cmd(claude), "pergunta; rm -rf $HOME", work)
            self.assertEqual(result, "ok")
            cwd, prompt, argv = answer.split("|")
            self.assertEqual(Path(cwd).resolve(), work.resolve())
            self.assertEqual(prompt, "pergunta; rm -rf $HOME")
            self.assertNotIn("pergunta", argv)

    def test_typing_while_waiting_then_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            claude = self.stub(d, "time.sleep(30)\n")
            waits = []
            result, answer, _ = bot.run_question([claude], "x", d, timeout=0.6,
                                                 on_wait=lambda: waits.append(1), poll=0.1)
            self.assertEqual(result, "timeout")
            self.assertEqual(answer, "")
            self.assertGreaterEqual(len(waits), 2)

    def test_error_exit(self):
        with tempfile.TemporaryDirectory() as d:
            claude = self.stub(d, "print(json.dumps({'result': 'meio', 'is_error': False}))\nsys.exit(1)\n")
            result, _, _ = bot.run_question([claude], "x", d, timeout=10)
            self.assertEqual(result, "error")


class ServiceFileTests(unittest.TestCase):
    def test_user_service_restarts_on_failure_only(self):
        unit = (REPO / "deploy" / "vault-agent-bot.service").read_text()
        lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in unit.splitlines()
                 if "=" in line and not line.startswith("#")}
        self.assertEqual(lines["Restart"], "on-failure")
        self.assertEqual(lines["RestartPreventExitStatus"], str(bot.EXIT_CONFIG))
        self.assertEqual(lines["WantedBy"], "default.target")  # user service, not multi-user
        self.assertIn('"@REPO_DIR@/scripts/telegram_bot.py"', lines["ExecStart"])
        self.assertNotIn("User", lines)

    def test_installer_targets_user_systemd(self):
        script = (REPO / "scripts" / "install_bot_service.sh").read_text()
        self.assertIn("systemd/user", script)
        self.assertIn("systemctl --user enable", script)
        self.assertNotIn("sudo systemctl", script)
        self.assertNotIn("cat \"$REPO_DIR/.env\"", script)


if __name__ == "__main__":
    unittest.main()
