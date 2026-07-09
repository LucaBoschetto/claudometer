# Burn-rate Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session and weekly "burn rate" (rate of utilization climb, in %/hr) to the Claudometer dashboard — a value in the Summary table plus two history mini-charts.

**Architecture:** Everything is client-side JavaScript embedded in `web.py`. Burn rate is derived in the browser from the already-fetched `rows` (each has `ts`, `session_pct`, `weekly_pct`) as a time-weighted trailing-window slope. No backend, DB, or endpoint changes.

**Tech Stack:** Python HTTP server serving a single HTML page with inline JS and Plotly.js. All edits are in `web.py`.

## Global Constraints

- No backend/DB/endpoint changes — burn rate is computed in-browser from existing `rows`.
- Session line color `#1f8deb`; weekly line color `#ff6a2b` (match the main chart).
- Burn rate is `≥ 0` within a window; a usage drop (reset) produces a gap, not a negative value.
- Smoothing window is a fixed constant (`30` minutes), not a UI control.
- No JS test harness exists in this repo; verification is manual against the running dashboard (`./tracker.sh foreground` or an already-running instance at `http://127.0.0.1:7474`).
- Scope: session + weekly only. No Sonnet/Extra burn charts, no burn alerts.

---

### Task 1: Burn-rate computation + Summary table value

**Files:**
- Modify: `web.py` — add JS helpers near the other series helpers (after `seriesFor`, ~line 1700); extend the Summary table header (line 495-507) and `renderSummaryTable` (line 1152); compute the series in `renderChart` (line 1996) and pass the latest value to the table.

**Interfaces:**
- Produces (consumed by Task 2):
  - `computeBurnRate(rows, key, windowMinutes = 30) -> Array<number|null>` — burn-rate series aligned 1:1 with `rows`, in %/hr, `null` where undefined (first sample, gaps, resets).
  - `lastNonNull(series) -> number|null` — last non-null entry of a series.
  - `fmtBurn(v) -> string` — formats a %/hr number as `"4.2 %/hr"` or `"-"`.
  - In `renderChart`, the locals `sessionBurnSeries` and `weeklyBurnSeries` (both `computeBurnRate` results) are in scope at the point `renderSummaryTable` is called.

- [ ] **Step 1: Add the computation and format helpers**

In `web.py`, immediately after the `seriesFor` function (ends at line 1695, before `maskedSeries`), insert:

```javascript
// Burn rate: rate of utilization climb in percentage-points per hour.
// Each output point is the time-weighted average of adjacent-sample slopes
// within the trailing `windowMinutes`. A step where usage drops (a window
// reset, or a downward correction) is dropped, so a reset reads as a gap
// rather than a large negative spike. Result is aligned 1:1 with `rows`.
function computeBurnRate(rows, key, windowMinutes = 30) {
  const n = rows.length;
  const out = new Array(n).fill(null);
  if (n < 2) return out;

  const times = rows.map((r) => new Date(r.ts).getTime());

  // slope[i] = %/hr for the step ending at sample i (from i-1 to i).
  const slope = new Array(n).fill(null);
  for (let i = 1; i < n; i += 1) {
    const v0 = rows[i - 1][key];
    const v1 = rows[i][key];
    if (v0 == null || v1 == null) continue;
    if (Number.isNaN(times[i]) || Number.isNaN(times[i - 1])) continue;
    const dtHours = (times[i] - times[i - 1]) / 3600000;
    if (dtHours <= 0) continue;
    if (v1 < v0) continue; // reset / correction -> no valid burn rate
    slope[i] = (v1 - v0) / dtHours;
  }

  const windowMs = windowMinutes * 60 * 1000;
  for (let i = 1; i < n; i += 1) {
    if (Number.isNaN(times[i])) continue;
    let weighted = 0;
    let weight = 0;
    for (let j = i; j >= 1; j -= 1) {
      if (times[i] - times[j] > windowMs) break;
      if (slope[j] == null) continue;
      const stepMs = times[j] - times[j - 1];
      if (!(stepMs > 0)) continue;
      weighted += slope[j] * stepMs;
      weight += stepMs;
    }
    if (weight > 0) out[i] = weighted / weight;
  }
  return out;
}

function lastNonNull(series) {
  for (let i = series.length - 1; i >= 0; i -= 1) {
    if (series[i] != null) return series[i];
  }
  return null;
}

function fmtBurn(v) {
  return (v === null || v === undefined || Number.isNaN(v)) ? '-' : Number(v).toFixed(1) + ' %/hr';
}
```

