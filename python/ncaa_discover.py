"""Season -> contest_id discovery (team-ids -> schedules -> dedup).

Reuses the bigballR port already vendored in sdv-py: the ``(team, season) ->
id`` crosswalk (:func:`ncaa_mbb_team_ids` / :func:`ncaa_wbb_team_ids`), the
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

import logging
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

__all__ = ["discover_season"]


def _season_str(season: int) -> str:
    """Ending-year int -> crosswalk ``"YYYY-YY"`` (2026 -> "2025-26")."""
    return f"{season - 1}-{str(season)[-2:]}"


def _default_fetch_fn() -> FetchFn:
    """Live fetch: one shared browser-transport session, ``teams/{id}``."""
    from sportsdataverse.wbb.wbb_ncaa_fetch import NcaaFetcher

    fetcher = NcaaFetcher.with_browser()
    return lambda team_id: fetcher.fetch_html(f"teams/{team_id}")


def _team_contest_ids(team_id: int, fetch_fn: FetchFn, league: str) -> List[str]:
    try:
        html = fetch_fn(team_id)
    except RuntimeError:
        # NcaaFetcher raises RuntimeError on a ban-suspect / exhausted-proxy
        # response ("BAN-SUSPECT:<marker>" is folded into the message) --
        # hard stop rather than silently skipping the team.
        logger.error("NCAA discovery hard-stopped on team_id=%s (ban-suspect / fetch failure)", team_id)
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
            parquet`` (columns ``contest_id``, ``season``, ``captured``).

    Returns:
        One row per unique ``contest_id`` (deduped across teams, sorted):
        ``contest_id`` (Utf8), ``season`` (Utf8).

    Raises:
        ValueError: *league* isn't ``"wbb"`` or ``"mbb"``, or the crosswalk
            filter for *season* matched zero teams on the live path
            (``team_ids`` not given) -- the latter almost certainly a
            season-format drift, raised loudly instead of silently returning
            an empty, complete-looking contest list.
        RuntimeError: A team-page fetch hit a ban-suspect / exhausted-proxy
            response -- discovery stops immediately rather than returning a
            partial result silently.
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
                f"No teams found in {league!r} crosswalk for season={season!r} (tried season_str={season_str!r}); "
                "the NCAA team-ids season format may have drifted."
            )
    if limit_teams is not None:
        ids = ids[:limit_teams]

    fn = fetch_fn if fetch_fn is not None else _default_fetch_fn()

    contest_ids: "set[str]" = set()
    for team_id in ids:
        contest_ids.update(_team_contest_ids(team_id, fn, league))

    result = pl.DataFrame({"contest_id": sorted(contest_ids)}, schema={"contest_id": pl.Utf8}).with_columns(
        pl.lit(str(season)).alias("season")
    )

    if root is not None:
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
    parser.add_argument("--season", type=int, required=True, help="Ending year of the season, e.g. 2026.")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Root of the raw data tree (default: repo root).",
    )
    parser.add_argument("--league", default="wbb", help="League slug: 'wbb' or 'mbb' (default: wbb).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = discover_season(args.season, league=args.league, root=args.root)
    print(f"discovered {result.height} contest_ids for season={args.season}")


if __name__ == "__main__":
    _main()
