"""Synthetic MRPL-style refinery planning model (mixed-integer).

ILLUSTRATIVE DATA ONLY - numbers are plausible magnitudes, not MRPL's real economics.

Decisions (single planning day, kbbl/d and $k/d):
  * integer crude parcels n[c] (fixed freight per parcel) and crude run x[c] <= parcel * n[c]
  * binary unit on/off for Reformer, FCC, DHDS-1, DHDS-2 (min throughput + fixed cost when on)
  * per-crude cut dispositions (keeps every quality constraint linear - no pooling)
  * product blending with sulfur / RON specs into product tanks 1..6

Everything the NL-to-constraint compiler may touch lives in RefineryParams so a
constraint change is an auditable parameter delta, never free-form math.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from ..model import INF, Model, ModelBuilder

CUTS = ["LPG", "NAP", "KERO", "GO", "VGO", "RES"]
# sulfur of a straight-run cut = factor * crude sulfur (wt%)
CUT_SULFUR_FACTOR = {"LPG": 0.0, "NAP": 0.05, "KERO": 0.15, "GO": 0.6, "VGO": 1.1, "RES": 2.2}


@dataclass
class Crude:
    name: str
    price: float  # $/bbl
    sulfur: float  # wt%
    parcel: float  # kbbl/d per parcel
    max_parcels: int
    freight: float  # $k per parcel-day
    yields: dict  # cut -> fraction
    origin: str = "import"


@dataclass
class Unit:
    name: str
    capacity: float
    min_run: float
    fixed_cost: float
    opex: float  # $/bbl
    status: str = "auto"  # auto | on | off


@dataclass
class Product:
    name: str
    tank: int
    price: float
    min_demand: float
    max_demand: float
    aliases: list = field(default_factory=list)


@dataclass
class Spec:
    product: str
    prop: str  # sulfur | ron
    sense: str  # <= | >=
    value: float
    unit: str  # 'wt%' | 'ppm' | 'RON'
    safety: bool = False


@dataclass
class RefineryParams:
    crudes: list
    units: dict
    products: dict
    specs: list
    cdu_capacity: float = 300.0
    cdu_min: float = 180.0
    cdu_opex: float = 1.2
    extra_crude_bounds: dict = field(default_factory=dict)  # crude -> {"max_parcels"/"min_parcels"/"max_run"/"min_run"}

    def copy(self) -> "RefineryParams":
        return copy.deepcopy(self)


def default_params() -> RefineryParams:
    crudes = [
        Crude("Arab Light", 82.0, 1.9, 60, 3, 95, dict(LPG=.02, NAP=.18, KERO=.13, GO=.22, VGO=.25, RES=.20)),
        Crude("Basrah Medium", 77.5, 2.9, 60, 3, 90, dict(LPG=.015, NAP=.16, KERO=.12, GO=.21, VGO=.24, RES=.255)),
        Crude("Bonny Light", 86.5, 0.15, 50, 2, 140, dict(LPG=.025, NAP=.22, KERO=.15, GO=.26, VGO=.23, RES=.115)),
        Crude("Murban", 85.0, 0.8, 50, 3, 110, dict(LPG=.03, NAP=.23, KERO=.15, GO=.24, VGO=.22, RES=.13)),
        Crude("Mumbai High", 83.0, 0.2, 40, 1, 25, dict(LPG=.02, NAP=.20, KERO=.14, GO=.25, VGO=.24, RES=.15), "domestic"),
    ]
    units = {
        "Reformer": Unit("Reformer", 45, 15, 120, 3.0),
        "FCC": Unit("FCC", 80, 35, 180, 2.5),
        "DHDS-1": Unit("DHDS-1", 110, 40, 90, 2.0),
        "DHDS-2": Unit("DHDS-2", 60, 20, 70, 2.0),
    }
    products = {
        "MS": Product("MS", 1, 112.0, 50, 90, ["petrol", "gasoline", "motor spirit", "ms bs-vi", "ms"]),
        "HSD": Product("HSD", 2, 108.0, 80, 140, ["diesel", "hsd", "high speed diesel", "gasoil product"]),
        "VLSFO": Product("VLSFO", 3, 80.0, 30, 70, ["vlsfo", "fuel oil", "fo", "bunker", "low sulphur fuel oil",
                                                    "low sulfur fuel oil"]),
        "ATF": Product("ATF", 4, 110.0, 20, 45, ["atf", "jet", "jet fuel", "aviation turbine fuel", "kerosene product"]),
        "LPG": Product("LPG", 5, 60.0, 0, 25, ["lpg", "cooking gas"]),
        "HSFO": Product("HSFO", 6, 64.0, 0, 60, ["hsfo", "high sulfur fuel oil", "high sulphur fuel oil"]),
        "NAP-EXPORT": Product("NAP-EXPORT", 7, 75.0, 0, 30, ["naphtha export", "naphtha"]),
    }
    specs = [
        Spec("MS", "ron", ">=", 91.0, "RON"),
        Spec("MS", "sulfur", "<=", 10.0, "ppm", safety=True),
        Spec("HSD", "sulfur", "<=", 10.0, "ppm", safety=True),
        Spec("ATF", "sulfur", "<=", 0.30, "wt%", safety=True),
        Spec("VLSFO", "sulfur", "<=", 0.50, "wt%", safety=True),
    ]
    return RefineryParams(crudes, units, products, specs)


# component qualities of processed streams (sulfur in wt%)
RON = {"SRN": 68.0, "REFORMATE": 98.0, "FCCG": 92.0}
SULFUR_PPM = {"SRN": 5.0, "REFORMATE": 1.0, "FCCG": 8.0, "ULSD": 8.0}
REFORMER_YIELD = {"REFORMATE": 0.85, "LPG": 0.08}
FCC_YIELD = {"FCCG": 0.50, "LCO": 0.20, "LPG": 0.15}
DHDS_YIELD = 0.97


def _slug(s: str) -> str:
    return s.replace(" ", "_").replace("-", "_")


def _sulfur_to(unit: str, wt_pct: float) -> float:
    return wt_pct * 10000.0 if unit == "ppm" else wt_pct


def build(params: RefineryParams | None = None, name: str = "MRPL-synthetic refinery plan") -> Model:
    p = params or default_params()
    b = ModelBuilder(name, maximize=True)  # maximize gross margin ($k/d)
    C = [_slug(c.name) for c in p.crudes]
    crude = {cs: c for cs, c in zip(C, p.crudes)}

    # --- crude purchase + run
    for cs, c in crude.items():
        extra = p.extra_crude_bounds.get(c.name, {})
        maxp = min(c.max_parcels, extra.get("max_parcels", c.max_parcels))
        minp = extra.get("min_parcels", 0)
        b.var(f"parcels[{cs}]", minp, maxp, obj=-c.freight, integer=True)
        b.var(f"crude[{cs}]", extra.get("min_run", 0.0), extra.get("max_run", INF),
              obj=-(c.price + p.cdu_opex))
        b.constr(f"parcel_link[{cs}]", {f"crude[{cs}]": 1, f"parcels[{cs}]": -c.parcel}, "<=", 0,
                 kind="logic", hard=True, desc=f"{c.name} run limited to purchased parcels ({c.parcel:g} kbbl/d each)")
    b.constr("cdu_capacity", {f"crude[{cs}]": 1 for cs in C}, "<=", p.cdu_capacity, kind="capacity",
             entity="CDU", desc=f"Crude unit throughput <= {p.cdu_capacity:g} kbbl/d", weight=5.0,
             value=p.cdu_capacity, label="CDU capacity", unit="kbbl/d")
    b.constr("cdu_min", {f"crude[{cs}]": 1 for cs in C}, ">=", p.cdu_min, kind="capacity", entity="CDU",
             desc=f"Crude unit minimum run >= {p.cdu_min:g} kbbl/d", weight=2.0,
             value=p.cdu_min, label="CDU minimum run", unit="kbbl/d")

    # --- units with on/off binaries
    for un, u in p.units.items():
        us = _slug(un)
        lo, hi = (1, 1) if u.status == "on" else (0, 0) if u.status == "off" else (0, 1)
        b.var(f"on[{us}]", lo, hi, obj=-u.fixed_cost, integer=True)
        b.var(f"feed[{us}]", 0, INF, obj=-u.opex)
        b.constr(f"cap[{us}]", {f"feed[{us}]": 1, f"on[{us}]": -u.capacity}, "<=", 0, kind="capacity",
                 entity=un, desc=f"{un} feed <= {u.capacity:g} kbbl/d when running", weight=3.0,
                 value=u.capacity, label=f"{un} capacity", unit="kbbl/d")
        b.constr(f"minrun[{us}]", {f"feed[{us}]": 1, f"on[{us}]": -u.min_run}, ">=", 0, kind="capacity",
                 entity=un, desc=f"{un} feed >= {u.min_run:g} kbbl/d when running (turndown)", weight=3.0,
                 value=u.min_run, label=f"{un} minimum run", unit="kbbl/d")

    # --- cut dispositions per crude (flow balances are hard physics)
    dest = {"NAP": ["REF", "MS", "EXP"], "KERO": ["ATF", "DHDS"], "GO": ["DHDS", "VLSFO"],
            "VGO": ["FCC", "VLSFO", "HSFO"], "RES": ["VLSFO", "HSFO"]}
    for cs, c in crude.items():
        for cut, ds in dest.items():
            for d in ds:
                b.var(f"{cut}[{cs}->{d}]")
            b.constr(f"yield[{cs},{cut}]", {**{f"{cut}[{cs}->{d}]": 1 for d in ds},
                                           f"crude[{cs}]": -c.yields[cut]}, "==", 0,
                     kind="balance", hard=True, desc=f"{c.name} {cut} yield {c.yields[cut]:.1%} fully dispositioned")

    # unit feeds
    b.constr("feed_bal[Reformer]", {**{f"NAP[{cs}->REF]": 1 for cs in C}, "feed[Reformer]": -1}, "==", 0,
             kind="balance", hard=True, desc="Reformer feed = naphtha routed to reformer")
    b.constr("feed_bal[FCC]", {**{f"VGO[{cs}->FCC]": 1 for cs in C}, "feed[FCC]": -1}, "==", 0,
             kind="balance", hard=True, desc="FCC feed = VGO routed to FCC")
    # DHDS pool: kero + gasoil + all LCO, split across the two trains
    dhds_terms = {**{f"KERO[{cs}->DHDS]": 1 for cs in C}, **{f"GO[{cs}->DHDS]": 1 for cs in C},
                  "feed[FCC]": FCC_YIELD["LCO"], "feed[DHDS_1]": -1, "feed[DHDS_2]": -1}
    b.constr("feed_bal[DHDS]", dhds_terms, "==", 0, kind="balance", hard=True,
             desc="DHDS trains take kerosene, gasoil and all FCC LCO")

    # --- products
    b.var("SRN_to_MS"); b.var("REFORMATE_to_MS"); b.var("FCCG_to_MS")
    b.constr("srn_bal", {**{f"NAP[{cs}->MS]": 1 for cs in C}, "SRN_to_MS": -1}, "==", 0, kind="balance",
             hard=True, desc="Straight-run naphtha to MS pool")
    b.constr("reformate_bal", {"REFORMATE_to_MS": 1, "feed[Reformer]": -REFORMER_YIELD["REFORMATE"]}, "==", 0,
             kind="balance", hard=True, desc="All reformate to MS pool")
    b.constr("fccg_bal", {"FCCG_to_MS": 1, "feed[FCC]": -FCC_YIELD["FCCG"]}, "==", 0, kind="balance",
             hard=True, desc="All FCC gasoline to MS pool")

    prod_terms = {
        "MS": {"SRN_to_MS": 1, "REFORMATE_to_MS": 1, "FCCG_to_MS": 1},
        "HSD": {"feed[DHDS_1]": DHDS_YIELD, "feed[DHDS_2]": DHDS_YIELD},
        "ATF": {f"KERO[{cs}->ATF]": 1 for cs in C},
        "VLSFO": {**{f"GO[{cs}->VLSFO]": 1 for cs in C}, **{f"VGO[{cs}->VLSFO]": 1 for cs in C},
                  **{f"RES[{cs}->VLSFO]": 1 for cs in C}},
        "HSFO": {**{f"VGO[{cs}->HSFO]": 1 for cs in C}, **{f"RES[{cs}->HSFO]": 1 for cs in C}},
        "LPG": {**{f"crude[{cs}]": crude[cs].yields["LPG"] for cs in C},
                "feed[Reformer]": REFORMER_YIELD["LPG"], "feed[FCC]": FCC_YIELD["LPG"]},
        "NAP-EXPORT": {f"NAP[{cs}->EXP]": 1 for cs in C},
    }
    for pn, pr in p.products.items():
        ps = _slug(pn)
        b.var(f"sales[{ps}]", 0, INF, obj=pr.price)
        b.constr(f"make[{ps}]", {**prod_terms[pn], f"sales[{ps}]": -1}, "==", 0, kind="balance", hard=True,
                 desc=f"Tank {pr.tank} ({pn}) receipts = sales")
        if pr.min_demand > 0:
            b.constr(f"demand_min[{ps}]", {f"sales[{ps}]": 1}, ">=", pr.min_demand, kind="demand", entity=pn,
                     desc=f"Tank {pr.tank} {pn} contract lifting >= {pr.min_demand:g} kbbl/d", weight=4.0,
                     value=pr.min_demand, label=f"Tank {pr.tank} {pn} contract lifting", unit="kbbl/d")
        b.constr(f"demand_max[{ps}]", {f"sales[{ps}]": 1}, "<=", pr.max_demand, kind="demand", entity=pn,
                 desc=f"Tank {pr.tank} {pn} market limit <= {pr.max_demand:g} kbbl/d", weight=1.0,
                 value=pr.max_demand, label=f"Tank {pr.tank} {pn} market limit", unit="kbbl/d")

    # --- quality specs:  sum_k (q_k - spec) * v_k  (<= or >=) 0
    def component_quality(prod: str, prop: str, unit: str) -> dict:
        q = {}
        if prod == "MS":
            if prop == "ron":
                return {"SRN_to_MS": RON["SRN"], "REFORMATE_to_MS": RON["REFORMATE"], "FCCG_to_MS": RON["FCCG"]}
            src = {"SRN_to_MS": "SRN", "REFORMATE_to_MS": "REFORMATE", "FCCG_to_MS": "FCCG"}
            return {v: (SULFUR_PPM[k] if unit == "ppm" else SULFUR_PPM[k] / 1e4) for v, k in src.items()}
        if prod == "HSD" and prop == "sulfur":
            s = SULFUR_PPM["ULSD"] if unit == "ppm" else SULFUR_PPM["ULSD"] / 1e4
            return {"feed[DHDS_1]": s * DHDS_YIELD, "feed[DHDS_2]": s * DHDS_YIELD}, DHDS_YIELD
        for cs, c in crude.items():
            for cut in ("KERO",) if prod == "ATF" else ("GO", "VGO", "RES"):
                var = f"{cut}[{cs}->{'ATF' if prod == 'ATF' else 'VLSFO'}]"
                q[var] = _sulfur_to(unit, c.sulfur * CUT_SULFUR_FACTOR[cut])
        return q

    for sp_ in p.specs:
        res = component_quality(sp_.product, sp_.prop, sp_.unit)
        scale = 1.0
        if isinstance(res, tuple):
            res, scale = res
        terms = {v: (q - sp_.value * scale) for v, q in res.items()}
        ps = _slug(sp_.product)
        label = {"sulfur": "sulfur", "ron": "RON"}[sp_.prop]
        tank = p.products[sp_.product].tank
        b.constr(f"spec[{ps},{sp_.prop},{'max' if sp_.sense == '<=' else 'min'}]", terms, sp_.sense, 0.0,
                 kind="spec", entity=sp_.product, prop=sp_.prop, safety=sp_.safety, weight=2.0,
                 value=sp_.value, unit=sp_.unit, volume_var=f"sales[{ps}]",
                 label=f"Tank {tank} {sp_.product} {label} spec",
                 desc=f"Tank {tank} {sp_.product} {label} {sp_.sense} {sp_.value:g} {sp_.unit}")
    return b.build(meta={"kind": "refinery", "units": "kbbl/d, $k/d", "illustrative": True})


def plan_report(model: Model, x: np.ndarray, p: RefineryParams | None = None) -> dict:
    p = p or default_params()
    v = dict(zip(model.var_names, x))
    crudes = []
    for c in p.crudes:
        cs = _slug(c.name)
        crudes.append({"name": c.name, "parcels": round(v.get(f"parcels[{cs}]", 0)), "run": v.get(f"crude[{cs}]", 0.0),
                       "sulfur": c.sulfur, "price": c.price, "origin": c.origin})
    units = [{"name": un, "on": round(v.get(f"on[{_slug(un)}]", 0)) == 1, "feed": v.get(f"feed[{_slug(un)}]", 0.0),
              "capacity": u.capacity, "min_run": u.min_run} for un, u in p.units.items()]
    products = []
    for pn, pr in p.products.items():
        products.append({"name": pn, "tank": pr.tank, "sales": v.get(f"sales[{_slug(pn)}]", 0.0), "price": pr.price,
                         "min": pr.min_demand, "max": pr.max_demand})
    # achieved qualities
    qual = []
    for sp_ in p.specs:
        i = model.row_names.index(f"spec[{_slug(sp_.product)},{sp_.prop},{'max' if sp_.sense == '<=' else 'min'}]")
        row = model.A.getrow(i)
        vol = v.get(f"sales[{_slug(sp_.product)}]", 0.0)
        lhs = float(row.dot(x)[0])
        # lhs = sum (q - spec) v  => achieved = spec + lhs / vol
        achieved = sp_.value + lhs / vol if vol > 1e-9 else None
        qual.append({"product": sp_.product, "prop": sp_.prop, "sense": sp_.sense, "spec": sp_.value,
                     "unit": sp_.unit, "achieved": achieved, "safety": sp_.safety})
    return {"crudes": crudes, "units": units, "products": products, "qualities": qual}
