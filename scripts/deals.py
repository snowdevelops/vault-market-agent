#!/usr/bin/env python3
"""
Helpers for the owner's deal notes in Deals/ (shared by the bot, the capture
commands and the review job).

  python3 scripts/deals.py pending-reviews    sold deals without Reviews/<deal>.md,
                                              one name per line (used by run_agent.sh)

Deal notes are Markdown with a YAML-like frontmatter (see
vault-template/agent/Templates/vehicle-deal.md). Only simple `key: value` lines are
read and written; everything else in a note is left exactly as it is.
Standard library only.
"""
import re
import sys
import unicodedata
from datetime import date

CLOSED = ("sold", "dropped")

_KEY_LINE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):(?P<rest>.*)$")


def normalize(text):
    """Lower case, no accents, and '-', '_' and runs of spaces as one space, so
    'Ônix_2019' and 'onix 2019' compare equal."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[-_]+", " ", text).split())


def deal_files(deals_dir):
    """Deal notes, sorted by name. Hidden files and folders are skipped."""
    if not deals_dir.is_dir():
        return []
    files = [p for p in deals_dir.rglob("*.md")
             if not any(part.startswith(".") for part in p.relative_to(deals_dir).parts)]
    return sorted(files, key=lambda p: normalize(p.stem))


def find_deal(deals_dir, query):
    """Match `query` against note file names. An exact match wins; otherwise the
    query must appear in exactly one name. Returns (path or None, candidates):
    the candidates are the ambiguous matches, or every deal when nothing matched."""
    files = deal_files(deals_dir)
    wanted = normalize(query)
    if not wanted:
        return None, files
    exact = [p for p in files if normalize(p.stem) == wanted]
    if len(exact) == 1:
        return exact[0], exact
    partial = exact or [p for p in files if wanted in normalize(p.stem)]
    if len(partial) == 1:
        return partial[0], partial
    return None, (partial or files)


# ---------- frontmatter ------------------------------------------------------

def _frontmatter_bounds(lines):
    """Indexes (first, closing) of the '---' lines around the frontmatter, or None."""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 0, i
    return None


def _split_value(rest):
    """Split what follows 'key:' into (spaces, value, comment). A '#' starts a
    comment only after whitespace and outside quotes."""
    spaces = rest[: len(rest) - len(rest.lstrip(" \t"))]
    body = rest[len(spaces):]
    if body.startswith("#"):
        return "", "", rest
    if body[:1] in ('"', "'"):
        end = body.find(body[0], 1)
        while end != -1 and body[0] == '"' and body[end - 1] == "\\":
            end = body.find('"', end + 1)
        if end != -1:
            value, tail = body[: end + 1], body[end + 1:]
            return spaces, value, tail
    m = re.search(r"[ \t]+#", body)
    if m:
        return spaces, body[: m.start()], body[m.start():]
    return spaces, body.rstrip(" \t"), body[len(body.rstrip(" \t")):]


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner.replace("''", "'")
    return value


def read_frontmatter(text):
    """Return the frontmatter as {key: value string}, comments and quotes removed."""
    lines = text.splitlines()
    bounds = _frontmatter_bounds(lines)
    if not bounds:
        return {}
    out = {}
    for line in lines[bounds[0] + 1: bounds[1]]:
        m = _KEY_LINE.match(line)
        if m:
            out[m.group("key")] = _unquote(_split_value(m.group("rest"))[1])
    return out


def yaml_value(value):
    """Format a value for a frontmatter line: numbers and ISO dates bare, text quoted."""
    if isinstance(value, bool) or value is None:
        raise TypeError("unsupported frontmatter value")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_frontmatter(text, updates):
    """Set frontmatter keys to already formatted values (see yaml_value). Existing
    lines keep their trailing comment, at the same column where possible; missing
    keys are added before the closing '---'. Nothing else changes."""
    lines = text.splitlines(keepends=True)
    bounds = _frontmatter_bounds([l.rstrip("\r\n") for l in lines])
    if not bounds:
        raise ValueError("note has no frontmatter")
    newline = "\r\n" if "\r\n" in text else "\n"
    pending = dict(updates)
    for i in range(bounds[0] + 1, bounds[1]):
        raw = lines[i]
        body = raw.rstrip("\r\n")
        ending = raw[len(body):]
        m = _KEY_LINE.match(body)
        if not m or m.group("key") not in pending:
            continue
        key = m.group("key")
        spaces, old, comment = _split_value(m.group("rest"))
        new = pending.pop(key)
        if comment.strip():
            pad = len(comment) - len(comment.lstrip(" \t"))
            pad = max(1, pad + len(spaces) + len(old) - 1 - len(new))
            comment = " " * pad + comment.lstrip(" \t")
        else:
            comment = ""
        lines[i] = f"{key}: {new}{comment}{ending}"
    if pending:
        added = "".join(f"{k}: {v}{newline}" for k, v in pending.items())
        lines.insert(bounds[1], added)
    return "".join(lines)


# ---------- deal lists ---------------------------------------------------------

def parse_iso_date(value):
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def deal_info(path):
    try:
        return read_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def open_deals(deals_dir, today=None):
    """Deals whose status is not sold or dropped, as dicts with name, status,
    days listed (None if not listed yet) and max_days_listed."""
    today = today or date.today()
    out = []
    for path in deal_files(deals_dir):
        info = deal_info(path)
        status = info.get("status", "").strip().lower()
        if status in CLOSED:
            continue
        listed = parse_iso_date(info.get("listed_on", ""))
        out.append({
            "name": path.stem,
            "status": status or "?",
            "days_listed": (today - listed).days if listed else None,
            "max_days_listed": info.get("max_days_listed", "").strip(),
        })
    return out


def pending_reviews(work):
    """Names of sold deals that have no Reviews/<name>.md yet."""
    reviews = work / "Reviews"
    return [p.stem for p in deal_files(work / "Deals")
            if deal_info(p).get("status", "").strip().lower() == "sold"
            and not (reviews / f"{p.stem}.md").exists()]


def main(argv):
    if argv == ["pending-reviews"]:
        from common import agent_dir
        for name in pending_reviews(agent_dir()):
            print(name)
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
