#!/usr/bin/env python3
# BOINC mode: limit BLAS threads before numpy loads
import sys as _sys, os as _os
_sys.stdout = _sys.stderr  # BOINC only captures stderr
if len(_sys.argv) > 1 and not _sys.argv[1].startswith("--"):
    _ncpus = _os.cpu_count() or 4
    _threads = str(max(1, min(4, _ncpus // 3)))  # 3 threads per task, cap at 4
    _os.environ["OPENBLAS_NUM_THREADS"] = _threads
    _os.environ["MKL_NUM_THREADS"] = _threads
    _os.environ["OMP_NUM_THREADS"] = _threads
"""
Axiom Streaming Client - Async Hebbian Learning

Replaces BOINC work unit model with continuous streaming for sample-by-sample
Hebbian learning. "Neurons that fire together, wire together."

Key features:
- Async data loading (never blocks training)
- Backprop SGD (proper gradient descent, not Hebbian)
- WebSocket weight sync (gossip protocol, non-blocking)
- Sample-by-sample processing (1000+ samples/sec vs BOINC's 1-2)

Usage:
    python axiom_streaming_client.py [--expert N] [--server ws://host:port]
"""

import asyncio
import json
import os
import sys
import time
import struct
import random
import math
import base64
import hashlib
import argparse
import signal
from pathlib import Path
from dataclasses import dataclass, field

# ── Ecosystem mode constants ──
ECOSYSTEM_PORT = 8767
from typing import Optional, List
import queue
import threading
import gc
import psutil
import zipfile
import gzip
import tarfile
import bz2
import lzma
import io

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# CuPy kernel cache - keep within BOINC working directory (not user home)
import os as _os
_cupy_cache = _os.path.join(_os.getcwd(), '.cupy_cache')
_os.makedirs(_cupy_cache, exist_ok=True)
_os.environ['CUPY_CACHE_DIR'] = _cupy_cache

from simple_ml import MoEConfig, ExpertWorker, HAS_NUMPY, HAS_GPU, to_cpu, _real_numpy
if HAS_NUMPY:
    import numpy as np


def _cleanup_stale_mei_dirs():
    """Clean up stale PyInstaller _MEI* extraction directories.
    
    PyInstaller --onefile binaries extract to _MEI* temp dirs each run.
    If tasks crash or get killed, these accumulate (21-27MB CPU, 1.1GB GPU).
    This removes stale ones older than 2 hours.
    """
    import tempfile
    import shutil
    
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        return
    
    current_mei = os.path.basename(meipass)
    temp_dir = tempfile.gettempdir()
    stale_threshold = 2 * 3600
    now = time.time()
    cleaned = 0
    freed_bytes = 0
    
    try:
        for entry in os.listdir(temp_dir):
            if not entry.startswith("_MEI"):
                continue
            if entry == current_mei:
                continue
            
            mei_path = os.path.join(temp_dir, entry)
            if not os.path.isdir(mei_path):
                continue
            
            # v6.07 safety: only delete Axiom _MEI dirs (contain simple_ml)
            is_axiom = False
            try:
                mei_files = os.listdir(mei_path)
                for mf in mei_files:
                    if mf.startswith("simple_ml"):
                        is_axiom = True
                        break
            except OSError:
                pass
            if not is_axiom:
                continue
            
            try:
                mtime = os.path.getmtime(mei_path)
                if now - mtime < stale_threshold:
                    continue
            except OSError:
                continue
            
            try:
                dir_size = 0
                for dirpath, dirnames, filenames in os.walk(mei_path):
                    for f in filenames:
                        try:
                            dir_size += os.path.getsize(os.path.join(dirpath, f))
                        except OSError:
                            pass
                
                shutil.rmtree(mei_path, ignore_errors=True)
                
                if not os.path.exists(mei_path):
                    cleaned += 1
                    freed_bytes += dir_size
            except Exception:
                pass
    except Exception as e:
        print(f"[Cleanup] MEI scan error: {e}")
    
    if cleaned > 0:
        print(f"[Cleanup] Removed {cleaned} stale _MEI* dirs ({freed_bytes / (1024*1024):.0f}MB freed)")


_cleanup_stale_mei_dirs()

# Optional WebSocket support
HAS_WEBSOCKETS = False
try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    print("[Client] websockets not installed - running in offline mode")
    print("[Client] Install with: pip install websockets")


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class ClientConfig:
    """Configuration for the streaming client."""
    # Expert assignment (-1 = request from coordinator)
    expert_idx: int = -1
    auto_assign: bool = True  # Request expert assignment from coordinator

    # User authentication (for credit tracking)
    authenticator: str = ""

    # Model configuration (matches server's MoE config)
    input_size: int = 64
    output_size: int = 256
    num_experts: int = 420
    expert_hidden: int = 3072       # Full transformer FFN size
    expert_layers: int = 6          # Full depth
    expert_type: str = "transformer"  # 42.6M params per expert → 17.8B total
    d_model: int = 768
    n_heads: int = 12

    # Training batch size (for Hebbian updates)
    mini_batch_size: int = 1        # True sample-by-sample Hebbian learning

    # Hebbian learning parameters
    hebbian_lr: float = 0.005
    hebbian_decay: float = 0.999

    # Sync parameters
    sync_interval: int = 100  # Sync weights every N samples
    gossip_alpha: float = 0.15  # 85/15 split: 85% local, 15% peer

    # Server connection
    server_url: str = "ws://65.21.196.61:8765"
    model_server_url: str = "https://axiom.heliex.net"

    # Data paths
    contribute_path: Optional[Path] = None
    cache_path: Optional[Path] = None

    # Performance
    data_buffer_size: int = 500   # Reduced from 10000 to save memory
    batch_report_interval: int = 100

    def __post_init__(self):
        if self.contribute_path is None:
            self.contribute_path = Path.home() / "Axiom" / "contribute"
        if self.cache_path is None:
            self.cache_path = Path.home() / "Axiom" / ".cache"


# =============================================================================
# Data Producer (Async file reading)
# =============================================================================

class DataProducer:
    """Async producer that continuously reads local files into a sample queue."""

    EXCLUDED_PATTERNS = (
        '*.env', '*.pem', '*.key', '*.p12', '*.pfx',
        '*password*', '*secret*', '*credential*'
    )

    # Magic bytes for file type detection
    MAGIC_BYTES = (
        # Archives (extractable via stdlib)
        (b'PK\x03\x04', 'zip'),        # ZIP, DOCX, XLSX, JAR, etc.
        (b'\x1f\x8b', 'gzip'),         # GZIP
        (b'BZh', 'bz2'),              # BZIP2
        (b'\xfd7zXZ', 'xz'),          # XZ
        # Compressed/binary (skip entirely - unlearnable)
        (b'\x89PNG', 'binary'),        # PNG
        (b'\xff\xd8\xff', 'binary'),   # JPEG
        (b'GIF8', 'binary'),           # GIF
        (b'\x00\x00\x01\x00', 'binary'),  # ICO
        (b'RIFF', 'binary'),           # WAV, AVI, WEBP
        (b'ID3', 'binary'),            # MP3 with ID3 tag
        (b'\xff\xfb', 'binary'),       # MP3 frame
        (b'\xff\xf3', 'binary'),       # MP3 frame
        (b'OggS', 'binary'),           # OGG
        (b'fLaC', 'binary'),           # FLAC
        (b'\x1aE\xdf\xa3', 'binary'),  # MKV/WEBM
        (b'7z\xbc\xaf', 'binary'),     # 7z (not stdlib extractable)
        (b'Rar!', 'binary'),           # RAR (not stdlib extractable)
        (b'\x28\xb5\x2f\xfd', 'binary'),  # ZSTD (not stdlib)
        (b'\x04\x22\x4d\x18', 'binary'),  # LZ4 (not stdlib)
    )

    # Archive extraction budget
    ARCHIVE_TOTAL_BUDGET = 1024 * 1024   # 1MB total per archive
    ARCHIVE_PER_FILE_CAP = 50 * 1024     # 50KB per inner file
    ARCHIVE_MAX_FILES = 20               # Max files sampled per archive

    def __init__(self, config: ClientConfig):
        self.config = config
        self.queue = asyncio.Queue(maxsize=config.data_buffer_size)
        self.running = False
        self.files_processed = 0
        self.samples_produced = 0
        self.warned_compressed = set()  # Track files we've warned about

    def classify_file(self, filepath: Path) -> str:
        """Classify file by magic bytes. Returns archive type, 'binary', or 'readable'."""
        try:
            with open(filepath, 'rb') as f:
                header = f.read(12)
            if len(header) < 2:
                return 'readable'
            for magic, ftype in self.MAGIC_BYTES:
                if header.startswith(magic):
                    return ftype
            # Check MP4/MOV (ftyp at offset 4)
            if len(header) >= 8 and header[4:8] == b'ftyp':
                return 'binary'
            # Check for tar by extension (magic is at offset 257, too far for quick check)
            if filepath.suffix.lower() in ('.tar', '.tgz', '.tbz2', '.txz'):
                return 'tar'
            return 'readable'
        except Exception:
            return 'readable'

    def _is_binary_content(self, data: bytes) -> bool:
        """Check if raw bytes appear to be binary/compressed (not readable)."""
        if len(data) < 4:
            return False
        for magic, ftype in self.MAGIC_BYTES:
            if data.startswith(magic):
                return True
        if len(data) >= 8 and data[4:8] == b'ftyp':
            return True
        # High ratio of null bytes = binary
        check_len = min(len(data), 512)
        null_ratio = data[:check_len].count(0) / check_len
        return null_ratio > 0.3

    def _extract_archive(self, filepath: Path, archive_type: str) -> bytes:
        """Extract readable content from archive with budget limits."""
        result = bytearray()
        files_read = 0

        try:
            if archive_type == 'zip':
                with zipfile.ZipFile(filepath, 'r') as zf:
                    for info in zf.infolist():
                        if files_read >= self.ARCHIVE_MAX_FILES:
                            break
                        if info.is_dir() or info.file_size == 0:
                            continue
                        with zf.open(info) as f:
                            chunk = f.read(self.ARCHIVE_PER_FILE_CAP)
                        if self._is_binary_content(chunk):
                            continue
                        result.extend(chunk)
                        files_read += 1
                        if len(result) >= self.ARCHIVE_TOTAL_BUDGET:
                            break

            elif archive_type == 'gzip':
                with gzip.open(filepath, 'rb') as f:
                    chunk = f.read(self.ARCHIVE_TOTAL_BUDGET)
                if not self._is_binary_content(chunk):
                    result.extend(chunk)

            elif archive_type == 'bz2':
                with bz2.open(filepath, 'rb') as f:
                    chunk = f.read(self.ARCHIVE_TOTAL_BUDGET)
                if not self._is_binary_content(chunk):
                    result.extend(chunk)

            elif archive_type == 'xz':
                with lzma.open(filepath, 'rb') as f:
                    chunk = f.read(self.ARCHIVE_TOTAL_BUDGET)
                if not self._is_binary_content(chunk):
                    result.extend(chunk)

            elif archive_type == 'tar':
                with tarfile.open(filepath, 'r:*') as tf:
                    for member in tf.getmembers():
                        if files_read >= self.ARCHIVE_MAX_FILES:
                            break
                        if not member.isfile() or member.size == 0:
                            continue
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        chunk = f.read(self.ARCHIVE_PER_FILE_CAP)
                        if self._is_binary_content(chunk):
                            continue
                        result.extend(chunk)
                        files_read += 1
                        if len(result) >= self.ARCHIVE_TOTAL_BUDGET:
                            break

        except Exception as e:
            print(f"[DataProducer] Archive extract error {filepath.name}: {e}")

        return bytes(result[:self.ARCHIVE_TOTAL_BUDGET])

    def should_exclude(self, filepath: Path) -> bool:
        """Check if file should be excluded from training."""
        name_lower = str(filepath).lower()
        for pattern in self.EXCLUDED_PATTERNS:
            if pattern.startswith('*') and pattern.endswith('*'):
                if pattern[1:-1] in name_lower:
                    return True
            elif pattern.startswith('*'):
                if name_lower.endswith(pattern[1:]):
                    return True
        parts = filepath.parts
        if any(p in ('.git', '.ssh', '.gnupg', '__pycache__', 'node_modules') for p in parts):
            return True
        return False

    def check_data_quality(self, data: bytes, filename: str = "") -> tuple:
        """
        Check if data is worth training on. Returns (ok, reason).
        Rejects random/encrypted data (no learnable patterns) and
        constant/near-constant data (degenerate training).
        """
        if len(data) < 64:
            return False, "too small"

        # Sample first 4KB for quick analysis
        sample = data[:4096]

        # Byte-level Shannon entropy
        freq = [0] * 256
        for b in sample:
            freq[b] += 1
        n = len(sample)
        entropy = 0.0
        for f in freq:
            if f > 0:
                p = f / n
                entropy -= p * math.log2(p)

        # Random/encrypted data: entropy ~8.0 bits/byte (no patterns to learn)
        if entropy > 7.0:
            return False, "high-entropy (entropy={:.2f})".format(entropy)

        # Constant/near-constant data: entropy ~0 (degenerate)
        if entropy < 0.5:
            return False, "near-constant (entropy={:.2f})".format(entropy)

        # Bit balance check: count 1s vs 0s
        ones = sum(bin(b).count('1') for b in sample)
        total_bits = n * 8
        balance = ones / total_bits  # 0.5 = perfect balance
        if balance > 0.95 or balance < 0.05:
            return False, "extreme bit skew ({:.2f})".format(balance)

        return True, "ok"

    async def start(self):
        """Start producing samples."""
        self.running = True
        self.config.contribute_path.mkdir(parents=True, exist_ok=True)

        print(f"[DataProducer] Reading from: {self.config.contribute_path}")

        while self.running:
            files = []
            for filepath in sorted(self.config.contribute_path.rglob('*')):
                if filepath.is_file() and not self.should_exclude(filepath):
                    files.append(filepath)

            random.shuffle(files)
            if not files:
                print("[DataProducer] No files found, waiting...")
                await asyncio.sleep(5)
                continue

            for filepath in files:
                if not self.running:
                    break

                file_type = self.classify_file(filepath)

                if file_type == 'binary':
                    if filepath not in self.warned_compressed:
                        self.warned_compressed.add(filepath)
                        print(f"[DataProducer] Skipping binary file: {filepath.name}")
                    continue

                if file_type in ('zip', 'gzip', 'bz2', 'xz', 'tar'):
                    try:
                        data = self._extract_archive(filepath, file_type)
                        if data:
                            await self._process_bytes(data)
                            self.files_processed += 1
                    except Exception as e:
                        print(f"[DataProducer] Archive error {filepath.name}: {e}")
                    continue

                try:
                    with open(filepath, 'rb') as f:
                        raw_data = f.read(10 * 1024 * 1024)
                    ok, reason = self.check_data_quality(raw_data, filepath.name)
                    if not ok:
                        if filepath not in self.warned_compressed:
                            self.warned_compressed.add(filepath)
                            print("[DataProducer] Skipping {}: {}".format(filepath.name, reason))
                        continue
                    byte_arr = _real_numpy.frombuffer(raw_data, dtype=_real_numpy.uint8)
                    await self._process_bytes_to_samples(byte_arr)
                    self.files_processed += 1
                except Exception as e:
                    print(f"[DataProducer] Error reading {filepath}: {e}")

            # Small delay before re-scanning
            await asyncio.sleep(0.1)

    async def _process_file(self, filepath: Path):
        """Read a file and produce samples from it."""
        with open(filepath, 'rb') as f:
            data = f.read(10 * 1024 * 1024)  # 10MB cap per file
        byte_arr = np.frombuffer(data, dtype=np.uint8)
        await self._process_bytes_to_samples(byte_arr)

    async def _process_bytes(self, data: bytes):
        """Convert raw bytes to byte-level samples and queue them."""
        byte_arr = np.frombuffer(data, dtype=np.uint8)
        await self._process_bytes_to_samples(byte_arr)

    async def _process_bytes_to_samples(self, byte_arr):
        """Generate batched byte-level samples and queue them.
        Each sample: 64 input bytes + 1 target byte (predict next byte)."""
        window_size = self.config.input_size  # 64 bytes
        if not isinstance(byte_arr, np.ndarray):
            byte_arr = np.array(byte_arr, dtype=np.uint8)
        n_samples = len(byte_arr) - window_size
        if n_samples <= 0:
            return

        # Create sliding windows of input_size+1 (last byte is target)
        windows = np.lib.stride_tricks.sliding_window_view(byte_arr, window_size + 1)
        n_samples = min(n_samples, len(windows))
        all_inputs = windows[:n_samples]  # each row is [input_bytes..., target_byte]

        # Queue pre-built numpy batches
        batch_size = max(self.config.mini_batch_size, 1)
        for start in range(0, n_samples, batch_size):
            end = min(start + batch_size, n_samples)
            # Copy from view and convert to float32
            batch = np.array(all_inputs[start:end], dtype=np.float32)
            await self.queue.put(batch)
            self.samples_produced += end - start
            batches_queued += 1
            # Yield control periodically, not every batch
            if batches_queued % 100 == 0:
                await asyncio.sleep(0)

    async def get_sample(self):
        """Get next sample from queue."""
        return await self.queue.get()

    def preload_all(self):
        """Preload all training data into a single uint8 byte array. For GPU crunch mode."""
        all_bytes = []
        files = 0
        contribute = self.config.contribute_path
        contribute.mkdir(parents=True, exist_ok=True)

        for filepath in sorted(contribute.rglob('*')):
            if not filepath.is_file() or self.should_exclude(filepath):
                continue

            file_type = self.classify_file(filepath)
            if file_type == 'binary':
                continue

            try:
                if file_type in ('zip', 'gzip', 'bz2', 'xz', 'tar'):
                    data = self._extract_archive(filepath, file_type)
                else:
                    with open(filepath, 'rb') as f:
                        data = f.read(10 * 1024 * 1024)  # 10MB cap per file

                if data:
                    ok, reason = self.check_data_quality(data, filepath.name)
                    if not ok:
                        print("[DataProducer] Skipping {}: {}".format(filepath.name, reason))
                        continue
                    byte_arr = np.frombuffer(data, dtype=np.uint8)
                    all_bytes.append(byte_arr)
                    files += 1
            except Exception as e:
                print(f"[DataProducer] Preload error {filepath.name}: {e}")

        if not all_bytes:
            return np.array([], dtype=np.uint8)

        combined = np.concatenate(all_bytes)
        n_samples = len(combined) - self.config.input_size
        print(f"[DataProducer] Preloaded {files} files -> {n_samples:,} byte-level samples ({len(combined):,} bytes)")
        return combined

    def stop(self):
        """Stop producing samples."""
        self.running = False


# =============================================================================
# Worker Self-Test
# =============================================================================

def evaluate_expert_bpc(expert_obj, holdout_bytes, input_size=64):
    """Quick self-test: evaluate expert on holdout data (byte-level).
    Returns (bpc, accuracy, n_samples).
    Uses small batches on GPU to avoid OOM after training.
    """
    _np = _real_numpy
    byte_arr = _np.frombuffer(holdout_bytes, dtype=_np.uint8)
    if len(byte_arr) < input_size + 1:
        return 8.0, 0.004, 0

    step = 51 if HAS_GPU else 256
    n_windows = (len(byte_arr) - input_size) // step
    if n_windows == 0:
        return 8.0, 0.004, 0

    contexts = _np.zeros((n_windows, input_size), dtype=_np.float32)
    targets = _np.zeros(n_windows, dtype=_np.int32)
    for j, i in enumerate(range(0, len(byte_arr) - input_size, step)):
        if j >= n_windows:
            break
        contexts[j] = byte_arr[i:i + input_size].astype(_np.float32)
        targets[j] = int(byte_arr[i + input_size])

    try:
        # Clear CUDA error state and free memory before eval
        if HAS_GPU:
            import cupy
            try:
                cupy.cuda.Device().synchronize()
                cupy.get_default_memory_pool().free_all_blocks()
                cupy.get_default_pinned_memory_pool().free_all_blocks()
                import gc; gc.collect()
            except:
                pass
        # Process in small batches to avoid GPU OOM after training
        eval_batch = 8 if HAS_GPU else n_windows
        all_logits = []
        for start in range(0, n_windows, eval_batch):
            end = min(start + eval_batch, n_windows)
            batch_ctx = contexts[start:end]
            if HAS_GPU:
                import cupy
                batch_logits = expert_obj.forward(cupy.asarray(batch_ctx))
                batch_logits = to_cpu(batch_logits)
            else:
                batch_logits = to_cpu(expert_obj.forward(batch_ctx))
            all_logits.append(_np.array(batch_logits, dtype=_np.float32))
            # Free saved context after each mini-batch to keep VRAM low
            if hasattr(expert_obj, 'saved_ctx'):
                expert_obj.saved_ctx = None
        logits = _np.concatenate(all_logits, axis=0)

        max_l = logits.max(axis=-1, keepdims=True)
        probs = _np.exp(logits - max_l)
        probs /= probs.sum(axis=-1, keepdims=True)
        target_probs = _np.maximum(probs[_np.arange(n_windows), targets], 1e-10)
        avg_bpc = float((-_np.log2(target_probs)).mean())
        accuracy = float((_np.argmax(logits, axis=-1) == targets).mean())
        return avg_bpc, accuracy, n_windows
    except Exception as e:
        print(f"[SelfTest] Error: {e}")
        return 8.0, 0.004, 0


# =============================================================================
# Weight Sync (WebSocket gossip)
# =============================================================================

class WeightSync:
    """Async weight synchronization via WebSocket gossip protocol."""

    def __init__(self, config: ClientConfig, expert: ExpertWorker, worker_id: int = 0):
        self.config = config
        self.expert = expert
        self.worker_id = worker_id
        self.running = False
        self.ws = None
        self.reference_weights = None
        self.pending_deltas = asyncio.Queue()
        self.sync_count = 0
        self.connected = False

    async def start(self):
        """Start the sync loop."""
        self.running = True
        self.reference_weights = self.expert.get_weights()

        if not HAS_WEBSOCKETS:
            print("[WeightSync] WebSocket not available - offline mode")
            return

        if not self.config.server_url:
            print("[WeightSync] No server URL - offline mode")
            while self.running:
                await asyncio.sleep(1)
            return

        while self.running:
            try:
                await self._connect_and_sync()
            except Exception as e:
                print(f"[WeightSync] Connection error: {e}")
                self.connected = False
                # Don't retry - fresh-flush at task end handles delta delivery
                print("[WeightSync] Will use fresh-flush at task end instead")
                while self.running:
                    await asyncio.sleep(60)

    async def _connect_and_sync(self):
        """Connect to server and run sync loop."""
        print(f"[WeightSync] Connecting to {self.config.server_url}...")

        async with websockets.connect(
            self.config.server_url,
            ping_interval=None,  # Disable client-side pings (server handles it)
            ping_timeout=None,
            open_timeout=10,     # Quick timeout - fresh-flush fallback if this fails
            close_timeout=10
        ) as ws:
            self.ws = ws
            self.connected = True
            print("[WeightSync] Connected!")

            # Register our expert with authenticator for credit tracking
            await ws.send(json.dumps({
                "type": "register",
                "expert_idx": self.config.expert_idx,
                "param_count": self.expert.get_param_count(),
                "authenticator": self.config.authenticator
            }))

            # Run send/receive loops concurrently
            await asyncio.gather(
                self._send_loop(),
                self._receive_loop()
            )

    async def _send_loop(self):
        """Send weight deltas to server."""
        while self.running and self.connected:
            try:
                # Check for pending deltas (non-blocking)
                delta_item = await asyncio.wait_for(
                    self.pending_deltas.get(),
                    timeout=1.0
                )

                # Unpack delta and real sample count from queue
                delta, real_samples = delta_item
                # Compress and send with actual samples for credit tracking
                compressed = self._compress_delta(delta)
                await self.ws.send(json.dumps({
                    "type": "weight_delta",
                    "expert_idx": self.config.expert_idx,
                    "delta": compressed,
                    "samples": real_samples
                }))
                self.sync_count += 1
                print(f"[Sync] Worker-{self.worker_id} Expert-{self.config.expert_idx}: Sync #{self.sync_count} complete")

            except asyncio.TimeoutError:
                pass  # No delta to send
            except Exception as e:
                print(f"[WeightSync] Send error: {e}")
                break

    async def _receive_loop(self):
        """Receive weight deltas from peers."""
        # Create gradients folder for bootstrap data
        gradients_dir = self.config.contribute_path / ".gradients"
        gradients_dir.mkdir(parents=True, exist_ok=True)

        while self.running and self.connected:
            try:
                msg = await asyncio.wait_for(
                    self.ws.recv(),
                    timeout=0.1
                )

                data = json.loads(msg)
                if data.get("type") == "peer_delta":
                    peer_expert = data.get("expert_idx", -1)

                    # Save gradient to .gradients folder (one file per sender per expert)
                    try:
                        import base64
                        sender_id = data.get("sender_id", "unknown")
                        gradient_file = gradients_dir / f"peer_{sender_id}_expert_{peer_expert}.bin"
                        raw_delta = base64.b64decode(data["delta"])
                        with open(gradient_file, 'wb') as f:
                            f.write(raw_delta)

                        # Time-based decay: delete files older than 10 minutes
                        now = time.time()
                        for old_file in gradients_dir.glob("peer_*.bin"):
                            try:
                                if now - old_file.stat().st_mtime > 600:
                                    old_file.unlink()
                            except:
                                pass
                    except Exception as e:
                        print(f"[Sync] Failed to save gradient: {e}")

                    # Apply peer's weight delta if same expert
                    if peer_expert == self.config.expert_idx or peer_expert == -1:
                        delta = self._decompress_delta(data["delta"])
                        self.expert.apply_weight_delta(delta, alpha=self.config.gossip_alpha)
                        print(f"[Sync] Worker-{self.worker_id}: Received peer delta (15% merge)")
                    else:
                        print(f"[Sync] Worker-{self.worker_id}: Saved gradient from expert {peer_expert}")

            except asyncio.TimeoutError:
                pass  # No message
            except Exception as e:
                if self.running:
                    print(f"[WeightSync] Receive error: {e}")
                break

    def queue_delta(self, delta, samples=0):
        """Queue a weight delta for sending (non-blocking)."""
        try:
            self.pending_deltas.put_nowait((delta, samples))
        except asyncio.QueueFull:
            pass  # Drop if queue full

    def _compress_delta(self, delta) -> str:
        """Compress weight delta using 4-bit quantization (90% cosine sim preserved).
        Format: b'QNT4' + uint32(vec_len) + float32(scale) + packed nibbles (2 per byte)
        Size: ~20MB raw, ~15MB gzipped"""
        if HAS_NUMPY:
            arr = to_cpu(np.asarray(delta, dtype=np.float32))  # Ensure CPU for serialization

            # Find scale (max absolute value)
            scale = float(_real_numpy.max(_real_numpy.abs(arr)))
            if scale < 1e-30:
                # All zeros - send minimal sparse delta
                buff = struct.pack('<IfI', len(arr), 0.0, 0)
                return base64.b64encode(buff).decode()

            # Quantize to 4-bit signed [-7, +7]
            normalized = arr / scale  # [-1, 1]
            quantized = _real_numpy.clip(_real_numpy.round(normalized * 7), -7, 7).astype(_real_numpy.int8)

            # Pack pairs of 4-bit values into bytes
            # Each byte: high nibble = even index, low nibble = odd index
            # Values are in [-7, +7], offset to [0, 14] for packing
            offset_q = (quantized + 7).astype(_real_numpy.uint8)  # [0, 14]
            n = len(offset_q)
            if n % 2 != 0:
                offset_q = _real_numpy.append(offset_q, _real_numpy.uint8(7))  # pad with 0 (7-7=0)
            packed = (offset_q[0::2] << 4) | offset_q[1::2]  # high nibble + low nibble

            # Pack: QNT4 header + packed bytes
            buff = b'QNT4' + struct.pack('<If', n, scale) + packed.tobytes()

            # Gzip compress
            compressed = gzip.compress(buff, compresslevel=6)
            return base64.b64encode(compressed).decode()
        else:
            # Fallback: just JSON encode top values
            indexed = sorted(enumerate(delta), key=lambda x: abs(x[1]), reverse=True)[:100]
            return json.dumps(indexed)

    def _decompress_delta(self, compressed: str):
        """Decompress weight delta. Supports SGN1 (SignSGD) and SPARSE4 formats."""
        if compressed.startswith('['):
            # JSON format
            indexed = json.loads(compressed)
            if HAS_NUMPY:
                delta = np.zeros(max(idx for idx, _ in indexed) + 1, dtype=np.float32)
            else:
                delta = [0.0] * (max(idx for idx, _ in indexed) + 1)
            for idx, val in indexed:
                delta[idx] = val
            return delta
        else:
            buff = base64.b64decode(compressed)
            # Try gzip decompress
            try:
                buff = gzip.decompress(buff)
            except Exception:
                pass

            if buff[:4] == b'SGN1':
                # SignSGD format
                vec_len, avg_mag = struct.unpack('<If', buff[4:12])
                if HAS_NUMPY:
                    sign_bits = _real_numpy.unpackbits(_real_numpy.frombuffer(buff[12:], dtype=_real_numpy.uint8))[:vec_len]
                    delta = _real_numpy.where(sign_bits, avg_mag, -avg_mag).astype(_real_numpy.float32)
                else:
                    delta = [avg_mag if b else -avg_mag for b in buff[12:]]
                return delta
            else:
                # SPARSE4 binary format
                vec_len, scale, count = struct.unpack('<IfI', buff[:12])
                if HAS_NUMPY:
                    delta = np.zeros(vec_len, dtype=np.float32)
                else:
                    delta = [0.0] * vec_len
                offset = 12
                for _ in range(count):
                    idx = struct.unpack('<I', buff[offset:offset+4])[0]
                    q = buff[offset + 4]
                    if q > 127:
                        q -= 256
                    delta[idx] = q / 127.0 * scale
                    offset += 5
                return delta

    def get_and_reset_delta(self):
        """Get current weight delta and reset reference."""
        delta = self.expert.get_weight_delta(self.reference_weights)
        self.reference_weights = self.expert.get_weights()
        return delta

    def stop(self):
        """Stop sync loop."""
        self.running = False
        self.connected = False


# =============================================================================
# Training Loop (Hebbian)
# =============================================================================

class EndocrineSystem:
    """
    Hormone-like GC modulation system with real system metrics.

    Uses ACTUAL system state (like a real endocrine system):
    - Total CPU usage (all cores averaged) → cortisol/adrenaline
    - System memory PERCENTAGE → triggers GC at 95% system memory
    - No arbitrary thresholds - adapts to any system

    Target: CPU at 95%+, memory under 95% of system RAM.
    """

    def __init__(self, target_cpu: float = 95.0, memory_threshold_pct: float = 85.0):
        """
        Memory threshold is PERCENTAGE of total system RAM.
        GC triggers when system memory usage crosses threshold.
        """
        self.cortisol = 0.0         # Stress level (0-1) - high CPU = defer GC
        self.adrenaline = 0.0       # Boost signal (0-1) - low CPU = work harder
        self.last_gc_time = time.time()
        self.gc_count = 0

        # System metrics
        self.cpu_usage = 0.0        # Total CPU % (all cores averaged)
        self.memory_pct = 0.0       # System memory usage %
        self.memory_gb = 0.0        # For display
        self.total_ram_gb = 0.0     # Total system RAM
        self.target_cpu = target_cpu
        self.memory_threshold_pct = memory_threshold_pct

    def update_metrics(self):
        """Sample current system metrics."""
        try:
            # Total CPU across ALL cores
            self.cpu_usage = psutil.cpu_percent(interval=None)

            # System-wide memory percentage
            mem = psutil.virtual_memory()
            self.memory_pct = mem.percent
            self.memory_gb = mem.used / (1024 ** 3)
            self.total_ram_gb = mem.total / (1024 ** 3)
        except:
            self.cpu_usage = 50.0
            self.memory_pct = 50.0
            self.memory_gb = 4.0
            self.total_ram_gb = 16.0

        # Cortisol: HIGH when total CPU is high (system stressed, defer GC!)
        if self.cpu_usage > 90:
            self.cortisol = min(1.0, self.cortisol + 0.15)
        elif self.cpu_usage > 80:
            self.cortisol = min(1.0, self.cortisol + 0.05)
        elif self.cpu_usage < 60:
            self.cortisol = max(0.0, self.cortisol - 0.1)
        else:
            self.cortisol = max(0.0, self.cortisol - 0.02)

        # Adrenaline: HIGH when CPU is LOW (spare capacity, push harder!)
        if self.cpu_usage < 50:
            self.adrenaline = min(1.0, self.adrenaline + 0.2)
        elif self.cpu_usage < 70:
            self.adrenaline = min(1.0, self.adrenaline + 0.1)
        elif self.cpu_usage > 90:
            self.adrenaline = max(0.0, self.adrenaline - 0.02)
        else:
            self.adrenaline = max(0.0, self.adrenaline - 0.05)

    def should_gc(self) -> str:
        """Check if GC needed based on SYSTEM MEMORY %. Returns: 'none', 'light', 'major', 'sleep'"""

        # Percentage thresholds
        light_thresh = self.memory_threshold_pct * 0.7   # e.g., 35% if threshold is 50%
        major_thresh = self.memory_threshold_pct * 0.85  # e.g., 42.5% if threshold is 50%

        # Critical - full flush when at or above threshold (always trigger)
        if self.memory_pct >= self.memory_threshold_pct:
            return 'sleep'

        # High cortisol (busy CPU) suppresses non-critical GC
        if self.cortisol > 0.7 and self.memory_pct < major_thresh:
            return 'none'

        # Major GC when memory getting high
        if self.memory_pct >= major_thresh:
            return 'major'

        # Light GC at lower threshold
        if self.memory_pct >= light_thresh:
            return 'light'

        # Time-based fallback - major GC every 60 seconds minimum
        if time.time() - self.last_gc_time > 60:
            return 'major'

        return 'none'

    def do_gc(self, level: str):
        """Perform garbage collection at specified level."""
        if level == 'none':
            return

        if level == 'light':
            gc.collect(0)  # Generation 0 only
        elif level == 'major':
            gc.collect(1)  # Gen 0 + 1
        elif level == 'sleep':
            # Full "glymphatic flush"
            gc.collect(2)  # All generations
            gc.collect()   # And again

        self.last_gc_time = time.time()
        self.gc_count += 1

    def status(self) -> str:
        return f"cpu={self.cpu_usage:.0f}% mem={self.memory_pct:.0f}%({self.memory_gb:.1f}GB) cort={self.cortisol:.1f} adren={self.adrenaline:.1f} gc={self.gc_count}"



def _compress_delta_qnt4(delta) -> str:
    """Standalone QNT4 compression for sector deltas (no WeightSync instance needed).
    Format: b'QNT4' + uint32(vec_len) + float32(scale) + packed nibbles, gzip compressed."""
    if not HAS_NUMPY:
        return json.dumps([])
    arr = to_cpu(_real_numpy.asarray(delta, dtype=_real_numpy.float32))
    scale = float(_real_numpy.max(_real_numpy.abs(arr)))
    if scale < 1e-30:
        buff = struct.pack('<IfI', len(arr), 0.0, 0)
        return base64.b64encode(buff).decode()
    normalized = arr / scale
    quantized = _real_numpy.clip(_real_numpy.round(normalized * 7), -7, 7).astype(_real_numpy.int8)
    offset_q = (quantized + 7).astype(_real_numpy.uint8)
    n = len(offset_q)
    if n % 2 != 0:
        offset_q = _real_numpy.append(offset_q, _real_numpy.uint8(7))
    packed = (offset_q[0::2] << 4) | offset_q[1::2]
    buff = b'QNT4' + struct.pack('<If', n, scale) + packed.tobytes()
    compressed = gzip.compress(buff, compresslevel=6)
    return base64.b64encode(compressed).decode()


def _compress_scaffold_gradient(delta, pool_size, orig_count):
    """Compress gradient by sum-pooling groups of pool_size values.
    Format: b'SCGR' + uint32(orig_count) + uint32(pool_size) + uint32(n_compressed) + float32[n_compressed]
    Returns base64-encoded string."""
    arr = to_cpu(_real_numpy.asarray(delta, dtype=_real_numpy.float32))
    n_compressed = (orig_count + pool_size - 1) // pool_size
    padded_len = n_compressed * pool_size
    padded = _real_numpy.zeros(padded_len, dtype=_real_numpy.float32)
    padded[:len(arr)] = arr
    comp_grad = padded.reshape(-1, pool_size).sum(axis=1)
    header = struct.pack('<4sIII', b'SCGR', orig_count, pool_size, n_compressed)
    return base64.b64encode(header + comp_grad.astype(_real_numpy.float32).tobytes()).decode()


async def _flush_delta_fresh(server_url, authenticator, expert_idx, compressed_delta, samples, bpc=None, sector_id=None, sector_start=0, sector_size=0):
    """Deliver delta via HTTP POST (fast, no handshake). Falls back to websocket."""
    if not server_url:
        print("[BOINC] No server URL for flush")
        return False

    # Try HTTP POST first (no handshake overhead)
    import urllib.request
    import urllib.error

    # Derive HTTP URL from websocket URL (ws://host:8765 -> http://host:8766)
    http_url = server_url.replace('ws://', 'http://').replace('wss://', 'https://')
    # Replace port 8765 with 8766
    if ':8765' in http_url:
        http_url = http_url.replace(':8765', ':8766')
    else:
        # Generic: replace last port
        parts = http_url.rsplit(':', 1)
        if len(parts) == 2:
            http_url = parts[0] + ':8766'
        else:
            http_url = http_url + ':8766'
    # Strip any trailing path
    if http_url.endswith('/'):
        http_url = http_url[:-1]
    http_url += '/submit_delta'

    payload_dict = {
        "expert_idx": expert_idx,
        "delta": compressed_delta,
        "samples": samples,
        "authenticator": authenticator,
        "bpc": bpc
    }
    if sector_id is not None:
        payload_dict["sector_id"] = sector_id
        payload_dict["sector_start"] = sector_start
        payload_dict["sector_size"] = sector_size
    payload = json.dumps(payload_dict).encode('utf-8')

    for attempt in range(3):
        try:
            print(f"[BOINC] HTTP flush attempt {attempt+1}/3 to {http_url}...")
            req = urllib.request.Request(
                http_url,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=15)
            )
            result = json.loads(resp.read())
            print(f"[BOINC] HTTP flush SUCCESS: expert {expert_idx}, {samples} samples, applied={result.get('applied')}")
            return True
        except Exception as e:
            print(f"[BOINC] HTTP flush attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                await asyncio.sleep(2)

    # Fall back to websocket as last resort
    if not HAS_WEBSOCKETS:
        print("[BOINC] HTTP failed, no websockets available - delta lost")
        return False
    print("[BOINC] HTTP failed, trying websocket fallback...")
    try:
        async with websockets.connect(server_url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({
                "type": "register",
                "expert_idx": expert_idx,
                "param_count": 42971392,
                "authenticator": authenticator
            }))
            await asyncio.wait_for(ws.send(json.dumps({
                "type": "weight_delta",
                "expert_idx": expert_idx,
                "delta": compressed_delta,
                "samples": samples,
                "bpc": bpc
            })), timeout=10)
            await asyncio.sleep(1)
            print(f"[BOINC] Websocket fallback SUCCESS: expert {expert_idx}, {samples} samples")
            return True
    except Exception as e:
        print(f"[BOINC] Websocket fallback also failed: {e} - delta lost")
        return False

class HebbianTrainer:
    """Main training loop using Hebbian learning."""

    def __init__(self, config: ClientConfig, worker_id: int = 0):
        self.config = config
        self.worker_id = worker_id
        self.expert = None
        self.data_producer = None
        self.weight_sync = None
        self.running = False

        # Endocrine system for GC modulation
        self.endocrine = EndocrineSystem()
        self.last_gc_check = time.time()

        # Stats
        self.samples_trained = 0
        self.start_time = None
        self.last_report_time = None
        self.last_report_samples = 0
        self.samples_since_sync = 0  # Track progress toward next sync

        # Shared counters for multiprocessing (set by run_single_worker)
        self.shared_samples = None
        self.shared_syncs = None
        self.shared_progress = None  # Per-worker progress array
        self.shared_experts = None   # Per-worker expert assignment array

        # Multi-expert GPU training
        self.gpu_experts = []  # [(expert_idx, ExpertWorker)]
        self.gpu_ref_weights = {}  # expert_idx -> reference weights

        # Sector system
        self.sector_id = None
        self.sector_start = 0
        self.sector_size = 0
        self.sector_weights = None       # Current sector weights (float32)
        self.sector_weights_orig = None  # Original for delta computation
        self.sector_mode = False         # True = 1-layer probe, False = full SGD

        # 1-layer linear probe params (initialized in _init_probe)
        self.probe_W = None   # (256, 64) weight matrix
        self.probe_b = None   # (256,) bias
        self.probe_W_init = None
        self.probe_b_init = None


    def _decode_qnt4_gzip(self, data: bytes):
        """Decode QNT4+gzip data to float32 numpy array."""
        # Try gzip decompress
        try:
            data = gzip.decompress(data)
        except Exception:
            pass  # Not gzipped
        if len(data) < 12 or data[:4] != b'QNT4':
            return None
        vec_len, scale = struct.unpack('<If', data[4:12])
        packed = _real_numpy.frombuffer(data[12:], dtype=_real_numpy.uint8)
        high = (packed >> 4).astype(_real_numpy.float32) - 7.0
        low = (packed & 0x0F).astype(_real_numpy.float32) - 7.0
        quantized = _real_numpy.empty(len(packed) * 2, dtype=_real_numpy.float32)
        quantized[0::2] = high
        quantized[1::2] = low
        return (quantized[:vec_len] / 7.0) * scale

    async def _download_sector_weights(self):
        """Download sector weights from coordinator via HTTP.
        Uses QNT4+gzip for efficient transfer (~400KB for 1.3M params)."""
        if self.sector_id is None or self.sector_size == 0:
            print("[Trainer] No sector assigned, skipping weight download")
            return False

        import urllib.request
        # Derive HTTP URL from websocket URL
        http_url = self.config.server_url.replace('ws://', 'http://').replace('wss://', 'https://')
        if ':8765' in http_url:
            http_url = http_url.replace(':8765', ':8766')
        url = f"{http_url}/sector_weights?expert=0&start={self.sector_start}&size={self.sector_size}"
        print(f"[Trainer] Downloading sector weights: start={self.sector_start}, size={self.sector_size:,}...")
        try:
            req = urllib.request.Request(url)
            resp = await asyncio.get_event_loop().run_in_executor(
                None, lambda: urllib.request.urlopen(req, timeout=30))
            data = resp.read()
            print(f"[Trainer] Downloaded {len(data)//1024}KB QNT4+gzip sector data")
            weights = self._decode_qnt4_gzip(data)
            if weights is None:
                print("[Trainer] Failed to decode QNT4 sector data")
                return False
            if len(weights) != self.sector_size:
                print(f"[Trainer] Sector size mismatch: got {len(weights)}, expected {self.sector_size}")
                return False
            self.sector_weights = weights.copy()
            self.sector_weights_orig = weights.copy()  # Keep original for delta computation
            print(f"[Trainer] Sector weights loaded: {len(weights):,} params, "
                  f"norm={float(_real_numpy.linalg.norm(weights)):.4f}")
            return True
        except Exception as e:
            print(f"[Trainer] Sector weight download failed: {e}")
            return False

    async def _download_full_weights(self):
        """Download full accumulated weights as FP32 gzipped (~152MB).
        Falls back to QNT4 if FP32 not available.
        """
        import urllib.request, ssl, struct, gzip as _gzip
        # Try FP32 first (lossless), fall back to QNT4
        fp32_url = self.config.model_server_url + "/expert_0_weights.fp32.gz"
        qnt4_url = self.config.model_server_url + "/expert_0_weights.qnt4"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handler = urllib.request.HTTPSHandler(context=ctx)
        opener = urllib.request.build_opener(handler)

        # Try FP32
        try:
            print("[Trainer] Downloading FP32 weights...")
            with opener.open(fp32_url, timeout=300) as resp:
                data = resp.read()
            print("[Trainer] Downloaded {:.1f}MB FP32 data".format(len(data) / 1024 / 1024))
            raw = _gzip.decompress(data)
            weights = _real_numpy.frombuffer(raw, dtype=_real_numpy.float32).copy()
            if len(weights) != self.expert.get_param_count():
                print("[Trainer] FP32 weight size mismatch: got {}, expected {}".format(len(weights), self.expert.get_param_count()))
                return False
            self.expert.set_weights_flat(weights)
            print("[Trainer] Set FP32 weights: {:,} params, norm={:.4f}, absmax={:.4f}".format(
                len(weights), float(_real_numpy.linalg.norm(weights)), float(_real_numpy.max(_real_numpy.abs(weights)))))
            return True
        except Exception as e:
            print("[Trainer] FP32 download failed: {}, trying QNT4...".format(e))

        # Fallback: QNT4
        try:
            print("[Trainer] Downloading QNT4 weights (fallback)...")
            with opener.open(qnt4_url, timeout=120) as resp:
                data = resp.read()
            print("[Trainer] Downloaded {:.1f}MB QNT4 data".format(len(data) / 1024 / 1024))
            if len(data) < 12 or data[:4] != b"QNT4":
                print("[Trainer] Invalid QNT4 weight format")
                return False
            vec_len, scale = struct.unpack("<If", data[4:12])
            packed = _real_numpy.frombuffer(data[12:], dtype=_real_numpy.uint8)
            high = (packed >> 4).astype(_real_numpy.float32) - 7.0
            low = (packed & 0x0F).astype(_real_numpy.float32) - 7.0
            quantized = _real_numpy.empty(len(packed) * 2, dtype=_real_numpy.float32)
            quantized[0::2] = high
            quantized[1::2] = low
            weights = (quantized[:vec_len] / 7.0) * scale
            if len(weights) != self.expert.get_param_count():
                print("[Trainer] Weight size mismatch: got {}, expected {}".format(len(weights), self.expert.get_param_count()))
                return False
            self.expert.set_weights_flat(weights)
            print("[Trainer] Set QNT4 weights (fallback): {:,} params, norm={:.4f}".format(len(weights), float(_real_numpy.linalg.norm(weights))))
            return True
        except Exception as e2:
            print("[Trainer] QNT4 download also failed: {}, using Xavier init".format(e2))
            return False

    async def _download_scaffold_weights(self):
        """Download max-abs compressed scaffold weights (~168KB).
        Expand to full model by repeating each value pool_size times."""
        import urllib.request, ssl
        url = self.config.model_server_url + "/expert_0_scaffold.bin"
        print("[Trainer] Downloading scaffold weights (~168KB)...")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handler = urllib.request.HTTPSHandler(context=ctx)
            opener = urllib.request.build_opener(handler)
            with opener.open(url, timeout=30) as resp:
                data = resp.read()
            if len(data) < 16 or data[:4] != b'SCAF':
                print("[Trainer] Invalid scaffold format, falling back to full QNT4")
                return False
            orig_count, pool_size, n_compressed = struct.unpack('<III', data[4:16])
            scaffold = _real_numpy.frombuffer(data[16:], dtype=_real_numpy.float32)[:n_compressed]
            # Expand: repeat each value pool_size times
            expanded = _real_numpy.repeat(scaffold, pool_size)[:orig_count]
            self._scaffold_pool_size = pool_size
            self._scaffold_orig_count = orig_count
            if len(expanded) != self.expert.get_param_count():
                print("[Trainer] Scaffold size mismatch: got {}, expected {}".format(
                    len(expanded), self.expert.get_param_count()))
                return False
            self.expert.set_weights_flat(expanded)
            print("[Trainer] Scaffold loaded: {:,} compressed -> {:,} expanded, "
                  "pool={}, {:.0f}KB download".format(
                      n_compressed, orig_count, pool_size, len(data) / 1024))
            return True
        except Exception as e:
            print("[Trainer] Scaffold download failed: {}, trying full QNT4".format(e))
            return False

    async def _request_expert_assignment(self) -> int:
        """Request expert + sector assignment from coordinator.
        Returns expert_idx. Stores sector info in self.sector_id/start/size.
        Also patches Xavier init with sector weights from coordinator.
        """
        import random
        if not HAS_WEBSOCKETS or not self.config.server_url:
            return random.randint(0, self.config.num_experts - 1)

        try:
            async with websockets.connect(
                self.config.server_url,
                open_timeout=10,
                close_timeout=5,
                max_size=50 * 1024 * 1024  # Sector weights can be ~2MB
            ) as ws:
                await ws.send(json.dumps({
                    "type": "request_assignment",
                    "authenticator": self.config.authenticator
                }))

                response = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(response)

                if data.get("type") == "assignment":
                    expert_idx = data.get("expert_idx", 0)
                    self.sector_id = data.get("sector_id")
                    self.sector_start = data.get("sector_start", 0)
                    self.sector_size = data.get("sector_size", 0)
                    weights_format = data.get("weights_format", "raw")

                    # Store sector weights (may be QNT4+gzip or raw base64)
                    raw_b64 = data.get("sector_weights", "")
                    if raw_b64 and weights_format == "qnt4_gzip":
                        try:
                            raw_bytes = base64.b64decode(raw_b64)
                            decoded = self._decode_qnt4_gzip(raw_bytes)
                            if decoded is not None and len(decoded) == self.sector_size:
                                self.sector_weights = decoded.copy()
                                self.sector_weights_orig = decoded.copy()
                                print(f"[Worker-{self.worker_id}] Sector weights inline QNT4+gzip: "
                                      f"{len(raw_bytes)//1024}KB -> {self.sector_size:,} params")
                            else:
                                print(f"[Worker-{self.worker_id}] Inline QNT4 decode size mismatch, will download via HTTP")
                                self.sector_weights = None
                        except Exception as e:
                            print(f"[Worker-{self.worker_id}] Inline QNT4 decode error: {e}")
                            self.sector_weights = None
                    else:
                        self._pending_sector_weights = raw_b64
                        self.sector_weights = None

                    if self.sector_id is not None:
                        print(f"[Worker-{self.worker_id}] Assigned expert {expert_idx}, "
                              f"sector start={self.sector_start} size={self.sector_size:,} "
                              f"({self.sector_size*4//1024}KB)")
                    else:
                        print(f"[Worker-{self.worker_id}] Assigned expert {expert_idx} (no sector)")
                    return expert_idx

        except Exception as e:
            print(f"[Trainer] Failed to get assignment: {e}, using random")

        return random.randint(0, self.config.num_experts - 1)

    async def initialize(self):
        """Initialize expert and components."""

        # Request expert assignment from coordinator if auto_assign is enabled
        if self.config.auto_assign and self.config.expert_idx < 0:
            self.config.expert_idx = await self._request_expert_assignment()

        print(f"[Trainer] Initializing expert {self.config.expert_idx}...")

        # Determine training mode: sector probe vs full SGD
        if self.sector_id is not None and self.sector_size > 0:
            self.sector_mode = True
            print(f"[Trainer] SECTOR MODE: 1-layer probe on {self.sector_size:,} params "
                  f"({self.sector_size * 4 // 1024}KB)")
        else:
            self.sector_mode = False
            print(f"[Trainer] FULL MODEL MODE: backprop SGD on full transformer")

        if self.sector_mode:
            # ===== Sector mode: lightweight 1-layer probe =====
            # Download sector weights if not already received inline
            if self.sector_weights is None:
                await self._download_sector_weights()
            if self.sector_weights is None:
                print("[Trainer] No sector weights available, falling back to full model mode")
                self.sector_mode = False

        if self.sector_mode:
            # Initialize the 1-layer linear probe from sector weights
            self._init_probe()
            # CPU batch size for probe (very lightweight)
            self.config.mini_batch_size = 64
            print(f"[Trainer] Probe training: batch=64, lr={self.config.hebbian_lr}")
            print(f"[Trainer] Memory: ~{self.sector_size * 4 * 3 // 1024 // 1024 + 20}MB "
                  f"(sector={self.sector_size * 4 // 1024 // 1024}MB × 3 + overhead)")
            # No ExpertWorker needed - probe handles training
            self.expert = None
        else:
            # ===== Full model mode: standard backprop SGD =====
            # Create MoE config
            moe_config = MoEConfig(
                input_size=self.config.input_size,
                output_size=self.config.output_size,
                num_experts=self.config.num_experts,
                expert_hidden=self.config.expert_hidden,
                expert_layers=self.config.expert_layers,
                expert_type=self.config.expert_type,
                d_model=self.config.d_model,
                n_heads=self.config.n_heads,
            )

            # Clear old cache on startup (fresh weights each session)
            self._clear_cache()

            expert_weights = None

            # Create expert worker
            self.expert = ExpertWorker(
                config=moe_config,
                expert_idx=self.config.expert_idx,
                expert_weights=expert_weights or [],
                seed=0  # Fixed seed for sector consistency
            )

            param_count = self.expert.get_param_count()
            print(f"[Trainer] Expert {self.config.expert_idx} initialized: {param_count:,} parameters")
            print(f"[Trainer] Memory usage: ~{param_count * 4 / 1024 / 1024:.1f} MB")

            # Download FP32 weights (152MB) - lossless, best training accuracy
            # Scaffold approach abandoned: repeating values loses too much info
            await self._download_full_weights()

            # GPU: start with aggressive batch size
            if HAS_GPU:
                import cupy
                free_mem = cupy.cuda.Device(0).mem_info[0]
                free_mb = free_mem // 1024 // 1024
                est_per_sample_mb = 30
                overhead_mb = 800
                auto_batch = max(32, min(1024, (free_mb - overhead_mb) // est_per_sample_mb))
                auto_batch = 2 ** int(auto_batch).bit_length() >> 1 if auto_batch > 0 else 32
                auto_batch = max(32, min(auto_batch, 1024))
                self.config.mini_batch_size = auto_batch
                self.config.sync_interval = 999999999
                print(f"[GPU] Initial batch estimate: {auto_batch} ({free_mb}MB free VRAM)")
            else:
                self.config.mini_batch_size = 32
                print(f"[CPU] Batch size: 32")

            # GPU multi-expert setup
            if HAS_GPU:
                import cupy
                import random as _rng
                self.gpu_experts = [(self.config.expert_idx, self.expert)]
                self.gpu_ref_weights[self.config.expert_idx] = self.expert.get_weights()

        # Download seed training data if needed
        self._ensure_seed_data()

        self.holdout_bytes = None

        # Initialize data producer
        self.data_producer = DataProducer(self.config)

        # Weight sync only needed for full model mode
        if not self.sector_mode and self.expert is not None:
            self.weight_sync = WeightSync(self.config, self.expert, self.worker_id)
            self._apply_gossip_gradients()
        else:
            self.weight_sync = None

    def _apply_gossip_gradients(self):
        """Apply one random peer gradient per expert at task start."""
        import random as _grng
        gradients_dir = self.config.contribute_path / ".gradients"
        if not gradients_dir.exists():
            return

        now = time.time()
        by_expert = {}  # expert_idx -> [file_path, ...]
        for gfile in gradients_dir.glob("peer_*_expert_*.bin"):
            try:
                if now - gfile.stat().st_mtime > 600:  # 10 min decay
                    gfile.unlink()
                    continue
                parts = gfile.stem.split("_expert_")
                if len(parts) == 2:
                    gexpert = int(parts[1])
                    by_expert.setdefault(gexpert, []).append(gfile)
            except:
                pass

        # Determine active experts
        if HAS_GPU and hasattr(self, "gpu_experts") and self.gpu_experts:
            active_experts = self.gpu_experts
        else:
            active_experts = [(self.config.expert_idx, self.expert)]

        for eidx, eobj in active_experts:
            candidates = by_expert.get(eidx, [])
            if candidates:
                chosen = _grng.choice(candidates)
                try:
                    raw = chosen.read_bytes()
                    delta = self.weight_sync._decompress_delta(
                        base64.b64encode(raw).decode()
                    )
                    eobj.apply_weight_delta(delta, alpha=self.config.gossip_alpha)
                    print(f"[Gossip] Applied 1/{len(candidates)} peer gradient to expert {eidx}")
                except Exception as e:
                    print(f"[Gossip] Error applying {chosen.name}: {e}")


    def _select_holdout_bytes(self, holdout_size=32768):
        """Select random ~2KB chunk from contribute data for self-testing.
        Returns bytes or None. Chunk must have entropy < 7."""
        import random as _rng
        seed_file = self.config.contribute_path / "seed_data.bin"
        if not seed_file.exists():
            return None
        try:
            with open(seed_file, 'rb') as f:
                all_data = f.read(10 * 1024 * 1024)
        except:
            return None
        if len(all_data) < holdout_size + 1024:
            return None
        for _ in range(5):
            start = _rng.randint(0, len(all_data) - holdout_size)
            chunk = all_data[start:start + holdout_size]
            # Shannon entropy in bits/byte
            counts = [0] * 256
            for b in chunk:
                counts[b] += 1
            ent = 0.0
            for c in counts:
                if c > 0:
                    p = c / len(chunk)
                    ent -= p * math.log2(p)
            if ent < 7.0:
                return chunk
        return None

    def _ensure_seed_data(self):
        """Download seed training data if missing or older than 24 hours."""
        seed_file = self.config.contribute_path / "seed_data.bin"
        need_download = False

        if not seed_file.exists():
            need_download = True
            print("[Seed] No seed data found, downloading...")
        else:
            age_hours = (time.time() - seed_file.stat().st_mtime) / 3600
            if age_hours > 24:
                need_download = True
                print(f"[Seed] Seed data is {age_hours:.1f}h old, refreshing...")

        if need_download:
            try:
                import urllib.request
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                url = self.config.model_server_url + "/seed_data.php"
                self.config.contribute_path.mkdir(parents=True, exist_ok=True)
                tmp_file = seed_file.with_suffix(".tmp")
                handler = urllib.request.HTTPSHandler(context=ctx)
                opener = urllib.request.build_opener(handler)
                with opener.open(url, timeout=60) as resp:
                    with open(str(tmp_file), 'wb') as f:
                        f.write(resp.read())
                size = tmp_file.stat().st_size
                if size > 1000:  # Sanity check
                    if seed_file.exists():
                        seed_file.unlink()
                    tmp_file.rename(seed_file)
                    print(f"[Seed] Downloaded {size:,} bytes of seed data")
                else:
                    tmp_file.unlink()
                    print(f"[Seed] Download too small ({size} bytes), skipped")
            except Exception as e:
                print(f"[Seed] Download failed: {e}")
                # Clean up partial download
                tmp = seed_file.with_suffix(".tmp")
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except:
                        pass

    def _clear_cache(self):
        """Clear all cached weight files on startup."""
        cache_dir = self.config.cache_path / "models"
        if cache_dir.exists():
            try:
                import shutil
                shutil.rmtree(cache_dir)
                print(f"[Trainer] Cleared weight cache")
            except Exception as e:
                print(f"[Trainer] Failed to clear cache: {e}")

    def _load_cached_weights(self) -> Optional[list]:
        """Load cached expert weights if available."""
        cache_file = self.config.cache_path / "models" / f"expert_{self.config.expert_idx}.bin"
        if cache_file.exists():
            try:
                if HAS_NUMPY:
                    weights = np.fromfile(str(cache_file), dtype=np.float32).tolist()
                    print(f"[Trainer] Loaded cached weights: {len(weights):,} params")
                    return weights
            except Exception as e:
                print(f"[Trainer] Failed to load cache: {e}")
        return None

    def _save_cached_weights(self):
        """Save current weights to cache."""
        cache_dir = self.config.cache_path / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"expert_{self.config.expert_idx}.bin"

        try:
            weights = self.expert.get_weights()
            if HAS_NUMPY:
                np.asarray(weights, dtype=np.float32).tofile(str(cache_file))
        except Exception as e:
            print(f"[Trainer] Failed to save cache: {e}")

    def _init_probe(self):
        """Initialize 1-layer linear probe from sector weights.
        W(256,64) + b(256) = 16,640 active params mapped to first 16,640 of sector."""
        W_SIZE = 256 * 64  # 16384
        B_SIZE = 256
        ACTIVE = W_SIZE + B_SIZE  # 16640

        if self.sector_weights is None or len(self.sector_weights) < ACTIVE:
            # Xavier init if sector too small or missing
            scale = _real_numpy.sqrt(2.0 / (64 + 256))
            self.probe_W = _real_numpy.random.randn(256, 64).astype(_real_numpy.float32) * scale
            self.probe_b = _real_numpy.zeros(256, dtype=_real_numpy.float32)
        else:
            # Map first 16,640 sector params to probe
            self.probe_W = self.sector_weights[:W_SIZE].reshape(256, 64).copy()
            self.probe_b = self.sector_weights[W_SIZE:ACTIVE].copy()

        self.probe_W_init = self.probe_W.copy()
        self.probe_b_init = self.probe_b.copy()
        print(f"[Probe] Initialized: W(256,64)={W_SIZE} + b(256)={B_SIZE} = {ACTIVE} active params")

    def _probe_train_step(self, x_batch, target_batch, lr=0.005):
        """One SGD step on the linear probe.
        x_batch: (batch, 64) float32 byte values
        target_batch: (batch,) int32 target byte (0-255)
        Returns loss (float)."""
        np = _real_numpy
        batch_size = len(x_batch)

        # Forward: logits = x @ W.T + b
        logits = x_batch @ self.probe_W.T + self.probe_b  # (batch, 256)

        # Softmax
        max_l = logits.max(axis=1, keepdims=True)
        exp_l = np.exp(logits - max_l)
        probs = exp_l / exp_l.sum(axis=1, keepdims=True)

        # Cross-entropy loss
        target_probs = probs[np.arange(batch_size), target_batch]
        loss = float(-np.log(np.maximum(target_probs, 1e-10)).mean())

        # Backward: d_logits = probs - one_hot(target)
        d_logits = probs.copy()
        d_logits[np.arange(batch_size), target_batch] -= 1.0
        d_logits /= batch_size

        # Gradients
        d_W = d_logits.T @ x_batch  # (256, 64)
        d_b = d_logits.sum(axis=0)  # (256,)

        # Gradient clipping
        grad_norm = float(np.sqrt(np.sum(d_W**2) + np.sum(d_b**2)))
        if grad_norm > 1.0:
            d_W *= 1.0 / grad_norm
            d_b *= 1.0 / grad_norm

        # SGD update
        self.probe_W -= lr * d_W
        self.probe_b -= lr * d_b

        return loss

    def _get_sector_delta(self):
        """Compute sector delta from probe training + regularization.
        Returns float32 array of sector_size."""
        np = _real_numpy
        W_SIZE = 256 * 64
        B_SIZE = 256
        ACTIVE = W_SIZE + B_SIZE

        delta = np.zeros(self.sector_size, dtype=np.float32)

        # Active region: probe weight changes
        dW = (self.probe_W - self.probe_W_init).flatten()
        db = self.probe_b - self.probe_b_init
        delta[:W_SIZE] = dW
        delta[W_SIZE:ACTIVE] = db

        # Passive region: mild regularization toward zero
        if self.sector_size > ACTIVE and self.sector_weights is not None:
            delta[ACTIVE:] = -0.001 * self.sector_weights[ACTIVE:]

        return delta

    async def train_loop(self):
        """Main training loop with mini-batching for efficiency."""
        self.running = True
        self.start_time = time.time()
        self.last_report_time = self.start_time

        batch_size = self.config.mini_batch_size

        # ===== Sector mode: lightweight 1-layer probe training =====
        if self.sector_mode:
            print(f"[Probe] Starting sector probe training loop")
            print(f"[Probe] lr={self.config.hebbian_lr}, batch=64, sector_size={self.sector_size:,}")
            step = 0
            while self.running:
                try:
                    batch = await asyncio.wait_for(
                        self.data_producer.get_sample(), timeout=5.0)

                    if HAS_NUMPY and isinstance(batch, _real_numpy.ndarray) and batch.ndim == 2:
                        # batch is (N, input_size+1), last column is target byte
                        x = batch[:, :-1].astype(_real_numpy.float32)
                        targets = batch[:, -1].astype(_real_numpy.int32)
                    else:
                        continue

                    actual_size = len(x)
                    if actual_size > 64:
                        x = x[:64]
                        targets = targets[:64]
                        actual_size = 64

                    loss = self._probe_train_step(x, targets, lr=self.config.hebbian_lr)

                    self.samples_trained += actual_size
                    if self.shared_samples is not None:
                        with self.shared_samples.get_lock():
                            self.shared_samples.value += actual_size
                    self.samples_since_sync += actual_size
                    step += 1

                    if step % 100 == 0:
                        elapsed = time.time() - self.start_time
                        rate = self.samples_trained / max(elapsed, 0.001)
                        print(f"[Probe] step={step}, samples={self.samples_trained:,}, "
                              f"loss={loss:.4f}, rate={rate:.0f}/s")

                    await asyncio.sleep(0)

                except asyncio.TimeoutError:
                    print("[Probe] Waiting for data...")
                except Exception as e:
                    print(f"[Probe] Error: {e}")
                    import traceback
                    traceback.print_exc()
                    await asyncio.sleep(0.1)
            return

        # ===== Full model mode: standard backprop SGD training =====
        print("[Trainer] Starting Hebbian training loop...")
        print(f"[Trainer] Learning rate: {self.config.hebbian_lr}")
        print(f"[Trainer] Weight decay: {self.config.hebbian_decay}")
        print(f"[Trainer] Mini-batch size: {batch_size}")
        print(f"[Trainer] Sync interval: {self.config.sync_interval} samples")

        # GPU: preload all data and crunch in tight loop (no async queue starvation)
        if HAS_GPU:
            print("[GPU] Pre-loading all data for crunch mode...")
            all_data = self.data_producer.preload_all()
            if len(all_data) > self.config.input_size:
                await self._gpu_crunch_loop(all_data)
                return
            else:
                print("[GPU] Not enough data, falling back to async queue")

        while self.running:
            try:
                # Get pre-batched numpy array from producer
                batch = await asyncio.wait_for(
                    self.data_producer.get_sample(),
                    timeout=5.0
                )

                # batch is a numpy float32 array of shape (N, input_size+1)
                # Last column is the target byte (0-255)
                if HAS_NUMPY and isinstance(batch, np.ndarray) and batch.ndim == 2:
                    target_bits = batch[:, -1].astype(np.int32)
                    x = batch[:, :-1]
                else:
                    # Legacy fallback for individual samples
                    target_bits = np.array([0], dtype=np.int32)
                    if isinstance(batch, tuple):
                        x = np.array([batch[0]], dtype=np.float32) if HAS_NUMPY else [batch[0]]
                    else:
                        x = np.array([batch], dtype=np.float32) if HAS_NUMPY else [batch]

                actual_size = len(x)

                # Truncate to current batch size (may have been reduced by OOM)
                if actual_size > self.config.mini_batch_size:
                    x = x[:self.config.mini_batch_size]
                    target_bits = target_bits[:self.config.mini_batch_size]
                    actual_size = self.config.mini_batch_size

                # Forward pass with OOM protection (GPU auto-halves batch on OOM)
                while True:
                    try:
                        output = self.expert.forward(x)
                        break
                    except Exception as oom_err:
                        if 'OutOfMemory' in type(oom_err).__name__ or 'out of memory' in str(oom_err).lower():
                            if len(x) <= 1:
                                raise
                            # Halve batch and retry
                            new_size = len(x) // 2
                            x = x[:new_size]
                            target_bits = target_bits[:new_size]
                            actual_size = new_size
                            self.config.mini_batch_size = new_size
                            self.expert.saved_ctx = None
                            try:
                                import cupy as _cp
                                import gc; gc.collect()
                                _cp.get_default_memory_pool().free_all_blocks()
                                _cp.get_default_pinned_memory_pool().free_all_blocks()
                            except Exception:
                                pass
                            print(f"[GPU] OOM! Batch halved to {new_size}")
                        else:
                            raise

                # Backprop SGD step
                target_int = target_bits
                if HAS_NUMPY:
                    target_int = to_cpu(target_bits).astype(int) if hasattr(target_bits, 'get') else target_bits.astype(int)
                self.expert.sgd_step(x, target_int, output, lr=self.config.hebbian_lr, max_grad_norm=1.0)

                del output, x, target_bits

                self.samples_trained += actual_size
                if self.shared_samples is not None:
                    with self.shared_samples.get_lock():
                        self.shared_samples.value += actual_size
                self.samples_since_sync += actual_size

                # Periodic sync
                if self.samples_since_sync >= self.config.sync_interval:
                    delta = self.weight_sync.get_and_reset_delta()
                    self.weight_sync.queue_delta(delta, self.samples_since_sync)
                    if self.shared_syncs is not None:
                        with self.shared_syncs.get_lock():
                            self.shared_syncs.value += 1
                    del delta
                    self.samples_since_sync = 0

                # Periodic reporting
                if self.samples_trained % self.config.batch_report_interval == 0:
                    self._report_progress()

                await asyncio.sleep(0)

            except asyncio.TimeoutError:
                print("[Trainer] Waiting for data...")
            except Exception as e:
                print(f"[Trainer] Error: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(0.1)

    async def _gpu_crunch_loop(self, bits):
        """Tight GPU training loop with multi-expert round-robin."""
        n_total = len(bits)
        window_size = self.config.input_size
        batch_size = self.config.mini_batch_size
        max_start = n_total - window_size

        if max_start <= 0:
            print("[GPU] Not enough data!")
            return

        import cupy
        gpu_bits = cupy.asarray(bits)
        offsets = cupy.arange(window_size + 1)  # +1 for target bit
        last_yield = time.time()

        # Multi-expert or single expert
        experts = self.gpu_experts if self.gpu_experts else [(self.config.expert_idx, self.expert)]
        n_experts = len(experts)
        cycle = 0
        per_expert_samples = {idx: 0 for idx, _ in experts}

        # --- Dynamic batch probe: find max batch that fits in VRAM ---
        probe_expert = experts[0][1]
        probe_batch = batch_size
        print(f"[GPU] Probing max batch size (starting at {probe_batch})...")
        while probe_batch >= 4:
            try:
                _p_starts = cupy.random.randint(0, max_start, size=min(probe_batch, max_start))
                _p_windows = gpu_bits[_p_starts[:, None] + offsets[None, :]]
                _p_x = _p_windows[:, :-1].astype(cupy.float32)
                _p_t = _p_windows[:, -1].astype(cupy.int32)
                _p_out = probe_expert.forward(_p_x)
                probe_expert.sgd_step(_p_x, _p_t.astype(int), _p_out, lr=0.0, max_grad_norm=1.0)
                del _p_starts, _p_windows, _p_x, _p_t, _p_out
                import gc; gc.collect()
                cupy.get_default_memory_pool().free_all_blocks()
                cupy.get_default_pinned_memory_pool().free_all_blocks()
                print(f"[GPU] Probe OK at batch={probe_batch}")
                break
            except Exception as _pe:
                if 'out of memory' in str(_pe).lower() or 'OutOfMemory' in type(_pe).__name__:
                    probe_expert.saved_ctx = None
                    import gc; gc.collect()
                    try:
                        cupy.get_default_memory_pool().free_all_blocks()
                        cupy.get_default_pinned_memory_pool().free_all_blocks()
                    except:
                        pass
                    probe_batch //= 2
                    print(f"[GPU] Probe OOM, trying batch={probe_batch}")
                else:
                    raise
        batch_size = max(probe_batch, 4)
        self.config.mini_batch_size = batch_size
        print(f"[GPU] Crunching {max_start:,} samples, batch={batch_size}, {n_experts} expert(s) round-robin")

        while self.running:
            eidx, eobj = experts[cycle % n_experts]
            cycle += 1

            starts = cupy.random.randint(0, max_start, size=min(batch_size, max_start))
            full_windows = gpu_bits[starts[:, None] + offsets[None, :]]
            x = full_windows[:, :-1].astype(cupy.float32)  # input bits
            target_bits = full_windows[:, -1].astype(cupy.int32)  # target bit
            actual_size = len(x)

            while True:
                try:
                    output = eobj.forward(x)
                    break
                except Exception as oom_err:
                    if 'OutOfMemory' in type(oom_err).__name__ or 'out of memory' in str(oom_err).lower():
                        if len(x) <= 1:
                            raise
                        new_size = len(x) // 2
                        x = x[:new_size]
                        target_bits = target_bits[:new_size]
                        actual_size = new_size
                        self.config.mini_batch_size = new_size
                        batch_size = new_size
                        eobj.saved_ctx = None
                        try:
                            import gc; gc.collect()
                            cupy.get_default_memory_pool().free_all_blocks()
                            cupy.get_default_pinned_memory_pool().free_all_blocks()
                        except:
                            pass
                        print(f"[GPU] OOM! Batch halved to {new_size}")
                    else:
                        raise

            target_int = target_bits.astype(int)  # Keep on same device as x (GPU or CPU)
            eobj.sgd_step(x, target_int, output, lr=self.config.hebbian_lr, max_grad_norm=1.0)
            del output, target_bits, target_int, x

            per_expert_samples[eidx] += actual_size
            self.samples_trained += actual_size
            if self.shared_samples is not None:
                with self.shared_samples.get_lock():
                    self.shared_samples.value += actual_size
            self.samples_since_sync += actual_size

            if self.samples_trained % self.config.batch_report_interval == 0:
                self._report_progress()

            now = time.time()
            if now - last_yield >= 0.5:
                await asyncio.sleep(0)
                last_yield = time.time()

        # Free GPU training data before self-test
        del gpu_bits, offsets

        for idx, cnt in per_expert_samples.items():
            print(f"[GPU] Expert {idx}: {cnt:,} samples")

    def _report_progress(self):
        """Print training progress."""
        now = time.time()
        elapsed = now - self.start_time
        samples_since_report = self.samples_trained - self.last_report_samples
        time_since_report = now - self.last_report_time

        if time_since_report > 0:
            samples_per_sec = samples_since_report / time_since_report
        else:
            samples_per_sec = 0

        # Update system metrics for endocrine system
        self.endocrine.update_metrics()

        # Update shared progress arrays for main process to read
        if self.shared_progress is not None:
            self.shared_progress[self.worker_id] = self.samples_since_sync
        if self.shared_experts is not None:
            self.shared_experts[self.worker_id] = self.config.expert_idx

        self.last_report_time = now
        self.last_report_samples = self.samples_trained

    async def run(self):
        """Run all components concurrently."""
        await self.initialize()

        # Start all tasks
        tasks = [
            asyncio.create_task(self.data_producer.start()),
            asyncio.create_task(self.train_loop()),
        ]

        if HAS_WEBSOCKETS and self.weight_sync is not None:
            tasks.append(asyncio.create_task(self.weight_sync.start()))

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("\n[Trainer] Shutting down...")
        finally:
            self.running = False
            self.data_producer.stop()
            if self.weight_sync is not None:
                self.weight_sync.stop()
            # self._save_cached_weights()  # Disabled - fresh weights each session
            print(f"[Trainer] Final: {self.samples_trained:,} samples trained")

    def stop(self):
        """Stop training."""
        self.running = False


# =============================================================================
# Parallel Multi-Expert Training
# =============================================================================

def run_single_worker(worker_id: int, server_url: str, lr: float, decay: float,
                      sync_interval: int, expert_type: str, batch_size: int,
                      memory_threshold_pct: float, authenticator: str = "",
                      auto_assign: bool = True,
                      shared_samples=None, shared_syncs=None,
                      shared_progress=None, shared_experts=None):
    """Run a single expert worker (for multiprocessing)."""
    config = ClientConfig(
        expert_idx=-1 if auto_assign else worker_id,  # -1 = request from coordinator
        auto_assign=auto_assign,
        server_url=server_url,
        hebbian_lr=lr,
        hebbian_decay=decay,
        sync_interval=sync_interval,
        expert_type=expert_type,
        mini_batch_size=batch_size,
        authenticator=authenticator,
    )

    trainer = HebbianTrainer(config, worker_id=worker_id)
    trainer.endocrine.memory_threshold_pct = memory_threshold_pct
    trainer.shared_samples = shared_samples
    trainer.shared_syncs = shared_syncs
    trainer.shared_progress = shared_progress
    trainer.shared_experts = shared_experts

    print(f"[Worker-{worker_id}] Starting (will request expert from coordinator)")
    try:
        asyncio.run(trainer.run())
    except KeyboardInterrupt:
        print(f"[Worker-{worker_id}] Shutdown")


def run_parallel_workers(num_workers: int, server_url: str,
                         lr: float, decay: float, sync_interval: int,
                         expert_type: str, batch_size: int, memory_threshold_pct: float,
                         cpu_mode: str = "adaptive", cpu_fixed_pct: float = 50.0,
                         cpu_backoff_threshold: float = 70.0, authenticator: str = "",
                         auto_assign: bool = True):
    """Launch multiple expert workers in parallel using multiprocessing."""
    import multiprocessing as mp

    # Limit workers based on RAM threshold - snap to nearest valid count
    cpu_count = os.cpu_count() or 4
    max_workers_by_ram = max(1, round(cpu_count * (memory_threshold_pct / 100)))
    if num_workers > max_workers_by_ram:
        print(f"[RAM Limit] Reducing workers from {num_workers} to {max_workers_by_ram} (RAM threshold {memory_threshold_pct:.0f}% of {cpu_count} CPUs)")
        num_workers = max_workers_by_ram

    # Shared counters for stats
    shared_samples = mp.Value('i', 0)
    shared_syncs = mp.Value('i', 0)
    # Per-worker progress and expert arrays
    shared_progress = mp.Array('i', num_workers)  # samples_since_sync per worker
    shared_experts = mp.Array('i', [-1] * num_workers)  # expert_idx per worker

    # Get system RAM info
    total_ram = psutil.virtual_memory().total / (1024**3)

    # CPU mode description
    if cpu_mode == "fixed":
        cpu_desc = f"Fixed {cpu_fixed_pct:.0f}%"
    else:
        cpu_desc = f"Adaptive (back off > {cpu_backoff_threshold:.0f}%)"

    assign_mode = "Load-balanced (coordinator assigns)" if auto_assign else "Sequential"

    print("=" * 60)
    print("  Axiom PARALLEL Streaming Client - Hebbian Learning")
    print("  'Neurons that fire together, wire together'")
    print("=" * 60)
    print(f"  Workers: {num_workers}")
    print(f"  Expert Assignment: {assign_mode}")
    print(f"  Expert Type: {expert_type.upper()}")
    print(f"  System RAM: {total_ram:.1f}GB")
    print(f"  Max Workers: {memory_threshold_pct:.0f}% of CPU threads")
    print(f"  CPU Mode: {cpu_desc}")
    print(f"  Server: {server_url or 'OFFLINE'}")
    print("=" * 60)
    print()

    # Scan contribute folder and report file classification
    contribute_path = Path.home() / "Axiom" / "contribute"
    if contribute_path.exists():
        producer = DataProducer(ClientConfig())
        archive_count = 0
        binary_count = 0
        readable_count = 0
        for filepath in contribute_path.rglob('*'):
            if filepath.is_file():
                ftype = producer.classify_file(filepath)
                if ftype == 'binary':
                    binary_count += 1
                elif ftype in ('zip', 'gzip', 'bz2', 'xz', 'tar'):
                    archive_count += 1
                else:
                    readable_count += 1
        if binary_count > 0:
            print(f"[Info] Files: {readable_count} readable, {archive_count} archives (will extract), {binary_count} binary (skipped)")
        elif archive_count > 0:
            print(f"[Info] Files: {readable_count} readable, {archive_count} archives (will extract)")

    # Spawn worker processes - all monitor system memory %
    processes = []
    for i in range(num_workers):
        p = mp.Process(
            target=run_single_worker,
            args=(i, server_url, lr, decay, sync_interval,
                  expert_type, batch_size, memory_threshold_pct, authenticator,
                  auto_assign, shared_samples, shared_syncs,
                  shared_progress, shared_experts)
        )
        p.start()
        processes.append(p)
        print(f"[Main] Launched worker {i} (PID: {p.pid})")

    # Track suspended workers for CPU management
    suspended = set()
    active_count = num_workers

    # Monitor and wait
    last_status_time = time.time()
    try:
        while any(p.is_alive() for p in processes):
            time.sleep(0.5)
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()

            alive_workers = [(i, p) for i, p in enumerate(processes) if p.is_alive()]
            active_workers = [(i, p) for i, p in alive_workers if i not in suspended]

            # Log status every 10 seconds
            if time.time() - last_status_time >= 10:
                last_status_time = time.time()
                print(f"[Monitor] CPU: {cpu:.0f}%, MEM: {mem.percent:.0f}% (threshold: {memory_threshold_pct:.0f}%), Active: {len(active_workers)}, Suspended: {len(suspended)}")

            # CPU management based on mode
            if cpu_mode == "adaptive":
                # Adaptive: back off when system is busy
                if cpu > cpu_backoff_threshold and len(active_workers) > 1:
                    # Suspend some workers to reduce CPU
                    for i, p in active_workers[len(active_workers)//2:]:
                        if i not in suspended:
                            try:
                                psutil.Process(p.pid).suspend()
                                suspended.add(i)
                                print(f"[CPU] Suspended worker {i} (system CPU {cpu:.0f}% > {cpu_backoff_threshold:.0f}%)")
                            except:
                                pass
                elif cpu < cpu_backoff_threshold - 10 and suspended:
                    # Resume workers when CPU is lower
                    for i in list(suspended):
                        if processes[i].is_alive():
                            try:
                                psutil.Process(processes[i].pid).resume()
                                suspended.discard(i)
                                print(f"[CPU] Resumed worker {i} (system CPU {cpu:.0f}%)")
                            except:
                                suspended.discard(i)

            elif cpu_mode == "fixed":
                # Fixed: try to maintain target CPU
                target = cpu_fixed_pct
                if cpu > target + 10 and len(active_workers) > 1:
                    # Too high - suspend a worker
                    for i, p in active_workers[-1:]:
                        if i not in suspended:
                            try:
                                psutil.Process(p.pid).suspend()
                                suspended.add(i)
                                print(f"[CPU] Suspended worker {i} (CPU {cpu:.0f}% > target {target:.0f}%)")
                            except:
                                pass
                elif cpu < target - 10 and suspended:
                    # Too low - resume a worker
                    for i in list(suspended)[:1]:
                        if processes[i].is_alive():
                            try:
                                psutil.Process(processes[i].pid).resume()
                                suspended.discard(i)
                                print(f"[CPU] Resumed worker {i} (CPU {cpu:.0f}% < target {target:.0f}%)")
                            except:
                                suspended.discard(i)


            samples = shared_samples.value
            syncs = shared_syncs.value
            status = f"[Monitor] CPU: {cpu:.0f}% | RAM: {mem.percent:.0f}% ({mem.used/(1024**3):.1f}/{mem.total/(1024**3):.1f}GB) | Samples: {samples:,} | Syncs: {syncs}"
            if suspended:
                status += f" | Suspended: {len(suspended)}"
            print(status)

            # Output per-worker progress for UI to parse
            for i in range(num_workers):
                prog = shared_progress[i]
                expert = shared_experts[i]
                print(f"[Worker-{i}] Expert: {expert} | Progress: {prog}/{sync_interval}")

    except KeyboardInterrupt:
        print("\n[Main] Shutting down workers...")
        # Resume any suspended workers before terminating
        for i in suspended:
            if processes[i].is_alive():
                try:
                    psutil.Process(processes[i].pid).resume()
                except:
                    pass
        for p in processes:
            p.terminate()

    for p in processes:
        p.join()
    print("[Main] All workers finished")


# =============================================================================
# Main Entry Point
# =============================================================================


def resolve_boinc_path(filepath):
    """Resolve BOINC soft link files to actual path."""
    import os
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                content = f.read().strip()
            if content.startswith('<soft_link>') and content.endswith('</soft_link>'):
                actual_path = content[11:-12].strip()
                print(f"[BOINC] Resolved soft link: {actual_path}")
                return actual_path
        except:
            pass
    return filepath



def _run_ecosystem_mode(wu_data, result_file):
    """Run ecosystem mode: download net, evolve it, upload it back."""
    import urllib.request, struct, gzip as _gzip, time as _time

    server_host = wu_data.get('server_host', '65.21.196.61')
    eco_port = wu_data.get('ecosystem_port', ECOSYSTEM_PORT)
    run_duration = wu_data.get('run_duration', 600)
    base_url = "http://{}:{}".format(server_host, eco_port)

    print("[ECO] Ecosystem mode: server={}, duration={}s".format(base_url, run_duration))

    # Step 1: Download assigned net
    assign_url = "{}/ecosystem/assign".format(base_url)
    print("[ECO] Requesting net from {}...".format(assign_url))

    net_id = -1
    mutate_steps = 3000
    mutate_lr = 0.005
    noise_scale = 0.001
    batch_size = 128

    try:
        req = urllib.request.Request(assign_url)
        with urllib.request.urlopen(req, timeout=600) as resp:
            net_id = int(resp.headers.get('X-Net-Id', -1))
            net_d = int(resp.headers.get('X-Net-D', 256))
            net_h = int(resp.headers.get('X-Net-H', 8))
            net_ff = int(resp.headers.get('X-Net-FF', 1024))
            net_layers = int(resp.headers.get('X-Net-L', 4))
            mutate_steps = int(resp.headers.get('X-Mutate-Steps', 3000))
            mutate_lr = float(resp.headers.get('X-Mutate-LR', 0.005))
            noise_scale = float(resp.headers.get('X-Noise-Scale', 0.001))
            batch_size = int(resp.headers.get('X-Batch-Size', 128))
            seq_len = 64

            raw_data = resp.read()
            try:
                raw_data = _gzip.decompress(raw_data)
            except:
                pass

        HEADER_FMT = '<4sIHHHHH'
        HEADER_SIZE = struct.calcsize(HEADER_FMT)
        magic, param_count, _, _, _, _, seq_len = struct.unpack(HEADER_FMT, raw_data[:HEADER_SIZE])
        if magic != b'ENET':
            raise ValueError("Bad magic: {}".format(magic))
        weights = _real_numpy.frombuffer(raw_data[HEADER_SIZE:HEADER_SIZE + param_count * 4],
                                          dtype=_real_numpy.float32).copy()
        print("[ECO] Got net {}: {:,} params, d={}, h={}, ff={}, L={}, {:.1f}MB".format(
            net_id, param_count, net_d, net_h, net_ff, net_layers, len(raw_data)/1024/1024))

    except Exception as e:
        print("[ECO] Failed to download net: {}".format(e))
        import traceback; traceback.print_exc()
        with open(result_file, 'w') as f:
            json.dump({"error": str(e)}, f)
        return

    # Step 2: Create model
    from simple_ml import SimpleTransformer as _ST
    net = _ST(seq_len=seq_len, d_model=net_d, n_heads=net_h, d_ff=net_ff, n_layers=net_layers)
    net.set_weights_flat(weights)
    print("[ECO] Model: {:,} params".format(net.get_param_count()))

    # Step 3: Download seed data
    print("[ECO] Downloading seed data...")
    import ssl as _ssl2
    _ctx2 = _ssl2.create_default_context()
    _ctx2.check_hostname = False
    _ctx2.verify_mode = _ssl2.CERT_NONE
    _opener2 = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx2))
    try:
        seed_url = "https://axiom.heliex.net/seed_data.php"
        with _opener2.open(seed_url, timeout=120) as resp:
            seed_data = resp.read()
        print("[ECO] Seed data: {:.1f}MB".format(len(seed_data)/1024/1024))
    except Exception as e:
        print("[ECO] Seed PHP failed: {}, trying static".format(e))
        fallback_url = "https://axiom.heliex.net/seed_data.bin"
        with _opener2.open(fallback_url, timeout=120) as resp:
            seed_data = resp.read()

    # Step 4: Pre-training BPC
    def _eco_eval_bpc(model, data, n_samples=100):
        rng = _real_numpy.random.RandomState(42)
        total_bits = 0.0
        bs = 50
        for start in range(0, n_samples, bs):
            b = min(bs, n_samples - start)
            bx, bt = [], []
            for _ in range(b):
                s = rng.randint(0, len(data) - seq_len - 1)
                c = data[s:s + seq_len + 1]
                bx.append(list(c[:seq_len]))
                bt.append(c[seq_len])
            x = np.array(bx, dtype=np.float32)
            t = _real_numpy.array(bt, dtype=_real_numpy.int32)
            out = model.forward(x)
            logits = to_cpu(out).reshape(-1, 256)
            if hasattr(model, 'saved_ctx'):
                model.saved_ctx = {}
            l = logits - logits.max(axis=-1, keepdims=True)
            e = _real_numpy.exp(l)
            probs = e / e.sum(axis=-1, keepdims=True)
            for i in range(b):
                p = max(float(probs[i, t[i]]), 1e-12)
                total_bits += -_real_numpy.log2(p)
        return total_bits / n_samples

    bpc_before = _eco_eval_bpc(net, seed_data)
    print("[ECO] BPC before: {:.4f}".format(bpc_before))

    # Step 5: Mutate + Train
    w = to_cpu(_real_numpy.asarray(net.get_weights_flat(), dtype=_real_numpy.float32))
    rng = _real_numpy.random.RandomState(int(_time.time() * 1000) % 2**31)
    noise = rng.randn(len(w)).astype(_real_numpy.float32) * noise_scale
    net.set_weights_flat(w + noise)

    # GPU auto-detect
    use_gpu = False
    train_net = net
    _np = _real_numpy
    try:
        import cupy
        train_net = _ST(seq_len=seq_len, d_model=net_d, n_heads=net_h, d_ff=net_ff, n_layers=net_layers)
        gpu_w = cupy.asarray(net.get_weights_flat())
        train_net.set_weights_flat(gpu_w)
        use_gpu = True
        _np = cupy
        # Cap GPU batch size - d=768 backprop OOMs at batch_size=256
        if batch_size > 64:
            print("[ECO] GPU: capping batch_size from {} to 64".format(batch_size))
            batch_size = 64
        print("[ECO] GPU detected, batch_size={}".format(batch_size))
    except:
        # Cap CPU batch size for d=768 model
        if batch_size > 12:
            print("[ECO] CPU: capping batch_size from {} to 12".format(batch_size))
            batch_size = 12
        print("[ECO] CPU mode, batch_size={}".format(batch_size))

    data_len = len(seed_data)
    t0 = _time.time()
    samples_trained = 0
    effective_duration = run_duration - 60
    step = 0
    min_batch = 8

    while step < mutate_steps:
        elapsed = _time.time() - t0
        if elapsed >= effective_duration:
            print("[ECO] Time limit at step {} ({:.1f}s)".format(step, elapsed))
            break

        bx, bt = [], []
        for _ in range(batch_size):
            s = rng.randint(0, data_len - seq_len - 1)
            c = seed_data[s:s + seq_len + 1]
            bx.append(list(c[:seq_len]))
            bt.append(c[seq_len])
        x = _np.array(bx, dtype=_np.float32)
        t = _np.array(bt, dtype=_np.int32)

        try:
            out = train_net.forward(x)
            train_net.sgd_step(x, t, out, lr=mutate_lr, max_grad_norm=1.0)
        except Exception as _oom_err:
            _oom_str = str(_oom_err).lower()
            if 'out of memory' in _oom_str or 'memoryallocation' in _oom_str or 'oom' in _oom_str:
                if hasattr(train_net, 'saved_ctx'):
                    train_net.saved_ctx = {}
                if use_gpu:
                    import cupy
                    cupy.get_default_memory_pool().free_all_blocks()
                old_bs = batch_size
                batch_size = max(min_batch, batch_size // 2)
                print("[ECO] OOM at batch_size={}, halving to {}".format(old_bs, batch_size))
                if batch_size < min_batch:
                    print("[ECO] Batch size too small, stopping training")
                    break
                continue
            else:
                raise

        if hasattr(train_net, 'saved_ctx'):
            train_net.saved_ctx = {}

        samples_trained += batch_size
        step += 1

        if step % 100 == 0:
            frac = min(0.95, elapsed / max(effective_duration, 1))
            try:
                with open('boinc_fraction_done', 'w') as ff:
                    ff.write("{:.6f}\n".format(frac))
            except:
                pass

        if step % 500 == 0:
            print("[ECO] Step {}/{}, {:,} samples, {:.1f}s, {:.0f} samp/s".format(
                step, mutate_steps, samples_trained, elapsed, samples_trained/max(elapsed, 0.1)))

    train_time = _time.time() - t0
    print("[ECO] Training done: {} steps, {:,} samples, {:.1f}s".format(step, samples_trained, train_time))

    # Transfer to CPU
    if use_gpu:
        import cupy
        final_weights = cupy.asnumpy(train_net.get_weights_flat()).astype(_real_numpy.float32)
        net.set_weights_flat(final_weights)
    else:
        final_weights = to_cpu(_real_numpy.asarray(net.get_weights_flat(), dtype=_real_numpy.float32))

    # Step 6: Post-training BPC
    bpc_after = _eco_eval_bpc(net, seed_data)
    print("[ECO] BPC after: {:.4f} (delta={:+.4f})".format(bpc_after, bpc_after - bpc_before))

    # Step 7: Upload
    HEADER_FMT = '<4sIHHHHH'
    header = struct.pack(HEADER_FMT, b'ENET', len(final_weights),
                         net_d, net_h, net_ff, net_layers, seq_len)
    net_data = header + final_weights.tobytes()
    compressed = _gzip.compress(net_data, compresslevel=1)

    submit_url = "{}/ecosystem/submit".format(base_url)
    print("[ECO] Uploading net {} ({:.1f}MB)...".format(net_id, len(compressed)/1024/1024))

    success = False
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                submit_url, data=compressed, method='POST',
                headers={
                    'Content-Type': 'application/octet-stream',
                    'Content-Encoding': 'gzip',
                    'X-Net-Id': str(net_id),
                    'X-Worker-BPC': '{:.4f}'.format(bpc_after),
                    'X-Worker-Samples': str(samples_trained),
                })
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read())
            print("[ECO] Upload OK: {}".format(result))
            success = True
            break
        except Exception as e:
            print("[ECO] Upload attempt {} failed: {}".format(attempt+1, e))
            _time.sleep(3)

    # Step 8: Write result
    result_data = {
        "mode": "ecosystem",
        "net_id": net_id,
        "bpc_before": bpc_before,
        "bpc_after": bpc_after,
        "bpc_delta": bpc_after - bpc_before,
        "samples_trained": samples_trained,
        "steps": step,
        "train_time": train_time,
        "upload_success": success,
    }
    try:
        with open(result_file, 'w') as f:
            json.dump(result_data, f, indent=2)
    except:
        pass

    try:
        with open('boinc_fraction_done', 'w') as ff:
            ff.write("1.0\n")
    except:
        pass

    print("[ECO] Done! Net {}: BPC {:.4f} -> {:.4f} ({:+.4f}), {:,} samples".format(
        net_id, bpc_before, bpc_after, bpc_after - bpc_before, samples_trained))



# ── Experiment container mode ──
def _run_experiment_mode(wu, result_file):
    """Execute an arbitrary experiment script from a URL.
    
    The script runs in a restricted namespace with numpy and stdlib available.
    It must write results to 'experiment_result.json' in the current directory.
    """
    import urllib.request
    import ssl
    import threading
    import traceback as tb
    
    experiment_name = wu.get('experiment_name', 'unknown')
    script_url = wu.get('script_url', '')
    run_duration = wu.get('run_duration', 600)
    
    print(f"[EXP] Experiment mode: {experiment_name}")
    print(f"[EXP] Script URL: {script_url}")
    print(f"[EXP] Run duration: {run_duration}s")
    
    if not script_url:
        with open(result_file, 'w') as f:
            json.dump({"error": "No script_url in work unit", "mode": "experiment", 
                       "experiment": experiment_name}, f)
        sys.exit(1)
    
    # Download the experiment script
    print(f"[EXP] Downloading script...")
    try:
        _exp_ctx = ssl.create_default_context()
        _exp_ctx.check_hostname = False
        _exp_ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(script_url, headers={'User-Agent': 'AxiomBOINC/6.0'})
        with urllib.request.urlopen(req, timeout=30, context=_exp_ctx) as resp:
            script_code = resp.read().decode('utf-8')
        print(f"[EXP] Downloaded {len(script_code)} bytes")
    except Exception as e:
        print(f"[EXP] Download failed: {e}")
        with open(result_file, 'w') as f:
            json.dump({"error": f"Script download failed: {e}", "mode": "experiment",
                       "experiment": experiment_name}, f)
        sys.exit(1)
    
    # Write script to local file for tracebacks
    with open('experiment_script.py', 'w', encoding='utf-8') as f:
        f.write(script_code)
    
    # Progress updater thread
    t0 = time.time()
    exp_done = threading.Event()
    
    def progress_updater():
        while not exp_done.is_set():
            elapsed = time.time() - t0
            frac = min(elapsed / max(run_duration, 1), 0.99)
            try:
                with open('boinc_fraction_done', 'w') as f:
                    f.write(f"{frac:.4f}\n")
            except:
                pass
            # Also check for experiment's own fraction_done
            try:
                with open('fraction_done', 'w') as f:
                    f.write(f"{frac:.4f}\n")
            except:
                pass
            exp_done.wait(2.0)
    
    progress_thread = threading.Thread(target=progress_updater, daemon=True)
    progress_thread.start()
    
    # Capture stdout
    import io
    stdout_capture = io.StringIO()
    
    # Execute the script
    print(f"[EXP] Executing experiment '{experiment_name}'...")
    exec_error = None
    try:
        # Build namespace with numpy and common modules available
        import numpy
        exec_namespace = {
            '__builtins__': __builtins__,
            '__name__': '__main__',
            '__file__': 'experiment_script.py',
            'np': numpy,
            'numpy': numpy,
        }
        
        # Inject GPU support if cupy is available
        try:
            import cupy
            exec_namespace['cupy'] = cupy
            exec_namespace['cp'] = cupy
            exec_namespace['HAS_GPU'] = True
            gpu_name = cupy.cuda.runtime.getDeviceProperties(0)['name'].decode()
            gpu_mem = cupy.cuda.runtime.getDeviceProperties(0)['totalGlobalMem']
            exec_namespace['GPU_NAME'] = gpu_name
            exec_namespace['GPU_MEMORY_MB'] = gpu_mem // (1024 * 1024)
            print(f"[EXP] GPU available: {gpu_name} ({gpu_mem // (1024*1024)}MB)")
        except Exception:
            exec_namespace['HAS_GPU'] = False
            exec_namespace['GPU_NAME'] = None
            exec_namespace['GPU_MEMORY_MB'] = 0
            print("[EXP] No GPU available, running CPU-only")
        
        # Compile and execute
        compiled = compile(script_code, 'experiment_script.py', 'exec')
        exec(compiled, exec_namespace)
        
    except Exception as e:
        exec_error = f"{type(e).__name__}: {e}\n{tb.format_exc()}"
        print(f"[EXP] Execution error: {exec_error}")
    
    elapsed = time.time() - t0
    exp_done.set()
    
    # Read experiment results
    exp_result = None
    if os.path.exists('experiment_result.json'):
        try:
            with open('experiment_result.json', 'r') as f:
                exp_result = json.load(f)
            print(f"[EXP] Read experiment_result.json successfully")
        except Exception as e:
            print(f"[EXP] Error reading experiment_result.json: {e}")
    
    # Build final result
    final_result = {
        "mode": "experiment",
        "experiment": experiment_name,
        "status": "completed" if exec_error is None else "error",
        "elapsed": round(elapsed, 1),
        "error": exec_error,
    }
    
    if exp_result:
        final_result["experiment_result"] = exp_result
    
    # Write BOINC result file
    with open(result_file, 'w') as f:
        json.dump(final_result, f, indent=2)
    
    print(f"[EXP] Experiment '{experiment_name}' finished in {elapsed:.1f}s")
    print(f"[EXP] Status: {final_result['status']}")
    if exp_result and 'summary' in exp_result:
        print(f"[EXP] Summary: {exp_result['summary']}")



def run_boinc_mode(wu_file, result_file):
    """Run in BOINC work unit mode."""
    import os
    print("[BOINC] Starting BOINC mode")
    if HAS_GPU:
        import cupy
        _mem = cupy.cuda.Device(0).mem_info
        print(f"[BOINC] GPU detected: {_mem[1]//1024//1024}MB VRAM, {_mem[0]//1024//1024}MB free")
    print(f"[BOINC] Work unit: {wu_file}")
    print(f"[BOINC] Result file: {result_file}")
    print(f"[BOINC] Current directory: {os.getcwd()}")
    print(f"[BOINC] Directory contents: {os.listdir('.')}")

    wu_file = resolve_boinc_path(wu_file)
    result_file = resolve_boinc_path(result_file)
    print(f"[BOINC] Actual WU file: {wu_file}")

    # Read work unit
    wu = None
    if os.path.exists(wu_file):
        try:
            with open(wu_file, 'r') as f:
                wu = json.load(f)
            print("[BOINC] Successfully loaded work unit")
        except Exception as e:
            print(f"[BOINC] Error reading work unit: {e}")

    if wu is None:
        print("[BOINC] Failed to read work unit")
        with open(result_file, 'w') as f:
            json.dump({"error": "Could not read work unit", "syncs": 0, "samples": 0}, f)
        return

    # -- Experiment container mode --
    if wu.get('mode') == 'experiment' or wu.get('script_url'):
        print('[BOINC] Experiment container mode detected')
        try:
            _run_experiment_mode(wu, result_file)
        except Exception as _exp_err:
            print('[EXP] FATAL: {}'.format(_exp_err))
            import traceback; traceback.print_exc()
            try:
                with open(result_file, 'w') as _ef:
                    json.dump({'error': str(_exp_err), 'mode': 'experiment'}, _ef)
            except:
                pass
        try:
            with open('boinc_fraction_done', 'w') as _ff:
                _ff.write('1.0' + chr(10))
        except:
            pass
        try:
            with open('fraction_done', 'w') as _ff:
                _ff.write('1.0' + chr(10))
        except:
            pass
        os._exit(0)

    # ── Ecosystem mode: completely different pipeline ──
    if wu.get('mode') == 'ecosystem':
        print("[BOINC] Ecosystem mode detected")
        try:
            _run_ecosystem_mode(wu, result_file)
        except Exception as _eco_err:
            print("[ECO] FATAL: {}".format(_eco_err))
            import traceback; traceback.print_exc()
            try:
                with open(result_file, "w") as _ef:
                    json.dump({"error": str(_eco_err), "mode": "ecosystem"}, _ef)
            except:
                pass
        try:
            with open("boinc_fraction_done", "w") as _ff:
                _ff.write("1.0\n")
        except:
            pass
        os._exit(0)

    # -- No recognized mode: error out --
    mode = wu.get('mode', 'unknown')
    print(f'[BOINC] ERROR: Unrecognized work unit mode: {mode}')
    print('[BOINC] Expected mode=experiment. Legacy training mode has been removed.')
    with open(result_file, 'w') as f:
        json.dump({'error': f'Unrecognized mode: {mode}', 'samples': 0, 'syncs': 0}, f)
    try:
        with open('boinc_fraction_done', 'w') as _ff:
            _ff.write('1.0' + chr(10))
    except:
        pass
    try:
        with open('fraction_done', 'w') as _ff:
            _ff.write('1.0' + chr(10))
    except:
        pass
    os._exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Axiom Experiment Client (BOINC)"
    )
    # BOINC wrapper positional args
    parser.add_argument('wu_file', help='BOINC work unit JSON file')
    parser.add_argument('result_file', help='BOINC result JSON file')

    args = parser.parse_args()
    run_boinc_mode(args.wu_file, args.result_file)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
