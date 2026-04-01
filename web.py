from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from threading import Thread
from typing import Any

from config import load_config, write_runtime_env_values
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
    notify_session_threshold_pct: float | None,
    notify_weekly_threshold_pct: float | None,
    notify_extra_threshold_pct: float | None,
    notify_sonnet_threshold_pct: float | None,
    notify_expected_weekly_overrun_enabled: bool,
    notify_expected_sonnet_overrun_enabled: bool,
    notify_expected_session_overrun_enabled: bool,
) -> ThreadingHTTPServer:
    handler_cls = _build_handler(
        db,
        poll_interval_seconds,
        expected_weekly_line_enabled,
        expected_active_start_hhmm,
        expected_active_end_hhmm,
        notify_session_threshold_pct,
        notify_weekly_threshold_pct,
        notify_extra_threshold_pct,
        notify_sonnet_threshold_pct,
        notify_expected_weekly_overrun_enabled,
        notify_expected_sonnet_overrun_enabled,
        notify_expected_session_overrun_enabled,
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
    notify_session_threshold_pct: float | None,
    notify_weekly_threshold_pct: float | None,
    notify_extra_threshold_pct: float | None,
    notify_sonnet_threshold_pct: float | None,
    notify_expected_weekly_overrun_enabled: bool,
    notify_expected_sonnet_overrun_enabled: bool,
    notify_expected_session_overrun_enabled: bool,
):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/":
                self._respond_html(_index_html(poll_interval_seconds))
                return

            if path == "/app.js":
                self._respond_js(
                    _app_js(
                        poll_interval_seconds,
                        expected_weekly_line_enabled,
                        expected_active_start_hhmm,
                        expected_active_end_hhmm,
                        notify_session_threshold_pct,
                        notify_weekly_threshold_pct,
                        notify_extra_threshold_pct,
                        notify_sonnet_threshold_pct,
                        notify_expected_weekly_overrun_enabled,
                        notify_expected_sonnet_overrun_enabled,
                        notify_expected_session_overrun_enabled,
                    )
                )
                return

            if path == "/app.css":
                self._respond_css(_app_css())
                return

            if path == "/data.json":
                query = parse_qs(parsed.query)
                range_preset = (query.get("range") or ["all"])[0]
                payload = db.fetch_chart_data(range_preset)
                payload["poll_interval_seconds"] = poll_interval_seconds
                current_config = load_config()
                payload["notification_settings"] = {
                    "session_threshold_pct": current_config.notify_session_threshold_pct,
                    "weekly_threshold_pct": current_config.notify_weekly_threshold_pct,
                    "extra_threshold_pct": current_config.notify_extra_threshold_pct,
                    "sonnet_threshold_pct": current_config.notify_sonnet_threshold_pct,
                    "expected_weekly_overrun_enabled": current_config.notify_expected_weekly_overrun_enabled,
                    "expected_sonnet_overrun_enabled": current_config.notify_expected_sonnet_overrun_enabled,
                    "expected_session_overrun_enabled": current_config.notify_expected_session_overrun_enabled,
                }
                self._respond_json(payload)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/notification-settings":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            try:
                payload = self._read_json_body()
                normalized = _normalize_notification_settings(payload)
                write_runtime_env_values(
                    {
                        "NOTIFY_SESSION_THRESHOLD_PCT": normalized["session_threshold_pct"],
                        "NOTIFY_WEEKLY_THRESHOLD_PCT": normalized["weekly_threshold_pct"],
                        "NOTIFY_EXTRA_THRESHOLD_PCT": normalized["extra_threshold_pct"],
                        "NOTIFY_SONNET_THRESHOLD_PCT": normalized["sonnet_threshold_pct"],
                        "NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED": normalized[
                            "expected_weekly_overrun_enabled"
                        ],
                        "NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED": normalized[
                            "expected_sonnet_overrun_enabled"
                        ],
                        "NOTIFY_EXPECTED_SESSION_OVERRUN_ENABLED": normalized[
                            "expected_session_overrun_enabled"
                        ],
                    }
                )
            except ValueError as exc:
                self._respond_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                self._respond_json({"error": f"Could not save settings: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._respond_json(
                {
                    "ok": True,
                    "notification_settings": {
                        "session_threshold_pct": _as_number_or_none(normalized["session_threshold_pct"]),
                        "weekly_threshold_pct": _as_number_or_none(normalized["weekly_threshold_pct"]),
                        "extra_threshold_pct": _as_number_or_none(normalized["extra_threshold_pct"]),
                        "sonnet_threshold_pct": _as_number_or_none(normalized["sonnet_threshold_pct"]),
                        "expected_weekly_overrun_enabled": normalized[
                            "expected_weekly_overrun_enabled"
                        ] == "true",
                        "expected_sonnet_overrun_enabled": normalized[
                            "expected_sonnet_overrun_enabled"
                        ] == "true",
                        "expected_session_overrun_enabled": normalized[
                            "expected_session_overrun_enabled"
                        ] == "true",
                    },
                }
            )

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

        def _respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def _read_json_body(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Invalid request length") from exc
            body = self.rfile.read(length)
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON payload") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON payload must be an object")
            return payload

    return DashboardHandler


def _normalize_threshold_value(raw: Any, label: str) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return ""
    else:
        value = str(raw).strip()

    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number between 0 and 100") from exc

    if parsed < 0 or parsed > 100:
        raise ValueError(f"{label} must be between 0 and 100")
    return f"{parsed:.1f}".rstrip("0").rstrip(".")


def _normalize_notification_settings(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "session_threshold_pct": _normalize_threshold_value(
            payload.get("session_threshold_pct"), "Session threshold"
        ),
        "weekly_threshold_pct": _normalize_threshold_value(
            payload.get("weekly_threshold_pct"), "Weekly threshold"
        ),
        "extra_threshold_pct": _normalize_threshold_value(
            payload.get("extra_threshold_pct"), "Extra usage threshold"
        ),
        "sonnet_threshold_pct": _normalize_threshold_value(
            payload.get("sonnet_threshold_pct"), "Sonnet threshold"
        ),
        "expected_weekly_overrun_enabled": "true"
        if bool(payload.get("expected_weekly_overrun_enabled"))
        else "false",
        "expected_sonnet_overrun_enabled": "true"
        if bool(payload.get("expected_sonnet_overrun_enabled"))
        else "false",
        "expected_session_overrun_enabled": "true"
        if bool(payload.get("expected_session_overrun_enabled"))
        else "false",
    }


def _as_number_or_none(raw: str) -> float | None:
    if not raw:
        return None
    return float(raw)


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
  <main class="dashboard-shell">
    <header class="topbar">
      <div class="title-block">
        <p class="eyebrow">Local dashboard</p>
        <h1>Claude Usage Tracker</h1>
        <p id="status">Polling every {poll_interval_seconds}s</p>
      </div>
      <div class="controls">
        <div class="control-field">
          <label for="view-mode">View</label>
          <select id="view-mode">
            <option value="raw">Raw</option>
            <option value="clean">Cleaned</option>
            <option value="smooth">Smoothed</option>
          </select>
        </div>

        <div class="control-field">
          <label for="range-preset">Range</label>
          <select id="range-preset">
            <option value="today">Today</option>
            <option value="weekly_cycle">Since weekly reset</option>
            <option value="all">All time</option>
            <option value="manual">Manual zoom</option>
          </select>
        </div>

        <div class="control-actions">
          <button id="y-reset" title="Reset Y axis zoom (0-100)" aria-label="Reset Y axis zoom (0-100)">Reset Y</button>
          <button id="theme-toggle" title="Toggle theme" aria-label="Toggle theme">Light</button>
        </div>
      </div>
    </header>

    <section class="panel chart-panel">
      <div class="panel-header">
        <div>
          <p class="panel-kicker">Live usage</p>
          <h2>Usage history</h2>
        </div>
      </div>
      <div id="chart-loading" class="chart-loading" aria-live="polite">
        <div class="chart-spinner" aria-hidden="true"></div>
        <span>Loading usage data…</span>
      </div>
      <div id="chart" aria-label="usage chart"></div>
    </section>

    <section id="summary-wrap" class="panel" aria-live="polite">
      <div class="panel-header">
        <div>
          <p class="panel-kicker">Current state</p>
          <h2>Summary</h2>
        </div>
        <div class="panel-actions">
          <span id="save-alert-status" aria-live="polite"></span>
          <label id="notifications-toggle-wrap" class="toggle-chip" title="Enable browser alerts">
            <input id="notifications-toggle" type="checkbox" aria-label="Alerts enabled" />
            <span id="notifications-toggle-text">Alerts enabled</span>
          </label>
          <button id="save-alert-settings" type="button" disabled>Save Alerts</button>
        </div>
      </div>
      <table id="summary-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Usage</th>
            <th>Expected</th>
            <th>Resets At (Local)</th>
            <th>Alert</th>
            <th>Overrun alert</th>
          </tr>
        </thead>
        <tbody id="summary-body"></tbody>
      </table>
    </section>
  </main>
  <script src="/app.js"></script>
</body>
</html>
"""


def _app_css() -> str:
    return """
:root {
  --bg: #07111b;
  --bg-2: #132435;
  --card: rgba(10, 22, 35, 0.84);
  --card-strong: rgba(15, 32, 49, 0.96);
  --fg: #f3f6f9;
  --muted: #9db0c2;
  --line: rgba(152, 179, 204, 0.18);
  --accent: #47c0ff;
  --accent-2: #ff8a4c;
  --shadow: 0 24px 80px rgba(0, 0, 0, 0.28);
}
body.theme-light {
  --bg: #f2f6fb;
  --bg-2: #dce8f6;
  --card: rgba(255, 255, 255, 0.82);
  --card-strong: rgba(255, 255, 255, 0.96);
  --fg: #1f2937;
  --muted: #617385;
  --line: rgba(105, 126, 149, 0.22);
  --accent: #0a84c6;
  --accent-2: #df6b31;
  --shadow: 0 18px 60px rgba(71, 96, 122, 0.18);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  color: var(--fg);
  background:
    radial-gradient(circle at top left, rgba(71, 192, 255, 0.12), transparent 28%),
    radial-gradient(circle at top right, rgba(255, 138, 76, 0.12), transparent 24%),
    linear-gradient(180deg, var(--bg-2), var(--bg));
  min-height: 100vh;
}
.dashboard-shell {
  max-width: 1100px;
  margin: 0 auto;
  padding: 24px;
}
.topbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 18px 24px;
  margin-bottom: 16px;
}
.title-block {
  min-width: 0;
}
.eyebrow,
.panel-kicker {
  margin: 0 0 6px 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.78rem;
  font-weight: 700;
}
h1 {
  margin: 0 0 8px 0;
  font-size: clamp(2rem, 4vw, 3rem);
  line-height: 0.98;
}
h2 {
  margin: 0;
  font-size: 1.1rem;
}
#status {
  margin: 0;
  color: var(--muted);
  line-height: 1.35;
  font-size: 0.92rem;
  max-width: 72rem;
  text-wrap: balance;
}
.controls {
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
  gap: 10px;
  color: var(--muted);
  flex-wrap: wrap;
  width: fit-content;
  max-width: 100%;
}
.control-field,
.control-actions {
  display: flex;
  align-items: stretch;
  gap: 8px;
}
.control-field {
  flex-direction: column;
  min-width: 140px;
}
.control-actions {
  align-items: flex-end;
}
.controls label {
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.03em;
}
.controls select,
.controls button {
  border: 1px solid var(--line);
  background: var(--card-strong);
  color: var(--fg);
  border-radius: 12px;
  padding: 10px 12px;
  font-size: 0.95rem;
  min-height: 44px;
  backdrop-filter: blur(14px);
}
.controls button {
  cursor: pointer;
  min-width: 84px;
  font-weight: 700;
}
.panel {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 20px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px);
}
.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 0 20px;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
}
.toggle-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line);
  background: var(--card-strong);
  color: var(--fg);
  border-radius: 12px;
  padding: 10px 12px;
  min-height: 40px;
  font-size: 0.95rem;
  font-weight: 700;
}
.toggle-chip input[type="checkbox"] {
  width: 18px;
  height: 18px;
  margin: 0;
  accent-color: var(--accent);
}
.toggle-chip.is-disabled {
  opacity: 0.68;
}
.toggle-chip.is-blocked {
  opacity: 0.68;
  color: var(--muted);
}
.panel-actions button,
.alert-control input,
.alert-control button {
  border: 1px solid var(--line);
  background: var(--card-strong);
  color: var(--fg);
  border-radius: 12px;
  padding: 10px 12px;
  font-size: 0.95rem;
  min-height: 40px;
}
.panel-actions button,
.alert-control button {
  cursor: pointer;
  font-weight: 700;
}
.panel-actions button:disabled,
.controls button:disabled,
.alert-control button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
  color: var(--muted);
  border-color: var(--line);
  box-shadow: none;
}
#save-alert-status {
  min-height: 1.2rem;
  font-size: 0.84rem;
}
.alert-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.alert-control input[type="number"] {
  width: 88px;
}
.alert-control label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9rem;
}
.alert-control input[type="checkbox"] {
  width: 18px;
  height: 18px;
  accent-color: var(--accent);
}
.chart-panel {
  overflow: hidden;
  position: relative;
}
#chart {
  min-height: clamp(320px, 58vh, 560px);
  padding: 4px 6px 0 6px;
}
.chart-loading {
  position: absolute;
  inset: 70px 0 0 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--muted);
  background: linear-gradient(180deg, rgba(7, 17, 27, 0.08), rgba(7, 17, 27, 0.18));
  backdrop-filter: blur(2px);
  z-index: 2;
  transition: opacity 0.2s ease;
}
.chart-loading.hidden {
  opacity: 0;
  pointer-events: none;
}
.chart-spinner {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 3px solid rgba(255, 255, 255, 0.14);
  border-top-color: var(--accent);
  animation: chart-spin 0.9s linear infinite;
}
@keyframes chart-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
#summary-wrap {
  margin-top: 14px;
  overflow: hidden;
}
#summary-table {
  width: 100%;
  border-collapse: collapse;
  background: transparent;
}
#summary-table th,
#summary-table td {
  text-align: left;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
}
#summary-table th {
  color: var(--muted);
  font-size: 0.84rem;
  font-weight: 700;
}
#summary-table td { font-weight: 600; }
#summary-table td[data-cell="metric"] {
  font-weight: 700;
}
#summary-table td[data-cell="expected"] {
  color: var(--muted);
  font-weight: 500;
}
#summary-table th:nth-child(3),
#summary-table td[data-cell="expected"] {
  border-left: 1px solid var(--line);
}
#summary-table tbody tr:last-child td { border-bottom: none; }
@media (max-width: 720px) {
  .dashboard-shell {
    padding: 14px;
  }
  .topbar {
    grid-template-columns: 1fr;
    gap: 14px;
  }
  .controls {
    justify-content: stretch;
    max-width: none;
  }
  .control-field,
  .control-actions {
    flex: 1 1 100%;
  }
  .control-actions {
    gap: 10px;
  }
  .control-actions button {
    flex: 1 1 0;
  }
  .panel-header {
    padding: 16px 16px 0 16px;
    flex-direction: column;
    align-items: stretch;
  }
  .panel-actions {
    justify-content: space-between;
  }
  #chart {
    min-height: min(60vh, 420px);
    padding: 0;
  }
  .chart-loading {
    inset: 62px 0 0 0;
  }
  #summary-table thead {
    display: none;
  }
  #summary-table,
  #summary-table tbody,
  #summary-table tr,
  #summary-table td {
    display: block;
    width: 100%;
  }
  #summary-table tbody {
    padding: 8px;
  }
  #summary-table tr {
    background: var(--card-strong);
    border: 1px solid var(--line);
    border-radius: 16px;
    margin-bottom: 10px;
    overflow: hidden;
  }
  #summary-table td {
    border-bottom: 1px solid var(--line);
    padding: 10px 14px;
  }
  #summary-table td[data-cell="metric"] {
    padding: 14px 14px 10px 14px;
    font-size: 1rem;
    border-bottom: 1px solid var(--line);
  }
  #summary-table td:last-child {
    border-bottom: none;
  }
  #summary-table td.cell-hidden {
    display: none;
  }
  #summary-table td[data-cell="expected"] {
    border-left: none;
  }
  #summary-table td::before {
    content: attr(data-label);
    display: block;
    margin-bottom: 4px;
    color: var(--muted);
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  #summary-table td[data-cell="metric"]::before {
    content: none;
  }
}
"""


def _app_js(
    poll_interval_seconds: int,
    expected_weekly_line_enabled: bool,
    expected_active_start_hhmm: str,
    expected_active_end_hhmm: str,
    notify_session_threshold_pct: float | None,
    notify_weekly_threshold_pct: float | None,
    notify_extra_threshold_pct: float | None,
    notify_sonnet_threshold_pct: float | None,
    notify_expected_weekly_overrun_enabled: bool,
    notify_expected_sonnet_overrun_enabled: bool,
    notify_expected_session_overrun_enabled: bool,
) -> str:
    poll_ms = poll_interval_seconds * 1000
    js = """
