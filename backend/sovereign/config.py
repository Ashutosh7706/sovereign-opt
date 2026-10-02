"""Validated configuration (audit #83, #99, #109).

Every environment variable the platform reads is declared here, validated at boot, and the
process refuses to start on a bad value (fail fast). Unknown SOVEREIGN_* variables are
reported (typos would otherwise be silently ignored). `python -m sovereign.config` prints
the table that must match the README.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

BACKEND = Path(__file__).resolve().parent.parent

ENV = {  # env var -> (field, description)
    "SOVEREIGN_DATA": ("data_dir", "SQLite database, backups, bootstrap file"),
    "SOVEREIGN_SKU": ("sku", "SKU-1 (Enterprise Multi-Tenant) or SKU-2 (Sovereign Single-Tenant)"),
    "SOVEREIGN_ALLOW_CLOUD_LLM": ("allow_cloud_llm", "1 enables cloud-llm mode; refused for SKU-2"),
    "SOVEREIGN_DISABLE_GPU": ("disable_gpu", "1 forces the CPU path"),
    "SOVEREIGN_GPU_MIN_ROWS": ("gpu_min_rows", "problems smaller than this stay on the CPU (measure: find_sweet_spot.py)"),
    "SOVEREIGN_LOCAL_LLM_URL": ("local_llm_url", "on-prem LLM (Ollama-compatible /api/chat)"),
    "SOVEREIGN_LOCAL_LLM_MODEL": ("local_llm_model", "model tag, pinned in audit records"),
    "SOVEREIGN_ALLOW_LAN_LLM": ("allow_lan_llm", "1 allows a non-loopback on-prem LLM host (audited)"),
    "SOVEREIGN_CLOUD_MODEL": ("cloud_model", "Claude model id for cloud-llm mode"),
    "GUROBI_GPU_PARAMS": ("gurobi_gpu_params", "JSON of Gurobi params for the GPU lane"),
    "SOVEREIGN_ANCHOR_DIR": ("anchor_dir", "write-once / off-box dir for audit chain anchors"),
    "SOVEREIGN_ALERT_WEBHOOK": ("alert_webhook", "optional on-prem webhook for solver-failure alerts"),
    "SOVEREIGN_ALERT_THRESHOLD": ("alert_threshold", "consecutive failures before an alert"),
    "SOVEREIGN_DISK_WARN_GB": ("disk_warn_gb", "free-space warning threshold for the data dir"),
    "SOVEREIGN_MAX_UPLOAD_MB": ("max_upload_mb", "MPS / readme upload size cap"),
    "SOVEREIGN_HTTPS": ("https", "1 when served over TLS (sets Secure cookies)"),
    "SOVEREIGN_RATE_PER_MIN": ("rate_per_min", "expensive calls per user per minute"),
    "SOVEREIGN_LOG_FORMAT": ("log_format", "json (default) or text"),
}


def _flag(v: str | None) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class Settings(BaseModel):
    data_dir: Path = BACKEND / "data"
    sku: Literal["SKU-1 Enterprise Multi-Tenant", "SKU-2 Sovereign Single-Tenant"] = "SKU-2 Sovereign Single-Tenant"
    allow_cloud_llm: bool = False
    disable_gpu: bool = False
    gpu_min_rows: int = Field(2000, ge=0, le=10_000_000)
    local_llm_url: str = "http://127.0.0.1:11434/api/chat"
    local_llm_model: str = "llama3.1:8b"
    allow_lan_llm: bool = False
    cloud_model: str = "claude-opus-5-5"
    gurobi_gpu_params: dict = Field(default_factory=lambda: {"Method": 6, "PDHGGPU": 1})
    anchor_dir: Optional[Path] = None
    alert_webhook: Optional[str] = None
    alert_threshold: int = Field(3, ge=1, le=100)
    disk_warn_gb: float = Field(2.0, ge=0)
    max_upload_mb: float = Field(50.0, gt=0, le=2048)
    https: bool = False
    rate_per_min: int = Field(30, ge=1, le=10000)
    log_format: Literal["json", "text"] = "json"
    unknown_env: list[str] = Field(default_factory=list)

    @field_validator("sku", mode="before")
    @classmethod
    def _sku(cls, v):
        aliases = {"sku-1": "SKU-1 Enterprise Multi-Tenant", "sku-2": "SKU-2 Sovereign Single-Tenant"}
        return aliases.get(str(v).strip().lower(), v)

    @field_validator("local_llm_url", "alert_webhook")
    @classmethod
    def _url(cls, v):
        if v is None:
            return v
        u = urlparse(v)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"not an http(s) URL: {v!r}")
        return v

    @model_validator(mode="after")
    def _policy(self):
        if self.sovereign and self.allow_cloud_llm:
            raise ValueError("SOVEREIGN_ALLOW_CLOUD_LLM=1 is not permitted in an SKU-2 Sovereign build "
                             "(cloud-llm is hard-locked off; build SKU-1 for Enterprise/cloud use)")
        return self

    @property
    def sovereign(self) -> bool:
        return self.sku.startswith("SKU-2")

    @property
    def cloud_allowed(self) -> bool:
        return self.allow_cloud_llm and not self.sovereign


def load(environ: dict | None = None) -> Settings:
    env = os.environ if environ is None else environ
    kw: dict = {}
    for var, (field, _) in ENV.items():
        if var not in env or env[var] == "":
            continue
        v = env[var]
        if field in ("allow_cloud_llm", "disable_gpu", "allow_lan_llm", "https"):
            kw[field] = _flag(v)
        elif field == "gurobi_gpu_params":
            try:
                kw[field] = json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"GUROBI_GPU_PARAMS is not valid JSON: {e}") from e
        else:
            kw[field] = v
    kw["unknown_env"] = sorted(k for k in env if k.startswith("SOVEREIGN_") and k not in ENV)
    return Settings(**kw)


def describe(s: Settings) -> list[dict]:
    out = []
    for var, (field, desc) in ENV.items():
        val = getattr(s, field)
        out.append({"env": var, "value": str(val) if val is not None else "", "set": var in os.environ,
                    "description": desc})
    return out


if __name__ == "__main__":  # python -m sovereign.config
    try:
        s = load()
    except (ValidationError, ValueError) as e:
        print(f"INVALID CONFIGURATION:\n{e}", file=sys.stderr)
        raise SystemExit(2)
    for row in describe(s):
        print(f"{row['env']:28s} {'*' if row['set'] else ' '} {row['value']:40s} {row['description']}")
    if s.unknown_env:
        print("\nUNKNOWN SOVEREIGN_* variables (typo?):", ", ".join(s.unknown_env))
