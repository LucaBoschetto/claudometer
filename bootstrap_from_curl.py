from __future__ import annotations

import re
import sys
from pathlib import Path

from auth import _write_runtime_env

ORG_RE = re.compile(r"https://claude\.ai/api/organizations/([0-9a-fA-F-]+)/usage")
HEADER_RE = re.compile(r"-H\s+'([^']+)'|\"([^\"]+)\"")
COOKIE_RE = re.compile(r"-b\s+'([^']+)'|-b\s+\"([^\"]+)\"")


def parse_curl(curl_text: str) -> dict[str, str]:
    org_match = ORG_RE.search(curl_text)
    if not org_match:
        raise ValueError("Could not find organization ID in cURL text")

    cookie_match = COOKIE_RE.search(curl_text)
    if not cookie_match:
        raise ValueError("Could not find cookie header (-b) in cURL text")

    raw_cookie = cookie_match.group(1) or cookie_match.group(2)

    headers = {}
    for m in HEADER_RE.finditer(curl_text):
        raw = m.group(1) or m.group(2)
        if not raw or ":" not in raw:
            continue
        k, v = raw.split(":", 1)
        headers[k.strip().lower()] = v.strip()

    return {
        "CLAUDE_ORG_ID": org_match.group(1),
        "CLAUDE_COOKIE_HEADER": raw_cookie,
        "ANTHROPIC_ANONYMOUS_ID": headers.get("anthropic-anonymous-id", ""),
        "ANTHROPIC_DEVICE_ID": headers.get("anthropic-device-id", ""),
    }


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python bootstrap_from_curl.py /path/to/curl.txt")
        raise SystemExit(2)

    p = Path(sys.argv[1]).expanduser()
    if not p.exists():
        print(f"File not found: {p}")
        raise SystemExit(2)

    values = parse_curl(p.read_text(encoding="utf-8"))
    _write_runtime_env(values)
    print("Wrote auth config to ~/.claude-usage-tracker/config.env")


if __name__ == "__main__":
    main()
