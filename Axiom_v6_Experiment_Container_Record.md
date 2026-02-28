# Axiom v6: LLM-Directed Experiment Container Platform — Invention Record

**Author:** PyHelix (Foxes.owo@gmail.com)
**Date:** February 26-28, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production — 10 experiments deployed across volunteer machines

---

## What Was Invented

### LLM-Directed Volunteer Computing Experiment Platform with AI-Judged Credit (v6.00+)

A system that transforms a BOINC volunteer computing network into a **general-purpose experiment container platform** where an LLM (Claude), under human oversight, designs numpy-based machine learning experiments, dispatches them to appropriate volunteer hardware based on capability matching, monitors results, and judges credit based on result quality rather than FLOPS reported.

This is, to my knowledge, the **first implementation** of all of the following:

1. **AI-judged credit in volunteer computing.** All existing BOINC projects (SETI@home, Rosetta@home, Einstein@home, Folding@home, etc.) use automated FLOPS-based credit or validator consensus. No volunteer computing project has ever used an AI/LLM to review actual result quality and award credit based on scientific validity. This eliminates FLOPS-based credit gaming entirely.

2. **LLM-designed experiments dispatched to volunteer hardware.** No prior system has an LLM design machine learning experiments specifically tailored to run on heterogeneous volunteer machines. The LLM considers each host's CPU count, RAM, OS, and GPU availability when designing and assigning experiments.

3. **Closed-loop AI research pipeline on volunteer computing.** The full loop — LLM designs experiment, dispatches to best-fit volunteer hardware, collects results, AI reviews results, refines next experiments — has no prior art in volunteer computing. The goal is full automation as AI advances, reducing human oversight over time until the system can self-direct its own research priorities.

---

## Why This Change Was Needed

After months of work on distributed LLM training (v3.x transformer backprop, v5.x spiking STDP), I discovered a fundamental limitation:

**You cannot reliably lower the BPC (entropy) of a decentralized LLM without every client downloading the entire model.**

The problem: each volunteer trains on different data, producing gradients in different loss landscape contexts. When these are aggregated on the server, the conflicting gradient directions largely cancel out. The sector approach (uploading 1/80th of gradients) was proven to produce pure noise. Even full-model delta averaging showed diminishing returns — the coordination overhead and gradient staleness made meaningful convergence impractical at volunteer computing scale with heterogeneous, unreliable nodes.

Rather than continue fighting this fundamental constraint, I pivoted the entire network into something more valuable: a general-purpose experiment container platform where volunteer machines run independently meaningful experiments designed by AI.

---

## Architecture

### How It Works

```
LLM (Claude)                    Server (Hetzner)                Volunteers (BOINC)
┌─────────────────┐           ┌──────────────────────┐        ┌──────────────────┐
│ Design experiment│           │                      │        │                  │
│ matched to host  │──────────►│ Stage script at       │        │ Download wu.json │
│ hardware         │           │ /experiments/name.py  │        │ (mode=experiment)│
│                  │           │                      │◄───────│                  │
│                  │           │ Create workunit with  │        │ Download script  │
│                  │           │ --target_host         │───────►│ from script_url  │
│                  │           │                      │        │                  │
│                  │           │                      │        │ exec() script    │
│                  │           │                      │        │ with numpy       │
│                  │           │                      │        │                  │
│ Review results   │◄──────────│ Assimilator collects  │◄───────│ Upload result    │
│ Judge credit     │           │ experiment_result.json│        │ JSON             │
│ based on quality │──────────►│                      │        │                  │
│                  │           │ Apply credit to       │        │                  │
│ Design next      │           │ host/user in DB       │        │                  │
│ experiment       │           │                      │        │                  │
└─────────────────┘           └──────────────────────┘        └──────────────────┘
```

### Experiment Script Contract
- Self-contained Python scripts using only numpy and stdlib
- Downloaded at runtime from `https://axiom.heliex.net/experiments/<name>.py`
- Write results to `experiment_result.json` in working directory
- Client executes via `exec()` in bundled PyInstaller Python environment
- CuPy available on GPU builds for CUDA acceleration

### Workunit Structure
```json
{
  "mode": "experiment",
  "script_url": "https://axiom.heliex.net/experiments/<name>.py",
  "run_duration": 600,
  "experiment_name": "<name>"
}
```

### Hardware-Matched Deployment (First Batch — February 28, 2026)

| Host | Hardware | Experiment | Why |
|------|----------|------------|-----|
| 194 (7950x) | 128 CPUs, 62GB, Linux | Double Descent | Large sweep of model widths |
| 287 (DESKTOP-N5RAJSE) | 192 CPUs, 256GB, Windows | Neural Scaling Laws | 7 model scales, massive parallelism |
| 296 (epyc7v12) | 240 CPUs, 189GB, Linux | Emergent Abilities | 5000-epoch grokking across 5 scales |
| 141 (SPEKTRUM) | 72 CPUs, 191GB, Windows | Mode Connectivity | Bezier curve optimization in weight space |
| 80 (MAIN) | 32 CPUs, 48GB, Windows | Lottery Ticket | Iterative pruning + rewind |
| 209 (Widmo) | 32 CPUs, 123GB, Linux | Information Bottleneck | Mutual information across MLP layers |
| 249 (thonon-meylan) | 20 CPUs, 47GB, Linux | Power Law Forgetting | Catastrophic forgetting measurement |
| 321 (Rosie) | 20 CPUs, 112GB, Windows | Reservoir Computing | Random RNN on Lorenz attractor |
| 60 (dell) | 8 CPUs, 16GB, Linux | Edge of Chaos | Lyapunov exponents vs spectral radius |
| 267 (philip) | 4 CPUs, 8GB, Linux | Cellular Automata | Evolve CA rules with genetic algorithm |

