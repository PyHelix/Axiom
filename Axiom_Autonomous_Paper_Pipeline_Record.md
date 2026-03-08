# Axiom Invention Record: Fully Autonomous Scientific Discovery-to-Publication Pipeline

**Date of Conception:** March 2, 2026  
**Inventor:** Cameron Schive (PyHelix)  
**Platform:** Axiom Distributed AI (https://axiom.heliex.net)

---

## Abstract

This document records the conception and initial design of a fully autonomous scientific discovery-to-publication pipeline — a system where an AI agent autonomously generates research hypotheses, designs experiments, deploys them to a distributed volunteer compute network, analyzes results, determines when findings reach publication-ready statistical significance, drafts complete academic papers (including literature review, methodology, results, discussion, and references), and submits them to academic venues — all without human intervention.

## Background

As of March 2026, the Axiom project has demonstrated the first two stages of this pipeline:

1. **Autonomous Discovery** — An AI principal investigator (Claude, Anthropic) designs experiments across 11+ STEM categories, deploys them to a BOINC volunteer computing network of 268 hosts, reviews results, awards credit, and iterates. The system runs 13 cycles per day (~every 1.2 hours) with zero human intervention and has produced 295+ experiment scripts across 11+ STEM categories, including genuinely novel results (e.g., the width-compositionality mechanistic chain: rank collapse → critical period → intervention).

2. **Prior Art Recording** — Findings are automatically committed to GitHub with timestamps (https://github.com/PyHelix/Axiom) and published to a public results page (https://axiom.heliex.net/experiment_results/).

The missing stage is **autonomous paper writing and submission**.

## The Invention: Autonomous Paper Pipeline

### Concept

A second AI agent monitors the findings database and, when a finding or cluster of related findings reaches sufficient statistical confirmation (e.g., 30+ independent seeds, cross-validated across multiple hosts), autonomously:

1. **Determines publication readiness** — Checks seed count, cross-validation status, effect size, and consistency across hardware.

2. **Conducts literature review** — Searches academic databases (Semantic Scholar, arXiv, Google Scholar) to:
   - Verify the finding is novel (not already published)
   - Identify related prior work for the introduction and discussion
   - Find the appropriate venue (conference, journal, workshop)

3. **Drafts the paper** — Generates a complete academic manuscript:
   - Title, abstract, introduction with motivation
   - Related work section with proper citations
   - Methodology (experiment design, network architecture, training details)
   - Results with statistical analysis
   - Discussion of implications, limitations, and future work
   - References in proper format

4. **Generates figures** — Creates publication-quality plots and tables from the raw experimental data.

5. **Self-reviews** — Runs the draft through a review agent that checks for:
   - Statistical validity
   - Overclaiming
   - Missing references
   - Methodological gaps
   - Clarity and readability

6. **Submits** — Uploads to arXiv and/or submits to appropriate venues via their submission systems.

### Architecture

```
Discovery Agent (exists)          Paper Agent (proposed)
┌─────────────────────┐          ┌─────────────────────┐
│ Design experiments  │          │ Monitor findings DB  │
│ Deploy to BOINC     │──────►   │ Check pub-readiness  │
│ Review results      │          │ Literature review    │
│ Update findings DB  │          │ Draft paper          │
│ Git push prior art  │          │ Generate figures     │
│ Design next expt    │          │ Self-review          │
└─────────────────────┘          │ Submit to arXiv      │
     Runs 13 cycles/day            └─────────────────────┘
                                      Runs on trigger
```

### Key Design Decisions

- **Separation of concerns**: The discovery agent and paper agent are independent. The discovery agent focuses on experimental throughput. The paper agent focuses on publication quality.
- **Trigger-based, not continuous**: The paper agent activates only when findings cross the confirmation threshold, not on a fixed schedule.
- **Human-in-the-loop option**: Initially, papers could be drafted for human review before submission. Full autonomy is the end goal.
- **Cost optimization**: Use smaller/cheaper models (e.g., Claude Haiku, GPT-4o-mini) for first drafts; larger models (Claude Opus) for final review and polishing.
- **Clustering related findings**: Multiple related findings (e.g., the width-compositionality chain) should be bundled into a single coherent paper rather than published individually.

## Prior Art Analysis

As of March 2026, no existing system implements this complete pipeline:

- **Agent Laboratory** (2025) — Accepts human-provided research ideas and autonomously does lit review, experimentation, and report writing. Does NOT generate its own hypotheses or deploy to distributed compute.
- **SciSpace** — AI writing assistant with 280M paper index. Assists humans, not autonomous.
- **Elicit** — Literature review and data extraction tool. Assists humans, not autonomous.
- **Paper2Agent** (2025) — Converts existing papers into interactive agents. Reverse direction.
- **The AI Scientist** (Sakana AI, 2024) — Generates ideas, writes code, runs experiments, writes papers. Closest prior art, but operates on a single machine, not distributed compute, and does not include volunteer computing or real-time experiment management.

The Axiom pipeline would be the first to combine:
1. Autonomous hypothesis generation
2. Distributed volunteer compute for experimentation
3. AI-judged credit and result validation
4. Autonomous paper drafting and submission
5. Continuous operation (24/7, no human in the loop)

## Current Status

- **Stage 1 (Discovery)**: Operational. 295+ experiment scripts, 40+ experiment families, 11+ STEM categories. 13 autonomous cycles per day.
- **Stage 2 (Prior Art)**: Operational. GitHub timestamps + public results page + public findings page with significance scores.
- **Stage 3 (Paper Writing)**: **First paper published March 7, 2026.** Ecology study on reactive mode localization in random community matrices. Based on 1,463 simulations across 17 volunteer hosts.

## Implementation — March 7, 2026

The pipeline produced its first publication on March 7, 2026:

**Title:** *"Species-Level Interaction Heterogeneity Localizes Reactive Modes and Widens the Stable-but-Reactive Window in Random Ecological Communities"*

**Data source:** 1,463 independent simulations run across 17 volunteer hosts via the Axiom BOINC network. Cohen's d > 80 (extremely large effect size).

**Pipeline stages demonstrated:**
1. AI designed the ecology experiment (random community matrix with species-level heterogeneity)
2. Experiments deployed to volunteer machines as BOINC workunits
3. Results collected and analyzed autonomously by the AI PI
4. Statistical significance confirmed (effect size, sample size, sign consistency)
5. Paper drafted from the experimental findings
6. Published at https://axiom.heliex.net/reactivity_localization_paper.pdf

Available at: https://axiom.heliex.net/reactivity_localization_paper.pdf

## Estimated Impact

With the first paper published on March 7, 2026, the pipeline is now operational rather than speculative. At the current discovery rate of 1-2 novel findings per day across 11+ STEM categories, the system has demonstrated end-to-end autonomous scientific publication — from hypothesis generation through distributed experimentation to a finished paper — with zero human researchers. Production rate is expected to scale as the paper agent matures and additional findings cross the confirmation threshold.

---

**This document serves as a timestamped record of invention for the fully autonomous scientific discovery-to-publication pipeline concept as applied to distributed volunteer computing.**

**Witnesses:** This document was committed to a public GitHub repository (https://github.com/PyHelix/Axiom) on March 2, 2026, establishing the date of conception.
