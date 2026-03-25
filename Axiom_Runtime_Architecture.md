# Axiom: Runtime Architecture — How Experiments Run on Volunteer Machines

**Author:** PyHelix
**Date:** March 25, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net

---

## Overview

Axiom uses a two-layer architecture: a large client binary that provides the runtime environment, and small experiment scripts that define the specific computation. This is the same pattern used by scientific computing frameworks — a heavyweight runtime with lightweight job inputs.

## Client Binary (the runtime)

The client binary is a PyInstaller-bundled Python application containing:

- **CPU version**: ~25MB — bundles Python, NumPy, websockets, psutil, and all dependencies into a single executable
- **GPU version**: ~1.8GB — additionally bundles CuPy (GPU-accelerated NumPy), CUDA NVRTC compiler, and CUDA runtime libraries

The binary is downloaded once when a volunteer attaches to the project. It provides:
- NumPy/CuPy for matrix operations, FFT, linear algebra, eigenvalue decomposition
- Iterative deepening bisection framework for threshold-finding experiments
- BOINC integration (progress reporting, result upload, checkpoint handling)
- Automatic GPU detection and VRAM-based batch sizing
- Seed data download for training tasks

## Experiment Scripts (the job input)

Each work unit specifies a small Python script (~10-20KB) that defines ONE specific experiment. The script is downloaded from the project server at task start. It contains:
- The mathematical model to test (e.g., a random matrix ensemble with specific structure)
- The parameter to bisect (the threshold being searched for)
- The observable to measure (e.g., spectral gap, bridge mass, localization score)
- Matrix size scaling schedule (N=64 → 128 → 256 → ... → 2048+)
- Result formatting (fitness score, bracket width, final measurements)

The script does NOT contain a neural network, model weights, or training data. It is a computational experiment — analogous to a physics simulation or statistical test.

## Why the Script is Small

The 10-20KB script size is appropriate because:

1. **The heavy libraries are in the binary.** NumPy (25MB compiled), CuPy (1.5GB with CUDA), and all math routines are already on the volunteer's machine inside the client binary. The script just calls functions like `numpy.linalg.eigh()` or `numpy.random.randn()`.

2. **Experiments are algorithmically defined.** A matrix experiment doesn't need stored data — it generates random matrices with specific mathematical structure and measures their properties. The entire experiment is defined by a few parameters and a measurement function.

3. **This is standard scientific computing.** CERN's analysis scripts are small Python files that call ROOT (a large C++ framework). Folding@Home's work units are small input files that run on a large molecular dynamics engine. The pattern of "small job description + large runtime" is universal in distributed computing.

## Execution Flow

```
1. BOINC downloads work unit (JSON, ~1KB) → contains experiment name + parameters
2. Wrapper launches client binary (~25MB CPU / ~1.8GB GPU)
3. Client reads work unit, downloads experiment script (~15KB) from project server
4. Client executes script using bundled NumPy/CuPy runtime
5. Script runs iterative deepening: start small (N=64), bisect parameter, double N, repeat
6. Each pass saves intermediate results (fitness score, bracket, measurements)
7. After time budget expires (15 min CPU, 30 min GPU), final result uploaded
8. BOINC reports result to server for validation and credit
```

## What the Computation Actually Does

Each experiment searches for a **critical threshold** in a mathematical system — the exact parameter value where a phase transition occurs. This is done via binary search (bisection) at increasing system sizes.

Example: "Does adding Toeplitz correlation structure to a heavy-tailed random matrix rescue a planted spike signal?" The experiment:
1. Generates random matrices with specific structure at size N
2. Bisects a correlation parameter to find where spike detection transitions from impossible to possible
3. Doubles N and repeats with a narrower bracket
4. Reports the threshold estimate, bracket width, and statistical measurements

The result is a precise numerical measurement of a mathematical phenomenon — reproducible, verifiable, and scientifically meaningful.

## Verification

- All experiment scripts are served from `https://axiom.heliex.net/experiments/` and can be inspected
- Results are sanitized (numbers only, no strings) before AI analysis
- 0.5% of tasks are randomly duplicated for verification (cosine similarity check)
- The project architecture, prompts, and codex loop are open source at `https://github.com/PyHelix/Axiom`
