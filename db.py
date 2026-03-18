from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from models import UsageSample


USAGE_LOG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_log (
  ts                 TEXT NOT NULL,
  session_pct        REAL,
  session_resets     TEXT,
  weekly_pct         REAL,
  weekly_resets      TEXT,
  extra_pct          REAL,
  extra_enabled      INTEGER,
  extra_used_credits REAL,
  extra_monthly_limit REAL
);
"""

USAGE_LOG_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_usage_log_ts ON usage_log(ts);
"""

USAGE_RUNS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_runs (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start           TEXT NOT NULL,
  ts_end             TEXT NOT NULL,
  sample_count       INTEGER NOT NULL,
  session_pct        REAL,
  session_resets     TEXT,
  weekly_pct         REAL,
  weekly_resets      TEXT,
  extra_pct          REAL,
  extra_enabled      INTEGER,
  extra_used_credits REAL,
  extra_monthly_limit REAL
);
"""

USAGE_RUNS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_start ON usage_runs(ts_start);
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_end ON usage_runs(ts_end);
"""

EXPECTED_USAGE_LOG_COLUMNS: tuple[tuple[str, str], ...] = (
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
            conn.execute(USAGE_LOG_SCHEMA_SQL)
            conn.execute(USAGE_LOG_INDEX_SQL)
            self._migrate_usage_log(conn)
            self._backfill_extra_usage(conn)
            conn.executescript(USAGE_RUNS_SCHEMA_SQL)
            conn.executescript(USAGE_RUNS_INDEX_SQL)
            self._backfill_usage_runs(conn)
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

    def _migrate_usage_log(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_log)").fetchall()
        }
        for column_name, column_type in EXPECTED_USAGE_LOG_COLUMNS:
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

    def _backfill_usage_runs(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute("SELECT COUNT(*) FROM usage_runs").fetchone()[0]
        if existing:
            return

        conn.row_factory = sqlite3.Row
        raw_rows = conn.execute(
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
        if not raw_rows:
            return

        runs: list[tuple[Any, ...]] = []
        current_run: dict[str, Any] | None = None

        for row in raw_rows:
            sample = self._normalize_sample(self._sample_from_row(row))
            if current_run is not None and self._sample_matches_run(sample, current_run):
                current_run["ts_end"] = sample.ts
                current_run["sample_count"] += 1
                continue

            if current_run is not None:
                runs.append(self._run_insert_tuple(current_run))

            current_run = {
                "ts_start": sample.ts,
                "ts_end": sample.ts,
                "sample_count": 1,
                "session_pct": sample.session_pct,
                "session_resets": sample.session_resets,
                "weekly_pct": sample.weekly_pct,
                "weekly_resets": sample.weekly_resets,
                "extra_pct": sample.extra_pct,
                "extra_enabled": None if sample.extra_enabled is None else int(sample.extra_enabled),
                "extra_used_credits": sample.extra_used_credits,
                "extra_monthly_limit": sample.extra_monthly_limit,
            }

        if current_run is not None:
            runs.append(self._run_insert_tuple(current_run))

        conn.executemany(
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
            runs,
        )

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

    def _sample_from_row(self, row: sqlite3.Row) -> UsageSample:
        extra_enabled = row["extra_enabled"]
        return UsageSample(
            ts=row["ts"],
            session_pct=row["session_pct"],
            session_resets=row["session_resets"],
            weekly_pct=row["weekly_pct"],
            weekly_resets=row["weekly_resets"],
            extra_pct=row["extra_pct"],
            extra_enabled=None if extra_enabled is None else bool(extra_enabled),
            extra_used_credits=row["extra_used_credits"],
            extra_monthly_limit=row["extra_monthly_limit"],
        )

    def _run_insert_tuple(self, run: dict[str, Any]) -> tuple[Any, ...]:
        return (
            run["ts_start"],
            run["ts_end"],
            run["sample_count"],
            run["session_pct"],
            run["session_resets"],
            run["weekly_pct"],
            run["weekly_resets"],
            run["extra_pct"],
            run["extra_enabled"],
            run["extra_used_credits"],
            run["extra_monthly_limit"],
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
