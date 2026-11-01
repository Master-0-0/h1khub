#!/usr/bin/env bash
# Restart the H1KHub backend on a given port (default 38110).
set -euo pipefail
PORT="${1:-38110}"
HOME_DIR="${H1KHUB_HOME:-$HOME/.h1khub}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if pgrep -f "backend/app.py --port ${PORT}" >/dev/null 2>&1; then
  pkill -f "backend/app.py --port ${PORT}" || true
  sleep 1.2
fi
nohup bash scripts/start.sh --port "${PORT}" --home "${HOME_DIR}" --no-open \
  >/tmp/h1khub.log 2>&1 &
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/public/info" >/dev/null 2>&1; then
    echo "h1khub listening on http://127.0.0.1:${PORT}"
    curl -s "http://127.0.0.1:${PORT}/api/public/info"
    echo
    exit 0
  fi
  sleep 0.4
done
echo "failed to start; log tail:" >&2
tail -30 /tmp/h1khub.log >&2
exit 1
