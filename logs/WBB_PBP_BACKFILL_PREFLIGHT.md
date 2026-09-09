# NCAA WBB PBP backfill — preflight findings (2026-08-01)

Scope: backfill play-by-play for `ncaa-wbb-hoops-raw` via `stats.ncaa.org`,
using the 50 Decodo US-residential sticky ports (`us.decodo.com:10001–10050`).

## Job size

`wbb/schedule_master.parquet` — 88,590 contests, seasons 2011–2026.
**`captured == False` for all 88,590. WBB PBP capture is at 0%.**

| season | contests | | season | contests |
|---|---|---|---|---|
| 2011 | 5,461 | | 2019 | 5,658 |
| 2012 | 5,464 | | 2020 | 5,433 |
| 2013 | 5,484 | | 2021 | 3,823 (COVID) |
| 2014 | 5,588 | | 2022 | 5,511 |
| 2015 | 5,602 | | 2023 | 5,821 |
| 2016 | 5,618 | | 2024 | 5,897 |
| 2017 | 5,623 | | 2025 | 5,960 |
| 2018 | 5,628 | | 2026 | 6,019 |

At the MBB-measured ~1,200 bundles/hr serial, 88.6k games ≈ **74 h single-worker**;
N workers on disjoint ports divide that.

## The proxies are the easy part

The MBB notes (`ncaa-mbb-hoops-raw/docs/SCRAPING_NOTES.md`, 2026-08-01 entry)
already define the winning stack, and it is *already* Decodo port-pinned:

- `decodo_patchright` vendor holds **20** ports `us.decodo.com:10001–10020`.
- Geo-canary PASSED 2026-08-01: all sampled ports egress US residential
  (AT&T / Verizon / T-Mobile / Spectrum / Frontier).
- `_vendor_fetcher` **already shards the port pool by worker index**
  (`shard_i/shard_n`), i.e. sticky-per-worker is the existing behavior.
- Rule: keep **≥2 ports per worker**; launcher cap 16.

So the 50 new ports are a **pool expansion 20 → 50**, not a new integration.
50 ports at ≥2/worker permits up to 25 workers (launcher cap 16 binds first).

Verified this session: port 10001 egresses `166.199.149.51` (mobile/residential)
vs droplet `161.35.59.239` (DigitalOcean). Creds stored `/root/.sdv-proxies` (0600).

## Blockers for running on THIS droplet

1. **All runner scripts hardcode the Windows dev box.** Six scripts point at
   `C:/Users/saiem/Documents/GitHub-Data/sdv-dev/sdv-py/.venv/Scripts/python.exe`:
   `run_wbb_backfill_range.sh`, `run_wbb_backfill.sh`, `run_canary.sh`,
   `run_discover.sh`, `run_capture.sh`, `run_datasets.sh`. They cannot run on Linux as-is.
2. **`canary_vendors.toml` is absent** on the droplet (gitignored; lives on the dev box).
3. **patchright + Chromium are not installed** here (no `~/.cache/ms-playwright`).
4. **GPU fingerprint risk — the real unknown.** The proven transport is
   `patchright launch_persistent_context(headless=False, args=["--headless=new"])`
   with a real Chrome UA, and the notes state it renders via **real GPU/ANGLE
   (verified RTX 3090 D3D11, *not* SwiftShader) — "Needs a real-GPU host."**
   This droplet has a **virtio GPU** (`/dev/dri/renderD128`), which means
   llvmpipe/SwiftShader software rendering. A software-renderer WebGL string is
   exactly the class of headless tell the notes say defeated earlier attempts.
   **Unproven either way on this host — must be canaried before any launch.**

## Reprocess-skill sizing (if it runs here)

- Free RAM **8 GB** of 15 GB; `nproc` **8** → `cpu_count - 2 = 6`.
- `peak_gb_per_worker` for a patchright Chromium context: **NOT YET MEASURED on
  this host** (skill step 2). Assume ~1.2 GB pending measurement.
- `safe_workers = floor(8 × 0.7 / 1.2) = 4` → `workers = min(4, 6, 25) = **4**`.
  RAM is the binding constraint, not ports and not the launcher cap.
