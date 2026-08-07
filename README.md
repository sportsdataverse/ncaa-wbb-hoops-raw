# ncaa-wbb-hoops-raw
NCAA WBB Raw Data

Raw-page capture + parse pipeline for `stats.ncaa.org` women's college
basketball. Three stages: **discover** (season -> contest_ids) -> **capture**
(contest -> 3-page HTML bundle) -> **parse** (bundle -> combined per-game
JSON). Data tree lives under `<root>/wbb/` (default root = repo root):
`schedule_master.parquet`, `raw/{season}/{contest_id}.json.gz`,
`json/{contest_id}.json`.

Three further committed datasets — **schedules**, **teams**, **rosters** — ride
along for free. `discover` already fetches every team's schedule page and
`rosters` already fetches every roster page, so both trees are persisted from
those existing fetches at **zero extra HTTP**; `teams` needs no fetch at all
(it is the bundled sdv-py crosswalk). Per team the source `html` and the parsed
`json` are kept; `parquet` is the one compiled dataset per season:

```
wbb/schedules/html/{season}/{team_id}.html     wbb/rosters/html/{season}/{team_id}.html
wbb/schedules/json/{season}/{team_id}.json     wbb/rosters/json/{season}/{team_id}.json
wbb/schedules/parquet/{season}.parquet         wbb/rosters/parquet/{season}.parquet
wbb/teams/{html,json,parquet}/{season}.*
```

Every one of them carries human-readable names next to the machine ids —
schedules pair `team_id`/`opponent_id` with `team`/`opponent`, rosters pair
`player_id` with `clean_name` (display form) *and* `player` (the ALL-CAPS
`FIRST.LAST` play-by-play join key), teams pair `ncaa_team_id` with the NCAA
name, conference and `division` (constant `"I"` — the crosswalk is scoped to
the Division-I `season_divisions` id).

**These three trees are also where the ESPN identity lives.** All three are
reference data, so each carries the ESPN team id from sdv-py's
`ncaa_espn_team_crosswalk`: schedules for both sides (`espn_team_id` /
`opponent_espn_team_id`), rosters for the roster's team, teams for the team
plus ESPN's display name and mascot. The **per-game** parsed families
(`pbp`, `possessions`, `player_box`, `team_box`, `shots`, `lineups`) carry
`*_ncaa_team_id`, the player ids and the readable names — but deliberately
**no** ESPN ids: repeating a reference id on millions of play rows is bloat,
so join `teams` on `ncaa_team_id` instead. Each parsed game does ship a
two-row `teams` block with the full ESPN identity for its own two sides.

On the play-by-play side the identity pass also resolves the ten on-court
slots — `home_1`..`home_5` / `away_1`..`away_5` on `pbp` and `possessions`
each gain `{slot}_player_id` + `{slot}_clean_name` — off the same
game-scoped roster index as `player_1`/`player_2`.

This is a retarget of the sibling `hoopR-dev/ncaa-mbb-hoops-raw` scraper --
same transport, same fetcher, same safe-rate rules. The `python/` package
(`ncaa_bundle.py` / `ncaa_capture.py` / `ncaa_discover.py` / `ncaa_parse.py`)
defaults `--league` to `"wbb"` throughout; `"mbb"` remains a legitimate
runtime value there for parity/regression checks against the MBB scraper.

## Setup

Requires the sibling `sdv-py` checkout at
`C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py` with its `.venv`
synced (`uv sync --all-extras --dev` there). Discover + capture also need
ProxyBonanza creds in `~/.Renviron` (or `~/Documents/.Renviron`):

```
PROXYBONANZA_API_KEY=...
PROXY_PKG=...
```

The launchers read these at call time and never print or persist the raw
values. `parse` is fully offline and needs no creds.

## Run order

