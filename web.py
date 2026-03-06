from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from db import UsageDB


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def start_dashboard_server(
    host: str,
    port: int,
    db: UsageDB,
    logger: logging.Logger,
    poll_interval_seconds: int,
    expected_weekly_line_enabled: bool,
    expected_active_start_hhmm: str,
    expected_active_end_hhmm: str,
) -> ThreadingHTTPServer:
    handler_cls = _build_handler(
        db,
        poll_interval_seconds,
        expected_weekly_line_enabled,
        expected_active_start_hhmm,
        expected_active_end_hhmm,
    )
    server = ReusableThreadingHTTPServer((host, port), handler_cls)

    thread = Thread(target=server.serve_forever, name="dashboard-http", daemon=True)
    thread.start()

    logger.info("Dashboard listening on http://%s:%d", host, port)
    return server


def _build_handler(
    db: UsageDB,
    poll_interval_seconds: int,
    expected_weekly_line_enabled: bool,
    expected_active_start_hhmm: str,
    expected_active_end_hhmm: str,
):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._respond_html(_index_html(poll_interval_seconds))
                return

            if self.path == "/app.js":
                self._respond_js(
                    _app_js(
                        poll_interval_seconds,
                        expected_weekly_line_enabled,
                        expected_active_start_hhmm,
                        expected_active_end_hhmm,
                    )
                )
                return

            if self.path == "/app.css":
                self._respond_css(_app_css())
                return

            if self.path == "/data.json":
                payload = {
                    "rows": db.fetch_all(),
                    "poll_interval_seconds": poll_interval_seconds,
                }
                self._respond_json(payload)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _respond_html(self, body: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _respond_js(self, body: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _respond_css(self, body: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _respond_json(self, payload: dict[str, Any]) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

    return DashboardHandler


def _index_html(poll_interval_seconds: int) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Claude Usage Tracker</title>
  <link rel="stylesheet" href="/app.css" />
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body class="theme-dark">
  <main>
    <header class="topbar">
      <div>
        <h1>Claude Usage Tracker</h1>
        <p id="status">Polling every {poll_interval_seconds}s</p>
      </div>
      <div class="controls">
        <label for="view-mode">View</label>
        <select id="view-mode">
          <option value="raw">Raw</option>
          <option value="clean">Cleaned</option>
          <option value="smooth">Smoothed</option>
        </select>

        <label for="range-preset">Range</label>
        <select id="range-preset">
          <option value="today">Today</option>
          <option value="weekly_cycle">Since weekly reset</option>
          <option value="all">All time</option>
          <option value="manual">Manual zoom</option>
        </select>

        <button id="y-reset" title="Reset Y axis zoom (0-100)" aria-label="Reset Y axis zoom (0-100)">⟲</button>
        <button id="theme-toggle" title="Toggle theme" aria-label="Toggle theme">☀</button>
      </div>
    </header>

    <div id="chart" aria-label="usage chart"></div>

    <div id="summary-wrap" aria-live="polite">
      <table id="summary-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Usage</th>
            <th>Resets At (Local)</th>
          </tr>
        </thead>
        <tbody id="summary-body"></tbody>
      </table>
    </div>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


def _app_css() -> str:
    return """
:root {
  --bg: #0f1115;
  --bg-2: #171a20;
  --card: #151922;
  --fg: #f3f5f7;
  --muted: #adb6c2;
  --line: #2a313d;
}
body.theme-light {
  --bg: #f4f7fb;
  --bg-2: #eaf1f9;
  --card: #ffffff;
  --fg: #1d2733;
  --muted: #5a6a7c;
  --line: #d8e0ea;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  color: var(--fg);
  background: radial-gradient(circle at top left, var(--bg-2), var(--bg));
}
main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}
.topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
h1 { margin: 0 0 6px 0; font-size: 2rem; }
#status { margin: 0; color: var(--muted); }
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
}
.controls label { font-size: 0.9rem; }
.controls select,
.controls button {
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--fg);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 0.9rem;
}
.controls button {
  cursor: pointer;
  width: 36px;
  font-size: 1rem;
}
#chart {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  margin-top: 12px;
  min-height: 520px;
}
#summary-wrap {
  margin-top: 12px;
}
#summary-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}
#summary-table th,
#summary-table td {
  text-align: left;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line);
}
#summary-table th {
  color: var(--muted);
  font-size: 0.9rem;
  font-weight: 700;
}
#summary-table td { font-weight: 600; }
#summary-table tbody tr:last-child td { border-bottom: none; }
"""


def _app_js(
    poll_interval_seconds: int,
    expected_weekly_line_enabled: bool,
    expected_active_start_hhmm: str,
    expected_active_end_hhmm: str,
) -> str:
    poll_ms = poll_interval_seconds * 1000
    js = """
const chartEl = document.getElementById('chart');
const statusEl = document.getElementById('status');
const summaryBodyEl = document.getElementById('summary-body');
const viewModeEl = document.getElementById('view-mode');
const rangePresetEl = document.getElementById('range-preset');
const yResetEl = document.getElementById('y-reset');
const themeToggleEl = document.getElementById('theme-toggle');

const expectedLineEnabled = __EXPECTED_LINE_ENABLED__;
const expectedActiveStart = '__EXPECTED_ACTIVE_START__';
const expectedActiveEnd = '__EXPECTED_ACTIVE_END__';

let hasInitializedXRange = false;
let hasBoundRelayout = false;
let userXRange = null;


function storageGet(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (_err) {
    return fallback;
  }
}

function storageSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_err) {
    // Ignore storage failures (privacy mode / blocked storage)
  }
}

let currentRows = [];
let viewMode = storageGet('tracker_view_mode', 'raw');
let rangePreset = storageGet('tracker_range_preset', 'weekly_cycle');
let themeMode = storageGet('tracker_theme_mode', 'dark');

function pad2(n) {
  return String(n).padStart(2, '0');
}

function roundDateToNearestMinute(dateObj) {
  return new Date(Math.round(dateObj.getTime() / 60000) * 60000);
}

function formatLocalDateTime(rawTs) {
  const d = new Date(rawTs);
  if (Number.isNaN(d.getTime())) return rawTs;
  const rounded = roundDateToNearestMinute(d);
  return `${rounded.getFullYear()}-${pad2(rounded.getMonth() + 1)}-${pad2(rounded.getDate())} ${pad2(rounded.getHours())}:${pad2(rounded.getMinutes())}`;
}

function formatLocalDateTimeFull(rawTs) {
  const d = new Date(rawTs);
  if (Number.isNaN(d.getTime())) return rawTs;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function toLocalPlotTs(rawTs) {
  const d = new Date(rawTs);
  if (Number.isNaN(d.getTime())) return rawTs;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function fmtPct(v) {
  return (v === null || v === undefined || Number.isNaN(v)) ? '-' : Number(v).toFixed(1) + '%';
}

function renderSummaryTable(latest, expectedNowPct) {
  const fmtReset = (rawTs) => rawTs ? formatLocalDateTime(rawTs) : '-';
  const rows = [
    ['Current session', fmtPct(latest ? latest.session_pct : null), fmtReset(latest ? latest.session_resets : null)],
    ['Weekly', fmtPct(latest ? latest.weekly_pct : null), fmtReset(latest ? latest.weekly_resets : null)],
    ['Extra usage', fmtPct(latest ? latest.extra_pct : null), ''],
    ['Expected weekly usage (now)', fmtPct(expectedNowPct), '']
  ];
  summaryBodyEl.innerHTML = rows
    .map((row) => `<tr><td>${row[0]}</td><td>${row[1]}</td><td>${row[2]}</td></tr>`)
    .join('');
}

function buildShapes(rows) {
  const shapes = [];
  const seen = new Set();
  rows.forEach((row) => {
    ['session_resets', 'weekly_resets'].forEach((key) => {
      const value = row[key];
      if (!value) return;
      const localValue = toLocalPlotTs(value);
      if (!localValue || seen.has(key + localValue)) return;
      seen.add(key + localValue);
      shapes.push({
        type: 'line',
        x0: localValue,
        x1: localValue,
        y0: 0,
        y1: 100,
        line: {
          color: key === 'session_resets' ? 'rgba(0,120,212,0.45)' : 'rgba(227,89,0,0.45)',
          width: 1,
          dash: 'dot'
        }
      });
    });
  });
  return shapes;
}

function hhmmToMinutes(hhmm) {
  const parts = hhmm.split(':');
  if (parts.length !== 2) return null;
  const hh = Number(parts[0]);
  const mm = Number(parts[1]);
  if (!Number.isFinite(hh) || !Number.isFinite(mm) || hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;
  return hh * 60 + mm;
}

function overlapMs(startA, endA, startB, endB) {
  const start = Math.max(startA, startB);
  const end = Math.min(endA, endB);
  return Math.max(0, end - start);
}

function activeMsBetween(startDate, endDate, startMin, endMin) {
  const startMs = startDate.getTime();
  const endMs = endDate.getTime();
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return 0;
  let total = 0;
  const cursor = new Date(startDate);
  cursor.setHours(0, 0, 0, 0);
  while (cursor.getTime() < endMs) {
    const dayStart = cursor.getTime();
    const activeStart = dayStart + startMin * 60 * 1000;
    const activeEnd = dayStart + endMin * 60 * 1000;
    total += overlapMs(startMs, endMs, activeStart, activeEnd);
    cursor.setDate(cursor.getDate() + 1);
  }
  return total;
}

function expectedPctAt(ts, cycleStart, cycleEnd, startMin, endMin, totalActive) {
  if (ts <= cycleStart) return 0;
  if (ts >= cycleEnd) return 100;
  if (totalActive <= 0) return null;
  const elapsed = activeMsBetween(cycleStart, ts, startMin, endMin);
  return Math.max(0, Math.min(100, (elapsed / totalActive) * 100));
}

function computeExpectedWeeklyTrace(rows) {
  if (!expectedLineEnabled || rows.length < 1) return null;
  const startMin = hhmmToMinutes(expectedActiveStart);
  const endMin = hhmmToMinutes(expectedActiveEnd);
  if (startMin === null || endMin === null || endMin <= startMin) return null;

  const resetDates = Array.from(new Set(rows.map((r) => r.weekly_resets).filter(Boolean)))
    .map((s) => new Date(s))
    .filter((d) => !Number.isNaN(d.getTime()))
    .sort((a, b) => a.getTime() - b.getTime());
  if (!resetDates.length) return null;

  const latest = new Date(rows[rows.length - 1].ts);
  let cycleEnd = resetDates.find((d) => d.getTime() >= latest.getTime()) || resetDates[resetDates.length - 1];
  let prev = null;
  for (let i = resetDates.length - 1; i >= 0; i -= 1) {
    if (resetDates[i].getTime() < cycleEnd.getTime()) { prev = resetDates[i]; break; }
  }
  const cycleStart = prev || new Date(cycleEnd.getTime() - 7 * 24 * 60 * 60 * 1000);

  const totalActive = activeMsBetween(cycleStart, cycleEnd, startMin, endMin);
  if (totalActive <= 0) return null;

  const pointMs = new Set([cycleStart.getTime(), cycleEnd.getTime()]);
  const cursor = new Date(cycleStart);
  cursor.setHours(0, 0, 0, 0);
  while (cursor.getTime() <= cycleEnd.getTime()) {
    const dayStart = cursor.getTime();
    const aStart = dayStart + startMin * 60 * 1000;
    const aEnd = dayStart + endMin * 60 * 1000;
    if (aStart > cycleStart.getTime() && aStart < cycleEnd.getTime()) pointMs.add(aStart);
    if (aEnd > cycleStart.getTime() && aEnd < cycleEnd.getTime()) pointMs.add(aEnd);
    cursor.setDate(cursor.getDate() + 1);
  }

  const pointList = Array.from(pointMs).sort((a, b) => a - b);
  const x = pointList.map((ms) => toLocalPlotTs(new Date(ms).toISOString()));
  const y = pointList.map((ms) => expectedPctAt(new Date(ms), cycleStart, cycleEnd, startMin, endMin, totalActive));
  const nowPct = expectedPctAt(new Date(), cycleStart, cycleEnd, startMin, endMin, totalActive);

  return {
    trace: {
      x,
      y,
      mode: 'lines',
      name: 'Expected weekly usage',
      line: { color: 'rgba(170,170,170,0.75)', width: 1, dash: '3px,3px' },
      hovertemplate: 'Expected: %{y:.1f}%<br>Time: %{x|%Y-%m-%d %H:%M}<extra>Expected weekly usage</extra>'
    },
    expectedNowPct: nowPct
  };
}

function cleanIsolated(values) {
  const out = [...values];
  for (let i = 1; i < values.length - 1; i += 1) {
    const prev = values[i - 1];
    const cur = values[i];
    const next = values[i + 1];
    if (prev == null || cur == null || next == null) continue;
    if (Math.abs(cur - prev) >= 1.0 && Math.abs(cur - next) >= 1.0 && Math.abs(prev - next) <= 0.35) {
      out[i] = (prev + next) / 2;
    }
  }
  return out;
}

function smoothMoving(values, windowSize = 3) {
  const radius = Math.floor(windowSize / 2);
  return values.map((v, i) => {
    if (v == null) return null;
    let sum = 0;
    let count = 0;
    for (let j = Math.max(0, i - radius); j <= Math.min(values.length - 1, i + radius); j += 1) {
      if (values[j] == null) continue;
      sum += values[j];
      count += 1;
    }
    return count ? (sum / count) : v;
  });
}

function seriesFor(rows, key) {
  const raw = rows.map((r) => r[key]);
  if (viewMode === 'raw') return raw;
  if (viewMode === 'clean') return cleanIsolated(raw);
  return smoothMoving(cleanIsolated(raw), 3);
}

function computeRangePreset(rows, preset) {
  if (!rows.length) return null;
  const latest = new Date(rows[rows.length - 1].ts);
  if (Number.isNaN(latest.getTime())) return null;

  if (preset === 'today') {
    const start = new Date(latest);
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    return [toLocalPlotTs(start.toISOString()), toLocalPlotTs(end.toISOString())];
  }

  if (preset === 'all') {
    const first = new Date(rows[0].ts);
    const end = new Date(latest.getTime() + 15 * 60 * 1000);
    return [toLocalPlotTs(first.toISOString()), toLocalPlotTs(end.toISOString())];
  }

  if (preset === 'weekly_cycle') {
    const resets = Array.from(new Set(rows.map((r) => r.weekly_resets).filter(Boolean)))
      .map((s) => new Date(s))
      .filter((d) => !Number.isNaN(d.getTime()))
      .sort((a, b) => a.getTime() - b.getTime());
    if (!resets.length) return null;

    let next = resets.find((d) => d.getTime() >= latest.getTime()) || resets[resets.length - 1];
    let prev = null;
    for (let i = resets.length - 1; i >= 0; i -= 1) {
      if (resets[i].getTime() < next.getTime()) { prev = resets[i]; break; }
    }
    const start = prev || new Date(next.getTime() - 7 * 24 * 60 * 60 * 1000);
    return [toLocalPlotTs(start.toISOString()), toLocalPlotTs(next.toISOString())];
  }

  return null;
}

function currentTheme() {
  return themeMode === 'light'
    ? {
        bodyClass: 'theme-light',
        plotBg: '#ffffff',
        paperBg: '#ffffff',
        grid: '#e1e7ef',
        fg: '#1d2733'
      }
    : {
        bodyClass: 'theme-dark',
        plotBg: '#151922',
        paperBg: '#151922',
        grid: '#2a313d',
        fg: '#f3f5f7'
      };
}

function applyTheme() {
  const theme = currentTheme();
  document.body.classList.remove('theme-light', 'theme-dark');
  document.body.classList.add(theme.bodyClass);
  themeToggleEl.textContent = themeMode === 'light' ? '🌙' : '☀';
}


function ensureRelayoutBinding() {
  if (hasBoundRelayout) return;
  if (typeof chartEl.on !== 'function') return;
  chartEl.on('plotly_relayout', (evt) => {
    if (!evt) return;
    if (evt['xaxis.range[0]'] && evt['xaxis.range[1]']) {
      userXRange = [evt['xaxis.range[0]'], evt['xaxis.range[1]']];
      rangePreset = 'manual';
      rangePresetEl.value = 'manual';
      storageSet('tracker_range_preset', rangePreset);
    }
    if (evt['xaxis.autorange'] === true) {
      userXRange = null;
    }
  });
  hasBoundRelayout = true;
}

function bindControls() {
  if (!['raw','clean','smooth'].includes(viewMode)) viewMode = 'raw';
  if (!['today','weekly_cycle','all','manual'].includes(rangePreset)) rangePreset = 'weekly_cycle';
  if (!['dark','light'].includes(themeMode)) themeMode = 'dark';

  viewModeEl.value = viewMode;
  rangePresetEl.value = rangePreset;
  applyTheme();

  viewModeEl.addEventListener('change', () => {
    viewMode = viewModeEl.value;
    storageSet('tracker_view_mode', viewMode);
    renderChart(currentRows);
  });

  rangePresetEl.addEventListener('change', () => {
    rangePreset = rangePresetEl.value;
    storageSet('tracker_range_preset', rangePreset);
    if (rangePreset !== 'manual') userXRange = null;
    if (rangePreset === 'manual' && !userXRange) {
      rangePreset = 'weekly_cycle';
      rangePresetEl.value = rangePreset;
      storageSet('tracker_range_preset', rangePreset);
    }
    renderChart(currentRows);
  });

  yResetEl.addEventListener('click', () => {
    Plotly.relayout(chartEl, {
      'yaxis.autorange': false,
      'yaxis.range': [0, 102]
    });
  });

  themeToggleEl.addEventListener('click', () => {
    themeMode = themeMode === 'light' ? 'dark' : 'light';
    storageSet('tracker_theme_mode', themeMode);
    applyTheme();
    renderChart(currentRows);
  });

}

function renderChart(rows) {
  const theme = currentTheme();

  if (!rows.length) {
    Plotly.newPlot(chartEl, [], {
      title: null,
      uirevision: 'keep-zoom',
      paper_bgcolor: theme.paperBg,
      plot_bgcolor: theme.plotBg,
      font: { color: theme.fg },
      xaxis: { title: 'Time (Local)', tickformat: '%Y-%m-%d %H:%M', hoverformat: '%Y-%m-%d %H:%M', gridcolor: theme.grid },
      yaxis: { title: 'Utilization (%)', range: [0, 102], gridcolor: theme.grid }
    }, { responsive: true });
    ensureRelayoutBinding();
    renderSummaryTable(null, null);
    return;
  }

  const x = rows.map((r) => toLocalPlotTs(r.ts));
  const traces = [
    {
      x,
      y: seriesFor(rows, 'session_pct'),
      mode: 'lines+markers',
      name: 'Current session',
      line: { color: '#1f8deb', width: 2.5 },
      marker: { size: 4 },
      cliponaxis: false
    },
    {
      x,
      y: seriesFor(rows, 'weekly_pct'),
      mode: 'lines+markers',
      name: 'Weekly',
      line: { color: '#ff6a2b', width: 2.5 },
      marker: { size: 4 },
      cliponaxis: false
    },
    {
      x,
      y: seriesFor(rows, 'extra_pct'),
      mode: 'lines+markers',
      name: 'Extra usage',
      line: { color: '#1db6a3', width: 2.5 },
      marker: { size: 4 },
      cliponaxis: false
    }
  ];

  const expectedData = computeExpectedWeeklyTrace(rows);
  if (expectedData) traces.push(expectedData.trace);

  const latest = rows[rows.length - 1];
  statusEl.textContent = 'Samples: ' + rows.length + ' | Last sample (Local): ' + formatLocalDateTimeFull(latest.ts);
  renderSummaryTable(latest, expectedData ? expectedData.expectedNowPct : null);

  const xaxisLayout = {
    title: 'Time (Local)',
    tickformat: '%Y-%m-%d %H:%M',
    hoverformat: '%Y-%m-%d %H:%M',
    gridcolor: theme.grid
  };

  if (rangePreset === 'manual' && userXRange) {
    xaxisLayout.range = userXRange;
    xaxisLayout.autorange = false;
  } else {
    const presetRange = computeRangePreset(rows, rangePreset);
    if (presetRange) {
      xaxisLayout.range = presetRange;
      xaxisLayout.autorange = false;
    }
  }

  Plotly.react(chartEl, traces, {
    title: null,
    uirevision: 'keep-zoom',
    paper_bgcolor: theme.paperBg,
    plot_bgcolor: theme.plotBg,
    font: { color: theme.fg },
    xaxis: xaxisLayout,
    yaxis: { title: 'Utilization (%)', range: [0, 102], gridcolor: theme.grid },
    shapes: buildShapes(rows),
    margin: { t: 50, r: 30, b: 100, l: 60 },
    legend: { orientation: 'h', y: -0.18, yanchor: 'top' }
  }, { responsive: true });

  ensureRelayoutBinding();
  hasInitializedXRange = true;
}

async function refreshData() {
  try {
    const res = await fetch('/data.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const payload = await res.json();
    currentRows = payload.rows || [];
    renderChart(currentRows);
  } catch (err) {
    statusEl.textContent = 'Data fetch failed: ' + err;
  }
}

bindControls();
renderChart([]);
refreshData();
setInterval(refreshData, __POLL_MS__);
"""
    return (
        js.replace("__POLL_MS__", str(poll_ms))
        .replace("__EXPECTED_LINE_ENABLED__", "true" if expected_weekly_line_enabled else "false")
        .replace("__EXPECTED_ACTIVE_START__", expected_active_start_hhmm)
        .replace("__EXPECTED_ACTIVE_END__", expected_active_end_hhmm)
    )
