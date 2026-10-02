#!/usr/bin/env python3
"""
Send a short, privacy-safe update to Telegram after an agent run.

  python3 scripts/notify_telegram.py brief            'Resumo' section of the newest weekly brief
  python3 scripts/notify_telegram.py research         newest entry of Research/log.md
  python3 scripts/notify_telegram.py review DEAL...   names of the sale reviews just written
  python3 scripts/notify_telegram.py --failed JOB     report that a run failed
  python3 scripts/notify_telegram.py --busy JOB       report that a run was skipped (lock)
  python3 scripts/notify_telegram.py --test           send a test message
  add --dry-run to print instead of sending

Only these short sections are sent. The prompts keep costs, margins and prices out
of them, so financial details never leave your machine through Telegram.
Messages are in Portuguese because they go to the owner.

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from common import agent_dir, load_env

MAX_LEN = 3800  # Telegram caps messages at 4096 characters

# Heading of the phone-safe section in a brief. Briefs written before the switch
# to Portuguese used "TL;DR", so it stays as a fallback.
SUMMARY_HEADINGS = ("Resumo", "TL;DR")

JOB_LABELS = {"research": "pesquisa", "brief": "brief semanal", "review": "revisão de venda"}


def newest_brief(briefs_dir):
    files = sorted(briefs_dir.glob("????-??-??.md"))
    return files[-1] if files else None


def extract_section(text, heading):
    """Return the body of a '## heading' section, or '' if absent."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().lower() == heading.lower()
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def last_section(text):
    """Return the last '## ' section (heading included), or '' if none."""
    sections, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            current = [line[3:].strip()]
            sections.append(current)
        elif current is not None:
            current.append(line)
    if not sections:
        return ""
    heading, *body = sections[-1]
    return (heading + "\n" + "\n".join(body).strip()).strip()


def count_fetch_errors(latest_md_text):
    section = extract_section(latest_md_text, "Fetch errors")
    return sum(1 for line in section.splitlines() if line.startswith("- "))


def brief_summary(brief_text):
    """Return the phone-safe summary section of a brief, or '' if absent."""
    for heading in SUMMARY_HEADINGS:
        section = extract_section(brief_text, heading)
        if section:
            return section
    return ""


def cap(msg):
    if len(msg) > MAX_LEN:
        msg = msg[: MAX_LEN - 20].rstrip() + "\n...(cortado)"
    return msg


def build_brief_message(brief_name, brief_text, fetch_errors):
    summary = brief_summary(brief_text) or "(seção Resumo não encontrada, abra o brief)"
    msg = f"Brief semanal {brief_name}\n\n{summary}"
    if fetch_errors:
        msg += f"\n\n{fetch_errors} erro(s) na coleta de dados, veja o brief."
    return cap(msg)


def build_research_message(log_text):
    entry = last_section(log_text) or "(o log de pesquisa está vazio)"
    return cap(f"Pesquisa\n\n{entry}")


def build_review_message(deals):
    """Reviews contain prices, so only their file names go to the phone."""
    names = "\n".join(f"- Reviews/{d}.md" for d in deals)
    return cap(f"Revisão de venda pronta\n\n{names}\n\n"
               "As lições entraram em Knowledge/playbook.md. Abra as notas no Obsidian para ler.")


def build_failed_message(job):
    label = JOB_LABELS.get(job, job)
    return f"FALHA na execução do agente ({label}). Veja .last_{job}.log na pasta do agente no servidor."


def build_busy_message(job):
    label = JOB_LABELS.get(job, job)
    return (f"Execução do agente ({label}) cancelada: outro trabalho ainda estava rodando "
            "depois de 30 minutos de espera.")


def api(method, params=None, timeout=30):
    """Call a Telegram Bot API method and return its result. Errors never include
    the request URL, because it contains the token."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except ValueError:
            body = {"description": f"HTTP {e.code}"}
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body.get("result")


def send(text):
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        raise SystemExit("TELEGRAM_CHAT_ID missing in .env")
    api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"})


def compose(args):
    if "--test" in args:
        return "Mensagem de teste do vault-market-agent. Se você está lendo isto, as notificações funcionam."
    if "--busy" in args:
        i = args.index("--busy")
        return build_busy_message(args[i + 1] if i + 1 < len(args) else "agent")
    if "--failed" in args:
        i = args.index("--failed")
        job = args[i + 1] if i + 1 < len(args) else "agent"
        return build_failed_message(job)

    if args and args[0] == "review":
        return build_review_message([a for a in args[1:] if a != "--dry-run"])
    base = agent_dir()
    if "brief" in args:
        brief = newest_brief(base / "Briefs")
        if brief is None:
            raise SystemExit("No brief found in Briefs/")
        latest = base / "Market" / "data" / "latest.md"
        errors = count_fetch_errors(latest.read_text(encoding="utf-8")) if latest.exists() else 0
        return build_brief_message(brief.stem, brief.read_text(encoding="utf-8"), errors)
    if "research" in args:
        log = base / "Research" / "log.md"
        return build_research_message(log.read_text(encoding="utf-8") if log.exists() else "")
    raise SystemExit(__doc__)


def main(argv):
    load_env()
    text = compose(argv)
    if "--dry-run" in argv:
        print(text)
    else:
        send(text)
        print("sent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