const chartEl = document.getElementById('chart');
const chartLoadingEl = document.getElementById('chart-loading');
const statusEl = document.getElementById('status');
const summaryBodyEl = document.getElementById('summary-body');
const viewModeEl = document.getElementById('view-mode');
const rangePresetEl = document.getElementById('range-preset');
const notificationsToggleEl = document.getElementById('notifications-toggle');
const notificationsToggleWrapEl = document.getElementById('notifications-toggle-wrap');
const notificationsToggleTextEl = document.getElementById('notifications-toggle-text');
const saveAlertSettingsEl = document.getElementById('save-alert-settings');
const saveAlertStatusEl = document.getElementById('save-alert-status');
const yResetEl = document.getElementById('y-reset');
const themeToggleEl = document.getElementById('theme-toggle');

const expectedLineEnabled = __EXPECTED_LINE_ENABLED__;
const expectedActiveStart = '__EXPECTED_ACTIVE_START__';
const expectedActiveEnd = '__EXPECTED_ACTIVE_END__';
let notifySessionThresholdPct = __NOTIFY_SESSION_THRESHOLD_PCT__;
let notifyWeeklyThresholdPct = __NOTIFY_WEEKLY_THRESHOLD_PCT__;
let notifyExtraThresholdPct = __NOTIFY_EXTRA_THRESHOLD_PCT__;
let notifySonnetThresholdPct = __NOTIFY_SONNET_THRESHOLD_PCT__;
let notifyExpectedWeeklyOverrunEnabled = __NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED__;
let notifyExpectedSonnetOverrunEnabled = __NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED__;
let notifyExpectedSessionOverrunEnabled = __NOTIFY_EXPECTED_SESSION_OVERRUN_ENABLED__;

