"""
device_specs.py — best-effort local hardware detection + Ollama model
recommendations for the setup screen (SetupOverlay in ui.py).

Nothing here is safety- or correctness-critical: if detection fails for any
reason (psutil missing, no GPU tooling, sandboxed/containerized host, etc.)
every function degrades gracefully to "unknown" rather than raising, so the
setup screen never breaks because it couldn't read hardware info.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass
class DeviceSpecs:
    ram_gb:        float | None = None
    cpu_cores:     int | None   = None
    gpu_name:      str | None   = None
    gpu_vram_gb:   float | None = None
    detected_ok:   bool         = False   # True if we got at least RAM


@dataclass
class ModelRecommendation:
    tag:         str            # Ollama pull tag, e.g. "qwen2.5:7b"
    approx_gb:   float          # rough download/resident size in GB
    min_ram_gb:  float          # rough RAM needed to run it comfortably
    note:        str
    recommended: bool = False   # best-fit flag for this device


# Curated, deliberately small list of well-known, broadly-useful Ollama
# tags spanning "runs on almost anything" to "needs a real GPU or a lot of
# RAM". Sizes/RAM figures are approximate (quantized GGUF defaults) — good
# enough for a recommendation, not meant to be exact.
_CATALOG: list[tuple[str, float, float, str]] = [
    ("qwen2.5:1.5b", 1.0,  2.0,  "Fastest option — good for low-power or older machines."),
    ("llama3.2:3b",  2.0,  4.0,  "Good balance of speed and quality for everyday laptops."),
    ("qwen2.5:7b",   4.7,  8.0,  "Solid general-purpose model — needs 8GB+ RAM."),
    ("qwen3:8b",     5.2,  8.0,  "Aether's current default — strong tool-calling support."),
    ("qwen2.5:14b",  9.0,  16.0, "Stronger reasoning — needs 16GB+ RAM."),
    ("qwen2.5:32b",  20.0, 32.0, "High quality — needs 32GB+ RAM or a strong GPU."),
    ("llama3.1:70b", 40.0, 64.0, "Best quality here — needs 64GB+ RAM or a high-VRAM GPU; will be slow otherwise."),
]


def _detect_ram_and_cpu() -> tuple[float | None, int | None]:
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        cores  = psutil.cpu_count(logical=True)
        return ram_gb, cores
    except Exception:
        return None, None


def _detect_gpu() -> tuple[str | None, float | None]:
    # Try nvidia-smi first — works without any Python deps, most reliable
    # signal for "there is a usable NVIDIA GPU with this much VRAM".
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            line = out.stdout.strip().splitlines()[0]
            name, mem_mib = [p.strip() for p in line.split(",")]
            return name, round(float(mem_mib) / 1024, 1)
    except Exception:
        pass

    # Fall back to torch's view of CUDA, if it happens to be importable
    # (Aether already depends on torch for hybrid memory's embedding model).
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / (1024 ** 3), 1)
            return name, vram
    except Exception:
        pass

    return None, None


def get_device_specs() -> DeviceSpecs:
    """Best-effort snapshot of this machine's RAM/CPU/GPU. Never raises."""
    ram_gb, cores = _detect_ram_and_cpu()
    gpu_name, gpu_vram = _detect_gpu()
    return DeviceSpecs(
        ram_gb=ram_gb,
        cpu_cores=cores,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        detected_ok=ram_gb is not None,
    )


def recommend_models(specs: DeviceSpecs, limit: int = 4) -> list[ModelRecommendation]:
    """Filter/rank the catalog for this device.

    If RAM couldn't be detected, returns a small safe default set rather
    than nothing, so the UI always has something useful to show.
    """
    if specs.ram_gb is None:
        # Unknown hardware -- offer the two safest, broadly-compatible
        # options rather than guessing.
        return [
            ModelRecommendation("qwen2.5:1.5b", 1.0, 2.0,
                                 "Safe default if we can't read your specs.",
                                 recommended=True),
            ModelRecommendation("llama3.2:3b", 2.0, 4.0,
                                 "A bit stronger, still light.", recommended=False),
        ]

    # A GPU with meaningful VRAM effectively raises the ceiling, since
    # Ollama can offload layers to it instead of relying purely on RAM.
    effective_gb = specs.ram_gb
    if specs.gpu_vram_gb:
        effective_gb = max(specs.ram_gb, specs.gpu_vram_gb * 2)

    fits = [
        ModelRecommendation(tag, size, min_ram, note)
        for tag, size, min_ram, note in _CATALOG
        if min_ram <= effective_gb
    ]
    if not fits:
        # Even the smallest model is a stretch -- still offer it; Ollama
        # will just be slow rather than refuse to run.
        tag, size, min_ram, note = _CATALOG[0]
        fits = [ModelRecommendation(tag, size, min_ram, note)]

    # Best fit = the largest model that comfortably fits, leaving some
    # headroom rather than maxing out available RAM.
    comfortable = [m for m in fits if m.min_ram_gb <= effective_gb * 0.75]
    best = (comfortable or fits)[-1]
    best.recommended = True

    # Keep the models nearest the best fit (a couple of steps down, one
    # step up if it fits) rather than the entire catalog.
    ordered = fits[-limit:]
    if best not in ordered:
        ordered = ordered[:-1] + [best]
    return ordered
