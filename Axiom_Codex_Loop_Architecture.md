# Axiom Codex Loop — Architecture & Reference

**Last updated:** 2026-03-13

## Overview

Axiom's autonomous research loop runs continuously on the server, executing a multi-step pipeline every ~3 hours. An LLM (GPT-5.4 via OpenAI Codex CLI) acts as the Principal Investigator — analyzing results, awarding credit, retiring completed experiments, triaging errors, and designing new experiments. No human intervention required for routine operation.

## Master Loop

- **Engine:** `codex exec` with GPT-5.4, medium reasoning effort
- **Cycle gap:** 1.8 hours between cycles
- **Rate limit:** Max 16 cycles per 24-hour sliding window
- **Step timeout:** 2 hours max per step
- **Lock file:** PID-based, prevents duplicate instances
- **Execution order:** Steps 0–6, then Dedup + Rating, then Steps 7–8, then cooldown

## Steps (9 total, numbered 0–8)

### Step 0 — Security Scan
Detects prompt injection in volunteer-submitted database fields. Verifies file integrity. Does NOT modify code or prompts.

### Step 1 — Health Check
Verifies all vital processes are running (MariaDB, BOINC daemons, assimilator, Apache, coordinator). Restarts any that are down. Monitors CPU load, RAM, and disk usage with alerting thresholds.

### Step 2 — Science (Scientific Analysis)
AI-driven analysis of experiment results. Gets the experiment menu via DB query (joins result + workunit tables for families with 10+ completed results today). Never scans the full sanitized results directory (156K+ files). AI chooses its own sample size per experiment using scientific judgment. Reads raw numbers and forms conclusions like a scientist reviewing lab results.

**Decision thresholds:**
- <10 results → SKIP
- Consistent large effect → CONFIRMED
- Mixed/noisy → NO EFFECT or PRELIMINARY
- Opposite of hypothesis → REJECTED

Updates findings_summary.txt (current day only). Publishes reports to Git and website.

### Step 3 — Validate & Credit
Credits ALL uncredited completed results via FLOPS-based formula: `credit = elapsed_time × p_fpops × 1e-11`. Uses host CPU Whetstone benchmark for all tasks (CPU and GPU). Equal hardware running equal time = equal credit. Anti-cheat spot-checks ~200 sanitized JSON files for empty payloads, outliers, duplicates, and stubs.

### Step 4 — Retire
Retires experiments that have collected enough data. Aborts remaining unsent and in-progress tasks. Updates the retirement registry.

### Step 5 — Error Triage 1 (Slow Tasks)
Finds experiments with elapsed_time > 50 min (target is 15 min CPU / 30 min GPU). Reads the slow script, identifies the bug, fixes it. Aborts remaining slow tasks. Redeploys fixed version.

### Step 6 — Error Triage 2 (Errors, Silent Failures)
Finds experiments with high error rates (>5% and >5 errors). Reads stderr, diagnoses bugs. Three options per experiment: FIX, ABORT, or SKIP. Also detects silent failures — scripts that crash gracefully (exit code 0 but no science data). Host quarantine for hosts failing >80% of tasks.

**Script Fix Rules (enforced in both triage steps):**
- ONE SEED PER WORKUNIT — no multi-trial loops
- Iterative deepening, not multi-trial
- Memory caps: 2GB CPU, 4GB GPU
- Early submit if output stabilizes
- Keep result boilerplate and RESULT_SCHEMA intact
- Don't change seed handling

### Dedup (runs after Step 6, before Step 7)
Deduplicates today's findings into a daily dedup file. A rebuild script merges daily files into the master dedup — preserves curated/enriched conclusions, only updates entries with more seeds AND richer conclusions. Entries older than 14 days are dropped.

### Rating — Significance Scoring (runs after Dedup, before Step 7)
AI-scores each deduplicated finding 0–100 for scientific significance. Considers effect size, sample size, consistency, novelty, theoretical relevance, generalizability, and conclusiveness.

### Step 7 — CPU Research & Deploy
Designs new numpy experiment scripts for STEM research. Checks novelty against retirement registry and existing findings. Deploys to BOINC as an untargeted pool. Pool cap: 80,000 staged workunits.

**Key constraints:**
- **One seed per workunit:** Each WU runs ONE deep computation with iterative deepening. Aggregation happens at the BOINC level across hundreds of WUs with different seeds.
- **Resource limits:** Peak RAM under 2GB. Fixed-size arrays. Result JSON under 1MB.
- **Early submit:** If computation stabilizes before 15 min, stop and submit immediately.
- **I/O limit:** Never scans full sanitized directory. Reads dedup + significance scores for novelty checks.

### Step 8 — GPU Research & Deploy
Same as CPU Research but for GPU experiments. Scripts must use CuPy (HAS_GPU check). Pool cap: 4,000 staged workunits. Task duration: 30 minutes (vs 15 min for CPU). Peak RAM under 4GB.

