# Swap Sonnet tracking for the generic scoped-model track — design

## Summary

The tracker's "Sonnet only" series reads `seven_day_sonnet.utilization` from the
usage API. That field is now `None`: Anthropic moved the per-model weekly limit
out of a named top-level field and into a generic `limits[]` array, where the
active entry currently scopes to **Fable**.

Rather than rename Sonnet to Fable and repeat this migration the next time the
scoped model rotates, the track becomes **model-agnostic**: it follows whatever
`weekly_scoped` limit the API reports and labels itself from the model name the
API hands back. Today that reads "Fable only"; if the scope rotates again, the
label follows with no code change.

The months of historical Sonnet data already recorded stay as one continuous
series — the same track was Sonnet then and is Fable now.

## Why the old field is dead

`parse_payload` reads `payload["seven_day_sonnet"]["utilization"]`. The live
payload still has the key, but its value is `None`. The per-model limit now lives
in a sibling `limits` array:

```json
{
  "kind": "weekly_scoped",
  "group": "weekly",
  "percent": 42,
  "severity": "normal",
  "resets_at": "2026-07-28T07:59:59+00:00",
  "scope": { "model": { "id": null, "display_name": "Fable" } },
  "is_active": true
}
```

The `limits` array also carries the unscoped `session` and `weekly_all` entries
(which duplicate `five_hour` / `seven_day`), so the scoped entry is identified by
`kind == "weekly_scoped"` plus a present `scope.model`, not by position.

## Decisions

- **Generic + dynamic label.** Track the `weekly_scoped` limit whatever its
  model; read the label live from `scope.model.display_name`. No hardcoded
  "Fable" in field or column names.
- **One continuous scoped track.** The existing `sonnet_pct` column (4,188
  non-null rows of real Sonnet history) is renamed, not abandoned. Historical
  rows are backfilled with the model label `Sonnet`; new rows carry `Fable`.
- **Legend shows the current model only.** A Plotly trace has a single legend
  name, so the continuous series shows under one label — the latest model
  (`Fable only`). The Sonnet-era points live under that trace. This is the
  accepted reading of "dynamic label = whatever it is now"; an era-neutral label
  was considered and declined.

## Data source

`scraper_api.parse_payload` stops reading `seven_day_sonnet` and instead scans
`payload["limits"]` for the entry where `kind == "weekly_scoped"` and
`scope.model` is present:

- `scoped_pct` ← `percent`, through the existing `_normalize_usage_api_pct`
  (which already clamps and floats an int like `42`).
- `scoped_model` ← `scope.model.display_name`.

When no such entry exists, both are `None` — the same null-safe path the code
takes today, so an account without a scoped limit simply shows no scoped row.

## Schema and migration

Table `usage_runs` (run-length encoded). One guarded, idempotent migration:

1. `ALTER TABLE usage_runs RENAME COLUMN sonnet_pct TO scoped_pct`, guarded by a
   `PRAGMA table_info` check so a second run is a no-op (SQLite ≥ 3.25).
2. `ALTER TABLE usage_runs ADD COLUMN scoped_model TEXT` (guarded the same way).
3. Backfill: `UPDATE usage_runs SET scoped_model = 'Sonnet'
   WHERE scoped_pct IS NOT NULL AND scoped_model IS NULL`. The existing non-null
   rows were Sonnet; going-forward rows get their label from the API.

The RLE run-equality comparison (`db.py`, currently comparing `sonnet_pct`) also
compares `scoped_model`, so the Sonnet→Fable handoff breaks a run instead of
merging two models into one collapsed run.

## Backend plumbing (mechanical `sonnet` → `scoped`)

- `models.py`: `sonnet_pct` → `scoped_pct`; add `scoped_model: Optional[str] = None`.
- `config.py`: `NOTIFY_SONNET_THRESHOLD_PCT` → `NOTIFY_SCOPED_THRESHOLD_PCT`,
  `NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED` → `NOTIFY_EXPECTED_SCOPED_OVERRUN_ENABLED`,
  and the matching `AppConfig` fields (`notify_scoped_threshold_pct`,
  `notify_expected_scoped_overrun_enabled`). The current runtime values are
  empty/default, so no saved setting is lost.
- `dashboard.py`: the `notify_sonnet_*` wiring follows the rename.
- `web.py` (Python): the config plumbing and the `__NOTIFY_SONNET_*__` template
  substitutions follow the rename; the row dict served to the frontend gains
  `scoped_model`.

## Frontend (`web.py` JS)

- A helper resolves the current label: the latest non-null `scoped_model`,
  falling back to `"Scoped"` if ever absent.
- The summary-table row and the chart trace named `"Sonnet only"` become
  `` `${model} only` `` (renders "Fable only"). Field reads `row.sonnet_pct` →
  `row.scoped_pct`; `hasSonnet` → `hasScoped`.
- `computeExpectedSonnetTrace` → `computeExpectedScopedTrace`, trace name
  `` `Expected ${model} usage` ``. It stays keyed to the `weekly_resets` cycle —
  the scoped `resets_at` matches the weekly reset.
- Notifications: title `` `Claudometer: ${model} usage alert` ``, body
  `` `${model}-only usage reached X%` `` and the expected-overrun equivalents.
  Alert-setting keys `sonnet_threshold_pct` → `scoped_threshold_pct`,
  `expected_sonnet_overrun_enabled` → `expected_scoped_overrun_enabled`.

## Tests

- `scripts/collapse_check.py`: `sonnet_pct` → `scoped_pct`, and add
  `scoped_model` to the fields the `range=all` RLE collapse must preserve.
- New parse test (`scraper_api`): a payload with a `weekly_scoped` limit yields
  `scoped_pct = 42`, `scoped_model = "Fable"`; a payload without one yields both
  `None`; a payload whose only `limits` entries are `session`/`weekly_all` also
  yields `None` (the scoped entry is picked by kind, not position).
- New migration test: an old DB carrying `sonnet_pct` migrates to `scoped_pct`
  with `scoped_model` backfilled `'Sonnet'` on non-null rows, and re-running the
  migration is a no-op.
- Browser-driven verification against a served snapshot of the DB (never the
  live 7474 tracker): the "Fable only" row and trace appear, the expected line
  renders, and the legend toggle still works.
- Existing suite stays green.

## Out of scope

- Multiple simultaneous scoped models. The API returns exactly one
  `weekly_scoped` entry; supporting several is deferred until it does.
- Backfilling `scoped_model` on the going-forward rows from any source other than
  the API response.
- The burn-rate charts, which have never drawn the scoped series.
