import sys
import os

# PyInstaller bundle: set up library paths so CuPy finds CUDA libs
if getattr(sys, "_MEIPASS", None):
    _meipass = sys._MEIPASS

    if sys.platform == "win32":
        os.environ["PATH"] = _meipass + ";" + os.environ.get("PATH", "")
        # Create 'bin' directory that CuPy expects for DLL loading
        os.makedirs(os.path.join(_meipass, 'bin'), exist_ok=True)
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(_meipass)
        if "CUDA_PATH" not in os.environ:
            os.environ["CUDA_PATH"] = _meipass
        # Copy CuPy's bundled CUDA headers for NVRTC JIT (needed when no system CUDA toolkit)
        _cupy_cuda_inc = os.path.join(_meipass, "cupy", "_core", "include", "cupy", "_cuda", "cuda-12")
        _dest_inc = os.path.join(_meipass, "include")
        if os.path.isdir(_cupy_cuda_inc) and not os.path.isfile(os.path.join(_dest_inc, "cuda_fp16.h")):
            try:
                os.makedirs(_dest_inc, exist_ok=True)
                import shutil
                for _hf in os.listdir(_cupy_cuda_inc):
                    shutil.copy2(os.path.join(_cupy_cuda_inc, _hf), os.path.join(_dest_inc, _hf))
            except Exception:
                pass
    else:
        # Find system CUDA installation (if any)
        _sys_cuda = None
        try:
            _candidates = ["/usr/local/cuda"]
            if os.path.isdir("/usr/local"):
                _candidates += sorted(
                    [os.path.join("/usr/local", d) for d in os.listdir("/usr/local") if d.startswith("cuda-")],
                    reverse=True
                )
            for _p in _candidates:
                if os.path.isdir(os.path.join(_p, "lib64")) and os.path.isdir(os.path.join(_p, "include")):
                    _sys_cuda = _p
                    break
        except Exception:
            pass

        if _sys_cuda:
            # System CUDA found - use its NVRTC (supports newer GPU architectures)
            _sys_lib = os.path.join(_sys_cuda, "lib64")
            os.environ["LD_LIBRARY_PATH"] = _sys_lib + ":" + _meipass + ":" + os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["CUDA_PATH"] = _sys_cuda
            # Replace bundled libnvrtc with symlink to system version
            # so CuPy uses system NVRTC (which supports newer GPU archs)
            _bundled = os.path.join(_meipass, "libnvrtc.so.12")
            # Find system libnvrtc (could be .so.12, .so.13, etc.)
            _sys_nvrtc = None
            try:
                import glob as _glob
                _nvrtc_candidates = sorted(_glob.glob(os.path.join(_sys_lib, "libnvrtc.so.*")), reverse=True)
                # Filter out builtins and stubs, pick highest version
                _nvrtc_candidates = [c for c in _nvrtc_candidates if "builtins" not in c and "static" not in c and "alt" not in c]
                if _nvrtc_candidates:
                    _sys_nvrtc = _nvrtc_candidates[0]
            except Exception:
                pass
            if _sys_nvrtc and os.path.exists(_bundled):
                try:
                    os.remove(_bundled)
                    os.symlink(_sys_nvrtc, _bundled)
                except Exception:
                    pass
        else:
            # No system CUDA - use bundled libs only
            os.environ["LD_LIBRARY_PATH"] = _meipass + ":" + os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["CUDA_PATH"] = _meipass
            # Copy CuPy's bundled CUDA headers to _MEIPASS/include/ for NVRTC JIT
            _cupy_cuda_inc = os.path.join(_meipass, "cupy", "_core", "include", "cupy", "_cuda", "cuda-12")
            _dest_inc = os.path.join(_meipass, "include")
            if os.path.isdir(_cupy_cuda_inc) and not os.path.isfile(os.path.join(_dest_inc, "cuda_fp16.h")):
                try:
                    os.makedirs(_dest_inc, exist_ok=True)
                    import shutil
                    for _hf in os.listdir(_cupy_cuda_inc):
                        shutil.copy2(os.path.join(_cupy_cuda_inc, _hf), os.path.join(_dest_inc, _hf))
                except Exception:
                    pass
            # Extract NVRTC 12.4 builtins for bundled NVRTC
            _nvrtc_so = os.path.join(_meipass, "libnvrtc-builtins.so.12.4")
            if not os.path.exists(_nvrtc_so):
                try:
                    import base64
                    from nvrtc_builtins_data import NVRTC_BUILTINS_B64
                    _data = base64.b64decode(NVRTC_BUILTINS_B64)
                    with open(_nvrtc_so, "wb") as _f:
                        _f.write(_data)
                except Exception:
                    pass

import math
import random
import json
import os

# GPU/CPU compute abstraction
# Try CuPy (NVIDIA GPU) first, fall back to NumPy (CPU), then pure Python

HAS_GPU = False
HAS_NUMPY = False
_real_numpy = None  # Always-CPU numpy for file I/O

# Check if this is the CUDA binary (has cupy bundled)
_IS_CUDA_BUILD = False
try:
    import cupy as _cupy_test
    _IS_CUDA_BUILD = True
    del _cupy_test
except ImportError:
    pass

try:
    import cupy as np
    import numpy as _real_numpy
    HAS_GPU = True
    HAS_NUMPY = True
    np.cuda.Device(0).use()
    _mem = np.cuda.Device(0).mem_info
    print(f"[GPU] CuPy CUDA: {_mem[1]//1024//1024}MB total, {_mem[0]//1024//1024}MB free")
except (ImportError, Exception) as _gpu_err:
    if _IS_CUDA_BUILD:
        # CUDA binary but GPU init failed - log error and exit so BOINC uses CPU version instead
        print(f"[GPU] CUDA initialization failed: {_gpu_err}")
        print("[GPU] This GPU binary requires NVIDIA driver 525+ for CUDA 12.x")
        print("[GPU] Please update your NVIDIA drivers or use the CPU version")
        sys.exit(1)
    try:
        import numpy as np
        import numpy as _real_numpy
        HAS_NUMPY = True
        print("[CPU] Using NumPy")
    except ImportError:
        print("[CPU] Pure Python fallback (slow)")


def to_cpu(arr):
    """Move array to CPU numpy if it's a CuPy array."""
    if HAS_GPU and hasattr(arr, 'get'):
        return arr.get()
    return arr


def to_device(arr):
    """Move array to GPU if CuPy is available."""
    if HAS_GPU and not hasattr(arr, 'device'):
        return np.asarray(arr)
    return arr


def get_device_info():
    """Return string describing compute device."""
    if HAS_GPU:
        dev = np.cuda.Device(0)
        mem = dev.mem_info
        return f"GPU: CuPy CUDA ({mem[1]//1024//1024}MB VRAM)"
    elif HAS_NUMPY:
        return "CPU: NumPy"
    else:
        return "CPU: Pure Python"

def softmax(logits, temperature: float = 1.0):
    t = float(temperature)
    if t <= 0:
        t = 1.0

    if HAS_NUMPY and hasattr(logits, 'ndim'):
        z = logits / t
        z = z - np.max(z, axis=-1, keepdims=True)
        exps = np.exp(z)
        return exps / np.sum(exps, axis=-1, keepdims=True)

    if hasattr(logits, "tolist"):
        logits = logits.tolist()

    if isinstance(logits, list) and logits and isinstance(logits[0], list):
        out = []
        for row in logits:
            out.append(softmax(row, t))
        return out

    xs = [float(v) / t for v in (logits or [])]
    if not xs:
        return []
    m = max(xs)
    exps = [math.exp(v - m) for v in xs]
    s = sum(exps) or 1.0
    return [e / s for e in exps]

