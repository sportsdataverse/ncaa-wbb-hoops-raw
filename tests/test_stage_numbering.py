"""Twin-repo structural parity gate for the NCAA hoops raw scrapers.

``ncaa-mbb-hoops-raw`` and ``ncaa-wbb-hoops-raw`` are engine-identical
league-binding shims over ``sportsdataverse.scrape.ncaa`` (sdv-py #328):
every ``python/ncaa_*.py`` module here is a thin ``LEAGUE``-bound re-export
(see any of them -- there is no logic in this repo, only the binding), and
both repos run the SAME five named stages in the SAME order --
``discover -> capture -> parse -> rosters -> datasets`` (README "Run order").

There is no numbered (``NN_``) stage-shim convention in this family -- that
pattern belongs to the sibling ``*-data`` build repos (see
``hoopR-nba-stats-data/tests/test_stage_inventory.py``), which iterate a
dataset REGISTRY the numbers must track build-order against. This repo has
no such registry: the "stages" are five fixed, named scripts wired by prose
in the README and composed by the ``run_*_backfill*.sh`` drivers, not
iterated by a build loop. So the parity contract here is the file INVENTORY
plus the declared stage order, not NN<->registry-key agreement -- ordinal
position in ``STAGES`` below stands in for the number.

Portability: this file is designed to be byte-identical in both repos -- it
derives its own league token from the repo directory name
(``ncaa-<lg>-hoops-raw``) rather than hardcoding "mbb" or "wbb". Any diff
between the two copies is drift, not a league difference.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The five run-order stages, in the order README.md's "## Run order" section
# runs them. This tuple IS the number-semantics contract for this family.
STAGES = ("discover", "capture", "parse", "rosters", "datasets")

# python/ shims that exist but are not one of the five pipeline stages.
NON_STAGE_MODULES = ("bundle", "canary", "espn_game_xwalk", "identity")

# python/ shims with no dedicated tests/test_ncaa_*.py -- exercised instead
# through another module's test file (ncaa_rosters via test_ncaa_datasets).
NO_DEDICATED_TEST = ("rosters",)

# scripts/ wrappers common to both leagues (no league token in the name).
COMMON_SCRIPTS = (
    "run_autocommit",
    "run_canary",
    "run_capture",
    "run_datasets",
    "run_discover",
    "run_parse",
    "run_reference_backfill",
    "run_rosters",
)

# Test FUNCTION names every twin must carry in each tests/test_ncaa_*.py file.
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
    "test_ncaa_bundle.py": frozenset(
        {
            "test_round_trip",
        }
    ),
    "test_ncaa_canary.py": frozenset(
        {
            "test_classify_ban_beats_size_floor",
            "test_classify_challenge_beats_size_floor",
            "test_classify_clean",
            "test_classify_error_buckets",
            "test_classify_markerless_stub",
            "test_example_config_every_vendor_is_skipped",
            "test_vendor_ready_accepts_real_creds",
            "test_vendor_ready_skips_placeholders",
        }
    ),
    "test_ncaa_capture.py": frozenset(
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
    "test_ncaa_datasets.py": frozenset(
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
    "test_ncaa_discover.py": frozenset(
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
    "test_ncaa_espn_game_xwalk.py": frozenset(
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
    "test_ncaa_identity.py": frozenset(
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
    "test_ncaa_parse.py": frozenset(
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
    "test_ncaa_discover.py": {
        "test_discover_season_league_wbb_selects_wbb_crosswalk_not_mbb": (
            "wbb",
            "asserts the wbb crosswalk sweep is disjoint from mbb's -- a fact "
            "provable from either side, WBB just happens to be the one that "
            "wrote it down; not a behavior MBB lacks.",
        ),
    },
    "test_ncaa_parse.py": {
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
    return {p.stem.removeprefix("test_ncaa_") for p in (REPO / "tests").glob("test_ncaa_*.py")}


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
    """The stage sequence inside README's fenced ```sh Run order block, in order."""
    text = (REPO / "README.md").read_text(encoding="utf-8")
    section = text.split("## Run order", 1)[1]
    block = section.split("```sh", 1)[1].split("```", 1)[0]
    return re.findall(r"run_(discover|capture|parse|rosters|datasets)\.sh", block)


def test_layout_is_discoverable() -> None:
    """Guard the guard: if these come back empty, every check below is vacuous."""
    assert _league(), "could not derive a league token from the repo path"
    assert _python_modules(), "no python/ncaa_*.py shims found"
    assert _test_modules(), "no tests/test_ncaa_*.py files found"
    assert _scripts(), "no scripts/run_*.sh drivers found"
    assert _readme_run_order(), "README has no parseable '## Run order' stage sequence"
    assert REQUIRED_TEST_FUNCTIONS, "REQUIRED_TEST_FUNCTIONS registry is empty"
    assert any(_test_functions(f) for f in REQUIRED_TEST_FUNCTIONS), (
        "ast parsing found zero test_* functions in any registered file -- "
        "the parser (or the registry's filenames) is broken, not this repo's tests"
    )


def test_python_shim_inventory_matches_the_documented_set() -> None:
    expected = {f"ncaa_{s}" for s in (*STAGES, *NON_STAGE_MODULES)}
    found = _python_modules()
    missing = expected - found
    extra = found - expected
    assert not missing, f"python/ is missing expected shim(s): {sorted(missing)}"
    assert not extra, (
        f"python/ has undocumented shim(s): {sorted(extra)} -- update STAGES/"
        "NON_STAGE_MODULES in this file (in BOTH twins) if it's intentional."
    )


def test_test_inventory_matches_shims_minus_documented_exceptions() -> None:
    modules = {m.removeprefix("ncaa_") for m in _python_modules()}
    needs_test = modules - set(NO_DEDICATED_TEST)
    found = _test_modules()
    missing = needs_test - found
    extra = found - modules
    assert not missing, f"no tests/test_ncaa_*.py for: {sorted(missing)}"
    assert not extra, f"tests/ has a test file with no matching python/ shim: {sorted(extra)}"


def test_script_inventory_matches_the_documented_set() -> None:
    lg = _league()
    expected = {*COMMON_SCRIPTS, f"run_{lg}_backfill", f"run_{lg}_backfill_range"}
    found = _scripts()
    missing = expected - found
    extra = found - expected
    assert not missing, f"scripts/ is missing expected driver(s): {sorted(missing)}"
    assert not extra, (
        f"scripts/ has undocumented driver(s): {sorted(extra)} -- update COMMON_SCRIPTS "
        "in this file (in BOTH twins), or add the league-specific pair, if intentional."
    )


def test_readme_run_order_matches_the_stage_tuple() -> None:
    order = tuple(_readme_run_order())
    assert order == STAGES, (
        f"README '## Run order' lists {order}, expected the canonical sequence {STAGES}"
    )


def test_test_function_inventory_has_no_undeclared_drift() -> None:
    """Per-file test FUNCTION names, not just filenames, must match the twins.

    The file-inventory checks above only prove ``tests/test_ncaa_discover.py``
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
