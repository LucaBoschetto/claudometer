# Range=All render performance — design

## Summary

Stop expanding run-length-encoded history into per-sample rows when the user
asks for `range=all`. The dashboard currently ships ~100k chart rows (28.8 MB of
JSON) that carry only ~5.5k distinct readings; collapsing them to run endpoints
cuts the payload ~9x and the Plotly point count with it, while drawing the same
usage chart.

The zoomed presets (`today`, `weekly_cycle`) keep the existing per-sample
expansion unchanged.

## Motivation

Measured 2026-07-16 against the real DB (~100.7k samples, 1280px viewport). The
tracker writes continuously, so sample counts drift by a few rows between
measurements taken minutes apart; figures below are exact per run, not
cross-comparable to the last digit.

| what | cost |
|---|---|
| `renderChart(rows)` at `range=all` | **4,521 ms** |
| `/data.json?range=all` payload | **28.83 MB** |
| `computeBurnRate` x2 | 46 ms (1%) |

The burn-rate math is not the bottleneck. The cost is Plotly drawing ~100k
points across ~6 traces, plus the browser downloading and parsing ~29 MB first.

## Key finding: the rows are mostly fabricated

`usage_runs` is effectively run-length encoded — a row means "this reading held
steady for N polls". `_expand_run` inflates 5,499 stored runs into 100,744 chart
rows, and `_expanded_row` copies the *same* values into every expanded point,
**inventing evenly-spaced timestamps** for them. Those intermediate points were
never real poll times.

Grouping the expanded rows on all non-`ts` fields yields exactly 5,499 groups —
the stored run count. So no two adjacent runs are safely mergeable, and the
correct collapse is simply *the endpoints of each stored run*.

`_expand_run` pins its first point at `ts_start` and its last at `ts_end`
(db.py), so emitting those two points reproduces the identical polyline:
same vertices, same slanted connectors between runs. The usage chart is
therefore **lossless**, not downsampled.

| | rows | payload |
|---|---|---|
| today | 100,754 | 28.84 MB |
| endpoints only | 9,368 | 2.78 MB |

That 9,368 is the *lossless-collapse baseline*, not what ships. The burn chart
forces decay points on top of it (next section), so the shipped figure is
~11.1k rows / ~3.2 MB.

## The burn chart constrains the collapse

`computeBurnRate` is time-weighted (`weighted += slope[j] * stepMs`, then
`weighted / weight`), so subdividing a segment into N sub-steps preserves the
average exactly. Within a run every value is equal, so intra-run slopes are 0
and the only nonzero slopes live *between* runs — identical in both
representations. Verified against the shipped function over the real DB:
**9,368 points compared, 0 mismatches, maxDiff 0.**

But burn is *derived*, and unlike the raw values it is not constant within a
run: after a step it decays over `BURN_WINDOW_MINUTES`, then sits at zero. Run
endpoints alone do not sample that decay. A 2.6-day idle run draws its
30-minute decay as a 2.6-day diagonal ramp (worst error **236.7 %/hr** against a
true range of 0–304 %/hr).

Adding 4 decay points across the first 30 minutes of long runs caps every gap at
7.5 minutes and bounds the error:

| strategy | points | vs today | mean err | worst err |
|---|---|---|---|---|
| collapse only | 9,368 | 10.8x | 3.750 | 236.7 |
| collapse + 4 decay | 11,076 | 9.1x | 0.145 | 40.2 |
| hybrid k=30 | 34,913 | 2.9x | 0.002 | 1.7 |

At `range=all`, 69 days across ~600px is ~2.8 hours per pixel, so a 7.5-minute
gap is **0.04px**. The residual error is invisible at the only range that
collapses. It becomes visible only under manual zoom, which Plotly serves
client-side without refetching — a known, accepted trade-off (see Follow-up).

## Design

`fetch_chart_data(range_preset)` already knows the range, so the decision lands
where the range is already resolved:

```
range=all           -> _collapse_run   -> ~11.1k rows (~3.2 MB)
range=today         -> _expand_run     -> ~1.4k rows   (unchanged)
range=weekly_cycle  -> _expand_run     -> ~2.6k rows   (unchanged)
```

`_expand_run` stays. It is the correct behaviour when the window is narrow and
fidelity is visible; it is wrong only when the window is wide.

`_collapse_run(run)`, per run:

| run | points emitted |
|---|---|
| `sample_count == 1` | 1, at `ts_start` |
| degenerate (`ts_end <= ts_start`, unparseable) | 1, at `ts_start` |
| duration <= `BURN_WINDOW_MINUTES` | 2 — `ts_start`, `ts_end` |
| duration > `BURN_WINDOW_MINUTES` | 6 — `ts_start`, 4 decay points across the first window, `ts_end` |

