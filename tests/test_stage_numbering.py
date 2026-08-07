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


def _league() -> str:
    m = re.fullmatch(r"ncaa-(?P<lg>[a-z]+)-hoops-raw", REPO.name)
    assert m, f"repo dir {REPO.name!r} doesn't match ncaa-<lg>-hoops-raw"
    return m.group("lg")


def _python_modules() -> set[str]:
    return {p.stem for p in (REPO / "python").glob("ncaa_*.py")}


def _test_modules() -> set[str]:
    return {p.stem.removeprefix("test_ncaa_") for p in (REPO / "tests").glob("test_ncaa_*.py")}


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
