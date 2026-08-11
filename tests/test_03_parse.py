"""Offline tests for ncaa_wbb_03_games_parse (raw bundle -> combined parsed JSON). No network.

WBB retarget of the shared template's parser tests: sweeps the 4 real WBB
fixture games under ``league="wbb"`` (the mbb template sweeps 8 ids -- 4 mbb
+ 4 wbb -- under the mbb default). Two additional tests
(``test_wbb_quarter_period_model_changes_period_length`` and
``test_wbb_shots_league_label_changes_arc_classification``) prove the two
WBB-specific parser deltas are real: each asserts the *same* fixture parsed
under ``league="mbb"`` produces a DIFFERENT value, so the assertion could not
also pass under the mbb model.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ncaa_bundle import read_bundle, write_bundle
from ncaa_wbb_03_games_parse import parse_and_write, parse_bundle, write_parsed

_FIX = Path(__file__).resolve().parent / "fixtures" / "ncaa" / "bigballr" / "html"

CONTEST_IDS = [
    "5722355",
    "5728709",
    "5732292",
    "5733807",
]

FAMILY_KEYS = {"pbp", "lineups", "player_box", "team_box", "shots", "possessions"}
KNOWN_GOOD_GAME = "5722355"


def _fixture_bundle(contest_id: str) -> dict:
    pbp_html = (_FIX / f"pbp_{contest_id}.html").read_text(encoding="utf-8")
    box_html = (_FIX / f"box_{contest_id}.html").read_text(encoding="utf-8")
    stats_html = (_FIX / f"individual_stats_{contest_id}.html").read_text(encoding="utf-8")
    return {
        "contest_id": contest_id,
        "league": "wbb",
        "season": "2024-25",
        "captured_at": "2024-11-14T00:00:00+00:00",
        "urls": {},
        "pages": {
            "play_by_play": pbp_html,
            "box_score": box_html,
            "individual_stats": stats_html,
        },
    }


def test_all_fixtures_produce_six_family_keys() -> None:
    for contest_id in CONTEST_IDS:
        bundle = _fixture_bundle(contest_id)
        parsed = parse_bundle(bundle, league="wbb")
        assert parsed["contest_id"] == contest_id
        assert isinstance(parsed["contest_id"], str)
        assert set(parsed.keys()) == {"contest_id", "teams", *FAMILY_KEYS}
        for key in FAMILY_KEYS:
            assert isinstance(parsed[key], list), f"{contest_id}/{key} not a list"


def test_known_good_game_has_populated_families() -> None:
    bundle = _fixture_bundle(KNOWN_GOOD_GAME)
    parsed = parse_bundle(bundle, league="wbb")
    for key in ("pbp", "lineups", "player_box", "shots", "possessions"):
        assert len(parsed[key]) > 0, f"{KNOWN_GOOD_GAME}/{key} unexpectedly empty"


def test_write_parsed_round_trips_valid_json() -> None:
    bundle = _fixture_bundle(KNOWN_GOOD_GAME)
    parsed = parse_bundle(bundle, league="wbb")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = write_parsed(root, "wbb", KNOWN_GOOD_GAME, parsed)
        assert path == root / "wbb" / "json" / f"{KNOWN_GOOD_GAME}.json"
        assert path.exists()
        # plain utf-8 JSON, not gzip
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        assert reloaded["contest_id"] == KNOWN_GOOD_GAME
        assert set(reloaded.keys()) == {"contest_id", "teams", *FAMILY_KEYS}


def test_parse_and_write_convenience() -> None:
    bundle = _fixture_bundle(KNOWN_GOOD_GAME)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = parse_and_write(bundle, root)  # league defaults to "wbb"
        assert path == root / "wbb" / "json" / f"{KNOWN_GOOD_GAME}.json"
        assert path.exists()
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        assert reloaded["contest_id"] == KNOWN_GOOD_GAME


def test_bundle_written_then_read_still_parses() -> None:
    """Exercise the real write_bundle/read_bundle round trip, not just an in-memory dict."""
    raw = _fixture_bundle(KNOWN_GOOD_GAME)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_bundle(
            root,
            "wbb",
            raw["season"],
            raw["contest_id"],
            pages=raw["pages"],
            urls=raw["urls"],
            captured_at=raw["captured_at"],
        )
        from ncaa_bundle import bundle_path

        bundle = read_bundle(bundle_path(root, "wbb", raw["season"], raw["contest_id"]))
        parsed = parse_bundle(bundle, league="wbb")
        assert len(parsed["pbp"]) > 0


def test_corrupt_pbp_page_yields_empty_pbp_without_raising() -> None:
    bundle = _fixture_bundle(KNOWN_GOOD_GAME)
    bundle["pages"]["play_by_play"] = ""  # deliberately corrupt
    parsed = parse_bundle(bundle, league="wbb")  # must not raise
    assert parsed["pbp"] == []
    # every downstream family that depends on pbp is also empty, but the call
    # still returns cleanly with all 6 keys present as lists.
    for key in FAMILY_KEYS:
        assert isinstance(parsed[key], list)


def test_wbb_quarter_period_model_changes_period_length() -> None:
    """WBB delta #1: the 4-quarter period model.

    The parser derives the TOTAL period count from the line-score table
    structure, not from ``period_model``'s ``n_reg`` -- so ``max(period)``
    is identical whether the same WBB page is parsed with ``league="wbb"``
    or ``league="mbb"`` (verified: NOT a real discriminator by itself).
    What ``league="wbb"`` actually changes is the per-period clock length
    (``_WBB_PERIOD_MODEL = (4, 600, 300)`` vs MBB's ``(2, 1200, 300)``): a
    WBB quarter ends at ~600 ``game_seconds``, while the SAME page parsed
    under the MBB half model treats periods 1-2 as 20-minute halves
    (~1200s) and mis-labels periods 3+ as overtime. Both the wbb value and
    the mbb-flip value are asserted here so the discriminator is proven,
    not assumed.
    """
    bundle = _fixture_bundle(KNOWN_GOOD_GAME)  # 5722355: regulation, no OT
    parsed_wbb = parse_bundle(bundle)  # relies on parse_bundle's league="wbb" default
    parsed_mbb = parse_bundle(bundle, league="mbb")

    periods_wbb = [r["period"] for r in parsed_wbb["pbp"]]
    assert max(periods_wbb) == 4, "regulation WBB game should reach period 4 (quarters)"

    p1_end_wbb = max(r["game_seconds"] for r in parsed_wbb["pbp"] if r["period"] == 1)
    p1_end_mbb = max(r["game_seconds"] for r in parsed_mbb["pbp"] if r["period"] == 1)

    # a WBB quarter is 600s; under the wbb model, period 1 ends near there.
    assert 540 <= p1_end_wbb <= 600, f"wbb period-1 end game_seconds out of quarter range: {p1_end_wbb}"
    # flip proof: the SAME page under the mbb half model (1200s/half) ends
    # period 1 near 1200 instead. If this also landed near 600 the delta
    # assertion above would prove nothing.
    assert p1_end_mbb > 1100, f"mbb-flip period-1 end game_seconds not near a half boundary: {p1_end_mbb}"


def test_wbb_2ot_game_exceeds_regulation_period_count() -> None:
    """5733807 (2 OT) reaches period 6 (4 quarters + 2 OT periods).

    Informational OT-detection sanity check -- the per-period COUNT is
    driven by the HTML table structure and is the same under either
    league (see test_wbb_quarter_period_model_changes_period_length for
    the real per-league discriminator, which is period LENGTH).
    """
    bundle = _fixture_bundle("5733807")
    parsed = parse_bundle(bundle, league="wbb")
    periods = [r["period"] for r in parsed["pbp"]]
    assert max(periods) == 6, f"2OT game should reach period 6 (4 quarters + 2 OT), got {max(periods)}"


def test_wbb_shots_league_label_changes_arc_classification() -> None:
    """WBB delta #2: the shots frame's league label.

    ``parse_bundle``'s shots family threads ``league="womens"`` into
    ``shot_events_to_frame``'s three-point-arc classification. There is no
    literal ``"league"`` column on the canonical shot schema -- the label
    surfaces as the geometry classification itself, via
    ``mbb_shot_quality_constants.LEAGUE_CONSTANTS[league].arc_radius_by_season``.

    The real fixture games are all season 2024-25, by which point BOTH the
    men's (moved 2019-20) and women's (moved 2021-22) three-point arcs have
    already settled at 22.15ft -- so for that season the two leagues
    classify identically and no assertion on the natural fixture bundle
    would be a real discriminator. Season 2020-21 is the last season the
    two arcs differed (women's still 20.75ft, men's already 22.15ft), so
    this test re-stamps the SAME real captured game with that season to
    force a genuine, non-fabricated divergence and asserts it both ways.
    """
    bundle = _fixture_bundle("5732292")
    bundle["season"] = "2020-21"  # deliberately chosen: see docstring
    parsed_wbb = parse_bundle(bundle, league="wbb")
    parsed_mbb = parse_bundle(bundle, league="mbb")
    assert len(parsed_wbb["shots"]) == len(parsed_mbb["shots"])

    # find a shot in the band where the two arcs disagree (womens 20.75ft < dist < mens 22.15ft)
    band_idx = [i for i, s in enumerate(parsed_wbb["shots"]) if 20.75 < s["dist_ft"] < 22.15]
    assert band_idx, "fixture drifted: no shot left in the mens/womens arc-disagreement band"
    i = band_idx[0]

    shot_wbb = parsed_wbb["shots"][i]
    shot_mbb = parsed_mbb["shots"][i]
    assert shot_wbb["point_value"] == 3, f"womens arc should classify this shot as a 3: {shot_wbb}"
    assert shot_wbb["shot_zone"] in ("abovebreak3", "corner3"), shot_wbb

    # flip proof: the SAME shot under the mens arc classifies as a 2. If it
    # also classified as a 3 the wbb assertions above would prove nothing.
    assert shot_mbb["point_value"] == 2, f"mens arc should classify this shot as a 2: {shot_mbb}"
    assert shot_mbb["shot_zone"] != "abovebreak3", shot_mbb


def test_every_family_row_carries_contest_id_and_no_game_id() -> None:
    """One per-game identifier, named `contest_id`, on every row of every family."""
    for contest_id in CONTEST_IDS:
        parsed = parse_bundle(_fixture_bundle(contest_id), league="wbb")
        for family in FAMILY_KEYS:
            for row in parsed[family]:
                assert "game_id" not in row, f"{contest_id}/{family} still has game_id"
                assert row["contest_id"] == contest_id, f"{contest_id}/{family} mismatch"
                assert isinstance(row["contest_id"], str), f"{contest_id}/{family} not Utf8"


def test_shots_contest_id_is_populated_and_agrees_with_the_other_families() -> None:
    """Regression: the shots adapter hardcodes `game_id=None`, so shots used to be
    the one family you could not join to the rest without enrichment."""
    parsed = parse_bundle(_fixture_bundle(KNOWN_GOOD_GAME), league="wbb")
    assert len(parsed["shots"]) > 0
    shot_ids = {r["contest_id"] for r in parsed["shots"]}
    pbp_ids = {r["contest_id"] for r in parsed["pbp"]}
    assert shot_ids == pbp_ids == {KNOWN_GOOD_GAME}
    assert None not in shot_ids


def test_contest_id_is_never_a_float_stringification() -> None:
    """`"5722355.0"` is the classic join-breaking defect; the value is the bundle's own str."""
    parsed = parse_bundle(_fixture_bundle(KNOWN_GOOD_GAME), league="wbb")
    for family in FAMILY_KEYS:
        for row in parsed[family]:
            assert "." not in row["contest_id"]


def test_espn_game_id_present_on_every_family_even_without_a_crosswalk() -> None:
    """The column never varies game-to-game: unbuilt crosswalk means null, not absent."""
    parsed = parse_bundle(_fixture_bundle(KNOWN_GOOD_GAME), league="wbb")
    for family in FAMILY_KEYS:
        for row in parsed[family]:
            assert "espn_game_id" in row, f"{family} is missing the espn_game_id column"


def main() -> None:
    test_every_family_row_carries_contest_id_and_no_game_id()
    test_shots_contest_id_is_populated_and_agrees_with_the_other_families()
    test_contest_id_is_never_a_float_stringification()
    test_espn_game_id_present_on_every_family_even_without_a_crosswalk()
    test_all_fixtures_produce_six_family_keys()
    test_known_good_game_has_populated_families()
    test_write_parsed_round_trips_valid_json()
    test_parse_and_write_convenience()
    test_bundle_written_then_read_still_parses()
    test_corrupt_pbp_page_yields_empty_pbp_without_raising()
    test_wbb_quarter_period_model_changes_period_length()
    test_wbb_2ot_game_exceeds_regulation_period_count()
    test_wbb_shots_league_label_changes_arc_classification()
    print("OK")


if __name__ == "__main__":
    main()
