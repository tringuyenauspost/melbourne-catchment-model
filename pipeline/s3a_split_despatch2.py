"""Split every Despatch2 flavour by WHO MADE IT, so a second building is always a second sort.

    IN / OUT   outputs/melbourne_optilogic_final/{Products,BillOfMaterials,ProductionPolicies,
                                                  ReplenishmentPolicies,TransportationPolicies}.csv

THE PROBLEM. `PP_MET_MPF_Despatch2` is finished freight flavoured by the site that FIRST sorted it,
and two different routes make the identical product: `BOM_LOAD_DIRECT_MET_PP` at Melbourne Parcel
(one sort) and `BOM_LOAD2_PP_MET_MPF` at any round-2 site (two sorts). Because they are the same
product, a site that round-2 sorts the flavour may also RECEIVE it already finished and send it
straight back out — two buildings, one sort. That is the relay, and the flat
"a site may not despatch what it cannot make" rule cannot touch it: those sites CAN make it.

On the 2 Sep 17:20 run the relay was 8,449 EA to a second building plus 1,478 to a third, and it
had nearly DOUBLED from the run before, because pinning the round-2 band capped the honest route
and the freight moved into the one channel still unconstrained. Band and relay are substitutes;
constraining one alone just moves the volume.

THE FIX. Give each route its own product:

    <CLS>_<FAM>_<SITE>_Despatch2    made ONLY at <SITE>, by LOAD_DIRECT      (one sort)
    <CLS>_<FAM>_<SITE>_Despatch2R   made ONLY at a round-2 site, by LOAD2    (two sorts)

and then let the SOURCING say the same thing: the direct product may be replenished only from its
own site, the round-2 product only from the sites that round-2 sort it. A site holding finished
freight it did not make now has no policy that lets it pass the freight on, so the relay is not
forbidden by a constraint — it is unrepresentable. Both products feed the same `_Delivered`
product through their own driver recipe, so demand and OriginMix are untouched.

WHAT MOVES, PER FLAVOUR. Mostly renames, not additions:

    Products               +1
    BillOfMaterials        +1  (a driver recipe consuming the R product)
    ProductionPolicies     LOAD2 rows RE-POINTED to the R product, +11 driver rows
    ReplenishmentPolicies  rows SPLIT by source: own site keeps the direct product,
                           round-2 sites move to the R product, any other source dropped
    TransportationPolicies the same, by origin

THIRTEEN FLAVOURS ARE NOT SPLIT and that is deliberate: interstate and regional volume arriving at
TPF/BAY/MNP/SWP has no round-2 recipe anywhere, so only the direct route can make it and there is
nothing to separate. The flat Max-0 rule already covers them.

Run:  uv run python post_process/split_despatch2_by_route.py
      uv run python post_process/split_despatch2_by_route.py --restore
      uv run python post_process/add_no_relay_constraints.py     # ALWAYS after either
"""
import argparse
import csv
import os
import shutil

from _paths import DATA_ROOT, FINAL_OUT, PRESPLIT

HERE = str(DATA_ROOT)
MODEL = str(FINAL_OUT)
SAVE = str(PRESPLIT)
TABLES = ("Products", "BillOfMaterials", "ProductionPolicies",
          "ReplenishmentPolicies", "TransportationPolicies")
TAG = "split-by-route"
SUFFIX = "R"                      # <flavour>_Despatch2 -> <flavour>_Despatch2R


