"""Operations: structured logging, metrics, rate limiting, failure alerts, disk checks
(audit #24, #76, #78, #79, #82). Standard library only - nothing to install on site.
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import threading
import time
import urllib.request
from collections import defaultdict, deque
from pathlib import Path


# ------------------------------------------------------------------ structured logging (#76)
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
             "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        extra = getattr(record, "fields", None)
        if extra:
            d.update(extra)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def setup_logging(fmt: str = "json") -> logging.Logger:
    log = logging.getLogger("sovereign")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def event(log: logging.Logger, msg: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, msg, extra={"fields": fields})


# ------------------------------------------------------------------ metrics (#78)
class Metrics:
    """Minimal Prometheus text-format exporter: counters + summary (count/sum) per label set."""

    def __init__(self):
        self._lock = threading.Lock()
        self.counters: dict[tuple, float] = defaultdict(float)
        self.sums: dict[tuple, float] = defaultdict(float)
        self.counts: dict[tuple, int] = defaultdict(int)
        self.gauges: dict[tuple, float] = {}
        self.help: dict[str, str] = {}

    def inc(self, name: str, help_: str = "", value: float = 1.0, **labels):
        with self._lock:
            self.help.setdefault(name, help_)
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def observe(self, name: str, value: float, help_: str = "", **labels):
        with self._lock:
            self.help.setdefault(name, help_)
            k = (name, tuple(sorted(labels.items())))
            self.sums[k] += value
            self.counts[k] += 1

    def gauge(self, name: str, value: float, help_: str = "", **labels):
        with self._lock:
            self.help.setdefault(name, help_)
            self.gauges[(name, tuple(sorted(labels.items())))] = value

    @staticmethod
    def _lab(labels) -> str:
        return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}" if labels else ""

    def render(self) -> str:
        out = []
        with self._lock:
            names = sorted({k[0] for k in list(self.counters) + list(self.sums) + list(self.gauges)})
            for n in names:
                if n in {k[0] for k in self.counters}:
                    out.append(f"# HELP {n} {self.help.get(n, '')}\n# TYPE {n} counter")
                    out += [f"{n}{self._lab(l)} {v}" for (nn, l), v in self.counters.items() if nn == n]
                if n in {k[0] for k in self.sums}:
                    out.append(f"# HELP {n} {self.help.get(n, '')}\n# TYPE {n} summary")
                    for (nn, l), v in self.sums.items():
                        if nn == n:
                            out.append(f"{n}_sum{self._lab(l)} {v}")
                            out.append(f"{n}_count{self._lab(l)} {self.counts[(nn, l)]}")
                if n in {k[0] for k in self.gauges}:
                    out.append(f"# HELP {n} {self.help.get(n, '')}\n# TYPE {n} gauge")
                    out += [f"{n}{self._lab(l)} {v}" for (nn, l), v in self.gauges.items() if nn == n]
        return "\n".join(out) + "\n"


# ------------------------------------------------------------------ rate limiting (#24)
class RateLimiter:
    """Sliding-window limiter keyed by user (or client IP for login)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, per_minute: int | None = None) -> bool:
        limit = per_minute or self.per_minute
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


# ------------------------------------------------------------------ failure alerts (#79)
class FailureAlerter:
    """Counts consecutive solver failures / numerical warnings; after `threshold` it raises an
    alert: ERROR log line, audit event, /health turns 'degraded', optional on-prem webhook."""

    def __init__(self, threshold: int, webhook: str | None, audit=None, log=None):
        self.threshold = threshold
        self.webhook = webhook
        self.audit = audit
        self.log = log
        self.consecutive = 0
        self.active_alert: dict | None = None
        self._lock = threading.Lock()

    def record(self, ok: bool, context: dict) -> None:
        with self._lock:
            if ok:
                self.consecutive = 0
                self.active_alert = None
                return
            self.consecutive += 1
            if self.consecutive < self.threshold:
                return
            self.active_alert = {"consecutive_failures": self.consecutive, "last": context,
                                 "since": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if self.log:
            event(self.log, "solver failure alert", logging.ERROR, **self.active_alert)
        if self.audit:
            try:
                self.audit.append("alert.solver_failures", "system", self.active_alert)
            except Exception:  # alerting must never take the service down
                pass
        if self.webhook:
            threading.Thread(target=self._post, args=(dict(self.active_alert),), daemon=True).start()

    def _post(self, body: dict):
        try:
            req = urllib.request.Request(self.webhook, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as e:  # pragma: no cover
            if self.log:
                event(self.log, "alert webhook failed", logging.WARNING, error=str(e))


# ------------------------------------------------------------------ disk (#82)
def disk_status(path: Path, warn_gb: float) -> dict:
    try:
        u = shutil.disk_usage(path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    free_gb = u.free / 1e9
    return {"ok": free_gb >= warn_gb, "free_gb": round(free_gb, 2), "total_gb": round(u.total / 1e9, 2),
            "warn_below_gb": warn_gb}


def dir_size_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 1e6, 2)