class SimpleMLP:
    """
    A simple Multi-Layer Perceptron (Neural Network) suitable for 
    Federated Learning on client devices.
    
    Structure: Input -> Dense(Relu) -> ... -> Dense(Linear)
    """
    def __init__(self, layer_sizes, seed=None):
        if seed is not None:
            if HAS_NUMPY:
                _rng = _real_numpy if _real_numpy is not None else np
                _rng.random.seed(seed)
            else:
                random.seed(seed)
        
        self.layer_sizes = layer_sizes
        self.weights = []
        self.biases = []
        _rng_mlp = _real_numpy if (_real_numpy is not None and HAS_NUMPY) else (np if HAS_NUMPY else None)
        def _randn(*shape):
            if _rng_mlp is not None:
                return np.asarray(_rng_mlp.random.randn(*shape).astype(_rng_mlp.float32))
            return None
        
        for i in range(len(layer_sizes) - 1):
            input_dim = layer_sizes[i]
            output_dim = layer_sizes[i+1]
            scale = math.sqrt(2.0 / (input_dim + output_dim))
            
            if HAS_NUMPY:
                self.weights.append(_randn(input_dim, output_dim) * scale)
                self.biases.append(np.zeros(output_dim))
            else:
                self.weights.append([[random.gauss(0, scale) for _ in range(output_dim)] for _ in range(input_dim)])
                self.biases.append([0.0] * output_dim)

    def get_weights_flat(self):
        """Flatten all parameters into a single list/array for FL transmission."""
        flat_params = []
        for w, b in zip(self.weights, self.biases):
            if HAS_NUMPY:
                flat_params.extend(w.ravel())
                flat_params.extend(b.ravel())
            else:
                flat_params.extend([item for sublist in w for item in sublist])
                flat_params.extend(b)
        return flat_params

    def set_weights_flat(self, flat_weights):
        """Load parameters from a flat list/array."""
        if HAS_NUMPY:
            arr = np.array(flat_weights, dtype=np.float32)
            idx = 0
            for i in range(len(self.layer_sizes) - 1):
                input_dim = self.layer_sizes[i]
                output_dim = self.layer_sizes[i+1]
                
                # Weights
                s_w = input_dim * output_dim
                self.weights[i] = arr[idx : idx + s_w].reshape(input_dim, output_dim)
                idx += s_w
                
                # Biases
                s_b = output_dim
                self.biases[i] = arr[idx : idx + s_b]
                idx += s_b
        else:
            idx = 0
            for i in range(len(self.layer_sizes) - 1):
                input_dim = self.layer_sizes[i]
                output_dim = self.layer_sizes[i+1]
                
                # Weights
                for r in range(input_dim):
                    for c in range(output_dim):
                        self.weights[i][r][c] = flat_weights[idx]
                        idx += 1
                
                # Biases
                for j in range(output_dim):
                    self.biases[i][j] = flat_weights[idx]
                    idx += 1

    def forward(self, x):
        """Forward pass."""
        activations = [x]
        zs = [] # Weighted inputs
        
        for i in range(len(self.weights)):
            w = self.weights[i]
            b = self.biases[i]
            
            if HAS_NUMPY:
                z = np.dot(activations[-1], w) + b
                zs.append(z)
                if i < len(self.weights) - 1: # ReLU for hidden layers
                    activations.append(np.maximum(0, z))
                else: # Linear output for the last layer
                    activations.append(z)
            else:
                # Pure python
                current_activation = activations[-1]
                z = [0.0] * len(b)
                for j in range(len(b)): # output_dim
                    val = b[j]
                    for k in range(len(current_activation)): # input_dim
                        val += current_activation[k] * w[k][j]
                    z[j] = val
                zs.append(z)
                
                if i < len(self.weights) - 1: # ReLU
                    activations.append([max(0, val) for val in z])
                else: # Linear
                    activations.append(z)
        
        self.saved_ctx = (activations, zs) # Save for backward pass
        return activations[-1]

    def hebbian_update(self, lr=0.001, decay=0.999):
        """
        Hebbian learning: "neurons that fire together, wire together"

        Update rule: Δw_ij = η * pre_i * post_j
        With weight decay to prevent runaway growth.

        This uses the activations saved during forward() - no backward pass needed!
        Memory efficient: O(1) per layer instead of O(layers) for backprop.
        """
        if not hasattr(self, 'saved_ctx') or self.saved_ctx is None:
            return
        activations, zs = self.saved_ctx
        self.saved_ctx = None  # Clear immediately to free memory

        if HAS_NUMPY:
            for i in range(len(self.weights)):
                pre = activations[i]      # Input to this layer
                post = activations[i+1]   # Output of this layer (after activation)

                # Ensure 2D for batch processing
                if pre.ndim == 1:
                    pre = pre.reshape(1, -1)
                if post.ndim == 1:
                    post = post.reshape(1, -1)

                # Hebbian update: outer product of pre and post activations
                # Average over batch
                delta_w = np.dot(pre.T, post) / pre.shape[0]

                # Normalize to prevent explosion (Oja's rule inspired)
                delta_w_norm = np.linalg.norm(delta_w)
                if delta_w_norm > 1.0:
                    delta_w = delta_w / delta_w_norm

                # Apply update with weight decay
                self.weights[i] = decay * self.weights[i] + lr * delta_w

                # Bias update: average post activation
                self.biases[i] = decay * self.biases[i] + lr * np.mean(post, axis=0)
        else:
            # Pure Python fallback
            for i in range(len(self.weights)):
                pre = activations[i]
                post = activations[i+1]

                # Single sample case
                if not isinstance(pre[0], list):
                    pre = [pre]
                    post = [post]

                # Compute average Hebbian update
                for r in range(len(self.weights[i])):
                    for c in range(len(self.weights[i][0])):
                        delta = sum(pre[b][r] * post[b][c] for b in range(len(pre))) / len(pre)
                        self.weights[i][r][c] = decay * self.weights[i][r][c] + lr * delta

                # Bias update
                for c in range(len(self.biases[i])):
                    delta = sum(post[b][c] for b in range(len(post))) / len(post)
                    self.biases[i][c] = decay * self.biases[i][c] + lr * delta

    def get_weight_delta(self, reference_weights):
        """Get difference between current weights and reference (for sync)."""
        current = to_cpu(self.get_weights_flat())
        if HAS_NUMPY:
            _np = _real_numpy if _real_numpy is not None else np
            if not isinstance(current, _np.ndarray):
                current = _np.array(current, dtype=_np.float32)
            reference_weights = to_cpu(reference_weights)
            if not isinstance(reference_weights, _np.ndarray):
                reference_weights = _np.array(reference_weights, dtype=_np.float32)
            return current - reference_weights  # Returns CPU numpy array
        else:
            return [c - r for c, r in zip(current, reference_weights)]

    def apply_weight_delta(self, delta, alpha=0.5):
        """Apply a weight delta from peer (gossip averaging)."""
        current = to_cpu(self.get_weights_flat())
        if HAS_NUMPY:
            _np = _real_numpy if _real_numpy is not None else np
            if not isinstance(current, _np.ndarray):
                current = _np.array(current, dtype=_np.float32)
            delta = to_cpu(delta)
            if not isinstance(delta, _np.ndarray):
                delta = _np.array(delta, dtype=_np.float32)
            self.set_weights_flat(current + alpha * delta)
        else:
            new_weights = [c + alpha * d for c, d in zip(current, delta)]
            self.set_weights_flat(new_weights)

    def backward(self, x, y, y_pred, lr=0.01):
        """
        Compute gradients. Assumes MSE loss.
        Returns flattened gradients.
        """
        activations, zs = self.saved_ctx
        num_layers = len(self.layer_sizes)
        
        # dL/dy_pred = 2 * (y_pred - y) / m
        if HAS_NUMPY:
            m = x.shape[0]
            if not isinstance(y_pred, np.ndarray):
                y_pred = np.array(y_pred)
            if not isinstance(y, np.ndarray):
                y = np.array(y)
            delta = 2 * (y_pred - y) / m
        else:
            # For pure Python, assuming single sample
            delta = [2 * (y_pred[i] - y[i]) for i in range(len(y))]
        
        d_weights = [None] * (num_layers - 1)
        d_biases = [None] * (num_layers - 1)
        
        # Backpropagate through layers
        for L in reversed(range(num_layers - 1)):
            if HAS_NUMPY:
                # dL/db_L = delta
                d_biases[L] = np.sum(delta, axis=0)
                # dL/dW_L = activation_L-1.T * delta
                d_weights[L] = np.dot(activations[L].T, delta)
                
                if L > 0: # If not input layer
                    # dL/dactivation_L = delta * W_L.T
                    d_activation = np.dot(delta, self.weights[L].T)
                    # dL/dz_L-1 = d_activation * ReLU_derivative(z_L-1)
                    delta = d_activation * (zs[L-1] > 0)
            else:
                # Pure Python (single sample)
                
                # Gradients for biases
                d_biases[L] = delta
                
                # Gradients for weights
                d_weights[L] = [[0.0]*len(self.weights[L][0]) for _ in range(len(self.weights[L]))]
                for r in range(len(activations[L])): # input to this layer
                    for c in range(len(delta)): # output from this layer
                        d_weights[L][r][c] = activations[L][r] * delta[c]
                        
                if L > 0: # If not input layer
                    d_activation = [0.0] * len(self.biases[L-1])
                    for r in range(len(delta)): # current layer outputs
                        for c in range(len(self.biases[L-1])): # previous layer outputs
                            d_activation[c] += delta[r] * self.weights[L][c][r]
                    
                    delta = [d_activation[i] if zs[L-1][i] > 0 else 0 for i in range(len(zs[L-1]))]
        
        # Flatten all gradients
        flat_grads = []
        for w, b in zip(d_weights, d_biases):
            if HAS_NUMPY:
                flat_grads.extend(w.ravel())
                flat_grads.extend(b.ravel())
            else:
                flat_grads.extend([item for sublist in w for item in sublist])
                flat_grads.extend(b)
        return flat_grads

    def backward_softmax_cross_entropy(self, x, y_idx, logits):
        """
        Compute gradients for softmax + cross-entropy loss.

        Args:
            x: input batch (numpy) or single sample (python list)
            y_idx: class indices (numpy array shape [m] or list[int] or int)
            logits: raw output of forward() (before softmax)

        Returns:
            Flattened gradients for all weights and biases.
        """
        activations, zs = self.saved_ctx
        num_layers = len(self.layer_sizes)

        d_weights = [None] * (num_layers - 1)
        d_biases = [None] * (num_layers - 1)

        if HAS_NUMPY:
            if not isinstance(logits, np.ndarray):
                logits = np.array(logits, dtype=np.float32)
            m = int(logits.shape[0]) if logits.ndim == 2 else 1
            if logits.ndim == 1:
                logits = logits.reshape(1, -1)

            y_arr = y_idx
            if not isinstance(y_arr, np.ndarray):
                y_arr = np.array(y_arr, dtype=np.int64)
            y_arr = y_arr.reshape(-1).astype(np.int64)
            if y_arr.shape[0] != logits.shape[0]:
                y_arr = np.resize(y_arr, (logits.shape[0],))

            probs = softmax(logits)
            delta = probs.copy()
            delta[np.arange(logits.shape[0]), y_arr] -= 1.0
            delta /= float(logits.shape[0])

            for L in reversed(range(num_layers - 1)):
                d_biases[L] = np.sum(delta, axis=0)
                d_weights[L] = np.dot(activations[L].T, delta)
                if L > 0:
                    d_activation = np.dot(delta, self.weights[L].T)
                    delta = d_activation * (zs[L-1] > 0)
        else:
            # Pure python (single sample only)
            if isinstance(y_idx, list):
                y_idx = int(y_idx[0]) if y_idx else 0
            y_idx = int(y_idx)

            probs = softmax(logits)
            delta = [float(p) for p in probs]
            if 0 <= y_idx < len(delta):
                delta[y_idx] -= 1.0

            for L in reversed(range(num_layers - 1)):
                d_biases[L] = delta
                d_weights[L] = [[0.0] * len(self.weights[L][0]) for _ in range(len(self.weights[L]))]
                for r in range(len(activations[L])):
                    for c in range(len(delta)):
                        d_weights[L][r][c] = activations[L][r] * delta[c]

                if L > 0:
                    d_activation = [0.0] * len(self.biases[L-1])
                    for r in range(len(delta)):
                        for c in range(len(self.biases[L-1])):
                            d_activation[c] += delta[r] * self.weights[L][c][r]
                    delta = [d_activation[i] if zs[L-1][i] > 0 else 0.0 for i in range(len(zs[L-1]))]

        flat_grads = []
        for w, b in zip(d_weights, d_biases):
            if HAS_NUMPY:
                flat_grads.extend(w.ravel())
                flat_grads.extend(b.ravel())
            else:
                flat_grads.extend([item for sublist in w for item in sublist])
                flat_grads.extend(b)
        return flat_grads