### Detail Enrichment (daily cron, not a loop step)
Takes yesterday's findings scored >80 and enriches them with deeper analysis. Rewrites dedup entries with per-condition breakdowns, confidence intervals, and scientific conclusions.

### Website Counters (cron, not a loop step)
Leaderboard queries DB directly. Counter files updated every 15 min via cron.

## Iterative Deepening — Adaptive Computation

Experiments do not use fixed problem sizes. Instead, each task uses **iterative deepening**: it starts with a small problem (e.g., a 64x64 matrix), runs the computation, measures how long it took, and doubles the problem size for the next pass. Before each new pass, it estimates whether the next one will fit in the remaining time budget based on the known time complexity (e.g., O(N³) for eigenvalue decomposition means each doubling takes ~8x longer).

This approach is **adversarial to pre-commitment** — the script never guesses in advance how large a computation the machine can handle. A fast machine automatically goes deeper than a slow one, and neither wastes time or runs over budget. The result is that every volunteer machine contributes the deepest computation it can within its time window.

The AI research steps decide *which* scientific questions to investigate. The volunteer's hardware decides *how deep* it can go. Statistical power comes from running hundreds of independent seeds across the network, each going as deep as its host allows.

## Data-Driven Validation Philosophy

The system validates through data, not trust. Every experiment runs across dozens to hundreds of independent seeds on separate volunteer hosts. Results are only considered meaningful when they show:

- **Statistical significance** (Cohen's d effect sizes)
- **Sign consistency** across seeds (e.g., 735/735 positive)
- **Replication** across different hosts and hardware

This makes the system resistant to individual host cheating (one bad host can't move the aggregate), random noise (hundreds of seeds wash out flukes), and cherry-picking (automated dedup catches duplicates, rating scores objectively).

The validator credits ALL completed work by default. Credit denial only happens for confirmed cheating patterns. The anti-cheat is statistical — spot-check random results against expected distributions — not punitive.

## Seed Injection Pipeline

Each workunit gets a unique seed, injected by the feeder into the WU's JSON payload:

1. **Feeder** adds `seed` field to each WU JSON (derived from experiment_name hash)
2. **Client** reads seed from WU JSON
3. **Client** injects `EXPERIMENT_SEED` into the experiment script's exec namespace
4. **Script's** `load_context()` reads `EXPERIMENT_SEED`
5. **Script** uses seed for all RNG initialization — one seed, one deep computation

This ensures every workunit produces a unique, reproducible result. Statistical power comes from running hundreds of seeds across the volunteer network, not from running multiple trials within a single workunit.

## Pool-Based Workunit Management

Research steps (7 and 8) use untargeted workunit pools instead of per-host targeting:

- **CPU pool cap:** 80,000 staged workunits
- **GPU pool cap:** 4,000 staged workunits
- No `--target_host` — BOINC scheduler distributes work naturally
- If pool is under cap → create deficit workunits
- If pool is over cap → trim oldest unsent
- Experiments check retirement registry before creating new WUs

## Public API

JSON API endpoints for external integrations:

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats.php` | Live network stats: hosts, GPUs, CPU cores, results |
| `GET /api/findings.php?status=confirmed&limit=10` | Scientific findings with effect sizes and seed counts |
| `GET /api/experiments.php?limit=10` | Active experiment scripts with source URLs |
| `GET /api/papers.php` | Published research papers with PDF links |

All endpoints return JSON, CORS enabled, no auth required.

## Published Papers

Papers are generated from experiment results and published on the website.

**First paper (2026-03-07):** *"Species-Level Interaction Heterogeneity Localizes Reactive Modes and Widens the Stable-but-Reactive Window in Random Ecological Communities"* — 14 pages, 735 seeds across 17 hosts, Cohen's d up to 335.59, 100% sign consistency. Bridges May's stability theory, reactivity theory, and Anderson localization from condensed matter physics.

## Deployed Client Versions (as of 2026-03-13)

| Platform | Version | Size |
|----------|---------|------|
| CPU Linux (x86_64) | v6.33 | ~21MB |
| GPU Linux (CUDA) | v6.33 | ~1.8GB |
| CPU Windows (x86_64) | v6.33 | ~27MB |
| GPU Windows (CUDA) | v6.33 | ~1.1GB |
| macOS ARM64 | v6.24 | ~8MB |

## Infrastructure

- **Backup:** Every 12 hours, critical DB tables dumped to gzipped SQL
- **Assimilator watchdog:** Runs every 5 min, auto-restarts if stuck (no new sanitized files in 10 min while WUs pending)
- **Immutable files:** All prompts and loop scripts locked with `chattr +i` to prevent accidental modification
- **Alerting:** Push notifications for rate limits, process failures, resource thresholds
