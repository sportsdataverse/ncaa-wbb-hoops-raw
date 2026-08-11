"""Twin-repo structural parity gate for the NCAA hoops raw scrapers.

``ncaa-mbb-hoops-raw`` and ``ncaa-wbb-hoops-raw`` are engine-identical
league-binding shims over ``sportsdataverse.scrape.ncaa`` (sdv-py #328):
every ``python/ncaa_*.py`` module here is a thin ``LEAGUE``-bound re-export
(see any of them -- there is no logic in this repo, only the binding), and
both repos run the SAME numbered stages in the SAME order (README "Run
order").

Stage files carry the house ``NN_`` convention (D31): a python shim is
``ncaa_<lg>_<NN>_<key>_<verb>.py``, its driver is ``run_<NN>_<short>.sh``,
and its suite is ``tests/test_<NN>_<short>.py``. The numbers are INTENDED
BUILD ORDER, not the order a campaign driver happens to invoke them in, and
a retired stage leaves a HOLE rather than renumbering its successors --
cross-repo number semantics beat dense numbering. 99 is deliberately unused
here: D31 reserves it for the schedule-master/coverage split, which is still
part of discover (01) and cannot be separated without an engine change.

The number also carries the delineation this family previously left to
prose: **numbered == a runnable stage with a CLI, unnumbered == a library**
imported by the stages (``ncaa_bundle``, ``ncaa_identity``). That is an
executable rule, not a naming preference -- see
``test_numbered_modules_are_runnable_and_libraries_are_not``, which exists
because the shim reduction silently dropped the canary's ``__main__`` block
and left its driver exiting 0 without probing anything.

Portability: this file is designed to be byte-identical in both repos -- it
derives its own league token from the repo directory name
(``ncaa-<lg>-hoops-raw``) rather than hardcoding "mbb" or "wbb", and every
test/script name below is league-free for the same reason. Any diff between
the two copies is drift, not a league difference.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The numbered stages. Fields:
#   num          -- the NN token (build order; holes are intentional)
#   module_key   -- python/ncaa_<lg>_<num>_<module_key>.py
#   short        -- run_<num>_<short>.sh and tests/test_<num>_<short>.py
#   in_run_order -- appears in README's "## Run order" block, in this order
#   has_test     -- carries a dedicated tests/test_<num>_<short>.py
#   has_driver   -- carries a scripts/run_<num>_<short>.sh
STAGES: tuple[tuple[str, str, str, bool, bool, bool], ...] = (
    ("01", "schedules_scrape", "schedules", True, True, True),
    ("02", "games_scrape", "games", True, True, True),
    ("03", "games_parse", "parse", True, True, True),
    # rosters is exercised through test_05_datasets (both persist via the
    # same engine writers), so it carries no dedicated suite of its own.
    ("04", "rosters_scrape", "rosters", True, False, True),
    ("05", "datasets_build", "datasets", True, True, True),
    # Run directly (python python/ncaa_<lg>_06_xwalk_build.py) after a
    # season's schedules exist; no driver because it takes no proxy creds
    # and no sharding.
    ("06", "xwalk_build", "xwalk", False, True, False),
    # Operator pre-flight, not part of a campaign -- hence the 98 block.
    ("98", "canary_probe", "canary", False, True, True),
)

# python/ncaa_<name>.py modules that are LIBRARIES, not stages: imported by
# the numbered shims, never run. They keep the league-free ncaa_ prefix and
# take no number precisely so the listing separates them at a glance.
LIBRARY_MODULES = ("bundle", "identity")

# scripts/ drivers that compose stages rather than being one. The league
# pair (run_<lg>_backfill{,_range}) is added per-repo in the check below.
CAMPAIGN_SCRIPTS = (
    "run_autocommit",
    "run_reference_backfill",
)

# Test FUNCTION names every twin must carry in each registered tests/ file.
# This is the file-inventory checks' missing rung: those only prove the FILE
# exists, so a same-named-file rewrite that quietly drops or renames a guard
# (the 2026-08 finding: a "cleaner" rewrite dropped a dtype-mismatch probe,
# and 3 shared-engine regression tests existed in only one twin) sails
# through them. A test function disappearing -- or reappearing under a new
# name -- from one twin without an update HERE is the drift signal.
#
# Populate/refresh this by comparing both twins' `ast.parse`-derived function
# name sets (see the module docstring's portability note -- this file itself
# must then stay byte-identical, so both copies get the same edit).
REQUIRED_TEST_FUNCTIONS: dict[str, frozenset[str]] = {
    "test_bundle.py": frozenset(
        {
            "test_round_trip",
        }
    ),
    "test_98_canary.py": frozenset(
        {
            "test_classify_ban_beats_size_floor",
            "test_classify_challenge_beats_size_floor",
            "test_classify_clean",
            "test_classify_error_buckets",
            "test_classify_markerless_stub",
            "test_example_config_every_vendor_is_skipped",
            "test_module_entry_point_actually_runs_the_engine",
            "test_vendor_ready_accepts_real_creds",
            "test_vendor_ready_skips_placeholders",
        }
    ),
    "test_02_games.py": frozenset(
        {
            "test_capture_contests_is_idempotent",
            "test_capture_contests_rejects_shell_page",
            "test_capture_contests_uses_injected_fetcher",
            "test_capture_contests_writes_bundles_matching_fixtures",
            "test_capture_hard_stops_on_consecutive_challenge_failures",
            "test_consecutive_failure_counter_resets_on_success",
            "test_max_contests_stops_chunk_cleanly_and_resumes",
            "test_select_pending_scopes_to_requested_season",
            "test_shard_disjoint_and_covers_all",
        }
    ),
    "test_05_datasets.py": frozenset(
        {
            "test_build_teams_espn_columns_present_even_without_the_crosswalk",
            "test_build_teams_joins_a_present_espn_crosswalk",
            "test_build_teams_null_fills_when_the_espn_loader_is_absent",
            "test_build_teams_survives_espn_crosswalk_schema_drift",
            "test_build_teams_writes_all_three_formats_with_names_and_division",
            "test_dataset_path_ignores_team_id_for_the_season_parquet",
            "test_dataset_path_layout",
            "test_dataset_path_rejects_unknown_kind",
            "test_dataset_path_requires_team_id_for_per_team_artifacts",
            "test_discover_persists_schedules_from_its_own_fetch",
            "test_discover_reuses_committed_html_instead_of_refetching",
            "test_persist_roster_writes_per_team_html_and_json_only",
            "test_persist_schedule_writes_per_team_html_and_json_only",
            "test_rebuild_missing_backfills_rosters_from_the_legacy_payload",
            "test_rebuild_missing_reparses_committed_html_offline",
            "test_rebuild_missing_skips_complete_output",
            "test_roster_carries_the_teams_espn_id",
            "test_roster_ids_are_utf8_never_float_stringified",
            "test_roster_season_parquet_carries_player_id_display_name_and_pbp_key",
            "test_rosters_persist_from_their_own_fetch_and_resume",
            "test_schedule_carries_espn_ids_for_both_sides",
            "test_schedule_espn_ids_null_when_the_crosswalk_is_absent",
            "test_schedule_ids_are_utf8_never_float_stringified",
            "test_schedule_json_is_a_records_array_matching_the_frame",
            "test_season_ncaa",
            "test_season_parquet_carries_ids_and_readable_names",
            "test_season_parquet_is_one_file_concatenating_every_team",
            "test_season_parquet_of_an_unswept_season_is_an_empty_typed_frame",
            "test_season_parquet_recompile_is_idempotent",
            "test_season_parquet_tolerates_a_team_whose_column_is_all_null",
            "test_season_teams_rejects_unknown_league_and_empty_season",
            "test_season_teams_shape_and_utf8_id",
        }
    ),
    "test_01_schedules.py": frozenset(
        {
            "test_discover_aborts_on_consecutive_team_failures",
            "test_discover_resumes_from_checkpoint",
            "test_discover_season_dedups_across_teams",
            "test_discover_season_offline",
            "test_discover_season_present_season_selects_teams_from_real_crosswalk",
            "test_discover_season_raises_on_crosswalk_format_drift",
            "test_discover_season_raises_on_unrecognized_league",
            "test_discover_shard_slices_and_skips_master",
            "test_discover_tolerates_flaky_team_and_skips_it",
            "test_season_str_conversion",
            "test_write_master_merges_and_preserves_captured",
        }
    ),
    "test_06_xwalk.py": frozenset(
        {
            "test_ambiguous_single_team_day_resolves_to_null",
            "test_date_window_tier_absorbs_a_one_day_offset",
            "test_exact_tier_matches_on_date_and_both_team_ids",
            "test_ids_are_utf8_and_never_float_stringified",
            "test_missing_crosswalk_file_loads_as_an_empty_index",
            "test_missing_schedules_parquet_yields_an_empty_crosswalk",
            "test_one_espn_game_claimed_by_two_contests_is_voided_on_both",
            "test_schedule_side_prefers_the_row_that_resolved_both_teams",
            "test_single_team_tier_covers_a_non_di_opponent",
            "test_unmatched_contest_keeps_its_row_with_a_null_id",
            "test_unordered_pair_tier_absorbs_a_neutral_site_inversion",
            "test_write_then_load_round_trips_an_offline_index",
        }
    ),
    "test_identity.py": frozenset(
        {
            "test_ambiguous_name_key_yields_null_not_a_coin_flip",
            "test_enrichment_is_additive_and_stamps_ids_on_every_family",
            "test_espn_team_ids_are_present_on_every_per_game_family",
            "test_key_name_does_not_collapse_different_players",
            "test_key_name_matches_last_first_against_all_caps_first_last",
            "test_key_name_survives_diacritics_hyphens_and_apostrophes",
            "test_loaded_ids_are_utf8",
            "test_missing_rosters_and_teams_tree_degrades_to_nulls_without_raising",
            "test_real_captured_game_resolves_ids_end_to_end",
            "test_team_pseudo_player_never_resolves_to_a_person",
            "test_unknown_season_degrades_to_nulls",
            "test_unmatched_rows_survive_with_null_ids",
            "test_utf8_id_never_stringifies_a_float_as_dot_zero",
        }
    ),
    "test_03_parse.py": frozenset(
        {
            "test_all_fixtures_produce_six_family_keys",
            "test_bundle_written_then_read_still_parses",
            "test_contest_id_is_never_a_float_stringification",
            "test_corrupt_pbp_page_yields_empty_pbp_without_raising",
            "test_espn_game_id_present_on_every_family_even_without_a_crosswalk",
            "test_every_family_row_carries_contest_id_and_no_game_id",
            "test_known_good_game_has_populated_families",
            "test_parse_and_write_convenience",
            "test_shots_contest_id_is_populated_and_agrees_with_the_other_families",
            "test_write_parsed_round_trips_valid_json",
        }
    ),
}

# Test functions that exist in only ONE twin by design -- a real league
# difference proven by the test itself (see each docstring), not drift.
# owner_league pins which twin is allowed to carry it; every entry needs a
# reason so this dict can't quietly absorb something that should instead
# have been added to REQUIRED_TEST_FUNCTIONS (in BOTH twins).
LEAGUE_SPECIFIC_TESTS: dict[str, dict[str, tuple[str, str]]] = {
    "test_01_schedules.py": {
        "test_discover_season_league_wbb_selects_wbb_crosswalk_not_mbb": (
            "wbb",
            "asserts the wbb crosswalk sweep is disjoint from mbb's -- a fact "
            "provable from either side, WBB just happens to be the one that "
            "wrote it down; not a behavior MBB lacks.",
        ),
    },
    "test_03_parse.py": {
        "test_wbb_quarter_period_model_changes_period_length": (
            "wbb",
            "WBB-only delta: proves league='wbb' selects the 4-quarter period "
            "model vs MBB's 2-half model (README 'The 4-quarter period model').",
        ),
        "test_wbb_2ot_game_exceeds_regulation_period_count": (
            "wbb",
            "informational OT-detection sanity check for the WBB period model "
            "above; not a real per-league discriminator by itself.",
        ),
        "test_wbb_shots_league_label_changes_arc_classification": (
            "wbb",
            "WBB-only delta: proves the three-point arc radius by season "
            "differs between leagues in the pre-2021-22 window.",
        ),
    },
}


def _league() -> str:
    m = re.fullmatch(r"ncaa-(?P<lg>[a-z]+)-hoops-raw", REPO.name)
    assert m, f"repo dir {REPO.name!r} doesn't match ncaa-<lg>-hoops-raw"
    return m.group("lg")


def _python_modules() -> set[str]:
    return {p.stem for p in (REPO / "python").glob("ncaa_*.py")}


def _test_modules() -> set[str]:
    """Every tests/ file except this gate itself."""
    return {p.name for p in (REPO / "tests").glob("test_*.py") if p.name != Path(__file__).name}


def _expected_modules() -> set[str]:
    lg = _league()
    numbered = {f"ncaa_{lg}_{num}_{key}" for num, key, *_ in STAGES}
    return numbered | {f"ncaa_{lib}" for lib in LIBRARY_MODULES}


def _expected_test_files() -> set[str]:
    staged = {f"test_{num}_{short}.py" for num, _key, short, _o, has_test, _d in STAGES if has_test}
    return staged | {f"test_{lib}.py" for lib in LIBRARY_MODULES}


def _has_cli(module_stem: str) -> bool:
    """True if ``python python/<module_stem>.py`` actually does something.

    Checked with ast, not by importing: a shim's body injects the engine's
    names into its globals, so importing it to look for ``__main__`` would
    both run that injection and still not tell us whether the file is
    runnable. The marker is a module-level ``if __name__ == "__main__":``.
    """
    tree = ast.parse((REPO / "python" / f"{module_stem}.py").read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


def _test_functions(filename: str) -> set[str]:
    """Module-level ``def test_*`` names in ``tests/<filename>``, via ast (no import)."""
    path = REPO / "tests" / filename
    if not path.is_file():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    }


def _scripts() -> set[str]:
    return {p.stem for p in (REPO / "scripts").glob("run_*.sh")}


def _readme_run_order() -> list[str]:
    """The ``NN`` tokens inside README's fenced ```sh Run order block, in order."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    section = text.split("## Run order", 1)[1]
    block = section.split("```sh", 1)[1].split("```", 1)[0]
    return re.findall(r"run_(\d{2})_[a-z_]+\.sh", block)


