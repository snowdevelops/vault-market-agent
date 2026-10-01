#!/usr/bin/env python3
"""
Send the TL;DR section of the newest weekly brief to Telegram.

Only the TL;DR is sent. The weekly prompt keeps costs, margins and prices out of
that section, so financial details never leave your machine through Telegram.

Usage:
  python3 scripts/notify_telegram.py            send newest brief's TL;DR
  python3 scripts/notify_telegram.py --test     send a test message
  python3 scripts/notify_telegram.py --failed   report that the brief run failed
  python3 scripts/notify_telegram.py --dry-run  print instead of sending

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. Standard library only.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from common import load_env, vault_dir

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


def count_fetch_errors(latest_md_text):
    section = extract_section(latest_md_text, "Fetch errors")
    return sum(1 for line in section.splitlines() if line.startswith("- "))


def build_message(brief_name, brief_text, fetch_errors):
    tldr = extract_section(brief_text, "TL;DR") or "(no TL;DR section found, open the brief)"
    msg = f"Weekly brief {brief_name}\n\n{tldr}"
    if fetch_errors:
        msg += f"\n\n{fetch_errors} data fetch error(s), see the brief."
    if len(msg) > MAX_LEN:
        msg = msg[: MAX_LEN - 20].rstrip() + "\n...(truncated)"
    return msg


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


def main(argv):
    load_env()
    dry_run = "--dry-run" in argv

    if "--test" in argv:
        text = "Test message from vault-market-agent. If you see this, notifications work."
    elif "--failed" in argv:
        text = "Weekly brief run FAILED. Check Business/Briefs/.last_run.log on the server."
    else:
        vault = vault_dir()
        brief = newest_brief(vault / "Business" / "Briefs")
        if brief is None:
            raise SystemExit("No brief found in Business/Briefs/")
        latest = vault / "Business" / "Market" / "data" / "latest.md"
        errors = count_fetch_errors(latest.read_text(encoding="utf-8")) if latest.exists() else 0
        text = build_message(brief.stem, brief.read_text(encoding="utf-8"), errors)

    if dry_run:
        print(text)
    else:
        send(text)
        print("sent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
