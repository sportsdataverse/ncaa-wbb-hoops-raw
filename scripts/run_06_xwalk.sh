#!/usr/bin/env bash
# User-run launcher (stage 06): NCAA contest_id <-> ESPN game_id crosswalk.
#
#   ./scripts/run_06_xwalk.sh --seasons 2027            # ending year
#   ./scripts/run_06_xwalk.sh --seasons 2027 --dry-run  # rates only
#
# OFFLINE except one load_wbb_schedule release read per season; no proxy.
# Reads wbb/schedules/parquet/{season}.parquet and the parsed wbb/json/{contest_id}.json
# (stage 03); writes wbb/xwalk/espn_game_id/{season}.json, then stamps espn_game_id into
# parsed contests that lack it (sdv-py stamp_parsed).
#
# Watch live:   tail -f logs/game_xwalk_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
SDV_PY="${SDV_PY:-}"
if [ -z "${SDV_PY}" ]; then
  for c in /mnt/sdv_repos/sdv-py "C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py"; do
    [ -d "$c" ] && SDV_PY="$c" && break
  done
fi
# venv layout and PYTHONPATH separator follow the OS (Git Bash on Windows vs Linux)
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; SEP=";" ;;
  *) PY="${PY:-${SDV_PY}/.venv/bin/python}"; SEP=":" ;;
esac
export PYTHONPATH="${SDV_PY}${SEP}${ROOT}/python"
export PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/game_xwalk_$(date +%Y%m%d_%H%M%S).log"
echo "log -> ${LOG}  (watch: tail -f ${LOG})"
"$PY" python/ncaa_wbb_06_xwalk_build.py --root "${ROOT}" "$@" 2>&1 | tee -a "${LOG}"
rc=${PIPESTATUS[0]}
echo "EXIT=${rc}" | tee -a "${LOG}"
exit "${rc}"
