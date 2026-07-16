# Range=All Render Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop expanding run-length-encoded history into ~100k per-sample chart rows at `range=all`, cutting the `/data.json` payload from 28.8 MB to ~3.2 MB and the Plotly point count ~9x, while drawing the identical usage chart.

**Architecture:** `usage_runs` is run-length encoded — one row means "this reading held steady for N polls". `_expand_run` inflates 5,499 runs into ~100k rows whose interior points are collinear duplicates on *invented* evenly-spaced timestamps. At `range=all` we emit only each run's endpoints, plus 4 interior "decay" points on runs longer than the burn window (the burn-rate series decays across that window, so endpoints alone would draw a 30-minute decay as a multi-day ramp). The zoomed presets (`today`, `weekly_cycle`) keep `_expand_run` untouched — they are small and their fidelity is visible.

**Tech Stack:** Python 3.12+ (stdlib sqlite3, no test framework), vanilla JS inside a Python string in `web.py`, Plotly, node's built-in test runner.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-render-perf-design.md`. Read it before starting.
- Branch: `render-perf-range-all`. Already exists; the spec is committed there as `5b9e3d2`.
- The usage chart at `range=all` MUST stay pixel-identical. This is the lossless bar.
- `_expand_run` MUST NOT change behaviour. It stays correct for `today`/`weekly_cycle`.
- `BURN_WINDOW_MINUTES` has exactly one definition (`config.py`). No literal `30` for the burn window anywhere else.
- NEVER restart or interfere with the live tracker on port 7474 (`systemctl --user claudometer`). Verification uses a serve-only harness on port 7475.
- Out of scope: `scattergl`, `line_shape: 'hv'`, decoupling the usage/burn charts, any schema change.
- Commit after each task. Personal repo: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` is fine.

## File Structure

| file | responsibility | change |
|---|---|---|
| `config.py` | single source of truth for the burn window | add `BURN_WINDOW_MINUTES` |
| `db.py` | run storage; expand OR collapse runs into chart rows | add `_collapse_run`, `_normalize_range`; wire `fetch_chart_data` |
| `web.py` | serves HTML/JS; burn math; chart render | JS const + substitution; resize debounce |
| `scripts/extract_js.py` | scrape JS out of `web.py` for tests | emit the new module-global into the preamble |
| `scripts/collapse_check.py` | **new** — assert the Python collapse | plain asserts, no framework |
| `scripts/burn_rate.test.mjs` | burn math tests | add subdivision-invariance test |
| `scripts/run_tests.sh` | test entrypoint | run the Python check too |

---

### Task 1: Single source of truth for `BURN_WINDOW_MINUTES`

The collapse (Task 2) needs the burn window, which today only exists as the
`windowMinutes = 30` default in `computeBurnRate`. Two copies would drift, so
this task plumbs one constant to Python, the served JS, and the tests.

**Files:**
- Modify: `config.py` (after `BROWSER_PROFILE_DIR`, ~line 14)
- Modify: `web.py:12` (import), `web.py:988` (JS const), `web.py:1767` (signature), `web.py:2375` (substitution chain)
- Modify: `scripts/extract_js.py` (imports + preamble)
- Test: `scripts/burn_rate.test.mjs` (the existing 9 tests are the net)

**Interfaces:**
- Consumes: nothing.
- Produces: `config.BURN_WINDOW_MINUTES: int` (= 30), imported by `db.py` in Task 2 and `scripts/collapse_check.py` in Task 2. JS module-global `BURN_WINDOW_MINUTES` visible to `computeBurnRate`.

- [ ] **Step 1: Add the constant to `config.py`**

Insert after the `BROWSER_PROFILE_DIR` line (~line 14):

```python
# Trailing window for the dashboard's burn-rate math (computeBurnRate in
# web.py). db.py's range=all collapse must keep enough points to sample the
# decay across this window, so the server, the served JS and the tests all read
# it from here rather than each hardcoding 30.
BURN_WINDOW_MINUTES = 30
```

- [ ] **Step 2: Import it in `web.py`**

Change line 12 from:

```python
from config import load_config, write_runtime_env_values
```

to:

```python
from config import BURN_WINDOW_MINUTES, load_config, write_runtime_env_values
```

- [ ] **Step 3: Declare the JS module-global**

In `_app_js`, immediately after line 988 (`const expectedLineEnabled = __EXPECTED_LINE_ENABLED__;`), add:

```javascript
const BURN_WINDOW_MINUTES = __BURN_WINDOW_MINUTES__;
```

- [ ] **Step 4: Read the constant in `computeBurnRate`**

Change line 1767 from:

```javascript
function computeBurnRate(rows, values, windowMinutes = 30) {
```

to:

```javascript
function computeBurnRate(rows, values, windowMinutes = BURN_WINDOW_MINUTES) {
```

- [ ] **Step 5: Substitute it when serving**

In the `.replace(...)` chain starting at line 2375, add the new replacement
directly after the `__POLL_MS__` line:

```python
        js.replace("__POLL_MS__", str(poll_ms))
        .replace("__BURN_WINDOW_MINUTES__", str(BURN_WINDOW_MINUTES))
        .replace("__POLL_INTERVAL_SECONDS__", str(poll_interval_seconds))
```

- [ ] **Step 6: Run the tests and watch them FAIL**

Run: `./scripts/run_tests.sh`

Expected: FAIL. Every test errors with `ReferenceError: BURN_WINDOW_MINUTES is not defined`.

This failure is the point of the step, not an accident. `extract_js.py` copies
function text **verbatim** and does not substitute placeholders, so
`shipped.mjs` now contains a `computeBurnRate` whose default parameter
references a global that only exists in the served page. The tests call it with
two arguments, so the default always evaluates. Step 7 fixes it. (This is also
why the constant is a module-global rather than a templated default parameter —
`windowMinutes = __BURN_WINDOW_MINUTES__` would leave the raw placeholder in
`shipped.mjs` and be unfixable from the preamble.)

- [ ] **Step 7: Teach `extract_js.py` to emit the constant**

`extract_js.py` already stubs the `viewMode` module-global for exactly this
reason. Add the new one beside it.

At the top, after `WEB_PY = ...` (~line 16), add the repo root to `sys.path` —
a bare `import config` fails, because running `python scripts/extract_js.py`
puts `scripts/` on `sys.path`, not the repo root:

```python
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config import BURN_WINDOW_MINUTES
```

Then in `main()`, add the constant to the `parts` preamble list, after the
`setViewMode` line:

```python
        "// computeBurnRate reads this module-global in web.py, where it is",
        "// templated from config.BURN_WINDOW_MINUTES. Keep them in sync.",
        f"const BURN_WINDOW_MINUTES = {BURN_WINDOW_MINUTES};",
```

- [ ] **Step 8: Run the tests and verify they PASS**

Run: `./scripts/run_tests.sh`

Expected: PASS, `# pass 9`, `# fail 0`.

- [ ] **Step 9: Verify the served page still substitutes**

Run:

```bash
cd /home/angel/code/personal/claudometer
grep -c "__BURN_WINDOW_MINUTES__" scripts/shipped.mjs
```

Expected: `0` — the placeholder must not survive into the extracted JS.

- [ ] **Step 10: Commit**

`scripts/shipped.mjs` is generated and gitignored (`.gitignore:36`) — do not add it.

```bash
git add config.py web.py scripts/extract_js.py
git commit -m "Read the burn window from a single constant

computeBurnRate hardcoded windowMinutes = 30. db.py's range=all collapse
needs the same value, so move it to config.py and feed the served JS,
db.py and the tests from there.

extract_js.py copies function text verbatim without substituting, so the
JS reads a module-global rather than a templated default parameter, and
the extractor emits it into the preamble beside the viewMode stub.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `_collapse_run` and its check script

**Files:**
- Create: `scripts/collapse_check.py`
- Modify: `db.py` (imports ~line 9; new constants; new `_collapse_run` after `_expand_run` ~line 503)
- Modify: `scripts/run_tests.sh`

**Interfaces:**
- Consumes: `config.BURN_WINDOW_MINUTES` (Task 1). `UsageDB._expanded_row(run: dict, ts: str) -> dict` (existing, unchanged). `db._parse_iso(raw: str | None) -> datetime | None` (existing module-level).
- Produces: `UsageDB._collapse_run(run: dict[str, Any]) -> list[dict[str, Any]]`, consumed by `fetch_chart_data` in Task 3.

- [ ] **Step 1: Write the failing check script**

Create `scripts/collapse_check.py`:

```python
#!/usr/bin/env python3
"""Assert UsageDB._collapse_run emits the right points for each run shape.

The collapse lives in Python, so scripts/burn_rate.test.mjs cannot see a bug in
it. This repo declares no Python test framework and this change does not justify
adding one, so these are plain asserts run from scripts/run_tests.sh alongside
the node suite.

Run: python scripts/collapse_check.py
"""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config import BURN_WINDOW_MINUTES
from db import COLLAPSE_DECAY_POINTS, UsageDB, _parse_iso

T0 = datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=BURN_WINDOW_MINUTES)
# ts_start + decay points + ts_end
LONG_RUN_POINTS = COLLAPSE_DECAY_POINTS + 2

# _collapse_run never touches the DB, and UsageDB.__init__ only stores the path.
DB = UsageDB(pathlib.Path("/nonexistent/never-opened.db"))


def make_run(minutes: float, count: int, **overrides) -> dict:
    run = {
        "ts_start": T0.isoformat(),
        "ts_end": (T0 + timedelta(minutes=minutes)).isoformat(),
        "sample_count": count,
        "session_pct": 10.0,
        "session_resets": "2026-07-16T15:00:00+00:00",
        "weekly_pct": 5.0,
        "weekly_resets": "2026-07-21T08:00:00+00:00",
        "extra_pct": 0.0,
        "extra_enabled": False,
        "extra_used_credits": 0.0,
        "extra_monthly_limit": 1000.0,
        "sonnet_pct": None,
    }
    run.update(overrides)
    return run


def times(rows: list[dict]) -> list[datetime]:
    return [_parse_iso(r["ts"]) for r in rows]


failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


print("_collapse_run:")

# A single-sample run has no span to draw; one point is all there is.
rows = DB._collapse_run(make_run(minutes=0, count=1))
check("single sample -> 1 point", len(rows) == 1)
check("single sample -> at ts_start", times(rows) == [T0])

# Degenerate spans: _expand_run emits `count` duplicates at the same instant,
# which is visually one point. Collapsing to one is a deliberate divergence.
rows = DB._collapse_run(make_run(minutes=0, count=5))
check("zero-length span -> 1 point", len(rows) == 1)

rows = DB._collapse_run(make_run(minutes=10, count=5, ts_end=T0.isoformat(), ts_start=(T0 + timedelta(minutes=10)).isoformat()))
check("ts_end before ts_start -> 1 point", len(rows) == 1)

rows = DB._collapse_run(make_run(minutes=10, count=5, ts_end="not-a-timestamp"))
check("unparseable ts_end -> 1 point", len(rows) == 1)

# A run shorter than the burn window draws as a flat segment: two points.
rows = DB._collapse_run(make_run(minutes=10, count=10))
check("short run -> 2 points", len(rows) == 2)
check("short run -> endpoints exactly", times(rows) == [T0, T0 + timedelta(minutes=10)])

# Exactly at the window boundary is still "short" (no decay points needed).
rows = DB._collapse_run(make_run(minutes=BURN_WINDOW_MINUTES, count=30))
check("run == window -> 2 points", len(rows) == 2)

# A long run needs interior points, or the burn chart draws its decay as a ramp.
long_run = make_run(minutes=3 * 24 * 60, count=4320)
rows = DB._collapse_run(long_run)
check(f"long run -> {LONG_RUN_POINTS} points", len(rows) == LONG_RUN_POINTS)
ts = times(rows)
check("long run -> starts at ts_start", ts[0] == T0)
check("long run -> ends at ts_end", ts[-1] == _parse_iso(long_run["ts_end"]))
check("long run -> last decay point lands on the window edge", ts[-2] == T0 + WINDOW)
max_gap = WINDOW.total_seconds() / COLLAPSE_DECAY_POINTS
gaps = [(b - a).total_seconds() for a, b in zip(ts[:-2], ts[1:-1])]
check(
    "long run -> no in-window gap exceeds window/COLLAPSE_DECAY_POINTS",
    all(g <= max_gap + 1e-6 for g in gaps),
)
check("long run -> timestamps strictly increasing", all(a < b for a, b in zip(ts, ts[1:])))

# The RLE property: every point of a run carries that run's values.
values = [{k: v for k, v in r.items() if k != "ts"} for r in rows]
check("long run -> all points carry identical values", all(v == values[0] for v in values))
check("long run -> values match the run", values[0]["session_pct"] == 10.0)

print()
if failures:
    print(f"collapse_check: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("collapse_check: all passed")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python scripts/collapse_check.py`

Expected: FAIL with `ImportError: cannot import name 'COLLAPSE_DECAY_POINTS' from 'db'` —
neither the constant nor `_collapse_run` exists yet. Step 3 adds both.

- [ ] **Step 3: Implement `_collapse_run`**

In `db.py`, add to the imports (~line 9, beside `from models import UsageSample`):

```python
from config import BURN_WINDOW_MINUTES
```

Add module-level constants after the imports:

```python
# Interior points emitted across the burn window of a long run. The burn-rate
# series decays over BURN_WINDOW_MINUTES after the step into a run; without
# these, a multi-day idle run draws that decay as a multi-day ramp. Four caps
# every in-window gap at 7.5 minutes, which is ~0.04px at range=all.
COLLAPSE_DECAY_POINTS = 4

_BURN_WINDOW = timedelta(minutes=BURN_WINDOW_MINUTES)
```

Add the method immediately after `_expand_run` (which stays unchanged), ~line 503:

```python
    def _collapse_run(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Emit the fewest points that redraw this run.

        usage_runs is run-length encoded: every sample in a run carries the same
        values, so the run draws as a flat segment and _expand_run's interior
        points are collinear duplicates sitting on invented evenly-spaced
        timestamps. Only the endpoints carry shape, and _expand_run pins its
        first point at ts_start and its last at ts_end, so emitting those two
        reproduces the identical polyline.

        The burn-rate series is the exception. It is derived, so it is *not*
        constant within a run: it decays across the burn window after the step
        into the run, then sits at zero. Endpoints alone would draw that decay
        as a ramp spanning the whole run, so long runs also get interior points
        across the window.

        Only used for range=all; the zoomed presets keep _expand_run.
        """
        count = max(1, int(run["sample_count"]))
        start_dt = _parse_iso(run["ts_start"])
        end_dt = _parse_iso(run["ts_end"])
        if count == 1 or start_dt is None or end_dt is None or end_dt <= start_dt:
            # No span to draw. _expand_run emits `count` duplicates at the same
            # instant here, which is visually this same single point.
            return [self._expanded_row(run, run["ts_start"])]

        points = [start_dt]
        if (end_dt - start_dt) > _BURN_WINDOW:
            for index in range(1, COLLAPSE_DECAY_POINTS + 1):
                points.append(start_dt + _BURN_WINDOW * (index / COLLAPSE_DECAY_POINTS))
        points.append(end_dt)
        return [
            self._expanded_row(run, point.astimezone(timezone.utc).isoformat())
            for point in points
        ]
```

- [ ] **Step 4: Run the check to verify it passes**

Run: `python scripts/collapse_check.py`

Expected: PASS, ending `collapse_check: all passed`, exit 0.

- [ ] **Step 5: Wire it into the test entrypoint**

In `scripts/run_tests.sh`, add the Python check before the node run, and update
the header comment:

```bash
#!/usr/bin/env bash
# Run the burn-rate tests against the JS currently in web.py, plus the Python
# checks for db.py's range=all collapse.
#
# Regenerates shipped.mjs first, so the tests always exercise the live source
# rather than a stale snapshot. Requires node (built-in test runner, no deps).
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/collapse_check.py
python scripts/extract_js.py > scripts/shipped.mjs
node --test scripts/*.test.mjs
```

- [ ] **Step 6: Run the full suite**

Run: `./scripts/run_tests.sh`

Expected: `collapse_check: all passed`, then `# pass 9`, `# fail 0`.

- [ ] **Step 7: Commit**

```bash
git add db.py scripts/collapse_check.py scripts/run_tests.sh
git commit -m "Add _collapse_run for range=all

usage_runs is run-length encoded, so every sample in a run carries the
same values and _expand_run's interior points are collinear duplicates on
invented timestamps. Emit the endpoints instead.

Long runs also get 4 interior points across the burn window: the burn
series decays over that window after a step, so endpoints alone would
draw a 30-minute decay as a multi-day ramp.

Not wired up yet. _expand_run is unchanged and still serves the zoomed
presets.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire `fetch_chart_data` to collapse at `range=all`

**Files:**
- Modify: `db.py:175-236` (`fetch_chart_data`), `db.py:238-241` (`_range_window`), new `_normalize_range`
- Modify: `scripts/collapse_check.py` (append an integration section)

**Interfaces:**
- Consumes: `UsageDB._collapse_run` (Task 2), `UsageDB._expand_run` (existing).
- Produces: `fetch_chart_data(range_preset: str = "all")` returning the same dict shape as today (`rows`, `total_samples`, `filtered_samples`, `run_count`) with collapsed `rows` when the range normalizes to `all`.

- [ ] **Step 1: Write the failing integration check**

Append to `scripts/collapse_check.py`, before the final `if failures:` block:

```python
import sqlite3
import tempfile

print()
print("fetch_chart_data:")

RUN_COLUMNS = (
    "ts_start", "ts_end", "sample_count", "session_pct", "session_resets",
    "weekly_pct", "weekly_resets", "extra_pct", "extra_enabled",
    "extra_used_credits", "extra_monthly_limit", "sonnet_pct",
)

with tempfile.TemporaryDirectory() as tmp:
    db = UsageDB(pathlib.Path(tmp) / "usage.db")
    db.init()
    # One long idle run (collapses to 6) and one short run (collapses to 2).
    seed = [
        make_run(minutes=3 * 24 * 60, count=4320),
        make_run(minutes=10, count=10, ts_start=(T0 + timedelta(days=3)).isoformat(),
                 ts_end=(T0 + timedelta(days=3, minutes=10)).isoformat()),
    ]
    with sqlite3.connect(db.path) as conn:
        for run in seed:
            conn.execute(
                f"INSERT INTO usage_runs ({','.join(RUN_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(RUN_COLUMNS))})",
                tuple(run[c] for c in RUN_COLUMNS),
            )
        conn.commit()

    expanded_total = 4320 + 10
    collapsed_total = LONG_RUN_POINTS + 2  # long run + short run's endpoints

    all_payload = db.fetch_chart_data("all")
    check("range=all -> collapsed rows", len(all_payload["rows"]) == collapsed_total)
    check("range=all -> total_samples counts real samples, not rows",
          all_payload["total_samples"] == expanded_total)
    check("range=all -> filtered_samples counts real samples",
          all_payload["filtered_samples"] == expanded_total)
    check("range=all -> run_count unchanged", all_payload["run_count"] == 2)

    # An unknown range normalizes to "all" in _range_window, so it must collapse
    # too. Otherwise it would get the all-window but 4330 expanded rows.
    bogus = db.fetch_chart_data("nonsense")
    check("unknown range -> collapses like all",
          len(bogus["rows"]) == len(all_payload["rows"]))

    # The zoomed presets must keep full per-sample expansion. The exact count is
    # timezone-dependent (the "today" window is derived from the latest ts_end in
    # local time), but expansion always yields strictly more rows than the
    # collapse would, and the latest run always falls inside the window.
    today = db.fetch_chart_data("today")
    check("range=today -> still expands (not collapsed)",
          len(today["rows"]) > collapsed_total)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python scripts/collapse_check.py`

Expected: FAIL. `range=all -> collapsed rows` fails because `fetch_chart_data`
still expands (4,330 rows, not 8), and `unknown range -> collapses like all`
fails with it.

- [ ] **Step 3: Extract the range normalization**

`_range_window` currently normalizes internally, so an unknown range silently
becomes `all`. `fetch_chart_data` must make the same decision from the same
place, or a bogus range gets the all-window with expanded rows.

Add this module-level function to `db.py`, just above `class UsageDB`:

```python
def _normalize_range(range_preset: str) -> str:
    """Fold unknown presets onto "all".

    Both the SQL window and the expand/collapse choice must agree on this, or a
    bogus range gets the all-window with per-sample expansion.
    """
    return range_preset if range_preset in {"today", "weekly_cycle", "all"} else "all"
```

Then change `_range_window`'s first line (line 241) from:

```python
        normalized = range_preset if range_preset in {"today", "weekly_cycle", "all"} else "all"
```

to:

```python
        normalized = _normalize_range(range_preset)
```

- [ ] **Step 4: Select the expander in `fetch_chart_data`**

In `fetch_chart_data`, replace the row-building block (lines 225-230):

```python
        payload_runs = [self._run_row_to_dict(row) for row in runs]
        expanded_rows: list[dict[str, Any]] = []
        filtered_samples = 0
        for run in payload_runs:
            filtered_samples += int(run["sample_count"])
            expanded_rows.extend(self._expand_run(run))
```

with:

```python
        payload_runs = [self._run_row_to_dict(row) for row in runs]
        # range=all spans the whole history, where per-sample expansion is ~100k
        # collinear points and ~29 MB of JSON. The zoomed presets are small and
        # their fidelity is visible, so they keep the full expansion.
        to_rows = (
            self._collapse_run
            if _normalize_range(range_preset) == "all"
            else self._expand_run
        )
        expanded_rows: list[dict[str, Any]] = []
        filtered_samples = 0
        for run in payload_runs:
            filtered_samples += int(run["sample_count"])
            expanded_rows.extend(to_rows(run))
```

Note `filtered_samples` still sums `sample_count` from the runs, so the
"Samples: N" readout is unaffected by the collapse.

- [ ] **Step 5: Run the check to verify it passes**

Run: `python scripts/collapse_check.py`

Expected: PASS, `collapse_check: all passed`.

- [ ] **Step 6: Run the full suite**

Run: `./scripts/run_tests.sh`

Expected: `collapse_check: all passed`, `# pass 9`, `# fail 0`.

- [ ] **Step 7: Measure the real payload drop**

Run:

```bash
cd /home/angel/code/personal/claudometer
.venv/bin/python -c "
import json
from config import DB_PATH
from db import UsageDB
db = UsageDB(DB_PATH)
for preset in ('all', 'weekly_cycle'):
    p = db.fetch_chart_data(preset)
    print(f'{preset:13} rows={len(p[\"rows\"]):>7,}  json={len(json.dumps(p))/1e6:>6.2f} MB  total_samples={p[\"total_samples\"]:,}')
"
```

Expected: `all` shows roughly 11,000 rows / ~3.2 MB (was 100,744 / 28.83 MB);
`weekly_cycle` is unchanged at ~2.6k rows. `total_samples` still reports the
real sample count (~100k) for both.

- [ ] **Step 8: Commit**

```bash
git add db.py scripts/collapse_check.py
git commit -m "Collapse runs at range=all

Cuts /data.json?range=all from ~100k rows / 28.8 MB to ~11k / ~3.2 MB.
The zoomed presets keep per-sample expansion.

Normalization moves into _normalize_range so the SQL window and the
expand/collapse choice cannot disagree; previously an unknown preset
would have taken the all-window with expanded rows.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Pin the invariance the collapse rests on

The collapse is only legitimate because `computeBurnRate` is time-weighted:
subdividing a segment into N sub-steps yields the same weighted average, so
dropping a run's interior points cannot move the burn rate. This test states
that property so a future change to the burn math cannot silently invalidate
the collapse.

Unlike Tasks 2 and 3 this is a **characterization test** — it passes the moment
it is written, because the property already holds. That is expected. It fails
only if someone later changes `computeBurnRate` to weight by sample count.

**Files:**
- Modify: `scripts/burn_rate.test.mjs` (append)

**Interfaces:**
- Consumes: `seriesFor`, `computeBurnRate` from `./shipped.mjs`; the file's existing `rows()` and `closeTo()` helpers.
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `scripts/burn_rate.test.mjs`:

```javascript
// db.py's range=all collapse drops a run's interior points and keeps only its
// endpoints. That is only safe because this function is time-weighted: a step
// subdivided into N sub-steps carries the same total weight and the same
// numerator. If that ever stops being true, the collapse silently starts
// lying and this test is the tripwire.
test('collapsing a flat run to its endpoints does not change the burn rate', () => {
  // A step from 10 to 20 at minute 1, then flat at 20 out to minute 20.
  const flat = [];
  for (let m = 1; m <= 20; m += 1) flat.push([m, 20]);
  const expanded = rows([[0, 10], ...flat]);

  // What the collapse emits: the step, then only the run's endpoints.
  const collapsed = rows([[0, 10], [1, 20], [20, 20]]);

  const e = burnOf(expanded);
  const c = burnOf(collapsed);

  closeTo(c[c.length - 1], e[e.length - 1]);
});

test('dropping interior points of a flat run preserves the current burn', () => {
  // currentBurn reads the last aligned point, which is what the Summary table
  // and the burn charts' headline number show.
  const dense = rows([[0, 0], [1, 30], [2, 30], [3, 30], [4, 30], [5, 30]]);
  const sparse = rows([[0, 0], [1, 30], [5, 30]]);
  closeTo(currentBurn(burnOf(sparse)), currentBurn(burnOf(dense)));
});
```

- [ ] **Step 2: Run the tests**

Run: `./scripts/run_tests.sh`

Expected: PASS, `# pass 11`, `# fail 0`. Both new tests pass immediately — see
the note above; they are tripwires, not TDD.

- [ ] **Step 3: Prove the tripwire actually trips**

Temporarily break the invariant to confirm the test is not vacuous. In `web.py`
line ~1803, change:

```javascript
      weighted += slope[j] * stepMs;
      weight += stepMs;
```

to count-weighting:

```javascript
      weighted += slope[j];
      weight += 1;
```

Run: `./scripts/run_tests.sh`

Expected: FAIL — the two new tests fail. **Revert the change immediately**
(`git checkout web.py`) and re-run to confirm `# pass 11` again.

- [ ] **Step 4: Commit**

```bash
git add scripts/burn_rate.test.mjs
git commit -m "Pin the invariance the range=all collapse depends on

The collapse drops a run's interior points, which is only safe because
computeBurnRate is time-weighted: subdividing a step preserves the
weighted average. Verified against the real DB (9,368 points compared, 0
mismatches). These tests fail if the weighting ever changes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Debounce the resize handler

Pre-existing and independent of the collapse, but it serves the same goal: a
resize drag currently queues one full re-render per resize event.

**Files:**
- Modify: `web.py:988` area (constant), `web.py:2371` (handler)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Add the debounce constant**

In `_app_js`, next to the `BURN_WINDOW_MINUTES` const added in Task 1, add:

```javascript
const RESIZE_DEBOUNCE_MS = 150;
```

- [ ] **Step 2: Debounce the handler**

Change line 2371 from:

```javascript
window.addEventListener('resize', () => rerenderChartWithLoading(currentRows));
```

to:

```javascript
// Resize fires continuously while dragging, and each re-render is a full
// Plotly redraw. Coalesce the burst into one render.
let resizeTimer = null;
window.addEventListener('resize', () => {
  if (resizeTimer !== null) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    rerenderChartWithLoading(currentRows);
  }, RESIZE_DEBOUNCE_MS);
});
```

- [ ] **Step 3: Verify the suite still passes**

Run: `./scripts/run_tests.sh`

Expected: `# pass 11`, `# fail 0`. (The handler is not covered by the extracted
functions; this step only confirms nothing regressed.)

