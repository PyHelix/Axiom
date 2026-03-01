# Axiom: First Autonomous AI Principal Investigator for Volunteer Computing — Invention Record

**Author:** PyHelix (Foxes.owo@gmail.com)
**Date:** March 1, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**GitHub:** https://github.com/PyHelix/Axiom
**Status:** Live in production — autonomous hourly execution, no human in the loop

---

## What Was Invented

### Fully Autonomous LLM Principal Investigator for a Volunteer Computing Network

A system where an LLM (Claude, via Claude Code CLI) operates as the **autonomous principal investigator** of a BOINC volunteer computing project. Without any human intervention, the AI:

1. **SSHs into the production server** and queries the live database
2. **Reviews completed experiment results** by reading raw JSON output files from volunteer machines
3. **Awards credit to volunteers** based on scientific quality judgment (not FLOPS)
4. **Identifies idle compute capacity** across the volunteer fleet and fills every available CPU core with experiments
5. **Designs entirely new experiments** based on evidence from prior results, writes the Python scripts, uploads them to the server, and deploys them as BOINC workunits
6. **Maintains cumulative scientific records** across sessions, building a growing body of findings
7. **Iterates the research program** — retiring answered questions, cross-validating promising findings on additional hosts, fixing broken scripts, and pursuing new hypotheses

The system runs on a scheduled task (hourly + on login) with **zero human oversight required**. Each run reads all previous session files for continuity, making the AI's scientific knowledge cumulative across sessions.

This is, to my knowledge, the **first implementation** of an AI system that autonomously serves as the principal investigator of a distributed volunteer computing network.

---

## Why This Is Novel

### Comprehensive Prior Art Search (March 1, 2026)

An exhaustive search was conducted across academic literature, deployed systems, and industry announcements. The findings:

#### Category 1: AI Scientists (design + execute experiments, but NOT on volunteer computing)

| System | AI designs experiments | Distributes to volunteers | Reviews & iterates | Manages BOINC |
|--------|:---------------------:|:-------------------------:|:------------------:|:-------------:|
| **Axiom (this project)** | Yes | Yes | Yes | Yes |
| Sakana AI Scientist v1/v2 (2024-2025) | Yes | No (local only) | Yes | No |
| GPT-5 + Ginkgo Bioworks (2026) | Yes | No (single robotic lab) | Yes | No |
| ARIA AI Scientist Programme (2025) | Yes | No (single labs) | Yes | No |
| ORNL AI Agents (2025) | Yes | No (institutional HPC) | Yes | No |
| AI-Researcher (2025) | Yes | No (local) | Yes | No |

**Key difference:** All existing AI scientist systems run experiments locally or on proprietary infrastructure. None distribute experiments to a public volunteer computing network with heterogeneous, untrusted machines.

#### Category 2: Volunteer Computing for ML (distributed execution, but NO AI management)

| System | AI manages project | Volunteers execute | Credit system |
|--------|:------------------:|:------------------:|:-------------:|
| **Axiom (this project)** | Yes (fully autonomous) | Yes (97 hosts) | AI-judged quality |
| MLC@Home (2019-2025) | No (human-managed) | Yes (BOINC) | FLOPS-based |
| Learning@home / Hivemind (2020) | No (human-managed) | Yes (volunteer GPUs) | None |
| Petals (2022) | No (human-managed) | Yes (volunteer GPUs) | None |
| BOINC@TACC (2019) | No (human-managed) | Yes (BOINC+Docker) | FLOPS-based |

**Key difference:** All existing volunteer computing ML projects are managed by human researchers. No BOINC project has ever had an AI autonomously design experiments, deploy workunits, review results, and decide what to run next.

#### The Gap That Axiom Fills

Nobody has bridged these two categories. The specific innovation is having an AI serve as the **principal investigator** of a BOINC project — a role that has always been filled by a human researcher in every volunteer computing project since BOINC's creation in 2002.