- Disk: 404 GB free on `/mnt/sdv_repos` — not a constraint.
- Resumability: **already disk-checkpointed** both stages (capture = file-exists
  per contest; discovery = `{league}/.discover/{season}/{team_id}.json`).

## Recommended next step

Install patchright + Chromium into a droplet venv and run the existing 10-game
canary through one Decodo port. It is bounded, cheap, and decisively answers
blocker #4. If it PASSES, the droplet is the right host (headless, 74 h job).
If it FAILS on the GPU tell, the campaign belongs on the dev box.

---

# Work performed (2026-08-01, droplet)

User elected to port to the droplet and launch WITHOUT a separate canary.
Launch is bounded (`--max-contests 25`, 1 worker) so a fingerprint rejection
surfaces in minutes rather than hours.

## 1. Transport installed

`uv pip install --python .venv/bin/python "patchright>=1.50"` into
`/mnt/sdv_repos/sdv-py/.venv` + `patchright install chromium`.
patchright is already a declared sdv-py dependency (`pyproject.toml:145`), so
this installs what the project expects. **`uv.lock` was NOT modified** (verified).

Browser smoke test on this host:

| probe | value |
|---|---|
| launch | OK |
| `navigator.webdriver` | `False` (patchright working) |
| UA | real Chrome 149 (no `HeadlessChrome` leak) |
| WebGL renderer | **`ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)`** |

The renderer is the **measured** confirmation of blocker #4 — the notes' proven
config was real GPU/ANGLE D3D11, explicitly "not SwiftShader". Launch proceeded
anyway by user decision.

## 2. Vendor config

`canary_vendors.toml` (gitignored, `chmod 600`) — vendor `decodo_patchright`,
type `proxy_patchright`, **50 port-pinned URLs** `us.decodo.com:10001–10050`.
These carry no `-session-` token, so `_vendor_fetcher`'s per-run session re-mint
is a deliberate no-op: the **port** is the sticky pin. Worker sharding
(`shard_i/shard_n` offset rotation) gives disjoint ports per worker for free.

## 3. Runners ported to Linux (5 files)

Pattern applied — dev-box behavior preserved, droplet enabled via env:

```sh
SDV_PY="${SDV_PY:-C:/Users/saiem/.../sdv-py}"     # was hardcoded
if [ -x "${SDV_PY}/.venv/bin/python" ]; then PY="${PY:-${SDV_PY}/.venv/bin/python}"
else PY="${PY:-${SDV_PY}/.venv/Scripts/python.exe}"; fi
```

`run_capture.sh`, `run_parse.sh`, `run_discover.sh`, `run_wbb_backfill.sh`,
`run_wbb_backfill_range.sh`.

**Real bug fixed (not just a path):** `run_capture.sh` unconditionally required
`PROXYBONANZA_API_KEY` + `PROXY_PKG` and `exit 2`-ed when absent — even with
`NCAA_VENDOR=decodo_patchright` set, where ProxyBonanza is never touched.
`PROXYBONANZA_API_KEY` is not in the droplet `.Renviron`, so every Decodo run
would have died at line 19. `run_discover.sh` already had the correct guard
(`if [ -z "${NCAA_VENDOR:-}" ]`); that guard is now mirrored into capture.

**New file:** `scripts/droplet_wbb_capture.sh` — thin Linux wrapper exporting
`SDV_PY` + `NCAA_VENDOR` and delegating to `run_capture.sh`. tmux/nohup ready.

**File modes:** this repo has `core.fileMode = true` (unlike the repos in the
earlier chmod sweep), so exec bits show as real diffs. Only the 5 capture-path
scripts were left `+x`; `run_canary.sh`, `run_datasets.sh`,
`run_reference_backfill.sh`, `run_rosters.sh` were reverted to 644 to keep the
diff free of mode-only noise.

## 4. Launch

```sh
cd /mnt/sdv_repos/ncaa-wbb-hoops-raw
nohup ./scripts/droplet_wbb_capture.sh --season 2025 --max-contests 25 &
tail -f logs/capture_<ts>.log
```

