#!/usr/bin/env python3
"""Assert the sonnet_pct -> scoped_pct migration renames, backfills, and is idempotent.

Plain asserts, run from scripts/run_tests.sh.
Run: python scripts/scoped_migration_check.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db import UsageDB

# The pre-migration schema: usage_runs carrying the old sonnet_pct column.
OLD_SCHEMA = """
CREATE TABLE usage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start TEXT NOT NULL, ts_end TEXT NOT NULL, sample_count INTEGER NOT NULL,
  session_pct REAL, session_resets TEXT, weekly_pct REAL, weekly_resets TEXT,
  extra_pct REAL, extra_enabled INTEGER, extra_used_credits REAL,
  extra_monthly_limit REAL, sonnet_pct REAL
);
"""

# Schema predating even sonnet_pct: neither sonnet_pct nor scoped_pct exists yet.
PRE_SONNET_SCHEMA = """
CREATE TABLE usage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start TEXT NOT NULL, ts_end TEXT NOT NULL, sample_count INTEGER NOT NULL,
  session_pct REAL, session_resets TEXT, weekly_pct REAL, weekly_resets TEXT,
  extra_pct REAL, extra_enabled INTEGER, extra_used_credits REAL,
  extra_monthly_limit REAL
);
"""

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(usage_runs)").fetchall()}


print("sonnet_pct -> scoped_pct migration:")

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "usage.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        # One Sonnet-era row (non-null) and one null row.
        conn.execute("INSERT INTO usage_runs (ts_start, ts_end, sample_count, sonnet_pct) "
                     "VALUES ('t0','t0',1, 12.5)")
        conn.execute("INSERT INTO usage_runs (ts_start, ts_end, sample_count, sonnet_pct) "
                     "VALUES ('t1','t1',1, NULL)")
        conn.commit()

    UsageDB(path).init()

    with sqlite3.connect(path) as conn:
        cols = columns(conn)
        check("sonnet_pct column gone", "sonnet_pct" not in cols)
        check("scoped_pct column present", "scoped_pct" in cols)
        check("scoped_model column present", "scoped_model" in cols)
        rows = conn.execute("SELECT scoped_pct, scoped_model FROM usage_runs "
                            "ORDER BY id").fetchall()
        check("non-null pct preserved", rows[0][0] == 12.5)
        check("non-null row backfilled 'Sonnet'", rows[0][1] == "Sonnet")
        check("null-pct row left unlabelled", rows[1][1] is None)

    # Idempotent: a second init() must not error or relabel.
    UsageDB(path).init()
    with sqlite3.connect(path) as conn:
        labels = [r[0] for r in conn.execute("SELECT scoped_model FROM usage_runs "
                                              "ORDER BY id").fetchall()]
        check("idempotent re-init keeps labels", labels == ["Sonnet", None])

print()
print("pre-sonnet_pct table gets scoped_pct and scoped_model:")

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "usage.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(PRE_SONNET_SCHEMA)
        conn.execute(
            "INSERT INTO usage_runs (ts_start, ts_end, sample_count) VALUES ('t0','t0',1)"
        )
        conn.commit()

    UsageDB(path).init()

    with sqlite3.connect(path) as conn:
        cols = columns(conn)
        check("scoped_pct column added", "scoped_pct" in cols)
        check("scoped_model column added", "scoped_model" in cols)

print()
if failures:
    print(f"scoped_migration_check: {len(failures)} FAILED")
    sys.exit(1)
print("scoped_migration_check: all passed")
