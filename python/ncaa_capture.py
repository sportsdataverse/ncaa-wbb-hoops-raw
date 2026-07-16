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
import os
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

# A sustained "challenge not cleared" run means the browser session has been
# SOFT-banned: every further request yields nothing AND digs the ban deeper.
# The 2026-07-13 backfill proved it -- 1262 consecutive failures across a full
# hour (zero bundles written) before stats.ncaa.org escalated to a hard 403.
# Stop at the first sign instead; the caller cools down and resumes.
DEFAULT_MAX_CONSECUTIVE_FAILURES = 25

FetchPagesFn = Callable[[Any, str], Dict[str, str]]

__all__ = ["capture_contests", "shard", "DEFAULT_MAX_CONSECUTIVE_FAILURES"]


def _is_clean(html: str) -> bool:
    """A page cleared the challenge iff it's large and table-populated."""
    return len(html) > _MIN_BYTES and len(_TR_RE.findall(html)) > _MIN_ROWS


def _default_fetch_pages_fn() -> FetchPagesFn:
    def fetch(fetcher: Any, contest_id: str) -> Dict[str, str]:
        return {
            "play_by_play": fetcher.fetch_game_pbp(contest_id, force=True),
            "box_score": fetcher.fetch_game_box(contest_id, force=True),
            "individual_stats": fetcher.fetch_game_individual_stats(
                contest_id, force=True
            ),
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
    max_contests: Optional[int] = None,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
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
        max_contests: Stop CLEANLY after this many NEW bundles are written
            (chunked backfill -- the browser session degrades past roughly
            2000 bundles / ~2h, so capture a chunk, cool down, resume).
            ``None`` (default) = unlimited.
        max_consecutive_failures: Hard-stop after this many consecutive
            challenge-not-cleared contests (soft-ban guard, see
            :data:`DEFAULT_MAX_CONSECUTIVE_FAILURES`). The counter resets on
            every successful capture, so isolated bad games never trip it.
            ``0`` disables the guard.

    Returns:
        Counts: ``{"captured": int, "skipped": int, "failed": int}``.

    Raises:
        SystemExit: A fetch raised ``RuntimeError`` (NcaaFetcher's
            ban-suspect / exhausted-proxy sentinel), OR
            *max_consecutive_failures* consecutive challenges failed to clear
            (soft-ban) -- either way the whole run stops immediately rather
            than continuing to the next contest. Both are resumable: re-run
            after a cooldown and already-captured contests are skipped.
    """
    season_str = str(season)
    counts = {"captured": 0, "skipped": 0, "failed": 0}
    consecutive_failures = 0

    fetch_fn = (
        fetch_pages_fn if fetch_pages_fn is not None else _default_fetch_pages_fn()
    )
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
                logger.error(
                    "BAN-SUSPECT: hard-stopping capture at contest_id=%s: %s",
                    contest_id,
                    exc,
                )
                raise SystemExit(
                    f"BAN-SUSPECT: capture halted at contest_id={contest_id}: {exc}"
                ) from exc

            if not all(_is_clean(pages.get(key, "")) for key in PAGE_KEYS):
                logger.warning(
                    "challenge not cleared for contest_id=%s -- bundle not written",
                    contest_id,
                )
                counts["failed"] += 1
                consecutive_failures += 1
                if (
                    max_consecutive_failures
                    and consecutive_failures >= max_consecutive_failures
                ):
                    msg = (
                        f"SOFT-BAN: capture halted after {consecutive_failures} consecutive "
                        f"challenge failures (last contest_id={contest_id}); the browser session "
                        f"is no longer clearing bm-verify. Cool down, then re-run to resume."
                    )
                    logger.error("%s", msg)
                    raise SystemExit(msg)
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
            consecutive_failures = 0  # a clear page proves the session is healthy

            if max_contests is not None and counts["captured"] >= max_contests:
                logger.info(
                    "chunk complete: %d new bundles captured (--max-contests) -- "
                    "stopping cleanly; re-run to continue",
                    counts["captured"],
                )
                break
    finally:
        if fetcher is not None:
            closer = getattr(fetcher, "__exit__", None)
            if callable(closer):
                closer(None, None, None)

    return counts


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    """Env override for an int knob; an unparseable value falls back to *default*."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("ignoring invalid %s=%r (want an int)", name, raw)
        return default


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

    parser = argparse.ArgumentParser(
        description="Capture the 3-page bundle for a season's not-yet-captured contests."
    )
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
    parser.add_argument("--league", default="wbb", help="League slug (default: wbb).")
    parser.add_argument(
        "--shard",
        default="0/1",
        help="This process's shard as 'i/N' (default: 0/1, no sharding).",
    )
    parser.add_argument(
        "--max-contests",
        type=int,
        default=_env_int("NCAA_MAX_CONTESTS", None),
        help=(
            "Stop cleanly after N NEW bundles -- chunked backfill (env NCAA_MAX_CONTESTS). "
            "The browser session degrades past roughly 2000 bundles / ~2h, so capture a "
            "chunk, cool down, then re-run. Default: unlimited."
        ),
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=_env_int(
            "NCAA_MAX_CONSECUTIVE_FAILURES", DEFAULT_MAX_CONSECUTIVE_FAILURES
        ),
        help=(
            "Hard-stop after N consecutive challenge-not-cleared contests -- the soft-ban "
            "guard (env NCAA_MAX_CONSECUTIVE_FAILURES; 0 disables). "
            f"Default: {DEFAULT_MAX_CONSECUTIVE_FAILURES}."
        ),
    )
    args = parser.parse_args()
    i, n = _parse_shard(args.shard)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    master_path = Path(args.root) / args.league / "schedule_master.parquet"
    master = pl.read_parquet(master_path)
    pending = (
        master.filter(pl.col("captured") == False).get_column("contest_id").to_list()
    )  # noqa: E712
    my_ids = shard(pending, i, n)
    print(f"pending={len(pending)} shard={i}/{n} assigned={len(my_ids)}")

    counts = capture_contests(
        my_ids,
        args.season,
        league=args.league,
        root=args.root,
        max_contests=args.max_contests,
        max_consecutive_failures=args.max_consecutive_failures,
    )
    print(
        f"captured={counts['captured']} skipped={counts['skipped']} failed={counts['failed']}"
    )


if __name__ == "__main__":
    _main()
