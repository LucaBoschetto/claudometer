from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from models import UsageSample


USAGE_RUNS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_runs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start            TEXT NOT NULL,
  ts_end              TEXT NOT NULL,
  sample_count        INTEGER NOT NULL,
  session_pct         REAL,
  session_resets      TEXT,
  weekly_pct          REAL,
  weekly_resets       TEXT,
  extra_pct           REAL,
  extra_enabled       INTEGER,
  extra_used_credits  REAL,
  extra_monthly_limit REAL
);
"""

USAGE_RUNS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_start ON usage_runs(ts_start);
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_end ON usage_runs(ts_end);
"""


class UsageDB:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(USAGE_RUNS_SCHEMA_SQL)
            conn.executescript(USAGE_RUNS_INDEX_SQL)
            conn.commit()

    def insert_sample(self, sample: UsageSample) -> None:
        normalized = self._normalize_sample(sample)
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            latest = conn.execute(
                """
                SELECT
                  id,
                  ts_start,
                  ts_end,
                  sample_count,
                  session_pct,
                  session_resets,
                  weekly_pct,
                  weekly_resets,
                  extra_pct,
                  extra_enabled,
                  extra_used_credits,
                  extra_monthly_limit
                FROM usage_runs
                ORDER BY ts_end DESC, id DESC
                LIMIT 1
                """
            ).fetchone()

            if latest is not None and self._sample_matches_run(normalized, latest):
                conn.execute(
                    """
                    UPDATE usage_runs
                    SET ts_end = ?, sample_count = sample_count + 1
                    WHERE id = ?
                    """,
                    (normalized.ts, latest["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO usage_runs (
                      ts_start,
                      ts_end,
                      sample_count,
                      session_pct,
                      session_resets,
                      weekly_pct,
                      weekly_resets,
                      extra_pct,
                      extra_enabled,
                      extra_used_credits,
                      extra_monthly_limit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized.ts,
                        normalized.ts,
                        1,
                        normalized.session_pct,
                        normalized.session_resets,
                        normalized.weekly_pct,
                        normalized.weekly_resets,
                        normalized.extra_pct,
                        None if normalized.extra_enabled is None else int(normalized.extra_enabled),
                        normalized.extra_used_credits,
                        normalized.extra_monthly_limit,
                    ),
                )
            conn.commit()

    def fetch_chart_data(self) -> dict[str, Any]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            runs = conn.execute(
                """
                SELECT
                  ts_start,
                  ts_end,
                  sample_count,
                  session_pct,
                  session_resets,
                  weekly_pct,
                  weekly_resets,
                  extra_pct,
                  extra_enabled,
                  extra_used_credits,
                  extra_monthly_limit
                FROM usage_runs
                ORDER BY ts_start ASC, id ASC
                """
            ).fetchall()

        payload_runs = [self._run_row_to_dict(row) for row in runs]
        expanded_rows: list[dict[str, Any]] = []
        total_samples = 0
        for run in payload_runs:
            total_samples += int(run["sample_count"])
            expanded_rows.extend(self._expand_run(run))
        return {
            "rows": expanded_rows,
            "total_samples": total_samples,
            "run_count": len(payload_runs),
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _normalize_sample(self, sample: UsageSample) -> UsageSample:
        return replace(
            sample,
            session_resets=_round_iso_to_nearest_minute(sample.session_resets),
            weekly_resets=_round_iso_to_nearest_minute(sample.weekly_resets),
        )

    def _sample_matches_run(self, sample: UsageSample, run: sqlite3.Row | dict[str, Any]) -> bool:
        return (
            sample.session_pct == run["session_pct"]
            and sample.session_resets == run["session_resets"]
            and sample.weekly_pct == run["weekly_pct"]
            and sample.weekly_resets == run["weekly_resets"]
            and sample.extra_pct == run["extra_pct"]
            and _boolish(sample.extra_enabled) == _boolish(run["extra_enabled"])
            and sample.extra_used_credits == run["extra_used_credits"]
            and sample.extra_monthly_limit == run["extra_monthly_limit"]
        )

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        if payload.get("extra_enabled") is not None:
            payload["extra_enabled"] = bool(payload["extra_enabled"])
        return payload

    def _expand_run(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        count = max(1, int(run["sample_count"]))
        if count == 1:
            return [self._expanded_row(run, run["ts_start"])]

        start_dt = _parse_iso(run["ts_start"])
        end_dt = _parse_iso(run["ts_end"])
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            return [self._expanded_row(run, run["ts_start"]) for _ in range(count)]

        span_seconds = (end_dt - start_dt).total_seconds()
        step_seconds = span_seconds / max(1, count - 1)
        rows: list[dict[str, Any]] = []
        for index in range(count):
            point_dt = start_dt + timedelta(seconds=step_seconds * index)
            if index == count - 1:
                point_dt = end_dt
            rows.append(self._expanded_row(run, point_dt.astimezone(timezone.utc).isoformat()))
        return rows

    def _expanded_row(self, run: dict[str, Any], ts: str) -> dict[str, Any]:
        return {
            "ts": ts,
            "session_pct": run["session_pct"],
            "session_resets": run["session_resets"],
            "weekly_pct": run["weekly_pct"],
            "weekly_resets": run["weekly_resets"],
            "extra_pct": run["extra_pct"],
            "extra_enabled": run["extra_enabled"],
            "extra_used_credits": run["extra_used_credits"],
            "extra_monthly_limit": run["extra_monthly_limit"],
        }


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _round_iso_to_nearest_minute(raw: str | None) -> str | None:
    parsed = _parse_iso(raw)
    if parsed is None:
        return raw
    rounded = parsed.astimezone(timezone.utc) + timedelta(seconds=30)
    rounded = rounded.replace(second=0, microsecond=0)
    return rounded.isoformat()


def _boolish(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
