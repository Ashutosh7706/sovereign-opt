"""Semantic registry: the only vocabulary the compiler can bind to."""
from __future__ import annotations

from ..models.refinery import RefineryParams

UNIT_ALIASES = {
    "Reformer": ["reformer", "ccr", "catalytic reformer", "platformer"],
    "FCC": ["fcc", "fccu", "cat cracker", "fluid catalytic cracker", "catalytic cracker"],
    "DHDS-1": ["dhds-1", "dhds 1", "dhds1", "dhds train 1", "dhds train-1", "diesel hydrotreater 1"],
    "DHDS-2": ["dhds-2", "dhds 2", "dhds2", "dhds train 2", "dhds train-2", "diesel hydrotreater 2"],
    "CDU": ["cdu", "crude unit", "crude distillation unit", "crude distillation", "topping unit"],
}
AMBIGUOUS_UNIT_ALIASES = {"dhds": ["DHDS-1", "DHDS-2"], "diesel hydrotreater": ["DHDS-1", "DHDS-2"],
                          "hydrotreater": ["DHDS-1", "DHDS-2"]}
CRUDE_ALIASES = {
    "Arab Light": ["arab light", "arablight", "arab-light", "al crude"],
    "Basrah Medium": ["basrah medium", "basrah", "basra"],
    "Bonny Light": ["bonny light", "bonny"],
    "Murban": ["murban"],
    "Mumbai High": ["mumbai high", "bombay high", "mh crude"],
}
SAFETY_PROPS = {"sulfur", "pressure", "temperature", "flash point", "rvp"}
SPEC_PROPS = {"MS": {"sulfur", "ron"}, "HSD": {"sulfur"}, "ATF": {"sulfur"}, "VLSFO": {"sulfur"}}


def registry(p: RefineryParams) -> dict:
    products = {}
    for name, pr in p.products.items():
        aliases = set(a.lower() for a in pr.aliases) | {name.lower(), f"tank {pr.tank}", f"tank-{pr.tank}",
                                                         f"tank{pr.tank}", f"tank no {pr.tank}", f"tank #{pr.tank}"}
        products[name] = sorted(aliases, key=len, reverse=True)
    units = {k: v for k, v in UNIT_ALIASES.items() if k in p.units or k == "CDU"}
    return {"products": products, "units": units, "crudes": CRUDE_ALIASES,
            "ambiguous_units": AMBIGUOUS_UNIT_ALIASES,
            "specs": [{"product": s.product, "prop": s.prop, "sense": s.sense, "value": s.value, "unit": s.unit,
                       "safety": s.safety} for s in p.specs],
            "tanks": {pr.tank: n for n, pr in p.products.items()}}


def describe_for_llm(p: RefineryParams) -> str:
    r = registry(p)
    lines = ["Products (tank -> name, aliases):"]
    for n, pr in p.products.items():
        lines.append(f"  tank {pr.tank}: {n}  aliases={pr.aliases}  demand {pr.min_demand}-{pr.max_demand} kbbl/d")
    lines.append("Current specs:")
    for s in r["specs"]:
        lines.append(f"  {s['product']} {s['prop']} {s['sense']} {s['value']} {s['unit']}")
    lines.append("Units: " + ", ".join(f"{n} (cap {u.capacity}, min run {u.min_run} kbbl/d)" for n, u in p.units.items())
                 + f", CDU (cap {p.cdu_capacity}, min {p.cdu_min})")
    lines.append("Crudes: " + ", ".join(f"{c.name} (max {c.max_parcels} parcels of {c.parcel} kbbl/d)" for c in p.crudes))
    return "\n".join(lines)
