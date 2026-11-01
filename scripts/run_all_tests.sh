#!/usr/bin/env bash
# Run every replica test suite sequentially; results are appended to one log.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${1:-docs/test-results.log}"
: > "$LOG"
fail=0
for t in test_db test_files test_archive test_archive_ui test_network test_jumps test_chromeproxy test_chromeproxy_jumps test_docker test_redis test_ai test_sync test_extras test_tasks test_serial test_images test_objects test_settings test_cluster test_tree test_assetmap test_agent_undo test_vnc test_sql_editor test_registry test_duckdb test_clickhouse test_mongodb test_trzsz test_local_registry test_zmodem test_x11 test_redisjson test_rpc_proto test_dameng test_sqlserver test_oracle test_split test_lock test_update test_reserved; do
  echo "===== $t =====" | tee -a "$LOG"
  # test_oracle talks to a remote shared Oracle instance (~5 s per API
  # round-trip, plus DPY-4011 reset retries); the local suites stay at 500 s.
  limit=500
  [ "$t" = test_oracle ] && limit=900
  if timeout "$limit" python3 "scripts/$t.py" >>"$LOG" 2>&1; then
    grep -E "^PASS=" "$LOG" | tail -1 | sed "s/^/OK   $t /" | tee -a "$LOG"
  else
    echo "FAIL $t" | tee -a "$LOG"
    fail=1
  fi
done
echo "===== ui_flow =====" | tee -a "$LOG"
if timeout 400 python3 scripts/ui_flow.py >>"$LOG" 2>&1 && grep -q "ERRORS: \[\]" "$LOG"; then
  echo "OK   ui_flow ERRORS: []" | tee -a "$LOG"
else
  echo "FAIL ui_flow" | tee -a "$LOG"
  fail=1
fi
echo "===== summary =====" | tee -a "$LOG"
grep -E "^(OK|FAIL) " "$LOG" | tee -a "$LOG"
exit $fail
