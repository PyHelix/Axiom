# Fitness Score Convention for Axiom Experiment Results

**Established:** 2026-03-19
**Status:** Active — all new experiments follow this convention

## Overview

Every Axiom experiment now produces a `_fitness` score as the **first key** in
`experiment_result`. This standardized metric enables the Science step to
prioritize the most informative results when analyzing experiment families,
rather than sampling randomly.

## Design Principle

The fitness score follows the same philosophy as Stockfish's depth+eval
system: **deeper search produces more trustworthy results**. A volunteer
machine that ran a bisection for 14 minutes and narrowed a bracket to width
1e-12 produces a more valuable result than one that ran for 30 seconds and
left a bracket of width 0.5 — even though both are valid.

## Formula

For bisection/threshold experiments (the majority of Axiom experiments):

```
_fitness = 1.0 / max(bracket_width, 1e-15)
```

- **Tighter bracket = higher fitness**
- A bracket width of 1e-6 yields fitness 1,000,000
- A bracket width of 1e-12 yields fitness 1,000,000,000,000
- The 1e-15 floor prevents division by zero

For training experiments, fitness may be defined differently (e.g., inverse
final loss, number of epochs completed), but the principle remains: deeper
computation = higher fitness.

## Pipeline Integration

### Science Step (Step 2)
- Reads all result files for an experiment family (typically ~300 sampled)
- **Sorts results by `_fitness` (highest first)** instead of random sampling
- Analyzes top-scoring results for scientific conclusions
- This means the Science AI sees the deepest, most converged results first

### Budget Parameters
- Total sample budget: **2000** (increased from 600)
- Per-pull sample size: **100** (increased from 30)
- These increases allow the Science step to see a wider spread of results
  while still prioritizing the best ones

### No I/O Impact
- Sorting happens on the already-filtered ~300 files per family
- No additional filesystem operations required
- The fitness key is already present in the JSON result files

## Backward Compatibility

Experiments deployed before this convention (without `_fitness` key) are
handled gracefully — they sort to the bottom of the priority list, and the
Science step still analyzes them normally.

## Example

```python
# In an experiment script, the result dict starts with _fitness:
experiment_result = {
    "_fitness": 1.0 / max(bracket_width, 1e-15),
    "parameter_c_estimate": best_c,
    "bracket_width": bracket_width,
    "last_bridge_ratio": bridge_ratio,
    # ... other fields
}
```

## Intellectual Property Note

This convention was designed and deployed on 2026-03-19 as part of the Axiom
distributed science platform. The fitness-prioritized analysis pipeline is
a novel contribution to autonomous experiment management in volunteer
computing.
