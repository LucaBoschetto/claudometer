from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

RUNTIME_DIR = Path.home() / ".claude-usage-tracker"
RUNTIME_ENV_PATH = RUNTIME_DIR / "config.env"
DB_PATH = RUNTIME_DIR / "usage.db"
LOG_PATH = RUNTIME_DIR / "dashboard.log"
BROWSER_PROFILE_DIR = RUNTIME_DIR / "browser-profile"


@dataclass
class AppConfig:
    poll_interval_seconds: int
    host: str
    port: int
    auth_browser: str
    claude_org_id: Optional[str]
    claude_cookie_header: Optional[str]
    anthropic_anonymous_id: Optional[str]
    anthropic_device_id: Optional[str]
    user_agent: str
    expected_weekly_line_enabled: bool
    expected_active_start_hhmm: str
    expected_active_end_hhmm: str


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def ensure_runtime_paths() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _as_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_hhmm(raw: str | None, default: str) -> str:
    if not raw:
        return default
    value = raw.strip()
    if len(value) != 5 or value[2] != ":":
        return default
    hh, mm = value.split(":", 1)
    if not (hh.isdigit() and mm.isdigit()):
        return default
    h = int(hh)
    m = int(mm)
    if h < 0 or h > 23 or m < 0 or m > 59:
        return default
    return f"{h:02d}:{m:02d}"


def _normalize_auth_browser(raw: str | None, default: str) -> str:
    if not raw:
        return default
    value = raw.strip().lower()
    if value in {"chrome", "chromium", "firefox", "webkit"}:
        return value
    return default


def load_config() -> AppConfig:
    ensure_runtime_paths()

    file_values = dotenv_values(RUNTIME_ENV_PATH)

    def get(key: str, default: str | None = None) -> str | None:
        env_override = os.getenv(key)
        if env_override is not None:
            return env_override
        file_value = file_values.get(key)
        if file_value is None:
            return default
        return str(file_value)

    return AppConfig(
        poll_interval_seconds=max(5, _as_int(get("POLL_INTERVAL_SECONDS"), 60)),
        host=get("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1",
        port=_as_int(get("DASHBOARD_PORT"), 7474),
        auth_browser=_normalize_auth_browser(get("AUTH_BROWSER"), "chrome"),
        claude_org_id=get("CLAUDE_ORG_ID"),
        claude_cookie_header=get("CLAUDE_COOKIE_HEADER"),
        anthropic_anonymous_id=get("ANTHROPIC_ANONYMOUS_ID"),
        anthropic_device_id=get("ANTHROPIC_DEVICE_ID"),
        user_agent=get("USER_AGENT", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT,
        expected_weekly_line_enabled=_as_bool(get("EXPECTED_WEEKLY_LINE_ENABLED"), True),
        expected_active_start_hhmm=_normalize_hhmm(get("EXPECTED_ACTIVE_START_HHMM"), "08:00"),
        expected_active_end_hhmm=_normalize_hhmm(get("EXPECTED_ACTIVE_END_HHMM"), "19:00"),
    )


def has_api_auth(config: AppConfig) -> bool:
    return bool(config.claude_org_id and config.claude_cookie_header)
