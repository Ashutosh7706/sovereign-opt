"""Digital-twin mapping: solver output -> 3D scene state (refinery_3d_digital_twin guide).

Everything shown in the 3D view comes from the actual model and solver - nothing is animated
from made-up numbers:

  x* (flows, runs, sales)   -> tank / unit fill levels and pipe flow rates (particle speed)
  y* (row duals)            -> bottleneck colour: shadow price in $/bbl  (1 $k/d per kbbl/d = 1 $/bbl)
  unit on/off binaries      -> unit lit / dark

The layout mirrors the synthetic MRPL-style model in models/refinery.py: 5 crude tanks, the CDU,
Reformer, FCC, two DHDS trains and product tanks 1-7.
"""
from __future__ import annotations

import numpy as np

from .model import Model
from .models.refinery import DHDS_YIELD, FCC_YIELD, REFORMER_YIELD, RefineryParams, _slug

# $/bbl thresholds for the colour scale (cyan = slack, green / amber / red = increasingly binding)
THRESHOLDS = {"green": 0.0, "amber": 2.0, "red": 8.0}
UNITS = ["Reformer", "FCC", "DHDS-1", "DHDS-2"]


def layout(p: RefineryParams) -> dict:
    nodes = []
    for i, c in enumerate(p.crudes):
        nodes.append({"id": f"C:{c.name}", "label": f"Crude tank: {c.name}", "short": c.name, "kind": "crude",
                      "capacity": c.parcel * c.max_parcels, "slot": i, "sulfur": c.sulfur,
                      "origin": c.origin})
    nodes.append({"id": "CDU", "label": "CDU (crude distillation unit)", "short": "CDU", "kind": "cdu",
                  "capacity": p.cdu_capacity})
    for i, un in enumerate(UNITS):
        u = p.units[un]
        nodes.append({"id": un, "label": un, "short": un, "kind": "unit", "capacity": u.capacity,
                      "min_run": u.min_run, "slot": i, "shape": "column" if un in ("FCC", "Reformer") else "reactor"})
    for pn, pr in sorted(p.products.items(), key=lambda kv: kv[1].tank):
        nodes.append({"id": f"T{pr.tank}", "label": f"Tank {pr.tank}: {pn}", "short": f"T{pr.tank} {pn}",
                      "kind": "product", "capacity": pr.max_demand, "slot": pr.tank - 1, "product": pn})
    tank = {pn: f"T{pr.tank}" for pn, pr in p.products.items()}
    edges = [{"id": f"C:{c.name}->CDU", "from": f"C:{c.name}", "to": "CDU", "stream": "crude"} for c in p.crudes]
    edges += [
        {"id": "CDU->Reformer", "from": "CDU", "to": "Reformer", "stream": "naphtha"},
        {"id": "CDU->FCC", "from": "CDU", "to": "FCC", "stream": "VGO"},
        {"id": "CDU->DHDS-1", "from": "CDU", "to": "DHDS-1", "stream": "kero + gasoil"},
        {"id": "CDU->DHDS-2", "from": "CDU", "to": "DHDS-2", "stream": "kero + gasoil"},
        {"id": "FCC->DHDS-1", "from": "FCC", "to": "DHDS-1", "stream": "LCO"},
        {"id": "FCC->DHDS-2", "from": "FCC", "to": "DHDS-2", "stream": "LCO"},
        {"id": f"CDU->{tank['MS']}", "from": "CDU", "to": tank["MS"], "stream": "straight-run naphtha"},
        {"id": f"Reformer->{tank['MS']}", "from": "Reformer", "to": tank["MS"], "stream": "reformate"},
        {"id": f"FCC->{tank['MS']}", "from": "FCC", "to": tank["MS"], "stream": "FCC gasoline"},
        {"id": f"DHDS-1->{tank['HSD']}", "from": "DHDS-1", "to": tank["HSD"], "stream": "ULSD"},
        {"id": f"DHDS-2->{tank['HSD']}", "from": "DHDS-2", "to": tank["HSD"], "stream": "ULSD"},
        {"id": f"CDU->{tank['VLSFO']}", "from": "CDU", "to": tank["VLSFO"], "stream": "gasoil/VGO/residue"},
        {"id": f"CDU->{tank['ATF']}", "from": "CDU", "to": tank["ATF"], "stream": "kerosene"},
        {"id": f"CDU->{tank['LPG']}", "from": "CDU", "to": tank["LPG"], "stream": "LPG"},
        {"id": f"Reformer->{tank['LPG']}", "from": "Reformer", "to": tank["LPG"], "stream": "LPG"},
        {"id": f"FCC->{tank['LPG']}", "from": "FCC", "to": tank["LPG"], "stream": "LPG"},
        {"id": f"CDU->{tank['HSFO']}", "from": "CDU", "to": tank["HSFO"], "stream": "VGO/residue"},
        {"id": f"CDU->{tank['NAP-EXPORT']}", "from": "CDU", "to": tank["NAP-EXPORT"], "stream": "naphtha"},
    ]
    return {"nodes": nodes, "edges": edges, "thresholds": THRESHOLDS,
            "units": {"flow": "kbbl/d", "shadow_price": "$/bbl"}}