def test_layout_is_discoverable() -> None:
    """Guard the guard: if these come back empty, every check below is vacuous."""
    assert _league(), "could not derive a league token from the repo path"
    assert _python_modules(), "no python/ncaa_*.py shims found"
    assert _test_modules(), "no tests/test_*.py files found"
    assert _scripts(), "no scripts/run_*.sh drivers found"
    assert _readme_run_order(), "README has no parseable '## Run order' stage sequence"
    assert REQUIRED_TEST_FUNCTIONS, "REQUIRED_TEST_FUNCTIONS registry is empty"
    assert any(_test_functions(f) for f in REQUIRED_TEST_FUNCTIONS), (
        "ast parsing found zero test_* functions in any registered file -- "
        "the parser (or the registry's filenames) is broken, not this repo's tests"
    )


def test_numbered_modules_are_runnable_and_libraries_are_not() -> None:
    """The number IS the promise: numbered == runnable stage, unnumbered == library.

    This exists because the promise was silently broken. The shim reduction
    (``8e92a31d9``) rewrote the canary as a re-export and dropped the
    ``if __name__ == "__main__": raise SystemExit(main())`` its predecessor
    carried, so ``run_98_canary.sh`` ran a file that defined some names and
    exited 0 -- a green log, a written EXIT=0, and no probe. Nothing else in
    the suite could see that: every other test imports the module rather
    than running it.
    """
    lg = _league()
    problems: list[str] = []
    for num, key, *_ in STAGES:
        module = f"ncaa_{lg}_{num}_{key}"
        if not _has_cli(module):
            problems.append(
                f"{module}.py is numbered but has no `if __name__ == '__main__'` "
                "block -- running it does nothing and its driver still exits 0"
            )
    for lib in LIBRARY_MODULES:
        if _has_cli(f"ncaa_{lib}"):
            problems.append(
                f"ncaa_{lib}.py is documented as a LIBRARY but carries a CLI -- "
                "give it a stage number or drop the entry point"
            )
    assert not problems, "\n".join(problems)


