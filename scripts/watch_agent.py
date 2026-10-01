#!/usr/bin/env python3
"""
Follow an agent run live, one readable line per step.

  python3 scripts/watch_agent.py                 follow the newest .last_*.jsonl
  python3 scripts/watch_agent.py --job research  follow .last_research.jsonl
  python3 scripts/watch_agent.py --summary FILE  print a short summary of a finished run

run_agent.sh writes Claude Code's stream-json output to .last_<job>.jsonl in the
agent folder. This script reads it like `tail -f`: it prints what is already there,
then waits for new lines, and exits when the final result arrives (or on Ctrl+C).
If the run already finished, it replays it and exits.

--summary exits non-zero when the run has no result or ended in an error, so
run_agent.sh can use it to mark the run as failed. Standard library only.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

TEXT_WIDTH = 160

# Input field that best describes what each tool is working on.
TARGET_FIELDS = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebSearch": "query",
    "WebFetch": "url",
    "Bash": "command",
    "Task": "description",
    "Agent": "description",
}
QUOTED = {"Glob", "Grep", "WebSearch"}


def one_line(text, width=TEXT_WIDTH):
    """Collapse whitespace and cut to width."""
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def relative(path, cwd):
    """Show paths inside the agent folder relative to it."""
    if cwd and isinstance(path, str) and os.path.isabs(path):
        try:
            rel = os.path.relpath(path, cwd)
        except ValueError:
            return path
        if not rel.startswith(".."):
            return rel
    return path


def tool_target(name, tool_input, cwd=None):
    """Return the file path, search query, URL or pattern a tool call works on."""
    if not isinstance(tool_input, dict):
        return ""
    field = TARGET_FIELDS.get(name)
    value = tool_input.get(field) if field else None
    if value is None:
        value = next((v for v in tool_input.values() if isinstance(v, str) and v), "")
    if field in ("file_path", "notebook_path"):
        return relative(value, cwd)
    if name in ("Glob", "Grep") and tool_input.get("path"):
        return f'"{value}" in {relative(tool_input["path"], cwd)}'
    if name in QUOTED:
        return f'"{value}"'
    return str(value)


def format_duration(ms):
    if not isinstance(ms, (int, float)):
        return "?"
    seconds = int(ms // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def result_ok(event):
    return event.get("subtype") == "success" and not event.get("is_error")


def result_headline(event):
    status = "ok" if result_ok(event) else f"FAILED ({event.get('subtype', 'unknown')})"
    return (f"{status}, {event.get('num_turns', '?')} turns, "
            f"{format_duration(event.get('duration_ms'))}")


def _row(label, text):
    return f"{label:<10}{text}"


def _tool_result_text(content):
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content or ""


def format_event(event, cwd=None):
    """Turn one stream-json event into zero or more readable lines."""
    if not isinstance(event, dict):
        return []
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return [_row("start", f"session started (model {event.get('model', '?')})")]
    if kind == "assistant":
        lines = []
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                lines.append(_row("text", one_line(block["text"])))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                lines.append(_row(name, one_line(tool_target(name, block.get("input"), cwd))))
        return lines
    if kind == "user":
        lines = []
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                lines.append(_row("! error", one_line(_tool_result_text(block.get("content")))))
        return lines
    if kind == "result":
        lines = [_row("done", result_headline(event))]
        if event.get("result"):
            lines += ["", str(event["result"]).strip()]
        return lines
    return []


def parse_line(line):
    """Return the event dict for a JSON line, or None for anything else."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def format_line(line, state):
    """Format one raw line. `state` keeps the session cwd between calls."""
    line = line.strip()
    if not line:
        return []
    event = parse_line(line)
    if event is None:
        return [_row("?", one_line(line))]
    if event.get("type") == "system" and event.get("cwd"):
        state["cwd"] = event["cwd"]
    return format_event(event, state.get("cwd"))


def summarize(lines, job="agent", now=None):
    """Return (ok, text) for a finished run's stream-json lines."""
    now = now or datetime.now()
    result, tools = None, {}
    for line in lines:
        event = parse_line(line.strip()) if line.strip() else None
        if not event:
            continue
        if event.get("type") == "result":
            result = event
        elif event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    tools[name] = tools.get(name, 0) + 1
    out = [f"job: {job}", f"finished: {now.isoformat(timespec='seconds')}"]
    if tools:
        used = ", ".join(f"{name} {count}" for name, count in sorted(tools.items()))
        out.append(f"tools: {sum(tools.values())} ({used})")
    if result is None:
        out.append("status: FAILED (no result in the stream; see errors above)")
        return False, "\n".join(out)
    ok = result_ok(result)
    out.append(f"status: {result_headline(result)}")
    if result.get("result"):
        out += ["", str(result["result"]).strip()]
    return ok, "\n".join(out)


def follow(path, out=sys.stdout, poll=0.5, sleep=time.sleep):
    """Print the run in `path` as it grows. Return 0 if it ended ok, 1 if not."""
    # Bytes, not text: offsets stay exact and a UTF-8 character split across two
    # reads is only decoded once its line is complete.
    state, buffer, position = {}, b"", 0
    while True:
        try:
            size = os.path.getsize(path)
        except FileNotFoundError:
            size = 0
        if size < position:  # a new run truncated the file: start over
            print(_row("restart", "a new run started"), file=out)
            state, buffer, position = {}, b"", 0
        if size > position:
            with open(path, "rb") as f:
                f.seek(position)
                chunk = f.read()
                position = f.tell()
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for raw in complete:
                line = raw.decode("utf-8", errors="replace")
                for text in format_line(line, state):
                    print(text, file=out, flush=True)
                event = parse_line(line.strip())
                if event and event.get("type") == "result":
                    return 0 if result_ok(event) else 1
        sleep(poll)


def newest_stream(folder, job=None):
    pattern = f".last_{job}.jsonl" if job else ".last_*.jsonl"
    files = [p for p in Path(folder).glob(pattern) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def job_from_path(path):
    name = Path(path).name
    if name.startswith(".last_") and name.endswith(".jsonl"):
        return name[len(".last_"):-len(".jsonl")]
    return "agent"


def main(argv):
    if "--summary" in argv:
        i = argv.index("--summary")
        if i + 1 >= len(argv):
            raise SystemExit("usage: watch_agent.py --summary FILE")
        path = Path(argv[i + 1])
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        ok, text = summarize(lines, job_from_path(path))
        print(text)
        return 0 if ok else 1

    from common import agent_dir
    job = None
    if "--job" in argv:
        i = argv.index("--job")
        job = argv[i + 1] if i + 1 < len(argv) else None
    folder = agent_dir()
    path = newest_stream(folder, job)
    if path is None:
        print(f"No .last_{job or '*'}.jsonl in {folder} yet. Has a job run since the update?")
        return 1
    print(f"Following {path.name} (Ctrl+C to stop)\n")
    try:
        return follow(path)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
