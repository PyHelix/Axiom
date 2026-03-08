# Axiom: Volunteer-Distributed Neural Network Training — Invention Record

**Author:** PyHelix (Foxes.owo@gmail.com)
**Date:** February 14-15, 2026 (updated March 8, 2026)
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production — Self-healing autonomous research infrastructure (v6.09+), 295+ experiments, 10-step AI PI

---

## What Was Invented

### 1. Volunteer-Distributed Backpropagation via BOINC (v3.80+)
Training a **17.8 billion parameter Mixture-of-Experts neural network** (420 experts x 42.97M parameters each) using volunteer computing through the BOINC distributed computing platform. Volunteers download the current model weights, train locally on seed data using standard backpropagation SGD, and upload compressed weight deltas back to the server. The server aggregates these deltas to improve the model continuously.

This is, to my knowledge, the **first implementation of distributed neural network training over BOINC** where untrusted volunteer machines contribute actual gradient updates to a production neural network.

### 2. QNT4 Delta Compression (v3.86+)
A custom 4-bit quantization format for transmitting weight deltas between workers and server. Each 32-bit float is quantized to a 4-bit signed integer [-7, +7] relative to the delta's scale factor, packed two per byte. Achieves **90% cosine similarity** with the full-precision gradient while reducing upload size from ~171MB (float32) to ~21.5MB. Format: `b'QNT4'` header + uint32 vector length + float32 scale + packed nibbles.

Previous attempts included sparse top-K (0.5% signal capture, broken) and SignSGD 1-bit (8% signal capture, insufficient).

### 3. Server-Side Genetic Algorithm Placement Controller (v3.90+)
A GA that evolves **how** worker deltas are applied to the model, not what the workers compute. The server maintains two competing copies of each expert that receive identical worker deltas but apply them with different evolved strategies (multiplier, position mode, aggressiveness). A continuous evaluator measures both; the lower-BPC expert wins the tournament, and the loser receives the winner's weights plus a mutated strategy.

Key insight: the GA discovers optimal delta application strategies that outperform any fixed multiplier. In simulation testing (42.97M param model), the reactive GA achieved **BPC 5.62** vs fixed-multiplier's **6.84** — breaking through a ceiling that no amount of tuning could pass.

### 4. Reactive Cruise/Kick/Rollback Policy (v3.90+)
The GA controller detects training stalls (N tournaments without minimum improvement) and switches from gentle "cruise" mode to aggressive "kick" mode with high multipliers. If the kick fails to improve BPC within a settle period, the model automatically **rolls back** to the pre-kick checkpoint. This allows safe exploration of aggressive parameter regions without risk of permanent damage.

### 5. 3-Thread Optimizer Daemon with EMA-Directed Perturbations (v3.92+)
Replaced cron-based evaluation with a persistent 3-thread daemon:
- **Two evaluation threads** continuously measure BPC for both GA experts (~11 second cycles)
- **One optimizer thread** accumulates an Exponential Moving Average (EMA) of observed weight changes from incoming worker deltas, then tests directed perturbations along the EMA direction at multiple scales. Only perturbations that demonstrably improve BPC (measured against fresh evaluation) are applied.

The optimizer detects coordinator weight file changes via filesystem monitoring, computes deltas from previous snapshots, and accumulates them into the EMA — no shared memory or message passing needed between the coordinator and optimizer processes. Communication happens through signal files (JSON + numpy) that the coordinator polls and applies atomically.

### 6. Full-Model No-Sector Architecture (v3.90+)
Discovered that the previous 80-sector approach (workers training full model but only uploading 1/80th of the gradient) was **fundamentally broken** — extracting a sector of the gradient computed in a different parameter context (Xavier-initialized non-sector parameters vs accumulated production weights) produces pure noise. Verified empirically: sector deltas worsened BPC by +0.06, while full-model deltas improved by -3.2 BPC under identical conditions.

The fix: workers download the **complete** QNT4-compressed model weights (~20.5MB), train the full model with SGD, and send back the full delta. This eliminated the noise floor and enabled actual learning. BPC dropped from 5.10 to 4.59 in the first 8 minutes of production.

