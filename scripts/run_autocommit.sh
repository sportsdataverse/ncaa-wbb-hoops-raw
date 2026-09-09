#!/usr/bin/env bash
# Periodic commit+push of capture output while a campaign runs.
#
# The capture campaign (run_*_backfill_range.sh) deliberately does NOT commit --
# it captures and parses only. Without this, a season accumulates ~12.5k
# untracked files (raw + parsed json) and a full backfill ~175k, which makes
# the eventual push enormous and a single failure expensive. This commits in
# small increments instead, so the repo is always close to pushed.
#
#   ./scripts/run_autocommit.sh                 # every 10 min until stopped
#   INTERVAL=300 PUSH=0 ./scripts/run_autocommit.sh
#
# Knobs (env-only):
#   INTERVAL=600   seconds between passes
#   PUSH=1         push after each commit (0 = commit only)
#   SETTLE=1       only stage files older than this many MINUTES
#
# SETTLE is the important one: a bundle being written right now must not be
# staged half-flushed, so only files whose mtime has settled are added. That
# makes this safe to run CONCURRENTLY with an active capture.
#
# Ctrl-C safe: it holds no state; the next run picks up whatever is pending.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
case "$PWD" in *wbb*) LEAGUE=wbb ;; *) LEAGUE=mbb ;; esac

# Singleton per repo. Two autocommits in one tree fight over .git/index.lock and
# each reports the other's failure as its own. flock releases on exit, including
# a kill -9, so a crashed run cannot wedge the next one.
exec 9>".git/.autocommit.lock"
if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
  echo "another autocommit is already running in $PWD -- exiting" >&2
  exit 0
fi
INTERVAL="${INTERVAL:-600}"
# Commit subject. Default describes a capture campaign, which is what this
# usually runs beside -- but the same batching is needed after a PARSER FIX
# reprocess, and labelling those commits "capture progress" would put a false
# provenance trail in `git log` (the raw json changed because the parser
# changed, not because anything new was captured). Override per run, e.g.
#   SUBJECT='fix(parse): wbb halves-era reparse' ./scripts/run_autocommit.sh
SUBJECT="${SUBJECT:-feat(data): capture progress --}"
PUSH="${PUSH:-1}"
RC=0   # ONESHOT exit status: a failed push must not report success
SETTLE="${SETTLE:-1}"

# --- termination guards -------------------------------------------------
# This loop had NO exit condition: `while :;` with no break, no deadline, no
# singleton lock and no parent check. Launched beside a capture (nohup/tmux),
# it outlived the capture and kept scanning the tree forever. That is not an
# idle process -- one pass measured >12 min at 850MB RSS over 230k paths, which
# is LONGER than the 600s interval, so an orphan runs essentially continuously.
#
#   MAX_IDLE_PASSES  consecutive "nothing to commit" passes before exiting.
#                    This is the real orphan killer: this script exists to
#                    commit an ACTIVE capture's output, so if nothing has
#                    changed for this many passes the capture is over and there
#                    is nothing left to serve. 0 disables.
#   MAX_RUNTIME_S    hard deadline regardless of activity. 0 disables.
#   WATCH_PID        exit when that pid goes away (pass the capture's pid).
MAX_IDLE_PASSES="${MAX_IDLE_PASSES:-6}"
MAX_RUNTIME_S="${MAX_RUNTIME_S:-43200}"   # 12h
WATCH_PID="${WATCH_PID:-}"
STARTED_AT=$SECONDS
idle_passes=0

mkdir -p logs
LOG="logs/autocommit_$(date +%Y%m%d_%H%M%S).log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
say "autocommit: league=${LEAGUE} interval=${INTERVAL}s push=${PUSH} settle=${SETTLE}min"
say "watch: tail -f $(pwd)/${LOG}"

