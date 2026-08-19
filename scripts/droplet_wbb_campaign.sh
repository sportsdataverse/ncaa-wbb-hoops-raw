#!/usr/bin/env bash
# Droplet WBB capture campaign: N workers, OOM-shielded, auto-halving.
#
# WHY THE SHIELD: this droplet also runs postgres (sdv-db), sdv-db-api,
# sdv-orch-* and a GH Actions runner. 8 chrome workers can exceed available
# RAM, and the kernel's OOM killer picks the LARGEST RSS -- which is postgres,
# not us. `choom -n 1000` scores the capture tree maximally killable, and
# oom_score_adj is INHERITED by children, so every python + chrome descendant
# is sacrificed before any production service. Capture checkpoints per-file,
# so an OOM kill costs at most one bundle per worker.
#
# Usage:
#   ./scripts/droplet_wbb_campaign.sh 2025            # WORKERS=8 (default)
#   WORKERS=4 ./scripts/droplet_wbb_campaign.sh 2025
#
# Survive SSH disconnect:
#   tmux new -s wbb './scripts/droplet_wbb_campaign.sh 2025'
# Watch:
#   tail -f logs/campaign_<season>_<ts>.log
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SEASON="${1:?usage: droplet_wbb_campaign.sh <season>   (ending year, e.g. 2025)}"
W="${WORKERS:-8}"

export SDV_PY="${SDV_PY:-/mnt/sdv_repos/sdv-py}"
export NCAA_VENDOR="${NCAA_VENDOR:-decodo_patchright}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

mkdir -p logs
LOG="logs/campaign_${SEASON}_$(date +%Y%m%d_%H%M%S).log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

[ -f canary_vendors.toml ] || { say "ERROR: canary_vendors.toml missing"; exit 2; }
command -v choom >/dev/null || { say "ERROR: choom not found (util-linux)"; exit 2; }

# Count OOM kills already in the kernel log so we only react to NEW ones.
oom_count() { dmesg 2>/dev/null | grep -c "Out of memory: Killed process" || true; }
BASE_OOM="$(oom_count)"

say "campaign season=${SEASON} workers=${W} vendor=${NCAA_VENDOR}"
say "OOM shield ON (choom -n 1000; inherited by all children). baseline oom kills=${BASE_OOM}"
say "watch: tail -f $(pwd)/${LOG}"

rc=0
while [ "$W" -ge 1 ]; do
  say "=== launching WORKERS=${W} ==="
  WORKERS="$W" choom -n 1000 -- ./scripts/run_wbb_backfill.sh "$SEASON" >>"$LOG" 2>&1
  rc=$?
  NOW_OOM="$(oom_count)"
  say "run exited rc=${rc} (oom kills: ${BASE_OOM} -> ${NOW_OOM})"

  [ "$rc" -eq 0 ] && { say "SEASON ${SEASON} COMPLETE"; break; }

  if [ "$NOW_OOM" -gt "$BASE_OOM" ] || [ "$rc" -eq 137 ]; then
    BASE_OOM="$NOW_OOM"
    W=$((W / 2))
    [ "$W" -lt 1 ] && { say "OOM at 1 worker -- a single unit exceeds budget; STOPPING"; break; }
    say "OOM detected -> halving to WORKERS=${W}, cooling down 60s, resuming from checkpoint"
    sleep 60
    continue
  fi

  # ponytail: any non-OOM failure (ban hard-stop, crosswalk refusal) stops the
  # loop rather than retrying -- retry policy for bans already lives in
  # run_wbb_backfill_range.sh; duplicating it here would fight that script.
  say "non-OOM failure rc=${rc} -- stopping. See ${LOG}"
  break
done

say "EXIT=${rc}"
exit "$rc"
