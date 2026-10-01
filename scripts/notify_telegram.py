#!/usr/bin/env python3
"""
Send a short, privacy-safe update to Telegram after an agent run.

  python3 scripts/notify_telegram.py brief            TL;DR of the newest weekly brief
  python3 scripts/notify_telegram.py research         newest entry of Research/log.md
  python3 scripts/notify_telegram.py --failed JOB     report that a run failed
  python3 scripts/notify_telegram.py --test           send a test message
  add --dry-run to print instead of sending

Only these short sections are sent. The prompts keep costs, margins and prices out
of them, so financial details never leave your machine through Telegram.

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. Standard library only.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from common import agent_dir, load_env

MAX_LEN = 3800  # Telegram caps messages at 4096 characters


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


def cap(msg):
    if len(msg) > MAX_LEN:
        msg = msg[: MAX_LEN - 20].rstrip() + "\n...(truncated)"
    return msg


def build_brief_message(brief_name, brief_text, fetch_errors):
    tldr = extract_section(brief_text, "TL;DR") or "(no TL;DR section found, open the brief)"
    msg = f"Weekly brief {brief_name}\n\n{tldr}"
    if fetch_errors:
        msg += f"\n\n{fetch_errors} data fetch error(s), see the brief."
    return cap(msg)


def build_research_message(log_text):
    entry = last_section(log_text) or "(research log is empty)"
    return cap(f"Research run\n\n{entry}")


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram refused the message: {body.get('description')}")


def compose(args):
    if "--test" in args:
        return "Test message from vault-market-agent. If you see this, notifications work."
    if "--failed" in args:
        i = args.index("--failed")
        job = args[i + 1] if i + 1 < len(args) else "agent"
        return f"Agent {job} run FAILED. Check .last_{job}.log in the agent folder on the server."

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
