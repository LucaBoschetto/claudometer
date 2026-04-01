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

RUNTIME_ENV_ORDER = (
    "CLAUDE_ORG_ID",
    "CLAUDE_COOKIE_HEADER",
    "ANTHROPIC_DEVICE_ID",
    "ANTHROPIC_ANONYMOUS_ID",
    "POLL_INTERVAL_SECONDS",
    "DASHBOARD_HOST",
    "DASHBOARD_PORT",
    "AUTH_BROWSER",
    "EXPECTED_WEEKLY_LINE_ENABLED",
    "EXPECTED_ACTIVE_START_HHMM",
    "EXPECTED_ACTIVE_END_HHMM",
    "NOTIFY_SESSION_THRESHOLD_PCT",
    "NOTIFY_WEEKLY_THRESHOLD_PCT",
    "NOTIFY_EXTRA_THRESHOLD_PCT",
    "NOTIFY_SONNET_THRESHOLD_PCT",
    "NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED",
    "NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED",
    "USER_AGENT",
)

RUNTIME_ENV_DEFAULTS = {
    "POLL_INTERVAL_SECONDS": "60",
    "DASHBOARD_HOST": "127.0.0.1",
    "DASHBOARD_PORT": "7474",
    "AUTH_BROWSER": "chrome",
    "EXPECTED_WEEKLY_LINE_ENABLED": "true",
    "EXPECTED_ACTIVE_START_HHMM": "08:00",
    "EXPECTED_ACTIVE_END_HHMM": "19:00",
    "NOTIFY_SESSION_THRESHOLD_PCT": "",
    "NOTIFY_WEEKLY_THRESHOLD_PCT": "",
    "NOTIFY_EXTRA_THRESHOLD_PCT": "",
    "NOTIFY_SONNET_THRESHOLD_PCT": "",
    "NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED": "false",
    "NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED": "false",
    "USER_AGENT": "",
}


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
    notify_session_threshold_pct: Optional[float]
    notify_weekly_threshold_pct: Optional[float]
    notify_extra_threshold_pct: Optional[float]
    notify_sonnet_threshold_pct: Optional[float]
    notify_expected_weekly_overrun_enabled: bool
    notify_expected_sonnet_overrun_enabled: bool


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


def _as_pct_threshold(raw: str | None) -> Optional[float]:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed < 0 or parsed > 100:
        return None
    return parsed


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
        notify_session_threshold_pct=_as_pct_threshold(get("NOTIFY_SESSION_THRESHOLD_PCT")),
        notify_weekly_threshold_pct=_as_pct_threshold(get("NOTIFY_WEEKLY_THRESHOLD_PCT")),
        notify_extra_threshold_pct=_as_pct_threshold(get("NOTIFY_EXTRA_THRESHOLD_PCT")),
        notify_sonnet_threshold_pct=_as_pct_threshold(get("NOTIFY_SONNET_THRESHOLD_PCT")),
        notify_expected_weekly_overrun_enabled=_as_bool(
            get("NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED"), False
        ),
        notify_expected_sonnet_overrun_enabled=_as_bool(
            get("NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED"), False
        ),
    )


def has_api_auth(config: AppConfig) -> bool:
    return bool(config.claude_org_id and config.claude_cookie_header)


def read_runtime_env_values() -> dict[str, str]:
    ensure_runtime_paths()
    values: dict[str, str] = {}
    if not RUNTIME_ENV_PATH.exists():
        return values
    for line in RUNTIME_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        values[key.strip()] = raw.strip()
    return values


def write_runtime_env_values(overrides: dict[str, str]) -> None:
    ensure_runtime_paths()
    values = read_runtime_env_values()
    values.update(overrides)

    lines = ["# Auto-generated by Claudometer. Do not share this file."]
    for key in RUNTIME_ENV_ORDER:
        lines.append(f"{key}={values.get(key, RUNTIME_ENV_DEFAULTS.get(key, ''))}")

    RUNTIME_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
