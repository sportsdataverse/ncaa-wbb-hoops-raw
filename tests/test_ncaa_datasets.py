"""Offline tests for the schedules / teams / rosters dataset trees (no network).

Driven entirely by the committed sdv-py HTML fixtures -- these assert the
persist/enrich/compile contract, and every "fetch" is a lambda over a fixture
string, so nothing here touches stats.ncaa.org.

Layout under test: **html + json are per team; parquet is the ONE compiled
season dataset** (plus ``teams``, one file per season in all three formats).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import ncaa_datasets as nd
import polars as pl
import pytest
from ncaa_datasets import (
    ROSTER_COLUMNS,
    SCHEDULE_COLUMNS,
    TEAMS_COLUMNS,
    build_season_aggregate,
    build_teams,
    dataset_path,
    persist_roster,
    persist_schedule,
    read_html,
    rebuild_missing,
    season_ncaa,
    season_teams,
)
from ncaa_discover import discover_season
from ncaa_rosters import capture_rosters

# Sibling checkout: .../sdv-dev/{wehoop-dev/ncaa-wbb-hoops-raw, sdv-py}.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ncaa" / "bigballr" / "html"
# South Carolina WBB, season 2025 (ending-year) -> crosswalk "2024-25".
_TEAM_ID = 592003
_SEASON = 2025
_LEAGUE = "wbb"
_SCHEDULE_HTML = (_FIXTURES / f"team_{_TEAM_ID}.html").read_text(encoding="utf-8")
_ROSTER_HTML = (_FIXTURES / f"roster_{_TEAM_ID}.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_team_identity():
    """``_teams_with_espn`` is lru_cached; monkeypatched crosswalks must not leak."""
    nd._teams_with_espn.cache_clear()
    yield
    nd._teams_with_espn.cache_clear()


@pytest.fixture()
def root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _season_parquet(root: Path, kind: str) -> pl.DataFrame:
    """Compile then read the season parquet -- the only parquet in the tree."""
    build_season_aggregate(kind, _SEASON, league=_LEAGUE, root=root)
    return pl.read_parquet(dataset_path(root, _LEAGUE, kind, "parquet", _SEASON))


# --------------------------------------------------------------------------
# paths + layout
# --------------------------------------------------------------------------


def test_dataset_path_layout(root: Path) -> None:
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731
    # html + json are per team...
    assert rel(dataset_path(root, "wbb", "schedules", "html", 2026, 1)) == "wbb/schedules/html/2026/1.html"
    assert rel(dataset_path(root, "wbb", "rosters", "json", 2026, 1)) == "wbb/rosters/json/2026/1.json"
    # ...parquet is ONE compiled file per season, one directory level up.
    assert rel(dataset_path(root, "wbb", "schedules", "parquet", 2026)) == "wbb/schedules/parquet/2026.parquet"
    assert rel(dataset_path(root, "wbb", "rosters", "parquet", 2026)) == "wbb/rosters/parquet/2026.parquet"
    # teams is one file per season in every format.
    for fmt in ("html", "json", "parquet"):
        assert rel(dataset_path(root, "wbb", "teams", fmt, 2026)) == f"wbb/teams/{fmt}/2026.{fmt}"


def test_dataset_path_ignores_team_id_for_the_season_parquet(root: Path) -> None:
    # A stray team_id must not resurrect a per-team parquet path.
    assert dataset_path(root, "wbb", "schedules", "parquet", 2026, 999) == dataset_path(
        root, "wbb", "schedules", "parquet", 2026
    )


def test_dataset_path_requires_team_id_for_per_team_artifacts(root: Path) -> None:
    with pytest.raises(ValueError, match="per-team"):
        dataset_path(root, "wbb", "rosters", "json", 2026)


def test_dataset_path_rejects_unknown_kind(root: Path) -> None:
    with pytest.raises(ValueError, match="unrecognized kind"):
        dataset_path(root, "wbb", "standings", "json", 2026, 1)


def test_season_ncaa() -> None:
    assert season_ncaa(2026) == "2025-26"
    assert season_ncaa(_SEASON) == "2024-25"


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------


def test_persist_schedule_writes_per_team_html_and_json_only(root: Path) -> None:
    df = persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)

    assert dataset_path(root, _LEAGUE, "schedules", "html", _SEASON, _TEAM_ID).is_file()
    assert dataset_path(root, _LEAGUE, "schedules", "json", _SEASON, _TEAM_ID).is_file()
    assert df.height > 0
    assert df.columns == SCHEDULE_COLUMNS
    # html round-trips byte-for-byte -> the tree can be re-parsed offline forever.
    assert read_html(root, _LEAGUE, "schedules", _SEASON, _TEAM_ID) == _SCHEDULE_HTML
    # No per-team parquet anywhere in the tree.
    assert list((root / _LEAGUE / "schedules").rglob("*.parquet")) == []


def test_schedule_json_is_a_records_array_matching_the_frame(root: Path) -> None:
    df = persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    payload = json.loads(dataset_path(root, _LEAGUE, "schedules", "json", _SEASON, _TEAM_ID).read_text("utf-8"))

    assert isinstance(payload, list) and len(payload) == df.height
    assert list(payload[0]) == SCHEDULE_COLUMNS
    assert payload[0]["team_id"] == str(_TEAM_ID)


def test_season_parquet_carries_ids_and_readable_names(root: Path) -> None:
    persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "schedules")

    # Never ids alone: every id column has a human-readable partner, so the
    # compiled season file is self-describing once teams are concatenated.
    for id_col, name_col in (("team_id", "team"), ("opponent_id", "opponent")):
        assert id_col in df.columns and name_col in df.columns
    assert df.get_column("team").unique().to_list() == ["South Carolina"]
    assert df.get_column("opponent").null_count() == 0
    assert df.get_column("opponent").str.len_chars().min() > 0


def test_schedule_carries_espn_ids_for_both_sides(root: Path) -> None:
    """ESPN identity is reference data -- it belongs HERE, not on the pbp rows."""
    persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "schedules")

    assert {"espn_team_id", "opponent_espn_team_id"} <= set(df.columns)
    assert df.schema["espn_team_id"] == pl.Utf8
    assert df.schema["opponent_espn_team_id"] == pl.Utf8
    # The swept team resolves for every row (South Carolina -> ESPN 2579).
    assert df.get_column("espn_team_id").unique().to_list() == ["2579"]
    # ...and at least one opponent does too. An unmatched opponent stays null,
    # never dropped and never guessed.
    matched = df.get_column("opponent_espn_team_id").drop_nulls()
    assert matched.len() > 0
    assert not any("." in v for v in matched.to_list())


def test_schedule_espn_ids_null_when_the_crosswalk_is_absent(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(nd._engine, "_espn_crosswalk", lambda league: None)
    nd._teams_with_espn.cache_clear()
    df = persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)

    assert df.height > 0  # rows survive
    assert df.get_column("espn_team_id").null_count() == df.height
    assert df.get_column("opponent_espn_team_id").null_count() == df.height
    assert df.get_column("opponent_id").null_count() < df.height  # NCAA ids unaffected


def test_schedule_ids_are_utf8_never_float_stringified(root: Path) -> None:
    persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "schedules")

    for col in ("contest_id", "team_id", "opponent_id"):
        assert df.schema[col] == pl.Utf8, col
        # A float-origin id stringifies as "592003.0" -- the classic silent join breaker.
        assert not any("." in v for v in df.get_column(col).drop_nulls().to_list()), col
    assert df.get_column("team_id").unique().to_list() == [str(_TEAM_ID)]
    # Typed non-id columns survive the json -> parquet compile.
    assert df.schema["home_score"] == pl.Int64
    assert df.schema["is_neutral"] == pl.Boolean
    assert df.schema["attendance"] == pl.Int64


# --------------------------------------------------------------------------
# rosters
# --------------------------------------------------------------------------


def test_persist_roster_writes_per_team_html_and_json_only(root: Path) -> None:
    df = persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)

    assert dataset_path(root, _LEAGUE, "rosters", "html", _SEASON, _TEAM_ID).is_file()
    assert dataset_path(root, _LEAGUE, "rosters", "json", _SEASON, _TEAM_ID).is_file()
    assert df.height > 0
    assert df.columns == ROSTER_COLUMNS
    assert list((root / _LEAGUE / "rosters").rglob("*.parquet")) == []


def test_roster_season_parquet_carries_player_id_display_name_and_pbp_key(root: Path) -> None:
    persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "rosters")

    assert {"player_id", "clean_name", "player", "team_id", "team"} <= set(df.columns)
    assert df.schema["player_id"] == pl.Utf8
    assert df.get_column("player_id").null_count() == 0
    assert df.get_column("team").unique().to_list() == ["South Carolina"]

    # clean_name is the properly-cased display form ("Raven Johnson"); player is
    # the ALL-CAPS FIRST.LAST key the play-by-play stream uses. BOTH must ship --
    # that pairing is the entire point of carrying the roster.
    row = df.row(0, named=True)
    assert row["clean_name"] != row["clean_name"].upper()
    assert row["player"] == row["player"].upper()
    assert "." in row["player"]
    assert row["player"] == row["clean_name"].upper().replace(" ", ".")


def test_roster_carries_the_teams_espn_id(root: Path) -> None:
    persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "rosters")

    assert df.schema["espn_team_id"] == pl.Utf8
    assert df.get_column("espn_team_id").unique().to_list() == ["2579"]  # South Carolina


def test_roster_ids_are_utf8_never_float_stringified(root: Path) -> None:
    persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    df = _season_parquet(root, "rosters")

    for col in ("player_id", "team_id"):
        assert df.schema[col] == pl.Utf8, col
        assert not any("." in v for v in df.get_column(col).drop_nulls().to_list()), col
    assert df.schema["ht_inches"] == pl.Int64


# --------------------------------------------------------------------------
# teams
# --------------------------------------------------------------------------


def test_season_teams_shape_and_utf8_id() -> None:
    df = season_teams(_LEAGUE, _SEASON)

    assert df.height > 300
    assert df.schema["ncaa_team_id"] == pl.Utf8
    assert not any("." in v for v in df.get_column("ncaa_team_id").to_list())
    assert str(_TEAM_ID) in df.get_column("ncaa_team_id").to_list()
    assert df.get_column("season_ncaa").unique().to_list() == [season_ncaa(_SEASON)]


def test_season_teams_rejects_unknown_league_and_empty_season() -> None:
    with pytest.raises(ValueError, match="unrecognized league"):
        season_teams("nba", _SEASON)
    with pytest.raises(ValueError, match="no .* crosswalk teams"):
        season_teams(_LEAGUE, 1800)


def test_build_teams_writes_all_three_formats_with_names_and_division(root: Path) -> None:
    df = build_teams(_SEASON, league=_LEAGUE, root=root)

    for fmt in ("html", "json", "parquet"):
        assert dataset_path(root, _LEAGUE, "teams", fmt, _SEASON).is_file(), fmt
    assert df.columns == TEAMS_COLUMNS
    # id + readable name + conference + division, never the id alone.
    assert df.get_column("team").null_count() == 0
    assert df.get_column("division").unique().to_list() == [nd.DIVISION]
    row = df.filter(pl.col("ncaa_team_id") == str(_TEAM_ID)).row(0, named=True)
    assert row["team"] == "South Carolina"
    assert row["conference"]

    written = pl.read_parquet(dataset_path(root, _LEAGUE, "teams", "parquet", _SEASON))
    assert written.schema["ncaa_team_id"] == pl.Utf8
    html = dataset_path(root, _LEAGUE, "teams", "html", _SEASON).read_text("utf-8")
    assert "<table>" in html and "South Carolina" in html


def test_build_teams_espn_columns_present_even_without_the_crosswalk(root: Path) -> None:
    # The sdv-py ncaa_espn_team_crosswalk loader may not be published yet; the
    # teams tree must degrade to typed nulls rather than blocking or dropping
    # the columns.
    df = build_teams(_SEASON, league=_LEAGUE, root=root)

    for col in ("espn_team_id", "espn_display_name", "espn_mascot"):
        assert col in df.columns, col
        assert df.schema[col] == pl.Utf8, col


def test_build_teams_null_fills_when_the_espn_loader_is_absent(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(nd._engine, "_espn_crosswalk", lambda league: None)
    df = build_teams(_SEASON, league=_LEAGUE, root=root)

    assert df.get_column("espn_team_id").null_count() == df.height


def test_build_teams_joins_a_present_espn_crosswalk(root: Path, monkeypatch) -> None:
    # Stand-in for the parallel sdv-py asset, at its agreed schema: Int64
    # ncaa_team_id + the NCAA "YYYY-YY" season form.
    fake = pl.DataFrame(
        {
            "season": [season_ncaa(_SEASON), "1999-00"],
            "ncaa_team_id": [_TEAM_ID, _TEAM_ID],
            "espn_team_id": ["2579", "WRONG-SEASON"],
            "espn_display_name": ["South Carolina Gamecocks", "nope"],
            "espn_mascot": ["Gamecocks", "nope"],
        },
        schema_overrides={"ncaa_team_id": pl.Int64},
    )
    monkeypatch.setattr(nd._engine, "_espn_crosswalk", lambda league: fake)
    df = build_teams(_SEASON, league=_LEAGUE, root=root)

    row = df.filter(pl.col("ncaa_team_id") == str(_TEAM_ID)).row(0, named=True)
    assert row["espn_team_id"] == "2579"  # the other row is a different season
    assert row["espn_display_name"] == "South Carolina Gamecocks"
    assert row["espn_mascot"] == "Gamecocks"
    # Unmatched teams stay in the frame with null ESPN ids -- a left join, not a filter.
    assert df.height == season_teams(_LEAGUE, _SEASON).height
    assert df.get_column("espn_team_id").null_count() == df.height - 1


def test_build_teams_survives_espn_crosswalk_schema_drift(root: Path, monkeypatch) -> None:
    monkeypatch.setattr(nd._engine, "_espn_crosswalk", lambda league: pl.DataFrame({"totally": ["different"]}))
    df = build_teams(_SEASON, league=_LEAGUE, root=root)

    assert df.columns == TEAMS_COLUMNS
    assert df.get_column("espn_team_id").null_count() == df.height


# --------------------------------------------------------------------------
# the compiled season parquet (built by a SEPARATE non-sharded pass)
# --------------------------------------------------------------------------


def test_season_parquet_is_one_file_concatenating_every_team(root: Path) -> None:
    a = persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    b = persist_schedule(_SCHEDULE_HTML, 700000, _SEASON, league=_LEAGUE, root=root)
    agg = build_season_aggregate("schedules", _SEASON, league=_LEAGUE, root=root)

    assert agg.height == a.height + b.height
    assert agg.columns == SCHEDULE_COLUMNS
    assert set(agg.get_column("team_id").unique().to_list()) == {str(_TEAM_ID), "700000"}
    # Exactly one parquet for the whole season.
    written = list((root / _LEAGUE / "schedules").rglob("*.parquet"))
    assert written == [dataset_path(root, _LEAGUE, "schedules", "parquet", _SEASON)]
    assert pl.read_parquet(written[0]).equals(agg)
    # ...and no json aggregate: the season view is parquet-only.
    json_files = {p.name for p in (root / _LEAGUE / "schedules" / "json" / str(_SEASON)).glob("*.json")}
    assert json_files == {f"{_TEAM_ID}.json", "700000.json"}


def test_season_parquet_recompile_is_idempotent(root: Path) -> None:
    persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    first = build_season_aggregate("schedules", _SEASON, league=_LEAGUE, root=root)
    second = build_season_aggregate("schedules", _SEASON, league=_LEAGUE, root=root)

    assert second.equals(first)


def test_season_parquet_of_an_unswept_season_is_an_empty_typed_frame(root: Path) -> None:
    agg = build_season_aggregate("rosters", 1999, league=_LEAGUE, root=root)

    assert agg.height == 0
    assert agg.columns == ROSTER_COLUMNS
    assert agg.schema["player_id"] == pl.Utf8
    assert agg.schema["ht_inches"] == pl.Int64


def test_season_parquet_tolerates_a_team_whose_column_is_all_null(root: Path) -> None:
    # An all-null column reads back from json as Null dtype; conforming each
    # team before the union is what stops that poisoning the season schema.
    persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    thin = dataset_path(root, _LEAGUE, "rosters", "json", _SEASON, 700000)
    thin.parent.mkdir(parents=True, exist_ok=True)
    thin.write_text(json.dumps([{"team_id": "700000", "player_id": "1", "ht_inches": None}]), encoding="utf-8")

    agg = build_season_aggregate("rosters", _SEASON, league=_LEAGUE, root=root)

    assert agg.columns == ROSTER_COLUMNS
    assert agg.schema["ht_inches"] == pl.Int64
    assert "700000" in agg.get_column("team_id").to_list()


# --------------------------------------------------------------------------
# resumability + offline rebuild
# --------------------------------------------------------------------------


def test_rebuild_missing_skips_complete_output(root: Path) -> None:
    persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)

    assert rebuild_missing("schedules", _SEASON, league=_LEAGUE, root=root) == 0


def test_rebuild_missing_reparses_committed_html_offline(root: Path) -> None:
    original = persist_schedule(_SCHEDULE_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    json_path = dataset_path(root, _LEAGUE, "schedules", "json", _SEASON, _TEAM_ID)
    json_path.unlink()

    assert rebuild_missing("schedules", _SEASON, league=_LEAGUE, root=root) == 1
    assert json.loads(json_path.read_text("utf-8")) == original.to_dicts()


def test_rebuild_missing_backfills_rosters_from_the_legacy_payload(root: Path) -> None:
    # Seasons captured before this tree existed have team_rosters/ JSON but no
    # html -- they must backfill without a re-fetch.
    legacy = root / _LEAGUE / "team_rosters" / str(_SEASON)
    legacy.mkdir(parents=True)
    parsed = persist_roster(_ROSTER_HTML, _TEAM_ID, _SEASON, league=_LEAGUE, root=root)
    (legacy / f"{_TEAM_ID}.json").write_text(
        json.dumps({"team_id": _TEAM_ID, "players": parsed.drop("season", "league", "team_id", "team").to_dicts()}),
        encoding="utf-8",
    )
    for fmt in ("html", "json"):
        dataset_path(root, _LEAGUE, "rosters", fmt, _SEASON, _TEAM_ID).unlink()

    assert rebuild_missing("rosters", _SEASON, league=_LEAGUE, root=root) == 1
    rebuilt = _season_parquet(root, "rosters")
    assert rebuilt.columns == ROSTER_COLUMNS
    # Same players; the season parquet is sorted, the parsed frame is page-order.
    assert sorted(rebuilt.get_column("player_id").to_list()) == sorted(parsed.get_column("player_id").to_list())
    assert rebuilt.get_column("team").unique().to_list() == ["South Carolina"]
    # No html for a legacy-backfilled team -- documented, and not a failure.
    assert read_html(root, _LEAGUE, "rosters", _SEASON, _TEAM_ID) is None


# --------------------------------------------------------------------------
# the zero-extra-HTTP contract with the two existing sweeps
# --------------------------------------------------------------------------


def test_discover_persists_schedules_from_its_own_fetch(root: Path) -> None:
    calls = []

    def fetch(team_id):
        calls.append(team_id)
        return _SCHEDULE_HTML

    df = discover_season(_SEASON, league=_LEAGUE, team_ids=[_TEAM_ID], fetch_fn=fetch, root=root)

    assert len(calls) == 1  # exactly the sweep's own fetch -- no second request
    assert df.height > 0
    assert dataset_path(root, _LEAGUE, "schedules", "html", _SEASON, _TEAM_ID).is_file()
    assert dataset_path(root, _LEAGUE, "schedules", "json", _SEASON, _TEAM_ID).is_file()
    # A shard must NOT write the shared season parquet.
    assert not dataset_path(root, _LEAGUE, "schedules", "parquet", _SEASON).exists()


def test_discover_reuses_committed_html_instead_of_refetching(root: Path) -> None:
    calls = []

    def fetch(team_id):
        calls.append(team_id)
        return _SCHEDULE_HTML

    first = discover_season(_SEASON, league=_LEAGUE, team_ids=[_TEAM_ID], fetch_fn=fetch, root=root)
    second = discover_season(_SEASON, league=_LEAGUE, team_ids=[_TEAM_ID], fetch_fn=fetch, root=root)

    assert len(calls) == 1  # the re-run is served entirely off disk
    assert second.equals(first)


def test_rosters_persist_from_their_own_fetch_and_resume(root: Path) -> None:
    calls = []

    def fetch(path):
        calls.append(path)
        return _ROSTER_HTML

    written, skipped, failed = capture_rosters(_SEASON, league=_LEAGUE, root=root, team_ids=[_TEAM_ID], fetch_fn=fetch)
    assert (written, skipped, failed) == (1, 0, 0)
    assert len(calls) == 1
    assert dataset_path(root, _LEAGUE, "rosters", "html", _SEASON, _TEAM_ID).is_file()
    assert dataset_path(root, _LEAGUE, "rosters", "json", _SEASON, _TEAM_ID).is_file()
    assert not dataset_path(root, _LEAGUE, "rosters", "parquet", _SEASON).exists()
    # The original team_rosters/ payload is still written for existing consumers.
    assert (root / _LEAGUE / "team_rosters" / str(_SEASON) / f"{_TEAM_ID}.json").is_file()

    again = capture_rosters(_SEASON, league=_LEAGUE, root=root, team_ids=[_TEAM_ID], fetch_fn=fetch)
    assert again == (0, 1, 0)
    assert len(calls) == 1  # resumed off disk, no second request
