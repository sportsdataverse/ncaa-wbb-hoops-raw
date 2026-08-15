#!/usr/bin/env bash
# Watchdog: kill capture workers that have stopped producing.
#
# WHY: a sticky Decodo session stops clearing bm-verify after ~70 min
# (docs/SCRAPING_NOTES.md, 2026-07-13). A season takes ~2 h at 32 workers, so
# the last few workers of every season outlive their session and spin without
# writing anything. Their run_02_games.sh wrapper has already printed EXIT=,
# so the python child is ORPHANED -- but the season driver is still blocked in
# `wait`, and the whole campaign stops until someone kills them by hand. That
# happened on season 2026 (2026-08-13): 4 workers, 43 min of zero output, the
# range driver stuck before its parse stage.
#
# WHAT: every INTERVAL seconds, compare the newest bundle mtime under
# <lg>/raw to now. If nothing has been written for STALL_S AND capture workers
# are alive, kill those workers by PID. The season driver's `wait` then
# returns, parse runs, and the campaign advances. Nothing is lost: the disk is
# the checkpoint and a later re-run picks up the stragglers.
#
#   nohup bash ops/watchdog_stalled_capture.sh >> logs/watchdog.log 2>&1 &
#
# Env: STALL_S (default 480 -- a cold bm-verify solve is 45-80s and retries a
#      few minutes, so 8min clears real work but not a hang), INTERVAL
#      (default 120), LEAGUE (auto).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
case "$PWD" in *wbb*) LEAGUE="${LEAGUE:-wbb}" ;; *) LEAGUE="${LEAGUE:-mbb}" ;; esac
STALL_S="${STALL_S:-480}"
INTERVAL="${INTERVAL:-120}"

mkdir -p logs
say() { echo "[$(date '+%F %T')] $*"; }
say "watchdog: league=${LEAGUE} stall=${STALL_S}s interval=${INTERVAL}s"

# Count capture workers (python only -- NEVER chrome: on this box the chrome
# processes are the user's own browser, not patchright's).
workers() {
  powershell -NoProfile -Command \
    "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'ncaa_${LEAGUE}_02_games' }).Count" 2>/dev/null | tr -d '\r'
}
newest_bundle_age() {
  local newest
  newest=$(find "${LEAGUE}/raw" -name '*.json.gz' -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
  [ -z "$newest" ] && { echo 999999; return; }
  echo $(( $(date +%s) - ${newest%.*} ))
}

# Age (seconds) of the YOUNGEST capture worker. The stall clock must be judged
# against how long THESE workers have been running, not wall-clock since the
# last bundle: between seasons the parse stage writes no bundles at all, so the
# bundle clock is already expired when the next season's workers start. Without
# this guard the watchdog killed season 2023 THIRTY-ONE SECONDS after it began
# and the driver moved on with 5,821 contests uncaptured (2026-08-13).
youngest_worker_age() {
  local iso
  iso=$(powershell -NoProfile -Command \
    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'ncaa_${LEAGUE}_02_games' } | Sort-Object CreationDate -Descending | Select-Object -First 1).CreationDate.ToString('yyyy-MM-ddTHH:mm:ss')" 2>/dev/null | tr -d '\r')
  [ -z "$iso" ] && { echo 0; return; }
  echo $(( $(date +%s) - $(date -d "$iso" +%s 2>/dev/null || echo "$(date +%s)") ))
}

while :; do
  n=$(workers); n=${n:-0}
  if [ "$n" -gt 0 ]; then
    age=$(newest_bundle_age)
    wage=$(youngest_worker_age)
    if [ "$wage" -lt "$STALL_S" ]; then
      # Fresh workers: give them at least STALL_S to produce their first bundle.
      sleep "$INTERVAL"; continue
    fi
    if [ "$age" -gt "$STALL_S" ]; then
      say "STALL: ${n} worker(s) alive, no bundle written for ${age}s (>${STALL_S}) -- killing them"
      powershell -NoProfile -Command \
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match 'ncaa_${LEAGUE}_02_games' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue; \"  killed \$(\$_.ProcessId)\" }" 2>/dev/null
      say "killed; the season driver's wait() should now return and the campaign advance"
      sleep 60   # let the driver move on before re-arming
    fi
  fi
  sleep "$INTERVAL"
done