# ============================================================
# TRANSFORMER EXPERT (drop-in replacement for SimpleMLP)
# ============================================================

def _layernorm_forward(x, gamma, beta, eps=1e-5):
    """Layer normalization forward pass.
    x: [..., d_model], gamma/beta: [d_model]
    Returns: (out, mean, inv_std) for backward.
    """
    mean = np.mean(x, axis=-1, keepdims=True)
    var = np.var(x, axis=-1, keepdims=True)
    inv_std = 1.0 / np.sqrt(var + eps)
    x_hat = (x - mean) * inv_std
    out = gamma * x_hat + beta
    return out, x_hat, mean, inv_std


def _layernorm_backward(d_out, x_hat, gamma, inv_std):
    """Layer normalization backward pass.
    Returns: (d_x, d_gamma, d_beta).
    """
    N = d_out.shape[-1]
    d_gamma = np.sum(d_out * x_hat, axis=tuple(range(d_out.ndim - 1)))
    d_beta = np.sum(d_out, axis=tuple(range(d_out.ndim - 1)))
    dx_hat = d_out * gamma
    d_x = inv_std * (dx_hat - np.mean(dx_hat, axis=-1, keepdims=True)
                      - x_hat * np.mean(dx_hat * x_hat, axis=-1, keepdims=True))
    return d_x, d_gamma, d_beta


def _psc_matmul(A, B, max_norm=1.0):
    """Per-sample clipped matmul: sum of individually norm-clipped outer products.
    A: [N, d1], B: [N, d2] -> returns [d1, d2]
    Each sample's outer product A[i] @ B[i].T is clipped to max_norm before summing.
    """
    a_norms = np.linalg.norm(A, axis=1, keepdims=True)  # [N, 1]
    b_norms = np.linalg.norm(B, axis=1, keepdims=True)  # [N, 1]
    outer_norms = a_norms * b_norms  # [N, 1] - norm of each outer product
    clip = np.minimum(1.0, max_norm / (outer_norms + 1e-8))  # [N, 1]
    return (np.dot((A * clip).T, B) / np.sqrt(A.shape[0])).astype(np.float32)  # [d1, d2] sqrt(N) normalized, keep float32


