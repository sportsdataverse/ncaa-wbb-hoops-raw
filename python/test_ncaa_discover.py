"""Offline tests for season -> contest_id discovery (no network)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl
import pytest
from sportsdataverse.mbb.mbb_ncaa_team_ids import ncaa_mbb_team_ids
from sportsdataverse.wbb.wbb_ncaa_team_ids import ncaa_wbb_team_ids

from ncaa_discover import _season_str, discover_season

# Sibling checkout: .../sdv-dev/{wehoop-dev/ncaa-wbb-hoops-raw, sdv-py}.
FIXTURE = (
    Path(__file__).resolve().parent
    / "tests"
    / "fixtures"
    / "ncaa"
    / "bigballr"
    / "html"
    / "team_592003.html"
)
_HTML = FIXTURE.read_text(encoding="utf-8")

# South Carolina WBB, season 2025 (ending-year) -> crosswalk "2024-25".
_TEAM_ID = 592003
_SEASON = 2025


def test_discover_season_offline() -> None:
    df = discover_season(
        _SEASON,
        league="wbb",
        limit_teams=1,
        team_ids=[_TEAM_ID],
        fetch_fn=lambda tid: _HTML,
    )

    assert df.schema["contest_id"] == pl.Utf8
    assert df.height > 0
    contest_ids = df.get_column("contest_id").to_list()
    assert all(isinstance(c, str) and c != "" for c in contest_ids)
    assert len(contest_ids) == len(set(contest_ids))  # no duplicates


def test_discover_season_dedups_across_teams() -> None:
    # Two distinct team_ids fed the SAME fixture page -> same contest_id set
    # on both "schedules" -> dedup must collapse the union back to one copy.
    solo = discover_season(_SEASON, team_ids=[_TEAM_ID], fetch_fn=lambda tid: _HTML)
    two_teams = discover_season(_SEASON, team_ids=[_TEAM_ID, 700000], fetch_fn=lambda tid: _HTML)

    assert two_teams.height == solo.height
    assert set(two_teams.get_column("contest_id").to_list()) == set(solo.get_column("contest_id").to_list())


def test_write_master_merges_and_preserves_captured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        discover_season(_SEASON, team_ids=[_TEAM_ID], fetch_fn=lambda tid: _HTML, root=root)

        master_path = root / "wbb" / "schedule_master.parquet"
        assert master_path.exists()

        master = pl.read_parquet(master_path)
        assert set(master.columns) == {"contest_id", "season", "captured"}
        assert (master.get_column("captured") == False).all()  # noqa: E712

        # Simulate a downstream capture step flipping one row to True, then
        # re-run discovery -- the captured=True row must survive the merge.
        first_id = master.get_column("contest_id")[0]
        updated = master.with_columns(
            pl.when(pl.col("contest_id") == first_id).then(True).otherwise(pl.col("captured")).alias("captured")
        )
        updated.write_parquet(master_path)

        discover_season(_SEASON, team_ids=[_TEAM_ID], fetch_fn=lambda tid: _HTML, root=root)
        after = pl.read_parquet(master_path)
        row = after.filter(pl.col("contest_id") == first_id)
        assert row.get_column("captured")[0] == True  # noqa: E712
        assert after.height == master.height  # re-run adds nothing new


def test_season_str_conversion() -> None:
    # Ending-year int -> crosswalk "YYYY-YY" format (the live-path filter key).
    assert _season_str(2026) == "2025-26"
    assert _season_str(2010) == "2009-10"


def test_discover_season_present_season_selects_teams_from_real_crosswalk() -> None:
    # 2025 -> "2024-25", the latest season the bundled WBB crosswalk actually
    # contains -- exercises the real (unmocked) crosswalk filter end to end,
    # not the team_ids bypass.
    df = discover_season(_SEASON, league="wbb", limit_teams=3, fetch_fn=lambda tid: _HTML)
    assert df.height > 0


def test_discover_season_raises_on_crosswalk_format_drift() -> None:
    # No team plays in a season this far outside the bundled crosswalk range
    # -- must fail loudly, not return an empty, complete-looking frame.
    with pytest.raises(ValueError):
        discover_season(1900, fetch_fn=lambda tid: _HTML)


def test_discover_season_raises_on_unrecognized_league() -> None:
    with pytest.raises(ValueError):
        discover_season(_SEASON, league="xbb", fetch_fn=lambda tid: _HTML)


def test_discover_season_league_wbb_selects_wbb_crosswalk_not_mbb() -> None:
    # The point of this task: discover_season(league="wbb") must sweep team
    # ids from the WBB crosswalk, not the MBB one. Drives the real (unmocked)
    # crosswalk path -- no team_ids= bypass -- with a stub fetch_fn that just
    # records which team ids it was called with.
    season_str = _season_str(_SEASON)
    wbb_ids = set(ncaa_wbb_team_ids().filter(pl.col("season") == season_str).get_column("id").to_list())
    mbb_ids = set(ncaa_mbb_team_ids().filter(pl.col("season") == season_str).get_column("id").to_list())
    # Sanity check on the real bundled crosswalks: if this ever fails, the two
    # id spaces started overlapping and the test below would no longer be
    # able to distinguish "swept the wbb crosswalk" from "swept the mbb one".
    assert wbb_ids.isdisjoint(mbb_ids)

    swept: list = []

    def recording_fetch_fn(team_id: int) -> str:
        swept.append(team_id)
        return _HTML

    discover_season(_SEASON, league="wbb", limit_teams=5, fetch_fn=recording_fetch_fn)

    assert swept, "no teams were swept"
    assert set(swept).issubset(wbb_ids)
    assert set(swept).isdisjoint(mbb_ids)


def test_discover_tolerates_flaky_team_and_skips_it() -> None:
    """One team failing every retry is SKIPPED, not fatal (bm-verify flake)."""
    tries: "dict[int, int]" = {}

    def flaky_fetch(team_id: int) -> str:
        tries[team_id] = tries.get(team_id, 0) + 1
        if team_id == 2:
            raise RuntimeError("BAN-SUSPECT:stub")
        return _HTML

    out = discover_season(_SEASON, team_ids=[1, 2, 3], fetch_fn=flaky_fetch)
    assert out.height > 0
    assert tries[2] == 3  # _TEAM_TRIES retries before giving up on that team


def test_discover_aborts_on_consecutive_team_failures() -> None:
    """A real ban looks like EVERY team failing -> abort on the run."""
    with pytest.raises(RuntimeError, match="consecutive teams failed"):
        discover_season(
            _SEASON,
            team_ids=list(range(1, 9)),
            fetch_fn=_always_ban_fetch,
        )


def _always_ban_fetch(team_id: int) -> str:
    raise RuntimeError("BAN-SUSPECT:stub")


def test_discover_resumes_from_checkpoint(tmp_path) -> None:
    """An aborted sweep's checkpointed teams are NOT refetched on re-run."""

    def flaky_fetch(team_id: int) -> str:
        if team_id == 2:
            raise RuntimeError("BAN-SUSPECT:stub")
        return _HTML

    # First run: team 2 fails all retries -> skipped (tolerant sweep); teams
    # 1 + 3 succeed and are checkpointed to disk.
    out1 = discover_season(_SEASON, team_ids=[1, 2, 3], fetch_fn=flaky_fetch, root=tmp_path)
    assert out1.height > 0
    scratch = tmp_path / "wbb" / ".discover" / str(_SEASON)
    assert sorted(p.stem for p in scratch.glob("*.json")) == ["1", "3"]

    # Second run: teams 1 + 3 must come from the checkpoint (fetch would now
    # blow up for them); only the previously-skipped team 2 is fetched.
    def second_fetch(team_id: int) -> str:
        assert team_id == 2, f"checkpointed team {team_id} was refetched"
        return _HTML

    out2 = discover_season(_SEASON, team_ids=[1, 2, 3], fetch_fn=second_fetch, root=tmp_path)
    assert set(out2.get_column("contest_id").to_list()) == set(out1.get_column("contest_id").to_list())


