"""Offline tests for ncaa_identity (ids + readable names onto parsed families).

No network. The end-to-end cases run REAL captured stats.ncaa.org game HTML
(the committed bigballR fixtures) through the real parser stack, against a
rosters/teams tree built in a tmp dir from that same game's real names.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ncaa_identity import (
    _key_name,
    _utf8_id,
    enrich_parsed,
    load_roster_index,
    load_team_index,
)
from ncaa_parse import parse_bundle

_SDV_PY_ROOT = Path(__file__).resolve().parents[3] / "sdv-py"
_FIX = _SDV_PY_ROOT / "tests" / "fixtures" / "ncaa" / "bigballr" / "html"

LEAGUE = "wbb"
SEASON = 2025
#: A real captured game with all six families populated (see test_ncaa_parse).
KNOWN_GOOD_GAME = "5722355"


def _fixture_bundle(contest_id: str = KNOWN_GOOD_GAME) -> dict:
    return {
        "contest_id": contest_id,
        "league": LEAGUE,
        "season": "2024-25",
        "captured_at": "2024-11-14T00:00:00+00:00",
        "urls": {},
        "pages": {
            "play_by_play": (_FIX / f"pbp_{contest_id}.html").read_text(
                encoding="utf-8"
            ),
            "box_score": (_FIX / f"box_{contest_id}.html").read_text(encoding="utf-8"),
            "individual_stats": (
                _FIX / f"individual_stats_{contest_id}.html"
            ).read_text(encoding="utf-8"),
        },
    }


def _write_tree(
    root: Path,
    teams: list[dict],
    rosters: dict[str, list[dict]],
    *,
    season: int = SEASON,
) -> None:
    """Materialize a {lg}/teams/json + {lg}/rosters/json tree, as ncaa_datasets writes it."""
    teams_path = root / LEAGUE / "teams" / "json" / f"{season}.json"
    teams_path.parent.mkdir(parents=True, exist_ok=True)
    teams_path.write_text(json.dumps(teams), encoding="utf-8")
    roster_dir = root / LEAGUE / "rosters" / "json" / str(season)
    roster_dir.mkdir(parents=True, exist_ok=True)
    for team_id, rows in rosters.items():
        (roster_dir / f"{team_id}.json").write_text(json.dumps(rows), encoding="utf-8")


def _team_row(ncaa_team_id: str, team: str, espn_team_id: str | None = "42") -> dict:
    return {
        "season": str(SEASON),
        "season_ncaa": "2024-25",
        "league": LEAGUE,
        "ncaa_team_id": ncaa_team_id,
        "team": team,
        "conference": "Test",
        "division": "I",
        "espn_team_id": espn_team_id,
        "espn_display_name": f"{team} Testers",
        "espn_mascot": "Testers",
    }


def _display_name(pbp_name: str) -> str:
    """``"ANGELO.BRIZZI"`` -> ``"Brizzi, Angelo"`` -- the roster's display form.

    The point of the end-to-end case: the roster stores ONLY this spelling, so
    the join has to bridge word order AND case with no help.
    """
    first, _, last = pbp_name.partition(".")
    return f"{last.title()}, {first.title()}" if last else first.title()


def _roster_row(
    team_id: str, team: str, player_id: str, clean_name: str, player: str
) -> dict:
    return {
        "season": str(SEASON),
        "league": LEAGUE,
        "team_id": team_id,
        "team": team,
        "player_id": player_id,
        "clean_name": clean_name,
        "player": player,
    }


# --- the format-immune name key --------------------------------------------


def test_key_name_matches_last_first_against_all_caps_first_last() -> None:
    """The whole point: the two sources spell the same player differently."""
    assert _key_name("Talton Jr, Derrick") == _key_name("DERRICK.TALTON")
    assert _key_name("B.J. Edwards") == _key_name("BJ.EDWARDS")
    assert _key_name("Brizzi, Angelo") == _key_name("ANGELO.BRIZZI")
    assert _key_name("Angelo Brizzi") == _key_name("ANGELO.BRIZZI")


def test_key_name_survives_diacritics_hyphens_and_apostrophes() -> None:
    assert _key_name("Bogdanović, Bojan") == _key_name("BOJAN.BOGDANOVIC")
    assert _key_name("Porter-Brown, Cam") == _key_name("CAM.PORTERBROWN")
    assert _key_name("Je'Kel Foster") == _key_name("JEKEL.FOSTER")


def test_key_name_does_not_collapse_different_players() -> None:
    assert _key_name("Smith, John") != _key_name("Smith, Jane")


# --- ID dtype discipline ----------------------------------------------------


def test_utf8_id_never_stringifies_a_float_as_dot_zero() -> None:
    """``str(123.0)`` == ``"123.0"`` is the classic id-join defect."""
    assert _utf8_id(123.0) == "123"
    assert _utf8_id(123) == "123"
    assert _utf8_id("123") == "123"
    assert _utf8_id(None) is None
    assert _utf8_id("") is None
    # A non-integral float is not an id; a plausible-looking wrong string is worse
    # than a null.
    assert _utf8_id(123.5) is None
    assert _utf8_id(True) is None


def test_loaded_ids_are_utf8() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tree(
            root,
            [_team_row("609642", "Buffalo")],
            # int player_id on the source row must still surface as Utf8
            {
                "609642": [
                    _roster_row("609642", "Buffalo", 999, "Oboh, Tim", "TIM.OBOH")
                ]
            },
        )
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        teams = load_team_index(str(root), LEAGUE, SEASON)
        rosters = load_roster_index(str(root), LEAGUE, SEASON)
        assert isinstance(teams["Buffalo"]["ncaa_team_id"], str)
        assert isinstance(teams["Buffalo"]["espn_team_id"], str)
        player_id = rosters["609642"]["names"][_key_name("TIM.OBOH")][0]
        assert isinstance(player_id, str)
        assert player_id == "999"


# --- enrichment is additive -------------------------------------------------


def test_enrichment_is_additive_and_stamps_ids_on_every_family() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tree(
            root,
            [
                _team_row("1", "Buffalo"),
                _team_row("2", "Southern Miss.", espn_team_id="7"),
            ],
            {
                "1": [_roster_row("1", "Buffalo", "11", "Oboh, Tim", "TIM.OBOH")],
                "2": [
                    _roster_row(
                        "2", "Southern Miss.", "22", "Carruth, Brewer", "BREWER.CARRUTH"
                    )
                ],
            },
        )
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        parsed = {
            "contest_id": "1",
            "pbp": [
                {
                    "home": "Buffalo",
                    "away": "Southern Miss.",
                    "event_team": "Buffalo",
                    "poss_team": "Buffalo",
                    "player_1": "TIM.OBOH",
                    "player_2": "BREWER.CARRUTH",
                    "event_type": "made2",
                }
            ],
            "player_box": [
                {
                    "home": "Buffalo",
                    "away": "Southern Miss.",
                    "team": "Buffalo",
                    "player": "TIM.OBOH",
                    "pts": 7,
                }
            ],
            "team_box": [
                {
                    "home": "Buffalo",
                    "away": "Southern Miss.",
                    "team": "Buffalo",
                    "pts": 70,
                }
            ],
            "shots": [{"team_id": "Buffalo", "shooter_id": "TiOboh", "made": True}],
            "possessions": [
                {
                    "home": "Buffalo",
                    "away": "Southern Miss.",
                    "poss_team": "Buffalo",
                    "pts": 2,
                }
            ],
            "lineups": [
                {
                    "team": {"team": {"name": "Buffalo"}, "year": {"value": 2024}},
                    "opponent": {
                        "team": {"name": "Southern Miss."},
                        "year": {"value": 2024},
                    },
                    "players": [
                        {"code": "TiOboh", "id": {"name": "Oboh, Tim"}, "ncaa_id": None}
                    ],
                    "players_in": [],
                    "players_out": [],
                }
            ],
        }
        before = {
            fam: [dict(r) for r in rows]
            for fam, rows in parsed.items()
            if fam != "contest_id"
        }

        enrich_parsed(parsed, root=root, league=LEAGUE, season=SEASON)

        # ADDITIVE: every original key survives with its original value.
        for fam, rows in before.items():
            for original, enriched in zip(rows, parsed[fam]):
                for key, value in original.items():
                    if fam == "lineups" and key == "players":
                        continue  # ncaa_id is filled in place (documented)
                    assert enriched[key] == value, f"{fam}.{key} was rewritten"

        pbp = parsed["pbp"][0]
        assert pbp["player_1_id"] == "11"
        assert pbp["player_1_clean_name"] == "Oboh, Tim"
        assert pbp["player_2_id"] == "22"
        assert pbp["home_ncaa_team_id"] == "1"
        assert pbp["home_espn_team_id"] == "42"
        assert pbp["away_ncaa_team_id"] == "2"
        assert pbp["away_espn_team_id"] == "7"
        assert pbp["event_team_ncaa_team_id"] == "1"
        assert pbp["poss_team_ncaa_team_id"] == "1"

        box = parsed["player_box"][0]
        assert (box["player_id"], box["clean_name"]) == ("11", "Oboh, Tim")
        assert box["team_ncaa_team_id"] == "1"
        assert parsed["team_box"][0]["team_ncaa_team_id"] == "1"
        assert parsed["possessions"][0]["poss_team_ncaa_team_id"] == "1"

        shot = parsed["shots"][0]
        # `team_id` holds a NAME; the real ids land beside it, it is not rewritten.
        assert shot["team_id"] == "Buffalo"
        assert (shot["ncaa_team_id"], shot["espn_team_id"]) == ("1", "42")
        assert (shot["shooter_player_id"], shot["shooter_clean_name"]) == (
            "11",
            "Oboh, Tim",
        )

        lineup = parsed["lineups"][0]
        assert lineup["team_ncaa_team_id"] == "1"
        assert lineup["opponent_ncaa_team_id"] == "2"
        assert lineup["players"][0]["ncaa_id"] == "11"

        assert [t["side"] for t in parsed["teams"]] == ["home", "away"]
        assert parsed["teams"][0]["espn_display_name"] == "Buffalo Testers"


# --- never drop, never guess ------------------------------------------------


def test_unmatched_rows_survive_with_null_ids() -> None:
    """A non-D-I opponent / a player off the roster snapshot keeps its row."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tree(root, [_team_row("1", "Buffalo")], {"1": []})
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        parsed = {
            "contest_id": "1",
            "pbp": [
                {
                    "home": "Buffalo",
                    "away": "Ozark Christian",  # non-D-I: no crosswalk row
                    "event_team": "Ozark Christian",
                    "poss_team": "Ozark Christian",
                    "player_1": "NOBODY.ATALL",
                    "player_2": None,
                }
            ],
            "player_box": [],
            "team_box": [],
            "shots": [],
            "possessions": [],
            "lineups": [],
        }
        enrich_parsed(parsed, root=root, league=LEAGUE, season=SEASON)
        row = parsed["pbp"][0]
        assert len(parsed["pbp"]) == 1  # not dropped
        assert row["away"] == "Ozark Christian"  # not rewritten
        assert row["away_ncaa_team_id"] is None
        assert row["away_espn_team_id"] is None
        assert row["player_1"] == "NOBODY.ATALL"
        assert row["player_1_id"] is None
        assert row["player_1_clean_name"] is None
        # ...and the side that DOES match still resolves.
        assert row["home_ncaa_team_id"] == "1"