def test_stage_numbers_are_unique_and_ascending() -> None:
    """The table itself must stay a legible build order (holes allowed)."""
    nums = [num for num, *_ in STAGES]
    assert len(set(nums)) == len(nums), f"duplicate stage number(s) in STAGES: {nums}"
    assert nums == sorted(nums), f"STAGES is not in ascending number order: {nums}"


def test_python_shim_inventory_matches_the_documented_set() -> None:
    expected = _expected_modules()
    found = _python_modules()
    missing = expected - found
    extra = found - expected
    assert not missing, f"python/ is missing expected shim(s): {sorted(missing)}"
    assert not extra, (
        f"python/ has undocumented shim(s): {sorted(extra)} -- update STAGES/"
        "LIBRARY_MODULES in this file (in BOTH twins) if it's intentional."
    )


def test_test_inventory_matches_shims_minus_documented_exceptions() -> None:
    expected = _expected_test_files()
    found = _test_modules()
    missing = expected - found
    extra = found - expected
    assert not missing, f"tests/ is missing expected suite(s): {sorted(missing)}"
    assert not extra, (
        f"tests/ has a suite with no matching entry in STAGES/LIBRARY_MODULES: {sorted(extra)}"
    )


def test_script_inventory_matches_the_documented_set() -> None:
    lg = _league()
    expected = {
        *CAMPAIGN_SCRIPTS,
        *(f"run_{num}_{short}" for num, _k, short, _o, _t, has_driver in STAGES if has_driver),
        f"run_{lg}_backfill",
        f"run_{lg}_backfill_range",
    }
    found = _scripts()
    missing = expected - found
    extra = found - expected
    assert not missing, f"scripts/ is missing expected driver(s): {sorted(missing)}"
    assert not extra, (
        f"scripts/ has undocumented driver(s): {sorted(extra)} -- update STAGES/"
        "CAMPAIGN_SCRIPTS in this file (in BOTH twins), or add the league-specific "
        "pair, if intentional."
    )


