from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from models import UsageSample


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_log (
  ts              TEXT NOT NULL,
  session_pct     REAL,
  session_resets  TEXT,
  weekly_pct      REAL,
  weekly_resets   TEXT,
  extra_pct       REAL,
  extra_enabled   INTEGER,
  extra_used_credits REAL,
  extra_monthly_limit REAL
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts);
"""

EXPECTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("extra_enabled", "INTEGER"),
    ("extra_used_credits", "REAL"),
    ("extra_monthly_limit", "REAL"),
)

BACKFILL_COLUMNS: tuple[str, ...] = (
    "extra_pct",
    "extra_enabled",
    "extra_used_credits",
    "extra_monthly_limit",
)


class UsageDB:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(SCHEMA_SQL)
            conn.execute(INDEX_SQL)
            self._migrate(conn)
            self._backfill_extra_usage(conn)
            conn.commit()

    def insert_sample(self, sample: UsageSample) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_log (
                  ts, session_pct, session_resets, weekly_pct, weekly_resets, extra_pct,
                  extra_enabled, extra_used_credits, extra_monthly_limit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.ts,
                    sample.session_pct,
                    sample.session_resets,
                    sample.weekly_pct,
                    sample.weekly_resets,
                    sample.extra_pct,
                    None if sample.extra_enabled is None else int(sample.extra_enabled),
                    sample.extra_used_credits,
                    sample.extra_monthly_limit,
                ),
            )
            conn.commit()

    def fetch_all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                  ts,
                  session_pct,
                  session_resets,
                  weekly_pct,
                  weekly_resets,
                  extra_pct,
                  extra_enabled,
                  extra_used_credits,
                  extra_monthly_limit
                FROM usage_log
                ORDER BY ts ASC
                """
            ).fetchall()
            payload = [dict(row) for row in rows]
            for row in payload:
                if row.get("extra_enabled") is not None:
                    row["extra_enabled"] = bool(row["extra_enabled"])
            return payload

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_log)").fetchall()
        }
        for column_name, column_type in EXPECTED_COLUMNS:
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE usage_log ADD COLUMN {column_name} {column_type}")

    def _backfill_extra_usage(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT rowid, ts, extra_pct, extra_enabled, extra_used_credits, extra_monthly_limit
            FROM usage_log
            ORDER BY ts ASC
            """
        ).fetchall()

        carried: dict[str, Any] = {column_name: None for column_name in BACKFILL_COLUMNS}
        updates: list[tuple[Any, ...]] = []

        for row in rows:
            next_values: dict[str, Any] = {}
            changed = False
            original_extra_pct = row["extra_pct"]
            for column_name in BACKFILL_COLUMNS:
                current_value = row[column_name]
                if current_value is not None:
                    carried[column_name] = current_value
                    next_values[column_name] = current_value
                    continue
                next_values[column_name] = carried[column_name]
                if carried[column_name] is not None:
                    changed = True

            # Historical rows with missing extra_pct came from the disabled-extra-usage
            # API shape. If we carry forward a prior percentage into such a row, we can
            # safely mark it disabled so the UI renders the inferred state distinctly.
            if original_extra_pct is None and next_values["extra_pct"] is not None:
                next_values["extra_enabled"] = 0
                changed = True

            if not changed:
                continue

            updates.append(
                (
                    next_values["extra_pct"],
                    next_values["extra_enabled"],
                    next_values["extra_used_credits"],
                    next_values["extra_monthly_limit"],
                    row["rowid"],
                )
            )

        if updates:
            conn.executemany(
                """
                UPDATE usage_log
                SET
                  extra_pct = ?,
                  extra_enabled = ?,
                  extra_used_credits = ?,
                  extra_monthly_limit = ?
                WHERE rowid = ?
                """,
                updates,
            )
