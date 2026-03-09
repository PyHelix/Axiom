# Axiom Credit & Validation System

## How Credit Works

Every Axiom workunit runs for **15 minutes** on every machine, regardless of hardware. Credit is based on the compute resources the volunteer donated — time and hardware capability — using BOINC's standard FLOPS-based approach.

**Credit formula:**

```
granted_credit = elapsed_time * host_p_fpops * 1e-9
```

This gives credit in **cobblestones** (BOINC's standard unit). A faster machine earns more credit per task because it has higher FLOPS. Two identical machines running the same duration earn identical credit, every time, regardless of which experiment they ran.

### Why FLOPS?

- **Deterministic**: Same hardware, same time = same credit. Always.
- **Hardware-proportional**: A Ryzen 9 7950X earns more than a Raspberry Pi for the same 15-minute task, reflecting the actual compute donated.
- **Standard BOINC**: Volunteers who run multiple projects expect consistent, FLOPS-based credit. This matches the convention that 25+ years of BOINC projects have established.
- **No dependencies on other volunteers**: Your credit is based on what *you* donated, not what anyone else did in the same cycle.
- **Ungameable**: `elapsed_time` and `p_fpops` are measured by the BOINC infrastructure, not by the experiment script.

### Examples

| Hardware | FLOPS | 15-min credit |
|----------|-------|---------------|
| Raspberry Pi 4 | ~1.5 GFLOPS | ~1,350 |
| Intel i5-6200U | ~4.0 GFLOPS | ~3,600 |
| AMD Ryzen 9 7950X | ~9.8 GFLOPS | ~8,820 |

Three identical PCs running for the same period get exactly 3x the credit of one PC. Always.

## How Validation Works

### Step 1: Data Sanitization (Automatic)

When a volunteer returns a result, the **assimilator** processes it immediately:

1. Strips ALL strings — zero text from volunteers passes through
2. Keeps only numeric data: integers, floats, booleans, numeric lists
3. Validates key names against a whitelist — unknown keys cause the entire result to be discarded
4. Saves a clean numeric-only JSON file for review

This makes prompt injection and data manipulation impossible. The validator never sees volunteer-controlled text.

### Step 2: AI Anti-Cheat Review

An AI reviewer spot-checks sanitized results for:

- **Empty payloads**: Results with all zeros, single repeated values, or trivial data -> zero credit
- **Stub results**: Tasks that completed in under 60 seconds (failed downloads or instant errors) -> zero credit
- **Outlier detection**: If most hosts return consistent values for an experiment and one host is wildly different -> flagged, zero credit
- **Duplicate/identical results**: If two different hosts return byte-identical data -> flagged

Flagged results receive 0.01 credit (denied sentinel). Everything else receives standard FLOPS credit.

### Step 3: Credit Distribution

A single batch operation:

1. Query all uncredited results with their `elapsed_time` and host `p_fpops`
2. Calculate `elapsed_time * p_fpops * 1e-9` for each
3. Set flagged results to 0.01
4. Batch UPDATE all results
5. Incrementally add credit to host and user totals

**Credit is never set to an absolute value** — it is always added to existing totals, preserving legacy credit.

## Anti-Cheat Philosophy

Axiom validates through **data, not trust**. Every experiment runs across dozens to hundreds of independent seeds on separate volunteer hosts. Results are only considered scientifically meaningful when they show:

- **Statistical significance**: Cohen's d effect sizes
- **Sign consistency**: e.g., 735/735 seeds showing the same directional effect
- **Replication**: Consistent results across different hosts and hardware

A single bad host cannot move the aggregate. Hundreds of seeds wash out noise and flukes. The math is the validator — not any single host's output.

Credit denial only happens for confirmed cheating patterns. The system rewards participation by default.

## Key Numbers

- **Task duration**: 15 minutes (all platforms)
- **Credit basis**: FLOPS (elapsed_time * host_p_fpops * 1e-9)
- **Validation frequency**: Every codex loop cycle (~1.2 hours)
- **Active hosts**: 113 (as of March 2026)
- **Results collected**: 46,000+

## Summary

| Aspect | Axiom | Notes |
|--------|-------|-------|
| Task duration | Fixed 15 min | Same across all platforms |
| Credit basis | FLOPS (standard BOINC) | elapsed_time * host_flops |
| Workunit type | Different experiment each cycle | AI-designed |
| Validation | AI anti-cheat + statistical | Spot-check + cross-seed replication |
| Who designs work | AI (autonomous) | New experiments every cycle |