def test_every_stage_driver_invokes_its_own_numbered_shim() -> None:
    """``run_NN_x.sh`` must call ``python/ncaa_<lg>_NN_*.py`` -- not a sibling.

    The number is only meaningful if the driver and the shim it runs agree on
    it; a copy-pasted driver still pointing at the stage it was cloned from
    would otherwise look correctly numbered from the directory listing.
    """
    lg = _league()
    problems: list[str] = []
    for num, key, short, _order, _test, has_driver in STAGES:
        if not has_driver:
            continue
        body = (REPO / "scripts" / f"run_{num}_{short}.sh").read_text(encoding="utf-8")
        expected = f"python/ncaa_{lg}_{num}_{key}.py"
        if expected not in body:
            called = sorted(set(re.findall(r"python/(ncaa_\w+)\.py", body)))
            problems.append(f"run_{num}_{short}.sh does not invoke {expected} (calls: {called})")
    assert not problems, "\n".join(problems)


def test_readme_run_order_matches_the_stage_tuple() -> None:
    expected = tuple(num for num, _k, _s, in_order, _t, _d in STAGES if in_order)
    order = tuple(_readme_run_order())
    assert order == expected, (
        f"README '## Run order' runs stages {order}, expected {expected} -- the "
        "prose and the numbering are the same contract."
    )


