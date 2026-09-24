"""Step 3 of the build — chain 1, the COLLECTION entity.

    IN    inputs/factors_assumed/          dials_chain1 + dials, machine_rates, site_sorters,
                                          operating_hours, transport_modes, sites,
                                          first_mile_despatch
          inputs/melbourne/<polygons.csv> the 411 catchment polygons, as WKT
          inputs/<clusters>/              OPTIONAL — first-mile route clusters instead, one
                                          collection cell per van round (PICKUP_CLUSTERS)
          outputs/…_chain2_observed/      Facilities, TransportationModes, SupplierCapabilities
    OUT   outputs/melbourne_optilogic_chain1/                     (17 tables)

Pickup -> round-0 sort -> round-1 sort -> terminate.

WHERE ROUND 1 HAPPENS IS AN INPUT. first_mile_despatch.csv gives a collecting site its own
(destination -> share) split; sites it does not name fall back to the interstate class dials, as
every site did before 2026-09-14. The destination does not have to be a hub: a transport facility
runs as a service, dropping to whichever buildings its trucks serve, and a depot with a sorter can
take that linehaul and sort it. A building is therefore a round-1 sort site because volume is
routed to it, which is the same rule the hub list already followed.
Balance: `P = Kept at depot + Vic Metro to Metro + PDO terminate + interstate + regional
pickup`, where PDO terminate is a share of the export volume that ends at the same hub.

NOTHING IS COPIED. The pickup suppliers are built from the collection cells — catchment
polygons, or the first-mile route clusters where PICKUP_CLUSTERS names them — lane distances from
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

import pandas as pd
import shapely

import _report                    # the phase report card — see _report.py
from _log import get_logger      # every message in the build goes through here
from _paths import CHAIN1_OUT as OUT, CHAIN2_OUT, FASS, INPUTS, RAW

log = get_logger(__file__)
OUT.mkdir(parents=True, exist_ok=True)

_CAST = {"int": int, "float": float, "str": str, "bool": lambda x: str(x).strip() == "True"}
_d1 = pd.read_csv(FASS / "dials_chain1.csv").set_index("parameter")
def d1(name):
    r = _d1.loc[name]
    return _CAST[r["kind"]](r["value"])

def d1_opt(name, default):
    """A dial that may be absent from an older dials_chain1.csv. Used for PICKUP_WEIGHTS, whose
    default reproduces the build that existed before it."""
    return d1(name) if name in _d1.index else default

def write(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    log.info(f"  ✓ {name+'.csv':<32} {len(df):>7,} rows")

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


def pickup_cluster_cells(folder, site_of):
    """The first-mile ROUTE CLUSTERS as collection cells — (node, cell, cell_order, lat, lon).

    The mirror of what chain 2 does with the delivery clusters in s2a: there, each last-mile
    cluster becomes one customer `CZ_<depot>_<n>` standing at its centroid; here each first-mile
    cluster becomes one supplier `SUP_PKP_<tag>_C<n>` standing at its centroid. The two files
    are the same routing run's output, one for the delivery side and one for the pickup side.

    The run writes the same pair of tables as the delivery run:
      cluster_summary.csv   one row per cluster — cluster_id, centroid_lat, centroid_lon
      temp_clustered.csv    one row per collection stop, which is the only place the run says
                            WHICH BUILDING a cluster belongs to: `cluster_id` is F<n>_<k>, and
                            the F<n> is an index into the run's own facility list, not a name.
    So the building comes from the stop table's `Facility Name`, which is the van-arm spelling
    sites.csv already carries — the same join `GEOJSON_SITE` makes for the catchment polygons.
    """
    d = INPUTS / folder
    summ = pd.read_csv(d / "cluster_summary.csv",
                       usecols=["cluster_id", "centroid_lat", "centroid_lon"])
    assert summ.cluster_id.is_unique, f"{folder}/cluster_summary.csv: duplicate cluster_id"
    stops = pd.read_csv(d / "temp_clustered.csv", usecols=["cluster_id", "Facility Name"])
    own = stops.drop_duplicates(["cluster_id", "Facility Name"])
    assert own.cluster_id.is_unique, (
        f"{folder}/temp_clustered.csv: a cluster's stops name two buildings — "
        f"{sorted(own.cluster_id[own.cluster_id.duplicated()])[:5]}")
    summ["van_arm"] = summ.cluster_id.map(dict(own[["cluster_id", "Facility Name"]].values))
    assert summ.van_arm.notna().all(), (
        f"{folder}: cluster_summary.csv has clusters temp_clustered.csv never places — "
        f"{sorted(summ.cluster_id[summ.van_arm.isna()])[:5]}")
    summ["node"] = summ.van_arm.map(site_of)
    assert summ.node.notna().all(), (
        f"{folder} names a van arm sites.csv does not have: "
        f"{sorted(set(summ.van_arm) - set(site_of))}")
    # the cell is the cluster's number WITHIN its building, so the name carries no facility
    # index that would move if the routing run reordered its facility list
    _k = summ.cluster_id.str.split("_").str[1].astype(int)
    return pd.DataFrame({"node": summ.node, "cell": "C" + _k.astype(str), "cell_order": _k,
                         "lat": summ.centroid_lat, "lon": summ.centroid_lon})

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
# Change 51: 1 and 2 were literals here AND, word for word, in chain 2. They are columns of
# sites.csv now — `van_arm` and `origin_cluster` — so the two entities cannot come to disagree
# about which building collects what, or about what the pickup it collects is called. Ordered by
# building, then by node, so the tables this drives do not reshuffle when a row moves in the
# registry — the transport facilities are services rather than buildings and sort last, which is
# how the rest of this file already treats them.
# 1. the geojson spells a van arm differently from sites.csv's building — `van_arm` says how
_SITES = pd.read_csv(FASS / "sites.csv")
_SITES = _SITES.iloc[sorted(range(len(_SITES)),
                            key=lambda i: (_SITES.role.iloc[i] == "transport",
                                           _SITES.node.iloc[i]))]
GEOJSON_SITE = {r.van_arm: r.node
                for r in _SITES[_SITES.van_arm.notna()].itertuples()}
# 2. the origin CLUSTER each collection site feeds — the token every product name carries
ORIGIN_TAG = {r.node: r.origin_cluster
              for r in _SITES[_SITES.origin_cluster.notna()].itertuples()}
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
    # the despatch split IS the destination list: a class exists at a building because volume is
    # routed there. The class dials are the DEFAULT — where a site sorts nothing itself, whatever
    # it collects is despatched on the interstate hub split.
    hub_split = {"PP": {"HUB_Melbourne_Parcel": MPF_SH,
                        "HUB_Tullamarine_Facility": round(1 - MPF_SH, 10)},
                 "EP": {EP_HUB: 1.0}}
    # first_mile_despatch.csv OVERRIDES that default per collecting site. A transport facility
    # runs as a SERVICE: it never sorts, and its trucks drop to a fixed set of buildings in fixed
    # proportions that have nothing to do with which hub despatches interstate. Only sites that
    # deviate are listed, so an unlisted site still follows the dials exactly as before.
    SITE_SPLIT = {}
    for r in pd.read_csv(FASS / "first_mile_despatch.csv").itertuples():
        SITE_SPLIT.setdefault((r.site, r.product_class), {})[r.destination] = float(r.share)
    def split_of(site, cls):
        """The (destination -> share) this site despatches on. A per-class row wins over an ALL
        row, and an unlisted site falls back to the class dials."""
        return (SITE_SPLIT.get((site, cls)) or SITE_SPLIT.get((site, "ALL")) or hub_split[cls])
    assert d1("REGIONAL_PICKUP_TERMINATE") == REG_HUB
    assert d1("PEAK_ROUNDING") == "floor"
    _dl = pd.read_csv(FASS / "dials.csv").set_index("parameter")
    DOCK_HEAD = float(_dl.loc["HUB_DOCK_BUFFER", "value"])
    FAC_HEAD  = float(_dl.loc["FACILITY_BUFFER", "value"])
    HOURS = dict(zip(*pd.read_csv(FASS / "operating_hours.csv")[["kind", "hours_per_day"]].T.values))
    MACH  = pd.read_csv(FASS / "machine_rates.csv").set_index("machine")
    SORTERS = {}
    for r in pd.read_csv(FASS / "site_sorters.csv").itertuples():
        SORTERS.setdefault(r.site, {})[r.machine] = r.rate_hr
    for h in HUBS:
        SORTERS.setdefault(h, {})["SORT_MANUAL"] = int(MACH.loc["SORT_MANUAL", "rate_hr"])
    MODES = pd.read_csv(FASS / "transport_modes.csv")
    LINEHAUL = MODES[MODES.linehaul == 1]
    # ── what each site collects ON ────────────────────────────────────────────────────
    # Leg 1 was one hardcoded Red_Van for every site. A transport facility does not run a van
    # fleet — it collects by truck — so the vehicle is an input, overrides only: a site that
    # first_mile_pickup.csv does not name still collects on PICKUP_MODE_DEFAULT.
    MODE_OF = {r.mode: r for r in MODES.itertuples()}
    DEFAULT_PICKUP = d1("PICKUP_MODE_DEFAULT")
    _fmp = pd.read_csv(FASS / "first_mile_pickup.csv")
    PICKUP_MODE = dict(_fmp[["site", "mode"]].values)
    # DIRECT: the site is a SERVICE, not a stop. Its vehicle collects at the catchment and drives
    # straight to the despatch destinations, so there is ONE leg instead of pickup-then-linehaul.
    # Nothing is unloaded, held or reloaded at the site, and it takes no throughput cap.
    DIRECT = set(_fmp.loc[_fmp.get("direct", 0).fillna(0).astype(int) == 1, "site"])
    for _s, _m in PICKUP_MODE.items():
        assert _m in MODE_OF, (
            f"first_mile_pickup.csv gives {short(_s)} the mode {_m}, which transport_modes.csv "
            f"does not define — known modes: {sorted(MODE_OF)}")
    def van_of(site):
        """The vehicle this site collects on. Named van_of for the leg it builds, not the
        vehicle: the two transport facilities collect by truck."""
        return MODE_OF[PICKUP_MODE.get(site, DEFAULT_PICKUP)]
    VAN = MODE_OF[DEFAULT_PICKUP]          # the regional lodgement, which is not a road pickup
    _byveh = {}
    for _s in FIRST:
        _byveh.setdefault(PICKUP_MODE.get(_s, DEFAULT_PICKUP), []).append(short(_s))
    if len(_byveh) > 1:
        log.info("\n  leg-1 pickup vehicle: " + " | ".join(
            f"{m} ({MODE_OF[m].capacity_ea:,} EA @ ${MODE_OF[m].rate_per_km}/km): "
            f"{', '.join(sorted(v))}" for m, v in sorted(_byveh.items())))
    assert DIRECT <= set(FIRST), (
        f"first_mile_pickup.csv marks {sorted(DIRECT - set(FIRST))} direct, but sites.csv does "
        f"not have them collecting")

    # ── where each site's collection is despatched, and which buildings therefore sort ────
    # DESTS is the whole routing of round 1: one (destination -> share) per (site, class). SORT1
    # is what falls out of it — every building that receives a round-1 linehaul and must sort it.
    # Before first_mile_despatch.csv that set was always the hubs; it can now include a depot.
    DESTS = {(p_, c): split_of(p_, c) for p_ in FIRST for c in CLASSES}
    SORT1 = sorted({h for d in DESTS.values() for h, sh in d.items() if sh > 0} | {REG_HUB})
    for (p_, c), d in DESTS.items():
        assert abs(sum(d.values()) - 1.0) < 1e-9, (
            f"first_mile_despatch.csv: {short(p_)} {c} shares sum to {sum(d.values())}, not 1.0")
    for h in SORT1:
        assert h in SORTERS and any(m != "SORT_MANUAL" for m in SORTERS[h]), (
            f"{short(h)} is a round-1 despatch destination but site_sorters.csv gives it no "
            f"sorter — a building cannot receive a linehaul it cannot sort")
        assert h in CODE, (
            f"{short(h)} is a round-1 despatch destination but has no code in sites.csv — the "
            f"Despatch1 product name is built from it")
    _off = sorted({short(h) for h in SORT1 if h not in HUBS})
    if _off:
        log.info(f"\n  round-1 sort happens OFF-HUB at {', '.join(_off)} — "
                 f"first_mile_despatch.csv routes collection there instead of to a hub")

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

    # ══ 1. the collection cells, and what each site collects ═════════════════════════
    # A CELL is the unit a site's collection is divided into: one supplier, one coordinate, one
    # leg-1 arc. Which geography the cells are is a dial.
    #
    # CATCHMENT (default) — one cell per postcode polygon the site's vans reach.
    # WKT + shapely, not .geojson + geopandas: the platform has no GDAL, and reading the file
    # and taking a centroid are shapely operations either way. utilities/convert_catchment_
    # geojson.py writes this CSV and refuses to if any centroid moves at all.
    #
    # CLUSTER — one cell per first-mile ROUTE CLUSTER, read out of the routing run's own
    # cluster_summary.csv. This is the pickup side of what chain 2 already does with the
    # delivery clusters: a cell is a van round rather than a postcode, so it stands where the
    # vehicle actually works and the cell count is the fleet, not the map. The dial names the
    # folder under inputs/; empty means catchments, so the default build is unchanged.
    CFOLD = d1_opt("PICKUP_CLUSTERS", "")
    CFOLD = "" if CFOLD.strip().lower() in ("", "nan", "none") else CFOLD.strip()
    if CFOLD:
        gj = pickup_cluster_cells(CFOLD, GEOJSON_SITE)
        CELL, CELL_SRC = "pickup cluster", f"{CFOLD}/cluster_summary.csv"
    else:
        gj = pd.read_csv(RAW / d1("CATCHMENT_POLYGONS"))
        gj["node"] = gj.facility_name.map(GEOJSON_SITE)
        assert gj.node.notna().all(), f"the polygons name a site not in GEOJSON_SITE: {sorted(set(gj.facility_name) - set(GEOJSON_SITE))}"
        # The supplier stands at the polygon's middle. A centroid in a geographic CRS is "likely
        # incorrect" for area or distance and immaterial here: these are single postcodes a few km
        # across, and the result reproduces the 11-August build's coordinates to six decimal
        # places. Re-projecting would move every supplier for no gain.
        _c = shapely.centroid(shapely.from_wkt(gj.pop("geometry")))
        gj["lat"], gj["lon"] = shapely.get_y(_c), shapely.get_x(_c)
        gj["cell_order"] = gj.post_code.astype(int)
        gj["cell"] = gj.cell_order.astype(str)
        CELL, CELL_SRC = "catchment", d1("CATCHMENT_POLYGONS")
    gj["sup"] = "SUP_PKP_" + gj.node.map(ORIGIN_TAG) + "_" + gj.cell
    assert gj.sup.is_unique, f"two cells produce the same supplier name in {CELL_SRC}"

    # ── how a site's collection splits across its cells ───────────────────────────────
    # DEFAULT: equally. Every cell a site has — each postcode it reaches, or each round it
    # runs — is modelled as collecting the same volume, which is what this build did before
    # PICKUP_WEIGHTS existed and what it still does when that dial is empty. The equal-split
    # arithmetic below is kept verbatim rather than expressed as a weight of 1/NCAT, so "off"
    # is byte-identical and not merely equal to within floating point. THIS IS THE BRANCH THE
    # CLUSTER BASIS TAKES: a route cluster carries no measured collection of its own, so a
    # site's volume is divided equally across its rounds.
    #
    # WEIGHTED: the dial names a CSV of (facility_name, post_code, weight) in inputs/melbourne/.
    # Weights are a SHAPE only — they sum to 1 within a site, so the site total, the pin, what
    # each building handles, the work centres and the facility caps are all untouched. Only the
    # per-catchment supply caps move. A catchment the file does not list is DROPPED: the weights
    # file is the authority on which cells collect anything (postcodes reached only on a
    # delivery booking, or only at another site's dock, collect nothing from the public).
    # pandas reads an empty cell as NaN and str(NaN) is the truthy "nan", so the off states are
    # spelled out rather than tested for emptiness
    WFILE = d1_opt("PICKUP_WEIGHTS", "")
    WFILE = "" if WFILE.strip().lower() in ("", "nan", "none") else WFILE.strip()
    assert not (WFILE and CFOLD), (
        f"PICKUP_WEIGHTS ({WFILE}) and PICKUP_CLUSTERS ({CFOLD}) are both set. The weights file "
        "is keyed by postcode, so it says nothing about a route cluster — set one or the other. "
        "Clusters split a site's collection EQUALLY across its rounds")
    if WFILE:
        _w = pd.read_csv(RAW / WFILE)
        _need = {"facility_name", "post_code", "weight"}
        assert _need <= set(_w.columns), f"{WFILE} needs {sorted(_need)}, has {sorted(_w.columns)}"
        _bad = _w.groupby("facility_name").weight.sum().sub(1).abs().max()
        assert _bad < 1e-6, f"{WFILE}: weights do not sum to 1 within a site (off by {_bad})"
        assert set(_w.facility_name) <= set(gj.facility_name), (
            f"{WFILE} weights a site the polygons do not have: "
            f"{sorted(set(_w.facility_name) - set(gj.facility_name))}")
        _n0 = len(gj)
        gj = gj.merge(_w[["facility_name", "post_code", "weight"]],
                      on=["facility_name", "post_code"], how="inner")
        assert len(gj) == len(_w), (
            f"{WFILE} has {len(_w)} rows but only {len(gj)} matched a polygon — a weighted "
            "(facility, postcode) is missing from CATCHMENT_POLYGONS")
        log.info(f"    pickup weights from {WFILE}: {len(gj):,} catchments carry a weight, "
                 f"{_n0 - len(gj)} dropped as collecting nothing")
    NCAT = gj.node.value_counts().to_dict()
    assert set(NCAT) >= set(PEAK), (
        f"{CELL_SRC} gives no collection cell to {sorted(short(p) for p in set(PEAK) - set(NCAT))}"
        " — every site sites.csv collects at needs somewhere to collect from")

    site_tot = {p: math.floor(v * FACTOR) for p, v in PEAK.items()}
    PERCAT, PIN = {}, {}
    for p, tot in site_tot.items():
        want = {"PP": int(tot * PP_SHARE)}
        want["EP"] = tot - want["PP"]
        cells = gj[gj.node == p]
        for c in CLASSES:
            if WFILE:
                # each catchment's own cap, rounded UP to the cent exactly as the equal split
                # rounds its single cap, so the caps still sum to at least the pin — the
                # SUP_PKP_* Site Production Capacity guard PEAK_ROUNDING relies on
                caps = {r.cell: math.ceil(r.weight * want[c] * 100) / 100
                        for r in cells.itertuples()}
                for pc_, v_ in caps.items():
                    PERCAT[(p, pc_, c)] = v_
                PIN[(p, c)] = math.floor(sum(caps.values()))
            else:
                one = math.ceil(want[c] / NCAT[p] * 100) / 100
                for pc_ in cells.cell:
                    PERCAT[(p, pc_, c)] = one
                PIN[(p, c)] = math.floor(NCAT[p] * one)
            assert sum(PERCAT[(p, r.cell, c)] for r in cells.itertuples()) \
                   >= PIN[(p, c)] - 1e-9, (
                f"{short(p)} {c}: per-cell caps sum below the pin — the supply guard is "
                "broken, so the solver could not meet the Min equality")
    METRO_P = sum(PIN.values())
    TOTAL_P = round(METRO_P / METRO_SH)
    REG_P   = TOTAL_P - METRO_P
    REG_PIN = {"PP": int(REG_P * PP_SHARE)}
    REG_PIN["EP"] = REG_P - REG_PIN["PP"]
    REG_TAG, REG_SUP = "REGIONAL", f"SUP_PKP_REGIONAL_{CODE[REG_HUB]}"
    ALL_TAGS = TAGS_ + [REG_TAG]

    # ══ 2. products, recipes, processes, work centres ═════════════════════════════════
    def hubs_of(cls, tag):
        """The buildings that sort this (class, origin) in round 1 — a hub, or a depot where
        first_mile_despatch.csv sends the collection there."""
        if tag == REG_TAG:
            return (REG_HUB,)
        return tuple(h for h, sh in DESTS[(TAG_PUD[tag], cls)].items() if sh > 0)
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

    # the machines each building runs, and what a day of them is worth. Docks and a sorter come
    # from RECEIVING a round-1 linehaul, not from being a hub: a depot that first_mile_despatch
    # sends collection to unloads, sorts and loads it exactly as a hub does. A site can be both —
    # Melbourne North collects its own catchment (round 0, bag unload) AND sorts what Melbourne
    # Transport drops (round 1, docks) — so the two lists are unioned, never overwritten.
    machines = {h: list(UNLOADS) + sorted(SORTERS[h]) + list(LOADS) for h in SORT1}
    for p in SORT0:
        machines[p] = ["BAG_UNLOAD"] + machines.get(p, sorted(SORTERS[p]))
    def rate(site, m):
        return SORTERS.get(site, {}).get(m) or int(MACH.loc[m, "rate_hr"])
    def kind(m):
        """Which operating window the machine runs in — BAG_UNLOAD is an unload shift."""
        return "UNLOAD" if "UNLOAD" in m else "LOAD" if "LOAD" in m else "SORT"
    def pool(m):
        """Which machines compete for the same work, and so may be sized as one group.
        BAG_UNLOAD is NOT one of the dock unloads even though it unloads: its only recipe is
        BOM_UNLOAD0_ALL (a van arriving from the catchment) and the dock unloads' only recipes
        are the round-1 BOM_UNLOAD_*. The volumes cannot move between them, so pooling their
        capacity would let a shortfall on one be paid for by slack on the other."""
        return "BAG" if m == "BAG_UNLOAD" else kind(m)

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

    log.info(f"\n  chain 1 GENERATED from inputs/ ({d1('MODEL_BASIS')}), no previous build read:")
    log.info(f"    {len(gj):,} {CELL} cells from {CELL_SRC[:38]}… over {len(FIRST)} sites"
             + (f", split EQUALLY per site ({'/'.join(str(NCAT[p_]) for p_ in FIRST)})"
                if CFOLD else ""))
    log.info(f"    metro pickup {METRO_P:,} ({FACTOR:.2f} x each site's own 2025 peak)"
             f"  +  regional {REG_P:,} at {short(REG_HUB)}  =  P {TOTAL_P:,}")
    log.info(f"    terminate: kept at depot {sum(KEEP.values()):,} | Vic Metro to Metro "
             f"{sum(MTERM.values()):,} | PDO terminate {PDO_TOT:,} | interstate {INTER - PDO_TOT:,} "
             f"| regional {REG_P:,}")
    log.info(f"      PDO terminate is {PDO_F:.2f} x (Vic Metro to Metro + kept at depot) carved out "
             f"of the {INTER:,} that used to leave as one interstate sink, at the same hubs")
    log.info(f"      sinks read off chain 2's SupplierCapabilities — "
             f"{len({s for s, _ in MET_SITE})} metro sites, {len({p for p, _ in KEEP_PUD})} depots; "
             f"chain 2 also stages {_offsite:,} EA where chain 1 never collects")

    # ══ 4. the demand rows ════════════════════════════════════════════════════════════
    rows, hub_in = [], {h: 0 for h in SORT1}
    for c in CLASSES:
        despatch = {}
        for p, t in ORIGIN_TAG.items():
            for h, q in largest_remainder(PIN[(p, c)] - KEEP_PUD.get((p, c), 0),
                                          DESTS[(p, c)]).items():
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
            "notes": f"{CELL} {r.cell}, {ORIGIN_TAG[r.node]} origin"}
           for r in gj.sort_values(["node", "cell_order"]).itertuples()]
    sup.append({"suppliername": REG_SUP, "status": "Include", "country": "Australia",
                "latitude": XY[REG_HUB]["latitude"], "longitude": XY[REG_HUB]["longitude"],
                "notes": f"regional pickup, lodged at {short(REG_HUB)} (no van catchment)"})
    T["Suppliers"] = frame("Suppliers", sup)

    cap, proc_p, leg1 = [], [], []
    for r in gj.sort_values(["node", "cell_order"]).itertuples():
        for c in CLASSES:
            pr = f"{c}_{ORIGIN_TAG[r.node]}_Pickup"
            cap.append({"suppliername": r.sup, "productname": pr, "status": "Include",
                        "supplycapacity": PERCAT[(r.node, r.cell, c)],
                        "supplycapacityuom": "EA",
                        "notes": f"{CELL} pickup, ALL"})
            # where the collection is DELIVERED. Normally its own depot; for a service site the
            # truck never stops there, so it is procured straight into each despatch destination.
            for _e in (hubs_of(c, ORIGIN_TAG[r.node]) if r.node in DIRECT else (r.node,)):
                proc_p.append({"facilityname": _e, "productname": pr, "sourcename": r.sup,
                    "status": "Include", "notes": f"{CELL} pickup, direct to the sorting "
                    f"building ({short(_e)}) — no stop at {short(r.node)}" if r.node in DIRECT
                    else f"{CELL} pickup into its PDC"})
    # leg 1 needs the supplier's own coordinate, which is not a facility — compute directly
    def km_pt(lat, lon, node):
        p, q = math.radians(lat), math.radians(lon)
        rr, s = math.radians(XY[node]["latitude"]), math.radians(XY[node]["longitude"])
        return round(2 * 6371 * math.asin(math.sqrt(
            math.sin((rr - p) / 2) ** 2 + math.cos(p) * math.cos(rr) * math.sin((s - q) / 2) ** 2)), 2)
    for r in gj.sort_values(["node", "cell_order"]).itertuples():
        v, direct = van_of(r.node), r.node in DIRECT
        for c in CLASSES:
            for e in (hubs_of(c, ORIGIN_TAG[r.node]) if direct else (r.node,)):
                d = km_pt(r.lat, r.lon, e)
                leg1.append({"originname": r.sup, "destinationname": e,
                    "productname": f"{c}_{ORIGIN_TAG[r.node]}_Pickup", "modename": v.mode,
                    "status": "Include", "fixedcost": round(v.rate_per_km * d, 2),
                    "fixedcostrule": "Prorate", "averageshipmentsize": float(v.capacity_ea),
                    "averageshipmentsizeuom": "EA", "transportdistance": d,
                    "transportdistanceuom": "KM",
                    "notes": (f"1 pickup: {CELL}->{short(e)} DIRECT on {v.mode} "
                              f"({short(r.node)} service, no stop)" if direct
                              else f"1 pickup: {CELL}->PDC on {v.mode}")})
    for c in CLASSES:
        cap.append({"suppliername": REG_SUP, "productname": f"{c}_{REG_TAG}_Pickup",
                    "status": "Include", "supplycapacity": REG_PIN[c], "supplycapacityuom": "EA",
                    "notes": "regional pickup, lodged at the hub"})
        proc_p.append({"facilityname": REG_HUB, "productname": f"{c}_{REG_TAG}_Pickup",
                       "sourcename": REG_SUP, "status": "Include",
                       "notes": "regional lodgement into the hub"})
        leg1.append({"originname": REG_SUP, "destinationname": REG_HUB,
            "productname": f"{c}_{REG_TAG}_Pickup", "modename": VAN.mode, "status": "Include",
            "fixedcost": 0.0, "fixedcostrule": "Prorate",
            "averageshipmentsize": float(VAN.capacity_ea), "averageshipmentsizeuom": "EA",
            "transportdistance": 0.0, "transportdistanceuom": "KM",
            "notes": "1 pickup: regional lodgement, on site"})
    T["SupplierCapabilities"] = frame("SupplierCapabilities", cap)
    T["ProcurementPolicies"] = frame("ProcurementPolicies", proc_p)

    # leg 2 + the replenishment that lets raw pickup reach a hub
    rep, leg2 = [], []
    for p, t in sorted(ORIGIN_TAG.items()):
        if p in DIRECT:
            continue          # one leg only — the truck already delivered it in leg 1
        for c in CLASSES:
            for h in hubs_of(c, t):
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
    log.info(f"    Vic Metro to Metro: {moved:,} EA of the {sum(MTERM.values()):,} is collected at "
             f"a building other than the one that sorted it, so it books a leg (leg 3c)")

    # the pins
    # The pin says "this much WAS collected from these cells". It has always named the
    # building the collection arrives at; a service site is not one, so for those the destination
    # is the GROUP of buildings its trucks deliver to. Same volume, same meaning — the pin stays
    # a statement about collection and says nothing about how it splits across the group.
    fc = [{"originname": "Pickup_Suppliers", "originnamegroupbehavior": "Aggregate",
           "destinationname": f"Entry_{ORIGIN_TAG[p]}" if p in DIRECT else p,
           "destinationnamegroupbehavior": "Aggregate",
           "productname": f"{c}_{ORIGIN_TAG.get(p, REG_TAG)}_Pickup",
           "productnamegroupbehavior": "Aggregate", "periodname": "ALL",
           "periodnamegroupbehavior": "Aggregate", "constrainttype": "Min",
           "constraintvalue": v, "status": "Include",
           "notes": (f"pickup pinned to {CELL} volume — delivered direct across "
                     f"Entry_{ORIGIN_TAG[p]}" if p in DIRECT
                     else f"pickup pinned to {CELL} volume")}
          for (p, c), v in sorted(PIN.items())]
    fc += [{"originname": "Pickup_Suppliers", "originnamegroupbehavior": "Aggregate",
            "destinationname": REG_HUB, "destinationnamegroupbehavior": "Aggregate",
            "productname": f"{c}_{REG_TAG}_Pickup", "productnamegroupbehavior": "Aggregate",
            "periodname": "ALL", "periodnamegroupbehavior": "Aggregate", "constrainttype": "Min",
            "constraintvalue": REG_PIN[c], "status": "Include",
            "notes": f"pickup pinned to {CELL} volume"} for c in CLASSES]
    T["FlowConstraints"] = frame("FlowConstraints", fc)

    grp = [{"groupname": "Pickup_Suppliers", "grouptype": "Suppliers", "membername": s,
            "status": "Include", "notes": f"{CELL} lodgement"} for s in gj.sup]
    grp.append({"groupname": "Pickup_Suppliers", "grouptype": "Suppliers", "membername": REG_SUP,
                "status": "Include", "notes": "regional lodgement"})
    grp += [{"groupname": "HUB_Facilities", "grouptype": "Facilities", "membername": h,
             "status": "Include"} for h in sorted(HUBS)]
    grp += [{"groupname": "PDC_Facilities", "grouptype": "Facilities", "membername": r.node,
             "status": "Include"} for r in sites.itertuples() if r.role != "hub"]
    grp += [{"groupname": f"Origin_{t}", "grouptype": "Facilities", "membername": TAG_PUD[t],
             "status": "Include", "notes": f"first-mile sites for {t}"} for t in TAGS_]
    grp += [{"groupname": f"Entry_{ORIGIN_TAG[p_]}", "grouptype": "Facilities", "membername": e,
             "status": "Include",
             "notes": f"where {short(p_)}'s direct pickup is delivered"}
            for p_ in sorted(DIRECT)
            for e in sorted({h for c in CLASSES for h in hubs_of(c, ORIGIN_TAG[p_])})]
    T["Groups"] = frame("Groups", grp)

    # ══ 6. machines, sized to this entity's own load ══════════════════════════════════
    # A building's day is everything that lands on it. hub_in is what round 1 despatches to it;
    # PIN is what it collects itself. Before the per-site split those two sets never overlapped,
    # so a dict merge was enough; now Melbourne North and Bayswater are in both and the merge
    # would have DROPPED the round-1 arrivals — sizing their machines and their facility cap on
    # their own collection alone.
    # What each building physically handles. A service site handles NOTHING — its trucks run
    # catchment -> sorting building without stopping — so it is left out here, which is what
    # keeps it out of `load`, out of the work centres and out of the Facilities cap.
    collected = {p_: PIN[(p_, "EP")] + PIN[(p_, "PP")] for p_ in PEAK if p_ not in DIRECT}
    load = {s_: hub_in.get(s_, 0) + collected.get(s_, 0) for s_ in set(hub_in) | set(collected)}
    wc, procs = [], []
    for site in sorted(machines):
        sh = short(site)
        # what each pool actually has to get through: the vans unload what this site collects,
        # the docks handle what round 1 sends it, and the sorter sees both.
        need_of = {"BAG": collected.get(site, 0), "UNLOAD": hub_in.get(site, 0),
                   "LOAD": hub_in.get(site, 0), "SORT": load.get(site, 0)}
        base = {m: rate(site, m) * HOURS[kind(m)] for m in machines[site]}
        for k in ("BAG", "UNLOAD", "LOAD", "SORT"):
            grp_m = [m for m in machines[site] if pool(m) == k]
            if not grp_m:
                continue
            need = need_of[k] * (1 + DOCK_HEAD)
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
    # Publish the SORT load so the combiner can size a shared sorter ONCE. A dock can be added to;
    # a sorter is one machine, and each entity sizing it for its own half and then merging the two
    # figures cannot land on the truth — see the header of s2c's sorter block.
    pd.DataFrame(sorted((f, int(v)) for f, v in load.items() if f in machines
                        and any(kind(m) == "SORT" for m in machines[f])),
                 columns=["facilityname", "sortload_ea"]).to_csv(
        OUT / "_sort_load.csv", index=False, encoding="utf-8-sig")
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
log.info(f"\n  chain-1 balance: P {_P:,} = " + " + ".join(f"{k} {v:,}" for k, v in _by.items() if v)
         + ("   OK" if _P == sum(_by.values()) else f"   MISMATCH ({_P - sum(_by.values()):+,})"))
assert _P == sum(_by.values()), "chain-1 balance broken"
log.info(f"  entity: {len(T['Products'])} products, {len(T['ProductionPolicies'])} production rows, "
         f"{len(T['WorkCenters'])} work centres, "
         f"{T['Customers'].customername.nunique()} sinks — generated from inputs/")

# The phase report card — what this step actually wrote, read back off the folder itself.
# One line here, the block in _report.py, so this file stays a builder.
_report.s2b(log=log)
