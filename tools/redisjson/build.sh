#!/usr/bin/env bash
# Build the H1KHub JSON Redis module against the running Redis headers.
# Usage: ./build.sh [output-dir]   (REDIS_SRC defaults to /tmp/redis-8.0.6)
set -euo pipefail
SRC="${REDIS_SRC:-/tmp/redis-8.0.6}"
OUT="${1:-$HOME/.hexhub-lab/redis-modules}"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SRC/src/redismodule.h" ]; then
  echo "redismodule.h not found in $SRC/src (set REDIS_SRC to a Redis source tree)" >&2
  exit 1
fi
mkdir -p "$OUT"
gcc -fPIC -shared -O2 -Wall -Wextra -I"$SRC/src" -o "$OUT/h1khub_json.so" "$HERE/h1khub_json.c" -lcjson
echo "built $OUT/h1khub_json.so"