def state(model: Model, x, y, p: RefineryParams) -> dict:
    """Levels (0..1), flows (kbbl/d), shadow prices ($/bbl) and notes for every node/edge."""
    v = dict(zip(model.var_names, np.asarray(x, float)))
    g = lambda name: max(0.0, float(v.get(name, 0.0)))  # noqa: E731
    C = {c.name: _slug(c.name) for c in p.crudes}
    tank = {pn: f"T{pr.tank}" for pn, pr in p.products.items()}
    sgn = -1.0 if model.maximize else 1.0
    rows = {n: i for i, n in enumerate(model.row_names)}

    def dual(row):
        if y is None or row not in rows:
            return 0.0
        return max(0.0, sgn * float(y[rows[row]])) if model.senses[rows[row]] == "L" else \
            max(0.0, -sgn * float(y[rows[row]]))

    nodes, flows = {}, {}
    total_run = 0.0
    for c in p.crudes:
        s = C[c.name]
        run = g(f"crude[{s}]")
        total_run += run
        cap = c.parcel * c.max_parcels
        parcels = round(g(f"parcels[{s}]"))
        # with the purchase decision fixed at 0 parcels, the link row's dual is not a real
        # bottleneck (it prices crude that was never bought) - show it only for bought crude
        d = dual(f"parcel_link[{s}]") if parcels > 0 else 0.0
        nodes[f"C:{c.name}"] = {"level": run / cap if cap else 0.0, "value": run, "unit": "kbbl/d run",
                                "parcels": parcels, "dual": d,
                                "binding": "all purchased parcels used" if d > 1e-6 else
                                ("not purchased" if parcels == 0 else None)}
        flows[f"C:{c.name}->CDU"] = run
    nodes["CDU"] = {"level": total_run / p.cdu_capacity, "value": total_run, "unit": "kbbl/d crude",
                    "dual": dual("cdu_capacity"), "binding": "CDU capacity" if dual("cdu_capacity") > 1e-6 else None}
    feeds = {}
    for un in UNITS:
        s = _slug(un)
        feed = g(f"feed[{s}]")
        feeds[un] = feed
        on = round(g(f"on[{s}]")) == 1
        cap = p.units[un].capacity
        d = dual(f"cap[{s}]") if on else 0.0
        nodes[un] = {"level": feed / cap if cap else 0.0, "value": feed, "unit": "kbbl/d feed", "on": on,
                     "dual": d, "binding": f"{un} capacity" if d > 1e-6 else None}
    sumC = lambda pat: sum(g(pat.format(s=s)) for s in C.values())  # noqa: E731
    flows["CDU->Reformer"] = sumC("NAP[{s}->REF]")
    flows["CDU->FCC"] = sumC("VGO[{s}->FCC]")
    straight = sumC("KERO[{s}->DHDS]") + sumC("GO[{s}->DHDS]")
    lco = FCC_YIELD["LCO"] * feeds["FCC"]
    tot = feeds["DHDS-1"] + feeds["DHDS-2"]
    for t in ("DHDS-1", "DHDS-2"):
        share = feeds[t] / tot if tot > 1e-9 else 0.0
        flows[f"CDU->{t}"] = straight * share
        flows[f"FCC->{t}"] = lco * share
    flows[f"CDU->{tank['MS']}"] = g("SRN_to_MS")
    flows[f"Reformer->{tank['MS']}"] = g("REFORMATE_to_MS")
    flows[f"FCC->{tank['MS']}"] = g("FCCG_to_MS")
    flows[f"DHDS-1->{tank['HSD']}"] = DHDS_YIELD * feeds["DHDS-1"]
    flows[f"DHDS-2->{tank['HSD']}"] = DHDS_YIELD * feeds["DHDS-2"]
    flows[f"CDU->{tank['VLSFO']}"] = sumC("GO[{s}->VLSFO]") + sumC("VGO[{s}->VLSFO]") + sumC("RES[{s}->VLSFO]")
    flows[f"CDU->{tank['ATF']}"] = sumC("KERO[{s}->ATF]")
    flows[f"CDU->{tank['LPG']}"] = sum(g(f"crude[{C[c.name]}]") * c.yields["LPG"] for c in p.crudes)
    flows[f"Reformer->{tank['LPG']}"] = REFORMER_YIELD["LPG"] * feeds["Reformer"]
    flows[f"FCC->{tank['LPG']}"] = FCC_YIELD["LPG"] * feeds["FCC"]
    flows[f"CDU->{tank['HSFO']}"] = sumC("VGO[{s}->HSFO]") + sumC("RES[{s}->HSFO]")
    flows[f"CDU->{tank['NAP-EXPORT']}"] = sumC("NAP[{s}->EXP]")

    for pn, pr in p.products.items():
        s = _slug(pn)
        sales = g(f"sales[{s}]")
        market = dual(f"demand_max[{s}]")
        specs = [sp_ for sp_ in p.specs if sp_.product == pn]
        spec_binding = []
        for sp_ in specs:
            rn = f"spec[{s},{sp_.prop},{'max' if sp_.sense == '<=' else 'min'}]"
            if rn in rows and y is not None and abs(float(y[rows[rn]])) > 1e-6:
                spec_binding.append(f"{sp_.prop} {sp_.sense} {sp_.value:g} {sp_.unit}")
        nodes[tank[pn]] = {"level": sales / pr.max_demand if pr.max_demand else 0.0, "value": sales,
                           "unit": "kbbl/d sales", "dual": market,
                           "binding": ("market limit" if market > 1e-6 else None),
                           "quality_limited": spec_binding}
    return {"nodes": {k: _clean(v) for k, v in nodes.items()},
            "flows": {k: round(float(val), 4) for k, val in flows.items()}}


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = round(v, 4) if np.isfinite(v) else None
        else:
            out[k] = v
    return out
