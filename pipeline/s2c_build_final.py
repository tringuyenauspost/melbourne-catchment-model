"""Step 4 of the build — the combiner. Chain 1 + chain 2 -> the folder Cosmic Frog takes.

    IN    outputs/melbourne_optilogic_chain1/            (step 3)
          outputs/melbourne_optilogic_chain2_observed/   (step 2)
    OUT   outputs/melbourne_optilogic_final/             (21 tables)

Four merge rules: products / suppliers / customers / lanes concatenate with a disjointness
assert; Facilities is a containment assert with chain 2's copy taken; TransportationModes is
identical by construction; WorkCenters is the one real merge — identical rows collapse, the
workload-scaled docks have their capacities summed.

STEP 5 IS NOT OPTIONAL: this step overwrites the three post-build patches every time it runs.

Converted from notebooks/melbourne-optilogic-final.ipynb (2026-09-07).

Run:  uv run python notebooks/build_final.py
      (or as step 4 of  uv run python pipeline/run_pipeline.py)
"""

# --------------------------------------------------------------------------------------
# # Melbourne — **the final Optilogic model**: chain 1 + chain 2 combined
#
# Combines the two entities into one upload folder:
#
# | entity | folder | basis |
# |---|---|---|
# | chain 1: pickup → sortation → terminate | `outputs/melbourne_optilogic_chain1` | current assumptions |
# | chain 2: sources → sort → delivery | `outputs/melbourne_optilogic_chain2_observed` | observed scan data |
#
# **Merge rules.** The entities share no products, suppliers, customers or lanes — those tables
# concatenate with a disjointness assert. `Facilities` and `TransportationModes` are identical by
# construction — asserted, then taken once. **WorkCenters is the one real merge:** the same
# physical machine serves both chains at shared sites, so identical rows collapse to one (a
# machine is not doubled by being used twice) while rows that differ — the workload-scaled docks —
# have their capacities **summed**, since each entity sized them to its own touches and the
# combined building carries both.
# --------------------------------------------------------------------------------------

from pathlib import Path
import pandas as pd

from _paths import CHAIN1_OUT as IN1, CHAIN2_OUT as IN2, DATA_ROOT as REPO, FINAL_OUT as OUT
OUT.mkdir(parents=True, exist_ok=True)
for p in (IN1, IN2):
    assert p.exists(), f"missing entity build: {p} — run its notebook first"