- [ ] **Step 2: Add the "Burn rate" column to the table header**

In `web.py`, in the `#summary-table` `<thead>` (lines 497-504), add a `Burn rate` header right after `Usage`:

```html
          <tr>
            <th>Metric</th>
            <th>Usage</th>
            <th>Burn rate</th>
            <th>Expected</th>
            <th>Resets at (Local)</th>
            <th>Alert</th>
            <th>Overrun alert</th>
          </tr>
```

- [ ] **Step 3: Accept burn values in `renderSummaryTable` and attach them to the rows**

Change the signature (line 1152) from:

```javascript
function renderSummaryTable(latest, expectedSessionNowPct, expectedWeeklyNowPct, expectedSonnetNowPct) {
```

to:

```javascript
function renderSummaryTable(latest, expectedSessionNowPct, expectedWeeklyNowPct, expectedSonnetNowPct, sessionBurnNow, weeklyBurnNow) {
```

In the `rows` array (starts line 1175), add a `burn` field to the Current session and Weekly entries. For the Current session row object add:

```javascript
      burn: sessionBurnNow ?? null,
```

For the Weekly row object add:

```javascript
      burn: weeklyBurnNow ?? null,
```

(Leave the Sonnet-only and Extra-usage row objects without a `burn` field.)

- [ ] **Step 4: Render the burn cell in each row**

In the row template inside `summaryBodyEl.innerHTML = rows.map(...)` (lines 1226-1233), add a burn cell right after the Usage cell:

```javascript
      return `<tr>
        <td data-cell="metric">${row.metric}</td>
        <td data-label="Usage">${row.usage}</td>
        <td data-label="Burn rate">${row.burn === undefined || row.burn === null ? '-' : fmtBurn(row.burn)}</td>
        <td data-cell="expected" data-label="Expected"${hasExpected ? '' : ' class="cell-hidden"'}${expectedStyle}>${hasExpected ? row.expected : ''}</td>
        <td data-label="Resets at (Local)">${row.reset || '-'}</td>
        <td data-label="Alert">${row.alert}</td>
        <td data-label="Overrun alert"${hasExpected ? '' : ' class="cell-hidden"'}>${hasExpected ? row.overrunAlert : ''}</td>
      </tr>`;
```

- [ ] **Step 5: Compute the series in `renderChart` and pass latest values to the table**

In `renderChart`, in the empty-rows branch, update the call at line 2016 from:

```javascript
    renderSummaryTable(null, null, null, null);
```

to:

```javascript
    renderSummaryTable(null, null, null, null, null, null);
```

In the main path, just before the `renderSummaryTable(...)` call at line 2100, add the series computation (place it right after `const latest = rows[rows.length - 1];` at line 2095):

```javascript
  const sessionBurnSeries = computeBurnRate(rows, 'session_pct');
  const weeklyBurnSeries = computeBurnRate(rows, 'weekly_pct');
```

Then update the `renderSummaryTable(...)` call (lines 2100-2105) to pass the latest burn values:

```javascript
  renderSummaryTable(
    latest,
    expectedSessionData ? expectedSessionData.expectedNowPct : null,
    expectedData ? expectedData.expectedNowPct : null,
    expectedSonnetData ? expectedSonnetData.expectedNowPct : null,
    lastNonNull(sessionBurnSeries),
    lastNonNull(weeklyBurnSeries)
  );
```

- [ ] **Step 6: Verify manually in the dashboard**

Run (or reload) the dashboard:

```bash
./tracker.sh restart && ./tracker.sh status
```