- [ ] **Step 4: Verify by hand in the browser**

Start the serve-only harness per Task 6 Step 1, open `http://127.0.0.1:7475`,
select range `All`, and drag the window edge. Expected: the chart re-renders
once when the drag settles, not continuously during it.

- [ ] **Step 5: Commit**

```bash
git add web.py
git commit -m "Debounce the chart resize handler

resize fires continuously while dragging and each event triggered a full
Plotly redraw, queueing multi-second renders at range=all. Coalesce into
one render 150ms after the drag settles.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Verify against the real dashboard

The lossless claim is falsifiable, so falsify it. **Do not touch the live
tracker on port 7474** — it is a systemd user unit polling the Claude API.

**Files:**
- Create: scratch files only, under the session scratchpad. Nothing committed.

**Interfaces:**
- Consumes: the whole change.
- Produces: the measured render time for the PR description.

- [ ] **Step 1: Write the harness and the capture script**

```bash
mkdir -p /tmp/claudometer-perf
```

Create `/tmp/claudometer-perf/serve_only.py`:

```python
"""Serve the dashboard read-only on a spare port: no poll loop, no API calls."""
import logging

from config import DB_PATH
from db import UsageDB
from web import start_dashboard_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("serve_only")

server = start_dashboard_server(
    host="127.0.0.1", port=7475, db=UsageDB(DB_PATH), logger=logger,
    poll_interval_seconds=60,
    expected_weekly_line_enabled=True,
    expected_active_start_hhmm="09:00",
    expected_active_end_hhmm="18:00",
    notify_session_threshold_pct=None,
    notify_weekly_threshold_pct=None,
    notify_extra_threshold_pct=None,
    notify_sonnet_threshold_pct=None,
    notify_expected_weekly_overrun_enabled=False,
    notify_expected_sonnet_overrun_enabled=False,
    notify_expected_session_overrun_enabled=False,
)
logger.info("serving on 7475")
server.serve_forever()
```

Create `/tmp/claudometer-perf/capture.py`. It takes a label, times `renderChart`
at `range=all`, and screenshots. Both the before and after runs use this same
script, so the two captures are directly comparable:

```python
"""Usage: .venv/bin/python /tmp/claudometer-perf/capture.py <label>"""
import sys