def test_team_pseudo_player_never_resolves_to_a_person() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tree(
            root,
            [_team_row("1", "Buffalo")],
            {"1": [_roster_row("1", "Buffalo", "11", "Oboh, Tim", "TIM.OBOH")]},
        )
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        parsed = {
            "contest_id": "1",
            "pbp": [{"home": "Buffalo", "away": "Buffalo", "player_1": "TEAM"}],
            "player_box": [],
            "team_box": [],
            "shots": [],
            "possessions": [],
            "lineups": [],
        }
        enrich_parsed(parsed, root=root, league=LEAGUE, season=SEASON)
        assert parsed["pbp"][0]["player_1"] == "TEAM"
        assert parsed["pbp"][0]["player_1_id"] is None


def test_ambiguous_name_key_yields_null_not_a_coin_flip() -> None:
    """Two players whose names share a signature must BOTH stay unresolved."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tree(
            root,
            [_team_row("1", "Buffalo")],
            {
                "1": [
                    _roster_row("1", "Buffalo", "11", "Smith, Ron", "RON.SMITH"),
                    # exact anagram of the above -> same sorted-letter signature
                    _roster_row("1", "Buffalo", "12", "Smith, Nor", "NOR.SMITH"),
                ]
            },
        )
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        parsed = {
            "contest_id": "1",
            "pbp": [{"home": "Buffalo", "away": "Buffalo", "player_1": "RON.SMITH"}],
            "player_box": [],
            "team_box": [],
            "shots": [],
            "possessions": [],
            "lineups": [],
        }
        enrich_parsed(parsed, root=root, league=LEAGUE, season=SEASON)
        assert parsed["pbp"][0]["player_1_id"] is None


# --- degrade gracefully -----------------------------------------------------


def test_missing_rosters_and_teams_tree_degrades_to_nulls_without_raising() -> None:
    """Most historical seasons have no rosters captured -- they must still parse."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)  # deliberately empty: no teams/, no rosters/
        load_team_index.cache_clear()
        load_roster_index.cache_clear()
        parsed = {
            "contest_id": "1",
            "pbp": [
                {"home": "Buffalo", "away": "Southern Miss.", "player_1": "TIM.OBOH"}
            ],
            "player_box": [
                {
                    "home": "Buffalo",
                    "away": "Buffalo",
                    "team": "Buffalo",
                    "player": "TIM.OBOH",
                }
            ],
            "team_box": [],
            "shots": [{"team_id": "Buffalo", "shooter_id": "TiOboh"}],
            "possessions": [],
            "lineups": [],
        }
        enrich_parsed(parsed, root=root, league=LEAGUE, season=1998)  # must not raise

        row = parsed["pbp"][0]
        # The columns EXIST (stable schema) and are typed nulls.
        for column in (
            "player_1_id",
            "player_1_clean_name",
            "home_ncaa_team_id",
            "home_espn_team_id",
        ):
            assert column in row, f"{column} missing on the degraded path"
            assert row[column] is None
        assert parsed["player_box"][0]["player_id"] is None
        assert parsed["shots"][0]["shooter_player_id"] is None
        assert parsed["shots"][0]["ncaa_team_id"] is None
        # Original data untouched.
        assert row["player_1"] == "TIM.OBOH"