class SimpleTransformer:
    """
    Transformer for bit-level next-token prediction.
    Drop-in replacement for SimpleMLP — same interface:
    forward(), backward_softmax_cross_entropy(), get_weights_flat(),
    set_weights_flat(), get_param_count().

    Numpy-only. For pure-python fallback, ExpertWorker uses SimpleMLP.
    """

    MINI_BATCH = 8  # internal chunk size - kept small for volunteer systems with limited RAM
    GPU_MINI_BATCH = 128  # larger chunks for GPU acceleration

    def __init__(self, seq_len, d_model, n_heads, d_ff, n_layers,
                 vocab_size=256, output_size=256, seed=None):
        if seed is not None:
            # Use numpy RNG (not CuPy) for deterministic init across GPU/CPU
            _rng = _real_numpy if _real_numpy is not None else np
            _rng.random.seed(seed)

        self.seq_len = seq_len
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads
        self.d_ff = d_ff
        self.n_layers = n_layers
        self.vocab_size = vocab_size
        self.output_size = output_size

        # Build causal mask: upper-triangular = -inf
        self.causal_mask = np.triu(
            np.full((seq_len, seq_len), -1e9, dtype=np.float32), k=1
        )

        self._init_weights()

    # ------------------------------------------------------------------
    # Weight initialization
    # ------------------------------------------------------------------
    def _init_weights(self):
        d = self.d_model
        S = self.seq_len
        V = self.vocab_size
        O = self.output_size
        h = self.d_ff

        # Helper: generate random values with numpy RNG (deterministic across GPU/CPU)
        _rng = _real_numpy if _real_numpy is not None else np
        def _randn(*shape):
            return np.asarray(_rng.random.randn(*shape).astype(_rng.float32))

        scale_e = 0.02
        scale_proj = 1.0 / math.sqrt(d)
        scale_ff1 = math.sqrt(2.0 / d)
        scale_ff2 = math.sqrt(2.0 / h) / math.sqrt(self.n_layers)

        self.tok_embed = _randn(V, d).astype(np.float32) * scale_e
        self.pos_embed = _randn(S, d).astype(np.float32) * scale_e

        self.layers = []
        for _ in range(self.n_layers):
            layer = {
                'ln1_g': np.ones(d, dtype=np.float32),
                'ln1_b': np.zeros(d, dtype=np.float32),
                'Wq': _randn(d, d).astype(np.float32) * scale_proj,
                'bq': np.zeros(d, dtype=np.float32),
                'Wk': _randn(d, d).astype(np.float32) * scale_proj,
                'bk': np.zeros(d, dtype=np.float32),
                'Wv': _randn(d, d).astype(np.float32) * scale_proj,
                'bv': np.zeros(d, dtype=np.float32),
                'Wo': _randn(d, d).astype(np.float32) * scale_proj,
                'bo': np.zeros(d, dtype=np.float32),
                'ln2_g': np.ones(d, dtype=np.float32),
                'ln2_b': np.zeros(d, dtype=np.float32),
                'W1': _randn(d, h).astype(np.float32) * scale_ff1,
                'b1': np.zeros(h, dtype=np.float32),
                'W2': _randn(h, d).astype(np.float32) * scale_ff2,
                'b2': np.zeros(d, dtype=np.float32),
                # Local prediction head for supervised layer-wise training
                'local_W': _randn(d, O).astype(np.float32) * 0.02,
                'local_b': np.zeros(O, dtype=np.float32),
            }
            self.layers.append(layer)

        self.final_ln_g = np.ones(d, dtype=np.float32)
        self.final_ln_b = np.zeros(d, dtype=np.float32)
        self.out_W = _randn(d, O).astype(np.float32) * 0.02
        self.out_b = np.zeros(O, dtype=np.float32)

    # ------------------------------------------------------------------
    # Param count
    # ------------------------------------------------------------------
    def get_param_count(self):
        d, S, V, O, h = self.d_model, self.seq_len, self.vocab_size, self.output_size, self.d_ff
        per_layer = 2*d + 4*(d*d + d) + 2*d + d*h + h + h*d + d  # attn + ffn + layernorms
        return V*d + S*d + self.n_layers * per_layer + 2*d + d*O + O

    # ------------------------------------------------------------------
    # Weight serialization (canonical order)
    # ------------------------------------------------------------------
    def get_weights_flat(self):
        parts = []
        parts.append(self.tok_embed.ravel())
        parts.append(self.pos_embed.ravel())
        for L in self.layers:
            for key in ('ln1_g', 'ln1_b', 'Wq', 'bq', 'Wk', 'bk',
                        'Wv', 'bv', 'Wo', 'bo', 'ln2_g', 'ln2_b',
                        'W1', 'b1', 'W2', 'b2'):
                parts.append(L[key].ravel())
        parts.append(self.final_ln_g.ravel())
        parts.append(self.final_ln_b.ravel())
        parts.append(self.out_W.ravel())
        parts.append(self.out_b.ravel())
        return to_cpu(np.concatenate(parts))  # Always return CPU numpy for serialization

    def set_weights_flat(self, flat_weights):
        arr = np.array(flat_weights, dtype=np.float32)
        idx = 0
        d = self.d_model
        S = self.seq_len
        V = self.vocab_size
        O = self.output_size
        h = self.d_ff

        def take(shape):
            nonlocal idx
            size = 1
            for s in shape:
                size *= s
            chunk = arr[idx:idx + size].reshape(shape)
            idx += size
            return chunk

        self.tok_embed = take((V, d))
        self.pos_embed = take((S, d))
        for L in self.layers:
            L['ln1_g'] = take((d,))
            L['ln1_b'] = take((d,))
            L['Wq'] = take((d, d))
            L['bq'] = take((d,))
            L['Wk'] = take((d, d))
            L['bk'] = take((d,))
            L['Wv'] = take((d, d))
            L['bv'] = take((d,))
            L['Wo'] = take((d, d))
            L['bo'] = take((d,))
            L['ln2_g'] = take((d,))
            L['ln2_b'] = take((d,))
            L['W1'] = take((d, h))
            L['b1'] = take((h,))
            L['W2'] = take((h, d))
            L['b2'] = take((d,))
        self.final_ln_g = take((d,))
        self.final_ln_b = take((d,))
        self.out_W = take((d, O))
        self.out_b = take((O,))

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------
    def forward(self, x):
        """Forward pass. x: [batch, seq_len] or [seq_len] of bit values (0/1 floats).
        Returns logits [batch, output_size] or [output_size]."""
        single = False
        if not isinstance(x, np.ndarray):
            x = np.array(x, dtype=np.float32)
        if x.ndim == 1:
            x = x.reshape(1, -1)
            single = True

        B = x.shape[0]
        # Mini-batch to keep attention matrices manageable
        _batch = self.GPU_MINI_BATCH if HAS_GPU else self.MINI_BATCH
        if B > _batch:
            chunks = [x[i:i + _batch] for i in range(0, B, _batch)]
            logits_parts = []
            ctx_parts = []
            for chunk in chunks:
                logits_c, ctx_c = self._forward_chunk(chunk)
                logits_parts.append(logits_c)
                ctx_parts.append(ctx_c)
            logits = np.concatenate(logits_parts, axis=0)
            self.saved_ctx = {'chunked': True, 'ctx_parts': ctx_parts,
                              'chunk_sizes': [c.shape[0] for c in chunks],
                              'single': single, 'x_full': x}
        else:
            logits, ctx = self._forward_chunk(x)
            self.saved_ctx = {'chunked': False, 'ctx': ctx, 'single': single, 'x_full': x}

        if single:
            return logits[0]
        return logits

    def _forward_chunk(self, x):
        """Forward one mini-batch chunk. x: [B, S] float.
        Returns (logits [B, O], saved_context dict)."""
        B, S = x.shape
        d = self.d_model
        nh = self.n_heads
        dh = self.d_head

        # Token + position embedding
        x_int = x.astype(np.int32)
        h = self.tok_embed[x_int] + self.pos_embed[np.newaxis, :S, :]  # [B, S, d]

        layer_ctx = []
        for li, L in enumerate(self.layers):
            ctx = {}
            ctx['h_in'] = h

            # Pre-norm attention block
            ln1_out, ln1_xhat, ln1_mean, ln1_inv = _layernorm_forward(h, L['ln1_g'], L['ln1_b'])
            ctx['ln1_xhat'] = ln1_xhat
            ctx['ln1_inv'] = ln1_inv

            # Q, K, V projections
            Q = ln1_out @ L['Wq'] + L['bq']  # [B, S, d]
            K = ln1_out @ L['Wk'] + L['bk']
            V = ln1_out @ L['Wv'] + L['bv']
            ctx['ln1_out'] = ln1_out

            # Reshape to multi-head: [B, nh, S, dh]
            Q = Q.reshape(B, S, nh, dh).transpose(0, 2, 1, 3)
            K = K.reshape(B, S, nh, dh).transpose(0, 2, 1, 3)
            V = V.reshape(B, S, nh, dh).transpose(0, 2, 1, 3)
            ctx['Q'] = Q
            ctx['K'] = K
            ctx['V'] = V

            # Scaled dot-product attention with causal mask
            scores = (Q @ K.transpose(0, 1, 3, 2)) / math.sqrt(dh)  # [B, nh, S, S]
            scores = scores + self.causal_mask[np.newaxis, np.newaxis, :S, :S]
            # Stable softmax
            scores_max = scores.max(axis=-1, keepdims=True)
            exp_scores = np.exp(scores - scores_max)
            A = exp_scores / (exp_scores.sum(axis=-1, keepdims=True) + 1e-12)
            ctx['A'] = A

            attn = A @ V  # [B, nh, S, dh]
            # Merge heads: [B, S, d]
            merged = attn.transpose(0, 2, 1, 3).reshape(B, S, d)
            attn_out = merged @ L['Wo'] + L['bo']
            ctx['merged'] = merged

            # Residual
            h = h + attn_out
            ctx['h_post_attn'] = h

            # Pre-norm FFN block
            ln2_out, ln2_xhat, ln2_mean, ln2_inv = _layernorm_forward(h, L['ln2_g'], L['ln2_b'])
            ctx['ln2_xhat'] = ln2_xhat
            ctx['ln2_inv'] = ln2_inv
            ctx['ln2_out'] = ln2_out

            # FFN: ReLU activation
            z1 = ln2_out @ L['W1'] + L['b1']  # [B, S, d_ff]
            a1 = np.maximum(0, z1)
            z2 = a1 @ L['W2'] + L['b2']  # [B, S, d]
            ctx['z1'] = z1
            ctx['a1'] = a1

            # Residual
            h = h + z2
            ctx['h_layer_out'] = h  # Save for local loss

            layer_ctx.append(ctx)

        # Final layer norm
        fn_out, fn_xhat, fn_mean, fn_inv = _layernorm_forward(h, self.final_ln_g, self.final_ln_b)

        # Last-token extraction
        pooled = fn_out[:, -1, :]  # [B, d]

        # Output head
        logits = pooled @ self.out_W + self.out_b  # [B, O]

        saved = {
            'x_int': x_int,
            'layer_ctx': layer_ctx,
            'h_pre_final_ln': h,
            'fn_xhat': fn_xhat,
            'fn_inv': fn_inv,
            'fn_out': fn_out,
            'pooled': pooled,
        }
        return logits, saved

    # ------------------------------------------------------------------
    # Backward pass (manual, no autograd)
    # ------------------------------------------------------------------
    def backward_softmax_cross_entropy(self, x, y_idx, logits):
        """Compute gradients via softmax cross-entropy loss.
        Returns flat gradient list matching get_weights_flat() order."""
        if not isinstance(x, np.ndarray):
            x = np.array(x, dtype=np.float32)
        if x.ndim == 1:
            x = x.reshape(1, -1)

        if not isinstance(logits, np.ndarray):
            logits = np.array(logits, dtype=np.float32)
        if logits.ndim == 1:
            logits = logits.reshape(1, -1)

        if not isinstance(y_idx, np.ndarray):
            y_idx = np.array(y_idx, dtype=np.int64)
        y_idx = y_idx.reshape(-1)

        B_total = x.shape[0]
        sc = self.saved_ctx

        # Accumulate gradients across mini-batch chunks
        grads = self._zero_grads()

        if sc['chunked']:
            offset = 0
            for ci, ctx in enumerate(sc['ctx_parts']):
                cb = sc['chunk_sizes'][ci]
                x_chunk = x[offset:offset + cb]
                y_chunk = y_idx[offset:offset + cb]
                logits_chunk = logits[offset:offset + cb]
                self._backward_chunk(x_chunk, y_chunk, logits_chunk, ctx, grads, B_total)
                offset += cb
        else:
            self._backward_chunk(x, y_idx, logits, sc['ctx'], grads, B_total)

        return self._flatten_grads(grads)

    def _zero_grads(self):
        """Initialize gradient accumulators matching parameter structure."""
        d = self.d_model
        S = self.seq_len
        V = self.vocab_size
        O = self.output_size
        h = self.d_ff
        g = {
            'd_tok_embed': np.zeros((V, d), dtype=np.float32),
            'd_pos_embed': np.zeros((S, d), dtype=np.float32),
            'layers': [],
            'd_final_ln_g': np.zeros(d, dtype=np.float32),
            'd_final_ln_b': np.zeros(d, dtype=np.float32),
            'd_out_W': np.zeros((d, O), dtype=np.float32),
            'd_out_b': np.zeros(O, dtype=np.float32),
        }
        for _ in range(self.n_layers):
            lg = {}
            for key in ('ln1_g', 'ln1_b', 'Wq', 'bq', 'Wk', 'bk',
                        'Wv', 'bv', 'Wo', 'bo', 'ln2_g', 'ln2_b',
                        'W1', 'b1', 'W2', 'b2'):
                param = self.layers[0][key]
                lg['d_' + key] = np.zeros_like(param)
            g['layers'].append(lg)
        return g

    def _backward_chunk(self, x_chunk, y_chunk, logits_chunk, ctx, grads, B_total):
        """Backward through one mini-batch chunk, accumulating into grads."""
        B = x_chunk.shape[0]
        S = self.seq_len
        d = self.d_model
        nh = self.n_heads
        dh = self.d_head

        # Softmax cross-entropy: dL/dlogits
        probs = softmax(logits_chunk)
        if not isinstance(probs, np.ndarray):
            probs = np.array(probs, dtype=np.float32)
        d_logits = probs.copy()
        d_logits[np.arange(B), y_chunk] -= 1.0
        d_logits /= B_total  # normalize by TOTAL batch, not chunk

        # Output head backward: logits = pooled @ out_W + out_b
        pooled = ctx['pooled']
        grads['d_out_W'] += pooled.T @ d_logits
        grads['d_out_b'] += d_logits.sum(axis=0)
        d_pooled = d_logits @ self.out_W.T  # [B, d]

        # Last-token backward
        d_fn_out = np.zeros((B, S, d), dtype=np.float32)
        d_fn_out[:, -1, :] = d_pooled

        # Final LayerNorm backward
        d_h, d_fg, d_fb = _layernorm_backward(
            d_fn_out, ctx['fn_xhat'], self.final_ln_g, ctx['fn_inv'])
        grads['d_final_ln_g'] += d_fg
        grads['d_final_ln_b'] += d_fb

        # Backward through layers (reverse)
        for li in range(self.n_layers - 1, -1, -1):
            L = self.layers[li]
            lc = ctx['layer_ctx'][li]
            lg = grads['layers'][li]

            # --- FFN block backward ---
            # Residual: h = h_post_attn + z2, so d_h flows to both
            d_z2 = d_h  # gradient through residual
            d_h_post_attn = d_h.copy()

            # z2 = a1 @ W2 + b2
            lg['d_W2'] += lc['a1'].reshape(-1, self.d_ff).T @ d_z2.reshape(-1, d)
            lg['d_b2'] += d_z2.sum(axis=(0, 1))
            d_a1 = d_z2 @ L['W2'].T  # [B, S, d_ff]

            # ReLU backward
            d_z1 = d_a1 * (lc['z1'] > 0)

            # z1 = ln2_out @ W1 + b1
            lg['d_W1'] += lc['ln2_out'].reshape(-1, d).T @ d_z1.reshape(-1, self.d_ff)
            lg['d_b1'] += d_z1.sum(axis=(0, 1))
            d_ln2_out = d_z1 @ L['W1'].T  # [B, S, d]

            # LN2 backward
            d_h_pre_ln2, d_lg2, d_lb2 = _layernorm_backward(
                d_ln2_out, lc['ln2_xhat'], L['ln2_g'], lc['ln2_inv'])
            lg['d_ln2_g'] += d_lg2
            lg['d_ln2_b'] += d_lb2

            # Through residual: d_h = d_h_post_attn + d_h_pre_ln2
            d_h = d_h_post_attn + d_h_pre_ln2

            # --- Attention block backward ---
            d_attn_out = d_h  # gradient through residual
            d_h_in = d_h.copy()

            # attn_out = merged @ Wo + bo
            merged = lc['merged']  # [B, S, d]
            lg['d_Wo'] += merged.reshape(-1, d).T @ d_attn_out.reshape(-1, d)
            lg['d_bo'] += d_attn_out.sum(axis=(0, 1))
            d_merged = d_attn_out @ L['Wo'].T  # [B, S, d]

            # Unmerge heads: [B, S, d] -> [B, nh, S, dh]
            d_attn = d_merged.reshape(B, S, nh, dh).transpose(0, 2, 1, 3)

            # attn = A @ V -> d_A, d_V
            Q = lc['Q']
            K = lc['K']
            V = lc['V']
            A = lc['A']

            d_V = A.transpose(0, 1, 3, 2) @ d_attn  # [B, nh, S, dh]
            d_A = d_attn @ V.transpose(0, 1, 3, 2)   # [B, nh, S, S]

            # Softmax backward: d_scores = A * (d_A - sum(d_A * A, -1, keepdim))
            d_scores = A * (d_A - (d_A * A).sum(axis=-1, keepdims=True))

            # Scale backward
            d_scores = d_scores / math.sqrt(dh)

            # QK^T backward
            d_Q = d_scores @ K                           # [B, nh, S, dh]
            d_K = d_scores.transpose(0, 1, 3, 2) @ Q    # [B, nh, S, dh]

            # Merge heads back: [B, nh, S, dh] -> [B, S, d]
            d_Q = d_Q.transpose(0, 2, 1, 3).reshape(B, S, d)
            d_K = d_K.transpose(0, 2, 1, 3).reshape(B, S, d)
            d_V = d_V.transpose(0, 2, 1, 3).reshape(B, S, d)

            # Q/K/V projection backward
            ln1_out = lc['ln1_out']
            ln1_flat = ln1_out.reshape(-1, d)

            lg['d_Wq'] += ln1_flat.T @ d_Q.reshape(-1, d)
            lg['d_bq'] += d_Q.sum(axis=(0, 1))
            lg['d_Wk'] += ln1_flat.T @ d_K.reshape(-1, d)
            lg['d_bk'] += d_K.sum(axis=(0, 1))
            lg['d_Wv'] += ln1_flat.T @ d_V.reshape(-1, d)
            lg['d_bv'] += d_V.sum(axis=(0, 1))

            d_ln1_out = (d_Q @ L['Wq'].T + d_K @ L['Wk'].T + d_V @ L['Wv'].T)

            # LN1 backward
            d_h_pre_ln1, d_lg1, d_lb1 = _layernorm_backward(
                d_ln1_out, lc['ln1_xhat'], L['ln1_g'], lc['ln1_inv'])
            lg['d_ln1_g'] += d_lg1
            lg['d_ln1_b'] += d_lb1

            # Through residual
            d_h = d_h_in + d_h_pre_ln1

        # Embedding backward
        x_int = ctx['x_int']  # [B, S]
        # Position embedding: broadcast added, so sum over batch
        grads['d_pos_embed'] += d_h.sum(axis=0)  # [S, d]
        # Token embedding: sparse scatter
        np.add.at(grads['d_tok_embed'], x_int, d_h)

    def _flatten_grads(self, grads):
        """Flatten gradient dict into list matching get_weights_flat() order."""
        parts = []
        parts.append(grads['d_tok_embed'].ravel())
        parts.append(grads['d_pos_embed'].ravel())
        for lg in grads['layers']:
            for key in ('ln1_g', 'ln1_b', 'Wq', 'bq', 'Wk', 'bk',
                        'Wv', 'bv', 'Wo', 'bo', 'ln2_g', 'ln2_b',
                        'W1', 'b1', 'W2', 'b2'):
                parts.append(lg['d_' + key].ravel())
        parts.append(grads['d_final_ln_g'].ravel())
        parts.append(grads['d_final_ln_b'].ravel())
        parts.append(grads['d_out_W'].ravel())
        parts.append(grads['d_out_b'].ravel())
        return np.concatenate(parts)


    def sgd_step(self, x, y_idx, logits, lr=0.001, max_grad_norm=1.0):
        """One SGD step: compute backprop gradients, clip, apply in-place."""
        if not isinstance(x, np.ndarray):
            x = np.array(x, dtype=np.float32)
        if x.ndim == 1:
            x = x.reshape(1, -1)
        if not isinstance(logits, np.ndarray):
            logits = np.array(logits, dtype=np.float32)
        if logits.ndim == 1:
            logits = logits.reshape(1, -1)
        if not isinstance(y_idx, np.ndarray):
            y_idx = np.array(y_idx, dtype=np.int64)
        y_idx = y_idx.reshape(-1)

        B_total = x.shape[0]
        sc = self.saved_ctx

        grads = self._zero_grads()
        if sc['chunked']:
            offset = 0
            for ci, ctx in enumerate(sc['ctx_parts']):
                cb = sc['chunk_sizes'][ci]
                self._backward_chunk(x[offset:offset+cb], y_idx[offset:offset+cb],
                                     logits[offset:offset+cb], ctx, grads, B_total)
                offset += cb
        else:
            self._backward_chunk(x, y_idx, logits, sc['ctx'], grads, B_total)

        # Compute gradient norm for clipping
        sum_sq = np.float32(0.0)
        all_grad_parts = [grads['d_tok_embed'], grads['d_pos_embed']]
        for lg in grads['layers']:
            for key in ('ln1_g', 'ln1_b', 'Wq', 'bq', 'Wk', 'bk',
                        'Wv', 'bv', 'Wo', 'bo', 'ln2_g', 'ln2_b',
                        'W1', 'b1', 'W2', 'b2'):
                all_grad_parts.append(lg['d_' + key])
        all_grad_parts.extend([grads['d_final_ln_g'], grads['d_final_ln_b'],
                               grads['d_out_W'], grads['d_out_b']])

        for g in all_grad_parts:
            sum_sq = sum_sq + np.sum(g * g)
        grad_norm = float(np.sqrt(sum_sq))

        clip_coeff = np.float32(min(1.0, max_grad_norm / (grad_norm + 1e-8)))
        lr_f = np.float32(lr) * clip_coeff

        # Apply gradients in-place (stays on same device)
        self.tok_embed -= lr_f * grads['d_tok_embed']
        self.pos_embed -= lr_f * grads['d_pos_embed']
        for li in range(self.n_layers):
            L = self.layers[li]
            lg = grads['layers'][li]
            for key in ('ln1_g', 'ln1_b', 'Wq', 'bq', 'Wk', 'bk',
                        'Wv', 'bv', 'Wo', 'bo', 'ln2_g', 'ln2_b',
                        'W1', 'b1', 'W2', 'b2'):
                L[key] -= lr_f * lg['d_' + key]
        self.final_ln_g -= lr_f * grads['d_final_ln_g']
        self.final_ln_b -= lr_f * grads['d_final_ln_b']
        self.out_W -= lr_f * grads['d_out_W']
        self.out_b -= lr_f * grads['d_out_b']

        self.saved_ctx = None  # Free activation memory

    def hebbian_update(self, lr=0.0001, decay=0.9999, target=None):
        """
        Hebbian learning for Transformer: "neurons that fire together, wire together"

        For attention layers, we use a modified Hebbian rule:
        - FFN layers: standard Hebbian (pre * post)
        - Attention: strengthen connections that co-activate

        Much more memory efficient than backprop - no need to store gradients.
        """
        if not hasattr(self, 'saved_ctx') or self.saved_ctx is None:
            return

        sc = self.saved_ctx
        self.saved_ctx = None  # Clear immediately to free memory

        if sc.get('chunked'):
            # Process each chunk and clear as we go
            chunk_sizes = sc.get('chunk_sizes', [])
            offset = 0
            for i, ctx in enumerate(sc['ctx_parts']):
                t_chunk = None
                if target is not None and i < len(chunk_sizes):
                    t_chunk = target[offset:offset + chunk_sizes[i]]
                    offset += chunk_sizes[i]
                self._hebbian_update_chunk(ctx, lr, decay, target=t_chunk)
                sc['ctx_parts'][i] = None  # Clear each chunk after processing
        else:
            self._hebbian_update_chunk(sc['ctx'], lr, decay, target=target)

        # Explicitly clear all references
        sc.clear()
        del sc

    def _hebbian_update_chunk(self, ctx, lr, decay, target=None):
        """Apply Hebbian update for one forward pass chunk."""
        d = self.d_model

        for li, L in enumerate(self.layers):
            lc = ctx['layer_ctx'][li]

            # FFN Hebbian: W1 and W2
            # post = relu(pre @ W1), so strengthen W1 where both fire
            ln2_out = lc['ln2_out']  # pre for FFN
            a1 = lc['a1']            # post for W1 (after ReLU)

            # W1 update: Δw = pre.T @ post (averaged over batch and seq)
            pre_flat = ln2_out.reshape(-1, d)
            post_flat = a1.reshape(-1, self.d_ff)

            # Normalized Hebbian
            delta_W1 = np.dot(pre_flat.T, post_flat) / pre_flat.shape[0]
            norm = float(np.linalg.norm(delta_W1))
            if norm > 1.0:
                delta_W1 = (delta_W1 / norm).astype(np.float32)
            L['W1'] = decay * L['W1'] + lr * 0.1 * delta_W1  # body: 0.1x lr
            L['b1'] = decay * L['b1'] + lr * 0.1 * np.mean(post_flat, axis=0)  # body: 0.1x lr

            # W2 update: a1 is pre, h_post_attn (residual output) approximates post
            h_post = lc['h_post_attn'].reshape(-1, d)
            delta_W2 = np.dot(post_flat.T, h_post) / post_flat.shape[0]
            norm = float(np.linalg.norm(delta_W2))
            if norm > 1.0:
                delta_W2 = (delta_W2 / norm).astype(np.float32)
            L['W2'] = decay * L['W2'] + lr * 0.1 * delta_W2  # body: 0.1x lr
            L['b2'] = decay * L['b2'] + lr * 0.1 * np.mean(h_post, axis=0)  # body: 0.1x lr

            # Attention Hebbian: strengthen Q/K/V based on attention patterns
            # Where attention is high, those Q-K pairs should be strengthened
            A = lc['A']  # [B, nh, S, S] attention weights
            Q = lc['Q']  # [B, nh, S, dh]
            K = lc['K']
            V = lc['V']

            # Simplified: update Wq, Wk, Wv based on attention-weighted activations
            ln1_out = lc['ln1_out'].reshape(-1, d)

            # For Wq: strengthen where queries attend strongly
            q_importance = np.mean(A, axis=-1)  # [B, nh, S] - how much each query attends
            q_flat = Q.transpose(0, 2, 1, 3).reshape(-1, d)  # [B*S, d]
            delta_Wq = _psc_matmul(ln1_out, q_flat)
            L['Wq'] = decay * L['Wq'] + lr * 0.1 * delta_Wq  # Smaller LR for attention

            # For Wk/Wv: strengthen where keys/values are attended to
            k_flat = K.transpose(0, 2, 1, 3).reshape(-1, d)
            delta_Wk = _psc_matmul(ln1_out, k_flat)
            L['Wk'] = decay * L['Wk'] + lr * 0.1 * delta_Wk

            v_flat = V.transpose(0, 2, 1, 3).reshape(-1, d)
            delta_Wv = _psc_matmul(ln1_out, v_flat)
            L['Wv'] = decay * L['Wv'] + lr * 0.1 * delta_Wv

            # --- Local loss: error-modulated Hebbian for this layer ---
            if target is not None and 'h_layer_out' in lc and 'local_W' in L:
                h_layer_out = lc['h_layer_out']  # [B, S, d]
                pooled_l = h_layer_out[:, -1, :]   # [B, d] last-token pool

                n_l = pooled_l.shape[0]
                if not isinstance(target, np.ndarray):
                    target = np.array(target)
                local_post = np.zeros((n_l, self.output_size), dtype=np.float32)
                t_int = target.astype(int).ravel()
                n_t = min(len(t_int), n_l)
                local_post[np.arange(n_t), t_int[:n_t]] = 1.0

                # Local error = target - layer's prediction
                local_logits = pooled_l @ L['local_W'] + L['local_b']
                local_predicted = softmax(local_logits)
                if not isinstance(local_predicted, np.ndarray):
                    local_predicted = np.array(local_predicted, dtype=np.float32)
                local_error = local_post - local_predicted

                # Update local head with error signal
                delta_local_W = _psc_matmul(pooled_l, local_error)
                L['local_W'] = decay * L['local_W'] + lr * 10 * delta_local_W  # head: 10x lr
                L['local_b'] = decay * L['local_b'] + lr * np.mean(local_error, axis=0)

                # Supervised modulation on FFN W2 (error-driven)
                supervised_post = np.dot(local_error, L['local_W'].T)  # [B, d]
                a1_last = a1[:, -1, :]  # [B, d_ff]
                delta_W2_sup = _psc_matmul(a1_last, supervised_post)
                L['W2'] += lr * 0.1 * delta_W2_sup

        # Output head: error-modulated Hebbian (delta rule)
        pooled = ctx['pooled']  # [B, d]
        if target is not None:
            if not isinstance(target, np.ndarray):
                target = np.array(target)
            post = np.zeros((pooled.shape[0], self.output_size), dtype=np.float32)
            target_int = target.astype(int).ravel()
            n = min(len(target_int), pooled.shape[0])
            post[np.arange(n), target_int[:n]] = 1.0
            # Error = target - model's current prediction
            logits = pooled @ self.out_W + self.out_b
            predicted = softmax(logits)
            if not isinstance(predicted, np.ndarray):
                predicted = np.array(predicted, dtype=np.float32)
            error = post - predicted
        else:
            logits = pooled @ self.out_W + self.out_b
            error = softmax(logits)
            if not isinstance(error, np.ndarray):
                error = np.array(error)

        delta_out_W = _psc_matmul(pooled, error)
        self.out_W = decay * self.out_W + lr * 10 * delta_out_W  # head: 10x lr
        self.out_b = decay * self.out_b + lr * np.mean(error, axis=0)
        # Clear ctx to free memory
        ctx.clear()

    def get_weight_delta(self, reference_weights):
        """Get difference between current weights and reference."""
        current = to_cpu(self.get_weights_flat())  # Ensure CPU numpy
        _np = _real_numpy if _real_numpy is not None else np
        reference_weights = to_cpu(reference_weights)
        if not isinstance(reference_weights, _np.ndarray):
            reference_weights = _np.array(reference_weights, dtype=_np.float32)
        return current - reference_weights  # Returns CPU numpy array

    def apply_weight_delta(self, delta, alpha=0.5):
        """Apply a weight delta from peer (gossip averaging)."""
        current = to_cpu(self.get_weights_flat())  # Ensure CPU numpy
        _np = _real_numpy if _real_numpy is not None else np
        delta = to_cpu(delta)
        if not isinstance(delta, _np.ndarray):
            delta = _np.array(delta, dtype=_np.float32)
        self.set_weights_flat(current + alpha * delta)


