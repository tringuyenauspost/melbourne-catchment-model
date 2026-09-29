"""Cut the Network Plan scenarios (S0-S3) out of the finished baseline, one lean model each.

    IN    outputs/melbourne_optilogic_final/            read only — the baseline is never touched
          inputs/factors_assumed/scenarios.csv          what each scenario closes, opens and caps
          inputs/factors_assumed/dials_scenario.csv     the scope, the placeholders, the switches
          inputs/factors_assumed/sites.csv              coordinates and ops capacity_ea
          inputs/factors_assumed/transport_modes.csv    vehicle capacity and $/km
    OUT   outputs/melbourne_scenario_<name>[_chain1]/   one folder per scenario, Cosmic Frog tables

WHY A SMALLER MODEL AND NOT A PATCH OF THE BASELINE. The deck asks a last-mile question — which
PDC should serve each delivery zone — over five PDCs and the terminating volume only. The baseline
answers a different one: it carries interstate, the PDO split, the handover sinks, 1,022 flow
constraints, a Despatch2 per route (s3a), the no-relay rows (s3b) and pinned round-2 shares (s3c),
all written for ELEVEN depots with every zone single-sourced to its own. Close Abbotsford in there
and the products named after it, the routes into it and the pins that hold the hub shares all lose
their target. So this step reads the baseline and writes a model the question fits.

WHAT IS KEPT, AND HOW.

    zones          every CZ_PUD_<pdc>_<n> of the in-scope PDCs, with the baseline's coordinates.
    demand         summed per zone per <PP|EP>_<origin>. The ORIGIN stays in the product name
                   (MET_MPF, REG_MPF and INTERSTATE_MPF all become PP_MPF; STG_Darebin stays
                   PP_STG_Darebin) so a zone that moves PDC still has to be fed from where its
                   freight is sorted. Collapsing to plain PP/EP would let the solver swap inbound
                   supply between PDCs — even in S0 — and call the swap a saving.
    inbound        one supplier per origin, AT the round-1 sort site the flavour names (sites.csv
                   `code`), or at the depot for a STG_ flavour (pickup kept at that depot). An
                   approximation: a round-2 sort in between is not modelled.
    last mile      a LASTMILE_MODE lane from every candidate PDC to every zone, priced exactly as
                   s2a prices it (haversine km, $/km x km per trip, Prorate) — so every S0 lane is
                   the baseline's own lane, asserted below.
    chain 1        optional (CHAIN1 / --chain1). The pickup clusters the in-scope PDCs collect,
                   each cluster's baseline volume as its supply, collected on PICKUP_MODE, then
                   sent onward to one sink per hub split by chain 1's own interstate dials
                   (INTERSTATE_PP_MPF_SHARE, INTERSTATE_EP_HUB) — a proxy for where it goes next.

WHAT A SCENARIO CHANGES (scenarios.csv, one row each):

    allocation     pinned = zone and cluster may use ONLY their baseline PDC (the benchmark);
                   free   = any open in-scope PDC, and each zone single-sourced
    abbotsford     open | closed   (CLOSING_PDC)
    port_melbourne absent | consider (the solver opens it if it pays) | open
    pdc_capacity   off | pm_only | on — a Max on each PDC's DELIVERY outflow, so chain 1 pickup
                   never eats the delivery capacity it was not sized for
    growth         0 | 1 — multiply every volume by GROWTH_PP / GROWTH_EP

Every $ in dials_scenario.csv is a PLACEHOLDER and the Port Melbourne capacity and the growth are
MADE UP. Distances are straight line. Change the CSVs, not this file.

Run:  uv run python pipeline/s4a_build_scenarios.py                 every scenario, CHAIN1 from dials
      uv run python pipeline/s4a_build_scenarios.py --scenario S1_closure --chain1
      uv run python pipeline/s4a_build_scenarios.py --no-chain1
"""
import argparse
import math
import shutil

import pandas as pd

from _log import banner, get_logger, kv, table, wrap
from _paths import FASS, FINAL_OUT, OUTPUTS

log = get_logger(__file__)

BASE = FINAL_OUT
TABLES = ["Products", "Facilities", "Suppliers", "SupplierCapabilities", "Customers",
          "CustomerDemand", "CustomerFulfillmentPolicies", "ProcurementPolicies",
          "TransportationPolicies", "Groups", "FlowConstraints"]
ZONES_GROUP = "Delivery_Zones"
CAST = {"int": int, "float": float, "str": str, "list": lambda v: v.split(";")}


