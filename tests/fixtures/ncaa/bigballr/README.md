# stats.ncaa.org HTML fixtures (bigballr corpus)

Copied from `sportsdataverse-py/tests/fixtures/ncaa/bigballr/html` on 2026-08-02.

**Why a copy and not a reference.** These tests previously read the fixtures
straight out of a sibling `../../../sdv-py` checkout. That resolves only on a
machine with this exact directory layout: CI has no sibling checkout, and
sdv-py's wheel excludes `tests/`, so the suite could not run anywhere but one
developer's box. The fixtures are ~5.8 MB across 30 files -- cheap next to this
repo's captured tree, and the price is that a re-capture upstream must be
copied here too.

**Provenance.** Real stats.ncaa.org pages (team schedule, roster, box,
individual stats, play-by-play) for a small set of known-good contests; they
are the same pages sdv-py's NCAA parser tests assert against, so both sides
exercise identical input.