# ============================================================
# MIXTURE OF EXPERTS (MoE) with Auto-Scaling
# ============================================================

class MoEConfig:
    """Configuration for Mixture of Experts model.

    v2.0 Scale (18B params total):
    - 420 experts × ~42.6M params each
    - Transformer architecture: d_model=768, n_heads=12, n_layers=6, d_ff=3072
    - Model file size: ~170MB per expert
    - 30 batches per task for 15+ minute work units
    """
    def __init__(
        self,
        input_size=64,           # Input dimension (context window in bits)
        output_size=256,         # Output classes (byte-level: 0-255)
        num_experts=420,         # v2.0: 420 experts for ~18B params total
        expert_hidden=3072,      # FFN hidden size (d_ff) per expert
        expert_layers=6,         # Transformer layers per expert
        max_experts=1000,        # Maximum experts when scaling
        max_expert_hidden=4096,  # Maximum FFN hidden size
        max_expert_layers=12,    # Room to grow
        expert_type="transformer",  # v2.0: transformer experts for better learning
        d_model=768,             # Transformer: embedding dimension
        n_heads=12,              # Transformer: number of attention heads
    ):
        self.input_size = input_size
        self.output_size = output_size
        self.num_experts = num_experts
        self.expert_hidden = expert_hidden
        self.expert_layers = expert_layers
        self.max_experts = max_experts
        self.max_expert_hidden = max_expert_hidden
        self.max_expert_layers = max_expert_layers
        self.expert_type = expert_type
        self.d_model = d_model
        self.n_heads = n_heads

    def to_dict(self):
        return {
            "input_size": self.input_size,
            "output_size": self.output_size,
            "num_experts": self.num_experts,
            "expert_hidden": self.expert_hidden,
            "expert_layers": self.expert_layers,
            "max_experts": self.max_experts,
            "max_expert_hidden": self.max_expert_hidden,
            "max_expert_layers": self.max_expert_layers,
            "expert_type": self.expert_type,
            "d_model": self.d_model,
            "n_heads": self.n_heads,
        }

    @classmethod
    def from_dict(cls, d):
        import inspect
        valid_keys = set(inspect.signature(cls.__init__).parameters.keys()) - {'self'}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)

    def get_expert_arch(self):
        """Return layer sizes for a single expert."""
        if self.expert_layers == 2:
            return [self.input_size, self.expert_hidden, self.output_size]
        else:
            # Multiple hidden layers, all same size
            layers = [self.input_size]
            for _ in range(self.expert_layers - 1):
                layers.append(self.expert_hidden)
            layers.append(self.output_size)
            return layers

    def get_router_arch(self):
        """Return layer sizes for router network."""
        # Router: input -> small hidden -> num_experts (softmax over experts)
        return [self.input_size, min(64, self.expert_hidden), self.num_experts]

    def count_expert_params(self):
        """Count parameters in a single expert."""
        if self.expert_type == "transformer":
            d = self.d_model
            S = self.input_size  # seq_len = input_size
            V = 256   # vocab_size (byte-level)
            O = self.output_size
            h = self.expert_hidden
            nL = self.expert_layers
            # tok_embed + pos_embed
            p = V * d + S * d
            # Per layer: LN1(2d) + Wq(d*d+d) + Wk(d*d+d) + Wv(d*d+d) + Wo(d*d+d)
            #          + LN2(2d) + W1(d*h+h) + W2(h*d+d)
            per_layer = (2 * d) + 3 * (d * d + d) + (d * d + d) + (2 * d) + (d * h + h) + (h * d + d)
            p += nL * per_layer
            # Final LN + output head
            p += 2 * d + d * O + O
            return p
        else:
            arch = self.get_expert_arch()
            params = 0
            for i in range(len(arch) - 1):
                params += arch[i] * arch[i+1]  # weights
                params += arch[i+1]            # biases
            return params

    def count_router_params(self):
        """Count parameters in router."""
        arch = self.get_router_arch()
        params = 0
        for i in range(len(arch) - 1):
            params += arch[i] * arch[i+1]
            params += arch[i+1]
        return params

    def count_total_params(self):
        """Count total parameters in full MoE model."""
        return self.count_router_params() + (self.num_experts * self.count_expert_params())