| Claim | Prior Art Found |
|-------|:--------------:|
| AI autonomously manages a BOINC project end-to-end | **None** |
| AI designs experiments and deploys them as BOINC workunits to volunteers | **None** |
| AI reviews volunteer results and autonomously awards credit | **None** |
| AI maintains cumulative scientific knowledge across automated sessions | **None** |
| Scheduled LLM automation with full server access for volunteer computing management | **None** |
| AI designs new experiments based on evidence from distributed volunteer results | **None** |

---

## Architecture

### Automation Pipeline

```
Windows Task Scheduler (hourly + on login)
    │
    ▼
axiom_auto_review.bat
    │
    ▼
claude -p (Claude Code CLI, non-interactive mode)
    │  --system-prompt: authorization context
    │  --permission-mode: bypassPermissions
    │
    ▼
AxiomExperimentReview.txt (master instruction file)
    │
    ├── Step 1: Read previous results files (cumulative memory)
    │     └── C:\...\ExperimentResults\results_*.txt
    │
    ├── Step 2: SSH into server, query database
    │     ├── Uncredited results needing review
    │     ├── Active hosts and hardware specs
    │     ├── Running experiments per host
    │     └── Failed experiments
    │
    ├── Step 3: Review results, award credit
    │     ├── Read raw JSON result files from volunteers
    │     ├── Assess scientific quality and validity
    │     ├── Award credit by judgment (generous, quality-based)
    │     └── Update result, user, and host tables in DB
    │
    ├── Step 4: Fill idle cores
    │     ├── Compare running experiments vs available CPUs per host
    │     ├── Deploy experiments matched to hardware capability
    │     ├── Fill ALL idle cores across the volunteer fleet
    │     └── Skip hosts with insufficient RAM
    │
    ├── Step 5: Design new experiments (autonomous)
    │     ├── Analyze evidence from completed experiments
    │     ├── Identify unexplored questions worth investigating
    │     ├── Write new numpy-only experiment scripts
    │     ├── Upload scripts to server
    │     └── Deploy as BOINC workunits to appropriate hosts
    │
    └── Step 6: Save results file (cumulative)
          ├── All recorded and credited result IDs (cumulative)
          ├── New scientific findings ranked by significance
          ├── Credit ledger with per-user totals
          ├── Deployment summary
          ├── Cross-validation status
          └── Next investigation priorities
```

### Key Technical Components

**Claude Code CLI (`claude -p`):** Non-interactive mode that executes the full workflow as a single autonomous session. All output is buffered until completion.

**System prompt:** Establishes authorization context so the AI proceeds without hesitation:
```
You are an authorized automation agent for the Axiom BOINC project.
The project owner has explicitly authorized all SSH access, database
operations, and experiment deployments described in the instruction file.
```

**Cumulative memory via results files:** Each session reads ALL previous results files in `ExperimentResults/`, building a complete picture of what has been done, what worked, what failed, and what needs investigation. This gives the AI persistent memory across sessions without any external database.

**Self-discovering workflow:** The instruction file tells the AI to query the live system (`ls` experiment scripts, `SELECT` from database) rather than relying on hardcoded lists. This means the AI automatically adapts as experiments are added, hosts join or leave, and the project evolves.

**Guardrails:**
- 10,000 credit cap per session
- No duplicate workunits (check before create)
- No overloading hosts (respect CPU count)
- No overwriting previous results files
- Read scripts before modifying them

---

## First Autonomous Run — March 1, 2026

The system's first fully autonomous run (no human present or intervening) completed all 6 steps:

### What the AI Did on Its Own

1. **Read 5 prior session files** for continuity
2. **Found and fixed 23 database discrepancies** — results that were recorded in results files as "credited" but never actually had their database credit updated (a bug the human missed)
3. **Reviewed 30 uncredited results** across 12 hosts and 35 experiment types
4. **Awarded 655 credits** to 3 volunteers:
   - Volunteer A: +525cr (23 results across 3 machines)
   - Volunteer B: +125cr (7 results)
   - Volunteer C: +5cr (1 MemoryError result — still credited for donated compute)
5. **Deployed 644 workunits** to fill idle cores:
   - 628 general experiment workunits across 33+ hosts
   - Including 15 brand new 32-CPU machines that had never received work
