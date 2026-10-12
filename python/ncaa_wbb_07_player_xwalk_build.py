"""Stage 07 -- NCAA roster player_id <-> ESPN athlete_id crosswalk.

League binding for :mod:`sportsdataverse.scrape.ncaa.espn_player_xwalk`; no logic here.
Writes ``wbb/xwalk/espn_athlete_id/{season}.json`` (stage 06's layout), one row per
NCAA roster player: ``{ncaa_team_id, ncaa_player_id, espn_team_id, espn_athlete_id,
match_method, unmatched_reason}``. ``--seasons`` takes this repo's tree key: the
ending year (2026 = 2025-26).

Reads the committed roster parquet (stage 05) plus ONE sdv-py release-loader read per
season (ESPN player box / game rosters) -- never stats.ncaa.org, never live ESPN.

Usage::

    python python/ncaa_wbb_07_player_xwalk_build.py --seasons 2025 2026
    python python/ncaa_wbb_07_player_xwalk_build.py --seasons 2026 --dry-run
"""

import pathlib

from sportsdataverse.scrape.ncaa import espn_player_xwalk as _engine

LEAGUE = "wbb"
REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[1])

if __name__ == "__main__":
    _engine._main(LEAGUE, REPO_ROOT)