class MoEModel:
    """
    Mixture of Experts model for distributed training.

    Each worker trains a single expert (shard).
    Router is trained on server or by all workers with small updates.
    """

    def __init__(self, config: MoEConfig, seed=None):
        self.config = config
        self.seed = seed

        # Initialize router network
        router_arch = config.get_router_arch()
        self.router = SimpleMLP(router_arch, seed=seed)

        # Initialize expert networks
        self.experts = []
        for i in range(config.num_experts):
            expert_seed = (seed + i + 1) if seed is not None else None
            self.experts.append(self._create_expert(config, expert_seed))

    def _create_expert(self, config, seed=None):
        """Create a single expert based on config.expert_type."""
        if config.expert_type == "transformer" and HAS_NUMPY:
            return SimpleTransformer(
                seq_len=config.input_size,
                d_model=config.d_model,
                n_heads=config.n_heads,
                d_ff=config.expert_hidden,
                n_layers=config.expert_layers,
                vocab_size=config.output_size,
                output_size=config.output_size,
                seed=seed,
            )
        else:
            expert_arch = config.get_expert_arch()
            return SimpleMLP(expert_arch, seed=seed)

    def get_expert_weights(self, expert_idx):
        """Get flattened weights for a single expert."""
        if 0 <= expert_idx < len(self.experts):
            return self.experts[expert_idx].get_weights_flat()
        return []

    def set_expert_weights(self, expert_idx, weights):
        """Set weights for a single expert."""
        if 0 <= expert_idx < len(self.experts):
            self.experts[expert_idx].set_weights_flat(weights)

    def get_router_weights(self):
        """Get flattened router weights."""
        return self.router.get_weights_flat()

    def set_router_weights(self, weights):
        """Set router weights."""
        self.router.set_weights_flat(weights)

    def get_all_weights(self):
        """Get all weights (router + all experts) as dict."""
        return {
            "router": self.get_router_weights(),
            "experts": [self.get_expert_weights(i) for i in range(len(self.experts))]
        }

    def set_all_weights(self, weights_dict):
        """Set all weights from dict."""
        if "router" in weights_dict:
            self.set_router_weights(weights_dict["router"])
        if "experts" in weights_dict:
            for i, exp_weights in enumerate(weights_dict["experts"]):
                if i < len(self.experts):
                    self.set_expert_weights(i, exp_weights)

    def route(self, x):
        """
        Compute routing probabilities for input x.
        Returns softmax over experts.
        """
        logits = self.router.forward(x)
        return softmax(logits)

    def forward(self, x, expert_idx=None):
        """
        Forward pass.

        If expert_idx is specified, only use that expert (for worker training).
        Otherwise, use weighted combination of all experts (full inference).
        """
        if expert_idx is not None:
            # Single expert forward (for distributed training)
            return self.experts[expert_idx].forward(x)

        # Full MoE forward with routing
        routing_probs = self.route(x)  # [batch, num_experts] or [num_experts]

        if HAS_NUMPY:
            # Batch processing
            if not isinstance(x, np.ndarray):
                x = np.array(x, dtype=np.float32)

            # Get output from each expert
            expert_outputs = []
            for expert in self.experts:
                out = expert.forward(x)
                expert_outputs.append(out)

            # Stack: [num_experts, batch, output_size]
            expert_outputs = np.stack(expert_outputs, axis=0)

            # Routing probs: [batch, num_experts] -> [batch, num_experts, 1]
            if not isinstance(routing_probs, np.ndarray):
                routing_probs = np.array(routing_probs)
            if routing_probs.ndim == 1:
                routing_probs = routing_probs.reshape(1, -1)

            # Weighted sum: [batch, output_size]
            # expert_outputs: [num_experts, batch, output_size]
            # routing_probs: [batch, num_experts]
            output = np.zeros((x.shape[0], self.config.output_size), dtype=np.float32)
            for i in range(len(self.experts)):
                output += routing_probs[:, i:i+1] * expert_outputs[i]

            return output
        else:
            # Pure Python - single sample
            expert_outputs = []
            for expert in self.experts:
                out = expert.forward(x)
                expert_outputs.append(out)

            # Weighted combination
            output = [0.0] * self.config.output_size
            for i, (prob, exp_out) in enumerate(zip(routing_probs, expert_outputs)):
                for j in range(len(output)):
                    output[j] += prob * exp_out[j]

            return output

    def forward_expert_only(self, x, expert_idx):
        """Forward through a single expert (for worker training)."""
        return self.experts[expert_idx].forward(x)

    def backward_expert_only(self, x, y_idx, logits, expert_idx):
        """
        Compute gradients for a single expert.
        Used by workers who only train their assigned expert.
        """
        return self.experts[expert_idx].backward_softmax_cross_entropy(x, y_idx, logits)

    def add_expert(self, seed=None):
        """Add a new expert (horizontal scaling)."""
        if len(self.experts) >= self.config.max_experts:
            return False

        new_seed = seed if seed is not None else (self.seed + len(self.experts) + 1 if self.seed else None)
        new_expert = self._create_expert(self.config, seed=new_seed)
        self.experts.append(new_expert)
        self.config.num_experts = len(self.experts)

        # Rebuild router to output to new number of experts
        self._rebuild_router()
        return True

    def grow_experts(self):
        """
        Grow expert capacity (vertical scaling).
        Doubles hidden size up to max.
        """
        new_hidden = min(self.config.expert_hidden * 2, self.config.max_expert_hidden)
        if new_hidden == self.config.expert_hidden:
            # Try adding a layer instead
            if self.config.expert_layers < self.config.max_expert_layers:
                self.config.expert_layers += 1
            else:
                return False  # Already at max
        else:
            self.config.expert_hidden = new_hidden

        # Rebuild experts with new architecture (lose old weights - need retraining)
        new_experts = []
        for i in range(len(self.experts)):
            expert_seed = (self.seed + i + 1) if self.seed is not None else None
            new_experts.append(self._create_expert(self.config, seed=expert_seed))
        self.experts = new_experts
        return True

    def _rebuild_router(self):
        """Rebuild router for new number of experts."""
        old_weights = self.router.get_weights_flat()
        router_arch = self.config.get_router_arch()
        self.router = SimpleMLP(router_arch, seed=self.seed)
        # Note: old weights don't transfer cleanly due to output size change
        # Router will need retraining after adding experts