### Anti-Cheat Credit System
Traditional BOINC credit is based on FLOPS (floating point operations reported by the client), which is trivially gameable — a malicious client can report inflated FLOPS without doing real work. Axiom v6 bypasses this entirely:

1. Assimilator saves full experiment results but grants **zero credit** automatically
2. An AI (Claude), under human oversight, reviews the actual result data
3. Credit is awarded based on result quality and scientific validity
4. Invalid, trivial, or suspicious results receive no credit

This makes credit gaming impossible without actually producing valid scientific results.

---

## Prior Art Search (February 28, 2026)

### Closest Related Work

**BOINC@TACC (2019-2023):** Uses Docker containers on BOINC for arbitrary computation. Similar containerization concept, but no AI involvement in experiment design, dispatch, or credit judging. Experiments are human-designed and manually deployed.

**The AI Scientist (Sakana AI, 2024):** LLM designs and runs ML experiments autonomously. However, it runs on local/cloud hardware, not volunteer computing. No distributed execution, no credit system, no hardware matching.

**SDDF — Self-Driving Digital Foundry (2024):** AI-directed materials science experiments in simulation. Related concept of AI directing experiments, but operates on dedicated HPC clusters, not volunteer computing, and targets chemistry not ML.

**Experiment.ai / AutoML platforms:** Automated hyperparameter search and neural architecture search. These optimize within a fixed search space, not open-ended experiment design. No volunteer computing component.

### What Has No Prior Art

| Claim | Prior Art Found |
|-------|----------------|
| AI/LLM judges credit quality in volunteer computing | **None** |
| LLM designs ML experiments for volunteer hardware | **None** |
| Hardware-capability-matched experiment dispatch by AI | **None** |
| Full closed loop: AI designs → dispatches to volunteers → AI reviews → refines | **None** |
| Arbitrary experiment containers on BOINC with AI oversight | **None** |

The individual components exist separately (BOINC containers, LLM experiment design, AI-judged evaluation), but the combination — an LLM designing experiments specifically for heterogeneous volunteer hardware, dispatching them via BOINC, and judging credit based on result quality — is entirely novel.

---

## 10 Experiment Scripts (First Batch)

All experiments are self-contained numpy-only Python scripts investigating fundamental questions in machine learning theory:

1. **Double Descent** (`double_descent.py`) — Maps the test error vs model width curve showing the double descent phenomenon where larger models improve after an interpolation peak
2. **Neural Scaling Laws** (`neural_scaling_laws.py`) — Measures loss vs parameter count across 7 model scales, fitting the empirical power law L = a * N^(-alpha)
3. **Emergent Abilities** (`emergent_abilities.py`) — Demonstrates grokking: sharp phase transitions in modular arithmetic generalization as model scale increases
4. **Mode Connectivity** (`mode_connectivity.py`) — Tests whether independently trained networks are connected by low-loss paths using quadratic Bezier curves in weight space
5. **Lottery Ticket** (`lottery_ticket.py`) — Iterative magnitude pruning with weight rewinding to find sparse subnetworks that match dense performance
6. **Information Bottleneck** (`information_bottleneck.py`) — Tracks mutual information between layers during MLP training to test the information bottleneck hypothesis
7. **Power Law Forgetting** (`power_law_forgetting.py`) — Measures catastrophic forgetting rate when training sequentially on different tasks
8. **Reservoir Computing** (`reservoir_computing.py`) — Trains a random fixed RNN (echo state network) with linear readout on chaotic Lorenz attractor prediction
9. **Edge of Chaos** (`edge_of_chaos.py`) — Maps Lyapunov exponents vs spectral radius to find the edge of chaos in random recurrent networks
10. **Cellular Automata Evolution** (`cellular_automata.py`) — Uses genetic algorithms to evolve 1D cellular automata rules for density classification

---

## Related: Proof of Useful Contribution (PoUC) Consensus Mechanism

The AI-judged credit system in Axiom v6 led to the conception of a novel blockchain consensus mechanism where block production weight is determined by AI-validated scientific contribution rather than stake or hash rate.

Documented separately: **[Axiom_PoUC_Consensus_Mechanism.md](Axiom_PoUC_Consensus_Mechanism.md)**

---

## Timeline

- **Jan 2026:** Axiom BOINC launched, distributed transformer training (v3.x)
- **Feb 14, 2026:** Discovered sector gradient aggregation is fundamentally broken
- **Feb 15, 2026:** Full-model delta averaging deployed, learning confirmed but limited
- **Feb 25-26, 2026:** Attempted spiking neural network approach (v5.x)
- **Feb 26, 2026:** Discovered fundamental limitation of decentralized LLM training
- **Feb 26, 2026:** Designed experiment container architecture (v6.00)
- **Feb 27, 2026:** Built and deployed client with experiment executor mode
- **Feb 28, 2026:** Fixed Windows encoding bug (v6.04), deployed 10 experiments to volunteer hosts
- **Feb 28, 2026:** First experiment results collected (Edge of Chaos completed successfully)
- **Feb 28, 2026:** Conceived Proof of Useful Contribution (PoUC) consensus mechanism — block production weighted by AI-judged scientific credit instead of stake or hash rate

---

*This document serves as a timestamped record of invention. The git commit timestamp, together with Internet Archive Wayback Machine snapshots of https://axiom.heliex.net and this repository, provides proof of the date these ideas were first implemented and deployed publicly.*
