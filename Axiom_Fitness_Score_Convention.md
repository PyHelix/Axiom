# Axiom: Fitness Score Convention — Invention Record

**Author:** PyHelix
**Date:** March 19, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production

---

## What Was Invented

### Fitness Score Convention for Distributed Experiment Results

A universal scoring convention for volunteer-computed experiment results, inspired by chess engine evaluation (Stockfish's depth-based scoring). Every experiment produces a `_fitness` score as the **first key** in its result JSON, enabling the AI Science step to prioritize the deepest, most converged results across heterogeneous experiment families.

### How It Works

**For threshold bisection experiments** (the primary experiment pattern):
```
_fitness = 1.0 / max(bracket_width, 1e-15)
```
A tighter bracket (more precise threshold measurement) produces a higher fitness score. A bracket of width 0.001 scores 1000; a bracket of width 0.1 scores 10.

**For other experiment types**, `_fitness` is defined as a meaningful quality metric — effect size magnitude, convergence measure, or similar — always scalar, always higher = better.

### Why It Matters

1. **Cross-experiment comparison**: The AI Science step can sort results by fitness across all experiment families and focus analysis on the most informative results first, regardless of which experiment produced them.

2. **Iterative deepening integration**: Combined with the iterative deepening bisection pattern (start at small matrix size N=64, bisect a parameter, double N, repeat), fitness naturally increases as volunteers contribute more compute time. Like Stockfish searching to depth 20 vs depth 40 — the deeper result is more trustworthy.

3. **Retirement decisions**: Experiments with consistently high fitness scores (tight brackets, clear convergence) across 15+ independent seeds can be confidently retired with a CONFIRMED or REJECTED conclusion.

4. **No early exit**: The convention explicitly prohibits early convergence exits. When a bracket narrows below a threshold, the experiment widens the bracket and doubles N to deepen further, rather than stopping. This ensures volunteers always contribute the maximum useful compute per task.

### Technical Details

- `_fitness` must be the **first key** in the result JSON (enforced by the assimilator)
- The AI Science step sorts by fitness (highest first) with an analysis budget of 2000 samples per cycle
- Results without `_fitness` are still accepted but deprioritized in analysis
- The fitness score is a float, not an integer — continuous precision is important

### Prior Art Analogy

This is analogous to how Stockfish (chess engine) reports evaluation at increasing depths. A position evaluated to depth 15 might show +0.5, but the same position at depth 30 might show +1.2 — the deeper evaluation is more reliable. Similarly, an Axiom experiment at N=128 with bracket width 0.05 is less informative than the same experiment at N=2048 with bracket width 0.001.

---

## Implementation Date

- Conceived and deployed: March 19, 2026
- All active experiment scripts updated to produce `_fitness` as first key
- Science step updated to sort by fitness instead of random sampling
- Analysis budget increased from 600 to 2000 samples for broader coverage