Open `http://127.0.0.1:7474`. Expected:
- The Summary table shows a new **Burn rate** column.
- Current session and Weekly rows show a `X.X %/hr` value (or `-` if there is < 2 samples of history in range); Sonnet-only and Extra usage rows show `-`.
- The value is non-negative and roughly matches the visible steepness of the corresponding usage line.

- [ ] **Step 7: Commit**

```bash
git add web.py
git commit -m "Add burn-rate value to Summary table"
```

---

### Task 2: Session + weekly burn-rate mini-charts

**Files:**
- Modify: `web.py` — add a new `<section>` after the chart panel (after line 478); add CSS (near the `#chart` rules, ~line 740); add element constants (near line 920); add `renderBurnCharts` + `renderBurnChart` functions; call them from `renderChart` (empty branch and main path).

**Interfaces:**
- Consumes (from Task 1): `computeBurnRate`, `lastNonNull`, `fmtBurn`, and the `sessionBurnSeries` / `weeklyBurnSeries` locals in `renderChart`.
- Produces: `renderBurnCharts(rows, sessionBurnSeries, weeklyBurnSeries, xaxisLayout)`.

- [ ] **Step 1: Add the burn-rate chart section to the HTML**

In `web.py`, immediately after the closing `</section>` of the chart panel (line 478, before the `<section id="summary-wrap" ...>` at line 480), insert:

```html
    <section class="panel burn-panel">
      <div class="panel-header">
        <div>
          <p class="panel-kicker">Rate of change</p>
          <h2>Burn rate</h2>
        </div>
      </div>
      <div class="burn-charts">
        <div class="burn-chart-block">
          <div class="burn-chart-title">Current session <span id="burn-session-now" class="burn-now">-</span></div>
          <div id="burn-chart-session" aria-label="session burn rate chart"></div>
        </div>
        <div class="burn-chart-block">
          <div class="burn-chart-title">Weekly <span id="burn-weekly-now" class="burn-now">-</span></div>
          <div id="burn-chart-weekly" aria-label="weekly burn rate chart"></div>
        </div>
      </div>
    </section>
```

- [ ] **Step 2: Add CSS for the mini-charts**

In `web.py`, right after the `#chart { ... }` rule (ends line 740), insert:

```css
.burn-charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.burn-chart-title {
  font-size: 13px;
  color: var(--muted);
  padding: 4px 6px 0 6px;
}
.burn-now {
  color: var(--fg);
  font-weight: 600;
  margin-left: 6px;
}
#burn-chart-session,
#burn-chart-weekly {
  min-height: clamp(160px, 26vh, 260px);
  padding: 2px 6px 0 6px;
}
```

And in the compact `@media (max-width: 720px)` block (the one containing `#chart` at line 834), add a rule so the two charts stack:

```css
  .burn-charts { grid-template-columns: 1fr; }
```

- [ ] **Step 3: Add element constants**

In `web.py`, next to `const chartEl = document.getElementById('chart');` (line 920), add:

```javascript
const burnChartSessionEl = document.getElementById('burn-chart-session');
const burnChartWeeklyEl = document.getElementById('burn-chart-weekly');
const burnSessionNowEl = document.getElementById('burn-session-now');
const burnWeeklyNowEl = document.getElementById('burn-weekly-now');
```

- [ ] **Step 4: Add the render functions**

In `web.py`, immediately before `function renderChart(rows) {` (line 1996), insert:

```javascript
function renderBurnChart(el, x, y, color, xaxisLayout) {
  const theme = currentTheme();
  const compact = isCompactViewport();
  const xaxis = Object.assign({}, xaxisLayout, { title: null });
  Plotly.react(el, [{
    x,
    y,
    mode: 'lines',
    line: { color, width: compact ? 1.6 : 2.2 },
    connectgaps: false,
    cliponaxis: false,
    hovertemplate: '%{y:.2f} %/hr<extra></extra>'
  }], {
    title: null,
    uirevision: 'keep-zoom',
    paper_bgcolor: theme.paperBg,
    plot_bgcolor: theme.plotBg,
    font: { color: theme.fg },
    xaxis,
    yaxis: { title: compact ? null : '%/hr', rangemode: 'tozero', gridcolor: theme.grid },
    margin: compact ? { t: 8, r: 18, b: 40, l: 46 } : { t: 10, r: 30, b: 48, l: 56 },
    showlegend: false
  }, { responsive: true });
}

function renderBurnCharts(rows, sessionBurnSeries, weeklyBurnSeries, xaxisLayout) {
  if (!rows.length) {
    Plotly.react(burnChartSessionEl, [], { uirevision: 'keep-zoom' }, { responsive: true });
    Plotly.react(burnChartWeeklyEl, [], { uirevision: 'keep-zoom' }, { responsive: true });
    burnSessionNowEl.textContent = '-';
    burnWeeklyNowEl.textContent = '-';
    return;
  }
  const x = rows.map((r) => toLocalPlotTs(r.ts));
  renderBurnChart(burnChartSessionEl, x, sessionBurnSeries, '#1f8deb', xaxisLayout);
  renderBurnChart(burnChartWeeklyEl, x, weeklyBurnSeries, '#ff6a2b', xaxisLayout);
  burnSessionNowEl.textContent = fmtBurn(lastNonNull(sessionBurnSeries));
  burnWeeklyNowEl.textContent = fmtBurn(lastNonNull(weeklyBurnSeries));
}
```

- [ ] **Step 5: Call `renderBurnCharts` from `renderChart`**

In the empty-rows branch of `renderChart`, right after `renderSummaryTable(null, null, null, null, null, null);` (updated in Task 1, ~line 2016), add:

```javascript
    renderBurnCharts([], [], [], null);
```

In the main path, the x-axis layout object `xaxisLayout` is built at lines 2113-2129 and used by the main `Plotly.react` call at line 2131. Right after that main `Plotly.react(chartEl, ...)` call (after line 2144), add:

```javascript
  renderBurnCharts(rows, sessionBurnSeries, weeklyBurnSeries, xaxisLayout);
```

(`sessionBurnSeries` and `weeklyBurnSeries` were introduced in Task 1, Step 5.)

- [ ] **Step 6: Verify manually in the dashboard**

Reload the dashboard:

```bash
./tracker.sh restart && ./tracker.sh status
```

Open `http://127.0.0.1:7474`. Expected:
- A new **Burn rate** panel below the main chart with two stacked mini-charts (Current session, Weekly), side by side on wide screens and stacked on narrow (< 720px).
- Each chart title shows the current burn rate (e.g. `Current session  4.2 %/hr`), matching the Summary table value from Task 1.
- Lines are non-negative, sit on a `%/hr` y-axis that starts at zero, and share the main chart's time range as you change the Range preset.
- A window reset shows a gap in the line, not a downward spike.
- Toggling the theme restyles both mini-charts.

- [ ] **Step 7: Commit**

```bash
git add web.py
git commit -m "Add session and weekly burn-rate mini-charts"
```

---

## Self-Review

**Spec coverage:**
- Session + weekly burn-rate history charts → Task 2.
- Current burn-rate value per chart (header) → Task 2, Step 4/6.
- Current burn-rate value in Summary table → Task 1.
- Rolling ~30-min window slope → Task 1, Step 1 (`computeBurnRate` default `windowMinutes = 30`).
- Reset handling (gap, not negative) → Task 1, Step 1 (`v1 < v0` → skip; `connectgaps: false` in Task 2, Step 4).
- Separate auto-scaled charts → Task 2 (`rangemode: 'tozero'`, independent divs).
- No backend/DB changes → all edits in `web.py`, browser-side only.
- Colors session `#1f8deb` / weekly `#ff6a2b` → Task 2, Step 5 render calls.

**Placeholder scan:** No TBD/TODO; every code step shows complete code.

**Type consistency:** `computeBurnRate` / `lastNonNull` / `fmtBurn` defined in Task 1 Step 1, consumed in Task 1 Steps 4-5 and Task 2 Step 4. `sessionBurnSeries` / `weeklyBurnSeries` defined in Task 1 Step 5, consumed in Task 2 Step 5. `renderBurnCharts` signature matches its two call sites. Summary table gained one column (7 total) consistently in header (Step 2) and row template (Step 4).
