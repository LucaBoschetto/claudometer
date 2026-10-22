from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from config import BURN_WINDOW_MINUTES
from models import UsageSample

# Interior points emitted across the burn window of a long run. The burn-rate
# series decays over BURN_WINDOW_MINUTES after the step into a run; without
# these, a multi-day idle run draws that decay as a multi-day ramp. Four caps
# every in-window gap at 7.5 minutes, which is ~0.04px at range=all.
COLLAPSE_DECAY_POINTS = 4

_BURN_WINDOW = timedelta(minutes=BURN_WINDOW_MINUTES)


LEGACY_USAGE_LOG_SCHEMA_SQL = """
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
  extra_monthly_limit REAL,
  scoped_pct          REAL,
  scoped_model        TEXT
);
"""

# Columns added after initial schema; applied via ALTER TABLE on existing DBs.
USAGE_RUNS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("scoped_pct", "REAL"),
    ("scoped_model", "TEXT"),
)

USAGE_RUNS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_start ON usage_runs(ts_start);
CREATE INDEX IF NOT EXISTS idx_usage_runs_ts_end ON usage_runs(ts_end);
"""

LEGACY_USAGE_LOG_EXPECTED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("extra_enabled", "INTEGER"),
    ("extra_used_credits", "REAL"),
    ("extra_monthly_limit", "REAL"),
)


def _normalize_range(range_preset: str) -> str:
    """Fold unknown presets onto "all".

    Both the SQL window and the expand/collapse choice must agree on this, or a
    bogus range gets the all-window with per-sample expansion.
    """
    return range_preset if range_preset in {"today", "weekly_cycle", "all"} else "all"


class UsageDB:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(USAGE_RUNS_SCHEMA_SQL)
            conn.executescript(USAGE_RUNS_INDEX_SQL)
            self._migrate_usage_runs_if_needed(conn)
            self._import_legacy_usage_log_if_needed(conn)
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
                  extra_monthly_limit,
                  scoped_pct,
                  scoped_model
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
                      extra_monthly_limit,
                      scoped_pct,
                      scoped_model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        normalized.scoped_pct,
                        normalized.scoped_model,
                    ),
                )
            conn.commit()

    def fetch_last_sample(self) -> dict[str, Any] | None:
        """Return the most recent usage run as a dict, or None if empty."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT
                  ts_end,
                  session_pct,
                  session_resets,
                  weekly_pct,
                  weekly_resets,
                  extra_pct,
                  extra_enabled,
                  extra_used_credits,
                  extra_monthly_limit,
                  scoped_pct,
                  scoped_model
                FROM usage_runs
                ORDER BY ts_end DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            if result.get("extra_enabled") is not None:
                result["extra_enabled"] = bool(result["extra_enabled"])
            return result

    def fetch_chart_data(self, range_preset: str = "all") -> dict[str, Any]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            total_samples = int(
                conn.execute("SELECT COALESCE(SUM(sample_count), 0) FROM usage_runs").fetchone()[0]
            )
            start_iso, end_iso = self._range_window(conn, range_preset)
            if start_iso is None and end_iso is None:
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
                      extra_monthly_limit,
                      scoped_pct,
                      scoped_model
                    FROM usage_runs
                    ORDER BY ts_start ASC, id ASC
                    """
                ).fetchall()
            else:
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
                      extra_monthly_limit,
                      scoped_pct,
                      scoped_model
                    FROM usage_runs
                    WHERE ts_end >= ? AND ts_start < ?
                    ORDER BY ts_start ASC, id ASC
                    """,
                    (start_iso, end_iso),
                ).fetchall()

        payload_runs = [self._run_row_to_dict(row) for row in runs]
        # range=all spans the whole history, where per-sample expansion is ~100k
        # collinear points and ~29 MB of JSON. The zoomed presets are small and
        # their fidelity is visible, so they keep the full expansion.
        to_rows = (
            self._collapse_run
            if _normalize_range(range_preset) == "all"
            else self._expand_run
        )
        payload_rows: list[dict[str, Any]] = []
        filtered_samples = 0
        for run in payload_runs:
            filtered_samples += int(run["sample_count"])
            payload_rows.extend(to_rows(run))
        return {
            "rows": payload_rows,
            "total_samples": total_samples,
            "filtered_samples": filtered_samples,
            "run_count": len(payload_runs),
        }

    def _range_window(
        self, conn: sqlite3.Connection, range_preset: str
    ) -> tuple[str | None, str | None]:
        normalized = _normalize_range(range_preset)
        if normalized == "all":
            return (None, None)

        latest = conn.execute(
            """
            SELECT ts_end, weekly_resets
            FROM usage_runs
            ORDER BY ts_end DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            return (None, None)

        latest_ts = _parse_iso(latest[0])
        if latest_ts is None:
            return (None, None)

        if normalized == "today":
            local_latest = latest_ts.astimezone()
            local_start = local_latest.replace(hour=0, minute=0, second=0, microsecond=0)
            local_end = local_start + timedelta(days=1)
            return (
                local_start.astimezone(timezone.utc).isoformat(),
                local_end.astimezone(timezone.utc).isoformat(),
            )

        current_cycle_end = _parse_iso(latest[1])
        if current_cycle_end is None:
            return (None, None)
        # Use the earliest ts_start that shares the same weekly_resets as the
        # latest row. This correctly handles mid-cycle plan switches, where the
        # new cycle begins before the previous cycle's weekly_resets date — using
        # the previous weekly_resets as cycle_start would silently drop the early
        # data from the new cycle.
        first_in_cycle = conn.execute(
            """
            SELECT ts_start
            FROM usage_runs
            WHERE weekly_resets = ?
            ORDER BY ts_start ASC, id ASC
            LIMIT 1
            """,
            (current_cycle_end.isoformat(),),
        ).fetchone()
        if first_in_cycle:
            cycle_start = _parse_iso(first_in_cycle[0]) or (current_cycle_end - timedelta(days=7))
        else:
            cycle_start = current_cycle_end - timedelta(days=7)
        return (cycle_start.isoformat(), current_cycle_end.isoformat())

    def _migrate_usage_runs_if_needed(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_runs)").fetchall()
        }
        # The model-scoped column used to hold Sonnet; it now holds whatever model
        # the API scopes the weekly cap to (Fable at time of writing). Rename in
        # place so the months of Sonnet history stay as one continuous series.
        needs_backfill = False
        if "sonnet_pct" in existing_columns and "scoped_pct" not in existing_columns:
            conn.execute("ALTER TABLE usage_runs RENAME COLUMN sonnet_pct TO scoped_pct")
            existing_columns.discard("sonnet_pct")
            existing_columns.add("scoped_pct")
            needs_backfill = True
        for column_name, column_type in USAGE_RUNS_NEW_COLUMNS:
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE usage_runs ADD COLUMN {column_name} {column_type}")
                existing_columns.add(column_name)
        if needs_backfill:
            # Every pre-rename non-null row was Sonnet. Guard on NULL so a re-run
            # or any already-labelled row is never relabelled.
            conn.execute(
                "UPDATE usage_runs SET scoped_model = 'Sonnet' "
                "WHERE scoped_pct IS NOT NULL AND scoped_model IS NULL"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # sqlite3.Connection's own context manager only commits/rolls back —
        # it does NOT close the connection, leaking one FD (plus a WAL FD)
        # per call until cyclic GC eventually fires. In a threaded HTTP
        # server this exhausts the process's file descriptor limit.
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _import_legacy_usage_log_if_needed(self, conn: sqlite3.Connection) -> None:
        has_legacy = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='usage_log'"
        ).fetchone()[0]
        if not has_legacy:
            return

        self._migrate_legacy_usage_log(conn)

        run_count = conn.execute("SELECT COUNT(*) FROM usage_runs").fetchone()[0]
        if run_count:
            self._drop_legacy_usage_log_if_safe(conn)
            return

        conn.row_factory = sqlite3.Row
        legacy_rows = conn.execute(
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
        if not legacy_rows:
            return

        runs: list[tuple[Any, ...]] = []
        current_run: dict[str, Any] | None = None

        for row in legacy_rows:
            sample = self._normalize_sample(self._sample_from_legacy_row(row))
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
              extra_monthly_limit,
              scoped_pct,
              scoped_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            runs,
        )
        self._drop_legacy_usage_log_if_safe(conn)

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
            and sample.scoped_pct == run["scoped_pct"]
            and sample.scoped_model == run["scoped_model"]
        )

    def _sample_from_legacy_row(self, row: sqlite3.Row) -> UsageSample:
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
            run.get("scoped_pct"),
            run.get("scoped_model"),
        )

    def _migrate_legacy_usage_log(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_log)").fetchall()
        }
        for column_name, column_type in LEGACY_USAGE_LOG_EXPECTED_COLUMNS:
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE usage_log ADD COLUMN {column_name} {column_type}")

    def _drop_legacy_usage_log_if_safe(self, conn: sqlite3.Connection) -> None:
        has_legacy = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='usage_log'"
        ).fetchone()[0]
        if not has_legacy:
            return

        legacy_count = conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
        run_summary = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(sample_count), 0) FROM usage_runs"
        ).fetchone()
        run_count = int(run_summary[0])
        represented_samples = int(run_summary[1])

        # Drop only when the compacted table is clearly populated and represents
        # at least as many samples as the legacy source table.
        if legacy_count <= 0 or run_count <= 0 or represented_samples < legacy_count:
            return

        conn.execute("DROP TABLE usage_log")

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

    def _collapse_run(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit the fewest points that redraw this run.

        usage_runs is run-length encoded: every sample in a run carries the same
        values, so the run draws as a flat segment and _expand_run's interior
        points are collinear duplicates sitting on invented evenly-spaced
        timestamps. Only the endpoints carry shape, and _expand_run pins its
        first point at ts_start and its last at ts_end, so emitting those two
        reproduces the identical polyline in the 'raw' and 'clean' view modes.

        'smooth' is the exception: seriesFor's smoothMoving averages by array
        index, not by time, so collapsing a run changes index spacing and is
        not invariant there. For long runs, the mirrored point before ts_end
        (below) stops the error at the run's end from spanning the whole run,
        and measurably helps (21,792 -> 18,115 differing pixels at range=all).

        It does NOT make 'smooth' pixel-identical: ~18,115 of 549,150 pixels
        (3.3%) still differ, and that residual's cause is UNKNOWN. Do not
        assume it is short runs keeping only two endpoints -- that was measured
        and rejected: mirroring every run regardless of duration only reached
        17,595. Density-driven antialiasing was also rejected, since 'raw' is
        byte-identical across the same point-count change. Diagnose before
        changing anything here. See the spec's "Known trade-offs and follow-up".

        The burn-rate series is also *not* constant within a run: it decays
        across the burn window after the step into the run, then sits at zero.
        Endpoints alone would draw that decay as a ramp spanning the whole run,
        so long runs also get interior points across the window.

        Only used for range=all; the zoomed presets keep _expand_run.
        """
        count = max(1, int(run["sample_count"]))
        start_dt = _parse_iso(run["ts_start"])
        end_dt = _parse_iso(run["ts_end"])
        if count == 1 or start_dt is None or end_dt is None or end_dt <= start_dt:
            # No span to draw. _expand_run emits `count` duplicates at the same
            # instant here, which is visually this same single point.
            return [self._expanded_row(run, run["ts_start"])]

        step = _BURN_WINDOW / COLLAPSE_DECAY_POINTS
        points = [start_dt]
        if (end_dt - start_dt) > _BURN_WINDOW:
            for index in range(1, COLLAPSE_DECAY_POINTS + 1):
                points.append(start_dt + step * index)
            # seriesFor's 'smooth' view averages by index, not by time, so a run's
            # last point is pulled toward the next run's value. Without a point just
            # before ts_end, that pull is drawn as a ramp across the whole run.
            if end_dt - step > points[-1]:
                points.append(end_dt - step)
        points.append(end_dt)
        return [
            self._expanded_row(run, point.astimezone(timezone.utc).isoformat())
            for point in points
        ]

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
            "scoped_pct": run.get("scoped_pct"),
            "scoped_model": run.get("scoped_model"),
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
