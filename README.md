# ncaa-wbb-hoops-raw
NCAA WBB Raw Data

Raw-page capture + parse pipeline for `stats.ncaa.org` women's college
basketball. Three stages: **discover** (season -> contest_ids) -> **capture**
(contest -> 3-page HTML bundle) -> **parse** (bundle -> combined per-game
JSON). Data tree lives under `<root>/wbb/` (default root = repo root):
`schedule_master.parquet`, `raw/{season}/{contest_id}.json.gz`,
`json/{contest_id}.json`.

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
bash scripts/run_capture.sh  --season 2025     # -> wbb/raw/2025/{contest_id}.json.gz
bash scripts/run_parse.sh                      # -> wbb/json/{contest_id}.json
```

Or the one-command chained backfill (discover -> capture -> parse, resumable):

```sh
./scripts/run_wbb_backfill.sh 2025
CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025          # stop after 1500 new bundles (recommended)
WORKERS=2 CHUNK=1500 ./scripts/run_wbb_backfill.sh 2025 # 2 workers (measured ceiling)
```

Watch a running job live:

```sh
tail -f logs/capture_*.log
tail -f logs/backfill_<season>_<ts>.log   # path is printed at start of run_wbb_backfill.sh
```

**The backfill is a USER-run, residential-IP job.** `stats.ncaa.org` bans
datacenter/cloud IPs, so it must be launched from a real terminal on a
residential connection -- not scheduled or run from a cloud agent.

## Season ceiling: 2025 (i.e. 2024-25) is the max

The bundled WBB team-id crosswalk
(`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`) covers **2009-10 through
2024-25 only** -- there is no 2025-26 row yet. This differs from MBB, whose
crosswalk does cover 2025-26 (which is why the MBB launcher can default to
the current season).

`discover_season(2026, league="wbb")` raises `ValueError("No teams found in
crosswalk for season=... ")`, worded as if the NCAA team-ids URL format had
drifted -- for WBB the real cause is crosswalk coverage, not format drift.
`scripts/run_wbb_backfill.sh` guards this up front and refuses `season >
2025` with a message naming the actual cause, before any network call is
made. `run_discover.sh` / `run_capture.sh` / `run_parse.sh` don't carry
their own guard (they're thin pass-throughs to the python CLIs), so calling
them directly with an out-of-range season still surfaces the raw
crosswalk-coverage `ValueError` from `discover_season`.

Extending the crosswalk to cover 2025-26 is a separate `sdv-py` change --
out of scope for this repo.

## Safe-rate rule (capture)

**1-2 workers max, ever.** Each worker is a *separate process* running
`run_capture.sh` with a disjoint `--shard i/N` -- never threads inside one
process, never 4+ processes. Measured: 1-2 browser workers is safe, 4
workers gets banned.

```sh
./scripts/run_capture.sh --season 2025                    # 1 worker (proven-safe default)
./scripts/run_capture.sh --season 2025 --shard 0/2 &       # 2 workers, only after 1-worker is stable
./scripts/run_capture.sh --season 2025 --shard 1/2 &
```

`run_wbb_backfill.sh` enforces the same ceiling: `WORKERS` must be `1` or
`2`, anything else is refused before any network call.

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
  without touching rows already `captured=True`.
- **capture** only fetches contest_ids where `captured==False` in the master
  file; re-running after a ban-suspect stop (or a plain interruption) picks up
  where it left off.
- **parse** skips any contest_id that already has a `wbb/json/{contest_id}.json`
  output; re-running only parses newly captured bundles.

So `bash scripts/run_discover.sh --season 2025 && bash scripts/run_capture.sh --season 2025 && bash scripts/run_parse.sh`
(or the equivalent `./scripts/run_wbb_backfill.sh 2025`) is safe to re-run
wholesale after any interruption.

## Status

The `python/` package (bundle/capture/discover/parse + 25 tests) is
complete and validated offline. **The live WBB backfill has not been run
yet** -- these launchers are prepped, not exercised end-to-end against
`stats.ncaa.org`. Run it yourself per "Run order" above.

## Next step (Phase 2, not yet built)

The season `-data` builder (`../ncaa-wbb-hoops-data`, package
`ncaa_wbb_data_build`, 9 `ncaa_wbb_*` datasets) is a **separate,
not-yet-created repo** -- planned as a follow-up PR after this one merges,
mirroring `../ncaa-mbb-hoops-data`. Don't expect
`python -m ncaa_wbb_data_build` to work until that repo exists.