from playwright.sync_api import sync_playwright

label = sys.argv[1]
out = f"/tmp/claudometer-perf/{label}.png"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto("http://127.0.0.1:7475/", wait_until="networkidle")
    page.select_option("#range-preset", "all")
    page.wait_for_timeout(6000)
    ms = page.evaluate("""() => {
      const t0 = performance.now();
      renderChart(currentRows);
      return performance.now() - t0;
    }""")
    rows = page.evaluate("currentRows.length")
    # Hide the live-updating status line: it prints "Last sample: <time>", which
    # changes as the real tracker writes and would show as a false pixel diff.
    page.evaluate("document.getElementById('status').style.visibility = 'hidden'")
    page.screenshot(path=out, full_page=True)
    browser.close()

print(f"[{label}] rows={rows:,}  renderChart={ms:.0f} ms  (baseline 4521 ms)")
print(f"[{label}] console errors: {errors or 'none'}")
print(f"[{label}] screenshot -> {out}")
```

- [ ] **Step 2: Capture the BEFORE baseline on `main`**

All work is committed, so the tree is clean and switching is safe. This only
changes checked-out files; it never touches the running 7474 service.

```bash
cd /home/angel/code/personal/claudometer
git status --porcelain   # expect empty
git switch main
PYTHONPATH=$PWD nohup .venv/bin/python /tmp/claudometer-perf/serve_only.py > /tmp/claudometer-perf/serve-before.log 2>&1 &
sleep 4 && curl -s -o /dev/null -w "harness: %{http_code}\n" http://127.0.0.1:7475/
.venv/bin/python /tmp/claudometer-perf/capture.py before
pkill -f "claudometer-perf/serve_only.py"; sleep 1
```

Expected: `harness: 200`, then `[before] rows=~100,744  renderChart=~4500 ms`.
This reproduces the baseline from the spec.

- [ ] **Step 3: Capture the AFTER on the branch**

```bash
cd /home/angel/code/personal/claudometer
git switch render-perf-range-all
PYTHONPATH=$PWD nohup .venv/bin/python /tmp/claudometer-perf/serve_only.py > /tmp/claudometer-perf/serve-after.log 2>&1 &
sleep 4 && curl -s -o /dev/null -w "harness: %{http_code}\n" http://127.0.0.1:7475/
.venv/bin/python /tmp/claudometer-perf/capture.py after
pkill -f "claudometer-perf/serve_only.py"; sleep 1
```

Expected: `[after] rows=~11,000` (was ~100,744), `renderChart` well under
4,521 ms (~500 ms hoped), no console errors.

**If the render time disappoints, stop and report it. `scattergl` is a
follow-up PR, not scope creep here.** Record both numbers for the PR description.

- [ ] **Step 4: Prove the usage chart is pixel-identical**

```bash
cd /home/angel/code/personal/claudometer
.venv/bin/python -c "
from PIL import Image, ImageChops
a = Image.open('/tmp/claudometer-perf/before.png').convert('RGB')
b = Image.open('/tmp/claudometer-perf/after.png').convert('RGB')
print('same size:', a.size == b.size)
box = ImageChops.difference(a, b).getbbox()
print('diff bbox:', box)
if box is None:
    print('VERDICT: identical everywhere')