let hasInitializedXRange = false;
let hasBoundRelayout = false;
let userXRange = null;
let currentTotalSamples = 0;
let alertSettingsDirty = false;
let saveAlertStatusTimer = null;
let hasLoadedInitialData = false;
let alertsEnabled = storageGet('tracker_alerts_enabled', 'false') === 'true';


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

function storageGetJson(key, fallback) {
  const raw = storageGet(key, null);
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw);
  } catch (_err) {
    return fallback;
  }
}

let currentRows = [];
let viewMode = storageGet('tracker_view_mode', 'raw');
let rangePreset = storageGet('tracker_range_preset', 'weekly_cycle');
let themeMode = storageGet('tracker_theme_mode', 'dark');
let dataRangePreset = (rangePreset === 'manual') ? 'weekly_cycle' : rangePreset;
let alertSettingsDraft = {
  session_threshold_pct: notifySessionThresholdPct,
  weekly_threshold_pct: notifyWeeklyThresholdPct,
  extra_threshold_pct: notifyExtraThresholdPct,
  sonnet_threshold_pct: notifySonnetThresholdPct,
  expected_weekly_overrun_enabled: notifyExpectedWeeklyOverrunEnabled,
  expected_sonnet_overrun_enabled: notifyExpectedSonnetOverrunEnabled,
  expected_session_overrun_enabled: notifyExpectedSessionOverrunEnabled
};

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

function thresholdsConfigured() {
  return (
    notifySessionThresholdPct !== null ||
    notifyWeeklyThresholdPct !== null ||
    notifyExtraThresholdPct !== null ||
    notifyExpectedWeeklyOverrunEnabled
  );
}