while :; do
  # Stage only SETTLED files so an in-flight write is never committed partially.
  # ONE git invocation: `| xargs git add` forks a NEW git per batch and each one
  # rewrites the whole index. That fork churn is what killed both autocommitters
  # on 2026-08-29 (MSYS `dofork ... exit code 0xC000026B` / `fork: retry:
  # Resource temporarily unavailable`), and it is near-quadratic besides.
  # --pathspec-from-file keeps the SETTLE semantics EXACTLY as before -- the
  # settle window is load-bearing here (these captures do not write atomically).
  _settled=$(mktemp)
  # Every committed data subtree, not just raw/json: discovery writes schedules/,
  # identity writes teams/, the roster stages write rosters/ and team_rosters/,
  # and the xwalk build writes xwalk/. Staging only raw+json meant a discovery or
  # roster run scraped pages that were never committed -- work done and silently
  # left on the box.
  _paths=""
  for _d in raw json schedules rosters team_rosters teams xwalk; do
    # Only dirs that EXIST. `git add` rejects the WHOLE pathspec if one element
    # matches nothing, so a repo missing a subtree would stage nothing at all --
    # the same way an ignored `logs/` silently broke the MFB driver's add.
    [ -d "${LEAGUE}/${_d}" ] && _paths="${_paths} ${LEAGUE}/${_d}"
  done

  if [ "${SETTLE}" -gt 0 ]; then
    # Settle window: enumerate settled files and hand git an explicit list, so a
    # bundle being written right now is never staged half-flushed.
    _settled=$(mktemp)
    find ${_paths} -type f -mmin "+${SETTLE}" -print0 2>/dev/null > "$_settled"
    [ -s "$_settled" ] && git add --pathspec-from-file="$_settled" --pathspec-file-nul --
    rm -f "$_settled"
  elif [ -n "${_paths}" ]; then
    # SETTLE=0 -- no settle window, so hand git the DIRECTORIES and let it walk
    # them. Identical result, but it avoids matching ~230k literal pathspecs
    # against a ~230k-entry index, which is near-quadratic: one such pass ran
    # >12 minutes at 850MB RSS. Only safe when nothing is mid-write, which is
    # exactly the post-capture sweep's situation.
    git add -- ${_paths}
  fi
  # schedule_master is rewritten by discovery; safe to take whole.
  [ -f "${LEAGUE}/schedule_master.parquet" ] && git add "${LEAGUE}/schedule_master.parquet"

  if git diff --cached --quiet; then
    idle_passes=$((idle_passes + 1))
    say "nothing settled to commit (idle pass ${idle_passes}/${MAX_IDLE_PASSES})"
  else
    idle_passes=0
    n=$(git diff --cached --name-only | wc -l | tr -d ' ')
    # Per-season counts make the commit message useful in `git log` later.
    summary=""
    for d in "${LEAGUE}"/raw/*/; do
      s=$(basename "$d")
      c=$(git diff --cached --name-only -- "${LEAGUE}/raw/${s}" | wc -l | tr -d ' ')
      [ "$c" -gt 0 ] && summary="${summary}${s}:+${c} "
    done
    # Capture the commit's OWN error and GATE THE PUSH ON IT. This was
    # `git commit -q ... && say "committed"` with the push block running
    # unconditionally after it, so a failed commit logged NOTHING and the
    # no-op push that followed logged "pushed" -- success reported for work
    # that never happened.
    _err=$(mktemp)
    if git commit -q -m "${SUBJECT} ${summary:-incremental} (${n} files)" 2>"$_err"; then
      say "committed ${n} files  ${summary}"
      rm -f "$_err"
    else
      say "COMMIT FAILED -- NOT pushing. git said:"
      sed 's/^/    /' "$_err" | head -20 | tee -a "$LOG"
      rm -f "$_err"
      if [ "${ONESHOT:-0}" = "1" ]; then say "oneshot: commit failed"; exit 1; fi
      sleep "$INTERVAL"
      continue
    fi
    if [ "$PUSH" = "1" ]; then
      # Integrate anything that landed on the remote FIRST. Without this the
      # loop cannot self-heal: ONE unrelated commit on origin/main (a CI tweak
      # is enough) makes every later push a non-fast-forward, and the loop
      # retries the identical rejected push every INTERVAL, forever, while
      # logging PUSH FAILED. That happened in BOTH raw repos and both data
      # repos during the 2026-08-19 parser-fix reprocess.
      #
      # --no-rebase: merge, never rebase -- these commits are already pushed in
      # the normal case and the tree holds ~100k files.
      # merge.autoStash=false: an in-flight parse leaves tens of thousands of
      # modified files and autostash dies with 'patch too large'.
      git -c merge.autoStash=false pull -q --no-rebase --no-edit origin main \
        || say "PULL FAILED (working tree untouched; next pass retries)"
      git -c http.version=HTTP/1.1 -c http.postBuffer=1048576000 push -q origin main \
        && say "pushed" || { say "PUSH FAILED (commit is safe locally; next pass retries)"; RC=1; }
    fi
  fi
  # ONESHOT=1: one pass, then exit -- for a driver that wants "everything
  # scraped is committed" as a POSTCONDITION of its own run rather than a
  # loop the operator remembers to start (and to stop).
  if [ "${ONESHOT:-0}" = "1" ]; then say "oneshot: pass complete (rc=${RC})"; exit "${RC}"; fi

  if [ -n "$WATCH_PID" ] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
    say "watched pid ${WATCH_PID} is gone -- final pass done, exiting (rc=${RC})"
    exit "$RC"
  fi
  if [ "$MAX_IDLE_PASSES" -gt 0 ] && [ "$idle_passes" -ge "$MAX_IDLE_PASSES" ]; then
    say "nothing to commit for ${idle_passes} consecutive passes -- capture looks finished, exiting (rc=${RC})"
    exit "$RC"
  fi
  if [ "$MAX_RUNTIME_S" -gt 0 ] && [ $((SECONDS - STARTED_AT)) -ge "$MAX_RUNTIME_S" ]; then
    say "max runtime ${MAX_RUNTIME_S}s reached -- exiting (rc=${RC}); re-run to continue"
    exit "$RC"
  fi
  sleep "$INTERVAL"
done
