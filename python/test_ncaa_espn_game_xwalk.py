"""Offline tests for the contest_id -> espn_game_id crosswalk. No network.

The ESPN side is injected as a frame, so every tier is exercised without a
release-loader call. `build_season_xwalk` itself is driven through its two
halves (`ncaa_schedule_side` off a written parquet, `espn_schedule_side`
monkeypatched) rather than mocked wholesale.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import polars as pl

import ncaa_espn_game_xwalk as xw
from ncaa_espn_game_xwalk import (
    build_season_xwalk,
    load_espn_game_index,
    ncaa_schedule_side,
    write_season_xwalk,
    xwalk_path,
)

SEASON = 2024
LEAGUE = "wbb"


def _ncaa_row(
    contest_id: str,
    game_date: str,
    team: str,
    espn_team_id: str | None,
    opponent: str,
    opponent_espn_team_id: str | None,
    *,
    home: str,
    away: str,
) -> dict:
    return {
        "season": str(SEASON),
        "league": LEAGUE,
        "team_id": "1",
        "team": team,
        "espn_team_id": espn_team_id,
        "contest_id": contest_id,
        "game_date": game_date,
        "home": home,
        "home_score": 70,
        "away": away,
        "away_score": 60,
        "opponent": opponent,
        "opponent_id": "2",
        "opponent_espn_team_id": opponent_espn_team_id,
        "is_neutral": False,
        "detail": None,
        "attendance": 100,
    }


def _write_schedules(root: Path, rows: list[dict]) -> None:
    path = Path(root) / LEAGUE / "schedules" / "parquet" / f"{SEASON}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)


def _espn(rows: list[tuple[str, date, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "espn_game_id": pl.Utf8,
            "game_date": pl.Date,
            "home_espn_team_id": pl.Utf8,
            "away_espn_team_id": pl.Utf8,
        },
        orient="row",
    )


def _build(root: Path, espn: pl.DataFrame, monkeypatch) -> pl.DataFrame:
    monkeypatch.setattr(xw, "espn_schedule_side", lambda league, season: espn)
    return build_season_xwalk(root, LEAGUE, SEASON)


# --- the four tiers ---------------------------------------------------------


def test_exact_tier_matches_on_date_and_both_team_ids(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [_ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC")],
        )
        out = _build(root, _espn([("401", date(2023, 11, 6), "150", "153")]), monkeypatch)
        assert out.to_dicts() == [{"contest_id": "C1", "espn_game_id": "401", "match_method": "exact"}]


def test_date_window_tier_absorbs_a_one_day_offset(monkeypatch) -> None:
    """A late tip-off can land the two sources on neighbouring dates."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [_ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC")],
        )
        out = _build(root, _espn([("401", date(2023, 11, 7), "150", "153")]), monkeypatch)
        assert out["espn_game_id"].to_list() == ["401"]
        assert out["match_method"].to_list() == ["date_window"]


def test_unordered_pair_tier_absorbs_a_neutral_site_inversion(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [_ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC")],
        )
        # ESPN has the same two teams that date, home/away flipped.
        out = _build(root, _espn([("401", date(2023, 11, 6), "153", "150")]), monkeypatch)
        assert out["espn_game_id"].to_list() == ["401"]
        assert out["match_method"].to_list() == ["unordered_pair"]


def test_single_team_tier_covers_a_non_di_opponent(monkeypatch) -> None:
    """stats.ncaa.org gives a non-D-I opponent no team id at all, so only one
    side of the pairing is known -- date + that one team must still resolve."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [
                _ncaa_row(
                    "C1",
                    "11/06/2023",
                    "Duke",
                    "150",
                    "Ozark Christian",
                    None,
                    home="Duke",
                    away="Ozark Christian",
                )
            ],
        )
        out = _build(root, _espn([("401", date(2023, 11, 6), "150", "99999")]), monkeypatch)
        assert out["espn_game_id"].to_list() == ["401"]
        assert out["match_method"].to_list() == ["single_team"]


# --- never guess, never drop ------------------------------------------------


def test_ambiguous_single_team_day_resolves_to_null(monkeypatch) -> None:
    """Two ESPN games for the same team on one date is not resolvable -- null, not a coin flip."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [
                _ncaa_row(
                    "C1",
                    "11/06/2023",
                    "Duke",
                    "150",
                    "Ozark Christian",
                    None,
                    home="Duke",
                    away="Ozark Christian",
                )
            ],
        )
        espn = _espn(
            [
                ("401", date(2023, 11, 6), "150", "99999"),
                ("402", date(2023, 11, 6), "88888", "150"),
            ]
        )
        out = _build(root, espn, monkeypatch)
        assert out["espn_game_id"].to_list() == [None]
        assert out["match_method"].to_list() == [None]