function notificationsSupported() {
  return typeof window !== 'undefined' && 'Notification' in window;
}

function updateNotificationsToggle() {
  if (!notificationsSupported()) {
    notificationsToggleWrapEl.hidden = true;
    return;
  }
  notificationsToggleWrapEl.hidden = false;
  notificationsToggleWrapEl.classList.remove('is-disabled', 'is-blocked');
  if (Notification.permission === 'granted') {
    notificationsToggleEl.disabled = false;
    notificationsToggleEl.checked = alertsEnabled;
    notificationsToggleTextEl.textContent = 'Alerts enabled';
    if (!alertsEnabled) {
      notificationsToggleWrapEl.classList.add('is-disabled');
    }
    return;
  }
  if (Notification.permission === 'denied') {
    notificationsToggleEl.checked = false;
    notificationsToggleEl.disabled = true;
    notificationsToggleTextEl.textContent = 'Alerts blocked';
    notificationsToggleWrapEl.classList.add('is-blocked');
    return;
  }
  notificationsToggleEl.checked = false;
  notificationsToggleEl.disabled = false;
  notificationsToggleTextEl.textContent = thresholdsConfigured() ? 'Alerts enabled' : 'Alerts enabled';
  notificationsToggleWrapEl.classList.add('is-disabled');
}

function setSaveAlertStatus(message, isError = false) {
  if (saveAlertStatusTimer) {
    clearTimeout(saveAlertStatusTimer);
    saveAlertStatusTimer = null;
  }
  saveAlertStatusEl.textContent = message;
  saveAlertStatusEl.style.color = isError ? 'var(--accent-2)' : 'var(--muted)';
}

function setChartLoading(isLoading) {
  if (!chartLoadingEl) return;
  chartLoadingEl.classList.toggle('hidden', !isLoading);
}

function rerenderChartWithLoading(rows = currentRows) {
  setChartLoading(true);
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      renderChart(rows);
    });
  });
}

function clearSaveAlertStatusLater() {
  if (saveAlertStatusTimer) {
    clearTimeout(saveAlertStatusTimer);
  }
  saveAlertStatusTimer = setTimeout(() => {
    saveAlertStatusEl.textContent = '';
    saveAlertStatusTimer = null;
  }, 3500);
}

function fmtThresholdInput(value) {
  return value === null || value === undefined || Number.isNaN(value) ? '' : String(value);
}

function syncAlertSettingsDraftFromRuntime() {
  alertSettingsDraft = {
    session_threshold_pct: notifySessionThresholdPct,
    weekly_threshold_pct: notifyWeeklyThresholdPct,
    extra_threshold_pct: notifyExtraThresholdPct,
    sonnet_threshold_pct: notifySonnetThresholdPct,
    expected_weekly_overrun_enabled: notifyExpectedWeeklyOverrunEnabled,
    expected_sonnet_overrun_enabled: notifyExpectedSonnetOverrunEnabled,
    expected_session_overrun_enabled: notifyExpectedSessionOverrunEnabled
  };
}

function normalizedAlertSettingsSnapshot(settings) {
  const normalizeThreshold = (value) => {
    if (value === null || value === undefined || value === '') return '';
    const parsed = Number(value);
    return Number.isFinite(parsed) ? String(parsed) : String(value).trim();
  };
  return JSON.stringify({
    session_threshold_pct: normalizeThreshold(settings.session_threshold_pct),
    weekly_threshold_pct: normalizeThreshold(settings.weekly_threshold_pct),
    extra_threshold_pct: normalizeThreshold(settings.extra_threshold_pct),
    sonnet_threshold_pct: normalizeThreshold(settings.sonnet_threshold_pct),
    expected_weekly_overrun_enabled: !!settings.expected_weekly_overrun_enabled,
    expected_sonnet_overrun_enabled: !!settings.expected_sonnet_overrun_enabled,
    expected_session_overrun_enabled: !!settings.expected_session_overrun_enabled
  });
}

function refreshSaveAlertButton() {
  const runtimeSettings = {
    session_threshold_pct: notifySessionThresholdPct,
    weekly_threshold_pct: notifyWeeklyThresholdPct,
    extra_threshold_pct: notifyExtraThresholdPct,
    sonnet_threshold_pct: notifySonnetThresholdPct,
    expected_weekly_overrun_enabled: notifyExpectedWeeklyOverrunEnabled,
    expected_sonnet_overrun_enabled: notifyExpectedSonnetOverrunEnabled,
    expected_session_overrun_enabled: notifyExpectedSessionOverrunEnabled
  };
  alertSettingsDirty =
    normalizedAlertSettingsSnapshot(alertSettingsDraft) !==
    normalizedAlertSettingsSnapshot(runtimeSettings);
  saveAlertSettingsEl.disabled = !alertSettingsDirty;
}

