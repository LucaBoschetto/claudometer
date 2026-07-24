# Scoped-model tracking (Sonnet → generic/Fable) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repoint the tracker's per-model weekly series from the now-null `seven_day_sonnet` field to the generic `limits[]` `weekly_scoped` entry, labelling it dynamically from the API (`Fable` today) and preserving the existing Sonnet history as one continuous series.

**Architecture:** Three layers, each its own task. (1) Backend data layer — `models.py`, `scraper_api.py`, `db.py` rename `sonnet_pct` → `scoped_pct`, add a `scoped_model` label column, and migrate/backfill existing rows. (2) Config plumbing — rename the `NOTIFY_SONNET_*` settings through `config.py`/`dashboard.py`/`web.py`. (3) Frontend — `web.py` JS gains a dynamic-label helper and renames `sonnet` → `scoped`. A final task verifies end-to-end in a browser against a served snapshot.

**Tech Stack:** Python 3.14, stdlib `sqlite3`, `requests`, Playwright (verification only). Frontend is Plotly JS templated inside `web.py`. Tests are plain-assert Python scripts + `node --test` .mjs, run by `scripts/run_tests.sh`.

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-07-24-fable-scoped-tracking-design.md`. Every task's requirements implicitly include it.
- The scoped entry in `limits[]` is identified by `kind == "weekly_scoped"` **and** a present `scope.model` — never by array position (the array also holds `session` and `weekly_all`).
- Visible label = the current model, shown as `` `${model} only` `` (renders "Fable only"). Fallback `"Scoped"` when no model name is present.
- Column rename is **in place** (`ALTER TABLE ... RENAME COLUMN`), guarded by `PRAGMA table_info` so re-running `init()` is a no-op. History is preserved, not dropped.
- Backfill label for pre-rename rows is exactly `'Sonnet'`.
- No em-dashes / Claude-tell prose in commit messages (personal repo; `Co-Authored-By` is allowed).
- Tests run via `./scripts/run_tests.sh`; keep it green at every commit.
- Never restart or point tests at the live tracker on port 7474. Serve a frozen snapshot on a spare port (see Task 4).

---

### Task 1: Backend data layer — model field, API parse, storage + migration

`models.py`, `scraper_api.py`, and `db.py` are coupled by the field name, so they change together to keep the tree working. Two new Python check scripts prove the parse and the migration.

**Files:**
- Modify: `models.py` (UsageSample dataclass)
- Modify: `scraper_api.py` (`parse_payload`, add `_find_scoped_weekly`)
- Modify: `db.py` (schema SQL, `USAGE_RUNS_NEW_COLUMNS`, `_migrate_usage_runs_if_needed`, every `usage_runs` SQL column list, `_sample_matches_run`, `_run_insert_tuple`, `_run_row_to_dict`, `_expanded_row`)
- Modify: `scripts/collapse_check.py` (`make_run`, `RUN_COLUMNS`)
- Modify: `scripts/run_tests.sh` (register the two new checks)
- Create: `scripts/scoped_parse_check.py`
- Create: `scripts/scoped_migration_check.py`

**Interfaces:**
- Produces: `UsageSample.scoped_pct: Optional[float]`, `UsageSample.scoped_model: Optional[str] = None`.
- Produces: `scraper_api._find_scoped_weekly(limits: Any) -> dict[str, Any] | None` returning `{"percent": <num|None>, "model": <str|None>}` for the `weekly_scoped` entry, else `None`.
- Produces: `usage_runs` columns `scoped_pct REAL`, `scoped_model TEXT`; chart/last-sample row dicts carry both keys.
- Consumes: existing `_normalize_usage_api_pct` (clamps/floats, handles int `42`).

- [ ] **Step 1: Write the failing parse check**

Create `scripts/scoped_parse_check.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python scripts/scoped_parse_check.py`
Expected: FAIL / traceback — `UsageSample` has no `scoped_pct` yet (`AttributeError` or a TypeError on the field).

- [ ] **Step 3: Rename the model field in `models.py`**

In `models.py`, in the `UsageSample` dataclass, replace:

```python
    sonnet_pct: Optional[float] = None
```

with:

```python
    scoped_pct: Optional[float] = None
    scoped_model: Optional[str] = None
