# stats.ncaa.org scraping — everything we know (NCAA WBB raw)

Hard-won operational knowledge for the NCAA raw scraper. Ported 2026-08-07
from the MBB sibling (`../../hoopR-dev/ncaa-mbb-hoops-raw/docs/SCRAPING_NOTES.md`,
last updated 2026-08-01) and adapted for WBB. Same site, same Akamai
bm-verify, same shared engine (`sportsdataverse.scrape.ncaa`), same transport
stack, same proxy vendors, same measured rate limits — every measurement
below was taken live on the MBB campaigns unless marked WBB. New transport
findings should land in BOTH copies; WBB-only findings live here.

**READ THIS WHOLE FILE BEFORE ANY SCRAPE RUN.** The 2026-08-01 MBB campaign
burned ~3 hours re-deriving facts already recorded in the sibling copy.

---

## What is WBB-specific

**Repo layout** (identical shape to MBB, `wbb/` instead of `mbb/`):

```
wbb/schedule_master.parquet       # discovered contest_ids per season
wbb/raw/{season}/{id}.json.gz     # captured 3-page bundles (the checkpoint)
wbb/json/{id}.json                # parsed per-contest output
wbb/schedules/{html,json,parquet}/...   # reference trees (see README)
wbb/rosters/{html,json,parquet}/...
wbb/teams/{html,json,parquet}/...
wbb/team_rosters/{season}/{team_id}.json
wbb/.discover/{season}/{team_id}.json   # per-team discovery checkpoints (gitignored)
```

**Launchers** (`scripts/`):

| Script | Stage |
|---|---|
| `run_canary.sh` | score proxy vendors against the bm-verify canary |
| `run_discover.sh` | season -> contest_ids (fans out `DISCOVER_WORKERS`, default 12, then one merge pass) |
| `run_capture.sh` | contest_ids -> raw bundles |
| `run_parse.sh` | raw bundles -> json (fans out `PARSE_WORKERS`, default 12; offline) |
| `run_rosters.sh` | season team rosters (with stats.ncaa.org player ids) |
| `run_datasets.sh` | compile the season reference parquets (offline, not sharded) |
| `run_wbb_backfill.sh` | one season, discover -> capture -> parse |
| `run_wbb_backfill_range.sh` | multi-season campaign, newest-first, chunked + cooldowns |
| `run_reference_backfill.sh` | reference-only campaign (schedules/rosters/teams) |
| `run_autocommit.sh` | settle-aware incremental commit(+push) during a campaign |

**Crosswalk coverage.** The bundled WBB crosswalk
(`sportsdataverse/wbb/data/ncaa_teamids_wbb.csv`) now covers
**2009-10 .. 2025-26**. `run_wbb_backfill.sh` refuses seasons outside
`MIN_SEASON..MAX_SEASON` with the accurate crosswalk-coverage cause rather
than letting `discover_season()` raise its generic "crosswalk drift"
`ValueError`. **Bump `MAX_SEASON` in that script when the crosswalk grows** —
on 2026-08-01 a stale guard (still 2025) refused season 2026, and the range
driver read the rc=2 as a capture hard-stop and burned the round on
cooldowns.

**League selection.** `ncaa_discover.discover_season` /
`ncaa_rosters.capture_rosters` take `league="wbb"` (default) or `"mbb"` and pick
the matching crosswalk from `_TEAM_ID_CROSSWALKS`. The page parsers, the fetch
layer, and the canary are league-agnostic — `sportsdataverse.wbb.wbb_ncaa_fetch`
re-exports the mbb fetch core *by reference*, and contest ids at stats.ncaa.org
are one sport-agnostic namespace.

**Period model.** WBB pbp ships one table per quarter (4 regulation periods,
10 min each, 5-min OTs — `_WBB_PERIOD_MODEL = (4, 600, 300)`), where MBB
ships one table per half. `parse_bundle(..., league="wbb")` (the default)
selects it automatically; nothing else in the pipeline changes.

**Credentials.** `canary_vendors.toml` (gitignored) holds live proxy creds;
`canary_vendors.toml.example` is the committed template. Never commit the
filled-in file. Per-league overrides go in the env, not in code.

---

## 2026-08-01 — multi-season campaign: session ceiling, tolerant discovery, port pool (MBB)

MBB backfill campaign 2025→2010 (`run_mbb_backfill_range.sh` there;
`run_wbb_backfill_range.sh` here is the same driver). Three failed launches
before a stable recipe; every fix below is committed in the shared engine.

