"""Offline tests for the game-capture loop (no network)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ncaa_bundle import is_captured, read_bundle
from ncaa_capture import capture_contests, shard

# Sibling checkout: .../sdv-dev/{wehoop-dev/ncaa-wbb-hoops-raw, sdv-py}.
FIXTURE_DIR = Path(__file__).resolve().parents[3] / "sdv-py" / "tests" / "fixtures" / "ncaa" / "bigballr" / "html"

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
    return (FIXTURE_DIR / f"{_FIXTURE_NAME[key]}_{contest_id}.html").read_text(encoding="utf-8")


def _fixture_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
    return {key: _fixture_text(key, contest_id) for key in _FIXTURE_NAME}


def test_capture_contests_writes_bundles_matching_fixtures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = capture_contests(CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn)

        assert counts == {"captured": 8, "skipped": 0, "failed": 0}

        for contest_id in CONTEST_IDS:
            assert is_captured(root, "wbb", "2026", contest_id) is True
            bundle = read_bundle(root / "wbb" / "raw" / "2026" / f"{contest_id}.json.gz")
            fixture_len = len(_fixture_text("play_by_play", contest_id).encode("utf-8"))
            got_len = len(bundle["pages"]["play_by_play"].encode("utf-8"))
            assert abs(got_len - fixture_len) / fixture_len <= 0.02


def test_capture_contests_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        capture_contests(CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn)

        counts = capture_contests(CONTEST_IDS, 2026, root=root, fetch_pages_fn=_fixture_fetch_pages_fn)

        assert counts == {"captured": 0, "skipped": 8, "failed": 0}


def test_capture_contests_rejects_shell_page() -> None:
    def shell_fetch_pages_fn(fetcher: object, contest_id: str) -> dict:
        pages = _fixture_fetch_pages_fn(fetcher, contest_id)
        pages["box_score"] = "<html>shell</html>"
        return pages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counts = capture_contests([CONTEST_IDS[0]], 2026, root=root, fetch_pages_fn=shell_fetch_pages_fn)

        assert counts == {"captured": 0, "skipped": 0, "failed": 1}
        assert is_captured(root, "wbb", "2026", CONTEST_IDS[0]) is False


def test_shard_disjoint_and_covers_all() -> None:
    a = shard(CONTEST_IDS, 0, 2)
    b = shard(CONTEST_IDS, 1, 2)

    assert set(a) & set(b) == set()
    assert set(a) | set(b) == set(CONTEST_IDS)


def main() -> None:
    test_capture_contests_writes_bundles_matching_fixtures()
    test_capture_contests_is_idempotent()
    test_capture_contests_rejects_shell_page()
    test_shard_disjoint_and_covers_all()
    print("OK")


if __name__ == "__main__":
    main()