function toLocalPlotTs(rawTs) {
  const d = new Date(rawTs);
  if (Number.isNaN(d.getTime())) return rawTs;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function fmtPct(v) {
  return (v === null || v === undefined || Number.isNaN(v)) ? '-' : Number(v).toFixed(1) + '%';
}

function renderSummaryTable(latest, expectedSessionNowPct, expectedWeeklyNowPct, expectedSonnetNowPct) {
  const fmtReset = (rawTs) => rawTs ? formatLocalDateTime(rawTs) : '-';
  const extraMetricLabel = latest && latest.extra_enabled === false ? 'Extra usage (disabled)' : 'Extra usage';

  function makeThresholdAlert(setting, draftValue) {
    return `<div class="alert-control">
      <input type="number" min="0" max="100" step="0.1" placeholder="Off"
        data-alert-setting="${setting}"
        value="${fmtThresholdInput(draftValue)}" />
      <span>%</span>
    </div>`;
  }

  function makeOverrunAlert(setting, checked) {
    return `<div class="alert-control">
      <label>
        <input type="checkbox" data-alert-setting="${setting}" ${checked ? 'checked' : ''} />
        Alert
      </label>
    </div>`;
  }

  // Rows: [metric, usage, reset, alert, expected, overrunAlert, hasExpected]
  const rows = [
    {
      metric: 'Current session',
      usage: fmtPct(latest ? latest.session_pct : null),
      reset: fmtReset(latest ? latest.session_resets : null),
      alert: makeThresholdAlert('session_threshold_pct', alertSettingsDraft.session_threshold_pct),
      expected: fmtPct(expectedSessionNowPct),
      rawExpected: expectedSessionNowPct ?? null,
      rawUsage: latest ? (latest.session_pct ?? null) : null,
      overrunAlert: makeOverrunAlert('expected_session_overrun_enabled', alertSettingsDraft.expected_session_overrun_enabled)
    },
    {
      metric: 'Weekly',
      usage: fmtPct(latest ? latest.weekly_pct : null),
      reset: fmtReset(latest ? latest.weekly_resets : null),
      alert: makeThresholdAlert('weekly_threshold_pct', alertSettingsDraft.weekly_threshold_pct),
      expected: fmtPct(expectedWeeklyNowPct),
      rawExpected: expectedWeeklyNowPct ?? null,
      rawUsage: latest ? (latest.weekly_pct ?? null) : null,
      overrunAlert: makeOverrunAlert('expected_weekly_overrun_enabled', alertSettingsDraft.expected_weekly_overrun_enabled)
    },
  ];

  // Sonnet-only row: only show when data is present, before Extra usage
  if (latest && latest.sonnet_pct !== null && latest.sonnet_pct !== undefined) {
    rows.push({
      metric: 'Sonnet only',
      usage: fmtPct(latest.sonnet_pct),
      reset: fmtReset(latest.weekly_resets),
      alert: makeThresholdAlert('sonnet_threshold_pct', alertSettingsDraft.sonnet_threshold_pct),
      expected: fmtPct(expectedSonnetNowPct),
      rawExpected: expectedSonnetNowPct ?? null,
      rawUsage: latest.sonnet_pct ?? null,
      overrunAlert: makeOverrunAlert('expected_sonnet_overrun_enabled', alertSettingsDraft.expected_sonnet_overrun_enabled)
    });
  }

  rows.push({
    metric: extraMetricLabel,
    usage: fmtPct(latest ? latest.extra_pct : null),
    reset: '-',
    alert: makeThresholdAlert('extra_threshold_pct', alertSettingsDraft.extra_threshold_pct),
    expected: null,
    overrunAlert: null
  });

  summaryBodyEl.innerHTML = rows
    .map((row) => {
      const hasExpected = row.expected !== null;
      const expectedOverrun = hasExpected && row.rawExpected !== null && row.rawUsage !== null && row.rawUsage > row.rawExpected;
      const expectedStyle = expectedOverrun ? ' style="color: var(--accent-2)"' : '';
      return `<tr>
        <td data-cell="metric">${row.metric}</td>
        <td data-label="Usage">${row.usage}</td>
        <td data-cell="expected" data-label="Expected"${hasExpected ? '' : ' class="cell-hidden"'}${expectedStyle}>${hasExpected ? row.expected : ''}</td>
        <td data-label="Resets At (Local)">${row.reset || '-'}</td>
        <td data-label="Alert">${row.alert}</td>
        <td data-label="Overrun alert"${hasExpected ? '' : ' class="cell-hidden"'}>${hasExpected ? row.overrunAlert : ''}</td>
      </tr>`;
    })
    .join('');
}

function notificationState() {
  return storageGetJson('tracker_notification_state', {});
}

function setNotificationState(state) {
  storageSet('tracker_notification_state', JSON.stringify(state));
}

function clearNotificationState() {
  setNotificationState({});
}

function showNotification(title, body, tag) {
  if (!notificationsSupported() || Notification.permission !== 'granted' || !alertsEnabled) return;
  try {
    new Notification(title, { body, tag });
  } catch (_err) {
    // Ignore notification delivery errors.
  }
}

function latestExpectedNowPct(rows) {
  const expectedData = computeExpectedWeeklyTrace(rows);
  return expectedData ? expectedData.expectedNowPct : null;
}

function evaluateThresholdNotificationsNow() {
  if (!currentRows.length) return;
  const latest = currentRows[currentRows.length - 1];
  const hasSonnet = currentRows.some((r) => r.sonnet_pct !== null && r.sonnet_pct !== undefined);
  const sonnetExpected = hasSonnet ? computeExpectedSonnetTrace(currentRows) : null;
  const sessionExpected = computeExpectedSessionTrace(currentRows);
  maybeNotifyThresholds(
    latest,
    sessionExpected ? sessionExpected.expectedNowPct : null,
    latestExpectedNowPct(currentRows),
    sonnetExpected ? sonnetExpected.expectedNowPct : null
  );
}

function maybeNotifyThresholds(latest, expectedSessionNowPct, expectedWeeklyNowPct, expectedSonnetNowPct) {
  if (!latest || !notificationsSupported() || Notification.permission !== 'granted' || !alertsEnabled) return;

  const state = notificationState();

  maybeNotifyThresholdCrossing(
    state,
    'session',
    latest.session_pct,
    notifySessionThresholdPct,
    latest.session_resets || 'unknown',
    'Claudometer: session usage alert',
    `Current session usage reached ${fmtPct(latest.session_pct)}.`
  );
  maybeNotifyExpectedSessionOverrun(state, latest, expectedSessionNowPct);

  maybeNotifyThresholdCrossing(
    state,
    'weekly',
    latest.weekly_pct,
    notifyWeeklyThresholdPct,
    latest.weekly_resets || 'unknown',
    'Claudometer: weekly usage alert',
    `Weekly usage reached ${fmtPct(latest.weekly_pct)}.`
  );

  maybeNotifyThresholdCrossing(
    state,
    'extra',
    latest.extra_pct,
    notifyExtraThresholdPct,
    latest.extra_monthly_limit || 'extra',
    'Claudometer: extra usage alert',
    `Extra usage reached ${fmtPct(latest.extra_pct)}.`
  );

  if (latest.sonnet_pct !== null && latest.sonnet_pct !== undefined) {
    maybeNotifyThresholdCrossing(
      state,
      'sonnet',
      latest.sonnet_pct,
      notifySonnetThresholdPct,
      latest.weekly_resets || 'unknown',
      'Claudometer: Sonnet usage alert',
      `Sonnet-only usage reached ${fmtPct(latest.sonnet_pct)}.`
    );
    maybeNotifyExpectedSonnetOverrun(state, latest, expectedSonnetNowPct);
  }

  maybeNotifyExpectedOverrun(state, latest, expectedWeeklyNowPct);

  setNotificationState(state);
}

function maybeNotifyThresholdCrossing(state, key, currentValue, threshold, marker, title, bodyPrefix) {
  if (threshold === null || currentValue === null || currentValue === undefined) return;
  const entryKey = `${threshold}:${marker || 'unknown'}`;
  const entry = state[key] || {};

  if (entry.key !== entryKey) {
    entry.key = entryKey;
    entry.alerted = false;
  }

  if (currentValue >= threshold) {
    if (!entry.alerted) {
      showNotification(title, `${bodyPrefix} Threshold ${fmtPct(threshold)}.`, `${key}:${entryKey}`);
      entry.alerted = true;
    }
  } else {
    entry.alerted = false;
  }

  state[key] = entry;
}

function maybeNotifyExpectedOverrun(state, latest, expectedNowPct) {
  if (
    !notifyExpectedWeeklyOverrunEnabled ||
    latest.weekly_pct === null ||
    latest.weekly_pct === undefined ||
    expectedNowPct === null ||
    expectedNowPct === undefined
  ) {
    return;
  }

  const entryKey = latest.weekly_resets || 'unknown';
  const entry = state.expectedOverrun || {};
  if (entry.key !== entryKey) {
    entry.key = entryKey;
    entry.alerted = false;
  }

  if (latest.weekly_pct > expectedNowPct) {
    if (!entry.alerted) {
      showNotification(
        'Claudometer: weekly usage above expected',
        `Weekly usage is ${fmtPct(latest.weekly_pct)}, above the expected ${fmtPct(expectedNowPct)}.`,
        `expected-overrun:${entryKey}`
      );
      entry.alerted = true;
    }
  } else {
    entry.alerted = false;
  }

  state.expectedOverrun = entry;
}

function maybeNotifyExpectedSessionOverrun(state, latest, expectedNowPct) {
  if (
    !notifyExpectedSessionOverrunEnabled ||
    latest.session_pct === null ||
    latest.session_pct === undefined ||
    expectedNowPct === null ||
    expectedNowPct === undefined
  ) {
    return;
  }

  const entryKey = latest.session_resets || 'unknown';
  const entry = state.expectedSessionOverrun || {};
  if (entry.key !== entryKey) {
    entry.key = entryKey;
    entry.alerted = false;
  }

  if (latest.session_pct > expectedNowPct) {
    if (!entry.alerted) {
      showNotification(
        'Claudometer: session usage above expected',
        `Session usage is ${fmtPct(latest.session_pct)}, above the expected ${fmtPct(expectedNowPct)}.`,
        `expected-session-overrun:${entryKey}`
      );
      entry.alerted = true;
    }
  } else {
    entry.alerted = false;
  }

  state.expectedSessionOverrun = entry;
}

function maybeNotifyExpectedSonnetOverrun(state, latest, expectedNowPct) {
  if (
    !notifyExpectedSonnetOverrunEnabled ||
    latest.sonnet_pct === null ||
    latest.sonnet_pct === undefined ||
    expectedNowPct === null ||
    expectedNowPct === undefined
  ) {
    return;
  }

  const entryKey = latest.weekly_resets || 'unknown';
  const entry = state.expectedSonnetOverrun || {};
  if (entry.key !== entryKey) {
    entry.key = entryKey;
    entry.alerted = false;
  }

  if (latest.sonnet_pct > expectedNowPct) {
    if (!entry.alerted) {
      showNotification(
        'Claudometer: Sonnet usage above expected',
        `Sonnet-only usage is ${fmtPct(latest.sonnet_pct)}, above the expected ${fmtPct(expectedNowPct)}.`,
        `expected-sonnet-overrun:${entryKey}`
      );
      entry.alerted = true;
    }
  } else {
    entry.alerted = false;
  }

  state.expectedSonnetOverrun = entry;
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

// Expected session line: simple linear 0% → 100% over the current 5-hour window.
// Only drawn for the latest (active) session — identified by session_resets matching
// the most recent row. Past sessions are ignored entirely.
function computeExpectedSessionTrace(rows) {
  if (!expectedLineEnabled || rows.length < 1) return null;
  const latest = rows[rows.length - 1];
  if (!latest.session_resets) return null;
  const cycleEnd = new Date(latest.session_resets);
  if (Number.isNaN(cycleEnd.getTime())) return null;
  const cycleStart = new Date(cycleEnd.getTime() - 5 * 60 * 60 * 1000);
  const now = new Date();
  const elapsed = Math.max(0, now.getTime() - cycleStart.getTime());
  const total = cycleEnd.getTime() - cycleStart.getTime();
  const nowPct = Math.max(0, Math.min(100, (elapsed / total) * 100));
  return {
    trace: {
      x: [toLocalPlotTs(cycleStart.toISOString()), toLocalPlotTs(cycleEnd.toISOString())],
      y: [0, 100],
      mode: 'lines',
      name: 'Expected session usage',
      line: { color: 'rgba(31,141,235,0.45)', width: 1.5, dash: '3px,3px' },
      hovertemplate: 'Expected session: %{y:.1f}%<br>Time: %{x|%Y-%m-%d %H:%M}<extra>Expected session usage</extra>'
    },
    expectedNowPct: nowPct
  };
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
      line: { color: 'rgba(170,170,170,0.75)', width: 1.5, dash: '3px,3px' },
      hovertemplate: 'Expected: %{y:.1f}%<br>Time: %{x|%Y-%m-%d %H:%M}<extra>Expected weekly usage</extra>'
    },
    expectedNowPct: nowPct
  };
}