else:
    print(f'VERDICT: differences from y={box[1]} to y={box[3]}')
"
```

The usage chart occupies roughly the top half of the page; the burn charts sit
below it (see the layout: "Usage history" card, then the "Burn rate" card).

Expected: either `identical everywhere`, or a diff bbox confined to the
**burn-rate card**. Burn-chart differences are known and accepted (see the
spec's trade-off section). **If the diff bbox reaches into the usage chart, the
lossless claim is broken — stop and report rather than adjusting the
expectation.**

If Pillow is not installed, open the two PNGs side by side and compare visually.
Do not add a dependency for this check.

- [ ] **Step 5: Confirm the live tracker was never touched**

```bash
pgrep -af "[s]erve_only" || echo "harness down"
systemctl --user is-active claudometer
curl -s -o /dev/null -w "7474: %{http_code}\n" http://127.0.0.1:7474/
```

Expected: `harness down`, `active`, `7474: 200`.

- [ ] **Step 6: Final suite run**

Run: `./scripts/run_tests.sh`

Expected: `collapse_check: all passed`, `# pass 11`, `# fail 0`.

- [ ] **Step 7: Open the PR**

Include in the description: the measured before/after render time, the payload
drop (28.8 MB to ~3.2 MB), and — stated plainly, not buried — the known
trade-off: the burn chart at `range=all` is not pixel-identical; errors up to
~40 %/hr exist but are sub-pixel (0.04px) at that zoom and visible only under
manual zoom. Link the spec and note the decoupling follow-up.
