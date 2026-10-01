"""Deterministic, fully offline rule parser (Sovereign Mode default - zero egress).

Returns the IR plus the *evidence* behind it (how the entity, comparator, number and
unit were found). The gate turns that evidence into a confidence score, so a phrase
like "Tank 3 sulfur limit 2.0" (missing unit, implied comparator, 4x the current spec)
scores low and is routed to a human.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .schema import ConstraintIR

RULES_VERSION = "1.1.0"  # bump on any rule change; pinned in every audit record (audit #100)

SYMBOLS = [("<=", "<="), ("≤", "<="), ("=<", "<="), (">=", ">="), ("≥", ">="), ("=>", ">="),
           ("==", "=="), ("<", "<"), (">", ">"), ("=", "==")]
LE_WORDS = ["no more than", "not more than", "not exceed", "not to exceed", "shall not exceed", "must not exceed",
            "at most", "up to", "maximum of", "maximum", "max of", "max", "below", "under", "less than",
            "lower than", "cap at", "capped at", "cap", "ceiling", "within"]
GE_WORDS = ["no less than", "not less than", "at least", "minimum of", "minimum", "min of", "min", "above",
            "more than", "greater than", "higher than", "over", "floor", "not below", "not fall below"]
EQ_WORDS = ["exactly", "equal to", "equals", "fixed at", "fix at", "set to", "set at"]
IMPLIED_LE = ["limit", "spec", "specification"]
OFF_WORDS = ["shut down", "shutdown", "shut", "take out", "offline", "off line", "turnaround", "maintenance",
             "switch off", "turn off", "stop", "down for", "bypass", "off"]
ON_WORDS = ["must run", "keep running", "keep on", "online", "on line", "switch on", "turn on", "start up",
            "startup", "must be on", "bring on", "put on", "keep it on"]
DEMAND_WORDS = ["demand", "lifting", "liftings", "sales", "sell", "supply", "deliver", "delivery", "offtake",
                "contract", "dispatch", "market"]
CAP_WORDS = ["capacity", "throughput", "feed", "charge", "run", "rate", "load", "processing"]
PARCEL_WORDS = ["parcel", "parcels", "cargo", "cargoes", "cargos", "shipment", "shipments"]
NEG_RE = re.compile(r"\b(not|never|don't|dont|no)\b")
NUM_RE = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(wt\s*%|wt%|%|ppm|mg/kg|kbbl/d|kb/d|kbpd|kbd|kbbl|kb|bpd|b/d|barrels per day|"
    r"parcels?|cargo(?:es|s)?|ron|octane)?", re.I)


@dataclass
class RuleParse:
    ir: ConstraintIR | None
    evidence: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    penalties: list = field(default_factory=list)  # (amount, reason)
    error: str | None = None


def _normalize(text: str) -> str:
    t = text.lower().replace("sulphur", "sulfur").replace("–", "-").replace("—", "-")
    t = re.sub(r"\bs content\b", "sulfur content", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def _find_aliases(t: str, table: dict[str, list[str]]):
    hits = []
    for canon, aliases in table.items():
        for a in sorted(aliases, key=len, reverse=True):
            for m in re.finditer(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", t):
                hits.append((m.start(), m.end(), canon, a))
    # keep longest non-overlapping
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    chosen = []
    for h in hits:
        if all(h[1] <= c[0] or h[0] >= c[1] for c in chosen):
            chosen.append(h)
    return chosen


def _has(t: str, words) -> str | None:
    for w in sorted(words, key=len, reverse=True):
        if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", t):
            return w
    return None


RELATIVE_RE = re.compile(r"\b(increase|decrease|raise|lower|reduce|cut|boost|drop|add|subtract)\w*\b.*\bby\b"
                         r"|\bby\s+-?\d+(\.\d+)?\s*(%|percent|kbbl)|\b(more|less|higher|lower) than (now|current|today)")
INJECTION_RE = re.compile(r"\b(ignore|disregard|override|bypass|system\s*:|assistant\s*:|instructions?|prompt|"
                          r"auto-?approve|approve (this|it|automatically)|jailbreak|drop table|delete from)\b", re.I)
TIME_RE = re.compile(r"\b(tomorrow|tonight|today|next|until|till|from \d|between|for \d+ (days?|hours?|weeks?)|"
                     r"during|shift|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")


def parse(text: str, reg: dict) -> RuleParse:
    out = RuleParse(None)
    ev = out.evidence
    raw = text
    if any(ch.isdigit() and not ch.isascii() for ch in text):  # e.g. full-width / other-script digits
        text = unicodedata.normalize("NFKC", text)
        out.penalties.append((0.15, "non-ASCII digits normalised - confirm the number"))
    # decimal comma (European style "0,2%") vs thousands separator ("2,000 ppm")
    text = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", text)
    if re.search(r"\d,\d", text):
        text = re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", text)
        out.notes.append("decimal comma read as a decimal point")
        out.penalties.append((0.15, "decimal comma - '0,2' read as 0.2; confirm the number"))
    t = _normalize(text)
    if RELATIVE_RE.search(t):
        out.error = ("relative changes ('increase by', 'more than now') are not supported - state the new "
                     "absolute limit, e.g. 'FCC capacity <= 90 kbbl/d'")
        return out
    if INJECTION_RE.search(t):
        out.penalties.append((0.40, "text contains words addressed to the system (instructions/approval/SQL) - "
                                    "only the constraint is used, but a human must look"))
    if TIME_RE.search(t):
        out.penalties.append((0.30, "time-bounded wording - the plan has one period, so the constraint would "
                                    "apply to the whole plan"))
    ev["normalised_text"] = t.strip() if t.strip() != raw.lower().strip() else None
    # ---------------- entities
    prod_hits = _find_aliases(t, reg["products"])
    unit_hits = _find_aliases(t, reg["units"])
    crude_hits = _find_aliases(t, reg["crudes"])
    amb_hits = [] if unit_hits else _find_aliases(t, {a: [a] for a in reg["ambiguous_units"]})
    spans = [(h[0], h[1]) for h in prod_hits + unit_hits + crude_hits + amb_hits]
    # strip entity text (e.g. "tank 3", "dhds-1") before reading numbers
    masked = list(t)
    for a, b in spans:
        for k in range(a, b):
            masked[k] = " "
    masked = "".join(masked)

    # ---------------- comparator
    sense, src = None, None
    for sym, s in SYMBOLS:
        if sym in masked:
            sense, src = s, "symbol"
            break
    if sense is None:
        w = _has(masked, LE_WORDS)
        rest = masked.replace(w, " ") if w else masked  # "no more than" must not also count as "more than"
        g = _has(rest, GE_WORDS)
        if g and not w:
            rest = masked.replace(g, " ")
        e = _has(rest, EQ_WORDS)
        if w and not g:
            sense, src = "<=", f"word '{w}'"
        elif g and not w:
            sense, src = ">=", f"word '{g}'"
        elif e:
            sense, src = "==", f"word '{e}'"
        elif w and g:
            out.penalties.append((0.35, f"conflicting comparator words ('{w}' and '{g}')"))
            sense, src = "<=", "conflict"
        elif _has(masked, IMPLIED_LE):
            sense, src = "<=", "implied by 'limit/spec'"
            out.penalties.append((0.10, "comparator implied by the word 'limit'/'spec', not stated explicitly"))
    if sense in ("<", ">"):
        out.notes.append(f"strict '{sense}' converted to '{sense}=' (LP constraints are closed)")
        out.penalties.append((0.05, "strict inequality converted to non-strict"))
        sense = sense + "="
    ev["comparator"] = {"sense": sense, "source": src}

    # ---------------- numbers
    nums = [(float(m.group(1)), (m.group(2) or "").lower().replace(" ", "")) for m in NUM_RE.finditer(masked)]
    ev["numbers"] = nums
    if len(nums) > 1:
        out.penalties.append((0.25, f"{len(nums)} numbers in the sentence - which one is the limit is ambiguous"))
    value, vunit = (nums[0] if nums else (None, ""))
    if NEG_RE.search(masked) and not any(p in masked for p in ("no more than", "not more than", "not exceed",
                                                                 "no less than", "not less than", "not below",
                                                                 "not fall below", "not to exceed")):
        out.penalties.append((0.30, "negation present - meaning may be inverted"))

    # ---------------- property / intent
    prop = "sulfur" if "sulfur" in t or re.search(r"\bs\b\s*(<|>|≤|≥|=)", t) else \
        ("ron" if re.search(r"\b(ron|octane)\b", t) else None)
    is_demand = _has(masked, DEMAND_WORDS)
    is_cap = _has(masked, CAP_WORDS)
    is_parcel = _has(masked, PARCEL_WORDS) or vunit.startswith(("parcel", "cargo"))
    status = None
    if _has(masked, OFF_WORDS):
        status = "off"
    elif _has(masked, ON_WORDS):
        status = "on"

    def unit_norm(u: str) -> str | None:
        if u in ("%", "wt%"):
            return "wt%"
        if u in ("ppm", "mg/kg"):
            return "ppm"
        if u in ("kbbl/d", "kb/d", "kbpd", "kbd", "kbbl", "kb"):
            return "kbbl/d"
        if u in ("bpd", "b/d", "barrelsperday"):
            return "bpd"
        if u.startswith(("parcel", "cargo")):
            return "parcels"
        if u in ("ron", "octane"):
            return "RON"
        return None

    unit = unit_norm(vunit)
    if value is not None and unit == "bpd":
        value, unit = value / 1000.0, "kbbl/d"
        out.notes.append("converted bbl/d to kbbl/d")
    ev.update({"prop": prop, "unit_raw": vunit, "status_word": status, "demand_word": is_demand,
               "capacity_word": is_cap, "parcel_word": bool(is_parcel)})

    # ---------------- entity resolution
    if amb_hits and not unit_hits:
        out.penalties.append((0.35, f"'{amb_hits[0][3]}' is ambiguous: {', '.join(reg['ambiguous_units'][amb_hits[0][3]])}"))
        unit_hits = [(amb_hits[0][0], amb_hits[0][1], reg["ambiguous_units"][amb_hits[0][3]][0], amb_hits[0][3])]
    kinds = [k for k, h in (("product", prod_hits), ("unit", unit_hits), ("crude", crude_hits)) if h]
    distinct = {h[2] for h in prod_hits + unit_hits + crude_hits}
    if not kinds:
        out.error = "no known product, tank, unit or crude named in the text"
        return out
    if len(distinct) > 1:
        out.penalties.append((0.30, f"several entities mentioned ({', '.join(sorted(distinct))})"))
    ev["entities"] = [{"canonical": h[2], "matched": h[3]} for h in prod_hits + unit_hits + crude_hits]

    if prop and is_demand and len(nums) > 1:
        out.error = "the sentence contains more than one constraint (a quality AND a volume) - enter them one at a time"
        return out

    # choose the intent
    if unit_hits and (status or not prod_hits):
        ent = unit_hits[0][2]
        if status and value is None:
            out.ir = ConstraintIR(kind="unit_status", entity=ent, status=status)
            return out
        if value is None:
            out.error = f"no number found for {ent}"
            return out
        if unit not in (None, "kbbl/d"):
            out.error = f"unit '{vunit}' is not a throughput unit for {ent}"
            return out
        if unit is None:
            out.penalties.append((0.15, "no unit given - assumed kbbl/d"))
        if sense is None:
            sense = ">=" if re.search(r"\b(min|minimum|turndown)\b", masked) else "<="
            out.penalties.append((0.20, "no comparator - assumed from context"))
        if not is_cap:
            out.penalties.append((0.10, "'capacity/throughput' not stated - interpreted as feed rate"))
        out.ir = ConstraintIR(kind="unit_capacity", entity=ent, sense=sense, value=value, unit="kbbl/d")
        return out

    if prod_hits:
        ent = prod_hits[0][2]
        if prop is None and unit in ("wt%", "ppm"):
            prop = "sulfur"
            out.penalties.append((0.25, "property not named - '%'/'ppm' interpreted as sulfur"))
        if prop is None and unit == "RON":
            prop = "ron"
        if prop:
            if value is None:
                out.error = f"no numeric {prop} value found"
                return out
            if prop == "ron":
                unit = "RON"
            elif unit is None:
                spec = next((s for s in reg["specs"] if s["product"] == ent and s["prop"] == prop), None)
                unit = spec["unit"] if spec else "wt%"
                out.penalties.append((0.20, f"no unit given for {prop} - assumed {unit} (current spec unit)"))
            if sense is None:
                sense = ">=" if prop == "ron" else "<="
                out.penalties.append((0.20, "no comparator - assumed the property's natural direction"))
            out.ir = ConstraintIR(kind="product_spec", entity=ent, prop=prop, sense=sense, value=value, unit=unit)
            return out
        if value is None:
            out.error = f"no number found for {ent}"
            return out
        if unit not in (None, "kbbl/d"):
            out.error = f"unit '{vunit}' does not fit a demand quantity"
            return out
        if unit is None:
            out.penalties.append((0.15, "no unit given - assumed kbbl/d"))
        if not is_demand:
            out.penalties.append((0.15, "'demand/lifting' not stated - interpreted as product volume"))
        if sense is None:
            sense = ">="
            out.penalties.append((0.20, "no comparator - assumed a minimum lifting"))
        out.ir = ConstraintIR(kind="product_demand", entity=ent, sense=sense, value=value, unit="kbbl/d")
        return out

    ent = crude_hits[0][2]
    if value is None:
        out.error = f"no number found for crude {ent}"
        return out
    if unit is None:
        if is_parcel or (float(value).is_integer() and value <= 5):
            unit = "parcels"
            if not is_parcel:
                out.penalties.append((0.25, "bare small integer interpreted as number of parcels"))
        else:
            unit = "kbbl/d"
            out.penalties.append((0.15, "no unit given - assumed kbbl/d"))
    if sense is None:
        sense = "<="
        out.penalties.append((0.20, "no comparator - assumed a maximum"))
    out.ir = ConstraintIR(kind="crude_limit", entity=ent, sense=sense, value=value, unit=unit)
    return out
