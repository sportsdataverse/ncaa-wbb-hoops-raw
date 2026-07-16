"""Tests for ncaa_bundle read/write helpers."""

from __future__ import annotations

import gzip
import tempfile
from pathlib import Path

from ncaa_bundle import bundle_path, is_captured, read_bundle, write_bundle

CONTRACT_KEYS = {"contest_id", "league", "season", "captured_at", "urls", "pages"}
PAGE_KEYS = {"play_by_play", "box_score", "individual_stats"}


def test_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        league = "wbb"
        season = "2025-26"
        contest_id = "401700000"
        urls = {
            "play_by_play": "https://example.com/pbp",
            "box_score": "https://example.com/box",
            "individual_stats": "https://example.com/stats",
        }
        pages = {
            "play_by_play": {"plays": [1, 2, 3]},
            "box_score": {"teams": ["A", "B"]},
            "individual_stats": {"players": [{"name": "X"}]},
        }
        captured_at = "2026-07-13T00:00:00+00:00"

        assert is_captured(root, league, season, contest_id) is False

        out_path = write_bundle(root, league, season, contest_id, pages=pages, urls=urls, captured_at=captured_at)

        assert is_captured(root, league, season, contest_id) is True

        expected_path = bundle_path(root, league, season, contest_id)
        assert out_path == expected_path
        assert str(expected_path).replace("\\", "/").endswith(f"{league}/raw/{season}/{contest_id}.json.gz")

        # File is valid gzip.
        with gzip.open(expected_path, "rb") as f:
            f.read()

        bundle = read_bundle(expected_path)
        assert set(bundle.keys()) == CONTRACT_KEYS
        assert set(bundle["pages"].keys()) == PAGE_KEYS
        assert bundle["contest_id"] == contest_id
        assert bundle["league"] == league
        assert bundle["season"] == season
        assert bundle["captured_at"] == captured_at
        assert bundle["urls"] == urls
        assert bundle["pages"] == pages


def main() -> None:
    test_round_trip()
    print("OK")


if __name__ == "__main__":
    main()