def read_base(name):
    return pd.read_csv(BASE / f"{name}.csv", low_memory=False, encoding="utf-8-sig")


def dials():
    d = pd.read_csv(FASS / "dials_scenario.csv")
    return {r.parameter: CAST[r.kind](r.value) for r in d.itertuples()}


def dials_chain1():
    d = pd.read_csv(FASS / "dials_chain1.csv")
    return {r.parameter: CAST[r.kind](r.value) for r in d.itertuples() if r.kind in CAST}


def km(a, b):
    """s2a's `_km`, verbatim — the S0 lanes must come out identical to the baseline's."""
    (la1, lo1), (la2, lo2) = a, b
    dphi, dlmb = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dphi/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlmb/2)**2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ── the baseline, read once ───────────────────────────────────────────────────────────────────
class Baseline:
    """Everything the scenarios need from the finished model, independent of any one scenario."""

    def __init__(self, D, C1):
        self.D = D
        sites = pd.read_csv(FASS / "sites.csv")
        self.sites = sites.set_index("node")
        self.code_node = dict(zip(sites.code.dropna(), sites.node[sites.code.notna()]))
        self.headers = {t: list(read_base(t).columns) for t in TABLES}
        scope = D["IN_SCOPE_PDCS"]
        assert D["CLOSING_PDC"] in scope, f"CLOSING_PDC {D['CLOSING_PDC']} is not in scope"

        cust = read_base("Customers")
        self.coords = {n: (float(la), float(lo)) for n, la, lo in
                       zip(cust.customername, cust.latitude, cust.longitude)}
        fac = read_base("Facilities")
        self.coords.update({n: (float(la), float(lo)) for n, la, lo in
                            zip(fac.facilityname, fac.latitude, fac.longitude)})
        self.coords[D["PM_NAME"]] = (D["PM_LAT"], D["PM_LONG"])

        # zones and their baseline PDC — read off the sourcing, not the name
        cfp = read_base("CustomerFulfillmentPolicies")
        z = cfp[cfp.customername.str.startswith("CZ_PUD_") & cfp.sourcename.isin(scope)]
        home = z.groupby("customername").sourcename.unique()
        assert home.map(len).eq(1).all(), "a baseline zone has more than one source PDC"
        self.zone_home = home.map(lambda a: a[0]).to_dict()

        # demand per zone per <PP|EP>_<origin>
        dem = read_base("CustomerDemand")
        dem = dem[dem.customername.isin(self.zone_home)].copy()
        m = dem.productname.str.extract(r"^(PP|EP)_(?:MET|REG|INTERSTATE)_([A-Z]+)_Delivered$")
        s = dem.productname.str.extract(r"^(PP|EP)_(STG_\w+?)_Delivered$")
        dem["cls"] = m[0].fillna(s[0])
        dem["origin"] = m[1].fillna(s[1])
        bad = dem[dem.origin.isna()].productname.unique()
        assert not len(bad), f"delivered products with no recognisable origin: {bad}"
        dem["product"] = dem.cls + "_" + dem.origin
        self.demand = dem.groupby(["customername", "product", "cls", "origin"],
                                  as_index=False).quantity.sum()
        self.baseline_total = int(dem.quantity.sum())

        # where each origin's supplier stands
        self.origin_node = {}
        for o in self.demand.origin.unique():
            node = f"PUD_{o[4:]}" if o.startswith("STG_") else self.code_node.get(o)
            assert node in self.coords, f"origin {o} has no building with a coordinate"
            self.origin_node[o] = node

        # the baseline last-mile lanes, for the S0 identity check
        tp = read_base("TransportationPolicies")
        lm = tp[tp.destinationname.isin(self.zone_home) & tp.originname.isin(scope)]
        self.base_lanes = lm.drop_duplicates(["originname", "destinationname"]).set_index(
            ["originname", "destinationname"])[["transportdistance", "fixedcost", "modename"]]

        # chain 1: pickup clusters the in-scope PDCs collect, and their volume
        self.collects = [p for p in scope if int(self.sites.loc[p, "first_mile"]) == 1]
        pp = read_base("ProcurementPolicies")
        pk = pp[pp.sourcename.str.startswith("SUP_PKP_") & pp.facilityname.isin(self.collects)]
        self.cluster_home = pk.drop_duplicates("sourcename").set_index(
            "sourcename").facilityname.to_dict()
        sc = read_base("SupplierCapabilities")
        sc = sc[sc.suppliername.isin(self.cluster_home)].copy()
        sc["cls"] = sc.productname.str[:2]
        self.pickup = sc.groupby(["suppliername", "cls"], as_index=False).supplycapacity.sum()
        sup = read_base("Suppliers")
        self.coords.update({n: (float(la), float(lo)) for n, la, lo in
                            zip(sup.suppliername, sup.latitude, sup.longitude)})
        pm_share = C1["INTERSTATE_PP_MPF_SHARE"]
        self.onward = {"PP": {"HUB_Melbourne_Parcel": pm_share,
                              "HUB_Tullamarine_Facility": 1 - pm_share},
                       "EP": {C1["INTERSTATE_EP_HUB"]: 1.0}}

        modes = pd.read_csv(FASS / "transport_modes.csv").set_index("mode")
        self.mode_cap = modes.capacity_ea.astype(int).to_dict()
        self.mode_rate = modes.rate_per_km.to_dict()


