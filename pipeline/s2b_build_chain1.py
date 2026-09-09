"""Step 3 of the build — chain 1, the COLLECTION entity.

    IN    inputs/factors_assumed/          dials_chain1 + dials, machine_rates, site_sorters,
                                          operating_hours, transport_modes, sites
          inputs/melbourne/<geojson>      the 411 catchment polygons
          outputs/…_chain2_observed/      Facilities, TransportationModes, SupplierCapabilities
    OUT   outputs/melbourne_optilogic_chain1/                     (17 tables)

Pickup -> round-0 sort -> terminate.
Balance: `P = Kept at depot + Vic Metro to Metro + PDO terminate + interstate + regional
pickup`, where PDO terminate is a share of the export volume that ends at the same hub.

NOTHING IS COPIED. The catchment suppliers are built from the geojson, lane distances from
haversine over those coordinates, the recipes from the stage rule, machine capacities from
machine_rates x operating_hours, and the terminate is read off chain 2's own
SupplierCapabilities. No previous build is read, so this folder rebuilds from inputs/ alone.

There is ONE path and no mode switch. Until 2026-09-08 this script also carried `filter_frozen`,
which copied rows out of an 11 August build; it went with the `CHAIN1_SRC` that pointed at it,
because a mode that copies a folder nobody can regenerate is not a way back, it is a dependency.

Converted from notebooks/melbourne-optilogic-chain1-pickup.ipynb (2026-09-07), and rewritten as
a generator (2026-09-08).

Run:  uv run python pipeline/s2b_build_chain1.py
      (or as step 3 of  uv run python pipeline/run_pipeline.py)
"""

import math
import warnings

import pandas as pd

from _paths import CHAIN1_OUT as OUT, CHAIN2_OUT, DATA_ROOT as REPO, FASS, RAW
OUT.mkdir(parents=True, exist_ok=True)

_CAST = {"int": int, "float": float, "str": str, "bool": lambda x: str(x).strip() == "True"}
_d1 = pd.read_csv(FASS / "dials_chain1.csv").set_index("parameter")
def d1(name):
    r = _d1.loc[name]
    return _CAST[r["kind"]](r["value"])

