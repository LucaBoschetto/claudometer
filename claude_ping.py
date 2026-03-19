#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DB_PATH, RUNTIME_DIR, load_config

PING_MESSAGE = (
    "Hi! This is an automated ping from Claudometer to anchor the weekly usage window. "
    "No reply needed. 🐟"
)
CRON_MARKER = "# CLAUDOMETER_WEEKLY_PING"
PING_LOG_PATH = RUNTIME_DIR / "claude_ping.log"


def _ensure_project_python() -> None:
    current = Path(sys.executable).resolve()
    project_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if not project_python.exists():
        return
    if current == project_python.resolve():
        return
    os.execv(str(project_python), [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_project_python()


def setup_logger() -> logging.Logger:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("claude_ping")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(PING_LOG_PATH)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def main() -> int:
    logger = setup_logger()
    try:
        config = load_config()
        current_weekly_reset = read_current_weekly_reset()
        if current_weekly_reset is None:
            raise RuntimeError("Could not determine current weekly reset time from usage_runs")
        logger.info(
            "Current weekly reset from DB: %s local (%s UTC)",
            current_weekly_reset.astimezone().isoformat(),
            current_weekly_reset.isoformat(),
        )

        send_ping(logger)

        next_weekly_reset = wait_for_confirmed_next_weekly_reset(
            current_weekly_reset=current_weekly_reset,
            poll_interval_seconds=config.poll_interval_seconds,
            logger=logger,
        )
        if next_weekly_reset is None:
            if has_managed_cron_entry():
                logger.info(
                    "Weekly reset is still in the future, so this looks like a pre-reset test run. "
                    "Leaving the existing cron entry unchanged."
                )
                return 0

            run_at = current_weekly_reset + timedelta(minutes=1)
            logger.info(
                "Weekly reset is still in the future, so this looks like a pre-reset test run. "
                "Seeding the initial cron entry for %s local (%s UTC)",
                run_at.astimezone().isoformat(),
                run_at.isoformat(),
            )
            install_or_update_cron(run_at, logger)
            return 0

        install_or_update_cron(next_weekly_reset + timedelta(minutes=1), logger)
        logger.info("Ping completed successfully")
        return 0
    except Exception:
        logger.exception("claude_ping failed")
        return 1


def send_ping(logger: logging.Logger) -> None:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("Could not find 'claude' on PATH")

    project_dir = Path(__file__).resolve().parent
    command = [
        claude_bin,
        "-p",
        "--tools",
        "",
        "--no-session-persistence",
        PING_MESSAGE,
    ]
    logger.info("Sending weekly anchor ping through Claude Code CLI")
    result = subprocess.run(
        command,
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            detail = detail[:1000]
        raise RuntimeError(f"Claude Code ping failed: {detail or f'exit code {result.returncode}'}")

    output = (result.stdout or "").strip()
    if output:
        logger.info("Claude Code ping completed: %s", first_line(output))
    else:
        logger.info("Claude Code ping completed with no textual output")


def wait_for_confirmed_next_weekly_reset(
    *,
    current_weekly_reset: datetime,
    poll_interval_seconds: int,
    logger: logging.Logger,
) -> datetime | None:
    now = datetime.now(timezone.utc)
    if now + timedelta(seconds=poll_interval_seconds * 2) < current_weekly_reset:
        return None

    deadline = now + timedelta(seconds=max(15 * 60, poll_interval_seconds * 10))
    while datetime.now(timezone.utc) <= deadline:
        observed_reset = read_current_weekly_reset()
        if observed_reset and observed_reset > current_weekly_reset:
            logger.info(
                "Observed next weekly reset in DB: %s local (%s UTC)",
                observed_reset.astimezone().isoformat(),
                observed_reset.isoformat(),
            )
            return observed_reset

        logger.info(
            "Weekly reset has not advanced in DB yet; rechecking in %ss",
            poll_interval_seconds,
        )
        time.sleep(poll_interval_seconds)

    raise RuntimeError(
        "Claude Code ping succeeded, but the next weekly reset was not observed in the DB "
        f"within {max(15 * 60, poll_interval_seconds * 10)} seconds"
    )


def read_current_weekly_reset() -> datetime | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT weekly_resets
            FROM usage_runs
            WHERE weekly_resets IS NOT NULL
            ORDER BY ts_end DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row or not row[0]:
        return None
    return parse_iso(row[0])


def install_or_update_cron(run_at: datetime, logger: logging.Logger) -> None:
    cron_command = build_cron_command()
    cron_line = cron_line_for(run_at, cron_command)
    existing_lines = read_crontab_lines()

    filtered_lines = [
        line for line in existing_lines if CRON_MARKER not in line and "claude_ping.py" not in line
    ]
    filtered_lines.append(cron_line)

    payload = "\n".join(filtered_lines).strip()
    if payload:
        payload += "\n"

    subprocess.run(["crontab", "-"], input=payload, text=True, check=True)
    logger.info(
        "Installed/updated cron entry for %s local (%s UTC)",
        run_at.astimezone().isoformat(),
        run_at.isoformat(),
    )


def build_cron_command() -> str:
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / ".venv" / "bin" / "python"
    python_bin = venv_python if venv_python.exists() else Path(sys.executable)
    script_path = Path(__file__).resolve()
    return (
        f"cd {shell_quote(str(project_dir))} && "
        f"{shell_quote(str(python_bin))} {shell_quote(str(script_path))} "
        f">> {shell_quote(str(PING_LOG_PATH))} 2>&1 {CRON_MARKER}"
    )


def cron_line_for(run_at: datetime, command: str) -> str:
    local_time = run_at.astimezone()
    return f"{local_time.minute} {local_time.hour} {local_time.day} {local_time.month} * {command}"


def read_crontab_lines() -> list[str]:
    result = subprocess.run(
        ["crontab", "-l"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "no crontab for" in result.stderr.lower():
        return []
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not read crontab")
    return [line for line in result.stdout.splitlines() if line.strip()]


def has_managed_cron_entry() -> bool:
    return any(CRON_MARKER in line or "claude_ping.py" in line for line in read_crontab_lines())


def parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def first_line(text: str) -> str:
    return text.splitlines()[0][:400]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
