#!/usr/bin/env bash
# Start the optional lab services used by the demo assets.
#
#   RedisJSON  127.0.0.1:6385  (module /home/kali/hexhub-lab/redis-modules/h1khub_json.so)
#   MongoDB    127.0.0.1:27017 (extracted mongod under ~/hexhub-lab/mongodb-7.0)
#   ClickHouse 127.0.0.1:9000  (system package; started with sudo, not here)
#
# All data lives in temporary directories; this never touches the user's
# existing databases or containers.
set -uo pipefail

HOME_DIR="${HOME}"
ok=0

port_open() {
  (echo > "/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1
}

# ---------------------------------------------------------------- RedisJSON
if port_open 6385; then
  echo "RedisJSON  6385  already running"
else
  MODULE=""
  for candidate in "$HOME_DIR/hexhub-lab/redis-modules/h1khub_json.so" \
                   "$HOME_DIR/.hexhub-lab/redis-modules/h1khub_json.so" \
                   "$HOME_DIR/hexhub-lab/redis-modules/hexhub_json.so" \
                   "$HOME_DIR/.hexhub-lab/redis-modules/hexhub_json.so"; do
    if [ -f "$candidate" ]; then MODULE="$candidate"; break; fi
  done
  if [ -z "$MODULE" ]; then
    echo "RedisJSON  6385  SKIP (h1khub_json.so not found; run tools/redisjson/build.sh first)" >&2
    ok=1
  else
    mkdir -p /tmp/redisjson
    if redis-server --port 6385 --bind 127.0.0.1 --daemonize yes \
        --loadmodule "$MODULE" --save "" --dir /tmp/redisjson >/dev/null 2>&1; then
      echo "RedisJSON  6385  started ($MODULE)"
    else
      echo "RedisJSON  6385  FAILED to start" >&2
      ok=1
    fi
  fi
fi

# ------------------------------------------------------------------ MongoDB
if port_open 27017; then
  echo "MongoDB    27017 already running"
else
  MONGOD="$HOME_DIR/hexhub-lab/mongodb-7.0/usr/bin/mongod"
  if [ ! -x "$MONGOD" ]; then
    echo "MongoDB    27017 SKIP ($MONGOD not found)" >&2
    ok=1
  else
    mkdir -p /tmp/hexhub-lab-mongo
    if "$MONGOD" --dbpath /tmp/hexhub-lab-mongo --port 27017 --bind_ip 127.0.0.1 \
        --logpath /tmp/hexhub-lab-mongo/mongod.log --fork >/dev/null 2>&1; then
      echo "MongoDB    27017 started"
    else
      echo "MongoDB    27017 FAILED to start (see /tmp/hexhub-lab-mongo/mongod.log)" >&2
      ok=1
    fi
  fi
fi

# --------------------------------------------------------------- ClickHouse
if port_open 9000; then
  echo "ClickHouse 9000  already running"
else
  echo "ClickHouse 9000  not running; start it with: sudo clickhouse start" >&2
  ok=1
fi

# Redis / MariaDB / PostgreSQL are expected to be pre-existing system services.
for spec in "Redis:6380" "MariaDB:3307" "PostgreSQL:5433"; do
  name="${spec%%:*}"; port="${spec##*:}"
  if port_open "$port"; then
    echo "$name ${port} running"
  else
    echo "$name ${port} not running (demo asset may fail)" >&2
  fi
done

# --------------------------------------------------------------- SQL Server
# The lab pins the 2019 image: the 2022 image never leaves PAL startup on this
# host (VMware VM, Linux 6.19). scripts/start_mssql.sh creates the demo database.
if port_open 14330; then
  echo "SQL Server 14330 running"
else
  echo "SQL Server 14330 not running; start it with: scripts/start_mssql.sh" >&2
  ok=1
fi

exit "$ok"