# ── one scenario ──────────────────────────────────────────────────────────────────────────────
def build(B, sc, chain1):
    D = B.D
    scope, closing, pm = D["IN_SCOPE_PDCS"], D["CLOSING_PDC"], D["PM_NAME"]
    free = sc.allocation == "free"
    grow = {"PP": D["GROWTH_PP"], "EP": D["GROWTH_EP"]} if int(sc.growth) else {"PP": 1, "EP": 1}
    rows = {t: [] for t in TABLES}

    # facilities: status and who is open
    status = {p: "Closed" if (p == closing and sc.abbotsford == "closed") else "Open"
              for p in scope}
    if sc.port_melbourne != "absent":
        status[pm] = "Consider" if sc.port_melbourne == "consider" else "Open"
    usable = [p for p, s in status.items() if s != "Closed"]
    handling = {p: D["PM_HANDLING_EA"] if p == pm else D["PDC_HANDLING_EA"] for p in status}
    for p, s in status.items():
        is_pm = p == pm
        rows["Facilities"].append(dict(
            facilityname=p, status="Include", facilitystatus=s,
            initialstate="Potential" if is_pm else "Existing", country="Australia",
            latitude=B.coords[p][0], longitude=B.coords[p][1],
            fixedoperatingcost=D["PM_FIXED_COST_DAY"] if is_pm else D["PDC_FIXED_COST_DAY"],
            fixedstartupcost=D["PM_STARTUP_COST"] if is_pm else "",
            notes="candidate PDC — 437 Plummer St" if is_pm else "in-scope PDC"))

    # capacity: a Max on each PDC's delivery outflow
    caps = {}
    if sc.pdc_capacity in ("on", "pm_only") and pm in status:
        caps[pm] = D["PM_CAPACITY_EA"]
    if sc.pdc_capacity == "on":
        caps.update({p: int(B.sites.loc[p, "capacity_ea"]) for p in usable if p != pm})
    for p, cap in caps.items():
        rows["FlowConstraints"].append(dict(
            originname=p, destinationname=ZONES_GROUP, destinationnamegroupbehavior="Aggregate",
            productnamegroupbehavior="Aggregate", periodname="ALL", constrainttype="Max",
            constraintvalue=cap, constraintvalueuom="EA", status="Include",
            notes="PDC delivery capacity, EA per day"))

    def lane(o, d, prod, mode, rule, speed, note):
        dist = round(km(B.coords[o], B.coords[d]), 2)
        rows["TransportationPolicies"].append(dict(
            originname=o, destinationname=d, productname=prod, modename=mode, status="Include",
            fixedcost=round(B.mode_rate[mode] * dist, 2), fixedcostrule=rule,
            averageshipmentsize=B.mode_cap[mode], averageshipmentsizeuom="EA",
            transportdistance=dist, transportdistanceuom="KM",
            transporttime=round(dist / speed, 3), transporttimeuom="HR", notes=note))

    # ── chain 2: zones, demand, sourcing, last mile ──
    dem = B.demand.copy()
    dem["qty"] = (dem.quantity * dem.cls.map(grow)).round().astype(int)
    dem = dem[dem.qty > 0]
    for z, (la, lo) in ((z, B.coords[z]) for z in sorted(B.zone_home)):
        rows["Customers"].append(dict(customername=z, status="Include", country="Australia",
                                      latitude=la, longitude=lo, singlesource="True" if free else "",
                                      notes=f"delivery zone, baseline PDC {B.zone_home[z]}"))
        rows["Groups"].append(dict(groupname=ZONES_GROUP, grouptype="Customers", membername=z,
                                   status="Include", notes="in-scope delivery zone"))
    sources = {z: (usable if free else [B.zone_home[z]]) for z in B.zone_home}
    for z, srcs in sources.items():
        assert srcs, f"{z}: no open PDC may serve it — a pinned scenario closed its home PDC"
        assert all(s in usable for s in srcs), f"{z}: pinned to a closed PDC {srcs}"
    for r in dem.itertuples():
        rows["CustomerDemand"].append(dict(customername=r.customername, productname=r.product,
                                           periodname="ALL", quantity=r.qty, status="Include"))
        for s in sources[r.customername]:
            rows["CustomerFulfillmentPolicies"].append(dict(
                customername=r.customername, productname=r.product, sourcename=s,
                status="Include", unitcost=handling[s], unitcostuom="EA",
                notes="delivered from this PDC; unitcost = PDC handling"))
    pairs = {(s, z) for z, srcs in sources.items() for s in srcs}
    zone_products = dem.groupby("customername").product.unique().to_dict()
    for s, z in sorted(pairs):
        for prod in zone_products.get(z, []):
            lane(s, z, prod, D["LASTMILE_MODE"], "Prorate", D["LASTMILE_SPEED_KMH"],
                 "last mile: PDC -> zone (STEM)")

    # inbound: one supplier per origin, able to feed every usable PDC
    for o, node in sorted(B.origin_node.items()):
        sup = f"SUP_IN_{o}"
        B.coords[sup] = B.coords[node]
        rows["Suppliers"].append(dict(suppliername=sup, status="Include", country="Australia",
                                      latitude=B.coords[node][0], longitude=B.coords[node][1],
                                      notes=f"inbound from {node}"))
        for c in ("PP", "EP"):
            prod = f"{c}_{o}"
            if prod not in set(dem["product"]):
                continue
            rows["SupplierCapabilities"].append(dict(suppliername=sup, productname=prod,
                                                     status="Include", notes="unlimited"))
            need = set(dem[dem["product"] == prod].customername)
            for p in sorted({s for z in need for s in sources[z]}):
                rows["ProcurementPolicies"].append(dict(facilityname=p, productname=prod,
                                                        sourcename=sup, status="Include"))
                if node != p:
                    lane(sup, p, prod, D["LINEHAUL_MODE"], D["LINEHAUL_RULE"],
                         D["LINEHAUL_SPEED_KMH"], f"inbound linehaul: {node} -> PDC")
                else:
                    rows["TransportationPolicies"].append(dict(
                        originname=sup, destinationname=p, productname=prod,
                        modename=D["LINEHAUL_MODE"], status="Include", transportdistance=0,
                        transportdistanceuom="KM", notes="inbound: already in the building"))
    products = sorted(dem["product"].unique())

    # ── chain 1 (optional) ──
    c1_total = 0
    if chain1:
        collectors = [p for p in usable if p in B.collects or (p == pm and D["PM_COLLECTS"])]
        assert collectors, "chain 1 is on but no open in-scope PDC collects"
        pk = B.pickup.copy()
        pk["qty"] = pk.supplycapacity * pk.cls.map(grow)
        for cl, grp in pk.groupby("suppliername"):
            home = B.cluster_home[cl]
            srcs = collectors if free else [home]
            assert all(s in usable for s in srcs), f"{cl}: pinned to a closed PDC {home}"
            rows["Suppliers"].append(dict(suppliername=cl, status="Include", country="Australia",
                                          latitude=B.coords[cl][0], longitude=B.coords[cl][1],
                                          notes=f"pickup cluster, baseline PDC {home}"))
            for r in grp.itertuples():
                prod = f"{r.cls}_Pickup"
                rows["SupplierCapabilities"].append(dict(
                    suppliername=cl, productname=prod, status="Include",
                    supplycapacity=round(r.qty, 2), supplycapacityuom="EA",
                    notes="pickup cluster volume"))
                for p in srcs:
                    rows["ProcurementPolicies"].append(dict(facilityname=p, productname=prod,
                                                            sourcename=cl, status="Include"))
                    lane(cl, p, prod, D["PICKUP_MODE"], "Prorate", D["LASTMILE_SPEED_KMH"],
                         "pickup: cluster -> PDC")
        # onward sinks, floored so the pickup supply (a ceiling) always covers them
        for cls, split in B.onward.items():
            tot = pk[pk.cls == cls].qty.sum()
            prod = f"{cls}_Pickup"
            products.append(prod)
            for hub, share in split.items():
                sink = f"CZ_Chain1_{hub.removeprefix('HUB_')}"
                B.coords[sink] = B.coords[hub]
                if sink not in {c["customername"] for c in rows["Customers"]}:
                    rows["Customers"].append(dict(customername=sink, status="Include",
                                                  country="Australia",
                                                  latitude=B.coords[hub][0],
                                                  longitude=B.coords[hub][1],
                                                  notes="chain 1 onward sink at the hub"))
                q = math.floor(tot * share)
                c1_total += q
                rows["CustomerDemand"].append(dict(customername=sink, productname=prod,
                                                   periodname="ALL", quantity=q,
                                                   status="Include"))
                for p in collectors:
                    rows["CustomerFulfillmentPolicies"].append(dict(
                        customername=sink, productname=prod, sourcename=p, status="Include",
                        unitcost=handling[p], unitcostuom="EA",
                        notes="chain 1 onward; unitcost = PDC handling"))
                    lane(p, sink, prod, D["LINEHAUL_MODE"], D["LINEHAUL_RULE"],
                         D["LINEHAUL_SPEED_KMH"], "chain 1 onward: PDC -> hub")

    for p in products:
        rows["Products"].append(dict(productname=p, status="Include",
                                     notes="chain 1 pickup" if p.endswith("_Pickup")
                                     else f"delivered, origin {p[3:]}"))

    # ── write ──
    name = sc.scenario + ("_chain1" if chain1 else "")
    out = OUTPUTS / f"melbourne_scenario_{name}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for t in TABLES:
        pd.DataFrame(rows[t]).reindex(columns=B.headers[t]).to_csv(
            out / f"{t}.csv", index=False, encoding="utf-8-sig")
    shutil.copy(BASE / "TransportationModes.csv", out / "TransportationModes.csv")

    # ── checks and report ──
    tp = pd.DataFrame(rows["TransportationPolicies"])
    if sc.allocation == "pinned" and not int(sc.growth):
        assert int(dem.qty.sum()) == B.baseline_total, "S0 delivery total drifted from baseline"
        lm = tp[tp.destinationname.isin(B.zone_home)].drop_duplicates(
            ["originname", "destinationname"]).set_index(["originname", "destinationname"])
        j = lm.join(B.base_lanes, rsuffix="_base", how="left")
        assert j.transportdistance_base.notna().all(), "an S0 lane has no baseline twin"
        assert (j.transportdistance == j.transportdistance_base).all() and \
               (j.fixedcost == j.fixedcost_base).all(), "S0 last-mile lanes differ from baseline"
    banner(log, name, sc.notes[:60])
    kv(log, "delivery zones", len(B.zone_home))
    kv(log, "delivery demand, EA", int(dem.qty.sum()),
       f"baseline {B.baseline_total:,}" + (" x growth" if int(sc.growth) else ""))
    if chain1:
        kv(log, "chain 1 onward demand, EA", int(c1_total), "pickup at the collecting PDCs")
    kv(log, "products", len(products))
    kv(log, "lanes", len(tp))
    zones_now = pd.Series(B.zone_home).value_counts()
    table(log, ["facility", "status", "baseline zones", "delivery cap", "handling $/EA"],
          [[p, status[p], int(zones_now.get(p, 0)),
            f"{caps[p]:,}" if p in caps else "—" if status[p] == "Closed" else "unlimited",
            handling[p]] for p in status])
    if sc.allocation == "pinned":
        wrap(log, "Pinned: every zone may use only its baseline PDC, so this is the benchmark "
                  "and the solver has no allocation to choose.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", nargs="+", default=["all"],
                    help="scenario names from scenarios.csv, or 'all' (default)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--chain1", dest="chain1", action="store_true", default=None,
                   help="also carry chain 1 pickup (overrides dial CHAIN1)")
    g.add_argument("--no-chain1", dest="chain1", action="store_false")
    args = ap.parse_args(argv)

    D = dials()
    chain1 = bool(D["CHAIN1"]) if args.chain1 is None else args.chain1
    scen = pd.read_csv(FASS / "scenarios.csv")
    if args.scenario != ["all"]:
        bad = set(args.scenario) - set(scen.scenario)
        assert not bad, f"no such scenario {bad} — scenarios.csv has {list(scen.scenario)}"
        scen = scen[scen.scenario.isin(args.scenario)]
    B = Baseline(D, dials_chain1())
    for sc in scen.itertuples():
        out = build(B, sc, chain1)
        log.info(f"    wrote {out}")


if __name__ == "__main__":
    main()