def test_unknown_season_degrades_to_nulls() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parsed = {
            "contest_id": "1",
            "pbp": [{"home": "Buffalo", "away": "Buffalo", "player_1": "TIM.OBOH"}],
            "player_box": [],
            "team_box": [],
            "shots": [],
            "possessions": [],
            "lineups": [],
        }
        enrich_parsed(parsed, root=Path(tmp), league=LEAGUE, season=None)
        assert parsed["pbp"][0]["player_1_id"] is None
        assert parsed["pbp"][0]["home_ncaa_team_id"] is None


# --- end-to-end over a REAL captured game -----------------------------------


def test_real_captured_game_resolves_ids_end_to_end() -> None:
    """Real stats.ncaa.org HTML -> parser stack -> a roster tree of its own names."""
    baseline = parse_bundle(
        _fixture_bundle(), league=LEAGUE, root=Path(tempfile.gettempdir()) / "nope"
    )
    home = baseline["pbp"][0]["home"]
    away = baseline["pbp"][0]["away"]
    # Real player names, as the box aggregation emits them (ALL-CAPS FIRST.LAST).
    by_team: dict[str, list[str]] = {}
    for row in baseline["player_box"]:
        by_team.setdefault(row["team"], []).append(row["player"])
    assert home in by_team and away in by_team

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        team_ids = {home: "100", away: "200"}
        rosters = {}
        for team, players in by_team.items():
            team_id = team_ids[team]
            rosters[team_id] = [
                # Store ONLY the "Last, First" display form -- the join must
                # bridge to the ALL-CAPS pbp spelling with no help.
                _roster_row(
                    team_id,
                    team,
                    str(9000 + i),
                    _display_name(name),
                    "",
                )
                for i, name in enumerate(players)
            ]
        _write_tree(root, [_team_row(tid, t) for t, tid in team_ids.items()], rosters)
        load_team_index.cache_clear()
        load_roster_index.cache_clear()

        parsed = parse_bundle(_fixture_bundle(), league=LEAGUE, root=root)

        assert parsed["teams"][0]["ncaa_team_id"] == "100"
        assert all(r["home_ncaa_team_id"] == "100" for r in parsed["pbp"])

        resolved = sum(1 for r in parsed["player_box"] if r["player_id"] is not None)
        assert resolved == len(parsed["player_box"]), (
            f"player_box ids: {resolved}/{len(parsed['player_box'])} resolved"
        )
        assert all(
            r["clean_name"] and not r["clean_name"].isupper()
            for r in parsed["player_box"]
        )

        named = [
            r for r in parsed["pbp"] if r.get("player_1") and r["player_1"] != "TEAM"
        ]
        hit = sum(1 for r in named if r["player_1_id"] is not None)
        assert hit / len(named) > 0.95, f"pbp player_1 join rate {hit}/{len(named)}"


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("OK")


if __name__ == "__main__":
    main()
