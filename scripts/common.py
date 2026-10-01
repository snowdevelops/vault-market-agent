"""Shared helpers: load .env and resolve the vault path.

The vault (with real deal data) lives OUTSIDE this repository. Its location comes
from VAULT_DIR in .env, which is gitignored, so no private path or data ever lands
in version control.
"""
import os
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]


def load_env(path=None):
    """Minimal .env loader for KEY=value lines. Variables already set in the
    environment take precedence over the file."""
    path = Path(path) if path else REPO_DIR / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def vault_dir():
    """Return the vault path from VAULT_DIR, or exit with a clear message."""
    load_env()
    value = os.environ.get("VAULT_DIR")
    if not value:
        raise SystemExit("VAULT_DIR is not set. Copy .env.example to .env and fill it in.")
    path = Path(value).expanduser()
    if not path.is_dir():
        raise SystemExit(f"VAULT_DIR does not exist: {path}")
    return path
