#!/usr/bin/env python3
"""
Telegram bot: the owner checks on the agent and asks it questions from the phone.

  python3 scripts/telegram_bot.py      run in the foreground (systemd runs it as a service)

Commands (replies are in Portuguese, for the owner):
  /status     running job, if any, and the last run of each job
  /ultimo     newest entry of Research/log.md
  /resumo     'Resumo' section of the newest brief
  /pesquisar  start a research run now (waits for the job lock like cron runs do)
  /ajuda      list the commands
  any other text is a question for the agent

Safety:
  - Long polling with getUpdates; nothing listens on a port.
  - Only TELEGRAM_CHAT_ID is answered. Other chats are dropped silently and only
    their count is logged, never their content.
  - Questions run `claude -p` inside the agent folder with config/agent-settings.json
    and read-only tools. The question goes in on stdin, never through a shell or as
    a command-line argument.
  - One question at a time, at most BOT_MAX_QUESTIONS_PER_HOUR (default 20).
  - The update offset is saved before an update is handled, so a message that
    crashes the bot is not handled again after the restart.

State lives in .state/bot.json in this repository (gitignored). Standard library only.
"""
import json
import logging
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

import notify_telegram as tg
import status
from common import REPO_DIR, agent_dir, load_env

STATE_FILE = status.STATE_DIR / "bot.json"
SETTINGS_FILE = REPO_DIR / "config" / "agent-settings.json"
QUESTION_PROMPT = REPO_DIR / "prompts" / "question.md"
RUN_AGENT = REPO_DIR / "scripts" / "run_agent.sh"

MAX_REPLY = 4000             # Telegram allows 4096 UTF-16 units per message
QUESTION_TIMEOUT = 600       # seconds
TYPING_EVERY = 4             # seconds; Telegram shows "typing" for about 5
POLL_TIMEOUT = 50            # seconds of long polling per getUpdates call
RATE_WINDOW = 3600           # seconds
DEFAULT_MAX_PER_HOUR = 20
READ_ONLY_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"
DENIED_TOOLS = "Edit,Write,NotebookEdit,Bash"
EXIT_CONFIG = 78             # systemd does not restart on this code (see deploy/)
EXIT_LOCK_BUSY = 75          # run_agent.sh: another job held the lock too long

COMMANDS = ("status", "ultimo", "resumo", "pesquisar", "ajuda")
ALIASES = {"start": "ajuda", "help": "ajuda", "último": "ultimo"}

HELP = """Comandos:
/status - trabalho em execução e a última execução de cada um
/ultimo - a entrada mais recente do log de pesquisa
/resumo - o Resumo do brief semanal mais recente
/pesquisar - começar uma sessão de pesquisa agora
/ajuda - esta lista

Qualquer outra mensagem é uma pergunta para o agente. Ele só lê e pesquisa, nunca altera nada. Respostas podem levar alguns minutos."""

log = logging.getLogger("telegram_bot")


# ---------- pure helpers (tested offline) -------------------------------------

def parse_command(text):
    """Classify a message: ('command', name), ('unknown', word), ('question', text)
    or ('empty', '')."""
    text = (text or "").strip()
    if not text:
        return "empty", ""
    if not text.startswith("/"):
        return "question", text
    word = text.split(maxsplit=1)[0]
    name = word[1:].split("@", 1)[0].lower()
    name = ALIASES.get(name, name)
    if name in COMMANDS:
        return "command", name
    return "unknown", word


