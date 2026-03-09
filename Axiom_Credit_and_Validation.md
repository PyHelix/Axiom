# Axiom Credit & Validation System

## How Credit Works

Every Axiom workunit runs for **15 minutes** on every machine, regardless of hardware. A Raspberry Pi and a Ryzen 9950X both run the same 15-minute task. The difference is in the *results* — faster hardware computes more data points, more trials, and returns a larger result file within that fixed window.

**Credit is proportional to the bytes of data returned.**

The formula:

```
result_credit = (your_result_bytes / total_bytes_this_cycle) * 10,000
```

- Minimum: 1.0 credit per valid result
- Maximum: 500.0 credit per result
- Budget: 10,000 credit distributed per validation cycle

### Why bytes, not FLOPS?

Traditional BOINC projects run uniform workunits — the same computation on every machine. FLOPS-based credit works well there because every task does the same work.

Axiom is different. An AI principal investigator designs **new experiments every cycle** — Kuramoto oscillator models, sandpile dynamics, ecological stability simulations, Markov branching processes. Each experiment is a unique Python script with different computational characteristics. There is no single FLOPS benchmark that applies to all of them.

Bytes returned is the natural metric because:

- **Hardware-neutral**: The same experiment produces the same output size regardless of CPU speed
- **Proportional to work**: Experiments that compute more trials and data points produce larger result files
- **Ungameable**: The assimilator strips all strings and only keeps numeric data — you can't inflate results with padding
- **Fair to GPU volunteers**: GPU tasks naturally return more data (30-75x more per task) and receive proportionally more credit without hardcoded multipliers

### Why not time-based?

All tasks already run for 15 minutes, so time is constant. Time-based credit would give every task identical credit regardless of how much useful computation was performed. Bytes-based credit rewards machines that produce more science within the same time window.

## How Validation Works

### Step 1: Data Sanitization (Automatic)

When a volunteer returns a result, the **assimilator** processes it immediately:

1. Strips ALL strings — zero text from volunteers passes through
2. Keeps only numeric data: integers, floats, booleans, numeric lists
3. Validates key names against a whitelist — unknown keys cause the entire result to be discarded
4. Saves a clean numeric-only JSON file for review

This makes prompt injection and data manipulation impossible. The validator never sees volunteer-controlled text.

### Step 2: AI Anti-Cheat Review (Regularly)

An AI validator reviews all uncredited results and checks for:

- **Empty payloads**: Results with all zeros, single repeated values, or trivial data -> zero credit
- **Stub results**: Files under 500 bytes that completed in under 60 seconds (failed downloads or instant errors) -> zero credit
- **Outlier detection**: If most hosts return consistent values for an experiment and one host is wildly different -> flagged, zero credit
- **Retired experiments**: Results for experiments that have been retired -> zero credit

Everything else receives credit proportional to bytes returned.

### Step 3: Credit Distribution

The validator runs a single batch SQL update:

1. Snapshots all uncredited result IDs
2. Extracts nbytes (uploaded file size) from each result
3. Calculates byte-weighted credit: (nbytes / total_nbytes) * 10,000
4. Applies floor (1.0) and cap (500.0)
5. Updates host and user credit totals incrementally

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
- **Credit budget**: 10,000 per validation cycle
- **Credit range**: 1.0 - 500.0 per result
- **Validation frequency**: Regularly
- **Active hosts**: 113 (as of March 2026)
- **Results collected**: 46,000+

## Summary

| Aspect | Axiom | Traditional BOINC |
|--------|-------|-------------------|
| Task duration | Fixed 15 min | Variable |
| Credit basis | Bytes returned | FLOPS estimated |
| Workunit type | Different experiment each cycle | Same computation |
| Validation | AI anti-cheat + statistical | Quorum / replication |
| Who designs work | AI (autonomous) | Human researchers |
