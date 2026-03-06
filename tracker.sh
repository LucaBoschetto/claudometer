#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/.claude-usage-tracker"
PID_FILE="$RUNTIME_DIR/dashboard.pid"
LOG_FILE="$RUNTIME_DIR/dashboard.log"
LAUNCHER_LOG="$RUNTIME_DIR/dashboard-launcher.log"

mkdir -p "$RUNTIME_DIR"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
  if is_running; then
    echo "Already running (PID $(cat "$PID_FILE"))."
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "http://127.0.0.1:7474" >/dev/null 2>&1 || true
    fi
    exit 0
  fi

  cd "$SCRIPT_DIR"
  nohup "$PYTHON_BIN" dashboard.py >>"$LAUNCHER_LOG" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 1
  if ! is_running; then
    echo "Failed to start dashboard.py. Recent launcher output:"
    tail -n 40 "$LAUNCHER_LOG" 2>/dev/null || true
    rm -f "$PID_FILE"
    exit 1
  fi
  echo "Started dashboard.py in background (PID $(cat "$PID_FILE"))."
  echo "Dashboard: http://127.0.0.1:7474"
  echo "App log: $LOG_FILE"
  echo "Launcher log: $LAUNCHER_LOG"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:7474" >/dev/null 2>&1 || true
  fi
}

stop() {
  if ! is_running; then
    echo "Not running."
    rm -f "$PID_FILE"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true

  for _ in {1..20}; do
    if kill -0 "$pid" 2>/dev/null; then
      sleep 0.2
    else
      break
    fi
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "Process still alive; sending SIGKILL to $pid"
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$PID_FILE"
  echo "Stopped."
}

status() {
  if is_running; then
    echo "Running (PID $(cat "$PID_FILE"))."
  else
    rm -f "$PID_FILE"
    echo "Not running."
  fi
}

logs() {
  tail -f "$LOG_FILE"
}

launcher_logs() {
  tail -f "$LAUNCHER_LOG"
}

foreground() {
  cd "$SCRIPT_DIR"
  exec "$PYTHON_BIN" dashboard.py
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs ;;
  launcher-logs) launcher_logs ;;
  fg|foreground) foreground ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs|launcher-logs|foreground}"
    exit 2
    ;;
esac