def test_unmatched_contest_keeps_its_row_with_a_null_id(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [_ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC")],
        )
        out = _build(root, _espn([("401", date(2024, 2, 2), "1", "2")]), monkeypatch)
        assert out.height == 1
        assert out["contest_id"].to_list() == ["C1"]
        assert out["espn_game_id"].to_list() == [None]


def test_one_espn_game_claimed_by_two_contests_is_voided_on_both(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [
                _ncaa_row("C1", "11/06/2023", "Duke", "150", "X", None, home="Duke", away="X"),
                _ncaa_row("C2", "11/06/2023", "Duke", "150", "Y", None, home="Duke", away="Y"),
            ],
        )
        out = _build(root, _espn([("401", date(2023, 11, 6), "150", "99999")]), monkeypatch)
        assert out["espn_game_id"].to_list() == [None, None]


def test_missing_schedules_parquet_yields_an_empty_crosswalk(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = build_season_xwalk(Path(tmp), LEAGUE, SEASON)
        assert out.height == 0
        assert out.columns == ["contest_id", "espn_game_id", "match_method"]


# --- dtype discipline + the offline read ------------------------------------


def test_ids_are_utf8_and_never_float_stringified(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [_ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC")],
        )
        out = _build(root, _espn([("401638645", date(2023, 11, 6), "150", "153")]), monkeypatch)
        assert out.schema["contest_id"] == pl.Utf8
        assert out.schema["espn_game_id"] == pl.Utf8
        assert out["espn_game_id"].to_list() == ["401638645"]
        assert "." not in out["espn_game_id"][0]


def test_write_then_load_round_trips_an_offline_index(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [
                _ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC"),
                _ncaa_row("C2", "11/07/2023", "Duke", "150", "Zzz", None, home="Duke", away="Zzz"),
            ],
        )
        out = _build(root, _espn([("401", date(2023, 11, 6), "150", "153")]), monkeypatch)
        path = write_season_xwalk(root, LEAGUE, SEASON, out)
        assert path == xwalk_path(root, LEAGUE, SEASON)

        load_espn_game_index.cache_clear()
        index = load_espn_game_index(str(root), LEAGUE, SEASON)
        assert index == {"C1": "401"}  # the unmatched C2 is simply absent


def test_missing_crosswalk_file_loads_as_an_empty_index() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        load_espn_game_index.cache_clear()
        assert load_espn_game_index(tmp, LEAGUE, SEASON) == {}


def test_schedule_side_prefers_the_row_that_resolved_both_teams() -> None:
    """A contest appears on both teams' pages; the fuller copy wins the dedup."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_schedules(
            root,
            [
                # This copy's own team matches neither home nor away -> both null.
                _ncaa_row("C1", "11/06/2023", "Elsewhere", "999", "UNC", "153", home="Duke", away="UNC"),
                _ncaa_row("C1", "11/06/2023", "Duke", "150", "UNC", "153", home="Duke", away="UNC"),
            ],
        )
        side = ncaa_schedule_side(root, LEAGUE, SEASON)
        assert side.height == 1
        assert side["home_espn_team_id"].to_list() == ["150"]
        assert side["away_espn_team_id"].to_list() == ["153"]
