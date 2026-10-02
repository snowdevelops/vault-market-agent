#!/usr/bin/env python3
"""
One-time load of past FIPE values for the basket (config/fipe-basket.json), so
depreciation curves exist from day one.

  python3 scripts/backfill_fipe.py              last 24 monthly tables
  python3 scripts/backfill_fipe.py --months 6   fewer months

Writes Market/data/fipe_history.csv in the agent folder, the same file the daily
fetch appends to. Values already there are skipped, so the script can be stopped
and run again; it continues where it left off. The FIPE site answers "too many
requests" when called fast, so calls are spaced (about 1,200 calls for 24 months,
roughly 30 to 60 minutes). Run it in tmux or with nohup. Standard library only.
"""
import sys
from datetime import date

import fipe
from common import agent_dir


def parse_months(argv):
    if not argv:
        return 24
    if len(argv) == 2 and argv[0] == "--months" and argv[1].isdigit() and 1 <= int(argv[1]) <= 120:
        return int(argv[1])
    raise SystemExit(__doc__)


def backfill(client, basket, history_path, months, fetched_on, out=print):
    """Fetch the missing basket values of the newest `months` tables. Returns the error list."""
    tables = client.tables()[:months]
    keys = fipe.history_keys(fipe.read_history(history_path))
    todo = sum(len(fipe.missing(basket, keys, month)) for _, month in tables)
    out(f"{len(tables)} FIPE tables ({tables[-1][1]} to {tables[0][1]}), {todo} values to fetch")
    done, errors = 0, []
    for table, month in tables:
        rows, errs = fipe.fetch_month(client, basket, table, month, keys, fetched_on)
        fipe.append_history(history_path, rows)  # saved per month, so a stop loses little
        keys |= fipe.history_keys(rows)
        errors += errs
        done += len(rows) + len(errs)
        out(f"  {month}: {len(rows)} values, {len(errs)} errors ({done}/{todo})")
    return errors


def main(argv):
    months = parse_months(argv)
    data_dir = agent_dir() / "Market" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    errors = backfill(fipe.Client(), fipe.load_basket(), data_dir / "fipe_history.csv", months,
                      date.today().isoformat(), out=lambda line: print(line, flush=True))
    for e in errors:
        print("  !", e, file=sys.stderr)
    print("done" if not errors else f"done with {len(errors)} errors; run again to retry them")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