// Sonnet expected line: uses the same active-hours settings and weekly_resets cycle
// (Sonnet resets at the same time as weekly usage).
function computeExpectedSonnetTrace(rows) {
  if (!expectedLineEnabled || rows.length < 1) return null;
  // Only compute when Sonnet data is actually present
  if (!rows.some((r) => r.sonnet_pct !== null && r.sonnet_pct !== undefined)) return null;

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
      name: 'Expected Sonnet usage',
      line: { color: 'rgba(192,132,252,0.65)', width: 1.5, dash: '3px,3px' },
      hovertemplate: 'Expected Sonnet: %{y:.1f}%<br>Time: %{x|%Y-%m-%d %H:%M}<extra>Expected Sonnet usage</extra>'
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

function maskedSeries(rows, key, predicate) {
  const values = seriesFor(rows, key);
  return values.map((value, index) => predicate(rows[index]) ? value : null);
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
    // Fall back to actual first data point rather than next-7days: a plan switch
    // mid-cycle creates a new cycle starting before the previous weekly_resets date,
    // so next-7days would cut off the early part of the cycle.
    const start = prev || (rows.length > 0 ? new Date(rows[0].ts) : new Date(next.getTime() - 7 * 24 * 60 * 60 * 1000));
    return [toLocalPlotTs(start.toISOString()), toLocalPlotTs(next.toISOString())];
  }

  return null;
}

function currentTheme() {
  return themeMode === 'light'
    ? {
        bodyClass: 'theme-light',
        plotBg: 'rgba(255,255,255,0)',
        paperBg: 'rgba(255,255,255,0)',
        grid: '#e1e7ef',
        fg: '#1d2733'
      }
    : {
        bodyClass: 'theme-dark',
        plotBg: 'rgba(0,0,0,0)',
        paperBg: 'rgba(0,0,0,0)',
        grid: '#2a313d',
        fg: '#f3f5f7'
      };
}

function applyTheme() {
  const theme = currentTheme();
  document.body.classList.remove('theme-light', 'theme-dark');
  document.body.classList.add(theme.bodyClass);
  themeToggleEl.textContent = themeMode === 'light' ? 'Dark' : 'Light';
  updateNotificationsToggle();
}

function isCompactViewport() {
  return window.matchMedia('(max-width: 720px)').matches;
}

function effectiveRangePreset() {
  if (rangePreset === 'manual') return rangePreset;
  if (isCompactViewport() && !storageGet('tracker_range_preset', null)) return 'today';
  return rangePreset;
}