def is_allowed(update, chat_id):
    """True only for a new message from the owner's chat."""
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return False
    return str(chat["id"]).strip() == str(chat_id).strip()


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def split_message(text, limit=MAX_REPLY):
    """Split text into chunks Telegram accepts, preferring paragraph, then line,
    then word boundaries. Length is counted in UTF-16 units, as Telegram does."""
    text = (text or "").strip()
    chunks = []
    while utf16_len(text) > limit:
        window = text[:limit]
        while utf16_len(window) > limit:  # emoji and other wide characters take 2 units
            window = window[: len(window) - (utf16_len(window) - limit + 1) // 2]
        cut = max(1, len(window))
        for sep in ("\n\n", "\n", " "):
            i = window.rfind(sep)
            if i >= len(window) // 2:
                cut = i
                break
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def rate_limit(times, now, limit, window=RATE_WINDOW):
    """Sliding-window limiter. Returns (allowed, timestamps to keep)."""
    recent = [t for t in times if now - t < window]
    if len(recent) >= limit:
        return False, recent
    return True, recent + [now]


def minutes_until_free(times, now, window=RATE_WINDOW):
    return max(1, math.ceil((min(times) + window - now) / 60)) if times else 0


def build_question_cmd(claude_bin, settings=SETTINGS_FILE):
    """claude in print mode, read-only. The prompt is sent on stdin."""
    return [
        str(claude_bin), "-p",
        "--settings", str(settings),
        "--permission-mode", "dontAsk",
        "--allowedTools", READ_ONLY_TOOLS,
        "--disallowedTools", DENIED_TOOLS,
        "--output-format", "json",
    ]


def build_question_prompt(template, question, today=None):
    today = (today or date.today()).isoformat()
    return f"{template.strip()}\n\nData de hoje: {today}\n\nPergunta do dono:\n{question.strip()}\n"


def parse_claude_json(stdout):
    """Return (ok, answer) from `claude -p --output-format json` output."""
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    answer = str(data.get("result") or "").strip()
    return (not data.get("is_error") and bool(answer)), answer


def redact(text, token):
    text = str(text)
    return text.replace(token, "<token>") if token else text


def child_env(extra=None):
    """Environment for child processes, without the bot token."""
    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
    env.update(extra or {})
    return env


def run_question(cmd, prompt, cwd, timeout=QUESTION_TIMEOUT, on_wait=None, poll=TYPING_EVERY):
    """Run claude and wait for it, calling on_wait() every `poll` seconds.
    Returns ('ok' | 'error' | 'timeout', answer, stderr)."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=child_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    deadline = time.monotonic() + timeout
    send = prompt
    while True:
        try:
            out, err = proc.communicate(input=send, timeout=min(poll, max(0.01, deadline - time.monotonic())))
            break
        except subprocess.TimeoutExpired:
            send = None  # the prompt was already written
            if time.monotonic() >= deadline:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)  # claude and anything it started
                except ProcessLookupError:
                    pass
                proc.communicate()
                return "timeout", "", ""
            if on_wait:
                on_wait()
    ok, answer = parse_claude_json(out)
    if proc.returncode != 0:
        ok = False
    return ("ok" if ok else "error"), answer, err or ""


def load_bot_state(path=STATE_FILE):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    offset = state.get("offset")
    times = state.get("question_times")
    return {
        "offset": offset if isinstance(offset, int) else None,
        "question_times": [t for t in times if isinstance(t, (int, float))] if isinstance(times, list) else [],
    }


# ---------- the bot -----------------------------------------------------------

class Bot:
    def __init__(self, chat_id, work, claude_bin, max_per_hour=DEFAULT_MAX_PER_HOUR,
                 state_path=STATE_FILE, api=tg.api, token=""):
        self.chat_id = str(chat_id).strip()
        self.work = Path(work)
        self.claude_bin = claude_bin
        self.max_per_hour = max_per_hour
        self.state_path = Path(state_path)
        self.api = api
        self.token = token
        self.state = load_bot_state(self.state_path)
        self.ignored = 0
        self.questions = queue.Queue()
        self.pending = 0                 # questions queued or being answered
        self.lock = threading.Lock()
        self.research_thread = None

    # -- Telegram output

    def reply(self, text, reply_to=None):
        for i, chunk in enumerate(split_message(text) or ["(vazio)"]):
            params = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": "true"}
            if reply_to and i == 0:
                params["reply_parameters"] = json.dumps(
                    {"message_id": reply_to, "allow_sending_without_reply": True})
            try:
                self.api("sendMessage", params)
            except Exception as e:  # keep going: one failed send must not kill the bot
                log.warning("sendMessage failed: %s", redact(e, self.token))

    def typing(self):
        try:
            self.api("sendChatAction", {"chat_id": self.chat_id, "action": "typing"})
        except Exception as e:
            log.warning("sendChatAction failed: %s", redact(e, self.token))

    def save_state(self):
        status.save_json(self.state, self.state_path)

    # -- incoming updates

    def handle_update(self, update, now=None):
        if not is_allowed(update, self.chat_id):
            self.ignored += 1
            log.info("ignored an update that is not from the owner's chat (total ignored: %d)", self.ignored)
            return
        message = update["message"]
        message_id = message.get("message_id")
        text = message.get("text")
        if text is None:
            self.reply("Por enquanto eu só entendo mensagens de texto.", message_id)
            return
        kind, value = parse_command(text)
        if kind == "command":
            log.info("command /%s", value)
            getattr(self, f"cmd_{value}")(message_id)
        elif kind == "unknown":
            self.reply(f"Comando desconhecido: {value}\n\n{HELP}", message_id)
        elif kind == "question":
            self.ask(value, message_id, now if now is not None else time.time())

    def ask(self, question, message_id, now):
        allowed, times = rate_limit(self.state["question_times"], now, self.max_per_hour)
        self.state["question_times"] = times
        self.save_state()
        if not allowed:
            log.info("question refused by the rate limit (%d/h)", self.max_per_hour)
            wait = minutes_until_free(times, now)
            self.reply(f"Você já fez {self.max_per_hour} perguntas na última hora, que é o limite. "
                       f"Tente de novo em cerca de {wait} min.", message_id)
            return
        with self.lock:
            ahead = self.pending
            self.pending += 1
        log.info("question received (%d chars, %d ahead)", len(question), ahead)
        if ahead:
            self.reply(f"Na fila: {ahead} pergunta(s) antes desta.", message_id)
        self.questions.put((question, message_id))

    # -- commands

    def cmd_ajuda(self, message_id):
        self.reply(HELP, message_id)

    def cmd_status(self, message_id):
        text = status.current_status("pt")
        with self.lock:
            pending = self.pending
        if pending:
            text += f"\nPerguntas em andamento ou na fila: {pending}"
        self.reply(text, message_id)

    def cmd_ultimo(self, message_id):
        path = self.work / "Research" / "log.md"
        entry = tg.last_section(path.read_text(encoding="utf-8")) if path.exists() else ""
        self.reply(entry or "O log de pesquisa ainda está vazio.", message_id)

    def cmd_resumo(self, message_id):
        brief = tg.newest_brief(self.work / "Briefs")
        if brief is None:
            self.reply("Ainda não há nenhum brief semanal.", message_id)
            return
        summary = tg.brief_summary(brief.read_text(encoding="utf-8"))
        self.reply(f"Resumo do brief {brief.stem}\n\n{summary or '(seção Resumo não encontrada)'}", message_id)

    def cmd_pesquisar(self, message_id):
        if self.research_thread and self.research_thread.is_alive():
            self.reply("Já existe uma pesquisa que você pediu em andamento. Aviso quando terminar.", message_id)
            return
        if status.is_locked():
            self.reply("Outro trabalho do agente está rodando. A pesquisa começa assim que ele terminar "
                       "(espera até 30 min). Aviso quando acabar.", message_id)
        else:
            self.reply("Pesquisa iniciada. Aviso quando terminar; costuma levar de 20 a 60 minutos.", message_id)
        self.research_thread = threading.Thread(target=self.run_research, args=(message_id,), daemon=True)
        self.research_thread.start()

    def run_research(self, message_id):
        log.info("research run requested from Telegram")
        try:
            code = subprocess.run(["bash", str(RUN_AGENT), "research"],
                                  env=child_env({"AGENT_SKIP_NOTIFY": "1"}),
                                  stdin=subprocess.DEVNULL).returncode
        except OSError as e:
            log.error("could not start run_agent.sh: %s", e)
            code = 1
        log.info("research run finished with exit code %d", code)
        if code == 0:
            path = self.work / "Research" / "log.md"
            self.reply(tg.build_research_message(path.read_text(encoding="utf-8") if path.exists() else ""),
                       message_id)
        elif code == EXIT_LOCK_BUSY:
            self.reply(tg.build_busy_message("research"), message_id)
        else:
            self.reply(tg.build_failed_message("research"), message_id)

    # -- questions, one at a time

    def answer(self, question, message_id):
        template = QUESTION_PROMPT.read_text(encoding="utf-8")
        self.typing()
        result, answer, err = run_question(build_question_cmd(self.claude_bin), build_question_prompt(template, question),
                                           self.work, on_wait=self.typing)
        log.info("question finished: %s (%d chars)", result, len(answer))
        if result == "ok":
            self.reply(answer, message_id)
        elif result == "timeout":
            self.reply(f"Não consegui responder em {QUESTION_TIMEOUT // 60} minutos e parei. "
                       "Tente uma pergunta mais específica.", message_id)
        else:
            if err.strip():
                log.warning("claude stderr: %s", redact(err.strip()[:300], self.token))
            self.reply("Não consegui responder: o agente terminou com erro. Veja o log do bot no servidor.",
                       message_id)

    def question_worker(self):
        while True:
            question, message_id = self.questions.get()
            try:
                self.answer(question, message_id)
            except Exception as e:
                log.error("question failed: %s", redact(e, self.token))
                self.reply("Não consegui responder por um erro interno. Veja o log do bot no servidor.", message_id)
            finally:
                with self.lock:
                    self.pending -= 1

    # -- main loop

    def skip_backlog(self):
        """On the very first start, skip messages sent before the bot existed."""
        updates = self.api("getUpdates", {"offset": -1, "timeout": 0}) or []
        self.state["offset"] = updates[-1]["update_id"] + 1 if updates else 0
        self.save_state()
        if updates:
            log.info("first start: skipped messages sent before the bot was running")

    def poll_once(self):
        updates = self.api("getUpdates", {
            "offset": self.state["offset"] or 0,
            "timeout": POLL_TIMEOUT,
            "allowed_updates": json.dumps(["message"]),
        }, timeout=POLL_TIMEOUT + 15) or []
        for update in updates:
            # Save first: if handling crashes the bot, this update is not retried forever.
            self.state["offset"] = update["update_id"] + 1
            self.save_state()
            try:
                self.handle_update(update)
            except Exception as e:
                log.error("error handling an update: %s: %s", type(e).__name__, redact(e, self.token))

    def run(self):
        threading.Thread(target=self.question_worker, daemon=True).start()
        if self.state["offset"] is None:
            self.skip_backlog()
        log.info("bot started; answering one chat, %d questions per hour", self.max_per_hour)
        backoff = 5
        while True:
            try:
                self.poll_once()
                backoff = 5
            except Exception as e:
                log.warning("getUpdates failed: %s; retrying in %ds", redact(e, self.token), backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env")
        return EXIT_CONFIG
    try:
        work = agent_dir()
    except SystemExit as e:
        log.error("%s", e)
        return EXIT_CONFIG
    try:
        max_per_hour = int(os.environ.get("BOT_MAX_QUESTIONS_PER_HOUR") or DEFAULT_MAX_PER_HOUR)
    except ValueError:
        max_per_hour = DEFAULT_MAX_PER_HOUR
    claude_bin = os.path.expanduser(os.environ.get("CLAUDE_BIN") or "~/.local/bin/claude")
    bot = Bot(chat_id, work, claude_bin, max(1, max_per_hour), token=token)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