### 7. Volunteer-Distributed Modular Spiking Neural Network with STDP (v5.00+)
**Date: February 25-26, 2026**

To my knowledge, the **first-ever** implementation of all of the following:

- **Large-scale STDP training on text/language data.** All prior STDP research is on small-scale vision tasks (MNIST, CIFAR), maxing out at ~40M synapses with ~7,000 neurons. Axiom v5.00 runs STDP on a **224 million parameter** (710M synapses) spiking network processing natural language (byte-level text prediction from Project Gutenberg). No published work applies pure STDP to language modeling at any scale.

- **Distributed volunteer computing for spiking neural network training.** Prior distributed SNN work targets neuromorphic hardware clusters (SpiNNaker, Loihi). Axiom distributes spiking network training across untrusted volunteer CPUs over the internet via BOINC — volunteers download 3-module neighborhoods (~1MB), train with STDP locally, and upload weight updates. No prior system uses volunteer computing for SNN training.

- **Modular neuromorphic architecture trained by crowd-sourced STDP.** 64 independent spiking modules (LIF neurons, 1600 neurons each, 3.5M params each) connected by sparse inter-module projections, with staleness-based neighborhood assignment ensuring full network coverage. Each volunteer trains a 3-module neighborhood. Overlapping neighborhoods propagate learning across the full network without any global error signal or central coordination of learning.

- **Long-duration unsupervised STDP on structured data as a self-organization experiment.** The hypothesis under test: whether sustained STDP-driven spike-timing correlations on statistically rich input data (English text) will drive the network toward self-organized criticality — a phase transition where emergent computational structure appears, analogous to cortical development. This has not been attempted or published.

**Architecture:** 64 spiking modules x 1600 LIF neurons x 3.5M params each = 224M total parameters. Sparse inter-module connectivity (1% density). Staleness-based module assignment. CPU-only work units (~5 min per task). Supervised readout layer (SGD cross-entropy) provides the only gradient signal; all module-internal learning is pure unsupervised STDP.