class ExpertWorker:
    """
    Lightweight worker that only loads and trains a single expert.
    Used by BOINC clients to minimize RAM usage.
    """

    def __init__(self, config: MoEConfig, expert_idx: int, expert_weights: list, seed=None):
        self.config = config
        self.expert_idx = expert_idx

        # Create expert based on type
        if config.expert_type == "transformer" and HAS_NUMPY:
            self.expert = SimpleTransformer(
                seq_len=config.input_size,
                d_model=config.d_model,
                n_heads=config.n_heads,
                d_ff=config.expert_hidden,
                n_layers=config.expert_layers,
                vocab_size=config.output_size,
                output_size=config.output_size,
                seed=seed,
            )
        else:
            expert_arch = config.get_expert_arch()
            self.expert = SimpleMLP(expert_arch, seed=seed)

        if expert_weights:
            self.expert.set_weights_flat(expert_weights)

    def forward(self, x):
        """Forward through our expert."""
        return self.expert.forward(x)

    def backward(self, x, y_idx, logits):
        """Compute gradients for our expert."""
        return self.expert.backward_softmax_cross_entropy(x, y_idx, logits)

    def get_weights(self):
        """Get our expert's weights."""
        return self.expert.get_weights_flat()

    def get_param_count(self):
        """Get number of parameters in our expert."""
        return self.expert.get_param_count()

    def set_weights(self, weights):
        """Set our expert's weights."""
        self.expert.set_weights_flat(weights)

    def sgd_step(self, x, y_idx, logits, lr=0.001, max_grad_norm=1.0):
        """One SGD step with backprop - delegates to SimpleTransformer."""
        self.expert.sgd_step(x, y_idx, logits, lr=lr, max_grad_norm=max_grad_norm)

    def hebbian_update(self, lr=0.001, decay=0.999, target=None):
        """
        Hebbian learning update: "neurons that fire together, wire together"
        Call this after forward() - no backward pass needed!
        """
        self.expert.hebbian_update(lr=lr, decay=decay, target=target)

    def get_weight_delta(self, reference_weights):
        """Get difference between current weights and reference (for sync)."""
        return self.expert.get_weight_delta(reference_weights)

    def apply_weight_delta(self, delta, alpha=0.5):
        """Apply a weight delta from peer (gossip averaging)."""
        self.expert.apply_weight_delta(delta, alpha=alpha)


