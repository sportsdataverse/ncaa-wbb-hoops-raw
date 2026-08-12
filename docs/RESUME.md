# Resume the NCAA WBB backfill

State verified by on-disk census **2026-08-12**. Nothing is running.

Read `docs/SCRAPING_NOTES.md` first — it is the canonical operational reference
for stats.ncaa.org and supersedes this file wherever they disagree about
transport.

## Where it stands

**WBB pbp capture has never successfully run.** `wbb/raw/` and `wbb/json/` do
not exist: **0 of ~90,000 contests captured.**

| tree | state |
| --- | --- |
| `wbb/schedules/` | COMPLETE (html + json + parquet, per team-season) |
| `wbb/rosters/`, `wbb/team_rosters/` | COMPLETE |
| `wbb/teams/` | COMPLETE |
| `wbb/xwalk/espn_game_id/` | COMPLETE (contest_id -> ESPN event id) |
| `wbb/schedule_master.parquet` | COMPLETE — the capture denominator |
| **`wbb/raw/` (3-page bundles)** | **EMPTY — this is the work** |
| **`wbb/json/` (parsed)** | **EMPTY** |

Reference data was built with ZERO extra HTTP: discovery already fetched the
team pages and the roster stage already fetched the roster pages.

### Why zero, and what was fixed

The 2026-08-01 campaign captured **nothing** and looked healthy while doing it.
A stale `MAX_SEASON=2025` guard in `scripts/run_wbb_backfill.sh` refused season
2026 with `rc=2`; the range driver read that as a capture hard-stop and burned
the campaign's first round on cooldowns. Fixed in `f6153441` (guard lifted to
2026, which is what the bundled crosswalk actually covers).

**The rule that fix encodes: bump `MAX_SEASON` in the same change as the
bundled WBB crosswalk** (`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`, read
via `wbb_ncaa_team_ids()`). A stale guard does not fail loudly — it burns a
campaign.

## Before you launch — four preconditions

1. **Decodo IPs only.** Binding user directive (2026-08-11). Set `VENDOR` /
   `NCAA_VENDOR` to a `decodo_*` entry. `run_02_games.sh` skips the ProxyBonanza
   cred check when **`NCAA_VENDOR`** is set (`2ae4a4a8`) — before that it
   demanded ProxyBonanza creds even for a Decodo run. The gate keys on the ENV
   VAR, not a `--vendor` CLI flag, because `run_wbb_backfill.sh` exports
   `NCAA_VENDOR` and does not pass a flag; MBB carried the flag-keyed form as a
   documented latent bug until 2026-08-12.

2. **Canary first.** `bash scripts/run_98_canary.sh --games 10` and confirm
   PASS (>=90% clean games) before scaling. On 2026-08-11 the MBB canary scored
   **7/7 Decodo vendors PASS, 141 clean pages, 0 failures** — the same pool WBB
   uses. Note the canary was a SILENT NO-OP between 2026-08-02 and 2026-08-11
   (the engine extraction dropped the shim's `__main__`); it works now and is
   regression-tested.

3. **`WORKERS` must be coprime to whatever count strands a shard.** See
   SCRAPING_NOTES 2026-08-11. For a FIRST run there is no residual yet, so any
   value in 1..24 is fine; the rule bites on the RE-run. The driver's ceiling
   was raised 16 -> 24 on 2026-08-12 so the coprime 23 is reachable.

4. **Know which sdv-py the launchers import.** They put the sdv-py *working
   tree* on `PYTHONPATH` — not a version pin — so whatever branch that checkout
   sits on is the code that runs. Check with
   `git -C ../../sdv-py diff --stat HEAD origin/main -- sportsdataverse/scrape/ncaa/`
   (empty output = safe).

## To run the campaign

Run these from the repository root (the drivers `cd` to it themselves, so any
checkout location works -- do not hard-code a path):

```sh
# 0. pre-flight: score the Decodo vendors (10 games x 2 pages each)
bash scripts/run_98_canary.sh --games 10

# 1. the full history, newest-first. Resume is free -- capture skips every
#    contest already on disk, so a restart costs a file-exists check per game.
VENDOR=decodo_patchright \
WORKERS=16 CHUNK=400 PARSE_WORKERS=16 MAX_ROUNDS=12 \
  nohup bash scripts/run_wbb_backfill_range.sh 2026 2011 >> logs/campaign.log 2>&1 &

# 2. ALWAYS start this too -- the campaign commits NOTHING itself
nohup bash scripts/run_autocommit.sh >> logs/autocommit_nohup.log 2>&1 &
```

Watch it live:

```sh
tail -f logs/backfill_range_*.log
```

**Scale expectation, from the MBB run:** ~90k contests at the 34.6 captures/min
a healthy Decodo pool sustained ≈ **40+ hours of wall clock**, plus ~5 min of
fixed per-season overhead even where there is nothing to fetch. Budget it as a
multi-day job, not an overnight one. Ctrl-C is always safe — the disk is the
checkpoint.

`hard-stopped (rc=1)` repeating means the vendor is being refused: re-canary
rather than letting it burn twelve rounds on cooldowns. `challenge not cleared`
is **not** that signal — it fires for any page failing `_is_clean` (see
SCRAPING_NOTES §2/§8).

## Known wrinkles (not blockers)

- `schedule_master`'s `captured` column is **vestigial**; resume is file-exists
  based. Do not read that column to answer "what do we have".
- The master is `wbb/schedule_master.parquet`; the D33 writer-side rename to
  `wbb/wbb_schedule_master.parquet` is an open follow-up (the `-data` twin
  already reads both names, new first).
- `scripts/run_*.sh` execute the **sibling sdv-py repo's** venv via a hardcoded
  absolute path, not this repo's `.venv`, so a live run does not use the
  `sportsdataverse` pin that CI enforces.
