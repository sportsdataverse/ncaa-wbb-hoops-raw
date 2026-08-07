"""Offline checks for the canary's pure logic: classifier + vendor gating.

No network, no browser. Run: pytest python/test_ncaa_canary.py
(with PYTHONPATH="${SDV_PY}:${ROOT}/python", same as scripts/run_canary.sh).
"""

from __future__ import annotations

from ncaa_canary import (
    BAN,
    CHALLENGE,
    CLEAN,
    STUB,
    classify,
    classify_error,
    _vendor_ready,
)

_REAL_PAGE = "<html>" + "<tr><td>play</td></tr>" * 100 + "</html>"  # >1 KB, no markers


def test_classify_clean() -> None:
    assert len(_REAL_PAGE) >= 1000
    assert classify(_REAL_PAGE) == CLEAN


def test_classify_markerless_stub() -> None:
    # The 15-byte in-page XHR stub -- no marker, only the size floor catches it.
    assert classify("NCAA Statistics") == STUB


def test_classify_challenge_beats_size_floor() -> None:
    # A tiny body carrying a sensor marker is a CHALLENGE, not a STUB.
    assert classify("<html>bm-verify sensor</html>") == CHALLENGE


def test_classify_ban_beats_size_floor() -> None:
    # A small ban page is a BAN, not a STUB -- ban markers are checked first.
    assert classify("Access Denied") == BAN


def test_classify_error_buckets() -> None:
    assert (
        classify_error(
            RuntimeError("NCAA fetch failed: every proxy in the pool is banned")
        )
        == BAN
    )
    assert (
        classify_error(RuntimeError("bm-verify not passed after 2 attempts"))
        == CHALLENGE
    )
    assert classify_error(Exception("Read timed out")) == "timeout"
    assert classify_error(Exception("some other boom")) == "error"


def test_vendor_ready_skips_placeholders() -> None:
    assert _vendor_ready(
        {"type": "proxy_browser", "proxies": ["http://USER:PASS@gw:1"]}
    )
    assert _vendor_ready({"type": "unblocker_zyte", "api_key": "YOUR_ZYTE_API_KEY"})
    assert _vendor_ready({"type": "unblocker_proxy", "proxy": ""})
    assert _vendor_ready({"type": "bogus"})


def test_vendor_ready_accepts_real_creds() -> None:
    assert (
        _vendor_ready(
            {"type": "proxy_browser", "proxies": ["http://u123:p456@1.2.3.4:8080"]}
        )
        is None
    )
    assert _vendor_ready({"type": "unblocker_zyte", "api_key": "abc123realkey"}) is None


def test_example_config_every_vendor_is_skipped() -> None:
    # Regression: the shipped .example must skip ALL vendors (placeholder creds),
    # or a dry run tries to connect to fake hosts and hangs on browser timeouts.
    import pathlib
    import tomllib

    example = (
        pathlib.Path(__file__).resolve().parents[1] / "canary_vendors.toml.example"
    )
    conf = tomllib.loads(example.read_text(encoding="utf-8"))
    for vendor in conf["vendor"]:
        assert _vendor_ready(vendor), (
            f"{vendor.get('name')} should be skipped in the example config"
        )
