# Axiom Codex Loop — Architecture & Reference

**Last updated:** 2026-03-18

## Overview

Axiom's autonomous research loop runs continuously on the server, executing a 17-step pipeline every ~3 hours. An LLM (GPT-5.4 via OpenAI Codex CLI) acts as the Principal Investigator — analyzing results, awarding credit, retiring completed experiments, triaging errors, designing new experiments, and maintaining system health. No human intervention required for routine operation.

## Master Loop

- **Engine:** `codex exec` with GPT-5.4, medium reasoning effort
- **Cycle gap:** 1.8 hours between cycles
- **Rate limit:** Max 16 cycles per 24-hour sliding window
- **Step timeout:** 2 hours max per step (Steps 6-8 have shorter custom timeouts)
- **Lock file:** PID-based, prevents duplicate instances
- **Execution order:** Steps 0–4 sequential → Steps 5–8 (bridge/dedup/rating/journal) → Steps 9–12 parallel research+deploy → Steps 13–16 sequential
- **Alerting:** Push notifications for rate limits, process failures, resource thresholds

## Steps (17 total, numbered 0–16)

### Step 0 — Security Scan
Detects prompt injection in volunteer-submitted database fields. Verifies file integrity. Does NOT modify code or prompts.

### Step 1 — Health Check
15 checks covering all vital processes and cron jobs:
- Checks 1–9: MariaDB, BOINC daemons, assimilator, Apache, coordinator, alert tunnel, CPU/RAM/disk, RAC update cron, counters
- Check 10: Team credit sync cron
- Check 11: Zombie codex process cleanup
- Check 12: Host backoff reset
- Check 13: Error rate watchdog cron
- Check 14: Patreon patron monitor cron
- Check 15: Experiment compliance lint cron

Cleans stale config locks. Does NOT modify code, prompts, configs, or data.

### Step 2 — Science (Scientific Analysis)
AI-driven analysis of experiment results. Gets the experiment menu via DB query (joins result + workunit tables for families with 10+ completed results today). Never scans the full sanitized results directory (156K+ files). AI chooses its own sample size per experiment using scientific judgment. Reads raw numbers and forms conclusions like a scientist reviewing lab results.

**Decision thresholds:**
- <10 results → SKIP
- Consistent large effect → CONFIRMED
- Mixed/noisy → NO EFFECT or PRELIMINARY
- Opposite of hypothesis → REJECTED

Checks master dedup for experiments already analyzed in prior cycles — skips any with Discovered date before today to prevent re-analyzing old experiments that still receive trickle-in results. Updates findings_summary.txt (current day only). Publishes reports to Git and website.

### Step 3 — Validate & Credit
Credits ALL uncredited completed results via price-per-FLOP market-rate scaling. CPU and GPU FLOPS priced separately based on real hardware market values. GPU tasks credit both the CPU core used AND the GPU. No cap, no floor, no budget pools. Denied results get 0.01 sentinel credit. Retired experiments still get credit (volunteer completed the work).

Anti-cheat is handled by automated systems (verification pair checker, error rate watchdog). This step handles ONLY credit.

### Step 4 — Retire (Mark Only)
Retires experiments that have collected enough data. ONLY marks them in retired_experiments.txt — does NOT abort WUs. Aborts happen later in Step 13 (Cleanup). The work generator continuously creates fresh WUs from the config, so there is no empty-pool window when retired WUs get aborted.

### Step 5 — Bridge (python3, not codex)
Converts findings_summary.txt into a results format that dedup can consume. Runs as plain python3, not a codex exec invocation. Takes ~1 second.

### Step 6 — Dedup (10 min timeout)
Deduplicates today's findings into a daily dedup file. Does NOT write to the master findings file directly. After writing the daily file, calls a rebuild script that MERGES daily files into the master — preserves curated/enriched conclusions, only updates entries with more seeds AND richer conclusions. Entries older than 14 days are dropped.

**Freeze rule:** Master entries discovered before today are NEVER overwritten, even if daily has more seeds. This prevents the Science step's trickle-in re-analysis from corrupting older entries.

### Step 7 — Rating / Significance Scoring (30 min timeout)
AI-scores each deduplicated finding 0–100 for scientific significance. Considers effect size, sample size, consistency, novelty, theoretical relevance, generalizability, and conclusiveness. Only scores findings from today without existing scores.

