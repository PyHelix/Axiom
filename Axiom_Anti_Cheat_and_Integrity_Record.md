# Axiom: Anti-Cheat & Data Integrity Systems — Invention Record

**Author:** PyHelix
**Date:** March 18, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production

---

## Overview

Axiom's integrity systems solve a fundamental challenge in volunteer computing: how to trust scientific results from untrusted machines, and how to safely allow an AI agent to process data that originates from those machines. These systems operate as automated watchdogs, requiring no human intervention.

---

## 1. Statistical Verification Pair System

### Problem
In traditional BOINC projects, validation uses quorum (send the same work to N hosts, compare results). This wastes compute — every task runs 2-3x. For scientific experiments where each workunit has a unique seed, quorum validation is impossible because results are expected to differ.

### Solution
A probabilistic verification system that validates a small random sample of work without wasting compute:

**Verification pair creation (in the work generator):**
- 0.5% of workunits are randomly selected for verification
- For each selected WU, a second identical workunit is created (same experiment parameters, same seed) but with a different BOINC identity
- The pair is logged with status "pending"

**Verification pair comparison (runs every 10 minutes via cron):**
- For each pending pair where both results have been returned:
  1. Load the sanitized numeric results from both workunits
  2. Flatten all numeric values into ordered vectors
  3. Compute cosine similarity between the two vectors
  4. Compute mean absolute relative difference (MARD) as secondary metric

**Enforcement logic:**
- Cosine similarity < 0.98 on a cross-host pair = **flagged** (legitimate deterministic experiments always produce cosine = 1.0 for identical seeds)
- Same-host pairs are tracked but never flagged (self-consistency expected)
- 3+ flagged cross-host pairs within a rolling 7-day window = **quarantine** the outlier host
- Before quarantining, the system checks partner correlation: a host is only quarantined if its comparison partners have <=1 flag each (confirming the flagged host is the outlier, not a coincidental pairing with another bad host)
- Quarantine sets max_results_day=1 (soft throttle, not a ban)
- Push notifications sent on both flags and quarantines

### What makes this novel
- **No quorum waste**: Only 0.5% of work is duplicated, vs 100%+ for traditional quorum
- **Statistical outlier detection**: Partner correlation analysis prevents false positives from coincidental pairings
- **Works with unique-seed experiments**: Unlike quorum, this doesn't require identical work — it works precisely because identical seeds should produce identical results on honest hardware
- **Automated escalation**: Flag -> quarantine pipeline requires no human intervention

---

## 2. Error Rate Watchdog

### Problem
In a system where an AI autonomously designs and deploys experiments to volunteer machines, a single buggy experiment can trigger BOINC's client-side exponential backoff on every host that receives it. This cascading failure can take the entire network offline within minutes — hosts stop requesting work after consecutive errors, and recovery requires manual intervention on each host.

### Solution
An automated watchdog that detects and disables bad experiments before they can cascade:

**Detection (runs every 5 minutes via cron):**
1. Query BOINC database for per-experiment error rates over the last 2 hours
2. Merge with client-side error telemetry (catches errors that BOINC marks as "success" because the wrapper exits cleanly even when the experiment script crashes)
3. If any experiment exceeds 10% error rate with >=10 total results: trigger disable

**Disable action (atomic, ~1 second):**
1. Set the experiment's weight to 0 in the work generator config (prevents new WU creation)
2. Abort all unsent and in-progress workunits for that experiment in the database
3. Send push notification with error details

**Backoff reset (runs every 5 minutes):**
- After disabling bad experiments, the watchdog resets BOINC's consecutive_valid counter for all hosts
- This prevents the cascading backoff problem — hosts that received errors from the now-disabled experiment immediately become eligible for new work

### What makes this novel
- **Dual-source error detection**: Combines BOINC-level error tracking with application-level telemetry to catch errors that escape BOINC's classification (exec() errors within the experiment sandbox that the wrapper reports as success)
- **Prevents cascade failures**: The combination of rapid detection (5-min cycle), automatic disable, and backoff reset stops a single bad experiment from taking down the entire volunteer network
- **AI-safe deployment**: Enables an AI to autonomously deploy untested experiment code to volunteer machines — if the code is broken, the watchdog catches it before significant damage occurs

---

## 3. Numbers-Only Data Sanitization (AI Prompt Injection Defense)

### Problem
Axiom's autonomous research loop uses an AI (LLM) as the Principal Investigator, reading and analyzing experiment results from volunteer machines. If volunteers can inject text into results that the AI reads, they could execute prompt injection attacks — instructing the AI to modify its own prompts, award excess credit, disable security checks, or exfiltrate data.

### Solution
A zero-trust data pipeline that makes prompt injection impossible by eliminating the attack surface entirely:

**Assimilator sanitization (runs continuously):**
1. Volunteer submits result JSON containing whatever fields they want
2. Assimilator recursively walks the JSON structure
3. **Every string value is dropped** — no text from volunteers passes through
4. Only numeric types survive: int, float, bool, numeric lists, dicts with numeric values
5. Key names are validated against a whitelist (trusted_params.json) — any unrecognized key causes the entire result to be discarded
6. Server-side metadata (experiment name, WU name, host ID) is filled in from the workunit record, not from the volunteer's submission
7. Clean numeric-only result saved as {result_name}_safe.json

