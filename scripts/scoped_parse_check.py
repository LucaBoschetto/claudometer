#!/usr/bin/env python3
"""Assert parse_payload reads the model-scoped weekly limit from limits[].

Plain asserts, run from scripts/run_tests.sh (this repo declares no Python test
framework). Run: python scripts/scoped_parse_check.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scraper_api import parse_payload

TS = "2026-07-24T12:00:00+00:00"

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def limits_with_scoped(model="Fable", percent=42):
    return [
        {"kind": "session", "group": "session", "percent": 41, "scope": None},
        {"kind": "weekly_all", "group": "weekly", "percent": 40, "scope": None},
        {
            "kind": "weekly_scoped",
            "group": "weekly",
            "percent": percent,
            "scope": {"model": {"id": None, "display_name": model}},
            "is_active": True,
        },
    ]


print("parse_payload scoped limit:")

# Scoped entry present -> pct + model come from it, picked by kind not position.
s = parse_payload({"five_hour": {"utilization": 41.0}, "seven_day": {"utilization": 40.0},
                   "limits": limits_with_scoped()}, TS)
check("scoped present -> scoped_pct = 42", s.scoped_pct == 42.0)
check("scoped present -> scoped_model = 'Fable'", s.scoped_model == "Fable")

# No scoped entry -> both None (account without a per-model cap).
s = parse_payload({"five_hour": {"utilization": 41.0}, "seven_day": {"utilization": 40.0},
                   "limits": [{"kind": "session", "scope": None},
                              {"kind": "weekly_all", "scope": None}]}, TS)
check("no scoped entry -> scoped_pct None", s.scoped_pct is None)
check("no scoped entry -> scoped_model None", s.scoped_model is None)

# limits missing entirely -> both None (null-safe).
s = parse_payload({"five_hour": {"utilization": 41.0}, "seven_day": {"utilization": 40.0}}, TS)
check("no limits key -> scoped_pct None", s.scoped_pct is None)
check("no limits key -> scoped_model None", s.scoped_model is None)

print()
if failures:
    print(f"scoped_parse_check: {len(failures)} FAILED")
    sys.exit(1)
print("scoped_parse_check: all passed")
