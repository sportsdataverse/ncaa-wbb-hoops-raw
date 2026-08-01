"""Season -> contest_id discovery (team-ids -> schedules -> dedup).

Reuses the bigballR port already vendored in sdv-py: the ``(team, season) ->
id`` crosswalk (:func:`ncaa_wbb_team_ids` / :func:`ncaa_mbb_team_ids`), the
team-schedule-page parser (:func:`parse_ncaa_bb_team_schedule`), and the
browser-transport fetcher (:class:`NcaaFetcher`) for the live path. Each game
appears on two teams' schedules, so the core job here is: fetch every team
page for a season, extract ``contests/{id}/`` links, and dedup across teams.

Discovery runs single-threaded (one browser, one team page at a time) --
Playwright's sync API is not thread-safe. Parallelism for the heavier
capture stage is achieved by running separate launcher PROCESSES over
disjoint shards, never internal threads sharing one browser.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, List, Optional, Union

import polars as pl

from sportsdataverse.mbb.mbb_ncaa_schedule import parse_ncaa_bb_team_schedule
from sportsdataverse.mbb.mbb_ncaa_team_ids import ncaa_mbb_team_ids
from sportsdataverse.wbb.wbb_ncaa_team_ids import ncaa_wbb_team_ids

logger = logging.getLogger(__name__)

FetchFn = Callable[[int], str]

_MASTER_COLUMNS: List[str] = ["contest_id", "season", "captured"]

# league -> crosswalk getter. A dict, not a registry -- two entries, one call
# site (discover_season below). Keep it minimal; do not grow this into a
# plugin layer for what is a two-way branch.
_TEAM_ID_CROSSWALKS = {
    "wbb": ncaa_wbb_team_ids,
    "mbb": ncaa_mbb_team_ids,
}

#: Fetch attempts per team page before skipping it (bm-verify flake tolerance).
_TEAM_TRIES = 3
#: Consecutive fully-failed teams that reads as a ban -> abort the sweep.
_MAX_CONSECUTIVE_TEAM_FAILURES = 5

__all__ = ["discover_season"]


def _season_str(season: int) -> str:
    """Ending-year int -> crosswalk ``"YYYY-YY"`` (2026 -> "2025-26")."""
    return f"{season - 1}-{str(season)[-2:]}"


def _default_fetch_fn(shard_i: int = 0, shard_n: int = 1) -> FetchFn:
    """Live fetch: one shared browser-transport session, ``teams/{id}``.

    ``NCAA_VENDOR`` (e.g. ``decodo_patchright``) routes discovery through a
    ``canary_vendors.toml`` transport — the same seam capture uses. Team pages
    sit behind the same Akamai bm-verify as game pages, and the ProxyBonanza
    datacenter pool stopped clearing it (2026-08-01: a 2025 discovery burned
    35 minutes in re-solve loops with zero yield), while Decodo US-residential
    cleared the same canary 10/10.
    """
    vendor = os.environ.get("NCAA_VENDOR")
    if vendor:
        from ncaa_capture import _vendor_fetcher

        repo_root = Path(__file__).resolve().parents[1]
        fetcher = _vendor_fetcher(vendor, repo_root, shard_i=shard_i, shard_n=shard_n)
        return lambda team_id: fetcher.fetch_html(f"teams/{team_id}")
    from sportsdataverse.wbb.wbb_ncaa_fetch import NcaaFetcher

    fetcher = NcaaFetcher.with_browser()
    return lambda team_id: fetcher.fetch_html(f"teams/{team_id}")


def _team_contest_ids(team_id: int, fetch_fn: FetchFn, league: str) -> List[str]:
    try:
        html = fetch_fn(team_id)
    except RuntimeError:
        # NcaaFetcher raises RuntimeError on a ban-suspect / exhausted-proxy
        # response ("BAN-SUSPECT:<marker>" is folded into the message). The
        # sweep in discover_season decides retry/skip/abort -- one flaky
        # bm-verify solve must not read as a ban.
        logger.warning("fetch failed for team_id=%s (bm-verify / ban-suspect)", team_id)
        raise
    schedule = parse_ncaa_bb_team_schedule(html, team_id, league=league)
    return schedule.get_column("game_id").drop_nulls().to_list()


def discover_season(
    season: int,
    *,
    league: str = "wbb",
    limit_teams: Optional[int] = None,
    fetch_fn: Optional[FetchFn] = None,
    team_ids: Optional[List[int]] = None,
    root: Optional[Union[str, Path]] = None,
    shard: "tuple[int, int]" = (0, 1),
    write_master: bool = True,
) -> pl.DataFrame:
    """Discover every ``contest_id`` played in *season* by sweeping team pages.

    Args:
        season: Ending year of the season, e.g. ``2026`` for 2025-26 (matches
            the launcher's ``--season``). Converted to the crosswalk's
            ``"YYYY-YY"`` format to filter the bundled ``(team, season) ->
            id`` crosswalk (live path); stamped as-is on the returned frame's
            ``season`` column.
        league: ``"wbb"`` or ``"mbb"`` -- selects both the ``(team, season)
            -> id`` crosswalk (both are bundled in sdv-py) and the schedule
            parser's league mode.
        limit_teams: Cap the number of teams swept (a small live smoke, e.g.
            one conference).
        fetch_fn: ``team_id -> html``. Defaults to a live fetch through one
            shared :class:`NcaaFetcher.with_browser` session (the team page
            can be Akamai-challenged like game pages). Inject a fake for
            offline tests.
        team_ids: Explicit team id list that bypasses the crosswalk filter
            entirely -- lets offline tests drive discovery without the
            crosswalk having the fixture's team id for the given season.
        root: If given, also write/merge ``{root}/{league}/schedule_master.
            parquet`` (columns ``contest_id``, ``season``, ``captured``), and
            checkpoint each swept team page to ``{root}/{league}/.discover/
            {season}/{team_id}.json`` so an aborted sweep RESUMES instead of
            restarting (disk-is-checkpoint, same pattern as capture). Delete
            that directory to force a from-scratch re-sweep of the season.
        shard: ``(i, N)`` -- with ``N > 1`` this process sweeps only the
            ``ids[i::N]`` slice, so ``N`` launcher PROCESSES cover disjoint
            teams (see :func:`_default_fetch_fn` for the per-worker proxy
            rotation that keeps their sticky sessions disjoint too).
        write_master: Set ``False`` on a sharded run -- a shard sees only its
            slice, so only the final shard-less merge pass should write
            ``schedule_master.parquet``.

    Returns:
        One row per unique ``contest_id`` (deduped across teams, sorted):
        ``contest_id`` (Utf8), ``season`` (Utf8).

    Raises:
        ValueError: *league* isn't ``"wbb"`` or ``"mbb"``, or the crosswalk
            filter for *season* matched zero teams on the live path
            (``team_ids`` not given) -- the latter almost certainly a
            season-format drift, raised loudly instead of silently returning
            an empty, complete-looking contest list.
        RuntimeError: ``_MAX_CONSECUTIVE_TEAM_FAILURES`` teams in a row failed
            every retry -- the real-ban signature. Whatever was swept before
            the abort is already checkpointed to disk, so the re-run resumes.
    """
    if team_ids is not None:
        ids = list(team_ids)
    else:
        try:
            crosswalk_fn = _TEAM_ID_CROSSWALKS[league]
        except KeyError:
            raise ValueError(f"unrecognized league={league!r}; expected one of {sorted(_TEAM_ID_CROSSWALKS)}") from None
        season_str = _season_str(season)
        crosswalk = crosswalk_fn()
        ids = crosswalk.filter(pl.col("season") == season_str).get_column("id").to_list()
        if not ids:
            raise ValueError(
                f"No teams found in {league!r} crosswalk for season={season!r} "
                f"(tried season_str={season_str!r}); "
                "the NCAA team-ids season format may have drifted."
            )
    if limit_teams is not None:
        ids = ids[:limit_teams]
    shard_i, shard_n = shard
    if shard_n > 1:
        # Disjoint team slice per worker. Each shard writes its own per-team
        # checkpoints; a final shard-less pass reads ALL of them (every team
        # then hits the checkpoint branch, so it fetches nothing) and writes
        # schedule_master -- which is why shard runs pass write_master=False.
        ids = ids[shard_i::shard_n]

    fn = fetch_fn if fetch_fn is not None else _default_fetch_fn(shard_i=shard_i, shard_n=shard_n)

    # Per-team tolerance + a consecutive-failure ban detector. bm-verify
    # clearing is flaky per navigation (~5% per page on the canary-validated
    # vendor; the first navigation of a cold profile fails most of all), so a
    # ~350-team sweep that hard-stops on the FIRST failure has ~zero chance of
    # completing (0.95^350). Each game appears on TWO teams' pages, so a team
    # skipped after retries is nearly lossless. A real ban looks like EVERY
    # team failing -- abort only on a consecutive-failure run.
    # Per-team disk checkpoint: a swept team's contest ids persist immediately,
    # so an abort (ban detector, Ctrl-C, crash) never loses the sweep -- the
    # re-run skips checkpointed teams and only fetches the remainder.
    scratch: Optional[Path] = None
    checkpointed: "dict[int, List[str]]" = {}
    if root is not None:
        scratch = Path(root) / league / ".discover" / str(season)
        if scratch.is_dir():
            for f in scratch.glob("*.json"):
                try:
                    checkpointed[int(f.stem)] = json.loads(f.read_text())
                except (ValueError, json.JSONDecodeError):
                    continue
        if checkpointed:
            already = sum(1 for t in ids if t in checkpointed)
            logger.info(
                "resuming discovery: %d/%d team pages already checkpointed",
                already,
                len(ids),
            )

    contest_ids: "set[str]" = set()
    consecutive = 0
    skipped: "List[int]" = []
    for team_id in ids:
        if team_id in checkpointed:
            contest_ids.update(checkpointed[team_id])
            continue
        got: Optional[List[str]] = None
        for _ in range(_TEAM_TRIES):
            try:
                got = _team_contest_ids(team_id, fn, league)
                break
            except RuntimeError:
                continue
        if got is None:
            consecutive += 1
            skipped.append(team_id)
            logger.warning(
                "team_id=%s skipped after %d tries (%d consecutive team failures)",
                team_id,
                _TEAM_TRIES,
                consecutive,
            )
            if consecutive >= _MAX_CONSECUTIVE_TEAM_FAILURES:
                raise RuntimeError(
                    f"discovery aborted: {consecutive} consecutive teams failed "
                    f"(ban-suspect); {len(contest_ids)} contest_ids gathered before abort"
                )
            continue
        consecutive = 0
        contest_ids.update(got)
        if scratch is not None:
            scratch.mkdir(parents=True, exist_ok=True)
            (scratch / f"{team_id}.json").write_text(json.dumps(got))
    if skipped:
        logger.warning(
            "discovery finished with %d/%d teams skipped: %s%s",
            len(skipped),
            len(ids),
            skipped[:10],
            "..." if len(skipped) > 10 else "",
        )

    result = pl.DataFrame({"contest_id": sorted(contest_ids)}, schema={"contest_id": pl.Utf8}).with_columns(
        pl.lit(str(season)).alias("season")
    )

    if root is not None and write_master:
        _write_master(result, root, league)

    return result


def _write_master(result: pl.DataFrame, root: Union[str, Path], league: str) -> Path:
    """Merge *result* into ``{root}/{league}/schedule_master.parquet``.

    Add-column-if-absent guard for ``captured`` (schema predates the column
    on an older master file), then union new contest_ids in without
    dropping any existing ``captured=True`` row.
    """
    path = Path(root) / league / "schedule_master.parquet"
    new_rows = result.with_columns(pl.lit(False).alias("captured")).select(_MASTER_COLUMNS)

    if path.exists():
        existing = pl.read_parquet(path)
        if "captured" not in existing.columns:
            existing = existing.with_columns(pl.lit(False).alias("captured"))
        combined = (
            pl.concat([existing.select(_MASTER_COLUMNS), new_rows], how="diagonal_relaxed")
            .sort("captured", descending=True)
            .unique(subset="contest_id", keep="first")
        )
    else:
        combined = new_rows

    combined = combined.select(_MASTER_COLUMNS).sort("contest_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.write_parquet(path)
    return path


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Discover a season's contest_ids and write schedule_master.parquet.")
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="Ending year of the season, e.g. 2026.",
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Root of the raw data tree (default: repo root).",
    )
    parser.add_argument("--league", default="wbb", help="League slug: 'wbb' or 'mbb' (default: wbb).")
    parser.add_argument(
        "--shard",
        default="0/1",
        help="This process's shard as 'i/N'. A sharded run sweeps only its "
        "team slice and does NOT write schedule_master -- run a final "
        "shard-less pass (all teams checkpointed, so it fetches nothing) "
        "to merge every shard's checkpoints into the master.",
    )
    args = parser.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = discover_season(
        args.season,
        league=args.league,
        root=args.root,
        shard=(i, n),
        write_master=(n == 1),
    )
    print(
        f"discovered {result.height} contest_ids for season={args.season} "
        f"shard={args.shard}{'' if n == 1 else ' (master NOT written -- merge pass pending)'}"
    )


if __name__ == "__main__":
    _main()