Season 2025 chosen: `run_wbb_backfill.sh` refuses >2025 (bundled WBB crosswalk
`ncaa_teamids_wbb.csv` has no 2025-26 row), even though `schedule_master` holds
6,019 season-2026 contests. Extending the crosswalk is a separate sdv-py change.

## 5. MEASURED RESULTS (bounded run, season 2025, 1 worker)

### bm-verify CLEARS on this droplet — blocker #4 did not materialize

Bundles landed steadily from a SwiftShader/virtio host:

| bundle | time | gap |
|---|---|---|
| 5722020 | 23:30:16 | ~51 s (cold solve) |
| 5722021 | 23:31:18 | 62 s |
| 5722022 | 23:32:01 | 43 s |
| 5722023 | 23:32:34 | 33 s |

**This corrects `ncaa-mbb-hoops-raw/docs/SCRAPING_NOTES.md`**, which states the
patchright transport "Needs a real-GPU host" and calls out SwiftShader as the
non-working config. A virtio GPU falling back to
`ANGLE (... SwiftShader Device (Subzero) ...)` clears Akamai bm-verify fine.
The load-bearing tells appear to be `navigator.webdriver=false` + a real Chrome
UA, not the WebGL renderer string. **Write this back to the canonical notes.**

### Throughput is ~12x below the planning figure

Measured: **n=5, span 180 s, mean 45.0 s/bundle → ~80 bundles/hr/worker.**
The notes budget ~1,200 bundles/hr serial. Whether the gap is the SwiftShader
software-render penalty, residential-proxy latency, or an over-optimistic figure
in the notes is not yet established — but the campaign ETA must use the measured
number, not the documented one.

### Peak RSS — the reprocess-skill input that was missing

**1,403 MB (1.37 GB)** peak for one capture python + all its chrome children.

Re-running the sizing formula with measured values (free 8 GB pre-launch):

```
safe_workers = floor(8 GB * 0.7 / 1.37 GB) = 4
workers      = max(1, min(4, cpu_count-2 = 6, port_cap = 25)) = 4
```

**RAM is the binding constraint at 4 workers** — not the proxy pool (50 ports
would allow 25) and not the launcher cap (16). The notes' "8 workers ran clean"
is a *per-IP pacing* result and remains true; this box simply lacks the RAM for
8 browsers.

### Revised campaign ETA (measured, 4 workers ≈ 320 bundles/hr)

| scope | contests | ETA @ 4 workers |
|---|---|---|
| season 2025 only | 5,960 | **~19 h** |
| seasons 2010–2025 (crosswalk range) | ~82,600 | **~11 days** |
| all of `schedule_master` (2011–2026) | 88,590 | **~12 days** |

The notes' "74 h" figure assumed 1,200 bundles/hr and does not hold here.

## 6. 8-WORKER CAMPAIGN — launched 2026-08-01 23:42, season 2025

Launched at user direction with an OOM shield (see below). `tmux` session `wbb`,
`scripts/droplet_wbb_campaign.sh 2025`, 8 disjoint shards of 745 contests.

### Why the shield was needed (discovered pre-launch)

The droplet is NOT idle. Concurrently resident: postgres (11 procs, sdv-db),
`sdv-db-api`, `sdv-orch-flows`, `sdv-orch-prefect`, a GitHub Actions runner, and
a **3.1 GB `full_refresh.py` job from a different Claude session**
(scratchpad `4e17802a-…`). Available RAM was **5.5 GB**, not the 8 GB measured
earlier. 8 chrome workers nominally want ~11 GB, and the kernel OOM killer
targets the largest RSS — postgres, not the scrapers.

Fix: `choom -n 1000 -- ./scripts/run_wbb_backfill.sh`. `oom_score_adj` is
**inherited by all children**, so the whole capture tree (python + chrome) is
sacrificed before any production service. Verified: parent 1000, child 1000,
postgres 0. Plus auto-halving 8→4→2→1 on any new kernel OOM kill.

### Measured at 8 workers (22.4 min sample)

| metric | 1 worker | 8 workers |
|---|---|---|
| aggregate rate | 80 /hr | **292 /hr** |
| s/bundle (aggregate) | 45.0 | **12.3** |
| scaling efficiency | — | **46 %** (292 vs 640 linear) |
| OOM kills | — | **0** |
| RAM available during run | — | ~4.3 GB steady |