def r1(n): return pd.read_csv(IN1 / f"{n}.csv")
def r2(n): return pd.read_csv(IN2 / f"{n}.csv")
def write(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    print(f"  ✓ {name+'.csv':<32} {len(df):>7,} rows")

def concat_disjoint(name, key):
    a, b = r1(name), r2(name)
    dup = set(a[key]) & set(b[key])
    assert not dup, f"{name}: the entities share {key} values: {sorted(dup)[:5]}"
    write(pd.concat([a, b], ignore_index=True), name)

def concat_dedupe(name):
    write(pd.concat([r1(name), r2(name)], ignore_index=True).drop_duplicates(), name)

# ── shared identity tables: must be identical, taken once ─────────────────────────────
fac1, fac2 = r1("Facilities"), r2("Facilities")
# Change 30: chain 2 may know about buildings chain 1 does not — Avalon sorts delivery
# freight and collects no pickup, so it has no reason to appear in the collection entity.
# The guard is therefore containment, not equality: chain 1 must not invent a facility.
_only1 = set(fac1.facilityname) - set(fac2.facilityname)
assert not _only1, f"chain 1 names facilities chain 2 has never heard of: {sorted(_only1)}"
_only2 = sorted(set(fac2.facilityname) - set(fac1.facilityname))
if _only2:
    print(f"  delivery-side only (chain 2 knows them, chain 1 has no pickup there): {_only2}")
# Throughput caps: the SAME rule as WorkCenters below — each entity sized the building for its
# own touches and the real building carries both, so the caps are SUMMED wherever both entities
# declare one. Chain 1 declares a cap only for the sites it collects at (everything else is
# blanked), so a row chain 1 does not own can never be added twice. Where chain 1 declares
# nothing, chain 2's figure ships unchanged.
_cap1 = fac1.set_index("facilityname").throughputcapacity
_fac, _sum = fac2.copy(), []
for _i, _r in _fac.iterrows():
    _c1 = _cap1.get(_r.facilityname)
    if pd.notna(_c1) and pd.notna(_r.throughputcapacity):
        _tot = int(_c1) + int(_r.throughputcapacity)
        _sum.append((_r.facilityname, int(_r.throughputcapacity), int(_c1), _tot))
        _fac.at[_i, "throughputcapacity"] = _tot
if _sum:
    _ops = {r["pud"]: int(r["capacity_ea"]) for r in __import__("csv").DictReader(
        open(REPO / "inputs" / "factors_assumed" / "pud_capacity.csv", encoding="utf-8-sig"))}
    print("  throughput caps = chain-2 delivery + chain-1 collection (the building carries both):")
    for _n, _d, _c, _t in sorted(_sum, key=lambda x: -x[3]):
        _o = _ops.get(_n)
        _vs = f"   vs ops {_o:,} = {_t/_o:.0%}" if _o else ""
        print(f"    {_n:<26} {_d:>8,} + {_c:>8,} = {_t:>8,}{_vs}")
    _above = [n for n, _, _, t in _sum if _ops.get(n) and t > _ops[n]]
    if _above:
        print(f"  ABOVE the stated ops throughput at {len(_above)} depot(s) — this is the peak "
              f"basis showing through, not a build error: {', '.join(sorted(_above))}")
write(_fac, "Facilities")
tm1, tm2 = r1("TransportationModes"), r2("TransportationModes")
assert set(tm1.modename) == set(tm2.modename)
write(tm2, "TransportationModes")

# ── disjoint entity tables: concatenate with the assert ───────────────────────────────
concat_disjoint("Products", "productname")
concat_disjoint("Suppliers", "suppliername")
concat_disjoint("Customers", "customername")
for name in ("BillOfMaterials", "SupplierCapabilities", "ProcurementPolicies",
             "CustomerDemand", "CustomerFulfillmentPolicies", "ReplenishmentPolicies",
             "TransportationPolicies", "FlowConstraints", "ProductionPolicies", "Groups"):
    concat_dedupe(name)
write(r2("OriginMix"), "OriginMix")
# Change 33: the work-centre mix bounds are chain 2's, and they are OPTIONAL — written only when
# a family is bounded. They are carried, not merged: the constraint is scoped by facility and
# product group, and it counts production through the processes BOTH entities run on, so a single
# copy already covers the combined workload on a shared dock.
for _n in ("UserDefinedVariables", "UserDefinedConstraints"):
    if (IN2 / f"{_n}.csv").exists():
        write(r2(_n), _n)
    elif (OUT / f"{_n}.csv").exists():
        (OUT / f"{_n}.csv").unlink()      # same hazard as chain 2: never leave a previous
        print(f"  - {_n+'.csv':<32} removed (stale — chain-2 mix bounds are now off)")
    else:
        print(f"  - {_n+'.csv':<32} not built (chain-2 mix bounds are off)")
(OUT / "_ElapsedTimeReference.csv").unlink(missing_ok=True)   # dropped 2026-09-08, see s2a

# ── WorkCenters: the one real merge ───────────────────────────────────────────────────
# THE KEY IS THE CAPACITY, NOT THE WHOLE ROW. This used to `drop_duplicates()` across every
# column, which meant two entities describing the SAME machine collapsed only while their `notes`
# text also matched. It stopped matching the moment chain 1 became a generator and wrote its own
# wording, and fourteen physically identical machines — Bayswater's large sorter at 94,500 in
# both, Melbourne Gateway's whole dock fleet — were silently DOUBLED into capacity that does not
# exist. The distinction the merge is actually making is: one figure means both entities read the
# same machine off machine_rates x hours, so it is one machine; two different figures mean each
# sized a dock for its own workload, and the building carries both.
wc = pd.concat([r1("WorkCenters"), r2("WorkCenters")], ignore_index=True)
merged, notes = [], []
for name, g in wc.groupby("workcentername", sort=True):
    caps = sorted({int(x) for x in pd.to_numeric(g["throughputcapacity"])})
    row = g.iloc[0].copy()
    if len(caps) > 1:
        row["throughputcapacity"] = sum(caps)
        row["notes"] = str(row["notes"]) + " | capacity = chain-1 + chain-2 workloads (merged)"
        notes.append((name, caps, sum(caps)))
    else:
        row["throughputcapacity"] = caps[0]
    merged.append(row)
work_centers = pd.DataFrame(merged)
write(work_centers, "WorkCenters")
if notes:
    print("  merged work centres (both entities size the same docks — capacities summed):")
    for n, parts, tot in notes:
        print(f"    {n:<34} {' + '.join(f'{p:,}' for p in parts)} = {tot:,}")

# Processes follow the merged capacities — BUT KEEP EACH PROCESS'S RATE RELATIVE TO ITS MACHINE.
# The rule used to be `rate = merged capacity`, which is right only while every process runs at its
# machine's full rate. Change 48 broke that: the scaled round-2 clones deliberately run SLOWER than
# the machine they share, so that the parcels the model routes take the machine hours the measured
# second-sort volume would. Overwriting the rate with the capacity silently restored them to full
# speed — the unit cost still carried the scale, so the money looked right and the hours did not.
# Carrying the ratio through is a general rule, not a Change-48 special case: a process that ran at
# 1/k of its machine before the merge still runs at 1/k of the merged capacity after it, and every
# ordinary process has k = 1 and is unaffected.
# k MUST BE MEASURED AGAINST THE CHAIN THE KEPT ROW CAME FROM. `drop_duplicates(keep="first")`
# keeps chain 1's row for a process both chains define, so dividing its rate by chain 2's capacity
# is comparing across entities: on the workload-scaled docks the two capacities differ, and the
# first attempt at this rule gave k = 1.7145 for 12 processes at Melbourne Parcel and Tullamarine
# — they ran 71% too fast and round-1 machine hours fell 279 -> 253 with nothing else changed.
_caps = {"r1": dict(zip(r1("WorkCenters").workcentername,
                        pd.to_numeric(r1("WorkCenters").throughputcapacity))),
         "r2": dict(zip(r2("WorkCenters").workcentername,
                        pd.to_numeric(r2("WorkCenters").throughputcapacity)))}
proc = pd.concat([r1("Processes").assign(_src="r1"), r2("Processes").assign(_src="r2")],
                 ignore_index=True).drop_duplicates(
                     subset=[c for c in r2("Processes").columns])
cap = dict(zip(work_centers.workcentername, work_centers.throughputcapacity))
proc = proc.drop_duplicates("processname", keep="first").copy()
_rate_before = pd.to_numeric(proc.processingrate, errors="coerce")
_own_cap = pd.Series([_caps[s].get(w) for s, w in zip(proc._src, proc.workcentername)],
                     index=proc.index, dtype="float64")
_k = (_rate_before / _own_cap).where(_own_cap.gt(0), 1.0).fillna(1.0)
proc["processingrate"] = proc.workcentername.map(cap) * _k
assert ((((_k - 1).abs() < 1e-9) | (_k < 0.999))).all(), \
    "a process runs FASTER than its machine — k should be 1 for every ordinary process"
proc = proc.drop(columns="_src")
_slow = proc[_k < 0.999]
if len(_slow):
    print(f"  {len(_slow)} process(es) run slower than their machine and keep that ratio through "
          f"the merge (Change 48 round-2 clones): x{_k[_k < 0.999].min():.4f}..{_k[_k < 0.999].max():.4f}")
write(proc, "Processes")

# ── the combined balance, and the tension the combination makes visible ───────────────
d1, d2 = r1("CustomerDemand"), r2("CustomerDemand")
P = int(pd.to_numeric(r1("FlowConstraints")["constraintvalue"]).sum())
_fam1 = {k: int(d1.loc[d1.customername.str.startswith(f"CZ_{k}_"), "quantity"].sum())
         for k in ("Interstate", "PdoTerm", "MetroTerm", "LocalTerm", "Regional")}
D = int(d2["quantity"].sum())
sc2 = r2("SupplierCapabilities")
STG = int(sc2.loc[sc2.suppliername.str.startswith("SUP_STAGE_"), "supplycapacity"].sum())
# Change 46 split the one SUP_VIC_ family into SUP_MET_ (lodged in metro, delivered in
# metro) and SUP_REG_ (regional Victoria, railed in). This line still asked for SUP_VIC_,
# which no chain-2 build has written since, so it read 0 and the balance printed below
# charged the whole Victorian volume to interstate: "VIC 0 + interstate 152,613" against a
# real 75,420 + 9,328 + 67,865. Print only — no emitted table reads VIC — but the build log
# is what gets read back, so it is the two families added, and each is shown as well.
MET = int(sc2.loc[sc2.suppliername.str.startswith("SUP_MET_"), "supplycapacity"].sum())
REG = int(sc2.loc[sc2.suppliername.str.startswith("SUP_REG_"), "supplycapacity"].sum())
VIC = MET + REG
_LBL1 = {"Interstate": "interstate", "PdoTerm": "PDO terminate",
         "MetroTerm": "Vic Metro to Metro", "LocalTerm": "kept at depot",
         "Regional": "regional pickup"}
print(f"  chain 1 (assumed):  P {P:,} = "
      + " + ".join(f"{_LBL1[k]} {v:,}" for k, v in _fam1.items() if v)
      + ("" if P == sum(_fam1.values()) else f"   MISMATCH ({P - sum(_fam1.values()):+,})"))
print(f"  chain 2 (measured): D {D:,} = stage {STG:,} + VIC {VIC:,} (metro {MET:,} + regional {REG:,})"
      f" + interstate {D-STG-VIC:,}")
print()
print(f"  The two chains are INDEPENDENT daily activities — collection and delivery — so these")
print(f"  are two ledgers, not one identity, and nothing here needs to reconcile. What chain 2's")
print(f"  measurement DOES offer chain 1 is an inference: {VIC:,} EA of today's deliveries were")
print(f"  lodged in Victoria (same-day), which is delivery-side evidence about the size of the")
print(f"  collection activity chain 1 models with an assumed P = {P:,}. Use it to revisit")
print(f"  PICKUP_TOTAL and LOCAL_SHARE when the lodgement-side extract lands; until then chain 1")
print(f"  stays as assumed, deliberately.")
_zone = 167431          # temp_clustered.csv, the zone table both entities were built from
if D != _zone:
    print()
    print(f"  ONE THING THAT IS NOT INDEPENDENT — the two entities count different days.")
    print(f"  Chain 2 carries {D:,} EA (the measured peak day); chain 1's volumes come from the")
    print(f"  zone table's {_zone:,} EA, which spans every delivery date the extract touches.")
    print(f"  Any figure that adds a chain-1 number to a chain-2 number is mixing bases until")
    print(f"  the chain-1 source build is rebased too, or chain 1 is scaled by {D/_zone:.4f}.")
else:
    print()
    print(f"  Both entities are on the same base ({_zone:,} EA, the zone table), so combined")
    print(f"  totals add up. Chain 2's SPLIT is measured; its SIZE is the zone table's.")


_want = {"Facilities", "TransportationModes", "Products", "Suppliers", "Customers",
         "BillOfMaterials", "SupplierCapabilities", "ProcurementPolicies", "CustomerDemand",
         "CustomerFulfillmentPolicies", "ReplenishmentPolicies", "TransportationPolicies",
         "FlowConstraints", "ProductionPolicies", "Groups", "OriginMix", "WorkCenters",
         "Processes"}
_have = {p.stem for p in OUT.glob("*.csv")} - {"UserDefinedVariables", "UserDefinedConstraints"}
assert _want <= _have, f"missing tables: {_want - _have}"
print(f"\n  {len(_have)} tables in {OUT.name} — upload this folder to Optilogic")
