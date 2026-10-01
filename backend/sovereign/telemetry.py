"""Live hardware telemetry for the dashboard (dashboard upgrade plan, feature 6).

Real numbers only: `nvidia-smi --query-gpu=... -lms 500` streams GPU utilisation, VRAM, temperature,
SM clock and power; the solver's own CPU load is measured from this process's CPU time. When there
is no NVIDIA driver the stream says so instead of showing anything simulated.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque

from . import device

FIELDS = ["utilization.gpu", "memory.used", "memory.total", "temperature.gpu", "clocks.sm", "power.draw"]
KEYS = ["gpu_util", "vram_used_mb", "vram_total_mb", "temp_c", "sm_clock_mhz", "power_w"]


def _num(s: str):
    try:
        return float(s.strip())
    except ValueError:
        return None  # "[N/A]" on some laptop GPUs (e.g. power draw)


class Telemetry:
    def __init__(self, interval_ms: int = 500, history: int = 240):
        self.interval_ms = interval_ms
        self.latest: dict = {}
        self.history: deque = deque(maxlen=history)
        self._lock = threading.Lock()
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self.smi = shutil.which("nvidia-smi")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:  # static facts are known at once, so the first message is never empty
            self.latest.setdefault("cpu_threads", os.cpu_count() or 1)
            self.latest.setdefault("solver_cpu_pct", None)
            self.latest.setdefault("solver_cores", None)
            self.latest.setdefault("gpu_available", False)
            self.latest.setdefault("gpu_note", "reading nvidia-smi…" if self.smi else
                                   "no NVIDIA driver (nvidia-smi) on this machine - GPU gauges off")
        self._thread = threading.Thread(target=self._run, name="telemetry", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _cpu_sampler(self):
        last_cpu, last_wall = time.process_time(), time.perf_counter()
        threads = os.cpu_count() or 1
        while not self._stop.is_set():
            time.sleep(self.interval_ms / 1000)
            cpu, wall = time.process_time(), time.perf_counter()
            cores = (cpu - last_cpu) / max(wall - last_wall, 1e-6)
            last_cpu, last_wall = cpu, wall
            sample = {"t": time.time(), "solver_cores": round(cores, 2),
                      "solver_cpu_pct": round(100 * cores / threads, 1), "cpu_threads": threads}
            if device.gpu_available():  # memory the solver itself holds on the GPU
                try:  # pragma: no cover - needs CUDA
                    sample["solver_vram_mb"] = round(device.cupy().get_default_memory_pool().used_bytes() / 1e6, 1)
                except Exception:
                    pass
            with self._lock:
                self.latest.update(sample)
                if not self.smi:
                    self.latest.update({"gpu_available": False,
                                        "gpu_note": "no NVIDIA driver (nvidia-smi) on this machine - GPU gauges off"})
                    self.history.append(dict(self.latest))

    def _run(self):
        threading.Thread(target=self._cpu_sampler, daemon=True).start()
        if not self.smi:
            return
        cmd = [self.smi, f"--query-gpu={','.join(FIELDS)}", "--format=csv,noheader,nounits", f"-lms",
               str(self.interval_ms)]
        try:  # pragma: no cover - needs an NVIDIA driver
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                parts = line.strip().split(",")
                if len(parts) < len(FIELDS):
                    continue
                vals = dict(zip(KEYS, (_num(p) for p in parts)))
                with self._lock:
                    self.latest.update(vals)
                    self.latest.update({"gpu_available": True, "gpu_name": device.describe()["gpu"],
                                        "gpu_note": None})
                    self.history.append(dict(self.latest))
        except Exception as e:  # pragma: no cover
            with self._lock:
                self.latest.update({"gpu_available": False, "gpu_note": f"nvidia-smi failed: {e}"})

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.latest)