function backendRangePreset() {
  const preset = effectiveRangePreset();
  if (preset === 'manual') return dataRangePreset || 'weekly_cycle';
  return preset;
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
  rangePresetEl.value = effectiveRangePreset();
  applyTheme();
  dataRangePreset = backendRangePreset();

  viewModeEl.addEventListener('change', () => {
    viewMode = viewModeEl.value;
    storageSet('tracker_view_mode', viewMode);
    rerenderChartWithLoading(currentRows);
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
    if (rangePreset === 'manual') {
      rerenderChartWithLoading(currentRows);
      return;
    }
    dataRangePreset = rangePreset;
    refreshData();
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
    rerenderChartWithLoading(currentRows);
  });

  notificationsToggleEl.addEventListener('change', async () => {
    if (!notificationsSupported()) return;
    if (Notification.permission === 'granted') {
      const wasEnabled = alertsEnabled;
      alertsEnabled = notificationsToggleEl.checked;
      storageSet('tracker_alerts_enabled', alertsEnabled ? 'true' : 'false');
      if (!wasEnabled && alertsEnabled) {
        clearNotificationState();
      }
      updateNotificationsToggle();
      if (alertsEnabled) {
        showNotification(
          'Claudometer alerts enabled',
          'Browser notifications are on for this dashboard.',
          'alerts-enabled'
        );
        evaluateThresholdNotificationsNow();
      }
      return;
    }
    if (!notificationsToggleEl.checked) {
      alertsEnabled = false;
      storageSet('tracker_alerts_enabled', 'false');
      updateNotificationsToggle();
      return;
    }
    if (Notification.permission === 'default') {
      try {
        await Notification.requestPermission();
      } catch (_err) {
        // Ignore permission request failures.
      }
    }
    if (Notification.permission === 'granted') {
      const wasEnabled = alertsEnabled;
      alertsEnabled = true;
      storageSet('tracker_alerts_enabled', 'true');
      if (!wasEnabled) {
        clearNotificationState();
      }
    } else {
      notificationsToggleEl.checked = false;
    }
    updateNotificationsToggle();
    if (Notification.permission === 'granted' && alertsEnabled) {
      showNotification(
        'Claudometer alerts enabled',
        'Browser notifications are on for this dashboard.',
        'alerts-enabled'
      );
      evaluateThresholdNotificationsNow();
    }
  });

  summaryBodyEl.addEventListener('input', (evt) => {
    const target = evt.target;
    if (!(target instanceof HTMLInputElement)) return;
    const setting = target.dataset.alertSetting;
    if (!setting) return;
    if (target.type === 'checkbox') {
      alertSettingsDraft[setting] = target.checked;
    } else {
      const raw = target.value.trim();
      alertSettingsDraft[setting] = raw === '' ? null : raw;
    }
    refreshSaveAlertButton();
    setSaveAlertStatus('Unsaved alert settings');
  });

  summaryBodyEl.addEventListener('change', (evt) => {
    const target = evt.target;
    if (!(target instanceof HTMLInputElement)) return;
    const setting = target.dataset.alertSetting;
    if (!setting) return;
    if (target.type === 'checkbox') {
      alertSettingsDraft[setting] = target.checked;
      refreshSaveAlertButton();
      setSaveAlertStatus('Unsaved alert settings');
    }
  });

  saveAlertSettingsEl.addEventListener('click', saveAlertSettings);

}

