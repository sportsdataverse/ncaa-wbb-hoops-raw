"""Offline tests for the game-capture loop (no network)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ncaa_bundle import is_captured, read_bundle
from ncaa_capture import capture_contests, shard

# Sibling checkout: .../sdv-dev/{wehoop-dev/ncaa-wbb-hoops-raw, sdv-py}.
FIXTURE_DIR = (
    Path(__file__).resolve().parents[3]
    / "sdv-py"
    / "tests"
    / "fixtures"
    / "ncaa"
    / "bigballr"
    / "html"
)

CONTEST_IDS = [
    "1613299",
    "5722355",
    "5728709",
    "5732292",
    "5733807",
    "6470186",
    "6479592",
    "6479639",
]

_FIXTURE_NAME = {
    "play_by_play": "pbp",
    "box_score": "box",
    "individual_stats": "individual_stats",
}


def _fixture_text(key: str, contest_id: str) -> str:
    return (FIXTURE_DIR / f"{_FIXTURE_NAME[key]}_{contest_id}.html").read_text(
        encoding="utf-8"
    )


def _fixture_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
    return {key: _fixture_text(key, contest_id) for key in _FIXTURE_NAME}


def test_capture_contests_writes_bundles_matching_fixtures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = capture_contests(
            CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn
        )

        assert counts == {"captured": 8, "skipped": 0, "failed": 0}

        for contest_id in CONTEST_IDS:
            assert is_captured(root, "wbb", "2026", contest_id) is True
            bundle = read_bundle(
                root / "wbb" / "raw" / "2026" / f"{contest_id}.json.gz"
            )
            fixture_len = len(_fixture_text("play_by_play", contest_id).encode("utf-8"))
            got_len = len(bundle["pages"]["play_by_play"].encode("utf-8"))
            assert abs(got_len - fixture_len) / fixture_len <= 0.02


def test_capture_contests_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        capture_contests(
            CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn
        )

        counts = capture_contests(
            CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn
        )

        assert counts == {"captured": 0, "skipped": 8, "failed": 0}


def test_capture_contests_rejects_shell_page() -> None:
    def shell_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
        pages = _fixture_fetch_pages_fn(fetcher, contest_id)
        pages["box_score"] = "<html>shell</html>"
        return pages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = capture_contests(
            [CONTEST_IDS[0]], 2026, root=root, fetch_pages_fn=shell_fetch_pages_fn
        )

        assert counts == {"captured": 0, "skipped": 0, "failed": 1}
        assert is_captured(root, "wbb", "2026", CONTEST_IDS[0]) is False


def _always_shell_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
    """Every page fails the challenge -- simulates a soft-banned session."""
    pages = _fixture_fetch_pages_fn(fetcher, contest_id)
    pages["box_score"] = "<html>shell</html>"
    return pages


def test_capture_hard_stops_on_consecutive_challenge_failures() -> None:
    """A soft-ban must stop the run, not silently burn the whole shard.

    The 2026-07-13 backfill logged 1262 consecutive failures across a full hour
    (zero bundles) before earning a hard 403 -- this guard is what prevents that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            capture_contests(
                CONTEST_IDS,
                2026,
                root=root,
                fetch_pages_fn=_always_shell_fetch_pages_fn,
                max_consecutive_failures=3,
            )
        except SystemExit as exc:
            assert "SOFT-BAN" in str(exc)
        else:
            raise AssertionError("expected SystemExit from the soft-ban guard")

        # Stopped AT the threshold -- it did not keep hammering the rest of the shard.
        for contest_id in CONTEST_IDS:
            assert is_captured(root, "wbb", "2026", contest_id) is False


def test_consecutive_failure_counter_resets_on_success() -> None:
    """Isolated bad games must never trip the guard -- only a sustained run does."""
    bad = set(CONTEST_IDS[::2])  # every other game fails: max run length is 1

    def alternating_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
        pages = _fixture_fetch_pages_fn(fetcher, contest_id)
        if contest_id in bad:
            pages["box_score"] = "<html>shell</html>"
        return pages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = capture_contests(
            CONTEST_IDS,
            2026,
            root=root,
            fetch_pages_fn=alternating_fetch_pages_fn,
            max_consecutive_failures=3,
        )

        # Ran to completion despite 4 failures -- none consecutive enough to trip.
        assert counts == {"captured": 4, "skipped": 0, "failed": 4}


def test_max_contests_stops_chunk_cleanly_and_resumes() -> None:
    """Chunked backfill: capture N, stop clean (no raise), resume the rest."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = capture_contests(
            CONTEST_IDS,
            2026,
            root=root,
            fetch_pages_fn=_fixture_fetch_pages_fn,
            max_contests=3,
        )
        assert first == {"captured": 3, "skipped": 0, "failed": 0}

        # Resume: the 3 already-captured are skipped, the next 3 land.
        second = capture_contests(
            CONTEST_IDS,
            2026,
            root=root,
            fetch_pages_fn=_fixture_fetch_pages_fn,
            max_contests=3,
        )
        assert second == {"captured": 3, "skipped": 3, "failed": 0}

        # Finish the tail (8 total: 3 + 3 + 2).
        third = capture_contests(
            CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn
        )
        assert third == {"captured": 2, "skipped": 6, "failed": 0}
        assert all(is_captured(root, "wbb", "2026", c) for c in CONTEST_IDS)


def test_shard_disjoint_and_covers_all() -> None:
    a = shard(CONTEST_IDS, 0, 2)
    b = shard(CONTEST_IDS, 1, 2)

    assert set(a) & set(b) == set()
    assert set(a) | set(b) == set(CONTEST_IDS)


def main() -> None:
    test_capture_contests_writes_bundles_matching_fixtures()
    test_capture_contests_is_idempotent()
    test_capture_contests_rejects_shell_page()
    test_capture_hard_stops_on_consecutive_challenge_failures()
    test_consecutive_failure_counter_resets_on_success()
    test_max_contests_stops_chunk_cleanly_and_resumes()
    test_shard_disjoint_and_covers_all()
    print("OK")


if __name__ == "__main__":
    main()