### Step 8 — Science Journal / Long-term Memory (10 min timeout)
Reads today's published findings and appends a 1-paragraph summary to today's journal file. Multiple cycles per day = multiple entries (append-only, never overwrites). New file created each day automatically via date in filename.

### Step 9 — CPU Research (Design & Write Only)
Designs new numpy experiment scripts for STEM research. Checks novelty against retirement registry and existing findings. Writes scripts and records filenames in a manifest for Step 10. Does NOT test, deploy, or retire.

**Key constraints:**
- **One seed per workunit:** Each WU runs ONE deep computation with iterative deepening. Aggregation happens at the BOINC level across hundreds of WUs with different seeds.
- **Iterative deepening mandatory:** Bisection experiments use `while time < deadline: N *= 2`. Training experiments use `while time < deadline: epoch += 1`. NEVER fixed epoch counts.
- **Resource limits:** Peak RAM under 2GB. CPU scripts: numpy + stdlib only. No scipy, torch, matplotlib.
- **I/O limit:** Never scans full sanitized directory (156K+ files). Reads dedup + significance scores for novelty checks.
- **Lock-aware:** Uses flock on config file to avoid races with GPU pipeline.

Runs in PARALLEL with GPU pipeline (Steps 11→12).

### Step 10 — CPU Dry-Run & Deploy
Tests, fixes, and deploys CPU experiment scripts written by Step 9. For each new script: py_compile syntax check, then 138-second dry-run. If script crashes or finishes too fast (<2.3 min), reads it, fixes the bug, and re-tests until it passes.

After testing: adds passing experiments to the work generator config with weight 1.0. Retires over-seeded CPU experiments (>50 completed): aborts unsent/in-progress WUs AND zeros their weight. Emergency fallback: if zero active CPU experiments remain, designs and deploys ONE new experiment as a stopgap.

Runs in PARALLEL with GPU pipeline (Steps 11→12).

### Step 11 — GPU Research (Design & Write Only)
Same as CPU Research but for GPU. Scripts MUST use CuPy for heavy computation. HAS_GPU/cupy mandatory. GPU FLOPS calibration mandatory. Task duration: 30 minutes (vs 15 min for CPU). Peak RAM under 4GB, VRAM under 80% of GPU_MEMORY_MB.

Runs in PARALLEL with CPU pipeline (Steps 9→10).

### Step 12 — GPU Dry-Run & Deploy
Tests, fixes, and deploys GPU experiment scripts written by Step 11. Includes CuPy-specific lint checks (known bugs: rng.random(), xp.linalg.eig(), cupyx.scipy.linalg import, missing CUPY_CACHE_IN_MEMORY, VRAM cap inversion). Dry-runs on a real GPU via reverse SSH tunnel service. Falls back to CPU-mode dry-run if tunnel is down.

Same retirement and emergency fallback logic as CPU Dry-Run.

Runs in PARALLEL with CPU pipeline (Steps 9→10).

### Step 13 — Cleanup (Abort Retired WUs)
Reads retired_experiments.txt and processes retired experiments in two phases:
1. **Zero config weights FIRST** — stops the work generator from creating new WUs
2. **Abort WUs** — aborts all unsent and in-progress WUs for retired experiments

Order matters: if aborts ran first, the work generator could re-create WUs in the gap before the config weight gets zeroed. Runs AFTER parallel Research+Deploy pipelines complete.

### Step 14 — Error Triage 1 (Slow Tasks)
Finds experiments with elapsed_time > 50 min (target is 15 min CPU / 30 min GPU). Reads the slow script, identifies the bug, fixes it. Aborts remaining slow tasks. Redeploys fixed version. Skips retired experiments.

Runs AFTER Cleanup (Step 13) so it only triages active experiments.

### Step 15 — Error Triage 2 (Errors, Silent Failures, Fast Finish, Telemetry)
Finds experiments with high error rates (>5% and >5 errors). Reads stderr, diagnoses bugs. Three options per experiment: FIX, ABORT, or SKIP.

**Sub-steps:**
- **1A: Client error telemetry** — reads error reports from v6.39+ clients (HTTP POST). Catches errors that BOINC marks as "success" because the client handles exceptions gracefully.
- **1B: Silent failure detection** — checks experiments where BOINC reports success but sanitized results contain no actual science data.
- **1C: Fast Finish detection** — finds experiments completing in under 2 minutes.
- **1D: GPU Wrapper detection** — finds GPU experiments doing most work in numpy instead of cupy.
- **3B: Prompt Change Suggestions** — logs recurring bug patterns as suggestions for prompt improvements.

