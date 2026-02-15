# Axiom: Volunteer-Distributed Neural Network Training — Invention Record

**Author:** PyHelix (Foxes.owo@gmail.com)
**Date:** February 14-15, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production, processing volunteer-contributed gradients

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

---

## Architecture Summary

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

## Model Specifications
- **Total parameters:** 17.8 billion (420 experts x 42,970,000 each)
- **Expert architecture:** SimpleTransformer (d_model=768, n_heads=12, d_ff=3072, n_layers=6)
- **Task:** Byte-level prediction (64 bytes input → predict next byte, vocab_size=256)
- **Metric:** Bits Per Character (BPC), range 0-8, random baseline = 8.0
- **Training data:** ~10GB Project Gutenberg corpus served via dynamic PHP endpoint

## Timeline
- **Jan 2026:** Initial BOINC infrastructure, Hebbian learning (later proven broken)
- **Feb 7, 2026:** Discovered and fixed representation collapse from Hebbian updates
- **Feb 11, 2026:** Switched to backpropagation SGD (v3.80) — first real learning
- **Feb 13, 2026:** QNT4 compression deployed, BPC improving
- **Feb 14, 2026:** Discovered sector approach broken, deployed full-model no-sector (v3.90)
- **Feb 14, 2026:** GA placement controller deployed, reactive policy active
- **Feb 15, 2026:** 3-thread optimizer daemon deployed (v3.92), EMA perturbations active

---

*This document serves as a timestamped record of invention. The git commit timestamp provides cryptographic proof of the date these ideas were first documented publicly.*
