"""Forbid the RELAY: a building may only send out a Despatch2 product it can make itself.

    IN / OUT   outputs/melbourne_optilogic_final/FlowConstraints.csv   (rewritten in place)

WHAT THE RELAY IS. `X_Despatch2` is finished freight — sorted, loaded, addressed to a delivering
depot. Sunshine West, Melbourne North and Bayswater are BOTH depots that receive it and sites that
send it, and the model has no way to tell those two roles apart at one node. So the solver brings
finished freight in and sends it straight back out without opening it: two buildings, one sort.

    19,370 EA in the baseline_with_wc run — Sunshine West 13,627, Melbourne North 4,261,
    Bayswater 1,482, biggest single stream Tullamarine -> Sunshine West 6,724 EA.

It is free, which is why the solver likes it. Every one of those rows carries `modename = None`,
no matching TransportationPolicies row, and `totalcost = 0.0`, against 127,297 EA of real despatch
that rides a Rigid_Truck / Twelve_Tonner / B_Double at a fixed cost per vehicle. They ARE real
`Replenishment` arcs though — NEO gives them a great-circle distance — so a flow constraint binds
them.

THE RULE. A facility may only originate a Despatch2 product it has a ProductionPolicy for. That
is exact, not a heuristic: on the current run it forbids 19,370 EA (100% of it mode `None`, 100%
of it freight the sender had itself received) and leaves all 127,297 EA of legitimate despatch
untouched. Checked before this file was written.

A flat Max 0 covers 19,370 of the 19,534 EA and no UserDefinedConstraint is needed for it. The
conditional form — "ship out no more than you round-2 sorted", two UserDefinedVariable rows and a
UserDefinedConstraint per pair — is only required where a depot BOTH relays a flavour and can make
it, because there a Max 0 would kill its legitimate despatch as well. That is 2 pairs and 164 EA
(0.8%): Sunshine West forwarding EP_MET_SWP and EP_MET_MNP. Those are EXEMPT from the rule below,
so that volume keeps relaying; the script prints it every run so it cannot drift quietly.

WRITTEN FOR EVERY BUILDING, not just the three that relay today. The relay rides no transport
policy, so policy absence is not what stops it — nothing does. A depot that does not relay in this
solve could relay in the next one, and the whole reason this file exists is that the model allowed
a movement nobody intended.

NOT IN THE NOTEBOOK. This is a post-build patch on the model folder; re-running
melbourne-optilogic-final.ipynb will drop it and you re-run this. Idempotent — it strips its own
rows before writing, so running twice is the same as running once.

Run:  uv run python post_process/add_no_relay_constraints.py
"""
import csv
import os

from collections import defaultdict

from _paths import DATA_ROOT, FINAL_OUT

HERE = str(DATA_ROOT)
MODEL = str(FINAL_OUT)
RUN = os.path.join(HERE, "outputs", "run_outputs", "OptimizationFlowSummary.csv")
TAG = "no-relay"            # marks our rows in `notes`, so a re-run can strip them


