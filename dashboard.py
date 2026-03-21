from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event

from auth import AuthTimeoutError, interactive_login_and_save_config
from config import DB_PATH, LOG_PATH, ensure_runtime_paths, has_api_auth, load_config
from db import UsageDB
from scraper_api import AuthRequiredError, UsageAPIClient
from web import start_dashboard_server


def setup_logger() -> logging.Logger:
    ensure_runtime_paths()
    logger = logging.getLogger("claude_usage_tracker")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    fallback_note = None
    try:
        file_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=5)
    except PermissionError:
        fallback_log = Path.cwd() / "dashboard.log"
        file_handler = RotatingFileHandler(fallback_log, maxBytes=2_000_000, backupCount=5)
        fallback_note = f"Cannot write to {LOG_PATH}; falling back to {fallback_log}"
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    if fallback_note:
        logger.warning(fallback_note)
    return logger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_authenticated_config(logger: logging.Logger) -> None:
    config = load_config()
    if has_api_auth(config):
        return
    interactive_login_and_save_config(logger, reason="missing local auth config")


def main() -> None:
    logger = setup_logger()
    logger.info("Starting Claude usage tracker")
    stop_event = Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.warning("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_signal)

    ensure_authenticated_config(logger)

    config = load_config()
    db = UsageDB(DB_PATH)
    db.init()

    server = start_dashboard_server(
        host=config.host,
        port=config.port,
        db=db,
        logger=logger,
        poll_interval_seconds=config.poll_interval_seconds,
        expected_weekly_line_enabled=config.expected_weekly_line_enabled,
        expected_active_start_hhmm=config.expected_active_start_hhmm,
        expected_active_end_hhmm=config.expected_active_end_hhmm,
        notify_session_threshold_pct=config.notify_session_threshold_pct,
        notify_weekly_threshold_pct=config.notify_weekly_threshold_pct,
        notify_extra_threshold_pct=config.notify_extra_threshold_pct,
        notify_expected_weekly_overrun_enabled=config.notify_expected_weekly_overrun_enabled,
    )

    try:
        while not stop_event.is_set():
            config = load_config()
            client = UsageAPIClient(config)
            ts = utc_now_iso()
            try:
                sample = client.fetch_sample(ts)
                db.insert_sample(sample)
                logger.info(
                    "Sample written: session=%.2f weekly=%.2f extra=%s",
                    sample.session_pct if sample.session_pct is not None else -1.0,
                    sample.weekly_pct if sample.weekly_pct is not None else -1.0,
                    f"{sample.extra_pct:.2f}" if sample.extra_pct is not None else "null",
                )
            except AuthRequiredError as exc:
                logger.warning("Auth expired/invalid: %s", exc)
                try:
                    interactive_login_and_save_config(logger, reason="session expired")
                except AuthTimeoutError as auth_exc:
                    logger.error("Re-authentication timed out: %s", auth_exc)
            except Exception:
                logger.exception("Unexpected poll failure")

            stop_event.wait(config.poll_interval_seconds)
    finally:
        logger.info("Stopping tracker")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