def test_test_function_inventory_has_no_undeclared_drift() -> None:
    """Per-file test FUNCTION names, not just filenames, must match the twins.

    The file-inventory checks above only prove ``tests/test_01_schedules.py``
    exists in both repos -- they say nothing about what's INSIDE it. That gap
    is exactly how this repo's own drift shipped: one twin's rewrite of a
    regression test quietly dropped a guarded assertion under a new function
    name, and 3 shared-engine tests existed in only one twin, with the
    filename-only checks green the whole time. A missing or undeclared-extra
    function name here is that same class of drift, caught before another
    silent gap. It does NOT prove an unchanged-name function's BODY didn't
    regress -- that is a real, accepted limit of this check.
    """
    lg = _league()
    problems: list[str] = []
    for filename, required in REQUIRED_TEST_FUNCTIONS.items():
        found = _test_functions(filename)
        allowed_extra = {
            name
            for name, (owner_lg, _reason) in LEAGUE_SPECIFIC_TESTS.get(filename, {}).items()
            if owner_lg == lg
        }
        missing = required - found
        extra = found - required - allowed_extra
        if missing:
            problems.append(f"{filename}: missing required test(s) {sorted(missing)}")
        if extra:
            problems.append(
                f"{filename}: undeclared test(s) {sorted(extra)} -- add to "
                "REQUIRED_TEST_FUNCTIONS (if it belongs in both twins) or "
                "LEAGUE_SPECIFIC_TESTS with a reason (if it's a real league "
                "difference), in BOTH copies of this file"
            )
    assert not problems, "\n".join(problems)