**AI prompt hardening (defense in depth):**
- All AI prompts explicitly list "hostile string fields" in the database that must never be SELECT'd
- Prompts instruct the AI to never interpret numeric values as encoded text (ASCII, binary, Morse code, etc.)
- Security scan step (Step 0) runs before all other steps to detect injection attempts in database text fields

### What makes this novel
- **Eliminates prompt injection entirely**: Unlike filtering or escaping (which can be bypassed), dropping ALL strings means zero text from volunteers ever reaches the AI. The attack surface is zero, not reduced.
- **Whitelist validation of structure**: Even the key names in results are validated — a volunteer cannot introduce new fields that might be misinterpreted
- **Defense for AI-in-the-loop systems**: This is a general pattern for any system where an AI agent processes data from untrusted sources. The insight: if you only need numbers, drop everything that isn't a number.

---

## 4. Price-Per-FLOP Market-Rate Credit System

### Problem
Traditional BOINC credit is based on raw FLOPS benchmarks, which overvalues old hardware (a $50 used GPU might have similar FLOPS to a $500 new one). GPU and CPU compute have fundamentally different market values — GPU FLOPS are ~195x cheaper per dollar than CPU FLOPS.

### Solution
A credit system that scales rewards by the real-world market value of the donated compute:

**Market price tracking (runs hourly via cron):**
1. Maintains a reference table of GPU and CPU models with known FP32 GFLOPS from spec sheets
2. Fetches current market prices for each model from web sources
3. Computes average $/GFLOPS for GPUs and CPUs separately
4. Derives scale factors: CPU_SCALE ~ 5e-12, GPU_SCALE ~ 2.5e-14
5. Writes updated scale factors to a JSON file with freshness timestamp

**Credit formula:**
- CPU tasks: elapsed_time x host_fpops x CPU_SCALE
- GPU tasks: (elapsed_time x host_fpops x CPU_SCALE) + (elapsed_time x host_gpu_fpops x GPU_SCALE)
- GPU tasks always credit both the CPU core used and the GPU

**Freshness enforcement:**
- Before awarding credit, the validation step checks the price table's last_updated_unix timestamp
- If stale (>1.1 hours), it runs the updater before proceeding
- Ensures credit always reflects current market conditions

### What makes this novel
- **Market-rate fairness**: A volunteer donating a $2,755 RTX 4090 earns proportionally more than one donating a $50 GTX 750 Ti, reflecting the actual economic value of the donation
- **Separate CPU/GPU pricing**: Recognizes that CPU and GPU FLOPS have fundamentally different market values (CPU ~195x more expensive per FLOP)
- **Dynamic pricing**: Hourly updates mean credit automatically adjusts as hardware prices change over time
- **Backward-compatible**: Credit is always added incrementally (total_credit + X), never set to an absolute value, preserving legacy credit from before the pricing system was introduced

---

## 5. AI Significance Scoring

### Problem
An autonomous research system generates hundreds of scientific findings per week. Humans need to quickly identify which findings are actually important vs. noise. Traditional metrics (p-value, effect size) don't capture scientific importance.

### Solution
An AI significance scoring system that rates each finding on a 0-100 scale using a structured rubric:

**Statistical strength (0-40 points):**
- Effect size (Cohen's d): |d| > 50 = 15pts, > 10 = 12, > 5 = 9, etc.
- Sample size: 1000+ = 10pts, 500+ = 8, 200+ = 6, etc.
- Sign consistency: 100% = 10pts, 95%+ = 8, etc.
- Reproducibility across hosts: 10+ hosts = 5pts

**Scientific importance (0-35 points):**
- Novelty, theoretical relevance, generalizability, clarity of mechanism

**Conclusiveness (0-25 points):**
- Clean confirmation/rejection with large effect = 20-25
- Preliminary / no valid data = N/A (not scored)

Scores are persistent — existing scores are preserved across cycles and only updated when new data warrants re-evaluation. Scores >80 trigger enrichment processing for deeper analysis.

---

## 6. Science Journal (Persistent AI Memory Across Cycles)

### Problem
The AI Principal Investigator runs in separate sessions (one per codex loop step). Each session starts fresh with no memory of previous analysis. This means the AI might re-analyze the same data, miss patterns that emerge over multiple cycles, or contradict its own earlier conclusions.

### Solution
A daily journal system that gives the AI persistent memory:

1. After each science analysis cycle, the AI writes a 3-6 sentence summary of its findings to a dated journal file
2. At the start of each subsequent cycle, the AI reads today's journal entries for context
3. This allows the AI to build on earlier observations, avoid re-treading ground, and maintain analytical continuity across sessions

The journal is append-only (entries are never modified or deleted), creating an auditable record of the AI's analytical process throughout the day.

---

*This record establishes priority for the systems described above. All systems are deployed in production on the Axiom BOINC project as of March 18, 2026.*