The degenerate case is a deliberate divergence: `_expand_run` today emits
`sample_count` duplicate points **at the same instant**, which is visually
identical to one point.

The decay points exist to bound the gap, not to add detail. Without them the
burn chart invents multi-day ramps.

Markers land on real inflection points as a consequence: the surviving points
*are* the run corners. Today's dense marker band sits on fabricated timestamps,
so this is also more honest about what the data is.

### One source of truth for the burn window

The collapse depends on the burn window, which is a charting detail
(`windowMinutes = 30` in `computeBurnRate`). Two copies would drift, so
`BURN_WINDOW_MINUTES` lives in `config.py` and feeds `db.py`, the served JS, and
the tests.

`web.py` already templates Python values into the JS (`__POLL_INTERVAL_SECONDS__`),
so the JS reads a module-global rather than a templated default parameter:

```js
const BURN_WINDOW_MINUTES = __BURN_WINDOW_MINUTES__;
function computeBurnRate(rows, values, windowMinutes = BURN_WINDOW_MINUTES) {
```

A templated *default parameter* would break the tests: `extract_js.py` copies
function text verbatim without substituting, so `shipped.mjs` would carry the
raw placeholder and throw `ReferenceError` on every 2-arg call. Instead
`extract_js.py` emits the constant into its preamble, exactly as it already
stubs the `viewMode` module-global.

## Placement in the code

- `config.py` — add `BURN_WINDOW_MINUTES = 30`.
- `db.py` — import it; add `_collapse_run` beside the untouched `_expand_run`;
  `fetch_chart_data` selects on `range_preset == "all"`. Decay-point count is a
  named constant.
- `web.py` — add the `BURN_WINDOW_MINUTES` JS const + substitution; debounce the
  resize handler.
- `scripts/extract_js.py` — emit `BURN_WINDOW_MINUTES` into the preamble.
- `scripts/burn_rate.test.mjs` — add the subdivision-invariance test.
- `scripts/collapse_check.py` — new; plain asserts, wired into `run_tests.sh`.

### Resize debounce

`window.addEventListener('resize', () => rerenderChartWithLoading(currentRows))`
has no debounce, so dragging a resize queues a render per event. Pre-existing
and separate from the expansion, but it serves the same goal. Its own commit.

## Scope

In scope:

- Collapse `range=all` to run endpoints + decay points.
- `BURN_WINDOW_MINUTES` in `config.py`, consumed by db/JS/tests.
- Resize debounce.
- Tests for the collapse and the invariance property.

Out of scope (YAGNI):

- `scattergl` / WebGL. Only if the measured render disappoints; then it is a
  follow-up, not scope creep here.
- A true step chart (`line_shape: 'hv'`). More honest than today's diagonal
  connectors, but a visible change.
- Decoupling the usage and burn charts (see Follow-up).
- Any schema change. `usage_runs` is already the right shape.
- Changing `_expand_run` behaviour for the zoomed presets.

## Testing

- `scripts/burn_rate.test.mjs` — existing 9 tests must pass untouched. Add a
  test pinning subdivision invariance: collapsing a flat run to its endpoints
  must not change the burn rate. This is the property the design rests on.
- `scripts/collapse_check.py` — the collapse is Python and no JS test can see a
  bug in it. Plain asserts over the four `_collapse_run` cases, run from
  `run_tests.sh` alongside node.

`total_samples` and `filtered_samples` are computed from runs, not rows, so the
"Samples: N" readout is unaffected. Worth asserting, because it looks like
something this change would break.

## Verification

Via the serve-only harness on port 7475 against the real DB — never the live
7474 tracker.

1. Time `renderChart` at `range=all`. Baseline 4,521 ms; expect ~500 ms.
2. Screenshot the usage chart at `range=all` before/after: must be
   **pixel-identical**. This is the falsifiable form of the lossless claim.
3. `./scripts/run_tests.sh` green.

If (1) disappoints, `scattergl` becomes a follow-up rather than growing this PR.

## Known trade-off and follow-up

The burn chart at `range=all` is not pixel-identical: errors up to ~40 %/hr
exist, sub-pixel (0.04px) at that zoom, visible only under manual zoom. This is
accepted here and called out in the PR description.

The root cause is that the usage chart and the burn charts have genuinely
different resolution needs — the burn series has curvature exactly where the raw
series is flat. A follow-up PR should rethink this properly, likely by
decoupling the two charts so the burn charts get their own series rather than
riding on the usage chart's rows.