```sh
bash scripts/run_discover.sh --season 2025     # -> wbb/schedule_master.parquet
                                               #    + wbb/schedules/{html,json}/2025/
bash scripts/run_capture.sh  --season 2025     # -> wbb/raw/2025/{contest_id}.json.gz
bash scripts/run_parse.sh                      # -> wbb/json/{contest_id}.json
bash scripts/run_rosters.sh  --season 2025     # -> wbb/rosters/{html,json}/2025/
bash scripts/run_datasets.sh --season 2025     # -> the season parquets + wbb/teams/
```

`run_datasets.sh` is fully offline (no creds, no network) and **not sharded**:
each season parquet is a single output file, so concurrent `--shard` workers
would race it. Run it once, after the sharded sweeps finish. It also re-derives
any missing per-team json from committed html, so a parser fix can be replayed
across every captured season with `--overwrite` and no re-scrape.

Or the one-command chained backfill (discover -> capture -> parse, resumable):

```sh
./scripts/run_wbb_backfill.sh 2025
CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025          # stop after 1500 new bundles (recommended)
WORKERS=2 CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025 # 2 workers (measured ceiling)
```

`run_wbb_backfill.sh` is the **single-season** chain. The other wrapper
drivers around the per-stage sequence:

- `scripts/run_wbb_backfill_range.sh [start] [end]` — **multi-season
  campaign** (default 2025 down to 2010), newest-first, wrapping
  `run_wbb_backfill.sh` per season: capture runs in chunked rounds (a
  fresh sticky IP each chunk) with cooldowns between rounds and after
  ban hard-stops, and up to `MAX_ROUNDS` straggler rounds per season
  before moving on (re-run later to finish the remainder).
- `scripts/run_reference_backfill.sh [start] [end]` — reference-only
  companion to the pbp backfill: per season (newest-first) it chains
  `run_discover.sh` -> sharded `ncaa_rosters.py` -> `run_datasets.sh`,
  then commits + pushes that season. Reference data is cheap (~2 pages
  per team-season vs 3 per game), so it runs first / independently of
  `run_wbb_backfill*.sh`; it does **no** pbp capture.
- `scripts/run_autocommit.sh` — incremental commit(+push) sweep of
  capture output every `INTERVAL` seconds. It stages only files whose
  mtime has settled at least `SETTLE` minutes, so an in-flight bundle
  is never committed half-flushed — safe to run **concurrently with an
  active capture**. The backfill drivers deliberately do not commit;
  this keeps the repo close to pushed during a long campaign.

Watch a running job live:

```sh
tail -f logs/capture_*.log
tail -f logs/backfill_<season>_<ts>.log   # path is printed at start of run_wbb_backfill.sh
```

**The backfill is a USER-run, residential-IP job.** `stats.ncaa.org` bans
datacenter/cloud IPs, so it must be launched from a real terminal on a
residential connection -- not scheduled or run from a cloud agent.

## Season ceiling: tracks the bundled crosswalk (currently 2026, i.e. 2025-26)

The bundled WBB team-id crosswalk
(`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`) covers **2009-10 through
2025-26** (the 2025-26 rows landed in sdv-py; 2026 discovery has already run
clean here -- 6,019 contests in `schedule_master`, 359 schedule pages
committed).

