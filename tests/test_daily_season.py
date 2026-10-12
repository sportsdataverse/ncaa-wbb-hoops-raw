"""Offline check of daily_wbb_scraper.sh's END-year season rule (no scrape).

Same rule as ncaa-wbb-hoops-data scripts/cron_publish_current.sh: Aug-Dec of
year Y -> season Y+1; Jan-Jul -> season Y. WBB_SEASON overrides it.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_wbb_scraper.sh"


def _season(tmp_path, year, month, override=None):
    fake = tmp_path / "date"  # stub: answers only the +%Y / +%m calls
    fake.write_text(f'#!/bin/sh\ncase "$1" in +%Y) echo {year};; +%m) echo {month:02d};; esac\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "WBB_SEASON"}
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    if override:
        env["WBB_SEASON"] = override
    out = subprocess.run(
        ["bash", str(SCRIPT), "--print-season"], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    ("year", "month", "season"),
    [(2026, 1, "2026"), (2026, 7, "2026"), (2026, 8, "2027"), (2026, 10, "2027"), (2026, 12, "2027")],
)
def test_end_year_rule(tmp_path, year, month, season):
    assert _season(tmp_path, year, month) == season


def test_env_override_wins(tmp_path):
    assert _season(tmp_path, 2026, 10, override="2025") == "2025"