Skips retired experiments. Runs AFTER Cleanup (Step 13) so it only triages active experiments.

**Script Fix Rules (enforced in both triage steps):**
- ONE SEED PER WORKUNIT — no multi-trial loops
- Iterative deepening, not multi-trial
- Memory caps: 2GB CPU, 4GB GPU
- Early submit if output stabilizes
- Keep result boilerplate and RESULT_SCHEMA intact
- Don't change seed handling

### Step 16 — Experiment Descriptions
Ensures every experiment has a human-readable description in experiment_descriptions.json. For any experiment missing a description, reads its script's docstring and writes a summary with title, category, summary, description, method, and fields_measured. Descriptions persist forever — stay in JSON even after experiments retire. Limit: 20 new descriptions per cycle.

## Parallel Pipelines

Steps 9–12 execute as two parallel bash subshells:

```
( Step 9: CPU Research → Step 10: CPU Dry-Run ) &
( Step 11: GPU Research → Step 12: GPU Dry-Run ) &
wait for both
Step 13: Cleanup
Step 14: Error Triage 1
Step 15: Error Triage 2
Step 16: Exp Descriptions
```

Within each pipeline, steps run sequentially (Research before Dry-Run). Both pipelines must complete before Cleanup runs. Error Triage runs AFTER Cleanup so it only triages active experiments. Race condition on the work generator config is handled by flock.

## Experiment Compliance Lint (Automated Cron)

A mechanical compliance lint runs every 5 minutes and auto-fixes known issues in active GPU experiment scripts:
1. Missing `CUPY_CACHE_IN_MEMORY=1` (adds it before cupy import)
2. `cupyx.scipy.linalg` imports (replaces with `cupy.linalg`)
3. Inverted VRAM cap: `MAX_N = max(INITIAL_N, MAX_N)` (fixes to `min`)
4. `rng.random()` usage (replaces with `rng.random_sample()`)

**Safety features:**
- 60-second mtime guard: skips files modified in the last 60s to avoid racing with Codex edits
- Idempotent: won't re-apply fixes that are already present
- **Aborts all unsent and in-progress WUs** for any experiment it fixes, preventing buggy tasks from triggering fleet-wide client backoff
- Health check (Step 1, Check 15) verifies the cron is running

## GPU Guardrails

Three rules enforced in GPU Research and GPU Dry-Run prompts:

1. **CUPY_CACHE_IN_MEMORY=1** — must be set via `os.environ.setdefault()` BEFORE importing cupy. Without it, CuPy writes JIT-compiled kernel cache to disk in the BOINC slot directory, exhausting per-task disk limits.

2. **INITIAL_N = min(INITIAL_N, MAX_N)** — FLOPS-scaled initial problem size must be clamped to the VRAM-derived cap. Scripts with `max(INITIAL, CAP)` invert the cap, causing OOM on small-VRAM GPUs.

3. **No cupyx.scipy.linalg** — module doesn't reliably exist across CuPy versions. Use `cupy.linalg` or `numpy.linalg` fallback instead.

## Work Generator & Feeder System

- **Feeder:** Runs every minute via cron
- **Config-driven:** Reads wu_generator_config.json with weighted random selection
- **TARGET_UNSENT:** 500
- **Pool guardian:** Runs every 3 min, trims unsent if above threshold (CPU_MAX=400, GPU_MAX=100)
- **max_total_results:** 1 per workunit
- **max_error_results:** 1 per workunit

Config format: `{ "gpu_percent": 5, "cpu": { "script_name": weight }, "gpu": { "script_name": weight } }`

Who edits the config:
- Step 10 (CPU Dry-Run): adds new CPU experiments with weight 1.0, zeros retired
- Step 12 (GPU Dry-Run): adds new GPU experiments with weight 1.0, zeros retired
- Step 13 (Cleanup): verifies retired experiments have weight 0

All config edits use flock for mutual exclusion.

## Error Rate Watchdog

