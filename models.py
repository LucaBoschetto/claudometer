from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_pct(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= v <= 1.0:
        v *= 100.0
    return max(0.0, min(100.0, v))


@dataclass
class UsageSample:
    ts: str
    session_pct: Optional[float]
    session_resets: Optional[str]
    weekly_pct: Optional[float]
    weekly_resets: Optional[str]
    extra_pct: Optional[float]
    extra_enabled: Optional[bool]
    extra_used_credits: Optional[float]
    extra_monthly_limit: Optional[float]
    sonnet_pct: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