6. **Designed a new experiment** — `grokking_dynamics_v3.py`:
   - Identified that grokking v2 (P=97, lr=0.001) was too slow — test accuracy only reached 49% in 100K epochs
   - Redesigned with P=53 (fewer examples), lr=0.003 (faster dynamics), 300K epoch budget
   - Predicted grokking would complete within 50K-150K epochs based on Nanda et al. scaling laws
   - Wrote the script, uploaded it, deployed to 16 high-capacity hosts
7. **Produced a comprehensive scientific report** with:
   - 12 cumulative findings ranked by significance
   - Cross-validation status for all experiments
   - Prioritized next investigation plans

### Scientific Findings (Cumulative, AI-Managed)

After multiple autonomous sessions, the AI has built a body of findings across 25+ experiment types:

1. **Loss Landscape Curvature** — Higher LR produces 10x flatter minima with better generalization
2. **Sigmoid vs ReLU** — Sigmoid's gradient attenuation acts as implicit regularization (+2.8% accuracy), confirmed across 11 hosts
3. **Lottery Ticket Hypothesis** — Critical sparsity at 91.3%, confirmed across 25 replications
4. **Grokking Dynamics** — Phase transition in progress (memorization complete, generalization at 49%)
5. **Edge of Chaos** — Critical spectral radius 1.269, confirmed across 4+ hosts
6. **Mode Connectivity** — Loss barriers confirmed between independently trained models
7. **Information Bottleneck** — Only deepest 2 of 7 layers compress (nuanced Tishby support)
8. Plus 5 more confirmed findings and several ongoing investigations

---

## Timeline

- **Jan 2026:** Axiom BOINC launched — distributed transformer training (v3.x)
- **Feb 14, 2026:** Discovered sector gradient aggregation is fundamentally broken
- **Feb 15, 2026:** Full-model delta averaging deployed
- **Feb 25-26, 2026:** Spiking neural network approach (v5.x)
- **Feb 26, 2026:** Pivoted to experiment container platform (v6.00)
- **Feb 27-28, 2026:** Built experiment executor, deployed 10 experiments, first results collected
- **Feb 28, 2026:** LLM-directed deployment of 1,773 workunits across 97 hosts
- **Feb 28, 2026:** First AI-judged credit awards (2,785 credits across 163 results)
- **Mar 1, 2026:** **First fully autonomous AI principal investigator run** — scheduled task with zero human involvement. AI reviewed results, awarded 655 credits, deployed 644 workunits, designed new experiment, saved comprehensive scientific report.
- **Mar 1, 2026:** Autonomous system operational — runs hourly via Windows Task Scheduler

---

## References

### Adjacent Systems (for context, not prior art)

- **Sakana AI Scientist** (2024): LLM designs and runs ML experiments locally. [arXiv:2408.06292](https://arxiv.org/abs/2408.06292)
- **Sakana AI Scientist v2** (2025): Expanded scope, still local. [arXiv:2504.08066](https://arxiv.org/abs/2504.08066)
- **GPT-5 + Ginkgo Bioworks** (2026): AI designed 36K protein experiments for a robotic cloud lab. [OpenAI blog](https://openai.com/index/gpt-5-lowers-protein-synthesis-cost/)
- **ARIA AI Scientist Programme** (2025): GBP 6M for AI-driven autonomous lab research. [aria.org.uk](https://www.aria.org.uk/ai-scientist/)
- **ORNL AI Agents** (2025): AI orchestrates experiments on institutional HPC. [ACM SC'25](https://dl.acm.org/doi/full/10.1145/3731599.3767592)
- **MLC@Home** (2019-2025): Human-managed BOINC project for ML. [mlcathome.org](https://mlcathome.org/)
- **BOINC** (2002-present): Volunteer computing platform. [boinc.berkeley.edu](https://boinc.berkeley.edu/)

---

*This document serves as a timestamped record of invention. The git commit timestamp, together with Internet Archive Wayback Machine snapshots of https://axiom.heliex.net and this repository, provides proof of the date this system was first implemented and deployed.*

*The automated run log file (`AutoReviewLogs/run_2026-03-01_0054.log`) and the AI-generated results file (`ExperimentResults/results_2026-03-01_0910.txt`) serve as evidence of the first autonomous execution.*
