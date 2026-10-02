#!/usr/bin/env python3
"""
Show whether an agent job is running and how the last run of each job went.

  python3 scripts/status.py                    print the status
  python3 scripts/status.py start JOB PID      (run_agent.sh) record that JOB started
  python3 scripts/status.py finish JOB CODE    (run_agent.sh) record how JOB ended

State lives in .state/ in this repository (gitignored), never in the vault:
  .state/agent.lock    held with flock by run_agent.sh while a job runs
  .state/status.json   which job holds the lock, since when, and each job's last run

The lock is the source of truth for "running", so a run that was killed cannot
leave a stale flag behind. Only run_agent.sh writes status.json, and only while
holding the lock, so there is never more than one writer. Standard library only.
"""
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_DIR / ".state"
STATUS_FILE = STATE_DIR / "status.json"
LOCK_FILE = STATE_DIR / "agent.lock"
JOBS = ("research", "brief")  # always listed; others (review) appear once they have run

LABELS = {
    "en": {
        "jobs": {"research": "research", "brief": "brief", "review": "sale review"},
        "running": "Running: {job} since {since} ({ago})",
        "running_unknown": "Running: a job (no details recorded)",
        "idle": "No job running.",
        "never": "{job}: never run",
        "last": "{job}: last run {started}, {outcome}, took {took}",
        "interrupted": "{job}: run started {started} did not finish",
        "ok": "ok",
        "failed": "failed (exit {code})",
        "date": "%Y-%m-%d %H:%M",
        "minutes": "{n} min",
    },
    "pt": {
        "jobs": {"research": "Pesquisa", "brief": "Brief semanal", "review": "Revisão de venda"},
        "running": "Em execução: {job} desde {since} (há {ago})",
        "running_unknown": "Em execução: um trabalho (sem detalhes registrados)",
        "idle": "Nenhum trabalho em execução.",
        "never": "{job}: nunca executado",
        "last": "{job}: última em {started}, {outcome}, levou {took}",
        "interrupted": "{job}: execução iniciada em {started} não terminou",
        "ok": "ok",
        "failed": "falhou (código {code})",
        "date": "%d/%m %H:%M",
        "minutes": "{n} min",
    },
}


def now_iso(now=None):
    return (now or datetime.now()).isoformat(timespec="seconds")


def empty_state():
    return {"running": None, "jobs": {}}


def load_state(path=STATUS_FILE):
    """Return the saved state; a missing or damaged file gives an empty state."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_state()
    if not isinstance(state, dict):
        return empty_state()
    state.setdefault("running", None)
    if not isinstance(state.get("jobs"), dict):
        state["jobs"] = {}
    return state


def save_json(data, path):
    """Write JSON atomically: readers see the old file or the new one, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_state(state, path=STATUS_FILE):
    save_json(state, path)


def record_start(state, job, pid, now=None):
    """Mark `job` as running. Its previous result stays in jobs until it finishes."""
    state["running"] = {"job": job, "pid": int(pid), "started": now_iso(now)}
    return state


def record_finish(state, job, exit_code, now=None):
    exit_code = int(exit_code)
    running = state.get("running") or {}
    started = running.get("started") if running.get("job") == job else None
    state["jobs"][job] = {"started": started, "finished": now_iso(now),
                          "exit_code": exit_code, "ok": exit_code == 0}
    state["running"] = None
    return state


def is_locked(lock_path=LOCK_FILE):
    """True if some process holds the job lock right now."""
    try:
        fd = os.open(lock_path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _parse(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _minutes(start, end, labels):
    if not start or not end:
        return "?"
    return labels["minutes"].format(n=max(0, round((end - start).total_seconds() / 60)))


def _when(value, labels):
    parsed = _parse(value)
    return parsed.strftime(labels["date"]) if parsed else "?"


def format_status(state, locked, now=None, lang="en"):
    """Readable status: the running job (if any), then the last run of each job."""
    labels = LABELS[lang]
    now = now or datetime.now()
    names = labels["jobs"]
    running = state.get("running") or {}
    lines = []
    running_name = names.get(running.get("job"), running.get("job"))
    if locked and running.get("job"):
        lines.append(labels["running"].format(
            job=running_name,
            since=_when(running.get("started"), labels),
            ago=_minutes(_parse(running.get("started")), now, labels)))
    elif locked:
        lines.append(labels["running_unknown"])
    else:
        lines.append(labels["idle"])
        if running.get("job"):
            # Recorded as started, but nobody holds the lock: the run was killed.
            lines.append(labels["interrupted"].format(
                job=running_name, started=_when(running.get("started"), labels)))

    jobs = state.get("jobs") or {}
    for job in list(JOBS) + sorted(set(jobs) - set(JOBS)):
        name = names.get(job, job)
        entry = jobs.get(job)
        if not entry:
            lines.append(labels["never"].format(job=name))
            continue
        outcome = labels["ok"] if entry.get("ok") else labels["failed"].format(code=entry.get("exit_code"))
        lines.append(labels["last"].format(
            job=name, started=_when(entry.get("started"), labels), outcome=outcome,
            took=_minutes(_parse(entry.get("started")), _parse(entry.get("finished")), labels)))
    return "\n".join(lines)


def current_status(lang="en", state_dir=STATE_DIR, now=None):
    state_dir = Path(state_dir)
    return format_status(load_state(state_dir / "status.json"), is_locked(state_dir / "agent.lock"), now, lang)


def main(argv):
    if not argv:
        print(current_status("en"))
        return 0
    if len(argv) == 3 and argv[0] in ("start", "finish"):
        command, job, number = argv
        state = load_state()
        if command == "start":
            record_start(state, job, number)
        else:
            record_finish(state, job, number)
        save_state(state)
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
