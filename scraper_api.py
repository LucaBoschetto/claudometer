from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from config import AppConfig
from models import UsageSample, normalize_pct


def _normalize_usage_api_pct(value: Any) -> float | None:
    """Usage endpoint already returns utilization in 0..100 units."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, v))

USAGE_ENDPOINT_TEMPLATE = "https://claude.ai/api/organizations/{org_id}/usage"
USAGE_REFERER = "https://claude.ai/settings/usage"


class AuthRequiredError(RuntimeError):
    pass


class UsageAPIClient:
    def __init__(self, config: AppConfig):
        self.config = config

    def fetch_sample(self, ts_iso: str) -> UsageSample:
        if not self.config.claude_org_id or not self.config.claude_cookie_header:
            raise AuthRequiredError("Missing org ID or cookie header in config")

        url = USAGE_ENDPOINT_TEMPLATE.format(org_id=self.config.claude_org_id)
        response = requests.get(
            url,
            headers=self._build_headers(),
            timeout=30,
            allow_redirects=True,
        )

        if response.status_code in (401, 403):
            raise AuthRequiredError(f"Received HTTP {response.status_code}")

        if "/login" in response.url:
            raise AuthRequiredError("Redirected to login")

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            raise AuthRequiredError("Unexpected non-JSON response, auth likely expired")

        payload = response.json()
        return parse_payload(payload, ts_iso)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "referer": USAGE_REFERER,
            "user-agent": self.config.user_agent,
            "cookie": self.config.claude_cookie_header or "",
            "anthropic-client-platform": "web_claude_ai",
        }

        if self.config.anthropic_anonymous_id:
            headers["anthropic-anonymous-id"] = self.config.anthropic_anonymous_id
        if self.config.anthropic_device_id:
            headers["anthropic-device-id"] = self.config.anthropic_device_id

        return headers


def parse_payload(payload: dict[str, Any], ts_iso: str) -> UsageSample:
    five_hour = payload.get("five_hour") or {}
    seven_day = payload.get("seven_day") or {}
    extra_usage = payload.get("extra_usage") or {}

    extra_util = _normalize_usage_api_pct(extra_usage.get("utilization"))
    if extra_util is None:
        monthly_limit = extra_usage.get("monthly_limit")
        used_credits = extra_usage.get("used_credits")
        if monthly_limit not in (None, 0) and used_credits is not None:
            try:
                extra_util = normalize_pct((float(used_credits) / float(monthly_limit)) * 100.0)
            except (TypeError, ValueError, ZeroDivisionError):
                extra_util = None

    return UsageSample(
        ts=ts_iso,
        session_pct=_normalize_usage_api_pct(five_hour.get("utilization")),
        session_resets=_normalize_ts(five_hour.get("resets_at")),
        weekly_pct=_normalize_usage_api_pct(seven_day.get("utilization")),
        weekly_resets=_normalize_ts(seven_day.get("resets_at")),
        extra_pct=extra_util,
    )


def _normalize_ts(raw: Any) -> str | None:
    if not raw:
        return None
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
