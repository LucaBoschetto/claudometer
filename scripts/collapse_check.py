#!/usr/bin/env python3
"""Assert UsageDB._collapse_run emits the right points for each run shape.

The collapse lives in Python, so scripts/burn_rate.test.mjs cannot see a bug in
it. This repo declares no Python test framework and this change does not justify
adding one, so these are plain asserts run from scripts/run_tests.sh alongside
the node suite.

Run: python scripts/collapse_check.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config import BURN_WINDOW_MINUTES
from db import COLLAPSE_DECAY_POINTS, UsageDB, _parse_iso

T0 = datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=BURN_WINDOW_MINUTES)
# ts_start + decay points + trailing mirror point + ts_end
LONG_RUN_POINTS = COLLAPSE_DECAY_POINTS + 3

# _collapse_run never touches the DB, and UsageDB.__init__ only stores the path.
DB = UsageDB(pathlib.Path("/nonexistent/never-opened.db"))


def make_run(minutes: float, count: int, **overrides) -> dict:
    run = {
        "ts_start": T0.isoformat(),
        "ts_end": (T0 + timedelta(minutes=minutes)).isoformat(),
        "sample_count": count,
        "session_pct": 10.0,
        "session_resets": "2026-07-16T15:00:00+00:00",
        "weekly_pct": 5.0,
        "weekly_resets": "2026-07-21T08:00:00+00:00",
        "extra_pct": 0.0,
        "extra_enabled": False,
        "extra_used_credits": 0.0,
        "extra_monthly_limit": 1000.0,
        "scoped_pct": None,
        "scoped_model": None,
    }
    run.update(overrides)
    return run


def times(rows: list[dict]) -> list[datetime]:
    return [_parse_iso(r["ts"]) for r in rows]


failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


print("_collapse_run:")

# A single-sample run has no span to draw; one point is all there is.
rows = DB._collapse_run(make_run(minutes=0, count=1))
check("single sample -> 1 point", len(rows) == 1)
check("single sample -> at ts_start", times(rows) == [T0])

# Degenerate spans: _expand_run emits `count` duplicates at the same instant,
# which is visually one point. Collapsing to one is a deliberate divergence.
rows = DB._collapse_run(make_run(minutes=0, count=5))
check("zero-length span -> 1 point", len(rows) == 1)

rows = DB._collapse_run(make_run(minutes=10, count=5, ts_end=T0.isoformat(), ts_start=(T0 + timedelta(minutes=10)).isoformat()))
check("ts_end before ts_start -> 1 point", len(rows) == 1)

rows = DB._collapse_run(make_run(minutes=10, count=5, ts_end="not-a-timestamp"))
check("unparseable ts_end -> 1 point", len(rows) == 1)

rows = DB._collapse_run(make_run(minutes=10, count=5, ts_start="not-a-timestamp"))
check("unparseable ts_start -> 1 point", len(rows) == 1)

# A run shorter than the burn window draws as a flat segment: two points.
rows = DB._collapse_run(make_run(minutes=10, count=10))
check("short run -> 2 points", len(rows) == 2)
check("short run -> endpoints exactly", times(rows) == [T0, T0 + timedelta(minutes=10)])

# Exactly at the window boundary is still "short" (no decay points needed).
rows = DB._collapse_run(make_run(minutes=BURN_WINDOW_MINUTES, count=30))
check("run == window -> 2 points", len(rows) == 2)

# A long run needs interior points, or the burn chart draws its decay as a ramp.
long_run = make_run(minutes=3 * 24 * 60, count=4320)
rows = DB._collapse_run(long_run)
check(f"long run -> {LONG_RUN_POINTS} points", len(rows) == LONG_RUN_POINTS)
ts = times(rows)
check("long run -> starts at ts_start", ts[0] == T0)
check("long run -> ends at ts_end", ts[-1] == _parse_iso(long_run["ts_end"]))
check("long run -> last decay point lands on the window edge", ts[-3] == T0 + WINDOW)
max_gap = WINDOW.total_seconds() / COLLAPSE_DECAY_POINTS
gaps = [(b - a).total_seconds() for a, b in zip(ts[:-3], ts[1:-2])]
check(
    "long run -> no in-window gap exceeds window/COLLAPSE_DECAY_POINTS",
    all(g <= max_gap + 1e-6 for g in gaps),
)
# seriesFor's 'smooth' view averages by index, not by time: without a point
# just before ts_end, the pull toward the next run's value draws as a ramp
# across the whole remaining run instead of one point spacing.
check(
    "long run -> trailing mirror point is within window/COLLAPSE_DECAY_POINTS of ts_end",
    (ts[-1] - ts[-2]).total_seconds() <= max_gap + 1e-6,
)
check("long run -> timestamps strictly increasing", all(a < b for a, b in zip(ts, ts[1:])))

# Just over the window: end_dt - step lands before the window-edge decay point,
# so the mirror point must be skipped rather than emitted out of order. This is
# the guard's regression test.
just_over_run = make_run(minutes=BURN_WINDOW_MINUTES + 1, count=32)
rows = DB._collapse_run(just_over_run)
ts = times(rows)
check(
    "run just over window -> timestamps strictly increasing",
    all(a < b for a, b in zip(ts, ts[1:])),
)

# The RLE property: every point of a run carries that run's values.
values = [{k: v for k, v in r.items() if k != "ts"} for r in rows]
check("long run -> all points carry identical values", all(v == values[0] for v in values))
check("long run -> values match the run", values[0]["session_pct"] == 10.0)

print()
print("fetch_chart_data:")

RUN_COLUMNS = (
    "ts_start", "ts_end", "sample_count", "session_pct", "session_resets",
    "weekly_pct", "weekly_resets", "extra_pct", "extra_enabled",
    "extra_used_credits", "extra_monthly_limit", "scoped_pct", "scoped_model",
)

with tempfile.TemporaryDirectory() as tmp:
    db = UsageDB(pathlib.Path(tmp) / "usage.db")
    db.init()
    # One long idle run (collapses to 6) and one short run (collapses to 2).
    seed = [
        make_run(minutes=3 * 24 * 60, count=4320),
        make_run(minutes=10, count=10, ts_start=(T0 + timedelta(days=3)).isoformat(),
                 ts_end=(T0 + timedelta(days=3, minutes=10)).isoformat()),
    ]
    with sqlite3.connect(db.path) as conn:
        for run in seed:
            conn.execute(
                f"INSERT INTO usage_runs ({','.join(RUN_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(RUN_COLUMNS))})",
                tuple(run[c] for c in RUN_COLUMNS),
            )
        conn.commit()

    expanded_total = 4320 + 10
    collapsed_total = LONG_RUN_POINTS + 2  # long run + short run's endpoints

    all_payload = db.fetch_chart_data("all")
    check("range=all -> collapsed rows", len(all_payload["rows"]) == collapsed_total)
    check("range=all -> total_samples counts real samples, not rows",
          all_payload["total_samples"] == expanded_total)
    check("range=all -> filtered_samples counts real samples",
          all_payload["filtered_samples"] == expanded_total)
    check("range=all -> run_count unchanged", all_payload["run_count"] == 2)

    # An unknown range normalizes to "all" in _range_window, so it must collapse
    # too. Otherwise it would get the all-window but 4330 expanded rows.
    bogus = db.fetch_chart_data("nonsense")
    check("unknown range -> collapses like all",
          len(bogus["rows"]) == len(all_payload["rows"]))

    # The zoomed presets must keep full per-sample expansion. The exact count is
    # timezone-dependent (the "today" window is derived from the latest ts_end in
    # local time), but expansion always yields strictly more rows than the
    # collapse would, and the latest run always falls inside the window.
    today = db.fetch_chart_data("today")
    check("range=today -> still expands (not collapsed)",
          len(today["rows"]) > collapsed_total)

print()
if failures:
    print(f"collapse_check: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("collapse_check: all passed")
