# ncaa-wbb-hoops-raw Copilot Instructions

## Project Context

Raw-page capture + parse pipeline for `stats.ncaa.org` women's college
basketball. Three stages: **discover** (season -> contest_ids) ->
**capture** (contest -> 3-page HTML bundle) -> **parse** (bundle ->
combined per-game JSON), under `wbb/`.

Pipeline: `stats.ncaa.org -> ncaa-wbb-hoops-raw [HERE] -> ncaa-wbb-hoops-data -> sportsdataverse-data`.

**This repo scrapes; it does not reshape.** Tidy season datasets are
`ncaa-wbb-hoops-data`'s job — never mix the two stages.

This is a retarget of the sibling `ncaa-mbb-hoops-raw` scraper — same
transport, same fetcher, same safe-rate rules. The `python/` modules
default `--league` to `"wbb"`; `"mbb"` stays a legitimate runtime value
for parity checks against the MBB scraper.

## ⚠️ Before any live scrape

**Read [`docs/SCRAPING_NOTES.md`](../docs/SCRAPING_NOTES.md) first.**
stats.ncaa.org is a hostile host: patchright + a residential proxy port
pool, a ~70-minute sticky-session ceiling, and a bm-verify solve flow.
The notes are a maintained WBB adaptation (not an MBB pointer stub) and
open with a "What is WBB-specific" section.

A ban-suspect response is a **hard stop**, not a retry — wait out the
cooldown before resuming.

## Capture state + the season-guard rule

`wbb/` currently holds `schedule_master.parquet`, `schedules/`, `rosters/`,
`team_rosters/`, `teams/`, `xwalk/`. **`wbb/raw/` and `wbb/json/` do not
exist yet** — the pbp capture campaign still needs a run.

The 2026-08-01 campaign captured **zero** pbp bundles: a stale
`MAX_SEASON=2025` guard in `scripts/run_wbb_backfill.sh` refused season
2026 with `rc=2`, the range driver read that as a capture hard-stop, and
the first round burned on cooldowns. Fixed in `f6153441`.

**`MAX_SEASON` must be bumped together with the bundled WBB crosswalk**
(`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`). A stale guard doesn't
fail loudly — it burns a campaign.

## Repository Workflow

- Branch from `main`; `main` is the default branch.
- `python/` holds flat `ncaa_*` modules run by path — they are **shims over
  `sportsdataverse.scrape.ncaa`**, the shared NCAA hoops engine. Fix
  transport / fetcher / parser bugs **upstream in sdv-py**, not inline here;
  the MBB twin (`ncaa-mbb-hoops-raw`) shares that engine.

## Build & Development Commands

```sh
uv sync --frozen
uv run pytest -q -m "not archive"
uv run ruff check python/ tests/

bash scripts/run_discover.sh --season 2025
bash scripts/run_capture.sh  --season 2025
bash scripts/run_parse.sh
```

Wrapper drivers: `run_canary.sh` (proxy pre-flight), `run_wbb_backfill.sh`
(single-season chain; holds the `MAX_SEASON` guard),
`run_wbb_backfill_range.sh` (multi-season campaign),
`run_reference_backfill.sh` (reference-only), `run_rosters.sh`,
`run_datasets.sh`, `run_autocommit.sh` (settle-aware incremental commits).

## Code Style

- Follow the parent SDK's Python conventions: `snake_case`, 4-space indent.
- Deps live in `pyproject.toml` + `uv.lock` (no `requirements.txt`);
  `sportsdataverse` is pinned to git `main` via `[tool.uv.sources]` and CI
  installs with `uv sync --frozen`.
- ruff is pinned: `select = ["E4","E7","E9","F","I"]`, `ignore = ["E712"]`
  (polars bool masks are written `pl.col("c") == True` on purpose),
  `line-length = 100`. Don't rely on ruff's defaults — they shift between
  versions and turn a green tree red with no code change.
- Tests live in `tests/` at repo **root** (not under `python/`), with
  fixtures in `tests/fixtures/`. pytest is wired with `testpaths = ["tests"]`
  and `pythonpath = ["python"]`.
- Tests needing the committed `wbb/` tree carry the **`archive`** marker and
  are deselected in CI, which sparse-checks out code only.
- Every script in `scripts/` must be referenced by a runbook, workflow, or
  another script — the shared `orphan-scripts` gate fails otherwise.

## CI

- `tests.yml` — sparse-checkout, `uv sync --frozen`, `ruff check python/ tests/`,
  `bash -n scripts/*.sh`, `pytest -q -m "not archive"`.
- `orphan_scripts.yml` — the shared `sportsdataverse/.github` orphan-scripts gate.

## Cross-Repo References

- Downstream reshaper: <https://github.com/sportsdataverse/ncaa-wbb-hoops-data>
- MBB twin: <https://github.com/sportsdataverse/ncaa-mbb-hoops-raw>
- SDK internals: <https://github.com/sportsdataverse/sportsdataverse-py/blob/main/CLAUDE.md>

## Conventional Commits

Use: `type(scope): description`. Common types: `feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`. Use `type!:` or a `BREAKING CHANGE:` footer for breaking changes.

**Important: Never include AI agents or assistants (e.g., Claude, Copilot, Cursor, GPT, Gemini) as co-authors on commits.** Omit all `Co-Authored-By` trailers referencing AI tools. This applies whether the change was generated, refactored, or reviewed with AI assistance — the human author is the sole attributable contributor.
