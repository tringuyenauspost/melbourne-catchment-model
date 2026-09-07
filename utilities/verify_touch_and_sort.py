"""Recompute every cell of the facility-touch and sortation tables, showing its working.

    IN   outputs/run_outputs/*.csv                    the solved model
         inputs/factors_observed/obs_legs.csv         the exported measurement
         outputs/melbourne_scan_path_analysis/consignment_paths.pkl   the scan reduction

Nothing here is copied from `sankey_from_optilogic.py`; every figure is derived from the source
files again, so agreeing with that page is evidence rather than restatement. Where the two ways of
counting a thing disagree the script prints BOTH and says which is which — the drawn second
building and the solved one are the standing example, and the difference between them is a real
finding about the solve, not a rounding wobble.

Run:  uv run python utilities/verify_touch_and_sort.py
"""
import csv
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root, one up from utilities/
RUN = os.path.join(HERE, "outputs", "run_outputs")
FOBS = os.path.join(HERE, "inputs", "factors_observed")
MODEL = os.path.join(HERE, "outputs", "melbourne_optilogic_final")

# the six buildings chain 2 carries. Written here rather than read so this file can be checked
# against the model by eye; sankey_from_optilogic.py reads them off the run's product names.
CODES = ("BAY", "MGF", "MNP", "MPF", "SWP", "TPF")
FACILITY = {"BAY": "PUD_Bayswater", "MGF": "HUB_Melbourne_Gateway", "MNP": "PUD_Melbourne_North",
            "MPF": "HUB_Melbourne_Parcel", "SWP": "PUD_Sunshine_West",
            "TPF": "HUB_Tullamarine_Facility"}
CODE_OF = {v: k for k, v in FACILITY.items()}
FAMILIES = ("INTERSTATE", "MET", "REG", "STG")       # chain 2's product tokens


