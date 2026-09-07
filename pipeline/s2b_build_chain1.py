"""Step 3 of the build — chain 1, the COLLECTION entity, under today's assumptions.

    IN    outputs/melbourne_optilogic_chain2_scan_constrained/   FROZEN, 11 Aug (the Change 27
                                                                 build, the last one in which
                                                                 both chains were built together)
    OUT   outputs/melbourne_optilogic_chain1/                    (17 tables)

Pickup -> round-0 sort -> terminate. Balance: `P = OUT + L`. Reads no inputs/ folder at all:
every row is SELECTED BY FAMILY out of the source build rather than re-derived, so "current
assumptions" means exactly the assumptions of that build.

Converted from notebooks/melbourne-optilogic-chain1-pickup.ipynb (2026-09-07).

Run:  uv run python notebooks/build_chain1_pickup.py
      (or as step 3 of  uv run python pipeline/run_pipeline.py)
"""

# --------------------------------------------------------------------------------------
# # Melbourne — **chain 1 as its own entity** (pickup → sortation → terminate)
#
# Chain 1 under **today's assumptions**, extracted from the Change 27 build
# (`outputs/melbourne_optilogic_chain2_scan_constrained/` — the last model in which both chains
# were built together under the current pickup rules). Chain 2 was rebuilt from observed data in
# `melbourne-optilogic-chain2-observed`; the two entities are combined by
# `melbourne-optilogic-final`.
#
# What this entity contains: the 7 origin clusters' pickup (catchment suppliers, pinned volumes),
# round-0 sort and the `LOCAL_KEEP_BY_PUD` terminate at the three sorting depots, round-1 sort at
# the hubs under the pinned pre-26 despatch split, and the two sinks — `CZ_Interstate_*` (export)
# and `CZ_LocalTerm_*` (kept). Its balance is `P = OUT + L`.
#
# The extraction is by construction, not by re-derivation: every row is selected from the Change 27
# tables by family, so "current assumptions" means *exactly* the assumptions of that build. If the
# Change 27 notebook is re-run with different dials, re-run this one after it.
# --------------------------------------------------------------------------------------

from pathlib import Path
import pandas as pd

from _paths import CHAIN1_OUT as OUT, CHAIN1_SRC as SRC, DATA_ROOT as REPO
OUT.mkdir(parents=True, exist_ok=True)
assert SRC.exists(), f"the Change 27 build is missing at {SRC} — run that notebook first"

def read(name):  return pd.read_csv(SRC / f"{name}.csv")
def write(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    print(f"  ✓ {name+'.csv':<32} {len(df):>7,} rows")

CLASSES = ("EP", "PP")
# the origin clusters are read off the source build's own suppliers — nothing hardcoded here
_pkp = pd.read_csv(SRC / "Suppliers.csv")
_pkp = _pkp[_pkp.suppliername.str.startswith("SUP_PKP_")]
TAGS = sorted({n[len("SUP_PKP_"):].rsplit("_", 1)[0] for n in _pkp.suppliername})
assert TAGS, "no SUP_PKP_ suppliers in the source build"
PICKUP_PREFIXES = tuple(f"{c}_{t}_" for c in CLASSES for t in TAGS)
print(f"origin clusters (from the source build): {TAGS}")
print(f"source: {SRC.name}  ({len(list(SRC.glob('*.csv')))} tables)")

# ── the product family defines the entity; everything else follows it ─────────────────
products = read("Products")
p1 = products[products.productname.str.startswith(PICKUP_PREFIXES)]
assert len(p1), "no pickup products in the source build"
write(p1, "Products")
P1 = set(p1.productname)

bom = read("BillOfMaterials")
write(bom[bom.productname.isin(P1)], "BillOfMaterials")

pp = read("ProductionPolicies")
pp1 = pp[pp.productname.isin(P1)]
write(pp1, "ProductionPolicies")

sup = read("Suppliers")
write(sup[sup.suppliername.str.startswith("SUP_PKP_")], "Suppliers")
sc = read("SupplierCapabilities")
write(sc[sc.suppliername.str.startswith("SUP_PKP_")], "SupplierCapabilities")
pc = read("ProcurementPolicies")
write(pc[pc.sourcename.str.startswith("SUP_PKP_")], "ProcurementPolicies")

# ── sinks, lanes, constraints ─────────────────────────────────────────────────────────
cust = read("Customers")
c1cust = cust[cust.customername.str.startswith(("CZ_Interstate_", "CZ_LocalTerm_"))]
write(c1cust, "Customers")
CN = set(c1cust.customername)

dem = read("CustomerDemand")
d1 = dem[dem.customername.isin(CN)]
write(d1, "CustomerDemand")
cfp = read("CustomerFulfillmentPolicies")
write(cfp[cfp.customername.isin(CN)], "CustomerFulfillmentPolicies")

tp = read("TransportationPolicies")
_leg = tp.notes.str.split(n=1).str[0]
write(tp[_leg.isin(["1", "2", "3a", "3b"])], "TransportationPolicies")
write(read("TransportationModes"), "TransportationModes")

rp = read("ReplenishmentPolicies")
write(rp[rp.productname.isin(P1)], "ReplenishmentPolicies")

fc = read("FlowConstraints")
write(fc[fc.notes.eq("pickup pinned to catchment volume")], "FlowConstraints")

grp = read("Groups")
write(grp[grp.groupname.isin(["Pickup_Suppliers", "HUB_Facilities", "PDC_Facilities"])
          | grp.groupname.str.startswith("Origin_")], "Groups")

write(read("Facilities"), "Facilities")     # shared identity — full copy, combiner dedupes

# ── work centres: exactly the machines chain-1 recipes run on ─────────────────────────
proc = read("Processes")
wc = read("WorkCenters")
used_procs = set(pp1.processname)
proc1 = proc[proc.processname.isin(used_procs)]
wc1 = wc[wc.workcentername.isin(set(proc1.workcentername))]
write(wc1, "WorkCenters")
write(proc1, "Processes")

# ── the entity's balance: P = OUT + L ─────────────────────────────────────────────────
P = int(pd.to_numeric(fc.loc[fc.notes.eq("pickup pinned to catchment volume"),
                             "constraintvalue"]).sum())
OUT_ = int(d1.loc[d1.customername.str.startswith("CZ_Interstate_"), "quantity"].sum())
L = int(d1.loc[d1.customername.str.startswith("CZ_LocalTerm_"), "quantity"].sum())
print(f"\n  chain-1 balance: P {P:,} = OUT {OUT_:,} + L {L:,}"
      + ("   OK" if P == OUT_ + L else f"   MISMATCH ({P - OUT_ - L:+,})"))
assert P == OUT_ + L, "chain-1 balance broken — the source build's pickup rules changed"
print(f"  entity: {len(p1)} products, {len(pp1)} production rows, {len(wc1)} work centres, "
      f"2 sink families — current assumptions, unmodified")

# --------------------------------------------------------------------------------------
# ## Notes
#
# - `LOCAL_KEEP_BY_PUD` (BAY 0.187 / MNP 0.102 / SWP 0.250) is **this entity's** dial — it sets
#   the Sort0 terminate `L`. Chain 2's overnight stage is a different, measured quantity and no
#   longer reads it.
# - The pickup despatch split stays pinned to the pre-Change-26 values because the scan extract is
#   delivery-side and says nothing about despatch. The lodgement-side extract (same query, selected
#   on lodgement facility) is what would turn this entity's assumptions into measurements.
# --------------------------------------------------------------------------------------