Runs every 5 minutes via cron. Prevents a single bad experiment from triggering fleet-wide client backoff:
- Checks each active experiment's error rate over last 2 hours
- If error_rate > 10% AND at least 10 results: auto-disables experiment
- Zeros weight in config, aborts all unsent WUs, sends alert
- Constants: ERROR_THRESHOLD=0.10, MIN_RESULTS=10, LOOKBACK_HOURS=2

## Seed Injection Pipeline

Each workunit gets a unique seed, injected by the feeder into the WU's JSON payload:

1. **Feeder** adds `seed` field to each WU JSON (derived from experiment_name hash)
2. **Client** reads seed from WU JSON
3. **Client** injects `EXPERIMENT_SEED` into the experiment script's exec namespace
4. **Script's** `load_context()` reads `EXPERIMENT_SEED`
5. **Script** uses seed for all RNG initialization — one seed, one deep computation

This ensures every workunit produces a unique, reproducible result. Statistical power comes from running hundreds of seeds across the volunteer network, not from running multiple trials within a single workunit.

## Data-Driven Validation Philosophy

The system validates through data, not trust. Every experiment runs across dozens to hundreds of independent seeds on separate volunteer hosts. Results are only considered meaningful when they show:

- **Statistical significance** (Cohen's d effect sizes)
- **Sign consistency** across seeds (e.g., 735/735 positive)
- **Replication** across different hosts and hardware

This makes the system resistant to individual host cheating (one bad host can't move the aggregate), random noise (hundreds of seeds wash out flukes), and cherry-picking (automated dedup catches duplicates, rating scores objectively).

The validator credits ALL completed work by default. Credit denial only happens for confirmed cheating patterns. The anti-cheat is statistical — not punitive.

## Price-per-FLOP Credit System

CPU and GPU FLOPS priced separately based on real hardware market values. A donated RTX 4090 earns proportionally more than a GTX 750 Ti. GPU tasks credit both the CPU core used AND the GPU. Price table updated hourly by automated price updater cron.

## Network FLOPS Calculation

Network TFLOPS displayed on the homepage is calculated over a 12-hour window:

```sql
SUM(elapsed_time * host.p_fpops + IF(gpu_task, elapsed_time * host.p_gpu_fpops, 0)) / 1e12 / 43200
```

This gives a sustained TFLOPS rate, not a cumulative total.

## Public API

JSON API endpoints for external integrations:

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats.php` | Live network stats: hosts, GPUs, CPU cores, results |
| `GET /api/findings.php?status=confirmed&limit=10` | Scientific findings with effect sizes and seed counts |
| `GET /api/experiments.php?limit=10` | Active experiment scripts with source URLs |
| `GET /api/papers.php` | Published research papers with PDF links |

All endpoints return JSON, CORS enabled, no auth required.

## Deployed Client Versions (as of 2026-03-16)

| Platform | Version | Size |
|----------|---------|------|
| CPU Linux (x86_64) | v6.39 | ~21MB |
| GPU Linux (CUDA 12.8) | v6.39 | ~1.8GB |
| CPU Windows (x86_64) | v6.39 | ~27MB |
| GPU Windows (CUDA 12.8) | v6.39 | ~1.1GB |
| macOS ARM64 | v6.39 | ~8MB |

Error telemetry: v6.39+ clients POST script errors via HTTP before BOINC sees them. Rate limited, fire-and-forget.

## Published Papers

Papers are generated from experiment results and published on the website.

**First paper (2026-03-07):** *"Species-Level Interaction Heterogeneity Localizes Reactive Modes and Widens the Stable-but-Reactive Window in Random Ecological Communities"* — 14 pages, 735 seeds across 17 hosts, Cohen's d up to 335.59, 100% sign consistency. Bridges May's stability theory, reactivity theory, and Anderson localization from condensed matter physics.

## Infrastructure

- **Backup:** Every 12 hours, critical DB tables dumped to gzipped SQL
- **Assimilator watchdog:** Runs every 5 min, auto-restarts if stuck
- **Compliance lint:** Runs every 5 min, auto-fixes GPU script issues and aborts buggy WUs
- **Immutable files:** All prompts and loop scripts locked with `chattr +i` to prevent accidental modification
- **Alerting:** Push notifications for rate limits, process failures, resource thresholds, new patrons
- **Patreon monitor:** Polls API every 10 min, alerts on new/departed patrons
- **Team credit sync:** Every 30 min, keeps team credits in sync with member totals