**The winning stack (use this, don't re-derive):**

- **Transport:** patchright new-headless + real Chrome UA (unchanged from 07-16).
- **Proxy pool:** `decodo_patchright` vendor in `canary_vendors.toml` holds
  **20 port-pinned sticky endpoints** `us.decodo.com:10001–10020`.
  **Geo-canary PASSED 2026-08-01**: all sampled ports egress US residential
  (AT&T / Verizon / T-Mobile / Spectrum / Frontier). This CORRECTS the 07-16
  note "port-based `:10001` = random-geo Spain" — that applied to a different
  hostname/cred; the `us.decodo.com` host pins country=US. Port style beats the
  single `gate.decodo.com:7000 -session-` URL for a pool: 20 independent sticky
  IPs, each auto-refreshed by Decodo, rotation = pick another port.
- **Discovery rides the same vendor seam**: `NCAA_VENDOR=decodo_patchright`.
  Team pages are bm-verify-challenged like game pages; the ProxyBonanza
  datacenter pool stopped clearing them entirely.

**Failure modes found (and their fixes, all in the shared engine):**

1. **Sticky-session aging ceiling (~70 min).** One browser context on one sticky
   session serves a whole ~350-team discovery sweep; past ~40–65 min bm-verify
   stops clearing on that session and every retry re-fails the same IP. Fix:
   an exhausted solve **closes the browser context**, and every (re)launch
   **re-mints the `-session-` id** in a gate-style username — with the port
   pool, the fetcher's rotation moves to a different port = different IP.
   Matches the 07-13 observation (`31.14.9.13` died at ~70 min) — this is a
   session/IP property, not a code bug.
2. **Hard-stop discovery is mathematically doomed.** bm-verify clearing flakes
   ~5%/page even on a healthy vendor; a 350-page sweep that aborts on first
   failure completes with probability ≈ 0.95^350 ≈ 0. Fix: per-team retries
   (3), skip-after-retries, abort only on **5 consecutive** failed teams
   (real-ban signature). Skips are nearly lossless — every game appears on TWO
   teams' pages.
3. **Cold-start flake is worst on the first navigation** of a fresh profile
   (warm-up + range-script discover retry with ban cooldown; the range script
   must `continue`, not `break`, on a failed discover or the season silently
   never backfills).
4. **Orphan processes from stopped tasks** (bash children survive TaskStop) cause
   store collisions. Kill by `Name -match "^(python|chrome)"` + script-name
   pattern only — a broad CommandLine match self-kills the cleanup shell.

**Worker ceiling CORRECTED (user-verified):** the "WORKERS 1–2 max, 4 = ban"
rule in §5 was measured on the shared ProxyBonanza datacenter pool — it is
**pool-relative, not absolute**. With per-worker DISJOINT sticky residential
ports, up to 8 workers have run clean (request-session runs). What matters is
**per-IP pacing**, not process count. `_vendor_fetcher` shards the port
pool by worker index (`shard_i/shard_n` — offset rotation) so parallel workers
never pile onto one port; launcher cap raised to 16 (keep ≥2 ports/worker).

**Resumability:** BOTH stages are disk-checkpointed. Capture always was
(file-exists per contest). Discovery checkpoints each swept team page to
`{league}/.discover/{season}/{team_id}.json` — an aborted sweep resumes
instead of restarting (the 08-01 round-1 abort threw away 67 min of team
pages; never again). Delete that dir to force a fresh sweep.

**MBB 2025 season result (2026-08-01):** 6,293/6,296 captured (3 contests are
pageless — cancelled games), all parsed. **4.5% of games (282) parse to empty
`lineups`+`shots` — RESOLVED as an upstream data limitation, not a defect:**
all 282 are games vs non-NCAA opponents (NAIA etc.) whose individual_stats
page carries only ONE team block. `parse_team_name` requires both team
titles, so the engine cannot build 5-on-5 stints — correctly. `pbp`/`box`/
`possessions` still land for these games. Expect the same few-percent rate
every season, WBB included (non-NCAA opponents exist here too).

**Campaign shape:** `run_wbb_backfill_range.sh START END` — seasons descending,
per-season rounds of (discover-if-needed → capture CHUNK=1400 → parse), cooldowns
between chunks (300 s) and after hard stops (1800 s), `MAX_ROUNDS=12`/season.
All knobs env-only. Watch: `tail -f logs/backfill_range_<ts>.log`. Scale
(MBB-measured): ~90–95k games 2010–2025 ≈ 75–80 h at ~1200 bundles/hr serial;
N workers on disjoint ports multiply that (keep per-IP volume ≤ ~1400/session
and canary first). WBB seasons are ~6,000 contests each — same order.

---

## 2026-07-16 — bm-verify SOLVED (supersedes the "buy a better service" framing below)

**A local browser DOES clear stats.ncaa.org bm-verify — cheaply, no paid service.**
Winning transport, proven live (10-game canary PASS: 19/20 pages clean, ~11s/page
warm, one sticky US residential IP, zero degradation):

- **patchright** — anti-detect Playwright fork (`navigator.webdriver=false`,
  `Runtime.enable` CDP leak patched). `uv pip install patchright && patchright install chromium`.
- `launch_persistent_context(headless=False, args=["--headless=new"])` → real
  GPU/ANGLE render (verified RTX 3090 D3D11, **not** SwiftShader). Needs a real-GPU host.
- **`user_agent` = a real Chrome UA** — **THE fix.** New-headless leaks `HeadlessChrome`
  in `navigator.userAgent`; that single tell was why every prior browser attempt failed.
- **US residential sticky proxy.** Decodo: `user-<sub>-country-us-session-<id>@gate.decodo.com:7000`
  (the port-based `:10001` cred handed out random-geo Spain → flagged; see the
  2026-08-01 correction above — `us.decodo.com` ports pin US).
- **Navigate once per URL**, then poll the in-page `fetch()` until `_abck` mints;
  **nav_timeout ≥45 s** (residential is slow; 25 s times out the cold solve). Cold
  ~45–80 s, warm ~11 s (the cookie is reused across pages in the same browser).

**This corrects the sections below.** The datacenter finding stands (§4/§7 —
ProxyBonanza is datacenter, ASN-confirmed), but the remedy is **not** a managed
browser or a paid sensor API. Ruled out en route: datacenter proxies (403), vanilla
Playwright new-headless (challenge — `webdriver=true`), `curl_cffi` fingerprint-only
`chrome146` (2310-byte challenge — the site REQUIRES JS-sensor execution, so JA3Proxy
and the OSS "fingerprint-only HTTP client" class can't work either), and OSS sensor
generators (none is a working/safe/maintained Python web generator).

**Cost:** ~$9–45 per ~6300-game season (patchright free + residential ~$3/GB).
Tooling: `python/ncaa_canary.py` + `scripts/run_canary.sh`.

---

## 1. The access model

Two page classes, and they behave completely differently:

| Class | Pages | Transport |
|---|---|---|
| Un-challenged | `/`, `/team/{id}`, `/season_divisions` (~10–20 KB) | `curl_cffi` (Chrome impersonation) works |
| **Game detail** | `.../play_by_play`, `.../box_score`, `.../individual_stats` | Akamai **bm-verify** JS proof-of-work. `curl_cffi` **cannot** clear it — needs the Playwright browser |

`curl_cffi` clears the TLS/JA3 edge but cannot run the sensor. Game detail requires
the browser transport (Chromium, `--headless=new` — old headless's SwiftShader
WebGL renderer is an Akamai tell).

## 2. Response classes — the classification that broke us

There are **four**, not two. Misreading this caused the entire MBB outage:

| Class | Shape | Detect via |
|---|---|---|
| Real content | **100 KB+** (pbp 135–144 KB, box 285–319 KB, individual_stats 219–228 KB) | size + `<tr>` count |
| Ban | HTTP **403**, ~413 B, `Access Denied` | status + ban marker |
| Unsolved — **navigation** | ~2310 B interstitial carrying `bm-verify` / `_abck` | markers |
| Unsolved — **in-page XHR** | **THIN stub**, no markers, no ban text | **size only** |

**The asymmetry that hides the bug:** Akamai answers a *navigation* with the full
marker-bearing interstitial (what `curl_cffi` sees, cookie-less), but answers an
*in-page `fetch()`* carrying an invalid `_abck` with a thin stub.

**The stub size VARIES** — observed at **15 bytes** (`NCAA Statistics`) and **411 bytes**
in the same session. Never match its signature; use a size floor. No real
stats.ncaa.org page is under 1 KB.

Why it slipped through: the stub is HTTP **200** with **no ban marker**, so
`_ban_check` calls it `"clean"`. The fetch layer returned it as a *successful fetch*.
Callers rejected it as too-small and logged `"challenge not cleared"` — while the
fetcher, believing it had succeeded, never re-solved and never rotated.

> ⚠️ `ncaa_capture`'s `"challenge not cleared"` warning is emitted for **any** page
> failing `_is_clean`. It is not evidence of a challenge. It misled a whole
> debugging session. Do not trust that log line — inspect the actual bytes.

## 3. Bugs found and fixed (all merged 2026-07-16, in the shared engine)

| # | Bug | Consequence | Fixed in |
|---|---|---|---|
| 1 | `_ensure_page` early-returned if a page existed; **Playwright binds the proxy at launch** | Browser egressed from its FIRST proxy forever while `_proxy_idx` "rotated" to no effect. One IP absorbed a whole run | sdv-py #264 |
| 2 | `_solve_challenge` set `_challenge_solved = True` after a **blind wait**, never verifying | A failed solve latched "solved"; every later fetch returned unsolved responses **forever** (1485 in one run) — the storm that earned the ban | sdv-py #266 |
| 3 | Unsolved responses classified as success (see §2) | Fetcher never rotated off a non-solving IP | sdv-py #266 |
| 4 | Rotation only on failure — i.e. **after** the IP was already dead | No way to retire an IP while healthy | sdv-py #264 (`rotate_every`, default 200) |
| 5 | Rotation cycled back into known-banned proxies | Re-earned 403s | sdv-py #264 (`_dead` set) |
| 6 | No breaker on a failure storm | Hammered **1262 failures in one hour**, zero yield | mbb-raw #1 (`max_consecutive_failures`, default 25) |
| 7 | Launchers ended with `echo "EXIT=..."` → **always exited 0** | Backfill reported "DONE ✓" on a ban | mbb-raw #1 (`exit "${rc}"`) |
| 8 | `inf` accepted for `SDV_PY_NCAA_ROTATION_BACKOFF` → `time.sleep` OverflowError | — | sdv-py #264 (`math.isfinite`) |
| 9 | No chunking | Couldn't bound a session | mbb-raw #1 (`--max-contests`) |

## 4. Measured behavior (MBB campaigns; same site + transport)

**Capture rate (healthy):** ~1200 bundles/hr, 1 worker, ~20/min. Real payloads
135–319 KB/page.

**IP lifetime — the numbers collapsed between runs:**

| Date | IP | Volume before it stopped solving | Subnet outcome |
|---|---|---|---|
| 2026-07-13 | `31.14.9.13` | **~1412 bundles / ~4236 requests** (70 min) | subnet mostly survived (24/25 healthy) |
| 2026-07-16 | `23.239.174.2` | **~35 bundles / ~105 requests** (3 min) | **entire /24 → 403** |
| 2026-07-16 | `154.81.58.x` | ~20 bundles | stopped solving (411 B stubs), incl. untouched IPs w/ fresh browsers |

A ~40× collapse in tolerance with no code change on their side. **Cause not
established** — see Open questions.

> **This falsifies the 2026-07-13 rate-probe conclusion.** That probe concluded
> "paced requests are SAFE; the ban was a BURST artifact, not a volume limit" and
> budgeted a season at ~6h on 1 worker / ~50 req/min. On 2026-07-16 a single
> self-paced worker at ~20/min stopped solving after **35 games**. Volume/duration
> on one IP matters after all — or something changed on their side between the two
> dates. The probe's *concurrency* finding (1–2 workers OK on a SHARED pool,
> 4 = ban) is untouched; its *"spacing doesn't matter, just go"* conclusion is
> not safe to rely on.

**Do bans lift?** No evidence they do. 26/50 of one pool still 403 after **62 hours**;
`23.239.174.x` still 403 after ~1 hour. Treat IPs as **consumable**.

**Concurrency:** the ProxyBonanza pool refuses ~**10 concurrent** connections
(`ProxyError` storm). **Serial is flawless.** See the 2026-08-01 correction:
the worker ceiling is pool-relative (up to 8 clean on disjoint sticky ports).

## 5. Operational rules

1. **Vendor proxy IPs only — NEVER the residential IP.** (Binding user directive.)
   The home IP cannot be rotated; given bans don't appear to lift, it's the one
   asset you can't replace. The fetcher is proxy-bound by design: no direct-fetch mode.
2. **Canary before scale.** Run `CHUNK=10` and confirm clean captures *before*
   `CHUNK=1500`. A 1500-chunk launched with no canary burned a subnet in 3 minutes.
3. **Worker ceiling is pool-relative** (2026-08-01 correction above): 1–2 on a
   shared/unsharded pool; up to 8 proven clean on disjoint sticky residential
   ports (launcher cap 16, keep ≥2 ports/worker). Per-IP pacing is what binds.
4. **Resume is free** — capture is file-exists based (`wbb/raw/{season}/{id}.json.gz`),
   so re-running skips captured contests. Ctrl-C is always safe.
   - Note: `schedule_master`'s `captured` column is **vestigial** (always `False`);
     resume is purely file-exists.
5. **Pull sdv-py main before running.** The launchers import sportsdataverse from
   the sibling **working tree** via `PYTHONPATH="${SDV_PY}:${ROOT}/python"` and run
   the sibling `.venv` — *not* this repo's locked venv (that one is for tests/CI).
   If that checkout sits on someone's feature branch, the backfill silently runs
   old code. This nearly re-ran the IP-burning version on MBB.
6. **Raw data IS committed** (the plan mandates it; the `-data` ingest reads
   `raw.githubusercontent.com/.../ncaa-wbb-hoops-raw/main/wbb/json/{cid}.json`).
   Do **not** gitignore `wbb/`. `.gitattributes` marks `.json.gz` binary —
   verified on MBB: 25/25 bundles read back out of git pass `gzip -t`.

## 6. Data facts (MBB season 2026, for scale expectations)

- Per game: pbp 400–540 rows, lineups 34–58, player_box 16–24, team_box 2,
  shots 104–177, possessions 133–198. (WBB: same families; 4 quarters not halves.)
- ~3–4.5% of games have empty lineups+shots (single-title individual_stats
  pages vs non-NCAA opponents) — parser swallows per-family, the game still lands.
- Sizes: bundle ~52 KB gzipped, parsed json ~850 KB. **JSON compresses 20.8:1**,
  so a season of json is tens of MB in git, not GB.
- End-to-end verified on MBB: `ingest.read_parsed` pulls a published game over
  HTTP and returns the full 7-key dict.

## 7. Open questions

1. **Can ProxyBonanza IPs sustain this at all?** Fresh IPs solved for only 20–35
   games before stopping (07-16). The pool type was later ASN-confirmed
   datacenter; the `decodo_patchright` residential port pool is the working
   answer (08-01). Keep the canary scorecard workflow before trusting any vendor.
2. **Ban vs. can't-solve.** Not established whether the 403s are true per-IP/subnet
   bans, the whole provider ASN being flagged, or bm-verify simply failing on
   datacenter IPs. These imply different remedies.
3. **Does anything decay?** Every check so far says no, but the longest observation
   is 62 hours. Worth one cheap probe before assuming permanence.

## 8. Debugging lessons (process)

- **The failure message lied.** `"challenge not cleared"` is emitted for any not-clean
  page. Three successive diagnoses were wrong because they trusted it instead of the
  bytes. **Dump the actual response** (status, length, first 300 chars) first.
- **Don't state a hypothesis as a conclusion.** "Subnet ban" was asserted on
  circumstantial evidence and was probably wrong; the challenge-not-passing read fit
  better and came from the user.
- **Canary before scale**, and **fail fast** — the engine raises loudly rather
  than grinding, which makes a canary cheap.
- **Exit codes**: a trailing `echo` makes a shell script exit 0. It masked a ban as
  success in the launchers, and then again in an ad-hoc `cmd; echo "EXIT=$?"` wrapper.

## 9. Current state (WBB, 2026-08-07)

- **Reference backfill COMPLETE and committed**: schedules + rosters + teams,
  seasons 2011–2026 (2026: 359 schedule + 359 roster pages, landed 2026-08-01).
- **Pbp capture: ZERO bundles yet.** The 2026-08-01 campaign's first round died
  on a stale season guard (`MAX_SEASON=2025` while the crosswalk already covered
  2025-26); the guard is fixed. `schedule_master` holds 2011–2026 (6,019
  contests for 2026 alone).
- All engine fixes above are live in the shared `sportsdataverse.scrape.ncaa`
  (this repo's `python/` modules are league-binding shims over it).
- Next: canary (`CHUNK=10`) → `run_wbb_backfill_range.sh` on the
  `decodo_patchright` port pool, per the campaign shape above.