def write(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    print(f"  ✓ {name+'.csv':<32} {len(df):>7,} rows")

def largest_remainder(total, weights):
    """Split an integer across weights so the parts sum to it EXACTLY. Used everywhere a share
    meets a volume, because int(share x volume) summed over rows loses parcels silently."""
    out = {k: 0 for k in weights}
    s = sum(weights.values())
    if total <= 0 or s <= 0:
        return out
    raw = {k: total * v / s for k, v in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (raw[k] - out[k], k), reverse=True)[:total - sum(out.values())]:
        out[k] += 1
    return out

CLASSES = ("EP", "PP")
def short(node): return node.replace("HUB_", "").replace("PUD_", "")

# ══════════════════════════════════════════════════════════════════════════════════════
# GENERATE — chain 1 built from inputs/, with no 11-August artefact in the chain
# ══════════════════════════════════════════════════════════════════════════════════════
# Everything below is CONSTRUCTED. The catchment suppliers come from the geojson, the lane
# distances from haversine over those coordinates, the recipes from the stage rule, the machine
# capacities from machine_rates x operating_hours, and the terminate from chain 2's own tables.
# Nothing is copied from a previous build.
#
# THE SCHEMA IS DECLARED HERE, not read from a reference export. inputs/optilogic/ carries the
# full Anura column set; this model populates a curated subset of it, and writing the superset
# would change every table's shape. So the columns each table writes are stated once, below.

COLS = {
    "Products": ["productname", "status", "unitvolume", "unitweight", "notes"],
    "BillOfMaterials": ["bomname", "productname", "quantity", "status", "notes"],
    "ProductionPolicies": ["facilityname", "productname", "status", "productionrate",
        "ratequantityuom", "ratetimeuom", "workcentername", "unitcost", "unitcostuom", "bomname",
        "processname", "notes", "co2emissionrate", "co2emissionrateuom"],
    "Processes": ["processname", "stepname", "stepnumber", "status", "workcentername",
        "processingrate", "ratequantityuom", "ratetimeuom", "unitcost", "unitcostuom", "fixedtime",
        "fixedtimeuom", "fixedcost", "lotsize", "lotsizeuom", "yieldpercentage", "notes",
        "fixedcostrule"],
    "WorkCenters": ["workcentername", "facilityname", "status", "workcenterstatus", "initialstate",
        "throughputcapacity", "throughputcapacityuom", "fixedoperatingcost", "fixedstartupcost",
        "fixedclosingcost", "changeovername", "notes", "minimumthroughput", "minimumthroughputuom"],
    "Suppliers": ["suppliername", "status", "city", "region", "postalcode", "country", "latitude",
        "longitude", "suppliercapacity", "notes"],
    "SupplierCapabilities": ["suppliername", "productname", "status", "supplycapacity",
        "supplycapacityuom", "notes", "unitcost", "unitcostuom", "co2emissionrate",
        "co2emissionrateuom"],
    "ProcurementPolicies": ["facilityname", "productname", "sourcename", "optimizationpolicy",
        "optimizationpolicyvalue", "status", "unitcost", "unitcostuom", "maxsourcingrange",
        "maxsourcingrangeuom", "notes"],
    "ReplenishmentPolicies": ["facilityname", "productname", "sourcename", "optimizationpolicy",
        "optimizationpolicyvalue", "status", "unitcost", "unitcostuom", "maxsourcingrange",
        "maxsourcingrangeuom", "notes"],
    "Customers": ["customername", "address", "status", "city", "region", "postalcode", "country",
        "latitude", "longitude", "notes", "singlesource", "geographicriskscore",
        "userdefinedriskscore"],
    "CustomerDemand": ["customername", "productname", "periodname", "quantity", "status", "notes",
        "seasontype", "zonetype"],
    "CustomerFulfillmentPolicies": ["customername", "productname", "sourcename", "status",
        "unitcost", "unitcostuom", "maxsourcingrange", "maxsourcingrangeuom", "notes"],
    "TransportationPolicies": ["originname", "destinationname", "productname", "modename",
        "optimizationpolicy", "optimizationpolicyvalue", "status", "unitcost", "unitcostuom",
        "fixedcost", "fixedcostrule", "averageshipmentsize", "averageshipmentsizeuom",
        "productnamegroupbehavior", "dutyrate", "inventorycarryingcostpercentage",
        "transportdistance", "transportdistanceuom", "transporttime", "transporttimeuom", "notes",
        "fuelsurcharge", "fuelsurchargebasis", "co2emissionrate", "co2emissionrateuom",
        "routeplannername", "timebetweendeliveries", "timebetweendeliveriesuom"],
    "FlowConstraints": ["originname", "originnamegroupbehavior", "destinationname",
        "destinationnamegroupbehavior", "productname", "productnamegroupbehavior", "modename",
        "modenamegroupbehavior", "periodname", "periodnamegroupbehavior", "constrainttype",
        "constraintvalue", "constraintvalueuom", "status", "notes"],
    "Groups": ["groupname", "grouptype", "membername", "status", "notes"],
}
def frame(name, rows):
    """A table with its declared columns, in order, blanks where the model says nothing."""
    return pd.DataFrame(rows, columns=COLS[name]) if rows else pd.DataFrame(columns=COLS[name])

# ── THE FOUR THINGS A NAME LIST STILL HAS TO SAY ──────────────────────────────────────
# sites.csv answers "which buildings, which roles, which sort, which collect". It cannot answer
# these four, so they are declared here rather than hidden in a copied table.
#
# 1. the geojson spells a van arm differently from sites.csv's building
GEOJSON_SITE = {
    "Bayswater Van Operations":       "PUD_Bayswater",
    "Dandenong South Van Operations": "PUD_Dandenong_South",
    "Melbourne North Van Operations": "PUD_Melbourne_North",
    "Oakleigh South Van Operations":  "PUD_Oakleigh_South",
    "Sunshine West Van Services":     "PUD_Sunshine_West",
    "Dandenong Transport Facility":   "PUD_Dandenong_Transport",
    "Melbourne Transport":            "PUD_Melbourne_Transport",
}
# 2. the origin CLUSTER each collection site feeds — the token every product name carries
ORIGIN_TAG = {
    "PUD_Bayswater": "OUTER_EAST",       "PUD_Dandenong_South": "DANDENONG",
    "PUD_Melbourne_North": "NORTH",      "PUD_Oakleigh_South": "SOUTH_EAST",
    "PUD_Sunshine_West": "WEST",         "PUD_Dandenong_Transport": "DANDENONG_TR",
    "PUD_Melbourne_Transport": "INNER",
}
# 3. (was a separate CLS_HUBS list) — which hubs sort which class is now DERIVED from the
#    despatch dials, so there is one source of truth. The frozen build kept a wider list and it
#    left seven EP_*_Despatch1_TPF products with a recipe at TPF, no replenishment lane to carry
#    raw EP there, and no demand: dead in three ways at once. Generating from the despatch split
#    means the product set moves with the routing dial and nothing is stranded.
# 4. the machines a hub runs beyond its sorters, and the round-0 pair a collecting depot runs
UNLOADS = ("UNLOAD_HAND", "UNLOAD_ULD", "UNLOAD_LONGREACH")
LOADS   = ("LOAD_HAND", "LOAD_ULD", "LOAD_LONGREACH")
WORKING_DAYS = 252            # fixed cost is quoted per year and the model runs a day

def generate():
    """Build all 17 tables from inputs/. Returns the table dict the writer takes."""
    import geopandas as gpd

    # ── the network, from sites.csv ───────────────────────────────────────────────────
    sites = pd.read_csv(FASS / "sites.csv")
    NODE  = {r.node: r for r in sites.itertuples()}
    HUBS  = [r.node for r in sites.itertuples() if r.role == "hub"]
    FIRST = [r.node for r in sites.itertuples() if r.first_mile]
    SORT0 = [n for n in FIRST if NODE[n].sorts]          # collects AND has a sorter
    CODE  = {r.node: r.code for r in sites.itertuples() if isinstance(r.code, str)}
    assert set(ORIGIN_TAG) == set(FIRST), (
        f"ORIGIN_TAG names {sorted(ORIGIN_TAG)} but sites.csv collects at {sorted(FIRST)}")
    TAG_PUD = {v: k for k, v in ORIGIN_TAG.items()}
    TAGS_   = sorted(ORIGIN_TAG.values())

    # ── the dials ─────────────────────────────────────────────────────────────────────
    PEAK = {k[len("PEAK_2025_"):]: d1(k) for k in _d1.index if k.startswith("PEAK_2025_")}
    FACTOR, PP_SHARE, METRO_SH = d1("PEAK_FACTOR"), d1("PP_SHARE"), d1("METRO_PICKUP_SHARE")
    REG_HUB, MPF_SH, EP_HUB = d1("REGIONAL_PICKUP_ENTRY"), d1("INTERSTATE_PP_MPF_SHARE"), d1("INTERSTATE_EP_HUB")
    POSTURE = d1("HUB_SORTER_POSTURE")
    # the despatch split IS the hub list: a class exists at a hub because volume is routed there
    hub_split = {"PP": {"HUB_Melbourne_Parcel": MPF_SH,
                        "HUB_Tullamarine_Facility": round(1 - MPF_SH, 10)},
                 "EP": {EP_HUB: 1.0}}
    CLS_HUBS = {c: tuple(h for h, sh in hub_split[c].items() if sh > 0) for c in CLASSES}
    assert d1("REGIONAL_PICKUP_TERMINATE") == REG_HUB
    assert d1("PEAK_ROUNDING") == "floor"
    _dl = pd.read_csv(FASS / "dials.csv").set_index("parameter")
    DOCK_HEAD = float(_dl.loc["HUB_DOCK_HEADROOM", "value"])
    FAC_HEAD  = float(_dl.loc["FACILITY_HEADROOM", "value"])
    HOURS = dict(zip(*pd.read_csv(FASS / "operating_hours.csv")[["kind", "hours_per_day"]].T.values))
    MACH  = pd.read_csv(FASS / "machine_rates.csv").set_index("machine")
    SORTERS = {}
    for r in pd.read_csv(FASS / "site_sorters.csv").itertuples():
        SORTERS.setdefault(r.site, {})[r.machine] = r.rate_hr
    for h in HUBS:
        SORTERS.setdefault(h, {})["SORT_MANUAL"] = int(MACH.loc["SORT_MANUAL", "rate_hr"])
    MODES = pd.read_csv(FASS / "transport_modes.csv")
    LINEHAUL = MODES[MODES.linehaul == 1]
    VAN = MODES[MODES["mode"] == "Red_Van"].iloc[0]

    # ── chain 2 supplies the shared identity and where pickup terminates ──────────────
    _fac2 = pd.read_csv(CHAIN2_OUT / "Facilities.csv")
    assert len(_fac2), "chain 2 has not been built — run s2a first"
    XY = _fac2.set_index("facilityname")[["latitude", "longitude"]].to_dict("index")
    _c2 = pd.read_csv(CHAIN2_OUT / "SupplierCapabilities.csv")
    _c2 = _c2.assign(cls=_c2.productname.str[:2], site=_c2.suppliername.str.split("_", n=2).str[2])
    def _c2_supply(prefix):
        g = _c2[_c2.suppliername.str.startswith(prefix)]
        return {(r.site, r.cls): int(r.supplycapacity)
                for r in g.groupby(["site", "cls"], as_index=False).supplycapacity.sum().itertuples()}

    def km(a, b):
        if a == b: return 0.0
        (p, q), (r, s) = ((math.radians(XY[x]["latitude"]), math.radians(XY[x]["longitude"]))
                          for x in (a, b))
        return round(2 * 6371 * math.asin(math.sqrt(
            math.sin((r - p) / 2) ** 2 + math.cos(p) * math.cos(r) * math.sin((s - q) / 2) ** 2)), 2)

    # ══ 1. the catchments, and what each site collects ════════════════════════════════
    gj = gpd.read_file(RAW / d1("CATCHMENT_GEOJSON"))
    gj["node"] = gj.facility_name.map(GEOJSON_SITE)
    assert gj.node.notna().all(), f"geojson names a site not in GEOJSON_SITE: {sorted(set(gj.facility_name) - set(GEOJSON_SITE))}"
    # The supplier stands at the polygon's middle. geopandas warns that a centroid in a
    # geographic CRS is "likely incorrect" — true for area or distance, immaterial here: these
    # are single postcodes a few km across, and the result reproduces the 11-August build's
    # coordinates to six decimal places. Re-projecting would move every supplier for no gain.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _c = gj.geometry.centroid
    gj["lat"], gj["lon"] = _c.y, _c.x
    gj["sup"] = "SUP_PKP_" + gj.node.map(ORIGIN_TAG) + "_" + gj.post_code.astype(int).astype(str)
    NCAT = gj.node.value_counts().to_dict()

    site_tot = {p: math.floor(v * FACTOR) for p, v in PEAK.items()}
    PERCAT, PIN = {}, {}
    for p, tot in site_tot.items():
        want = {"PP": int(tot * PP_SHARE)}
        want["EP"] = tot - want["PP"]
        for c in CLASSES:
            PERCAT[(p, c)] = math.ceil(want[c] / NCAT[p] * 100) / 100
            PIN[(p, c)]    = math.floor(NCAT[p] * PERCAT[(p, c)])
    METRO_P = sum(PIN.values())
    TOTAL_P = round(METRO_P / METRO_SH)
    REG_P   = TOTAL_P - METRO_P
    REG_PIN = {"PP": int(REG_P * PP_SHARE)}
    REG_PIN["EP"] = REG_P - REG_PIN["PP"]
    REG_TAG, REG_SUP = "REGIONAL", f"SUP_PKP_REGIONAL_{CODE[REG_HUB]}"
    ALL_TAGS = TAGS_ + [REG_TAG]

    # ══ 2. products, recipes, processes, work centres ═════════════════════════════════
    def hubs_of(cls, tag):
        return (REG_HUB,) if tag == REG_TAG else CLS_HUBS[cls]
    prods, boms, prod_pol = [], [], []
    for c in CLASSES:
        for t in ALL_TAGS:
            base, reg = f"{c}_{t}", t == REG_TAG
            note = (f"regional pickup — lodged and terminated at {short(REG_HUB)}" if reg
                    else f"{{}}, {t} origin")
            prods.append({"productname": f"{base}_Pickup", "status": "Include",
                          "notes": note if reg else f"pickup, {t} origin, ALL lodgement"})
            for st in ("Unloaded", "Sorted"):
                prods.append({"productname": f"{base}_{st}", "status": "Include",
                              "notes": note if reg else f"{st}, {t} origin"})
            for h in hubs_of(c, t):
                prods.append({"productname": f"{base}_Despatch1_{CODE[h]}", "status": "Include",
                    "notes": note if reg else (f"round-1 despatch, {t} origin, sorted at {CODE[h]}"
                             " — interstate sink ONLY, no round-2 recipe consumes it")})
            # round-1: three competing unloads, one sort, three competing loads
            for m in UNLOADS:
                boms.append({"bomname": f"BOM_{m}_{base}", "productname": f"{base}_Pickup",
                    "quantity": 1, "status": "Include", "notes": note if reg else
                    f"unload on {m.split('_',1)[1]} — competing recipe (no lodgement tag to select it)"})
            boms.append({"bomname": f"BOM_SORT_{base}", "productname": f"{base}_Unloaded",
                         "quantity": 1, "status": "Include", "notes": note if reg else "round-1 sort"})
            boms.append({"bomname": f"BOM_LOAD_{base}", "productname": f"{base}_Sorted",
                "quantity": 1, "status": "Include", "notes": note if reg else
                "round-1 load -> the hub's own Despatch1 flavour. TERMINAL: no round-2 recipe "
                "consumes a pickup-origin product."})
            for h in hubs_of(c, t):
                sh = short(h)
                for m in UNLOADS:
                    prod_pol.append({"facilityname": h, "productname": f"{base}_Unloaded",
                        "status": "Include", "bomname": f"BOM_{m}_{base}",
                        "processname": f"{sh}_{m}", "notes": note if reg else
                        f"unload on {m.split('_',1)[1]} — competing recipe"})
                for m in sorted(SORTERS[h]):
                    prod_pol.append({"facilityname": h, "productname": f"{base}_Sorted",
                        "status": "Include", "bomname": f"BOM_SORT_{base}",
                        "processname": f"{sh}_{m}", "notes": note if reg else "round-1 sort"})
                for m in LOADS:
                    prod_pol.append({"facilityname": h, "productname": f"{base}_Despatch1_{CODE[h]}",
                        "status": "Include", "bomname": f"BOM_LOAD_{base}",
                        "processname": f"{sh}_{m}", "notes": note if reg else
                        "round-1 load -> own Despatch1 flavour (terminal)"})
            # round-0: only where the site both collects and owns a sorter
            if not reg and TAG_PUD[t] in SORT0:
                p, sh = TAG_PUD[t], short(TAG_PUD[t])
                for st, n in (("Unload0", "ends at CZ_LocalTerm"), ("Sort0", "ends at CZ_LocalTerm")):
                    prods.append({"productname": f"{base}_{st}", "status": "Include",
                                  "notes": f"{st}, {t} origin — {n}"})
                boms.append({"bomname": f"BOM_UNLOAD0_ALL_{base}", "productname": f"{base}_Pickup",
                    "quantity": 1, "status": "Include",
                    "notes": "round-0 bag unload at the origin PDC, ALL lodgement"})
                boms.append({"bomname": f"BOM_SORT0_{base}", "productname": f"{base}_Unload0",
                    "quantity": 1, "status": "Include",
                    "notes": "round-0 sort — the kept share TERMINATES at CZ_LocalTerm"})
                prod_pol.append({"facilityname": p, "productname": f"{base}_Unload0",
                    "status": "Include", "bomname": f"BOM_UNLOAD0_ALL_{base}",
                    "processname": f"{sh}_BAG_UNLOAD",
                    "notes": "round-0 bag unload, own catchment, ALL"})
                prod_pol.append({"facilityname": p, "productname": f"{base}_Sort0",
                    "status": "Include", "bomname": f"BOM_SORT0_{base}",
                    "processname": f"{sh}_{[m for m in SORTERS[p] if m != 'SORT_MANUAL'][0]}",
                    "notes": "round-0 sort, kept volume terminates on site"})

    # the machines each building runs, and what a day of them is worth
    machines = {h: list(UNLOADS) + sorted(SORTERS[h]) + list(LOADS) for h in HUBS}
    for p in SORT0:
        machines[p] = ["BAG_UNLOAD"] + sorted(SORTERS[p])
    def rate(site, m):
        return SORTERS.get(site, {}).get(m) or int(MACH.loc[m, "rate_hr"])
    def kind(m):
        return "UNLOAD" if "UNLOAD" in m else "LOAD" if "LOAD" in m else "SORT"

    # ══ 3. where it terminates — chain 2's own tables, so the two tie by construction ══
    MET_SITE = _c2_supply("SUP_MET_")
    STG_ALL  = _c2_supply("SUP_STAGE_")
    KEEP_PUD = {(f"PUD_{p}", c): v for (p, c), v in STG_ALL.items() if f"PUD_{p}" in PEAK}
    _offsite = sum(v for (p, c), v in STG_ALL.items() if f"PUD_{p}" not in PEAK)
    KEEP_PUD = {k: min(v, PIN[k]) for k, v in KEEP_PUD.items()}
    KEEP  = {c: sum(v for (p, cc), v in KEEP_PUD.items() if cc == c) for c in CLASSES}
    MTERM = {c: sum(v for (s, cc), v in MET_SITE.items() if cc == c) for c in CLASSES}
    _site_node = {short(n): n for n in XY}
    MTERM_SITE = {c: {_site_node[s]: v for (s, cc), v in MET_SITE.items() if cc == c and v}
                  for c in CLASSES}
    INTER = METRO_P - sum(KEEP.values()) - sum(MTERM.values())
    # PDO terminate — the second half of what used to be one interstate sink. It is metro-bound
    # volume the collection entity hands over at the hub, sized off the two families that already
    # terminate in metro, and it stands in the SAME building the interstate sink does, so the
    # split is in the ledger and not in the routing.
    PDO_F   = d1("PDO_TERMINATE_FACTOR")
    PDO_TOT = int(round(PDO_F * (sum(MTERM.values()) + sum(KEEP.values()))))
    assert PDO_TOT <= INTER, (
        f"PDO_TERMINATE_FACTOR {PDO_F} asks for {PDO_TOT:,} EA but only {INTER:,} is leaving "
        f"the hubs — the carve-out cannot be larger than the sink it comes out of")
    PDO_CLS = largest_remainder(PDO_TOT, {c: MTERM[c] + KEEP[c] for c in CLASSES})

    print(f"\n  chain 1 GENERATED from inputs/ ({d1('MODEL_BASIS')}), no previous build read:")
    print(f"    {len(gj):,} catchments from {d1('CATCHMENT_GEOJSON')[:38]}… over {len(FIRST)} sites")
    print(f"    metro pickup {METRO_P:,} ({FACTOR:.2f} x each site's own 2025 peak)"
          f"  +  regional {REG_P:,} at {short(REG_HUB)}  =  P {TOTAL_P:,}")
    print(f"    terminate: kept at depot {sum(KEEP.values()):,} | Vic Metro to Metro "
          f"{sum(MTERM.values()):,} | PDO terminate {PDO_TOT:,} | interstate {INTER - PDO_TOT:,} "
          f"| regional {REG_P:,}")
    print(f"      PDO terminate is {PDO_F:.2f} x (Vic Metro to Metro + kept at depot) carved out "
          f"of the {INTER:,} that used to leave as one interstate sink, at the same hubs")
    print(f"      sinks read off chain 2's SupplierCapabilities — "
          f"{len({s for s, _ in MET_SITE})} metro sites, {len({p for p, _ in KEEP_PUD})} depots; "
          f"chain 2 also stages {_offsite:,} EA where chain 1 never collects")

    # ══ 4. the demand rows ════════════════════════════════════════════════════════════
    rows, hub_in = [], {h: 0 for h in HUBS}
    for c in CLASSES:
        despatch = {}
        for p, t in ORIGIN_TAG.items():
            for h, q in largest_remainder(PIN[(p, c)] - KEEP_PUD.get((p, c), 0), hub_split[c]).items():
                despatch[(t, h)] = q
                hub_in[h] += q
        _left = dict(despatch)
        for s, sv in largest_remainder(MTERM[c], MTERM_SITE[c]).items():
            for (t, h), q in largest_remainder(sv, {k: v for k, v in despatch.items() if v}).items():
                q = min(q, _left[(t, h)])
                if q:
                    rows.append((f"CZ_MetroTerm_{short(s)}", f"{c}_{t}_Despatch1_{CODE[h]}", q, h,
                                 f"Vic Metro to Metro — chain 2 collects it at {short(s)}"))
                    _left[(t, h)] -= q
        # the carve-out spreads over the same (origin, hub) cells the interstate residual would
        # have taken, in proportion — a share of every cell rather than whole cells, so no origin
        # or hub stops exporting. Proportional shares cannot exceed their own cell while the
        # total is smaller than the pool, and the assert below is what says so.
        for (t, h), q in largest_remainder(
                PDO_CLS[c], {k: v for k, v in _left.items() if v}).items():
            if q:
                rows.append((f"CZ_PdoTerm_{short(h)}", f"{c}_{t}_Despatch1_{CODE[h]}", q, h,
                             "PDO terminate — metro-bound, handed over at the hub"))
                _left[(t, h)] -= q
        assert min(_left.values(), default=0) >= 0, "PDO terminate overdrew a despatch cell"
        for (t, h), q in sorted(_left.items()):
            if q:
                rows.append((f"CZ_Interstate_{short(h)}", f"{c}_{t}_Despatch1_{CODE[h]}", q, h,
                             "interstate despatch"))
    for (p, c), v in sorted(KEEP_PUD.items()):
        if v:
            st = "Sort0" if p in SORT0 else "Pickup"
            rows.append((f"CZ_LocalTerm_{short(p)}", f"{c}_{ORIGIN_TAG[p]}_{st}", v, p,
                         f"Kept at depot ({'sorted' if p in SORT0 else 'hand-sorted, no sorter here'})"))
    for c in CLASSES:
        rows.append((f"CZ_Regional_{CODE[REG_HUB]}", f"{c}_{REG_TAG}_Despatch1_{CODE[REG_HUB]}",
                     REG_PIN[c], REG_HUB, "regional pickup — starts and ends here"))
        hub_in[REG_HUB] += REG_PIN[c]
    dem = pd.DataFrame(rows, columns=["cust", "prod", "qty", "src", "note"])
    assert dem.qty.sum() == TOTAL_P, f"balance broken: {dem.qty.sum():,} vs {TOTAL_P:,}"

    # ══ 5. every table ════════════════════════════════════════════════════════════════
    T = {}
    T["Products"] = frame("Products", prods)
    T["BillOfMaterials"] = frame("BillOfMaterials", boms)
    T["ProductionPolicies"] = frame("ProductionPolicies", prod_pol)

    # suppliers: one per catchment polygon, plus the regional lodgement at its hub
    sup = [{"suppliername": r.sup, "status": "Include", "country": "Australia",
            "latitude": round(r.lat, 6), "longitude": round(r.lon, 6),
            "notes": f"catchment {int(r.post_code)}, {ORIGIN_TAG[r.node]} origin"}
           for r in gj.sort_values(["node", "post_code"]).itertuples()]
    sup.append({"suppliername": REG_SUP, "status": "Include", "country": "Australia",
                "latitude": XY[REG_HUB]["latitude"], "longitude": XY[REG_HUB]["longitude"],
                "notes": f"regional pickup, lodged at {short(REG_HUB)} (no van catchment)"})
    T["Suppliers"] = frame("Suppliers", sup)

    cap, proc_p, leg1 = [], [], []
    for r in gj.sort_values(["node", "post_code"]).itertuples():
        for c in CLASSES:
            pr = f"{c}_{ORIGIN_TAG[r.node]}_Pickup"
            cap.append({"suppliername": r.sup, "productname": pr, "status": "Include",
                        "supplycapacity": PERCAT[(r.node, c)], "supplycapacityuom": "EA",
                        "notes": "catchment pickup, ALL"})
            proc_p.append({"facilityname": r.node, "productname": pr, "sourcename": r.sup,
                           "status": "Include", "notes": "catchment pickup into its PDC"})
            d = km(r.node, r.node)  # placeholder, replaced below
    # leg 1 needs the supplier's own coordinate, which is not a facility — compute directly
    def km_pt(lat, lon, node):
        p, q = math.radians(lat), math.radians(lon)
        rr, s = math.radians(XY[node]["latitude"]), math.radians(XY[node]["longitude"])
        return round(2 * 6371 * math.asin(math.sqrt(
            math.sin((rr - p) / 2) ** 2 + math.cos(p) * math.cos(rr) * math.sin((s - q) / 2) ** 2)), 2)
    for r in gj.sort_values(["node", "post_code"]).itertuples():
        d = km_pt(r.lat, r.lon, r.node)
        for c in CLASSES:
            leg1.append({"originname": r.sup, "destinationname": r.node,
                "productname": f"{c}_{ORIGIN_TAG[r.node]}_Pickup", "modename": VAN["mode"],
                "status": "Include", "fixedcost": round(VAN.rate_per_km * d, 2),
                "fixedcostrule": "Prorate", "averageshipmentsize": float(VAN.capacity_ea),
                "averageshipmentsizeuom": "EA", "transportdistance": d,
                "transportdistanceuom": "KM", "notes": "1 pickup: catchment->PDC"})
    for c in CLASSES:
        cap.append({"suppliername": REG_SUP, "productname": f"{c}_{REG_TAG}_Pickup",
                    "status": "Include", "supplycapacity": REG_PIN[c], "supplycapacityuom": "EA",
                    "notes": "regional pickup, lodged at the hub"})
        proc_p.append({"facilityname": REG_HUB, "productname": f"{c}_{REG_TAG}_Pickup",
                       "sourcename": REG_SUP, "status": "Include",
                       "notes": "regional lodgement into the hub"})
        leg1.append({"originname": REG_SUP, "destinationname": REG_HUB,
            "productname": f"{c}_{REG_TAG}_Pickup", "modename": VAN["mode"], "status": "Include",
            "fixedcost": 0.0, "fixedcostrule": "Prorate",
            "averageshipmentsize": float(VAN.capacity_ea), "averageshipmentsizeuom": "EA",
            "transportdistance": 0.0, "transportdistanceuom": "KM",
            "notes": "1 pickup: regional lodgement, on site"})
    T["SupplierCapabilities"] = frame("SupplierCapabilities", cap)
    T["ProcurementPolicies"] = frame("ProcurementPolicies", proc_p)

    # leg 2 + the replenishment that lets raw pickup reach a hub
    rep, leg2 = [], []
    for p, t in sorted(ORIGIN_TAG.items()):
        for c in CLASSES:
            for h in CLS_HUBS[c]:
                rep.append({"facilityname": h, "productname": f"{c}_{t}_Pickup", "sourcename": p,
                            "status": "Include", "notes": "PDC->hub raw pickup"})
                d = km(p, h)
                for m in LINEHAUL.itertuples():
                    leg2.append({"originname": p, "destinationname": h,
                        "productname": f"{c}_{t}_Pickup", "modename": m.mode, "status": "Include",
                        "fixedcost": round(m.rate_per_km * d, 2), "fixedcostrule": "Prorate",
                        "averageshipmentsize": float(m.capacity_ea), "averageshipmentsizeuom": "EA",
                        "transportdistance": d, "transportdistanceuom": "KM",
                        "notes": f"2 linehaul: PDC->hub (raw pickup, {c} stream)"})
    T["ReplenishmentPolicies"] = frame("ReplenishmentPolicies", rep)

    # sinks: one customer per building the volume ends at
    SINK_AT = {}
    for r in dem.itertuples():
        SINK_AT[r.cust] = (_site_node[r.cust[len("CZ_MetroTerm_"):]]
                           if r.cust.startswith("CZ_MetroTerm_") else r.src)
    NOTE = {"CZ_I": "interstate despatch sink", "CZ_M": "Vic Metro to Metro sink",
            "CZ_L": "Kept at depot sink", "CZ_R": "regional terminate sink",
            "CZ_P": "PDO terminate sink — same hub building as the interstate sink"}
    T["Customers"] = frame("Customers", [
        {"customername": cn, "status": "Include", "country": "Australia",
         "latitude": XY[SINK_AT[cn]]["latitude"], "longitude": XY[SINK_AT[cn]]["longitude"],
         "notes": NOTE[cn[:4]]} for cn in sorted(set(dem.cust))])
    T["CustomerDemand"] = frame("CustomerDemand", [
        {"customername": r.cust, "productname": r.prod, "periodname": "ALL", "quantity": r.qty,
         "status": "Include", "notes": r.note} for r in dem.itertuples()])
    T["CustomerFulfillmentPolicies"] = frame("CustomerFulfillmentPolicies", [
        {"customername": r.cust, "productname": r.prod, "sourcename": r.src, "status": "Include",
         "notes": r.note} for r in dem.itertuples()])

    # leg 3: the sink arc. Ending in the building that sorted it costs nothing to move; ending
    # anywhere else is a real truck, priced the same way leg 2 is.
    leg3, moved = [], 0
    for r in dem.itertuples():
        d = km(r.src, SINK_AT[r.cust])
        base = {"originname": r.src, "destinationname": r.cust, "productname": r.prod,
                "status": "Include", "transportdistance": 0.0, "transportdistanceuom": "KM"}
        if d == 0:
            leg3.append({**base, "notes": "3a SINK: ends in the building that sorted it"})
            continue
        moved += r.qty
        for m in LINEHAUL.itertuples():
            leg3.append({**base, "modename": m.mode, "transportdistance": d,
                "fixedcost": round(m.rate_per_km * d, 2), "fixedcostrule": "Prorate",
                "averageshipmentsize": float(m.capacity_ea), "averageshipmentsizeuom": "EA",
                "notes": "3c linehaul: sorting hub -> the building chain 2 collects it at"})
    T["TransportationPolicies"] = frame("TransportationPolicies", leg1 + leg2 + leg3)
    T["TransportationModes"] = pd.read_csv(CHAIN2_OUT / "TransportationModes.csv")
    print(f"    Vic Metro to Metro: {moved:,} EA of the {sum(MTERM.values()):,} is collected at "
          f"a building other than the one that sorted it, so it books a leg (leg 3c)")

    # the pins
    fc = [{"originname": "Pickup_Suppliers", "originnamegroupbehavior": "Aggregate",
           "destinationname": p, "destinationnamegroupbehavior": "Aggregate",
           "productname": f"{c}_{ORIGIN_TAG.get(p, REG_TAG)}_Pickup",
           "productnamegroupbehavior": "Aggregate", "periodname": "ALL",
           "periodnamegroupbehavior": "Aggregate", "constrainttype": "Min",
           "constraintvalue": v, "status": "Include",
           "notes": "pickup pinned to catchment volume"}
          for (p, c), v in sorted(PIN.items())]
    fc += [{"originname": "Pickup_Suppliers", "originnamegroupbehavior": "Aggregate",
            "destinationname": REG_HUB, "destinationnamegroupbehavior": "Aggregate",
            "productname": f"{c}_{REG_TAG}_Pickup", "productnamegroupbehavior": "Aggregate",
            "periodname": "ALL", "periodnamegroupbehavior": "Aggregate", "constrainttype": "Min",
            "constraintvalue": REG_PIN[c], "status": "Include",
            "notes": "pickup pinned to catchment volume"} for c in CLASSES]
    T["FlowConstraints"] = frame("FlowConstraints", fc)

    grp = [{"groupname": "Pickup_Suppliers", "grouptype": "Suppliers", "membername": s,
            "status": "Include", "notes": "catchment lodgement"} for s in gj.sup]
    grp.append({"groupname": "Pickup_Suppliers", "grouptype": "Suppliers", "membername": REG_SUP,
                "status": "Include", "notes": "regional lodgement"})
    grp += [{"groupname": "HUB_Facilities", "grouptype": "Facilities", "membername": h,
             "status": "Include"} for h in sorted(HUBS)]
    grp += [{"groupname": "PDC_Facilities", "grouptype": "Facilities", "membername": r.node,
             "status": "Include"} for r in sites.itertuples() if r.role != "hub"]
    grp += [{"groupname": f"Origin_{t}", "grouptype": "Facilities", "membername": TAG_PUD[t],
             "status": "Include", "notes": f"first-mile sites for {t}"} for t in TAGS_]
    T["Groups"] = frame("Groups", grp)

    # ══ 6. machines, sized to this entity's own load ══════════════════════════════════
    load = {**hub_in, **{p: PIN[(p, "EP")] + PIN[(p, "PP")] for p in PEAK}}
    wc, procs = [], []
    for site in sorted(machines):
        sh = short(site)
        base = {m: rate(site, m) * HOURS[kind(m)] for m in machines[site]}
        for k in ("UNLOAD", "LOAD", "SORT"):
            grp_m = [m for m in machines[site] if kind(m) == k]
            if not grp_m:
                continue
            need = load.get(site, 0) * (1 + DOCK_HEAD)
            if k == "SORT" and POSTURE != "lift_to_load":
                need = 0
            scale = max(1.0, need / sum(base[m] for m in grp_m))
            for m in grp_m:
                cap_m = int(math.ceil(base[m] * scale))
                wc.append({"workcentername": f"WC_{m}_{sh}", "facilityname": site,
                    "status": "Include", "workcenterstatus": "Open", "initialstate": "Existing",
                    "throughputcapacity": cap_m, "throughputcapacityuom": "EA",
                    "fixedoperatingcost": round(int(MACH.loc[m, "fixed_yr"]) / WORKING_DAYS),
                    "notes": f"{m} at {sh}: {rate(site, m) * scale:,.1f}/hr x "
                             f"{HOURS[kind(m)]}h per period"})
                procs.append({"processname": f"{sh}_{m}", "stepname": m, "stepnumber": 1,
                    "status": "Include", "workcentername": f"WC_{m}_{sh}",
                    "processingrate": cap_m, "ratequantityuom": "EA", "ratetimeuom": "DAY",
                    "unitcost": float(MACH.loc[m, "unit_cost"]), "unitcostuom": "EA",
                    "notes": f"{m} at {sh}"})
    T["WorkCenters"] = frame("WorkCenters", wc)
    T["Processes"] = frame("Processes", procs)

    # facilities: chain 2's copy for identity, this entity's own cap where it collects
    f = _fac2.copy()
    f[["throughputcapacity", "throughputcapacityuom"]] = None
    for i, r in f.iterrows():
        if load.get(r.facilityname) and r.facilityname in PEAK:
            f.at[i, "throughputcapacity"] = math.ceil(load[r.facilityname] * (1 + FAC_HEAD))
            f.at[i, "throughputcapacityuom"] = "EA"
    T["Facilities"] = f
    return T


# ══════════════════════════════════════════════════════════════════════════════════════
T = generate()

for _n in ("Products", "BillOfMaterials", "ProductionPolicies", "Suppliers",
           "SupplierCapabilities", "ProcurementPolicies", "Customers", "CustomerDemand",
           "CustomerFulfillmentPolicies", "TransportationPolicies", "TransportationModes",
           "ReplenishmentPolicies", "FlowConstraints", "Groups", "Facilities",
           "WorkCenters", "Processes"):
    write(T[_n], _n)

_P = int(pd.to_numeric(T["FlowConstraints"].constraintvalue).sum())
_d = T["CustomerDemand"]
_by = {k: int(_d.loc[_d.customername.str.startswith(f"CZ_{k}"), "quantity"].sum())
       for k in ("Interstate", "PdoTerm", "LocalTerm", "MetroTerm", "Regional")}
print(f"\n  chain-1 balance: P {_P:,} = " + " + ".join(f"{k} {v:,}" for k, v in _by.items() if v)
      + ("   OK" if _P == sum(_by.values()) else f"   MISMATCH ({_P - sum(_by.values()):+,})"))
assert _P == sum(_by.values()), "chain-1 balance broken"
print(f"  entity: {len(T['Products'])} products, {len(T['ProductionPolicies'])} production rows, "
      f"{len(T['WorkCenters'])} work centres, "
      f"{T['Customers'].customername.nunique()} sinks — generated from inputs/")
