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
OVERAGE_SPEND_LIMIT_ENDPOINT_TEMPLATE = "https://claude.ai/api/organizations/{org_id}/overage_spend_limit"
USAGE_REFERER = "https://claude.ai/settings/usage"


class AuthRequiredError(RuntimeError):
    pass


class UsageAPIClient:
    def __init__(self, config: AppConfig):
        self.config = config

    def fetch_sample(self, ts_iso: str) -> UsageSample:
        if not self.config.claude_org_id or not self.config.claude_cookie_header:
            raise AuthRequiredError("Missing org ID or cookie header in config")

        headers = self._build_headers()
        url = USAGE_ENDPOINT_TEMPLATE.format(org_id=self.config.claude_org_id)
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)

        if response.status_code in (401, 403):
            raise AuthRequiredError(f"Received HTTP {response.status_code}")

        if "/login" in response.url:
            raise AuthRequiredError("Redirected to login")

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            raise AuthRequiredError("Unexpected non-JSON response, auth likely expired")

        payload = response.json()
        overage_payload = self._fetch_overage_payload(headers)
        sample = parse_payload(payload, ts_iso, overage_payload)
        if sample.session_pct is None or sample.weekly_pct is None:
            raise AuthRequiredError("Usage payload missing core utilization fields, auth likely expired")
        return sample

    def _fetch_overage_payload(self, headers: dict[str, str]) -> dict[str, Any] | None:
        if not self.config.claude_org_id:
            return None
        url = OVERAGE_SPEND_LIMIT_ENDPOINT_TEMPLATE.format(org_id=self.config.claude_org_id)
        response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        if response.status_code in (401, 403):
            raise AuthRequiredError(f"Received HTTP {response.status_code} from overage endpoint")
        if "/login" in response.url:
            raise AuthRequiredError("Redirected to login from overage endpoint")
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None

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


def parse_payload(
    payload: dict[str, Any], ts_iso: str, overage_payload: dict[str, Any] | None = None
) -> UsageSample:
    five_hour = payload.get("five_hour") or {}
    seven_day = payload.get("seven_day") or {}
    extra_usage = payload.get("extra_usage") or {}
    overage = overage_payload or {}

    extra_util = _normalize_usage_api_pct(extra_usage.get("utilization"))
    extra_enabled = _normalize_optional_bool(extra_usage.get("is_enabled"))
    extra_used_credits = _as_float(extra_usage.get("used_credits"))
    extra_monthly_limit = _as_float(extra_usage.get("monthly_limit"))

    if extra_enabled is None:
        extra_enabled = _normalize_optional_bool(overage.get("is_enabled"))
    if extra_used_credits is None:
        extra_used_credits = _as_float(overage.get("used_credits"))
    if extra_monthly_limit is None:
        extra_monthly_limit = _as_float(overage.get("monthly_credit_limit"))

    if extra_util is None:
        if extra_monthly_limit not in (None, 0) and extra_used_credits is not None:
            try:
                extra_util = normalize_pct((extra_used_credits / extra_monthly_limit) * 100.0)
            except (TypeError, ValueError, ZeroDivisionError):
                extra_util = None

    return UsageSample(
        ts=ts_iso,
        session_pct=_normalize_usage_api_pct(five_hour.get("utilization")),
        session_resets=_normalize_ts(five_hour.get("resets_at")),
        weekly_pct=_normalize_usage_api_pct(seven_day.get("utilization")),
        weekly_resets=_normalize_ts(seven_day.get("resets_at")),
        extra_pct=extra_util,
        extra_enabled=extra_enabled,
        extra_used_credits=extra_used_credits,
        extra_monthly_limit=extra_monthly_limit,
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


def _normalize_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