```

- [ ] **Step 4: Read the scoped limit in `scraper_api.py`**

In `scraper_api.py`, add this helper above `parse_payload`:

```python
def _find_scoped_weekly(limits: Any) -> dict[str, Any] | None:
    """The per-model weekly cap, identified by kind + a present scope.model.

    limits[] also holds the unscoped session/weekly_all entries, so match on
    kind rather than position.
    """
    if not isinstance(limits, list):
        return None
    for entry in limits:
        if not isinstance(entry, dict) or entry.get("kind") != "weekly_scoped":
            continue
        model = (entry.get("scope") or {}).get("model") or {}
        if model:
            return {"percent": entry.get("percent"), "model": model.get("display_name")}
    return None
```

In `parse_payload`, delete the `seven_day_sonnet` line:

```python
    seven_day_sonnet = payload.get("seven_day_sonnet") or {}
```

and add near the other lookups:

```python
    scoped = _find_scoped_weekly(payload.get("limits"))
```

Then in the returned `UsageSample(...)`, replace the `sonnet_pct=...` line with:

```python
        scoped_pct=_normalize_usage_api_pct(scoped["percent"]) if scoped else None,
        scoped_model=scoped["model"] if scoped else None,
```

- [ ] **Step 5: Run the parse check to verify it passes**

Run: `python scripts/scoped_parse_check.py`
Expected: PASS — all six checks ok.

- [ ] **Step 6: Write the failing migration check**

Create `scripts/scoped_migration_check.py`:

```python
#!/usr/bin/env python3
"""Assert the sonnet_pct -> scoped_pct migration renames, backfills, and is idempotent.

Plain asserts, run from scripts/run_tests.sh.
Run: python scripts/scoped_migration_check.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from db import UsageDB

# The pre-migration schema: usage_runs carrying the old sonnet_pct column.
OLD_SCHEMA = """
CREATE TABLE usage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start TEXT NOT NULL, ts_end TEXT NOT NULL, sample_count INTEGER NOT NULL,
  session_pct REAL, session_resets TEXT, weekly_pct REAL, weekly_resets TEXT,
  extra_pct REAL, extra_enabled INTEGER, extra_used_credits REAL,
  extra_monthly_limit REAL, sonnet_pct REAL
);
"""

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(label)
        print(f"  FAIL {label}")


def columns(conn) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(usage_runs)").fetchall()}


print("sonnet_pct -> scoped_pct migration:")

with tempfile.TemporaryDirectory() as tmp:
    path = pathlib.Path(tmp) / "usage.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        # One Sonnet-era row (non-null) and one null row.
        conn.execute("INSERT INTO usage_runs (ts_start, ts_end, sample_count, sonnet_pct) "
                     "VALUES ('t0','t0',1, 12.5)")
        conn.execute("INSERT INTO usage_runs (ts_start, ts_end, sample_count, sonnet_pct) "
                     "VALUES ('t1','t1',1, NULL)")
        conn.commit()

    UsageDB(path).init()

    with sqlite3.connect(path) as conn:
        cols = columns(conn)
        check("sonnet_pct column gone", "sonnet_pct" not in cols)
        check("scoped_pct column present", "scoped_pct" in cols)
        check("scoped_model column present", "scoped_model" in cols)
        rows = conn.execute("SELECT scoped_pct, scoped_model FROM usage_runs "
                            "ORDER BY id").fetchall()
        check("non-null pct preserved", rows[0][0] == 12.5)
        check("non-null row backfilled 'Sonnet'", rows[0][1] == "Sonnet")
        check("null-pct row left unlabelled", rows[1][1] is None)

    # Idempotent: a second init() must not error or relabel.
    UsageDB(path).init()
    with sqlite3.connect(path) as conn:
        labels = [r[0] for r in conn.execute("SELECT scoped_model FROM usage_runs "
                                              "ORDER BY id").fetchall()]
        check("idempotent re-init keeps labels", labels == ["Sonnet", None])

print()
if failures:
    print(f"scoped_migration_check: {len(failures)} FAILED")
    sys.exit(1)
print("scoped_migration_check: all passed")
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python scripts/scoped_migration_check.py`
Expected: FAIL — `init()` currently only ADDs `sonnet_pct`; `scoped_pct`/`scoped_model` absent, no backfill.

- [ ] **Step 8: Update the schema and migration in `db.py`**

In `USAGE_RUNS_SCHEMA_SQL`, replace the `sonnet_pct REAL` line (last column) with:

```sql
  scoped_pct          REAL,
  scoped_model        TEXT
```

Replace `USAGE_RUNS_NEW_COLUMNS`:

```python
# Columns added after initial schema; applied via ALTER TABLE on existing DBs.
USAGE_RUNS_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("scoped_model", "TEXT"),
)
```

Replace `_migrate_usage_runs_if_needed` in full:

```python
    def _migrate_usage_runs_if_needed(self, conn: sqlite3.Connection) -> None:
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_runs)").fetchall()
        }
        # The model-scoped column used to hold Sonnet; it now holds whatever model
        # the API scopes the weekly cap to (Fable at time of writing). Rename in
        # place so the months of Sonnet history stay as one continuous series.
        needs_backfill = False
        if "sonnet_pct" in existing_columns and "scoped_pct" not in existing_columns:
            conn.execute("ALTER TABLE usage_runs RENAME COLUMN sonnet_pct TO scoped_pct")
            existing_columns.discard("sonnet_pct")
            existing_columns.add("scoped_pct")
            needs_backfill = True
        for column_name, column_type in USAGE_RUNS_NEW_COLUMNS:
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE usage_runs ADD COLUMN {column_name} {column_type}")
                existing_columns.add(column_name)
        if needs_backfill:
            # Every pre-rename non-null row was Sonnet.
            conn.execute(
                "UPDATE usage_runs SET scoped_model = 'Sonnet' WHERE scoped_pct IS NOT NULL"
            )
```

- [ ] **Step 9: Rename the column through the rest of `db.py`**

Apply these edits (every `usage_runs` column list and value tuple). In each SQL `SELECT`/`INSERT` column list that ends with `sonnet_pct`, replace `sonnet_pct` with `scoped_pct,\n scoped_model` (matching the existing indentation), and add a matching `?` to each `VALUES (...)` placeholder list. Specifically:

- `insert_sample`: the `SELECT ... LIMIT 1` list (ends `sonnet_pct`) → `scoped_pct, scoped_model`. The `INSERT INTO usage_runs (...)` list → `scoped_pct, scoped_model`; its `VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)` gains one `?` (13 total); the value tuple's trailing `normalized.sonnet_pct,` → `normalized.scoped_pct,\n normalized.scoped_model,`.
- `fetch_last_sample`: `SELECT` list `sonnet_pct` → `scoped_pct, scoped_model`.
- `fetch_chart_data`: both `SELECT` lists (all-window and windowed) `sonnet_pct` → `scoped_pct, scoped_model`.
- `_import_legacy_usage_log_if_needed`: the `INSERT INTO usage_runs (...)` list `sonnet_pct` → `scoped_pct, scoped_model`; its `VALUES (...)` gains one `?` (13 total). (The legacy `current_run` dict has neither key, so both insert as `None` via `_run_insert_tuple`.)

In `_sample_matches_run`, replace the last comparison:

```python
            and sample.sonnet_pct == run["sonnet_pct"]
```

with:

```python
            and sample.scoped_pct == run["scoped_pct"]
            and sample.scoped_model == run["scoped_model"]
```

In `_run_insert_tuple`, replace `run.get("sonnet_pct"),` with:

```python
            run.get("scoped_pct"),
            run.get("scoped_model"),
```

In `_expanded_row`, replace `"sonnet_pct": run.get("sonnet_pct"),` with:

```python
            "scoped_pct": run.get("scoped_pct"),
            "scoped_model": run.get("scoped_model"),
```

(`_run_row_to_dict` uses `dict(row)`, so it picks up both columns automatically once the SELECTs include them.)

- [ ] **Step 10: Update `scripts/collapse_check.py` for the renamed columns**

In `make_run`, replace `"sonnet_pct": None,` with:

```python
        "scoped_pct": None,
        "scoped_model": None,
```

In `RUN_COLUMNS`, replace the trailing `"sonnet_pct",` with:

```python
    "extra_used_credits", "extra_monthly_limit", "scoped_pct", "scoped_model",
```

(Replace the whole `"extra_used_credits", "extra_monthly_limit", "sonnet_pct",` line.)

- [ ] **Step 11: Register the new checks in `scripts/run_tests.sh`**

After the `python scripts/collapse_check.py` line, add:

```bash
python scripts/scoped_parse_check.py
python scripts/scoped_migration_check.py
```

- [ ] **Step 12: Run the migration check, then the whole suite**

Run: `python scripts/scoped_migration_check.py`
Expected: PASS — all checks ok.

Run: `./scripts/run_tests.sh`
Expected: PASS — `collapse_check`, `scoped_parse_check`, `scoped_migration_check` all pass, node suite green.

- [ ] **Step 13: Commit**

```bash
git add models.py scraper_api.py db.py scripts/collapse_check.py scripts/run_tests.sh scripts/scoped_parse_check.py scripts/scoped_migration_check.py
git commit -m "Repoint model-scoped track to limits[] and migrate sonnet_pct -> scoped_pct

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Config + notification-settings plumbing rename

Pure identifier rename of the model-scoped notification settings. No behavior change; the app must still import and build. Your Sonnet notify values are empty/default, so nothing is lost.

**Files:**
- Modify: `config.py` (`RUNTIME_ENV_ORDER`, `RUNTIME_ENV_DEFAULTS`, `AppConfig` fields, `load_config`)
- Modify: `dashboard.py` (the `notify_sonnet_*` wiring)
- Modify: `web.py` (Python side only: `build_app`/`_render_page` params, settings normalize/save/load dicts, `__NOTIFY_SONNET_*__` substitutions)

**Interfaces:**
- Produces: config keys `NOTIFY_SCOPED_THRESHOLD_PCT`, `NOTIFY_EXPECTED_SCOPED_OVERRUN_ENABLED`; `AppConfig.notify_scoped_threshold_pct`, `AppConfig.notify_expected_scoped_overrun_enabled`.
- Produces: settings-dict keys `scoped_threshold_pct`, `expected_scoped_overrun_enabled` (shared with Task 3's JS).
- Produces: template tokens `__NOTIFY_SCOPED_THRESHOLD_PCT__`, `__NOTIFY_EXPECTED_SCOPED_OVERRUN_ENABLED__`.

- [ ] **Step 1: Apply the rename mapping across the three Python files**

Apply these exact identifier renames everywhere they appear in `config.py`, `dashboard.py`, and the **Python** portions of `web.py` (do not touch the JS string block yet — that is Task 3):

| Old | New |
|---|---|
| `NOTIFY_SONNET_THRESHOLD_PCT` | `NOTIFY_SCOPED_THRESHOLD_PCT` |
| `NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED` | `NOTIFY_EXPECTED_SCOPED_OVERRUN_ENABLED` |
| `notify_sonnet_threshold_pct` | `notify_scoped_threshold_pct` |
| `notify_expected_sonnet_overrun_enabled` | `notify_expected_scoped_overrun_enabled` |
| `__NOTIFY_SONNET_THRESHOLD_PCT__` | `__NOTIFY_SCOPED_THRESHOLD_PCT__` |
| `__NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED__` | `__NOTIFY_EXPECTED_SCOPED_OVERRUN_ENABLED__` |
| `"sonnet_threshold_pct"` (settings dict key) | `"scoped_threshold_pct"` |
| `"expected_sonnet_overrun_enabled"` (settings dict key) | `"expected_scoped_overrun_enabled"` |
| `"Sonnet threshold"` (error label in `_normalize_threshold_value` call) | `"Scoped threshold"` |

Concretely this covers: `config.py` lines in `RUNTIME_ENV_ORDER`, `RUNTIME_ENV_DEFAULTS`, the `AppConfig` field declarations, and `load_config`; `dashboard.py` the two `notify_sonnet_*` keyword args; `web.py` the `_render_page`/`build_app` parameter list, the settings normalize/response dicts (the `sonnet_threshold_pct` / `expected_sonnet_overrun_enabled` object keys on the Python side), and the two `.replace("__NOTIFY_SONNET_*__", ...)` substitution calls.

- [ ] **Step 2: Verify the modules import and the page renders**

Run:

```bash
python -c "import config, dashboard, web; print('import ok')"
grep -n "SONNET\|sonnet" config.py dashboard.py | grep -v "^.*#"
```

Expected: `import ok`, and the second grep prints nothing (no stray Sonnet identifiers left in `config.py`/`dashboard.py`).

- [ ] **Step 3: Run the suite (still green — no test references these yet)**

Run: `./scripts/run_tests.sh`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add config.py dashboard.py web.py
git commit -m "Rename NOTIFY_SONNET_* settings to NOTIFY_SCOPED_*

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — dynamic label + `sonnet` → `scoped`

The `web.py` JS block gains a helper that resolves the current model name from the rows, renames every `sonnet` reference, and reads `row.scoped_pct` / `row.scoped_model`. The scoped expected-line trace is computed for the table/notifications only and never added to the chart (unchanged), so its `name` is not user-visible.

**Files:**
- Modify: `web.py` (the JS string block only)

**Interfaces:**
- Consumes (from Task 1): `row.scoped_pct`, `row.scoped_model` on every chart row and `latest`.
- Consumes (from Task 2): JS globals `notifyScopedThresholdPct`, `notifyExpectedScopedOverrunEnabled`; settings keys `scoped_threshold_pct`, `expected_scoped_overrun_enabled`.
- Produces: `resolveScopedModel(rows) -> string` (last non-null `scoped_model`, else `"Scoped"`).

- [ ] **Step 1: Add the `resolveScopedModel` helper**

In the `web.py` JS block, immediately before `function renderChart` (or above `computeExpectedScopedTrace`), add:

```javascript
// The visible label for the model-scoped series. The API scopes the weekly cap
// to one model at a time (Sonnet historically, Fable now) and names it per row;
// use the most recent non-null name, falling back to a neutral word.
function resolveScopedModel(rows) {
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i] && rows[i].scoped_model) return rows[i].scoped_model;
  }
  return 'Scoped';
}
```

- [ ] **Step 2: Rewrite the summary-table scoped row**

In `renderSummaryTable`, replace the `// Sonnet-only row` block (the `if (latest && latest.sonnet_pct ...)` push) with:

```javascript
  // Model-scoped row: only show when data is present, before Extra usage.
  if (latest && latest.scoped_pct !== null && latest.scoped_pct !== undefined) {
    const scopedLabel = latest.scoped_model || 'Scoped';
    rows.push({
      metric: `${scopedLabel} only`,
      usage: fmtPct(latest.scoped_pct),
      reset: fmtReset(latest.weekly_resets),
      alert: makeThresholdAlert('scoped_threshold_pct', alertSettingsDraft.scoped_threshold_pct),
      expected: fmtPct(expectedScopedNowPct),
      rawExpected: expectedScopedNowPct ?? null,
      rawUsage: latest.scoped_pct ?? null,
      overrunAlert: makeOverrunAlert('expected_scoped_overrun_enabled', alertSettingsDraft.expected_scoped_overrun_enabled)
    });
  }
```

Also rename the function's 4th parameter `expectedSonnetNowPct` → `expectedScopedNowPct` in the `function renderSummaryTable(...)` signature.

- [ ] **Step 3: Rewrite the notification threshold block**

Rename the `maybeNotifyThresholds` 4th parameter `expectedSonnetNowPct` → `expectedScopedNowPct`, and replace its `if (latest.sonnet_pct ...)` block with:

```javascript
  if (latest.scoped_pct !== null && latest.scoped_pct !== undefined) {
    const scopedLabel = latest.scoped_model || 'Scoped';
    maybeNotifyThresholdCrossing(
      state,
      'scoped',
      latest.scoped_pct,
      notifyScopedThresholdPct,
      latest.weekly_resets || 'unknown',
      `Claudometer: ${scopedLabel} usage alert`,
      `${scopedLabel}-only usage reached ${fmtPct(latest.scoped_pct)}.`
    );
    maybeNotifyExpectedScopedOverrun(state, latest, expectedScopedNowPct);
  }
```

- [ ] **Step 4: Rewrite `maybeNotifyExpectedSonnetOverrun`**

Replace the whole function with:

```javascript
function maybeNotifyExpectedScopedOverrun(state, latest, expectedNowPct) {
  if (
    !notifyExpectedScopedOverrunEnabled ||
    latest.scoped_pct === null ||
    latest.scoped_pct === undefined ||
    expectedNowPct === null ||
    expectedNowPct === undefined
  ) {
    return;
  }

  const scopedLabel = latest.scoped_model || 'Scoped';
  const entryKey = latest.weekly_resets || 'unknown';
  const entry = state.expectedScopedOverrun || {};
  if (entry.key !== entryKey) {
    entry.key = entryKey;
    entry.alerted = false;
  }

  if (latest.scoped_pct > expectedNowPct) {
    if (!entry.alerted) {
      showNotification(
        `Claudometer: ${scopedLabel} usage above expected`,
        `${scopedLabel}-only usage is ${fmtPct(latest.scoped_pct)}, above the expected ${fmtPct(expectedNowPct)}.`,
        `expected-scoped-overrun:${entryKey}`
      );
      entry.alerted = true;
    }
  } else {
    entry.alerted = false;
  }

  state.expectedScopedOverrun = entry;
}
```

- [ ] **Step 5: Rename `computeExpectedSonnetTrace`**

Rename the function `computeExpectedSonnetTrace` → `computeExpectedScopedTrace`, update its comment/`name`/`hovertemplate` from "Sonnet" to "Scoped", and change its guard `r.sonnet_pct` → `r.scoped_pct`:

```javascript
// Scoped-model expected line: same active-hours settings and weekly_resets cycle
// (the scoped cap resets with weekly usage). Computed for the table/notifications
// only; not added as a chart trace.
function computeExpectedScopedTrace(rows) {
  if (!expectedLineEnabled || rows.length < 1) return null;
  if (!rows.some((r) => r.scoped_pct !== null && r.scoped_pct !== undefined)) return null;
```

and in its returned trace object:

```javascript
      name: 'Expected scoped usage',
      line: { color: 'rgba(192,132,252,0.65)', width: 2.0, dash: '3px,3px' },
      hovertemplate: 'Expected scoped: %{y:.1f}%<br>Time: %{x|%Y-%m-%d %H:%M}<extra>Expected scoped usage</extra>'
```

- [ ] **Step 6: Rewrite the chart trace block in `renderChart`**

Replace `const hasSonnet = rows.some((r) => r.sonnet_pct ...)` with:

```javascript
  const hasScoped = rows.some((r) => r.scoped_pct !== null && r.scoped_pct !== undefined);
```

Replace the `if (hasSonnet) { traces.push({... 'Sonnet only' ...}) }` block with:

```javascript
  if (hasScoped) {
    traces.push({
      x,
      y: seriesFor(rows, 'scoped_pct'),
      mode: 'lines+markers',
      name: `${resolveScopedModel(rows)} only`,
      line: { color: '#c084fc', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 25,
      cliponaxis: false
    });
  }
```

Replace the `expectedSonnetData` line and its two uses:

```javascript
  const expectedScopedData = hasScoped ? computeExpectedScopedTrace(rows) : null;
```

and in the `renderSummaryTable(...)` and `maybeNotifyThresholds(...)` calls, replace `expectedSonnetData ? expectedSonnetData.expectedNowPct : null` (both occurrences) with `expectedScopedData ? expectedScopedData.expectedNowPct : null`. Also update the comment at `// Sonnet expected line is identical...` to say "Scoped".

- [ ] **Step 7: Rename the remaining JS plumbing references**

Apply across the JS block (settings draft/save/load objects and globals):

| Old | New |
|---|---|
| `notifySonnetThresholdPct` | `notifyScopedThresholdPct` |
| `notifyExpectedSonnetOverrunEnabled` | `notifyExpectedScopedOverrunEnabled` |
| `sonnet_threshold_pct` (JS object key) | `scoped_threshold_pct` |
| `expected_sonnet_overrun_enabled` (JS object key) | `expected_scoped_overrun_enabled` |

Then confirm nothing is left:

```bash
grep -ni "sonnet" web.py
```

Expected: no matches.

- [ ] **Step 8: Regenerate the extracted JS and run the suite**

Run: `./scripts/run_tests.sh`
Expected: PASS. (`extract_js.py` does not extract any scoped/sonnet function, so `shipped.mjs` and the node tests are unaffected; this step confirms no syntax break in the regenerated module and that `collapse_check`/`scoped_*` checks still pass.)

- [ ] **Step 9: Commit**

```bash
git add web.py
git commit -m "Label the model-scoped series dynamically (Fable) and rename sonnet -> scoped in the UI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: End-to-end verification against a served snapshot

No production code. Prove the "Fable only" row and trace render, the legend toggle still works, and the settings round-trip — driving a served copy of the real DB, never the live 7474 tracker. Follow the repo's serve-only verification memory.

**Files:**
- None (verification only). Optionally Create: `docs/superpowers/specs/notes` — skip unless findings warrant.

- [ ] **Step 1: Freeze a DB snapshot**

Run:

```bash
cp ~/.claude-usage-tracker/usage.db /tmp/claude-1000/-home-angel-code-personal-claudometer/06028cd5-4510-4941-9c90-12ac909c9bc1/scratchpad/usage-snapshot.db
```

- [ ] **Step 2: Migrate the snapshot in isolation and confirm the data**

Run:

```bash
python -c "
import pathlib
from db import UsageDB
p = pathlib.Path('/tmp/claude-1000/-home-angel-code-personal-claudometer/06028cd5-4510-4941-9c90-12ac909c9bc1/scratchpad/usage-snapshot.db')
UsageDB(p).init()
import sqlite3
c = sqlite3.connect(p)
print('cols:', [r[1] for r in c.execute('PRAGMA table_info(usage_runs)')])
print('sonnet-era backfilled:', c.execute(\"SELECT COUNT(*) FROM usage_runs WHERE scoped_model='Sonnet'\").fetchone()[0])
print('fable rows:', c.execute(\"SELECT COUNT(*) FROM usage_runs WHERE scoped_model='Fable'\").fetchone()[0])
"
```

Expected: columns include `scoped_pct`, `scoped_model`, no `sonnet_pct`; ~4188 rows labelled `Sonnet`; Fable rows accrue once the live tracker writes new samples (may be 0 on a stale snapshot).

- [ ] **Step 3: Serve the snapshot on a spare port**

Serve `web.py` against the snapshot on a non-7474 port using `load_config()` (mirror `dashboard.py`; do not hardcode a harness config — see the serve-only memory). Confirm it listens, e.g. on `127.0.0.1:7475`.

- [ ] **Step 4: Drive the page with venv Playwright**

Load the served page. Verify:
- A summary-table row reads **"Fable only"** (or "Sonnet only"/no row if the snapshot predates Fable and has no recent scoped data — note which).
- The chart shows the scoped trace under the current-model label.
- Clicking the scoped legend entry hides only its series; the reset-marker toggles from #9 still work.
- Open the settings panel, set the scoped threshold, save, reload: the value round-trips (writes `NOTIFY_SCOPED_THRESHOLD_PCT`).

- [ ] **Step 5: Clean up by port**

Stop the spare-port server by its port/PID (never `pkill -f`, which kills the Bash tool's own shell — see the memory). Leave the live 7474 service untouched.

- [ ] **Step 6: Final full-suite run**

Run: `./scripts/run_tests.sh`
Expected: PASS.

---

## Self-Review

**Spec coverage:**
- Data source (`limits[]` weekly_scoped) → Task 1 Steps 4–5. ✓
- Schema rename + `scoped_model` + backfill + RLE run-equality → Task 1 Steps 8–9. ✓
- Backend `sonnet`→`scoped` (models/config/dashboard/web Python) → Task 1 (models) + Task 2. ✓
- Frontend dynamic label + rename + expected trace + notifications → Task 3. ✓
- Tests: collapse update, parse test, migration test → Task 1 Steps 1, 6, 10; browser verify → Task 4. ✓
- "Fable only" current-model label, `"Scoped"` fallback → Task 3 Steps 1–2, 6. ✓
- Out-of-scope (multi-scoped, burn charts) → untouched. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code or an exact rename mapping. The rename-mapping tables are complete identifier lists, not vague instructions.

**Type consistency:** `scoped_pct: Optional[float]`, `scoped_model: Optional[str]` used identically in `models.py`, `db.py` SQL, row dicts, and JS `row.scoped_pct`/`row.scoped_model`. Helper `resolveScopedModel(rows)` defined in Task 3 Step 1 and consumed in Step 6. `expectedScopedNowPct` param renamed consistently in Steps 2, 3, 6. Settings keys `scoped_threshold_pct` / `expected_scoped_overrun_enabled` match between Task 2 (Python) and Task 3 (JS). `_find_scoped_weekly` return shape (`{"percent", "model"}`) matches its consumer in `parse_payload`.
