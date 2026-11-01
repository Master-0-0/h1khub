#!/usr/bin/env bash
# H1KHub replica launcher
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if ! python3 - <<'EOF' >/dev/null 2>&1
import aiohttp, paramiko, pymysql, psycopg2, redis, cryptography
EOF
then
  echo "Missing Python dependencies. Install them with:"
  echo "  python3 -m pip install -r requirements.txt"
  exit 1
fi

if [ ! -f frontend/dist/index.html ]; then
  echo "Frontend bundle not found, building…"
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the frontend" >&2
    exit 1
  fi
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi

# dmPython loads the DM client shared libraries at connect time. When a DM
# client directory is present, expose it to the dynamic loader for this process
# only; nothing is added to the system loader configuration.
for dm_dir in "${H1KHUB_DM_CLIENT:-}" /home/kali/dm_client/bin /opt/dmdbms/bin; do
  if [ -n "$dm_dir" ] && [ -d "$dm_dir" ]; then
    export LD_LIBRARY_PATH="$dm_dir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export DM_HOME="${H1KHUB_DM_HOME:-$(dirname "$dm_dir")}"
    break
  fi
done

exec python3 backend/app.py "$@"