def rows(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def head(n, title):
    print(f"\n{'=' * 78}\n{n}. {title}\n{'=' * 78}")


# ══ 1 ══ THE MODEL'S SORTS ═══════════════════════════════════════════════════════════
def model_sorts():
    """Machine sort touches, straight off OptimizationProcessSummary.

    The rule, and it is the only rule: a row is a SORT if `currentstepname` starts with "SORT".
    WHICH sort it is comes from the product it made, not from the step name — the step is called
    e.g. `Melbourne_Parcel_SORT_AUTO_LGE` in every round.

        product ends "Sort0"    -> round-0 sort  (chain 1, the origin PDC's bag sort)
        product ends "Sorted2"  -> round-2 sort
        anything else           -> round-1 sort

    Chain 2 names its family in the product (`PP_INTERSTATE_...`, `EP_MET_...`); chain 1 carries a
    collection catchment instead (`PP_WEST_...`), so the family token is the test.
    """
    head(1, "MODEL SORTS — outputs/run_outputs/OptimizationProcessSummary.csv")
    agg, by_site, seen = defaultdict(float), defaultdict(float), 0
    for r in rows(os.path.join(RUN, "OptimizationProcessSummary.csv")):
        q = float(r["processedquantity"] or 0)
        if q <= 0 or not (r["currentstepname"] or "").startswith("SORT"):
            continue
        seen += 1
        p = r["productname"] or ""
        rnd = "round0" if p.endswith("Sort0") else "round2" if p.endswith("Sorted2") else "round1"
        chain = "chain2" if any(f"_{k}_" in p for k in FAMILIES) else "chain1"
        agg[(chain, rnd)] += q
        if chain == "chain2" and rnd == "round2":
            by_site[r["facilityname"]] += q
    print(f"  {seen} SORT rows with processedquantity > 0")
    for k in sorted(agg):
        print(f"     {k[0]:<7} {k[1]:<7} {agg[k]:>10,.0f} EA")
    r1, r2 = agg[("chain2", "round1")], agg[("chain2", "round2")]
    c1 = agg[("chain1", "round0")] + agg[("chain1", "round1")]
    print(f"\n  ROUND-1 SORTS (chain 2) = {r1:>10,.0f}   <- table row 1")
    print(f"  ROUND-2 SORTS (chain 2) = {r2:>10,.0f}   <- table row 2   "
          f"({r2 / r1:.1%} of round 1)")
    print(f"  CHAIN-1 PICKUP SORTS    = {c1:>10,.0f}   <- table row 4   "
          f"(round-0 {agg[('chain1', 'round0')]:,.0f} + round-1 {agg[('chain1', 'round1')]:,.0f})")
    print("\n  round-2 by the site that DID the sorting (the per-site table's model column):")
    for f in sorted(by_site, key=lambda x: -by_site[x]):
        print(f"     {f:<28} {by_site[f]:>9,.0f}")
    print(f"     {'TOTAL':<28} {sum(by_site.values()):>9,.0f}")
    return r1, r2, c1, by_site


# ══ 2 ══ THE MODEL'S SECOND BUILDINGS ════════════════════════════════════════════════
def model_buildings(r2):
    """Two different numbers, and the difference between them is the point.

    A parcel is in a SECOND building whenever a truck carried it from one of our buildings to
    another. Two mechanisms do that and they cost different amounts of work:

      ROUND-2 LEG   a `..._Despatch1_<SITE>` product moving between two buildings. `Despatch1` is
                    a MID-state product: nothing consumes it but UNLOAD2 -> SORT2, so arriving
                    anywhere forces a sort. Its inter-building flow IS the round-2 sort count.

      RELAY         a `..._Despatch2`/`..._Despatch2R` product that arrives at a building and
                    leaves again — BOTH halves of the 2 Sep split. That is
                    finished freight; the driver wave is the only step left, so the building never
                    opens it. `min(in, out)` per (building, product) is that volume exactly.

    The page's own pass-through block detects only part of the relay, so "as drawn" and "honest"
    differ. Both are printed; the honest one is the second-building count.
    """
    head(2, "MODEL SECOND BUILDINGS — outputs/run_outputs/OptimizationFlowSummary.csv")
    B = set(FACILITY.values())
    legs = defaultdict(float)
    inn, out, src = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in rows(os.path.join(RUN, "OptimizationFlowSummary.csv")):
        q = float(r["flowquantity"] or 0)
        p, o, d = r["productname"], r["originname"], r["destinationname"]
        if q <= 0:
            continue
        if "_Despatch1_" in p and o in B and d in B:
            legs[(o, d)] += q
        if p.endswith(("Despatch2", "Despatch2R")):
            if d.startswith(("PUD_", "HUB_")):
                inn[(d, p)] += q
                src[(d, p, o)] += q
            if o.startswith(("PUD_", "HUB_")):
                out[(o, p)] += q
    r2_leg = sum(legs.values())
    print(f"  a) Despatch1 legs between two of the six buildings: {r2_leg:>9,.0f} EA")
    print(f"     identical to the round-2 sort count above ({r2:,.0f}) — the same movement counted")
    print(f"     at the truck instead of at the machine. {'AGREES' if abs(r2_leg - r2) < 2 else 'DISAGREES'}")

    # the relay, split by whether the relaying site is the parcel's 2nd building or a later one
    second, third = 0.0, 0.0
    for (site, p), v in out.items():
        rel = min(v, inn.get((site, p), 0.0))
        if rel <= 1e-6:
            continue
        # the product flavour names the site that SORTED it; if the truck came from there, the
        # relaying site is building number two. If not, the parcel had already moved once.
        m = re.search(r"_(?:INTERSTATE|MET|REG|STG)_([A-Z]{3})_Despatch2R?$", p)
        sorted_at = FACILITY.get(m.group(1)) if m else None
        for (dd, pp, o), sv in src.items():
            if (dd, pp) != (site, p):
                continue
            share = rel * sv / inn[(site, p)]
            if o == sorted_at and o in B and site in B:
                second += share
            elif o != sorted_at:
                third += share
    print(f"\n  b) relay, min(in,out) per (building, Despatch2 product):")
    print(f"       reaching a SECOND building : {second:>9,.0f} EA   <- counts here")
    print(f"       reaching a THIRD  building : {third:>9,.0f} EA   (depth-2 cannot hold it)")
    honest = r2_leg + second
    print(f"\n  SECOND BUILDING, HONEST = {r2_leg:,.0f} + {second:,.0f} = {honest:>9,.0f} EA")
    return honest, second, third, legs


# ══ 3 ══ THE DENOMINATOR ═════════════════════════════════════════════════════════════
def model_base():
    """Same-day freight: everything delivered, minus what never left its depot.

    The driver wave delivers EVERYTHING — same-day and staged alike — so it is the total. Staged
    freight (`_STG_`) was sorted yesterday, rides no sort lane today and has no first building, so
    it comes out of both sides of every share.
    """
    head(3, "MODEL BASE — the same-day denominator")
    delivered, stage = 0.0, 0.0
    for r in rows(os.path.join(RUN, "OptimizationProcessSummary.csv")):
        q = float(r["processedquantity"] or 0)
        if q > 0 and (r["currentstepname"] or "") == "DRIVER_WAVE":
            delivered += q
            if "_STG_" in (r["productname"] or ""):
                stage += q
    print(f"  DRIVER_WAVE throughput, all products    = {delivered:>10,.0f} EA  (everything delivered)")
    print(f"  of which _STG_ (kept at depot overnight) = {stage:>10,.0f} EA")
    print(f"  SAME-DAY BASE                            = {delivered - stage:>10,.0f} EA   <- every model share divides by this")
    return delivered - stage, delivered, stage


# ══ 4 ══ THE EXPORT ══════════════════════════════════════════════════════════════════
def export_side():
    """obs_legs.csv, restricted to the six buildings the model has, so the shares are like for like.

    One row is one (family, class, entry building, destination) cell. `dest == "ONCE"` means the
    parcel finished in the building it entered; any other dest is a second building — and on the
    facility-path basis that one row covers both a cross-dock hop and a round-2 leg, because the
    measurement cannot tell them apart and does not need to.
    """
    head(4, "EXPORT — inputs/factors_observed/obs_legs.csv")
    legs = rows(os.path.join(FOBS, "obs_legs.csv"))
    allt = sum(float(r["articles"]) for r in legs)
    print(f"  {len(legs)} rows, {allt:,.0f} EA over "
          f"{len({r['entry'] for r in legs})} entry buildings")
    six = [r for r in legs if r["entry"] in CODES]
    base = sum(float(r["articles"]) for r in six)
    two = sum(float(r["articles"]) for r in six if r["dest"] != "ONCE")
    print(f"  entry in {list(CODES)}:")
    print(f"     BASE  (all rows)              = {base:>9,.0f} EA   <- the export denominator")
    print(f"     2nd building (dest != ONCE)   = {two:>9,.0f} EA")
    print(f"     1 building   (dest == ONCE)   = {base - two:>9,.0f} EA")
    print(f"     buildings/parcel = ({base:,.0f} + {two:,.0f}) / {base:,.0f} = {(base + two) / base:.3f}")
    print(f"\n  outside those six buildings: {allt - base:,.0f} EA at "
          f"{sorted({r['entry'] for r in legs if r['entry'] not in CODES})}")
    by_dest = defaultdict(float)
    for r in legs:
        if r["dest"] != "ONCE":
            by_dest[r["dest"]] += float(r["articles"])
    print("\n  2nd-building arrivals by DESTINATION (the per-site table's export column):")
    for k in sorted(by_dest, key=lambda x: -by_dest[x]):
        print(f"     {k:<6} {by_dest[k]:>9,.0f}")
    print(f"     {'TOTAL':<6} {sum(by_dest.values()):>9,.0f}")
    return base, two, by_dest


# ══ 5 ══ THE MEASUREMENT, FROM THE REDUCTION ═════════════════════════════════════════
def measured():
    """The scan reduction itself — the only place the SORT count lives.

    obs_legs is on the facility-path basis and counts BUILDINGS, so it cannot answer "how many
    times was this sorted". `nrounds` can: it is the number of machine-sort chain entries the scans
    record for that consignment.
    """
    head(5, "MEASURED — outputs/melbourne_scan_path_analysis/consignment_paths.pkl")
    import sys
    sys.path.insert(0, os.path.join(HERE, "model_input_preparation"))   # where the exporter lives
    from export_chain2_factors import cohort, load_paths
    q = cohort(load_paths().set_index("Consignment_ID"))
    a = q.articles
    print(f"  cohort                                     {a.sum():>10,.0f} EA")
    st = a[q.fam == "METRO_DEPOT"].sum()
    print(f"  - METRO_DEPOT (kept at depot, rides no lane) {-st:>9,.0f} EA")
    sd = q.fam != "METRO_DEPOT"
    A, nr = a[sd], q.nrounds[sd]
    print(f"  = SAME-DAY                                 {A.sum():>10,.0f} EA   <- measured denominator")
    print(f"\n  by machine-sort rounds:")
    for k in (0, 1, 2):
        v = A[nr == k].sum()
        print(f"     {k} sorts   {v:>10,.0f}   {v / A.sum():6.2%}")
    v3 = A[nr >= 3].sum()
    print(f"     3+ sorts  {v3:>10,.0f}   {v3 / A.sum():6.2%}")
    r1 = A[nr >= 1].sum()
    r2 = A[nr >= 2].sum()
    print(f"\n  MEASURED ROUND-1 (>=1 sort)  = {r1:>10,.0f}   <- sorts table, row 1")
    print(f"  MEASURED ROUND-2 (>=2 sorts) = {r2:>10,.0f}   <- sorts table, row 2   "
          f"({r2 / A.sum():.1%} of same-day)")
    n = q.path_sites.apply(len)[sd]
    print(f"\n  and the trap this number used to fall into — buildings vs sorts:")
    print(f"     >=2 sorts AND >=2 buildings : {A[(nr >= 2) & (n >= 2)].sum():>9,.0f}"
          f"   (what a '>=2 buildings' gate keeps)")
    print(f"     >=2 sorts but  <2 buildings : {A[(nr >= 2) & (n < 2)].sum():>9,.0f}"
          f"   (a 2nd sort at the DELIVERING depot,")
    print(f"                                              which the capped path drops)")
    return r1, r2, A.sum()


def main():
    r1, r2, c1, by_site = model_sorts()
    honest, second, third, legs = model_buildings(r2)
    base, delivered, stage = model_base()
    e_base, e_two, e_dest = export_side()
    m_r1, m_r2, m_base = measured()

    head(6, "THE TABLES")
    print("  FACILITY TOUCHES")
    print(f"    {'':<34}{'model':>12}{'export':>12}")
    print(f"    {'base, same-day':<34}{base:>12,.0f}{e_base:>12,.0f}")
    print(f"    {'2nd building (honest)':<34}{honest:>12,.0f}{e_two:>12,.0f}")
    print(f"    {'   as a share of the base':<34}{honest / base:>11.1%}{e_two / e_base:>12.1%}")
    print(f"    {'1 building = base - 2nd':<34}{base - honest:>12,.0f}{e_base - e_two:>12,.0f}")
    print(f"    {'   as a share of the base':<34}{(base - honest) / base:>11.1%}"
          f"{(e_base - e_two) / e_base:>12.1%}")
    print(f"    {'buildings/parcel = 1 + 2nd/base':<34}{1 + honest / base:>12.3f}"
          f"{1 + e_two / e_base:>12.3f}")
    print("\n  SORTS")
    print(f"    {'':<34}{'model':>12}{'measured':>12}")
    print(f"    {'base, same-day':<34}{base:>12,.0f}{m_base:>12,.0f}")
    print(f"    {'round-1 sorts':<34}{r1:>12,.0f}{m_r1:>12,.0f}")
    print(f"    {'round-2 sorts':<34}{r2:>12,.0f}{m_r2:>12,.0f}")
    print(f"    {'   as a share of the base':<34}{r2 / base:>11.1%}{m_r2 / m_base:>12.1%}")
    print(f"    {'gap, model - measured':<34}{r2 - m_r2:>+12,.0f}")
    print(f"    {'chain-1 pickup sorts':<34}{c1:>12,.0f}{'—':>12}")
    print(f"\n  Notes the tables carry in words rather than numbers:")
    print(f"    * {second:,.0f} EA of the second buildings is RELAY — carried there and not opened —")
    print(f"      so second buildings ({honest:,.0f}) exceed second sorts ({r2:,.0f}) by exactly that.")
    print(f"      Since the 2 Sep rewire sankey_from_optilogic.py draws this same figure; if the two")
    print(f"      ever disagree the diagram has stopped showing the solve.")
    print(f"    * {third:,.0f} EA is relayed twice — a THIRD building the depth-2 basis cannot hold.")
    print(f"    * the model and the measurement have different bases ({base:,.0f} vs {m_base:,.0f},")
    print(f"      {base / m_base - 1:+.1%}), so compare the SHARES, not the EA.")


if __name__ == "__main__":
    main()