**8 workers did NOT OOM.** The RSS-sum estimate (8 × 1.37 = 11 GB) badly
overcounted shared Chrome pages; true usage left ~4.3 GB headroom.

**Scaling is sublinear (46 %)** — almost certainly CPU contention: SwiftShader
software rendering is CPU-heavy and there are 8 chrome instances on 8 cores.
Note 8 workers (292/hr) landed close to the *predicted* 4-worker figure
(320/hr), so 4 workers may deliver nearly the same throughput at half the RAM.
Worth measuring if the box gets busier.

### Revised ETA (measured @ 292/hr)

| scope | remaining | ETA |
|---|---|---|
| season 2025 | 5,850 | **~20 h** |
| seasons 2010–2025 | ~82,500 | **~11.8 days** |

### Resumability — verified at the mechanism

`ncaa_capture.py:296-298` states the `captured` parquet column "is never set
True"; real resumability is `ncaa_bundle.is_captured()` checking **disk**
per-contest (`ncaa_capture.py:155`, increments `skipped`). So the
`pending=5960` line is cosmetic and never decreases — do NOT use it as a
progress signal. Count files in `wbb/raw/<season>/` instead.

## 7. CAMPAIGN STOPPED (user request) — 2026-08-02

Killed at **342 / 5,960** bundles for season 2025. Clean shutdown:

| check | result |
|---|---|
| bundles kept on disk | **342** (all valid; resume skips them) |
| orphan python/chrome procs | **0** (the MBB notes' store-collision failure mode avoided) |
| OOM kills, whole campaign | **0** |
| postgres / sdv-db-api | **active** — never at risk (shield held) |
| sdv-db ingest | still running, unharmed |
| RAM available after kill | 2,767 MB → **6,286 MB** |

Kill used the bracket-pattern trick (`ncaa_captur[e]\.py`) — a bare
`pkill -f ncaa_capture.py` self-kills the cleanup shell because the shell's own
command line contains the pattern. That exact trap fired once this session
(exit 144) and is documented in the MBB notes' failure mode #4.

### RESUME — one command, no state to reconstruct

```sh
cd /mnt/sdv_repos/ncaa-wbb-hoops-raw
WORKERS=4 tmux new -d -s wbb './scripts/droplet_wbb_campaign.sh 2025'
tail -f logs/campaign_2025_*.log
```

Resume is free: `is_captured()` checks disk per contest, so the 342 already
captured are skipped. `WORKERS=8` also works (measured stable, 0 OOM), but 4 is
gentler on concurrent sdv-db ingest and — at 46 % scaling efficiency — costs
only ~25-30 % throughput.

### DO NOT REDO (verified this session)

- patchright + Chromium installed in `/mnt/sdv_repos/sdv-py/.venv`; `uv.lock` clean.
- `canary_vendors.toml` written, 50 ports, `chmod 600`, gitignored.
- 5 runners ported to Linux; capture's ProxyBonanza gate made conditional.
- OOM shield verified (parent 1000 → child 1000; postgres 0).
- Resumability verified at the mechanism, not assumed.
- bm-verify **clears on SwiftShader** — the "needs a real-GPU host" claim is wrong.

### OPEN ITEMS

1. **Write the SwiftShader correction into
   `ncaa-mbb-hoops-raw/docs/SCRAPING_NOTES.md`** (canonical). Currently that file
   tells the next person they need a real GPU; measured evidence says otherwise.
   Also worth recording the measured 80/hr (1w) and 292-404/hr (8w) rates against
   its ~1,200/hr figure.
2. Decide host for the full 2010-2025 campaign (~11.8 days at 8 workers on this
   box, competing with production services).
3. Season 2026 (6,019 contests) is blocked on the WBB crosswalk, which stops at
   2025 — a separate sdv-py change.

## Uncommitted state

Nothing has been committed. `git status` shows the 5 modified runners +
untracked `scripts/droplet_wbb_capture.sh`. `canary_vendors.toml` and `logs/`
are gitignored and must never be committed (creds).