def load(name):
    with open(os.path.join(MODEL, f"{name}.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return list(rows[0].keys()), rows


def save(name, cols, rows):
    with open(os.path.join(MODEL, f"{name}.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--restore", action="store_true", help="put the pre-split tables back")
    args = ap.parse_args(argv)

    if args.restore:
        if not os.path.isdir(SAVE):
            raise SystemExit(f"  nothing to restore — {SAVE} does not exist")
        for t in TABLES:
            shutil.copy(os.path.join(SAVE, f"{t}.csv"), os.path.join(MODEL, f"{t}.csv"))
        shutil.rmtree(SAVE)
        print(f"  restored {len(TABLES)} tables from {os.path.relpath(SAVE, HERE)}")
        print("  NOW RE-RUN  uv run python post_process/add_no_relay_constraints.py")
        return

    # THE SAVE-DIR OUTLIVES A REBUILD, AND THAT IS A TRAP. `outputs/.presplit` says "already
    # split", but the notebooks rewrite the model underneath it, so after a rebuild the flag is
    # true and the model is NOT split. The script then refuses, and anyone who does not read the
    # refusal uploads an unsplit model that looks patched. Ask the MODEL, not the flag: if no
    # `Despatch2R` product exists, the save is stale — say so, drop it, and carry on.
    if os.path.isdir(SAVE):
        _now = {r["productname"] for r in load("Products")[1]}
        if any(p.endswith(SUFFIX) for p in _now):
            raise SystemExit(f"  already split ({os.path.relpath(SAVE, HERE)} exists) — "
                             f"--restore first if you want to redo it")
        print(f"  {os.path.relpath(SAVE, HERE)} is STALE — it survived a rebuild and the model in "
              f"front of me carries no {SUFFIX} product. Discarding it and splitting afresh.")
        shutil.rmtree(SAVE)

    tab = {t: load(t) for t in TABLES}
    pol_cols, pol = tab["ProductionPolicies"]

    # ── which flavours have BOTH routes, and where each route lives ──────────────────
    direct, load2, live = {}, {}, lambda r: (r["status"] or "Include") != "Exclude"
    for r in pol:
        if not live(r):
            continue
        b = r["bomname"] or ""
        if b.startswith("BOM_LOAD_DIRECT"):
            direct.setdefault(r["productname"], set()).add(r["facilityname"])
        elif b.startswith("BOM_LOAD2"):
            load2.setdefault(r["productname"], set()).add(r["facilityname"])
    split = sorted(set(direct) & set(load2))
    assert split, "no flavour has both a direct and a round-2 route — has the model changed?"
    R = {p: p + SUFFIX for p in split}
    # A round-2 site that is ALSO the flavour's own site would make the two products
    # indistinguishable again; the model never grants that (a site cannot round-2 sort its own
    # flavour) but the split is only sound because of it, so it is asserted rather than assumed.
    for p in split:
        assert not (load2[p] & direct[p]), \
            f"{p} is made BOTH ways at {load2[p] & direct[p]} — the split cannot separate it"

    os.makedirs(SAVE, exist_ok=True)
    for t in TABLES:
        shutil.copy(os.path.join(MODEL, f"{t}.csv"), os.path.join(SAVE, f"{t}.csv"))

    # ── 1. the new products ──────────────────────────────────────────────────────────
    pr_cols, prods = tab["Products"]
    by_name = {r["productname"]: r for r in prods}
    for p in split:
        prods.append({**by_name[p], "productname": R[p],
                      "notes": f"{by_name[p].get('notes', '')} [{TAG}: the ROUND-2 made half; "
                               f"{p} is now the direct-made half]"})

    # ── 2. the driver recipe for the R product ───────────────────────────────────────
    bom_cols, bom = tab["BillOfMaterials"]
    driver_bom = {}                       # p -> the bomname whose driver wave consumes p
    for r in bom:
        if r["productname"] in R and "DRIVER" in r["bomname"]:
            driver_bom[r["productname"]] = r["bomname"]
    newbom = []
    for p, b in driver_bom.items():
        for r in [x for x in bom if x["bomname"] == b]:
            newbom.append({**r, "bomname": f"{b}_{SUFFIX}", "productname": R[p],
                           "notes": f"{r.get('notes', '')} [{TAG}: round-2 made]"})
    bom.extend(newbom)

    # ── 3. production: re-point LOAD2, and give the R product its driver rows ────────
    n_point, newpol = 0, []
    for r in pol:
        if (r["bomname"] or "").startswith("BOM_LOAD2") and r["productname"] in R:
            r["productname"] = R[r["productname"]]
            r["notes"] = f"{r.get('notes', '')} [{TAG}]"
            n_point += 1
    for p, b in driver_bom.items():
        for r in [x for x in pol if x["bomname"] == b]:
            newpol.append({**r, "bomname": f"{b}_{SUFFIX}",
                           "notes": f"{r.get('notes', '')} [{TAG}: round-2 made]"})
    pol.extend(newpol)

    # ── 4 & 5. sourcing: the direct product from its own site, the R from round-2 sites ──
    def resplit(name, origin_col, dest_col):
        """Route the sourcing rows to the half that can actually make them, and delete the rest.

        ONE RULE: the ORIGIN must be able to MAKE what it ships. The own site keeps the direct
        half, a round-2 site takes the R half, and any other origin was only ever a relay lane.

        THERE WAS A SECOND RULE AND IT WAS WRONG — do not reintroduce it. "A site that can produce
        a flavour has no reason to receive it" sounds right and is false: production is capped by
        the pinned Despatch1 band, so a round-2 site can make SOME of a flavour and still need the
        rest shipped in already sorted. Dropping those lanes made the model INFEASIBLE — 7,486 EA
        that the previous solve actually carried lost its only route, all of it into Sunshine West
        and Melbourne North, and NEO reported infeasible with no constraint violated because the
        break was structural rather than a rule conflict.

        The rule was aimed at the R half relaying between two round-2 sites. It is not needed for
        the relay that actually happens: all 8,449 EA of second-building relay in the 2 Sep 17:20
        run arrived from the flavour's OWN site, so it is direct-half freight, and the origin rule
        alone leaves it with no lane and no production to ship it on. What remains possible is a
        1,478 EA third-building hop on the R half, which is worth MEASURING in the next run rather
        than pre-empting with a rule that costs feasibility.
        """
        cols, rows = tab[name]
        keep, made, why = [], [], {"origin": 0}
        for r in rows:
            p = r["productname"]
            if p not in R:
                keep.append(r)
                continue
            o, d = r[origin_col], r[dest_col]
            if o in direct[p]:
                tgt = keep
            elif o in load2[p]:
                tgt = made
            else:
                why["origin"] += 1                   # origin can make NEITHER half
                continue
            if tgt is keep:
                keep.append(r)
            else:
                made.append({**r, "productname": R[p],
                             "notes": f"{r.get('notes', '')} [{TAG}: round-2 made]"})
        tab[name] = (cols, keep + made)
        return len(rows), len(keep), len(made), why

    rep = resplit("ReplenishmentPolicies", "sourcename", "facilityname")
    trn = resplit("TransportationPolicies", "originname", "destinationname")

    for t in TABLES:
        save(t, *tab[t])

    print(f"  {len(split)} flavours split (13 direct-only flavours left alone)")
    print(f"    Products               +{len(split)}")
    print(f"    BillOfMaterials        +{len(newbom)}  (driver recipe for the R product)")
    print(f"    ProductionPolicies      {n_point} LOAD2 rows re-pointed, +{len(newpol)} driver rows")
    for nm, (was, kept, moved, why) in (("ReplenishmentPolicies", rep),
                                        ("TransportationPolicies", trn)):
        print(f"    {nm:<22} {was:>6} -> {kept + moved:>6}   "
              f"{kept} direct + {moved} round-2;  "
              f"dropped {why['origin']} (origin can make neither half)")
    # ── THE GUARD: can the LAST SOLVE still be routed? ───────────────────────────────
    # Trimming sourcing rows is how this file works, and trimming one row too many makes the
    # model INFEASIBLE with no constraint violated — NEO reports a structural break, not a rule
    # conflict, so nothing in the error report points at the cause. The cheapest detector is the
    # previous solve: every Despatch2 movement it made must still have a lane, on whichever half
    # of the split it now belongs to. Warned rather than raised, because a genuinely changed
    # model may legitimately drop a lane the old run used — but it is printed loudly, because
    # the one time this was skipped it cost an upload.
    run = os.path.join(HERE, "outputs", "run_outputs", "OptimizationFlowSummary.csv")
    if os.path.exists(run):
        rep_k = {(r["facilityname"], r["productname"], r["sourcename"])
                 for r in tab["ReplenishmentPolicies"][1]}
        trn_k = {(r["originname"], r["destinationname"], r["productname"])
                 for r in tab["TransportationPolicies"][1]}
        makes_now = {}
        for r in tab["ProductionPolicies"][1]:
            makes_now.setdefault(r["productname"], set()).add(r["facilityname"])
        lost, tot = {}, 0.0
        for r in csv.DictReader(open(run, encoding="utf-8-sig")):
            q = float(r["flowquantity"] or 0)
            p_, o, d = r["productname"], r["originname"], r["destinationname"]
            if q <= 0 or not p_.endswith("Despatch2") or p_ not in R:
                continue
            tot += q
            pr = p_ if o in makes_now.get(p_, set()) else R[p_]
            if (o, d, pr) not in trn_k or (d, pr, o) not in rep_k:
                lost[(o, d, pr)] = lost.get((o, d, pr), 0.0) + q
        if lost:
            print(f"\n  !! {sum(lost.values()):,.0f} EA of {tot:,.0f} that the LAST SOLVE carried has no "
                  f"lane any more, on {len(lost)} arcs. Expect INFEASIBLE:")
            for (o, d, pr), v in sorted(lost.items(), key=lambda x: -x[1])[:6]:
                print(f"       {o} -> {d}  {pr}  {v:,.0f}")
        else:
            print(f"\n  guard: all {tot:,.0f} EA the last solve carried is still routable")

    print(f"  pre-split tables saved to {os.path.relpath(SAVE, HERE)}  (--restore puts them back)")
    print("  NOW RE-RUN  uv run python post_process/add_no_relay_constraints.py")


if __name__ == "__main__":
    main()
