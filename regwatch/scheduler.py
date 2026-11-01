"""Scheduled monitoring: polls every regulator whose interval (sources.yaml `interval_hours`) has elapsed.

    python -m regwatch.scheduler --once            # one cycle (use with cron / Windows Task Scheduler)
    python -m regwatch.scheduler --every 15        # keep running, check every 15 minutes
    python -m regwatch.scheduler --once --force    # poll all sources now, ignoring intervals

Each cycle writes one monitoring_run row per polled source (ok / partial / error); failing sites
fall back to their seeded copies, so a cycle never stalls. New documents go through the full
pipeline (hash dedupe -> extract -> compare -> impact mapping).
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

from . import db, monitor


def cycle(force: bool = False) -> list[dict]:
    conn = db.connect()
    db.init_db(conn)
    due = None if force else monitor.due_sources(conn)
    if due == []:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  nothing due")
        return []
    results = monitor.run_monitoring(conn, due)
    for r in results:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {r['source']:<6} {r['status']:<8} {r['message'][:140]}")
    conn.close()
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="RegWatch scheduled monitoring")
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--every", type=float, default=15, help="minutes between due-checks (loop mode)")
    ap.add_argument("--force", action="store_true", help="poll every source now, ignoring intervals")
    a = ap.parse_args()
    if a.once:
        cycle(a.force)
        return
    print(f"RegWatch scheduler: checking due sources every {a.every:g} min (Ctrl+C to stop)")
    while True:
        try:
            cycle(a.force)
        except Exception as e:  # a failed cycle must never kill the scheduler
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  cycle failed: {type(e).__name__}: {e}")
        time.sleep(max(1.0, a.every) * 60)


if __name__ == "__main__":
    main()
