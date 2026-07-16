"""Per-contest game-page capture loop (3-page bundle write).

For each contest_id not already captured (:func:`ncaa_bundle.is_captured`),
fetches the play-by-play / box-score / individual-stats pages through one
shared browser-transport :class:`NcaaFetcher` session, validates each page
cleared the Akamai challenge (large enough + enough ``<tr>`` rows), and
writes the 3-page bundle via :func:`ncaa_bundle.write_bundle`.

Single-threaded by design: Playwright's sync API is not thread-safe, so one
browser session drives contests serially. Throughput scales by running
separate launcher PROCESSES over disjoint shards -- see :func:`shard`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from ncaa_bundle import is_captured, write_bundle

logger = logging.getLogger(__name__)

PAGE_KEYS = ("play_by_play", "box_score", "individual_stats")
_MIN_BYTES = 20000
# ponytail: 30, not the round-number 50 -- the committed real-capture
# fixtures show box_score is a small fixed-structure team-stats table
# (~41 <tr> on every one of the 8 games, independent of score), while
# pbp/individual_stats run into the hundreds. 30 stays well clear of a
# genuine shell (0 rows) with margin below the observed real floor.
_MIN_ROWS = 30
_TR_RE = re.compile(r"<tr[\s>]", re.IGNORECASE)

FetchPagesFn = Callable[[Any, str], Dict[str, str]]

__all__ = ["capture_contests", "shard"]


def _is_clean(html: str) -> bool:
    """A page cleared the challenge iff it's large and table-populated."""
    return len(html) > _MIN_BYTES and len(_TR_RE.findall(html)) > _MIN_ROWS


def _default_fetch_pages_fn() -> FetchPagesFn:
    def fetch(fetcher: Any, contest_id: str) -> Dict[str, str]:
        return {
            "play_by_play": fetcher.fetch_game_pbp(contest_id, force=True),
            "box_score": fetcher.fetch_game_box(contest_id, force=True),
            "individual_stats": fetcher.fetch_game_individual_stats(contest_id, force=True),
        }

    return fetch


def _page_urls(contest_id: str) -> Dict[str, str]:
    base = f"https://stats.ncaa.org/contests/{contest_id}"
    return {
        "play_by_play": f"{base}/play_by_play",
        "box_score": f"{base}/box_score",
        "individual_stats": f"{base}/individual_stats",
    }


def shard(contest_ids: "List[str]", i: int, n: int) -> "List[str]":
    """Split a sorted contest_id list into disjoint shard *i* of *n*.

    Launcher parallelism model: start N separate processes, each running
    one :class:`NcaaFetcher` session over ``shard(ids, i, n)`` for its
    ``i``. Never share a browser across threads.
    """
    return [c for k, c in enumerate(sorted(contest_ids)) if k % n == i]


def capture_contests(
    contest_ids: "List[str]",
    season: int,
    *,
    league: str = "wbb",
    root: Union[str, Path],
    fetch_pages_fn: Optional[FetchPagesFn] = None,
) -> "Dict[str, int]":
    """Capture the 3-page bundle for each contest_id not already captured.

    Args:
        contest_ids: Contest ids to capture (kept as str throughout).
        season: Ending-year season int, stamped on the bundle as ``str(season)``.
        league: League slug (``"mbb"`` / ``"wbb"``).
        root: Root directory of the raw data tree (passed to
            :func:`ncaa_bundle.write_bundle` / :func:`ncaa_bundle.is_captured`).
        fetch_pages_fn: ``(fetcher, contest_id) -> {"play_by_play":html,
            "box_score":html, "individual_stats":html}``. Defaults to one
            shared :class:`NcaaFetcher.with_browser` session calling
            ``fetch_game_pbp/box/individual_stats(id, force=True)``. Inject
            a fake for offline tests -- the *fetcher* arg is unused by the
            default path's caller in that case and may be ``None``.

    Returns:
        Counts: ``{"captured": int, "skipped": int, "failed": int}``.

    Raises:
        SystemExit: A fetch raised ``RuntimeError`` (NcaaFetcher's
            ban-suspect / exhausted-proxy sentinel) -- the whole run stops
            immediately rather than continuing to the next contest.
    """
    season_str = str(season)
    counts = {"captured": 0, "skipped": 0, "failed": 0}

    fetch_fn = fetch_pages_fn if fetch_pages_fn is not None else _default_fetch_pages_fn()
    use_live_fetcher = fetch_pages_fn is None

    fetcher: Any = None
    if use_live_fetcher:
        from sportsdataverse.wbb.wbb_ncaa_fetch import NcaaFetcher

        fetcher = NcaaFetcher.with_browser()

    try:
        for contest_id in contest_ids:
            if is_captured(root, league, season_str, contest_id):
                counts["skipped"] += 1
                continue

            try:
                pages = fetch_fn(fetcher, contest_id)
            except RuntimeError as exc:
                logger.error("BAN-SUSPECT: hard-stopping capture at contest_id=%s: %s", contest_id, exc)
                raise SystemExit(f"BAN-SUSPECT: capture halted at contest_id={contest_id}: {exc}") from exc

            if not all(_is_clean(pages.get(key, "")) for key in PAGE_KEYS):
                logger.warning("challenge not cleared for contest_id=%s -- bundle not written", contest_id)
                counts["failed"] += 1
                continue

            write_bundle(
                root,
                league,
                season_str,
                contest_id,
                pages={key: pages[key] for key in PAGE_KEYS},
                urls=_page_urls(contest_id),
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
            counts["captured"] += 1
    finally:
        if fetcher is not None:
            closer = getattr(fetcher, "__exit__", None)
            if callable(closer):
                closer(None, None, None)

    return counts


def _parse_shard(spec: str) -> "tuple[int, int]":
    """``"i/N"`` -> ``(i, N)``. Defaults to ``0/1`` (no sharding)."""
    i_str, _, n_str = spec.partition("/")
    i, n = int(i_str), int(n_str or "1")
    if n < 1 or not (0 <= i < n):
        raise ValueError(f"invalid --shard {spec!r}; expected 'i/N' with 0<=i<N")
    return i, n


def _main() -> None:
    import argparse

    import polars as pl

    parser = argparse.ArgumentParser(description="Capture the 3-page bundle for a season's not-yet-captured contests.")
    parser.add_argument("--season", type=int, required=True, help="Ending year of the season, e.g. 2026.")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Root of the raw data tree (default: repo root).",
    )
    parser.add_argument("--league", default="wbb", help="League slug (default: wbb).")
    parser.add_argument("--shard", default="0/1", help="This process's shard as 'i/N' (default: 0/1, no sharding).")
    args = parser.parse_args()
    i, n = _parse_shard(args.shard)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    master_path = Path(args.root) / args.league / "schedule_master.parquet"
    master = pl.read_parquet(master_path)
    pending = master.filter(pl.col("captured") == False).get_column("contest_id").to_list()  # noqa: E712
    my_ids = shard(pending, i, n)
    print(f"pending={len(pending)} shard={i}/{n} assigned={len(my_ids)}")

    counts = capture_contests(my_ids, args.season, league=args.league, root=args.root)
    print(f"captured={counts['captured']} skipped={counts['skipped']} failed={counts['failed']}")


if __name__ == "__main__":
    _main()
