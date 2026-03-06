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
  extra_pct       REAL
);
"""


class UsageDB:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(SCHEMA_SQL)
            conn.commit()

    def insert_sample(self, sample: UsageSample) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_log (
                  ts, session_pct, session_resets, weekly_pct, weekly_resets, extra_pct
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.ts,
                    sample.session_pct,
                    sample.session_resets,
                    sample.weekly_pct,
                    sample.weekly_resets,
                    sample.extra_pct,
                ),
            )
            conn.commit()

    def fetch_all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT ts, session_pct, session_resets, weekly_pct, weekly_resets, extra_pct
                FROM usage_log
                ORDER BY ts ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
