# CLAUDE.md — ncaa-wbb-hoops-raw Development Guide

## Package Overview

This repo is the **raw-page capture + parse stage** for `stats.ncaa.org`
women's college basketball. It scrapes; it does not reshape.

Pipeline: `stats.ncaa.org -> ncaa-wbb-hoops-raw [HERE] -> ncaa-wbb-hoops-data
-> sportsdataverse-data`.

Three stages, in order:

1. **discover** — season -> `contest_id`s (`wbb/schedule_master.parquet`
   plus `wbb/schedules/{html,json}/{season}/`).
2. **capture** — contest -> a 3-page HTML bundle at
   `wbb/raw/{season}/{contest_id}.json.gz`.
3. **parse** — bundle -> combined per-game JSON at `wbb/json/{contest_id}.json`.

This repo is a **retarget of the sibling `hoopR-dev/ncaa-mbb-hoops-raw`
scraper** — same transport, same fetcher, same safe-rate rules. The `python/`
modules default `--league` to `"wbb"`; `"mbb"` remains a legitimate runtime
value there for parity/regression checks against the MBB scraper.

`README.md` is the operator-facing runbook. This file is the agent-facing
companion — read both.

> **REQUIRED READING before any live `stats.ncaa.org` scrape:
> [`docs/SCRAPING_NOTES.md`](docs/SCRAPING_NOTES.md).** stats.ncaa.org is a
> hostile host — patchright + a residential proxy port pool, a ~70-minute
> sticky-session ceiling, and a bm-verify solve flow. The notes are a
> maintained WBB adaptation (not an MBB pointer stub): they open with a
> "What is WBB-specific" section, then carry the access model, the
> response-class taxonomy, measured campaign behavior, and the operational
> rules. Do **not** start a capture from intuition.

**The `-raw` / `-data` split is load-bearing: never mix them.** This repo
scrapes and parses only. Reshaping parsed JSON into tidy season datasets is
`ncaa-wbb-hoops-data`'s job.

## Current capture state (verify before trusting)

`wbb/` currently holds `schedule_master.parquet`, `schedules/`, `rosters/`,
`team_rosters/`, `teams/`, and `xwalk/`. **`wbb/raw/` and `wbb/json/` do not
exist yet** — the pbp capture campaign still needs a run.

**Why:** the 2026-08-01 multi-season campaign captured **zero** pbp bundles. A
stale `MAX_SEASON=2025` guard in `scripts/run_wbb_backfill.sh` refused season
2026 with `rc=2`, which the range driver read as a capture hard-stop and burned
the campaign's first round on cooldowns. Fixed in **`f6153441`** (guard lifted
to 2026).

**The rule that fix encodes: `MAX_SEASON` must be bumped together with the
bundled WBB crosswalk.** The guard tracks
`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv` (read via
`sportsdataverse.wbb.wbb_ncaa_team_ids.ncaa_wbb_team_ids()`); when the
crosswalk gains a season, bump `MAX_SEASON` in that script in the same change.
A stale guard does not fail loudly — it burns a campaign.

## Layout

```
python/       # flat ncaa_* modules, run by path (NOT an installable package)
  ncaa_discover.py  ncaa_capture.py  ncaa_parse.py  ncaa_bundle.py
  ncaa_datasets.py  ncaa_rosters.py  ncaa_identity.py
  ncaa_canary.py    ncaa_espn_game_xwalk.py
scripts/      # bash drivers (see below)
tests/        # suite + fixtures/ at repo ROOT, not under python/
docs/         # SCRAPING_NOTES.md (required reading)
logs/         # run logs (no longer gitignored — D22)
wbb/          # the committed capture tree; see README.md
```

The `python/` modules are **shims over `sportsdataverse.scrape.ncaa`**, the
shared NCAA hoops engine (sdv-py #328/#330/#331). Fix transport, fetcher, and
parser bugs **upstream in sdv-py**, not inline here — the MBB twin
(`hoopR-dev/ncaa-mbb-hoops-raw`) shares that engine, so an inline fix here only
half-fixes the problem.

### scripts/

Per-stage: `run_discover.sh`, `run_capture.sh`, `run_parse.sh`,
`run_rosters.sh`, `run_datasets.sh`.

Wrappers: `run_canary.sh` (pre-flight proxy-vendor scorecard),
`run_wbb_backfill.sh` (single-season discover->capture->parse chain; holds the
`MAX_SEASON` guard), `run_wbb_backfill_range.sh` (multi-season campaign
wrapping it), `run_reference_backfill.sh` (reference-only companion; no pbp
capture), `run_autocommit.sh` (settle-aware incremental commit sweep, safe to
run concurrently with an active capture).

`.github/workflows/orphan_scripts.yml` runs the shared
`sportsdataverse/.github` gate: **every** entry in `scripts/` must be
referenced by a runbook, a workflow, or another script.

## Packaging

Root `pyproject.toml` + `uv.lock`. **There is no `requirements.txt`.**

- `sportsdataverse` is pinned to git `main` via `[tool.uv.sources]` — the NCAA
  engine lands on main ahead of PyPI. CI installs with `uv sync --frozen`, so
  the lockfile is the contract.
- `[tool.uv] package = false` — `python/` holds flat modules run by path.
- pytest: `testpaths = ["tests"]`, `pythonpath = ["python"]`, and an
  **`archive` marker** for tests that need the committed `wbb/` tree. CI
  deselects them (`-m "not archive"`).
- ruff: `line-length = 100`, rule set pinned to `select = ["E4","E7","E9","F","I"]`,
  `ignore = ["E712"]` (polars bool masks are written `pl.col("c") == True` on
  purpose). The pin is deliberate — ruff's defaults shift between versions.

```sh
uv sync --frozen
uv run pytest -q -m "not archive"
uv run ruff check python/ tests/
```

## CI

- `.github/workflows/tests.yml` — sparse-checkout (the committed `wbb/` tree
  never lands on a runner; the tests never read it), then `uv sync --frozen`
  -> `ruff check python/ tests/` -> `bash -n scripts/*.sh` ->
  `pytest -q -m "not archive"`.
- `.github/workflows/orphan_scripts.yml` — the shared orphan-scripts gate.

## Commit Convention

[Conventional Commits](https://www.conventionalcommits.org/):
`type(scope): description`. Common types: `feat`, `fix`, `chore`, `ci`, `docs`,
`refactor`, `test`, `style`, `build`. Use `type!:` or a `BREAKING CHANGE:`
footer for breaking changes.

**Never include AI agents or assistants (Claude, Copilot, Cursor, GPT, Gemini,
…) as co-authors.** Omit all `Co-Authored-By` trailers referencing AI tools,
whether the change was generated, refactored, or reviewed with AI assistance —
the human author is the sole attributable contributor. This is hook-enforced.

## Cross-Repo References

- Downstream reshaper: `wehoop-dev/ncaa-wbb-hoops-data`
- MBB twin (same engine, same rules): `hoopR-dev/ncaa-mbb-hoops-raw`
- SDK internals: <https://github.com/sportsdataverse/sportsdataverse-py/blob/main/CLAUDE.md>