**Prior art search (February 26, 2026):**
- [SpikeGPT](https://arxiv.org/abs/2302.13939) (2023): Spiking language model, but trained with surrogate gradient backprop, not STDP
- [SpikeLLM](https://arxiv.org/abs/2407.04752) (2024): Converts existing LLMs to spiking, doesn't train from scratch with STDP
- [Contrastive signal-dependent plasticity](https://pmc.ncbi.nlm.nih.gov/articles/PMC11639678/) (2024): Better local learning rules, small scale only
- No results found for: distributed STDP training, volunteer computing for SNNs, STDP on language/text data, or large-scale STDP self-organization experiments

### 8. LLM-Directed Experiment Container Platform with AI-Judged Credit (v6.00+)
**Date: February 26-28, 2026**

Documented separately: **[Axiom_v6_Experiment_Container_Record.md](Axiom_v6_Experiment_Container_Record.md)**

Transformed the BOINC volunteer network into a general-purpose experiment container platform where an LLM (Claude), under human oversight, designs numpy-based ML experiments, dispatches them to volunteer hardware matched by capability (CPU count, RAM, OS, GPU), and judges credit based on result quality rather than FLOPS.

To my knowledge, the **first implementation** of:
- **AI-judged credit** in volunteer computing (no prior art found)
- **LLM-designed experiments dispatched to volunteer hardware** (no prior art found)
- **Closed-loop AI research pipeline** on volunteer computing (no prior art found)

This pivot was driven by the discovery that you cannot reliably lower BPC in a decentralized LLM without every client downloading the entire model — conflicting gradients from different data contexts cancel out during aggregation. See the [v6 record](Axiom_v6_Experiment_Container_Record.md) for full architecture, prior art search, and experiment details.

### 9. First Autonomous AI Principal Investigator for Volunteer Computing
**Date: March 1, 2026**

Documented separately: **[Axiom_Autonomous_AI_PI_Record.md](Axiom_Autonomous_AI_PI_Record.md)**

Built the first system where an LLM (Claude Code CLI) operates as a **fully autonomous principal investigator** for a volunteer computing network. Running on a scheduled task (hourly + on login) with zero human oversight, the AI SSHs into the production server, reviews experiment results, awards credit to volunteers based on scientific judgment, fills idle compute capacity with experiments, designs entirely new experiments based on evidence, and maintains cumulative scientific records across sessions.

An exhaustive prior art search (March 2026) found **no prior system** that bridges autonomous AI scientists (Sakana AI Scientist, GPT-5+Ginkgo) with volunteer computing management (BOINC). The first fully autonomous run on March 1, 2026 awarded 655 credits, deployed 644 workunits, designed a new grokking experiment (v3), and produced a comprehensive scientific report — all without any human involvement.

To my knowledge, the **first implementation** of:
- **AI autonomously managing a BOINC project end-to-end** (no prior art found)
- **AI designing experiments and deploying them as BOINC workunits** (no prior art found)
- **Scheduled LLM automation as principal investigator of a volunteer computing network** (no prior art found)

### 10. Self-Healing Autonomous Research Infrastructure (v6.09+, March 2026)
**Date: March 6-8, 2026**

The autonomous AI PI system evolved from a 6-step pipeline into a **10-step self-healing research infrastructure** with two additional post-step phases (dedup + significance rating), running 13 cycles per day. The system now autonomously diagnoses and fixes its own failing experiments, restarts crashed daemons, detects security threats, and quarantines misbehaving hosts — without any human intervention.

New capabilities with no prior art in volunteer computing:
- **Error Triage**: AI reads stderr from failing experiment tasks, reads the experiment script source code, diagnoses the root cause, and either fixes the script and redeploys, or aborts the experiment if unfixable. Host quarantine system sets `max_results_day=1` for hosts failing >80% of tasks.
- **Performance Audit**: AI identifies experiments exceeding runtime targets (>20 min vs 15 min target), reads the script, identifies the bottleneck, patches the code, aborts slow tasks, and redeploys the fixed version.
- **Health Check**: Verifies all vital processes (MariaDB, Apache, BOINC daemons, assimilator) are running and auto-restarts any that have crashed.
- **Security Scan**: Detects prompt injection attempts in volunteer-submitted database fields and verifies file integrity of locked system files.

To my knowledge, **no volunteer computing project has ever had self-diagnosing, self-repairing experiment infrastructure** managed by an AI.

### 11. Multi-Model AI Significance Scoring Pipeline (March 2026)
**Date: March 7-8, 2026**

A multi-model pipeline for autonomous quality assessment of scientific findings:
1. A lightweight model (GPT-5.3 Codex Spark) **deduplicates** findings from the rolling 14-day window
2. A high-reasoning model (GPT-5.4 with xhigh reasoning effort) **scores** each deduplicated finding 0-100 for scientific significance, considering: effect size (30 points), sample size (25), sign consistency (25), and verdict clarity (20)

Scores are published to the public website at https://axiom.heliex.net/scientific_findings.php with color-coded significance badges. This enables autonomous prioritization — high-scoring findings are candidates for paper generation, while low-scoring ones may trigger additional experimentation.

To my knowledge, **no system autonomously scores the scientific significance of its own distributed computing results** using multi-model AI evaluation.

### 12. Pool-Based Autonomous Workunit Management (March 2026)
**Date: March 7, 2026**

Replaced per-host targeted workunit creation (`--target_host`) with an autonomous **pool-based system** with caps:
- CPU pool cap: 40,000 workunits (unsent + in-progress)
- GPU pool cap: 2,000 workunits
- Each cycle: AI counts current pool, computes deficit, creates untargeted workunits to fill the gap
- If over cap: trims oldest unsent workunits
- BOINC's built-in scheduler distributes untargeted workunits to appropriate hosts

This eliminated the fragile per-host querying (which required tracking each host's throughput and backlog), reduced token costs by ~40%, and made the system resilient to hosts going offline — their queued work automatically redistributes to other volunteers.

### 13. First AI-Generated Research Paper from Distributed Volunteer Computing (March 2026)
**Date: March 7, 2026**

Published the first research paper generated end-to-end from Axiom's autonomous pipeline:

**Title:** *"Species-Level Interaction Heterogeneity Localizes Reactive Modes and Widens the Stable-but-Reactive Window in Random Ecological Communities"*

The paper was based on 1,463 independent simulations run across 17 volunteer hosts, with Cohen's d > 80 (extremely large effect size). The complete pipeline: AI designed the ecology experiment → volunteers ran simulations on their home computers → AI analyzed results and identified statistical significance → paper was drafted from the findings.

Available at: https://axiom.heliex.net/reactivity_localization_paper.pdf

This demonstrates the complete autonomous discovery-to-publication loop first conceived on March 2, 2026 (see [Axiom_Autonomous_Paper_Pipeline_Record.md](Axiom_Autonomous_Paper_Pipeline_Record.md)).

---

## Architecture Summary

### v3.x — Transformer + Backprop (Jan-Feb 2026)
```
Volunteers (BOINC)          Server (Hetzner)
┌──────────────┐           ┌──────────────────────────────┐
│ Download QNT4 │◄──────────│ Expert Weights (QNT4)        │
│ weights ~20MB │           │                              │
│              │           │ ┌──────────────────────────┐ │
│ Train 30 SGD │           │ │ Coordinator              │ │
│ steps locally│           │ │  - Buffers 5 QNT4 deltas │ │
│              │           │ │  - Averages them          │ │
│ Upload QNT4  │──────────►│ │  - Applies to Expert A+B │ │
│ delta ~21MB  │           │ │  - GA strategy per expert │ │
│              │           │ │  - Exports winner as QNT4 │ │
└──────────────┘           │ └──────────────────────────┘ │
                           │                              │
                           │ ┌──────────────────────────┐ │
                           │ │ Optimizer Daemon (3-thread)│ │
                           │ │  - Eval thread A (BPC)    │ │
                           │ │  - Eval thread B (BPC)    │ │
                           │ │  - Optimizer (EMA perturb)│ │
                           │ │  - Tournament selection   │ │
                           │ │  - Cruise/Kick/Rollback   │ │
                           │ └──────────────────────────┘ │
                           └──────────────────────────────┘
```

### v5.x — Modular Spiking Network + STDP (Feb 26, 2026+)
```
Volunteers (BOINC)              Server (Hetzner)
┌───────────────────┐          ┌─────────────────────────────────┐
│ Download 3-module  │◄─────────│ 64 Spiking Modules (3.5M each) │
│ neighborhood ~1MB  │          │                                 │
│                   │          │ ┌─────────────────────────────┐ │
│ Train with STDP    │          │ │ Spiking Coordinator         │ │
│ (spike-timing      │          │ │  - Staleness-based assign   │ │
│  dependent         │          │ │  - Direct weight writeback  │ │
│  plasticity)       │          │ │  - Rate-mode BPC evaluation │ │
│                   │          │ │  - Module storage on disk   │ │
│ + Readout SGD      │          │ │  - Credit tracking          │ │
│ (supervised)       │          │ └─────────────────────────────┘ │
│                   │          │                                 │
│ Upload delta ~1MB │──────────►│ Topology: 64 modules, sparse   │
└───────────────────┘          │ inter-module connections (1%)   │
                               │ 3-module neighborhoods overlap  │
                               └─────────────────────────────────┘
```

## Model Specifications

### Current (v6.00+): Experiment Container Platform
- **Mode:** AI autonomously designs experiments across 11+ STEM disciplines, dispatches to matched hardware, AI judges results
- **Experiment format:** Self-contained Python scripts (numpy for CPU, CuPy for GPU), exec()'d by client
- **Scale:** 295+ experiment scripts, 40+ experiment families, 268 total hosts
- **STEM categories:** Ecology, Epidemiology, Physics, Nonlinear Dynamics, Network Science, Statistics, Machine Learning, Number Theory, Neuroscience, Population Genetics, and more
- **Credit:** AI-reviewed, quality-based (10,000 budget per validation cycle, 1-50 credit per result)
- **AI PI:** 10-step autonomous loop + dedup + significance rating, 13 cycles/day, GPT-5.4 engine
- **See:** [Axiom_v6_Experiment_Container_Record.md](Axiom_v6_Experiment_Container_Record.md)

### Previous (v5.00): Modular Spiking Network
- **Total parameters:** 224 million (64 modules x 3.5M each) + 710M synapses
- **Module architecture:** 1600 LIF neurons, W_rec (1600x1600), W_in (512x1600), W_pool (1600x64)
- **Learning rule:** STDP (unsupervised) for modules, SGD cross-entropy (supervised) for readout only
- **Inter-module connectivity:** Sparse (1% density), 190 connections across 64 modules
- **Task:** Byte-level prediction (64 bytes input → predict next byte, vocab_size=256)
- **Metric:** Bits Per Character (BPC), range 0-8, random baseline = 8.0
- **Training data:** ~10GB Project Gutenberg corpus served via dynamic PHP endpoint
- **Work unit size:** ~1MB download, ~5 min CPU per task

### Previous (v3.x): Transformer MoE
- **Total parameters:** 17.8 billion (420 experts x 42,970,000 each)
- **Expert architecture:** SimpleTransformer (d_model=768, n_heads=12, d_ff=3072, n_layers=6)

## Timeline
- **Jan 2026:** Initial BOINC infrastructure, Hebbian learning (later proven broken)
- **Feb 7, 2026:** Discovered and fixed representation collapse from Hebbian updates
- **Feb 11, 2026:** Switched to backpropagation SGD (v3.80) — first real learning
- **Feb 13, 2026:** QNT4 compression deployed, BPC improving
- **Feb 14, 2026:** Discovered sector approach broken, deployed full-model no-sector (v3.90)
- **Feb 14, 2026:** GA placement controller deployed, reactive policy active
- **Feb 15, 2026:** 3-thread optimizer daemon deployed (v3.92), EMA perturbations active
- **Feb 25, 2026:** Modular spiking network designed and tested on Vast.ai
- **Feb 26, 2026:** v5.00 deployed — first-ever volunteer-distributed STDP spiking network on language data (224M params, 64 modules, BOINC). BPC dropped from 8.0 → 5.2 in first 2 hours (readout learning; STDP self-organization experiment ongoing)
- **Feb 26, 2026:** Discovered fundamental limitation of decentralized LLM training; designed experiment container architecture (v6.00)
- **Feb 27, 2026:** Built experiment executor client, 10 numpy-only experiment scripts
- **Feb 28, 2026:** v6.04 deployed — 10 experiments across 10 volunteer machines, AI-judged credit system. First results collected (Edge of Chaos completed)
- **Mar 1, 2026:** First fully autonomous AI principal investigator run — scheduled task with zero human involvement. AI reviewed results, awarded credit, deployed 644 workunits, designed new experiment, saved scientific report. System operational on hourly schedule.
- **Mar 2, 2026:** Patreon launched for project sustainability. Conceived autonomous paper pipeline.
- **Mar 6, 2026:** v6.09 deployed — BOINC compliance update, PyInstaller extraction in slot directory
- **Mar 7, 2026:** Pool-based workunit management deployed, replacing per-host targeting. AI PI expanded to 10-step loop with error triage, performance audit, health check, security scan, dedup, and significance rating
- **Mar 7, 2026:** First AI-generated research paper published — ecology study on reactive mode localization (1,463 simulations, 17 hosts, Cohen's d > 80)
- **Mar 8, 2026:** 295+ experiment scripts deployed across 11+ STEM categories. 268 total hosts contributed. 13 autonomous cycles per day operational.

---

*This document serves as a timestamped record of invention. The git commit timestamp, together with Internet Archive Wayback Machine snapshots, provides cryptographic proof of the date these ideas were first documented publicly.*