# Auto-scaling logic
class AutoScaler:
    """
    Monitors training progress and triggers scaling when plateaued.
    """

    def __init__(
        self,
        patience=10,           # Updates without improvement before scaling
        min_improvement=0.001, # Minimum BPC improvement to count as progress
        scale_strategy="alternate",  # "horizontal", "vertical", or "alternate"
    ):
        self.patience = patience
        self.min_improvement = min_improvement
        self.scale_strategy = scale_strategy

        self.bpc_history = []
        self.best_bpc = float('inf')
        self.updates_without_improvement = 0
        self.last_scale_action = "vertical"  # For alternating

    def record_bpc(self, bpc):
        """Record a BPC measurement. Returns True if should scale."""
        self.bpc_history.append(bpc)

        if bpc < self.best_bpc - self.min_improvement:
            # Improvement!
            self.best_bpc = bpc
            self.updates_without_improvement = 0
            return False
        else:
            # No improvement
            self.updates_without_improvement += 1
            if self.updates_without_improvement >= self.patience:
                self.updates_without_improvement = 0
                return True  # Time to scale
            return False

    def get_scale_action(self):
        """
        Decide how to scale.
        Returns: "add_expert", "grow_experts", or None
        """
        if self.scale_strategy == "horizontal":
            return "add_expert"
        elif self.scale_strategy == "vertical":
            return "grow_experts"
        else:  # alternate
            if self.last_scale_action == "vertical":
                self.last_scale_action = "horizontal"
                return "add_expert"
            else:
                self.last_scale_action = "vertical"
                return "grow_experts"

    def to_dict(self):
        return {
            "patience": self.patience,
            "min_improvement": self.min_improvement,
            "scale_strategy": self.scale_strategy,
            "bpc_history": self.bpc_history[-100:],  # Keep last 100
            "best_bpc": self.best_bpc,
            "updates_without_improvement": self.updates_without_improvement,
            "last_scale_action": self.last_scale_action,
        }

    @classmethod
    def from_dict(cls, d):
        scaler = cls(
            patience=d.get("patience", 10),
            min_improvement=d.get("min_improvement", 0.001),
            scale_strategy=d.get("scale_strategy", "alternate"),
        )
        scaler.bpc_history = d.get("bpc_history", [])
        scaler.best_bpc = d.get("best_bpc", float('inf'))
        scaler.updates_without_improvement = d.get("updates_without_improvement", 0)
        scaler.last_scale_action = d.get("last_scale_action", "vertical")
        return scaler
