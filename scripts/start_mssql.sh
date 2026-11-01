#!/usr/bin/env bash
# Start the local SQL Server lab instance used by the demo asset.
#
#   container  hexhub-mssql-lab   image hexhub-mssql:2019   host port 14330
#
# SQL Server 2022 (hexhub-mssql:2022) never finishes PAL startup on this host
# (VMware VM, Linux 6.19): it spins at 100% CPU right after "Buffer pool
# extension is already disabled" and never opens TDS. The 2019 image starts in
# about 80 s with the same settings, so the lab pins 2019. See
# docs/verification.md section 24 for the full comparison.
#
# Idempotent: an already running container is left untouched, and the demo
# database is only created when it is missing.
set -euo pipefail

IMAGE="${H1KHUB_MSSQL_IMAGE:-hexhub-mssql:2019}"
CONTAINER="${H1KHUB_MSSQL_CONTAINER:-mssql2019}"
PORT="${H1KHUB_MSSQL_PORT:-14330}"
PASSWORD="${H1KHUB_DEMO_SQLSERVER_PASSWORD:-Hexhub@12345}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  cat >&2 <<EOF
image $IMAGE not found. Pull it with:
  python3 $ROOT/scripts/pull_image_fast.py --registry mcr.microsoft.com \\
      --repo mssql/server --tag 2019-latest --repo-tag hexhub-mssql:2019
EOF
  exit 1
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" = "true" ]; then
  echo "container $CONTAINER already running"
else
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  # 2019 expects SA_PASSWORD; MSSQL_SA_PASSWORD is only read by the 2022 image.
  docker run -d --name "$CONTAINER" \
    -e ACCEPT_EULA=Y -e MSSQL_PID=Developer \
    -e "SA_PASSWORD=$PASSWORD" -e "MSSQL_SA_PASSWORD=$PASSWORD" \
    -p "$PORT:1433" "$IMAGE" >/dev/null
  echo "container $CONTAINER started on 127.0.0.1:$PORT"
fi

for _ in $(seq 1 60); do
  if (echo > "/dev/tcp/127.0.0.1/$PORT") >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

# The port can answer before the engine finishes recovering system databases.
python3 "$ROOT/scripts/lab_mssql_setup.py" --host 127.0.0.1 --port "$PORT" --wait 180