async function saveAlertSettings() {
  saveAlertSettingsEl.disabled = true;
  setSaveAlertStatus('Saving...');
  try {
    const payload = {
      session_threshold_pct: normalizeDraftThreshold(alertSettingsDraft.session_threshold_pct),
      weekly_threshold_pct: normalizeDraftThreshold(alertSettingsDraft.weekly_threshold_pct),
      extra_threshold_pct: normalizeDraftThreshold(alertSettingsDraft.extra_threshold_pct),
      sonnet_threshold_pct: normalizeDraftThreshold(alertSettingsDraft.sonnet_threshold_pct),
      expected_weekly_overrun_enabled: !!alertSettingsDraft.expected_weekly_overrun_enabled,
      expected_sonnet_overrun_enabled: !!alertSettingsDraft.expected_sonnet_overrun_enabled,
      expected_session_overrun_enabled: !!alertSettingsDraft.expected_session_overrun_enabled
    };
    const res = await fetch('/notification-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const body = await res.json();
    if (!res.ok) {
      throw new Error(body.error || ('HTTP ' + res.status));
    }
    applyNotificationSettings(body.notification_settings, true);
    rerenderChartWithLoading(currentRows);
    setSaveAlertStatus('Alert settings saved');
    clearSaveAlertStatusLater();
  } catch (err) {
    setSaveAlertStatus('Save failed: ' + err, true);
  } finally {
    refreshSaveAlertButton();
  }
}

function normalizeDraftThreshold(rawValue) {
  if (rawValue === null || rawValue === undefined || rawValue === '') return null;
  const parsed = Number(rawValue);
  return Number.isFinite(parsed) ? parsed : rawValue;
}

function applyNotificationSettings(settings, fromSave = false) {
  notifySessionThresholdPct = settings.session_threshold_pct ?? null;
  notifyWeeklyThresholdPct = settings.weekly_threshold_pct ?? null;
  notifyExtraThresholdPct = settings.extra_threshold_pct ?? null;
  notifySonnetThresholdPct = settings.sonnet_threshold_pct ?? null;
  notifyExpectedWeeklyOverrunEnabled = !!settings.expected_weekly_overrun_enabled;
  notifyExpectedSonnetOverrunEnabled = !!settings.expected_sonnet_overrun_enabled;
  notifyExpectedSessionOverrunEnabled = !!settings.expected_session_overrun_enabled;
  if (!alertSettingsDirty || fromSave) {
    syncAlertSettingsDraftFromRuntime();
  }
  if (fromSave) {
    alertSettingsDirty = false;
  }
  updateNotificationsToggle();
  refreshSaveAlertButton();
  if (fromSave && Notification.permission === 'granted') {
    evaluateThresholdNotificationsNow();
  }
}

function renderChart(rows) {
  const theme = currentTheme();
  const compact = isCompactViewport();
  const activeRangePreset = effectiveRangePreset();
  const lineWidth = compact ? 1.6 : 2.5;
  const markerSize = compact ? 2.5 : 4;
  rangePresetEl.value = activeRangePreset;

  if (!rows.length) {
    Plotly.newPlot(chartEl, [], {
      title: null,
      uirevision: 'keep-zoom',
      paper_bgcolor: theme.paperBg,
      plot_bgcolor: theme.plotBg,
      font: { color: theme.fg },
      xaxis: { title: compact ? null : 'Time (Local)', tickformat: '%Y-%m-%d %H:%M', hoverformat: '%Y-%m-%d %H:%M', gridcolor: theme.grid },
      yaxis: { title: compact ? null : 'Utilization (%)', range: [0, 102], gridcolor: theme.grid },
      margin: compact ? { t: 20, r: 18, b: 48, l: 42 } : { t: 24, r: 30, b: 72, l: 60 }
    }, { responsive: true });
    ensureRelayoutBinding();
    renderSummaryTable(null, null, null, null);
    setChartLoading(false);
    return;
  }

  const x = rows.map((r) => toLocalPlotTs(r.ts));
  const hasSonnet = rows.some((r) => r.sonnet_pct !== null && r.sonnet_pct !== undefined);
  const traces = [
    {
      x,
      y: maskedSeries(rows, 'extra_pct', (row) => row.extra_enabled !== false),
      mode: 'lines+markers',
      name: 'Extra usage',
      line: { color: '#1db6a3', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 30,
      cliponaxis: false
    },
    {
      x,
      y: maskedSeries(rows, 'extra_pct', (row) => row.extra_enabled === false),
      mode: 'lines+markers',
      name: 'Extra usage (disabled)',
      line: { color: '#0f5f50', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 40,
      cliponaxis: false
    },
    {
      x,
      y: seriesFor(rows, 'session_pct'),
      mode: 'lines+markers',
      name: 'Current session',
      line: { color: '#1f8deb', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 10,
      cliponaxis: false
    },
    {
      x,
      y: seriesFor(rows, 'weekly_pct'),
      mode: 'lines+markers',
      name: 'Weekly',
      line: { color: '#ff6a2b', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 20,
      cliponaxis: false
    }
  ];

  if (hasSonnet) {
    traces.push({
      x,
      y: seriesFor(rows, 'sonnet_pct'),
      mode: 'lines+markers',
      name: 'Sonnet only',
      line: { color: '#c084fc', width: lineWidth },
      marker: { size: markerSize },
      legendrank: 25,
      cliponaxis: false
    });
  }

  const expectedData = computeExpectedWeeklyTrace(rows);
  if (expectedData) {
    expectedData.trace.legendrank = 50;
    traces.push(expectedData.trace);
  }

  // Sonnet expected line is identical to the weekly expected line, so we only
  // compute it for the table/notifications — no separate chart trace needed.
  const expectedSonnetData = hasSonnet ? computeExpectedSonnetTrace(rows) : null;

  const expectedSessionData = computeExpectedSessionTrace(rows);
  if (expectedSessionData) {
    expectedSessionData.trace.legendrank = 5;
    traces.push(expectedSessionData.trace);
  }

  const latest = rows[rows.length - 1];
  statusEl.textContent =
    'Samples: ' + currentTotalSamples +
    ' | Polling interval: __POLL_INTERVAL_SECONDS__s' +
    ' | Last sample: ' + formatLocalDateTimeFull(latest.ts);
  renderSummaryTable(
    latest,
    expectedSessionData ? expectedSessionData.expectedNowPct : null,
    expectedData ? expectedData.expectedNowPct : null,
    expectedSonnetData ? expectedSonnetData.expectedNowPct : null
  );
  maybeNotifyThresholds(
    latest,
    expectedSessionData ? expectedSessionData.expectedNowPct : null,
    expectedData ? expectedData.expectedNowPct : null,
    expectedSonnetData ? expectedSonnetData.expectedNowPct : null
  );

  const xaxisLayout = {
    title: compact ? null : 'Time (Local)',
    tickformat: '%Y-%m-%d %H:%M',
    hoverformat: '%Y-%m-%d %H:%M',
    gridcolor: theme.grid
  };

  if (activeRangePreset === 'manual' && userXRange) {
    xaxisLayout.range = userXRange;
    xaxisLayout.autorange = false;
  } else {
    const presetRange = computeRangePreset(rows, activeRangePreset);
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
    yaxis: { title: compact ? null : 'Utilization (%)', range: [0, 102], gridcolor: theme.grid },
    shapes: buildShapes(rows),
    margin: compact ? { t: 20, r: 18, b: 88, l: 42 } : { t: 28, r: 30, b: 100, l: 60 },
    legend: compact
      ? { orientation: 'h', y: -0.28, yanchor: 'top', x: 0, font: { size: 11 }, traceorder: 'normal' }
      : { orientation: 'h', y: -0.18, yanchor: 'top', traceorder: 'normal' }
  }, { responsive: true });

  ensureRelayoutBinding();
  hasInitializedXRange = true;
  hasLoadedInitialData = true;
  setChartLoading(false);
}

async function refreshData() {
  try {
    setChartLoading(true);
    const params = new URLSearchParams({ range: backendRangePreset() });
    const res = await fetch('/data.json?' + params.toString(), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const payload = await res.json();
    if (payload.notification_settings) {
      applyNotificationSettings(payload.notification_settings);
    }
    currentRows = payload.rows || [];
    currentTotalSamples = payload.total_samples || currentRows.length;
    renderChart(currentRows);
  } catch (err) {
    setChartLoading(false);
    statusEl.textContent = 'Data fetch failed: ' + err;
  }
}

bindControls();
renderChart([]);
refreshData();
window.addEventListener('resize', () => rerenderChartWithLoading(currentRows));
setInterval(refreshData, __POLL_MS__);
"""
    return (
        js.replace("__POLL_MS__", str(poll_ms))
        .replace("__POLL_INTERVAL_SECONDS__", str(poll_interval_seconds))
        .replace("__EXPECTED_LINE_ENABLED__", "true" if expected_weekly_line_enabled else "false")
        .replace("__EXPECTED_ACTIVE_START__", expected_active_start_hhmm)
        .replace("__EXPECTED_ACTIVE_END__", expected_active_end_hhmm)
        .replace(
            "__NOTIFY_SESSION_THRESHOLD_PCT__",
            "null" if notify_session_threshold_pct is None else str(float(notify_session_threshold_pct)),
        )
        .replace(
            "__NOTIFY_WEEKLY_THRESHOLD_PCT__",
            "null" if notify_weekly_threshold_pct is None else str(float(notify_weekly_threshold_pct)),
        )
        .replace(
            "__NOTIFY_EXTRA_THRESHOLD_PCT__",
            "null" if notify_extra_threshold_pct is None else str(float(notify_extra_threshold_pct)),
        )
        .replace(
            "__NOTIFY_SONNET_THRESHOLD_PCT__",
            "null" if notify_sonnet_threshold_pct is None else str(float(notify_sonnet_threshold_pct)),
        )
        .replace(
            "__NOTIFY_EXPECTED_WEEKLY_OVERRUN_ENABLED__",
            "true" if notify_expected_weekly_overrun_enabled else "false",
        )
        .replace(
            "__NOTIFY_EXPECTED_SONNET_OVERRUN_ENABLED__",
            "true" if notify_expected_sonnet_overrun_enabled else "false",
        )
        .replace(
            "__NOTIFY_EXPECTED_SESSION_OVERRUN_ENABLED__",
            "true" if notify_expected_session_overrun_enabled else "false",
        )
    )
