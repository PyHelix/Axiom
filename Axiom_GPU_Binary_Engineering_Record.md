# Axiom: Cross-Platform GPU Binary Engineering — Invention Record

**Author:** PyHelix
**Date:** March 18, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production

---

## Overview

Axiom distributes GPU-accelerated scientific experiments to volunteer machines that may or may not have CUDA development tools installed. This required solving several packaging challenges that have no published solutions.

---

## 1. NVRTC Base64 Embedding for PyInstaller

### Problem
CuPy (the GPU computing library) requires NVRTC (NVIDIA Runtime Compiler) to JIT-compile CUDA kernels at runtime. NVRTC is a shared library (libnvrtc-builtins.so on Linux, nvrtc-builtins64_*.dll on Windows) that is part of the CUDA toolkit — which volunteers typically don't have installed. PyInstaller's --add-binary flag silently fails to bundle .so files on Linux.

### Solution
Embed the NVRTC builtins library as base64-encoded Python data:

1. Convert libnvrtc-builtins.so to a Python file containing the binary as a base64 string (~7MB)
2. Include this file via PyInstaller's --add-data (which works, unlike --add-binary for .so)
3. At runtime, detect whether system CUDA is available via ldconfig -p (Linux) or registry/PATH (Windows)
4. If system CUDA exists: symlink the bundled libnvrtc.so.12 to the system's version (handles CUDA 12->13 transitions)
5. If no system CUDA: extract the base64 builtins to the PyInstaller temp directory and copy CuPy's bundled CUDA headers to _MEIPASS/include/

### What makes this novel
- **Workaround for PyInstaller .so limitation**: --add-binary silently drops shared objects on Linux. Base64 embedding via --add-data is a reliable alternative.
- **Version-aware CUDA detection**: The runtime code globs for libnvrtc.so.* across system library paths, filters out stubs and alternate versions, and picks the highest version. This handles the CUDA 12->13 transition (where the library was renamed from .so.12 to .so.13) without requiring separate binaries.
- **Self-contained GPU binary**: The resulting binary (~1.3GB Linux, ~1.1GB Windows) runs on any machine with an NVIDIA GPU driver installed, even without the CUDA toolkit.

---

## 2. CuPy RPATH Override via Symlinks

### Problem
PyInstaller-bundled CuPy shared extensions (.so files) have RPATH set to $ORIGIN, which points to the _MEIPASS temp directory. On systems with a CUDA toolkit, the bundled libnvrtc.so.12 may be older than the system's version (e.g., system has CUDA 12.8, binary bundles CUDA 12.4). Using the older bundled version causes JIT compilation failures for newer GPU architectures (e.g., Blackwell/SM_120 requires CUDA 12.8+).

### Solution
Instead of trying to override RPATH (which is baked into the ELF binary), create a symlink inside _MEIPASS that points to the system's NVRTC library:

```
_MEIPASS/libnvrtc.so.12 -> /usr/lib/x86_64-linux-gnu/libnvrtc.so.13
```

CuPy resolves libnvrtc.so.12 via RPATH $ORIGIN, finds the symlink, follows it to the system library. This gives the binary system CUDA capabilities while remaining self-contained as a fallback.

---

## 3. Cross-Platform CUDA Dependency Resolution

### Problem
GPU binaries must work on Windows (where DLLs resolve via PATH) and Linux (where .so files resolve via RPATH/LD_LIBRARY_PATH), with different CUDA versions, and with or without the CUDA toolkit installed.

### Solution
A unified detection and configuration block that runs before CuPy is imported:

**Linux:**
1. Check _MEIPASS for bundled PyInstaller context
2. Parse ldconfig -p output to find system NVRTC libraries
3. Glob for libnvrtc.so.*, filter stubs, pick highest version
4. If system CUDA found: symlink bundled lib to system lib
5. If no system CUDA: extract base64 builtins, copy CuPy headers
6. Set LD_LIBRARY_PATH to include _MEIPASS

**Windows:**
1. Check _MEIPASS for bundled context
2. Create _MEIPASS/bin/ directory (CuPy's _setup_win32_dll_directory expects it)
3. DLLs bundled via --add-binary from CUDA toolkit bin/ directory
4. Set os.environ["PATH"] to include _MEIPASS before CuPy import
5. CuPy resolves DLLs via PATH, finds bundled copies

**Package collection:**
- Must collect 3 packages: cupy, cupy_backends, fastrlock (missing any = silent CPU fallback, 0% GPU utilization with no error)
- curand DLL must be explicitly bundled on Windows (without it, CuPy's random module fails, producing constant outputs)

---

## 4. Iterative Deepening for Heterogeneous Volunteer Hardware

### Problem
Volunteer machines range from Raspberry Pi (1.5 GFLOPS) to RTX 4090 (82 TFLOPS) — a 55,000x performance range. Fixed-size computations either waste fast machines or time out slow ones. The experiment designer (an AI) cannot know in advance what hardware will run each task.

### Solution
Experiments use iterative deepening: start with a small problem size, measure execution time, estimate time for the next doubling based on known algorithmic complexity, and continue until the time budget is exhausted.

**Implementation pattern:**
```python
N = 64  # starting size
while True:
    start = time.time()
    result = run_computation(N)
    elapsed = time.time() - start
    record_result(N, result)

    # Estimate next pass: O(N^3) means 8x time per doubling
    estimated_next = elapsed * scaling_factor
    if time.time() + estimated_next > deadline:
        break
    N *= 2
```

**Key properties:**
- **Adversarial to pre-commitment**: The script never guesses how large a computation the machine can handle
- **Automatic depth adaptation**: A fast machine automatically goes deeper than a slow one
- **No wasted time**: Every machine contributes the deepest computation it can within its time window
- **Complexity-aware scaling**: The estimate uses the algorithm's known complexity class (O(N^2), O(N^3), O(N log N), etc.) to predict the next pass duration accurately

### What makes this novel
- **Applied to volunteer computing**: While iterative deepening is known in search algorithms (IDA*), applying it as a general resource-adaptive computation strategy for heterogeneous volunteer machines is a distinct use case
- **Eliminates the sizing problem**: Traditional BOINC projects either use fixed work sizes (wasting fast machines) or create different-sized workunits (requiring knowledge of host capabilities). Iterative deepening makes this unnecessary.

---

## 5. Parallel Dry-Run Validation Pipeline

### Problem
AI-designed experiment scripts may contain bugs, infinite loops, or resource violations. Deploying directly to volunteers wastes compute and risks triggering client backoff cascades.

### Solution
A two-stage design-validate-deploy pipeline, with CPU and GPU pipelines running in parallel:

**Stage 1 — Research (AI designs experiments):**
- AI reads existing findings, significance scores, and retirement registry
- Designs new experiment scripts testing novel hypotheses
- Writes scripts to the experiments directory

**Stage 2 — Dry-Run (AI validates experiments):**
- For each newly written script, run it locally on the server with a short time limit
- Verify it produces valid output matching the expected schema
- Check resource usage (RAM, disk, CPU time)
- Only scripts that pass dry-run are deployed to BOINC

**Parallel execution:**
- CPU pipeline (Research -> Dry-Run) and GPU pipeline (Research -> Dry-Run) run simultaneously
- Each pipeline is sequential internally (dry-run depends on research output)
- Both must complete before the cleanup step runs

This catches ~90% of bugs before they reach volunteers, dramatically reducing the error rate watchdog's workload.

---

*This record establishes priority for the engineering approaches described above. All systems are deployed in production on the Axiom BOINC project as of March 18, 2026.*