def rows(name):
    with open(os.path.join(MODEL, f"{name}.csv"), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main():
    products = [r["productname"] for r in rows("Products")
                if r["productname"].endswith("Despatch2")]
    # every building the model carries — the two facility groups, which is where `Despatch2`
    # can start or end. Read, not typed: a site added to the model joins this automatically.
    groups = defaultdict(list)
    for r in rows("Groups"):
        if r["grouptype"] == "Facilities":
            groups[r["groupname"]].append(r["membername"])
    buildings = sorted(set(groups["HUB_Facilities"]) | set(groups["PDC_Facilities"]))
    assert buildings, "no HUB_Facilities / PDC_Facilities group — has Groups.csv changed?"

    # what each facility can actually MAKE. An Exclude row is not a capability.
    makes = defaultdict(set)
    for r in rows("ProductionPolicies"):
        if (r["status"] or "Include") != "Exclude":
            makes[r["facilityname"]].add(r["productname"])

    # ── the check that decides whether a flat Max 0 is enough ────────────────────────
    # A depot that relays a flavour it CAN round-2 sort cannot be handled by Max 0 — the
    # constraint would kill its real second sorts too, and the conditional UDC would be needed
    # instead. Asserted against the solved run rather than assumed.
    if os.path.exists(RUN):
        site = lambda n: n.startswith(("PUD_", "HUB_"))          # noqa: E731
        inn, out = defaultdict(float), defaultdict(float)
        for r in csv.DictReader(open(RUN, encoding="utf-8-sig")):
            q = float(r["flowquantity"] or 0)
            p, o, d = r["productname"], r["originname"], r["destinationname"]
            if q <= 0 or not p.endswith("Despatch2"):
                continue
            if site(d):
                inn[(d, p)] += q
            if site(o) and site(d):
                out[(o, p)] += q
        relay = {k: v for k, v in out.items() if inn.get(k, 0) > 0 and k[1] not in makes[k[0]]}
        clash = {k: v for k, v in out.items() if inn.get(k, 0) > 0 and k[1] in makes[k[0]]}
        print(f"  run check: {len(relay)} relaying (site, product) pairs, "
              f"{sum(relay.values()):,.0f} EA the site cannot make — CAUGHT by these rows")
        # THE RESIDUAL, and it is a residual rather than a failure. A site that relays a flavour
        # it CAN make is exempt from the rule above (its own despatch is legitimate and a Max 0
        # would kill it), so that volume keeps passing through. Only the conditional form —
        # "ship out no more than you round-2 sorted", two UserDefinedVariable rows and a
        # UserDefinedConstraint per pair — can separate the two, and it is worth the extra
        # machinery only if this number grows. Reported every run so it cannot drift quietly.
        if clash:
            print(f"  → {len(clash)} pairs relay a flavour the site CAN make, so they are exempt "
                  f"and {sum(clash.values()):,.0f} EA "
                  f"({sum(clash.values()) / max(sum(relay.values()) + sum(clash.values()), 1):.1%}"
                  f" of the relay) SURVIVES: "
                  + ", ".join(f"{s}/{p}" for s, p in sorted(clash)))
            print("    fix those with the conditional UDC if the number ever matters")
    else:
        print(f"  (no solved run at {RUN} — writing the rule without the empirical check)")

    # ── the rows ─────────────────────────────────────────────────────────────────────
    # destination is the PDC group: every Despatch2 flow in the run lands at a PUD (251 of 251),
    # so one aggregated row per (building, product) covers the whole movement.
    existing = rows("FlowConstraints")
    cols = list(existing[0].keys()) if existing else []
    kept = [r for r in existing if TAG not in (r.get("notes") or "")]
    dropped = len(existing) - len(kept)

    new = []
    for b in buildings:
        for p in sorted(products):
            if p in makes[b]:
                continue                    # it makes this one — that despatch is legitimate
            new.append({**{c: "" for c in cols},
                        "originname": b, "originnamegroupbehavior": "",
                        "destinationname": "PDC_Facilities",
                        "destinationnamegroupbehavior": "Aggregate",
                        "productname": p, "productnamegroupbehavior": "Aggregate",
                        "periodname": "ALL", "periodnamegroupbehavior": "Aggregate",
                        "constrainttype": "Max", "constraintvalue": "0",
                        "constraintvalueuom": "EA", "status": "Include",
                        "notes": f"{TAG}: {b} does not produce {p}, so it may not despatch it "
                                 f"— forbids the free pass-through of finished freight"})

    path = os.path.join(MODEL, "FlowConstraints.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(kept + new)
    print(f"  {len(buildings)} buildings x {len(products)} Despatch2 products")
    print(f"  {'stripped ' + str(dropped) + ' rows from a previous run' if dropped else 'no previous rows to strip'}")
    print(f"  wrote {path}")
    print(f"    {len(kept)} existing constraints kept + {len(new)} new = {len(kept) + len(new)} rows")


if __name__ == "__main__":
    main()
