"""Language-model front-ends for the NL compiler (Sec. 14.2 - two deployment modes).

* sovereign-local : open-weight model served INSIDE the firewall (Ollama-compatible
                    endpoint). Refuses any non-loopback host unless explicitly allowed.
* cloud           : Claude via the Anthropic API - Enterprise/Cloud mode only, never
                    enabled in an air-gapped (CVC) deployment.

Both must return the closed LLMParse schema; anything else is discarded and the offline
rule parser takes over. The gate (gate.py) scores and verifies the result either way.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .schema import LLMParse

SYSTEM = """You translate one plain-English refinery planning instruction into a single typed constraint.
Only use entities from the registry below. Never invent entities, units or numbers.
Kinds:
 - product_spec: entity=product, prop in {sulfur, ron}, sense, value, unit (wt% | ppm | RON)
 - product_demand: entity=product, sense, value, unit=kbbl/d
 - unit_capacity: entity=unit (Reformer, FCC, DHDS-1, DHDS-2, CDU), sense (<= capacity, >= minimum run), value, unit=kbbl/d
 - unit_status: entity=unit, status on|off
 - crude_limit: entity=crude, sense, value, unit = parcels | kbbl/d
Keep the number and unit exactly as written (do not convert % to ppm). If anything is ambiguous,
list it in `ambiguities` and lower `confidence`. If the text is not a single supported constraint,
return ir = null. `restatement` must say plainly what you understood, including the unit.

Registry:
"""


class LLMUnavailable(RuntimeError):
    pass


def parse_cloud(text: str, registry_text: str) -> LLMParse:
    try:
        import anthropic
    except ImportError as e:
        raise LLMUnavailable("anthropic SDK not installed (pip install anthropic)") from e
    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=os.environ.get("SOVEREIGN_CLOUD_MODEL", "claude-opus-5-5"),
            max_tokens=4000,
            system=SYSTEM + registry_text,
            messages=[{"role": "user", "content": text}],
            output_format=LLMParse,
        )
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable(f"cannot reach Anthropic API: {e}") from e
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable("no valid Anthropic credentials (set ANTHROPIC_API_KEY)") from e
    except anthropic.APIStatusError as e:
        raise LLMUnavailable(f"Anthropic API error {e.status_code}") from e
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise LLMUnavailable(f"model returned no structured output (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def parse_local(text: str, registry_text: str) -> LLMParse:
    url = os.environ.get("SOVEREIGN_LOCAL_LLM_URL", "http://127.0.0.1:11434/api/chat")
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("SOVEREIGN_ALLOW_LAN_LLM") != "1":
        raise LLMUnavailable(f"refusing non-loopback LLM host '{host}' in sovereign mode "
                             "(set SOVEREIGN_ALLOW_LAN_LLM=1 for an on-prem inference server)")
    body = {
        "model": os.environ.get("SOVEREIGN_LOCAL_LLM_MODEL", "llama3.1:8b"),
        "stream": False,
        "format": LLMParse.model_json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM + registry_text}, {"role": "user", "content": text}],
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except Exception as e:
        raise LLMUnavailable(f"local LLM endpoint not reachable at {url}: {e}") from e
    content = data.get("message", {}).get("content", "")
    try:
        return LLMParse.model_validate_json(content)
    except Exception as e:
        raise LLMUnavailable(f"local LLM output did not match the schema: {e}") from e
