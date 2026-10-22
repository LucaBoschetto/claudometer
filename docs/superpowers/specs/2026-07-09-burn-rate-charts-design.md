# Burn-rate charts — design

## Summary

Add two stacked mini-charts below the main usage chart on the Claudometer
dashboard, showing the *rate* at which usage climbs (the derivative of
utilization over time) in percentage-points per hour (%/hr):

- **Current session** burn rate (over the 5-hour window)
- **Weekly** burn rate (over the 7-day window)

Each mini-chart shows history across the selected range, and the current burn
rate is surfaced in that chart's header (e.g. "Current session — 4.2 %/hr now").
The current burn rate is also shown as a value in the Summary table, on the
Current session and Weekly rows, in %/hr.

## Motivation

The dashboard shows *how much* has been used, but not *how fast*. Burn rate
answers "am I spending faster than sustainable?" and lets the user eyeball
whether a session or the week is about to run out at the current pace.

## Scope

In scope:

- Session and weekly burn-rate history charts.
- Current (latest) burn-rate value shown per chart.
- Current burn-rate value in the Summary table (Current session + Weekly rows).
- Reuse of existing fetched data — no backend/DB changes.

Out of scope (YAGNI):

- Burn-rate alerts or thresholds.
- Sonnet-only and Extra-usage burn charts.
- A user-facing control for the smoothing window size (fixed constant).
- Any new backend endpoint or schema change.

## Placement in the code

Everything is client-side in `web.py` (the HTML + JS dashboard). The rows fed to
`renderChart` already carry `ts`, `session_pct`, and `weekly_pct`, so burn rate
is derived entirely in the browser from data already fetched. No changes to
`db.py`, `scraper_api.py`, or the HTTP handlers.

## Computation

New function `computeBurnRate(rows, values, windowMinutes = 30)`, where `values`
is the already view-adjusted series from `seriesFor(rows, key)` rather than a raw
row field, so the burn rate follows the Raw/Cleaned/Smoothed selector and an
isolated spike does not read as a huge slope (and then as a reset on its way
back down):

1. **Adjacent slopes.** For each consecutive pair of samples, compute
   `(pct[i] - pct[i-1]) / hoursBetween(i-1, i)` in %/hr.
2. **Reset handling.** When `pct` drops (a session/weekly window reset), that
   step's slope is dropped (`null`), not plotted as a large negative value. Burn
   rate is ≥ 0 within a window. A simple negative-delta check is sufficient;
   the `session_resets` / `weekly_resets` timestamps in each row are available
   if finer boundary detection is ever needed.
3. **Rolling window.** Each output point's burn rate is the time-weighted
   average of adjacent slopes falling within the trailing ~30 minutes, so the
   line is calm but still tracks recent activity. Each step's weight is clipped
   at the window boundary, so a step straddling it (e.g. one long gap after the
   machine slept) contributes only its in-window duration. Points with no valid
   slopes in the window (e.g. right after a reset) are `null`.

The result is an array aligned to the same x (time) axis as the source rows,
suitable for a Plotly trace.

## Rendering

- A new `<section class="panel">` is added after the main chart panel,
  containing two chart divs: `#burn-chart-session` and `#burn-chart-weekly`.
- Each is its own `Plotly.newPlot` / `Plotly.react` call with an auto-scaled
  y-axis (so the ~30x scale difference between session and weekly each read
  clearly), sharing the main chart's theme and x-range behavior.
- Line colors match the main chart: session `#1f8deb`, weekly `#ff6a2b`.
- A new `renderBurnCharts(rows, sessionBurnSeries, weeklyBurnSeries, xaxisLayout)`
  is called from the existing refresh path (from `renderChart`), so the burn
  charts update live alongside everything else and respond to range-preset
  changes. Manual zoom/pan on the main chart is mirrored onto them from its
  `plotly_relayout` handler, since they are separate plots.
- Each chart's header shows the current burn rate, meaning the latest aligned
  point (`currentBurn`). When the newest sample is a reset that point is `null`
  and the header reads `-`; it deliberately does not walk back to an earlier
  slope, which would report stale pre-reset burn as if it were current.
- `renderSummaryTable` gains a burn-rate value (%/hr) on the Current session and
  Weekly rows, taken from that same latest aligned point.
  Whether it is a new column or appended to the existing Usage cell is an
  implementation detail chosen to fit the table's current layout.

## Testing

The repo has no JS test harness. Verification is manual: run the dashboard
against the live SQLite DB and confirm:

- Slopes look correct (a climbing usage line yields a positive burn rate).
- Window resets produce a gap, not a negative spike.
- Charts update live and follow the range-preset selector.
- The Summary table burn-rate value matches the latest point on the chart.
- Layout is reasonable on both wide and compact viewports.

Results shown to the user before the work is called done.
