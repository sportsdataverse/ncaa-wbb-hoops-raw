# Scraping notes (NCAA WBB raw)

## Canonical reference lives in the MBB repo

**Read this first, before ANY stats.ncaa.org scrape:**

`../../hoopR-dev/ncaa-mbb-hoops-raw/docs/SCRAPING_NOTES.md`

Same site, same Akamai bm-verify, same transport stack, same proxy vendors,
same measured rate limits. That file is the operational source of truth for:

- bm-verify / ban signatures and what each one actually means
- proxy vendor selection + the canary scorecard workflow
- sticky-session semantics and why the session id is re-minted per run
- session ceilings (~70 min / ~1400 bundles) and the chunk-then-cooldown rule
- what to do when a run hard-stops

Do not duplicate that content here — fix it there.

## What is WBB-specific

**Repo layout** (identical shape, `wbb/` instead of `mbb/`):

```
wbb/schedule_master.parquet       # discovered contest_ids per season
wbb/raw/{season}/{id}.json.gz     # captured 3-page bundles (the checkpoint)
wbb/json/{id}.json                # parsed per-contest output
wbb/team_rosters/{season}/{team_id}.json
wbb/.discover/{season}/{team_id}.json   # per-team discovery checkpoints (gitignored)
```

**Launchers** (`scripts/`):

| Script | Stage |
|---|---|
| `run_canary.sh` | score proxy vendors against the bm-verify canary |
| `run_discover.sh` | season -> contest_ids (fans out `DISCOVER_WORKERS`, default 12, then one merge pass) |
| `run_capture.sh` | contest_ids -> raw bundles |
| `run_parse.sh` | raw bundles -> json (fans out `PARSE_WORKERS`, default 12; offline) |
| `run_rosters.sh` | season team rosters (with stats.ncaa.org player ids) |
| `run_wbb_backfill.sh` | one season, discover -> capture -> parse |
| `run_wbb_backfill_range.sh` | multi-season campaign, newest-first, chunked + cooldowns |

**Crosswalk coverage.** The bundled WBB crosswalk
(`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`) covers **2009-10 .. 2024-25**
only — there is no 2025-26 row yet (the MBB crosswalk does have one).
`run_wbb_backfill.sh` refuses season 2026 with that explicit cause rather than
letting `discover_season()` raise its generic "crosswalk drift" `ValueError`.

**League selection.** `ncaa_discover.discover_season` /
`ncaa_rosters.capture_rosters` take `league="wbb"` (default) or `"mbb"` and pick
the matching crosswalk from `_TEAM_ID_CROSSWALKS`. The page parsers, the fetch
layer, and the canary are league-agnostic — `sportsdataverse.wbb.wbb_ncaa_fetch`
re-exports the mbb fetch core *by reference*, and contest ids at stats.ncaa.org
are one sport-agnostic namespace.

**Credentials.** `canary_vendors.toml` (gitignored) holds live proxy creds;
`canary_vendors.toml.example` is the committed template. Never commit the
filled-in file. Per-league overrides go in the env, not in code.