def test_discover_shard_slices_and_skips_master(tmp_path) -> None:
    """A sharded run sweeps only its slice and never writes schedule_master."""
    seen: "list[int]" = []

    def fetch(team_id: int) -> str:
        seen.append(team_id)
        return _HTML

    ids = [1, 2, 3, 4, 5, 6]
    discover_season(
        _SEASON,
        team_ids=ids,
        fetch_fn=fetch,
        root=tmp_path,
        shard=(1, 3),
        write_master=False,
    )
    assert seen == [2, 5]  # ids[1::3]
    assert not (tmp_path / "wbb" / "schedule_master.parquet").exists()

    # Remaining shards, then the merge pass: it re-reads every shard's
    # checkpoints, fetches nothing new, and writes the master.
    for i in (0, 2):
        discover_season(
            _SEASON,
            team_ids=ids,
            fetch_fn=fetch,
            root=tmp_path,
            shard=(i, 3),
            write_master=False,
        )
    swept = len(seen)
    out = discover_season(_SEASON, team_ids=ids, fetch_fn=fetch, root=tmp_path)
    assert len(seen) == swept  # merge pass fetched nothing
    assert out.height > 0
    assert (tmp_path / "wbb" / "schedule_master.parquet").exists()


def main() -> None:
    test_discover_season_offline()
    test_discover_season_dedups_across_teams()
    test_write_master_merges_and_preserves_captured()
    test_season_str_conversion()
    test_discover_season_present_season_selects_teams_from_real_crosswalk()
    test_discover_season_raises_on_crosswalk_format_drift()
    test_discover_season_raises_on_unrecognized_league()
    test_discover_season_league_wbb_selects_wbb_crosswalk_not_mbb()
    test_discover_tolerates_flaky_team_and_skips_it()
    test_discover_aborts_on_consecutive_team_failures()
    print("OK")


if __name__ == "__main__":
    main()
