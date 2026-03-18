# Claude Usage Tracker (Local)

Lightweight local tracker for `https://claude.ai/settings/usage` that:

- Polls usage every `POLL_INTERVAL_SECONDS` (default `60`)
- Stores normalized usage runs in local SQLite (`~/.claude-usage-tracker/usage.db`)
- Serves a live dashboard at `http://127.0.0.1:7474`
- Optionally shows a dashed "Expected weekly usage" guideline
- Uses local time in chart/status display

## What It Tracks

- Current session usage (`five_hour.utilization`)
- Weekly usage (`seven_day.utilization`)
- Extra usage (`extra_usage.utilization`, or derived from `used_credits/monthly_limit`)
- Reset timestamps for session + weekly windows
- Extra-usage reset date is not currently captured (not present in the `/api/organizations/{org_id}/usage` JSON we use)

All percentages are normalized to `0..100` before storage. Reset timestamps are rounded to the nearest minute before run coalescing, and each run stores both its time span and represented sample count.

## Files

- `dashboard.py`: long-running process (poll + write DB + serve dashboard)
- `auth.py`: headed Playwright auth bootstrap and re-auth flow
- `bootstrap_from_curl.py`: fallback bootstrap from DevTools "Copy as cURL"
- `scraper_api.py`: Option A API polling logic
- `db.py`: SQLite schema and persistence
- `web.py`: HTTP server + Plotly frontend
- `config.py`: runtime config loading
- `tracker.sh`: launcher (`start|stop|restart|status|logs|launcher-logs|foreground`)

## Runtime Paths

- `~/.claude-usage-tracker/config.env`: auto-generated auth/config
- `~/.claude-usage-tracker/usage.db`: SQLite DB
- `~/.claude-usage-tracker/dashboard.log`: rotating app log
- `~/.claude-usage-tracker/dashboard-launcher.log`: launcher stdout/stderr
- `~/.claude-usage-tracker/browser-profile/`: Playwright profile

## Setup

1. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

2. Run:

```bash
python3 dashboard.py
```

Or with launcher:

```bash
./tracker.sh start
./tracker.sh status
./tracker.sh logs
./tracker.sh stop
```

## First Run

- A headed Chromium window opens at Claude login.
- Log in normally.
- Once authenticated, auth data is stored in `~/.claude-usage-tracker/config.env`.
- Polling starts and dashboard is available at `http://127.0.0.1:7474`.

## Re-authentication

During polling, if auth expires (401/403/redirect to login), the tool:

- Logs the auth failure
- Re-opens headed Chromium for login
- Resumes polling after successful login

## Cloudflare Loop Fallback

If headed Playwright gets stuck in a Cloudflare verification loop, bootstrap auth from a DevTools cURL capture:

1. In a normal browser session where Claude is already logged in, open `https://claude.ai/settings/usage`.
2. DevTools -> Network -> find `GET /api/organizations/<org_id>/usage`.
3. Right-click request -> Copy -> Copy as cURL.
4. Save that cURL command in a local file, for example `curl.txt`.
5. Run:

```bash
python3 bootstrap_from_curl.py curl.txt
```

This writes `~/.claude-usage-tracker/config.env` automatically.

## Expected Weekly Line Config

Set these in `~/.claude-usage-tracker/config.env`:

- `EXPECTED_WEEKLY_LINE_ENABLED=true|false` (default `true`)
- `EXPECTED_ACTIVE_START_HHMM=08:00` (default)
- `EXPECTED_ACTIVE_END_HHMM=19:00` (default)

Behavior:

- Dashed line progresses only during the active daily window
- Reaches `100%` at next weekly reset
- Summary line under the chart shows `Expected weekly usage (now)`

## Database Schema

```sql
CREATE TABLE usage_runs (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start            TEXT NOT NULL,
  ts_end              TEXT NOT NULL,
  sample_count        INTEGER NOT NULL,
  session_pct         REAL,
  session_resets      TEXT,
  weekly_pct          REAL,
  weekly_resets       TEXT,
  extra_pct           REAL,
  extra_enabled       INTEGER,
  extra_used_credits  REAL,
  extra_monthly_limit REAL
);
```

## Notes

- No cron, no systemd, no Docker.
- Keep `~/.claude-usage-tracker/config.env` private.
- If you exposed cookies/tokens, rotate Claude sessions and re-login.
- If you need `extra usage` reset timestamps later, capture additional Usage-page network calls; `/usage` alone does not currently expose that field.
