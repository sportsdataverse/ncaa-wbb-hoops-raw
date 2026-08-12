#!/usr/bin/env bash
# User-run launcher: parse captured raw bundles -> combined per-contest JSON.
# Fully offline (reads local raw/*.json.gz only) -- no proxy creds needed.
#
# Fans out PARSE_WORKERS shard processes (default 12). Pass an explicit
# --shard i/N to run exactly one shard instead. Parse skips games whose json
# already exists, so shards never collide and a re-run is always safe.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1   # -> ncaa-wbb-hoops-raw repo root
ROOT="$(pwd)"
SDV_PY="${SDV_PY:-C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py}"
PARSE_WORKERS="${PARSE_WORKERS:-12}"

export PYTHONPATH="${SDV_PY}:${ROOT}/python"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/parse_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"

# .venv layout is OS-dependent: Linux/droplet = .venv/bin, Windows = .venv/Scripts
if [ -x "${SDV_PY}/.venv/bin/python" ]; then PY="${PY:-${SDV_PY}/.venv/bin/python}"
else PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; fi
rc=0
if [[ " $* " == *" --shard "* ]]; then
  "$PY" python/ncaa_wbb_03_games_parse.py "$@" 2>&1 | tee -a "${LOG}"
  rc=${PIPESTATUS[0]}
else
  pids=()
  for i in $(seq 0 $((PARSE_WORKERS - 1))); do
    "$PY" python/ncaa_wbb_03_games_parse.py "$@" --shard "${i}/${PARSE_WORKERS}" >> "${LOG}" 2>&1 &
    pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || rc=$?; done
  tail -3 "${LOG}"
fi
echo "EXIT=${rc}" | tee -a "${LOG}"
# Propagate the worst exit code -- a bare trailing `echo` would mask a
# ban hard-stop as success (it did: the 2026-07-13 backfill reported rc=0).
exit "${rc}"
