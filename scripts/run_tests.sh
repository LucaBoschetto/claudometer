#!/usr/bin/env bash
# Run the burn-rate tests against the JS currently in web.py.
#
# Regenerates shipped.mjs first, so the tests always exercise the live source
# rather than a stale snapshot. Requires node (built-in test runner, no deps).
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/extract_js.py > scripts/shipped.mjs
node --test scripts/*.test.mjs
