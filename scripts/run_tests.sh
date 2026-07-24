#!/usr/bin/env bash
# Run the burn-rate tests against the JS currently in web.py, plus the Python
# checks for db.py's range=all collapse.
#
# Regenerates shipped.mjs first, so the tests always exercise the live source
# rather than a stale snapshot. Requires node (built-in test runner, no deps).
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/collapse_check.py
python scripts/scoped_parse_check.py
python scripts/scoped_migration_check.py
python scripts/extract_js.py > scripts/shipped.mjs
node --test scripts/*.test.mjs