For a season past the crosswalk, `discover_season(..., league="wbb")` raises
`ValueError("No teams found in crosswalk for season=... ")`, worded as if
the NCAA team-ids URL format had drifted -- the real cause is crosswalk
coverage, not format drift. `scripts/run_wbb_backfill.sh` guards this up
front (`MAX_SEASON`, currently 2026) and refuses out-of-range seasons with a
message naming the actual cause, before any network call is made. **Bump
`MAX_SEASON` when the crosswalk grows** -- a stale guard reads as a capture
hard-stop to the range driver (this burned the 2026-08-01 campaign's first
round). `run_discover.sh` / `run_capture.sh` / `run_parse.sh` don't carry
their own guard (they're thin pass-throughs to the python CLIs), so calling
them directly with an out-of-range season still surfaces the raw
crosswalk-coverage `ValueError` from `discover_season`.

## Safe-rate rule (capture)

**The worker ceiling is pool-relative, not absolute** (user-verified
2026-08-01 on the MBB sibling, `docs/SCRAPING_NOTES.md`): the old "1-2
workers max" rule was measured on a shared datacenter pool. With per-worker
DISJOINT sticky residential ports (the `decodo_patchright` port pool), up to
8 workers have run clean — what matters is **per-IP pacing**, and the
fetcher shards the port pool by worker index so workers never pile onto one
port. Each worker is a *separate process* running `run_capture.sh` with a
disjoint `--shard i/N` -- never threads inside one process. On a
shared/unsharded pool, stay at 1-2:

```sh
./scripts/run_capture.sh --season 2025                    # 1 worker (proven-safe default)
./scripts/run_capture.sh --season 2025 --shard 0/2 &       # 2 workers, only after 1-worker is stable
./scripts/run_capture.sh --season 2025 --shard 1/2 &
```

`run_wbb_backfill.sh` caps `WORKERS` at 1..16 (keep at least 2 ports per
worker); anything else is refused before any network call.

A ban-suspect response is a **hard stop**, not a retry: the process exits
immediately (`BAN-SUSPECT: capture halted at contest_id=...`). Wait out the
cooldown before resuming -- do not immediately re-launch.

⚠️ On a persistent ban, the upstream `NcaaFetcher` retries across the entire
residential proxy pool with no delay before raising -- so a single
ban-detection can send a ~pool-sized burst before the scraper hard-stops.
This is bounded (the run terminates), but re-running immediately into a live
ban will re-churn the pool. On a `BAN-SUSPECT` stop, WAIT for a multi-minute
cooldown before resuming.

## The 4-quarter period model (WBB vs MBB delta)

WBB play-by-play ships **one table per quarter** (4 regulation periods, 10
minutes each, 5-minute overtimes -- `_WBB_PERIOD_MODEL = (4, 600, 300)` in
`python/ncaa_parse.py`), where MBB ships **one table per half** (2 periods).
`parse_bundle(..., league="wbb")` (the default) selects this period model
automatically; nothing else in the capture/discover pipeline changes.

## Resume story

Every stage is idempotent and re-runnable:

- **discover** merges new contest_ids into the existing `schedule_master.parquet`
  (and checkpoints each swept team page under `wbb/.discover/{season}/`, so an
  aborted sweep resumes instead of restarting).
- **capture** resume is **file-exists based**: a contest is skipped iff its
  `wbb/raw/{season}/{contest_id}.json.gz` bundle is already on disk. The
  master's `captured` column is vestigial (always `False`) — see
  `docs/SCRAPING_NOTES.md` §5. Re-running after a ban-suspect stop (or a plain
  interruption) picks up where it left off.
- **parse** skips any contest_id that already has a `wbb/json/{contest_id}.json`
  output; re-running only parses newly captured bundles.

So `bash scripts/run_discover.sh --season 2025 && bash scripts/run_capture.sh --season 2025 && bash scripts/run_parse.sh`
(or the equivalent `./scripts/run_wbb_backfill.sh 2025`) is safe to re-run
wholesale after any interruption.

## Status

The `python/` package (league-binding shims over the shared
`sportsdataverse.scrape.ncaa` engine, sdv-py #328/#330) is complete and
validated offline. **The reference backfill HAS run live**: schedules,
rosters and teams for 2011-2026 are committed under `wbb/` (2026: 359
schedule + 359 roster pages, 2026-08-01). **The pbp capture has NOT yet
produced bundles** -- the 2026-08-01 campaign's first round died on the
stale season guard (fixed; see the season-ceiling section) and no
`wbb/raw/` tree exists yet. Run it per "Run order" above.

## Phase 2: the season `-data` builder

The season `-data` builder lives in the sibling repo
`../ncaa-wbb-hoops-data` (package `ncaa_wbb_data_build`), mirroring
`../ncaa-mbb-hoops-data`. It ingests this repo's committed `wbb/` tree over
HTTP from `main`, which is why the data tree must stay committed (see
`.gitignore`).
