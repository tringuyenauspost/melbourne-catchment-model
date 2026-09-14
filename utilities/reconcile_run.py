"""Put the model we uploaded and the solve NEO sent back side by side, one table per promise.

    IN   outputs/melbourne_optilogic_final/*.csv   what we ASKED for (the uploaded model)
         outputs/run_outputs/*.csv                 what we GOT     (the solved run)

Every table has the same shape: an ASKED column off the model folder, a SOLVED column off the run,
and a verdict. Nothing is read from the Sankey page — the page draws one view of the run, this
recomputes each number from the two folders so agreeing with it is evidence rather than restatement.

Nine tables, in the order a number can go wrong:

     1  BALANCE          supply in == demand out, and the run's cost
     2  DEMAND           CustomerDemand      vs the CustomerFulfillment that served it
     3  SUPPLY           SupplierCapabilities vs the Procurement drawn against it
     4  FLOWCONSTRAINTS  every Min/Max row   vs the flow on that lane        <- the pins
     5  UDC              every user-defined constraint recomputed from the solve
     6  WORKCENTRES      WorkCenters cap     vs the throughput reported
     7  FACILITIES       Facilities cap      vs the freight that entered
     8  RECIPES          what we offered     vs what the solver actually used
     9  CONSERVATION     per building: in == out, since no BOM loses a unit

Run:  uv run python utilities/reconcile_run.py
      uv run python utilities/reconcile_run.py --table 4 5      only those tables
      uv run python utilities/reconcile_run.py --csv outputs/reconcile   also write each as a CSV
      uv run python utilities/reconcile_run.py --model outputs/... --run outputs/...

Exit status is 1 if any table fails, so it can gate an upload.
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root, one up from utilities/

TOL = 0.5           # EA. NEO writes quantities to 6dp; anything under half a parcel is rounding.
NEAR = 0.995        # a capacity this fully used is reported as binding


# ══ plumbing ═════════════════════════════════════════════════════════════════════════
def rows(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(v, default=0.0):
    """NEO and the model folder both leave a blank where a number is absent, and blank is not 0
    in a capacity column — it means unlimited. The caller decides which by passing `default`."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def included(r):
    return (r.get("status") or "Include").strip() != "Exclude"


class Table:
    """One comparison. Holds its own rows so it can be printed and written without recomputing."""

    def __init__(self, n, title, cols, widths=None, note=""):
        self.n, self.title, self.cols, self.note = n, title, cols, note
        self.widths = widths or [max(14, len(c) + 2) for c in cols]
        self.body, self.lines, self.ok, self.verdict = [], [], True, ""

    def row(self, *vals):
        self.body.append(list(vals))

    def say(self, line=""):
        self.lines.append(line)

    def fail(self, msg):
        self.ok = False
        self.verdict = msg

    def pass_(self, msg):
        self.verdict = msg

    def print(self):
        print(f"\n{'=' * 100}\n{self.n}. {self.title}\n{'=' * 100}")
        if self.note:
            for ln in self.note.strip().splitlines():
                print(f"  {ln.strip()}")
            print()
        if self.body:
            head = "".join(c.rjust(w) if i else c.ljust(w)
                           for i, (c, w) in enumerate(zip(self.cols, self.widths)))
            print("  " + head)
            print("  " + "-" * len(head))
            for b in self.body:
                print("  " + "".join(str(v).rjust(w) if i else str(v).ljust(w)
                                     for i, (v, w) in enumerate(zip(b, self.widths))))
        for ln in self.lines:
            print("  " + ln if ln else "")
        print(f"\n  {'PASS' if self.ok else 'FAIL'}  {self.verdict}")

    @staticmethod
    def _plain(v):
        """Excel and pandas both read "23,763" as text. The printed table wants the separators;
        the CSV wants the number, so strip them back out on the way to disk."""
        t = str(v)
        if t and t.replace(",", "").replace(".", "").replace("-", "").isdigit() and "," in t:
            return t.replace(",", "")
        return t

    def write(self, d):
        path = os.path.join(d, f"{self.n:02d}_{self.title.split(' — ')[0].lower().replace(' ', '_')}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(self.cols)
            w.writerows([self._plain(c) for c in row] for row in self.body)
        return path


def fmt(x, dp=0):
    if abs(x) < 10 ** -dp / 2:
        x = 0.0          # NEO writes to 6dp, so a true zero arrives as -1e-9 and prints "-0"
    return f"{x:,.{dp}f}"


def pct(a, b, dp=1):
    return "—" if not b else f"{100 * a / b:.{dp}f}%"


# ══ the model and the run, loaded once ═══════════════════════════════════════════════
class Model:
    def __init__(self, model_dir, run_dir):
        self.dir, self.run_dir = model_dir, run_dir
        need_m = ["CustomerDemand", "SupplierCapabilities", "FlowConstraints", "Groups",
                  "UserDefinedVariables", "UserDefinedConstraints", "WorkCenters", "Facilities",
                  "ProductionPolicies", "Processes", "Customers", "Suppliers"]
        need_r = ["OptimizationFlowSummary", "OptimizationProcessSummary",
                  "OptimizationProductionSummary", "OptimizationWorkCenterSummary"]
        for d, names in ((model_dir, need_m), (run_dir, need_r)):
            missing = [n for n in names if not os.path.exists(os.path.join(d, n + ".csv"))]
            if missing:
                sys.exit(f"{d} is missing {', '.join(missing)}.csv — is that the right folder?")
        self.m = {n: [r for r in rows(os.path.join(model_dir, n + ".csv")) if included(r)]
                  for n in need_m}
        self.r = {n: rows(os.path.join(run_dir, n + ".csv")) for n in need_r}

        # ── one scenario only. Mixing two is the classic way to double every number here.
        scen = {s for n in need_r for s in {x["scenarioname"] for x in self.r[n]}}
        self.scenarios = sorted(scen)
        self.scenario = self.scenarios[0] if self.scenarios else ""
        if len(scen) > 1:
            keep = self.scenarios[0]
            print(f"!! {len(scen)} scenarios in the run ({', '.join(self.scenarios)}); "
                  f"keeping '{keep}' only. Pass --scenario to choose another.")
            self.filter_scenario(keep)

        self.groups = defaultdict(set)
        for g in self.m["Groups"]:
            self.groups[g["groupname"]].add(g["membername"])
        self.facilities = {f["facilityname"] for f in self.m["Facilities"]}
        self.customers = {c["customername"] for c in self.m["Customers"]}

    def filter_scenario(self, name):
        self.r = {k: [x for x in v if x["scenarioname"] == name] for k, v in self.r.items()}
        self.scenario = name

    def members(self, name):
        """A field in a NEO table is either one entity or the name of a group of them."""
        return self.groups[name] if name in self.groups else {name}

    # the solve, folded the three ways every table below wants it
    def flows(self):
        for f in self.r["OptimizationFlowSummary"]:
            q = num(f["flowquantity"])
            if q:
                yield f, q


# ══ 1 ══ BALANCE ═════════════════════════════════════════════════════════════════════
def t_balance(M):
    """The one line that has to be true before any other table means anything.

    Every parcel enters at a supplier and leaves at a customer, and no process in this model loses
    one (every BOM is 1:1, every yieldpercentage is blank), so procurement and fulfilment are the
    same number. Replenishment is the freight between buildings — it is NOT part of the balance,
    it is how many inter-building legs that same freight rode.
    """
    t = Table(1, "BALANCE — does the run conserve parcels", ["flowtype", "rows", "EA"],
              [26, 10, 16], t_balance.__doc__)
    by = defaultdict(float)
    cnt = defaultdict(int)
    cost = 0.0
    for f, q in M.flows():
        by[f["flowtype"]] += q
        cnt[f["flowtype"]] += 1
        cost += num(f["totalcost"])
    for k in sorted(by):
        t.row(k, fmt(cnt[k]), fmt(by[k]))
    proc, ful = by.get("Procurement", 0), by.get("CustomerFulfillment", 0)
    t.say()
    t.say(f"procurement {fmt(proc)} EA  -  fulfilment {fmt(ful)} EA  =  {fmt(proc - ful)} EA")
    t.say(f"inter-building legs (Replenishment) {fmt(by.get('Replenishment', 0))} EA "
          f"= {pct(by.get('Replenishment', 0), ful)} of the freight, i.e. that many legs per parcel")
    t.say(f"total cost on the flow table: {fmt(cost, 2)}   "
          f"(flow legs only — work-centre operating cost is table 6)")
    if abs(proc - ful) <= TOL:
        t.pass_(f"in == out at {fmt(ful)} EA")
    else:
        t.fail(f"in != out by {fmt(proc - ful)} EA — a process is losing or making parcels")
    return t


# ══ 2 ══ DEMAND ══════════════════════════════════════════════════════════════════════
def t_demand(M):
    """What we asked each sink for, against what the solve delivered to it.

    A shortfall here is the single most important failure in the file: it means NEO could not
    reach a customer and chose to leave demand unserved rather than report infeasible. Sinks are
    grouped by family (the `CZ_<family>_<building>` prefix) because there are ~1,000 of them; the
    per-sink detail goes to the CSV.
    """
    t = Table(2, "DEMAND — CustomerDemand vs CustomerFulfillment",
              ["sink family", "sinks", "asked EA", "served EA", "unmet EA", "served"],
              [34, 8, 14, 14, 14, 10], t_demand.__doc__)
    asked, served = defaultdict(float), defaultdict(float)
    for d in M.m["CustomerDemand"]:
        asked[(d["customername"], d["productname"])] += num(d["quantity"])
    for f, q in M.flows():
        if f["flowtype"] == "CustomerFulfillment":
            served[(f["destinationname"], f["productname"])] += q

    def fam(name):
        p = name.split("_")
        return "_".join(p[:2]) if len(p) > 2 and p[0] == "CZ" else name

    fa, fs, sinks = defaultdict(float), defaultdict(float), defaultdict(set)
    for (c, p), v in asked.items():
        fa[fam(c)] += v
        sinks[fam(c)].add(c)
    for (c, p), v in served.items():
        fs[fam(c)] += v
        sinks[fam(c)].add(c)
    for k in sorted(fa, key=lambda x: -fa[x]):
        t.row(k, len(sinks[k]), fmt(fa[k]), fmt(fs[k]), fmt(fa[k] - fs[k]), pct(fs[k], fa[k], 2))
    ta, ts = sum(fa.values()), sum(fs.values())
    t.row("TOTAL", sum(len(v) for v in sinks.values()), fmt(ta), fmt(ts), fmt(ta - ts), pct(ts, ta, 2))

    short = {k: asked[k] - served.get(k, 0) for k in asked if asked[k] - served.get(k, 0) > TOL}
    extra = {k: served[k] - asked.get(k, 0) for k in served if served[k] - asked.get(k, 0) > TOL}
    t.say()
    t.say(f"demand rows in the model: {len(asked):,}   fulfilment cells in the run: {len(served):,}")
    if short:
        t.say(f"{len(short)} (sink, product) cells short — worst five:")
        for (c, p), v in sorted(short.items(), key=lambda kv: -kv[1])[:5]:
            t.say(f"    {c} / {p}: {fmt(v)} EA short of {fmt(asked[(c, p)])}")
    if extra:
        t.say(f"!! {len(extra)} cells served MORE than asked — check for a duplicated demand row")
    if not short and not extra:
        t.pass_(f"every one of the {len(asked):,} demand cells served exactly — {fmt(ts)} EA")
    else:
        bits = ([f"{fmt(sum(short.values()))} EA unserved across {len(short)} cells"] if short else []) \
             + ([f"{fmt(sum(extra.values()))} EA served at {len(extra)} cells the model "
                 f"never asked for"] if extra else [])
        t.fail("; ".join(bits))
    return t


# ══ 3 ══ SUPPLY ══════════════════════════════════════════════════════════════════════
def t_supply(M):
    """What each supplier was allowed to send, against what the solve drew.

    `supplycapacity` blank means unlimited, which is not the same as zero — those rows are counted
    separately and never appear as "at cap". A family sitting at 100% is the interesting case: it
    is a binding constraint, so the answer moved because of a number WE chose, and the next
    question is whether that number is measured or a placeholder.
    """
    t = Table(3, "SUPPLY — SupplierCapabilities vs Procurement",
              ["supplier family", "arcs", "offered EA", "drawn EA", "used", "arcs at cap"],
              [26, 8, 16, 16, 9, 14], t_supply.__doc__)
    offered, drawn, unlimited = defaultdict(float), defaultdict(float), defaultdict(int)
    cells = {}
    for s in M.m["SupplierCapabilities"]:
        k = (s["suppliername"], s["productname"])
        c = num(s["supplycapacity"], default=None) if str(s["supplycapacity"]).strip() else None
        cells[k] = c
    for f, q in M.flows():
        if f["flowtype"] == "Procurement":
            drawn[(f["originname"], f["productname"])] += q

    def fam(name):
        p = name.split("_")
        return "_".join(p[:2]) if len(p) > 1 and p[0] == "SUP" else name

    fo, fd, fn, fu, at = (defaultdict(float), defaultdict(float), defaultdict(int),
                          defaultdict(int), defaultdict(int))
    over = []
    for k, c in cells.items():
        s, p = k
        d = drawn.get(k, 0.0)
        fn[fam(s)] += 1
        fd[fam(s)] += d
        if c is None:
            fu[fam(s)] += 1
        else:
            fo[fam(s)] += c
            if d > c + TOL:
                over.append((s, p, d, c))
            elif c and d >= c * NEAR:
                at[fam(s)] += 1
    orphan = {k: v for k, v in drawn.items() if k not in cells}
    for k in sorted(fn, key=lambda x: -fd[x]):
        cap = fmt(fo[k]) + (f" +{fu[k]} free" if fu[k] else "")
        t.row(k, fn[k], cap, fmt(fd[k]), pct(fd[k], fo[k]) if fo[k] else "—",
              f"{at[k]} / {fn[k] - fu[k]}")
    t.row("TOTAL", sum(fn.values()), fmt(sum(fo.values())), fmt(sum(fd.values())),
          pct(sum(fd.values()), sum(fo.values())), f"{sum(at.values())} / "
          f"{sum(fn.values()) - sum(fu.values())}")
    t.say()
    t.say(f"{sum(1 for k in cells if drawn.get(k, 0) <= TOL):,} of {len(cells):,} supply arcs "
          f"were not used at all")
    if over:
        t.say(f"!! {len(over)} arcs drew MORE than their capacity:")
        for s, p, d, c in sorted(over, key=lambda x: x[3] - x[2])[:5]:
            t.say(f"    {s} / {p}: {fmt(d)} drawn vs {fmt(c)} allowed")
    if orphan:
        t.say(f"!! {len(orphan)} procurement arcs have no SupplierCapabilities row "
              f"({fmt(sum(orphan.values()))} EA) — the run is not this model")
    if over or orphan:
        t.fail(f"{len(over)} over-capacity arcs, {len(orphan)} arcs not in the model")
    else:
        t.pass_(f"every arc inside its capacity; {sum(at.values())} arcs pinned at 100% "
                f"are what fixed the answer")
    return t


# ══ 4 ══ FLOW CONSTRAINTS ════════════════════════════════════════════════════════════
def t_flowconstraints(M):
    """Every Min and Max row we wrote, recomputed against the flow that actually ran on that lane.

    This is the table that says whether the pins held. A row is resolved the way NEO resolves it:
    an origin/destination that names a Group covers every member, `Aggregate` means the sum over
    the set (never per member), a blank mode means all modes. A Min at slack 0 and a Max at slack 0
    are both BINDING — the solver wanted to be somewhere else and our number stopped it.
    """
    t = Table(4, "FLOW CONSTRAINTS — every Min/Max vs the flow that ran",
              ["result", "rows", "EA under the rows"], [16, 10, 20], t_flowconstraints.__doc__)
    lane = defaultdict(float)
    for f, q in M.flows():
        lane[(f["originname"], f["destinationname"], f["productname"])] += q

    detail, binding, violated = [], [], []
    for c in M.m["FlowConstraints"]:
        origins = M.members(c["originname"]) if c["originname"] else None
        dests = M.members(c["destinationname"]) if c["destinationname"] else None
        prod = c["productname"]
        got = sum(v for (o, d, p), v in lane.items()
                  if (origins is None or o in origins)
                  and (dests is None or d in dests)
                  and (not prod or p == prod))
        val = num(c["constraintvalue"])
        kind = c["constrainttype"]
        if kind == "Min":
            slack, bad = got - val, got < val - TOL
        else:
            slack, bad = val - got, got > val + TOL
        state = "VIOLATED" if bad else ("binding" if abs(slack) <= TOL else "slack")
        detail.append([c["originname"], c["destinationname"], prod, kind, fmt(val), fmt(got),
                       fmt(slack), state, c["notes"]])
        if bad:
            violated.append((c, got, val))
        elif state == "binding":
            binding.append((c, got, val))

    by_state = defaultdict(lambda: [0, 0.0])
    for d in detail:
        s = by_state[d[7]]
        s[0] += 1
        s[1] += num(d[5].replace(",", ""))
    for k in ("VIOLATED", "binding", "slack"):
        if k in by_state:
            t.row(k, by_state[k][0], fmt(by_state[k][1]))
    t.cols_detail = ["originname", "destinationname", "productname", "type", "asked", "solved",
                     "slack", "state", "notes"]
    t.detail = detail

    t.say()
    t.say(f"{len(M.m['FlowConstraints']):,} constraint rows, resolved through "
          f"{len(M.groups):,} groups")
    if binding:
        t.say(f"{len(binding)} rows are BINDING — the solve is sitting on our number, not its own:")
        for c, got, val in binding[:8]:
            t.say(f"    {c['constrainttype']:<4} {fmt(val):>10} EA  "
                  f"{c['originname']} -> {c['destinationname']} / {c['productname']}")
        if len(binding) > 8:
            t.say(f"    ... and {len(binding) - 8} more (full list in the CSV)")
    if violated:
        t.say(f"!! {len(violated)} rows VIOLATED:")
        for c, got, val in violated[:10]:
            t.say(f"    {c['constrainttype']:<4} asked {fmt(val):>10}  got {fmt(got):>10}  "
                  f"{c['originname']} -> {c['destinationname']} / {c['productname']}")
        t.fail(f"{len(violated)} of {len(detail)} constraint rows are not honoured — "
               f"if these are Min rows on a group, check the group still has members")
    else:
        t.pass_(f"all {len(detail)} rows honoured; {len(binding)} binding, "
                f"{by_state['slack'][0]} with slack")
    return t


# ══ 5 ══ USER-DEFINED CONSTRAINTS ════════════════════════════════════════════════════
def t_udc(M):
    """Each user-defined constraint rebuilt term by term out of the production table.

    A UDC is a sum of terms over a variable; every term here is `type=Production`, so its value is
    the production quantity at one facility for one product group, optionally narrowed to one
    process. Multiply by the coefficient, add up, compare to the bound.

    Two traps this table exists to catch, both of which look like a clean solve:
      * a ConstraintName that reuses a VariableName — NEO DROPS the row silently and nothing is
        bounded. Checked by name before anything is computed.
      * a variable whose terms all resolve to zero volume, so the constraint is true and inert.
    """
    t = Table(5, "USER-DEFINED CONSTRAINTS — recomputed from the solve",
              ["constraint", "type", "bound", "computed", "slack", "state"],
              [46, 6, 12, 14, 14, 12], t_udc.__doc__)
    prod = defaultdict(float)
    for p in M.r["OptimizationProductionSummary"]:
        q = num(p["productionquantity"])
        if q:
            prod[(p["facilityname"], p["productname"], p["processname"])] += q

    terms = defaultdict(list)
    for v in M.m["UserDefinedVariables"]:
        terms[v["variablename"]].append(v)

    names = set(terms)
    collide = [c["constraintname"] for c in M.m["UserDefinedConstraints"]
               if c["constraintname"] in names]

    detail, inert, violated, binding = [], [], [], []
    for c in M.m["UserDefinedConstraints"]:
        tt = terms.get(c["variablename"])
        if not tt:
            detail.append([c["constraintname"], c["constrainttype"], c["constraintvalue"],
                           "NO VARIABLE", "", "BROKEN", ""])
            violated.append(c["constraintname"])
            continue
        total, gross, parts = 0.0, 0.0, []
        for term in tt:
            prods = M.members(term["productname"]) if term["productname"] else None
            procs = M.members(term["processname"]) if term["processname"] else None
            fac = term["facilityname"]
            val = sum(q for (f, p, pr), q in prod.items()
                      if (not fac or f == fac)
                      and (prods is None or p in prods)
                      and (procs is None or pr in procs))
            coef = num(term["coefficient"])
            total += coef * val
            gross += abs(val)
            parts.append(f"{term['termname']}={fmt(val)}x{coef}")
        val = num(c["constraintvalue"])
        if c["constrainttype"] == "Min":
            slack, bad = total - val, total < val - TOL
        else:
            slack, bad = val - total, total > val + TOL
        if gross <= TOL:
            state = "INERT"
            inert.append(c["constraintname"])
        elif bad:
            state = "VIOLATED"
            violated.append(c["constraintname"])
        elif abs(slack) <= TOL:
            state = "binding"
            binding.append(c["constraintname"])
        else:
            state = "slack"
        detail.append([c["constraintname"], c["constrainttype"], fmt(val), fmt(total, 1),
                       fmt(slack, 1), state, "; ".join(parts)])
        t.row(c["constraintname"][:45], c["constrainttype"], fmt(val), fmt(total, 1),
              fmt(slack, 1), state)
    t.cols_detail = ["constraintname", "type", "bound", "computed", "slack", "state", "terms"]
    t.detail = detail

    t.say()
    t.say(f"{len(M.m['UserDefinedConstraints'])} constraints over "
          f"{len(terms)} variables ({len(M.m['UserDefinedVariables'])} terms)")
    if collide:
        t.say(f"!! {len(collide)} constraintname(s) reuse a variablename — NEO drops those rows "
              f"SILENTLY and the solve looks clean with nothing bounded: {', '.join(collide[:4])}")
    if inert:
        t.say(f"!! {len(inert)} constraint(s) have no volume under them, so they bind nothing: "
              f"{', '.join(inert[:4])}")
    if violated or collide:
        t.fail(f"{len(violated)} violated, {len(collide)} silently dropped by name collision")
    else:
        t.pass_(f"all {len(detail)} honoured; {len(binding)} binding, {len(inert)} inert")
    return t


# ══ 6 ══ WORK CENTRES ════════════════════════════════════════════════════════════════
def t_workcentres(M):
    """Capacity we sized against throughput the solve reported, for every work centre.

    Utilisation is quantity / capacity because `throughputcapacityuom` is EA everywhere. A centre
    at exactly 100% is a hard edge in the answer: the freight wanted to go through it and could
    not. A centre in the model with NO row in the run never opened.
    """
    t = Table(6, "WORK CENTRES — sized vs used",
              ["work centre", "facility", "cap EA", "used EA", "util", "state"],
              [40, 26, 12, 13, 10, 12], t_workcentres.__doc__)
    solved = {w["workcentername"]: w for w in M.r["OptimizationWorkCenterSummary"]}
    asked = {w["workcentername"]: w for w in M.m["WorkCenters"]}
    full, over, idle, cost = [], [], [], 0.0
    detail = []
    for name in sorted(asked, key=lambda n: -num(solved.get(n, {}).get("throughputquantity"))):
        a = asked[name]
        cap = num(a["throughputcapacity"], default=None) if str(a["throughputcapacity"]).strip() else None
        s = solved.get(name)
        used = num(s["throughputquantity"]) if s else 0.0
        cost += num(s["totalworkcentercost"]) if s else 0.0
        minthru = num(a["minimumthroughput"], default=None) if str(a.get("minimumthroughput") or "").strip() else None
        if s is None:
            state = "NOT IN RUN"
            idle.append(name)
        elif used <= TOL:
            state = "unused"
            idle.append(name)
        elif cap and used > cap + TOL:
            state = "OVER CAP"
            over.append((name, used, cap))
        elif cap and used >= cap * NEAR:
            state = "AT CAP"
            full.append((name, used, cap))
        else:
            state = "ok"
        if minthru is not None and 0 < used < minthru - TOL:
            state = "UNDER MIN"
            over.append((name, used, minthru))
        detail.append([name, a["facilityname"], "" if cap is None else fmt(cap), fmt(used),
                       pct(used, cap or 0), state,
                       (s or {}).get("optimizedstatus", ""), fmt(num((s or {}).get("totalworkcentercost")), 2)])
        t.row(name[:39], a["facilityname"][:25], "free" if cap is None else fmt(cap), fmt(used),
              pct(used, cap or 0), state)
    t.cols_detail = ["workcentername", "facilityname", "capacity", "throughput", "utilisation",
                     "state", "optimizedstatus", "workcentrecost"]
    t.detail = detail
    t.say()
    t.say(f"{len(asked)} work centres in the model, {len(solved)} in the run; "
          f"work-centre cost {fmt(cost, 2)}")
    t.say(f"{len(full)} at capacity, {len(idle)} never used")
    if full:
        t.say("at capacity — these are the walls the answer is leaning on:")
        for n, u, c in full[:10]:
            t.say(f"    {n:<44} {fmt(u):>12} / {fmt(c)}")
    orphan = [n for n in solved if n not in asked]
    if orphan:
        t.say(f"!! {len(orphan)} work centres in the run are not in the model — "
              f"the run was solved from a different folder: {', '.join(orphan[:3])}")
    if over or orphan:
        t.fail(f"{len(over)} centres outside their bounds, {len(orphan)} not in the model")
    else:
        t.pass_(f"every centre inside its bounds; {len(full)} pinned at capacity")
    return t


# ══ 7 ══ FACILITIES ══════════════════════════════════════════════════════════════════
def t_facilities(M):
    """Building throughput caps against the freight that actually entered each building.

    The cap is sized in s2a as delivery + arrivals + stage + round-2 inbound, plus headroom, so the
    comparison here is everything that arrived on a lane. It is a bound, not an identity: a
    building under its cap is fine, a building over it means NEO counted something the sizing
    formula does not — the dial to move then is FACILITY_HEADROOM, not the formula.
    """
    t = Table(7, "FACILITIES — cap vs freight in",
              ["facility", "cap EA", "in EA", "out EA", "util", "state"],
              [30, 14, 14, 14, 9, 12], t_facilities.__doc__)
    inn, out = defaultdict(float), defaultdict(float)
    for f, q in M.flows():
        if f["destinationname"] in M.facilities:
            inn[f["destinationname"]] += q
        if f["originname"] in M.facilities:
            out[f["originname"]] += q
    over = []
    for fa in sorted(M.m["Facilities"], key=lambda x: -inn[x["facilityname"]]):
        n = fa["facilityname"]
        cap = num(fa["throughputcapacity"], default=None) if str(fa["throughputcapacity"]).strip() else None
        state = "ok"
        if cap and inn[n] > cap + TOL:
            state = "OVER CAP"
            over.append((n, inn[n], cap))
        elif cap and inn[n] >= cap * NEAR:
            state = "AT CAP"
        elif inn[n] <= TOL and out[n] <= TOL:
            state = "unused"
        t.row(n, "free" if cap is None else fmt(cap), fmt(inn[n]), fmt(out[n]),
              pct(inn[n], cap or 0), state)
    t.say()
    t.say("'in' is every lane arriving at the building, 'out' every lane leaving it; the gap is "
          "what the building's own customers took.")
    if over:
        for n, i, c in over:
            t.say(f"!! {n}: {fmt(i)} EA in against a {fmt(c)} EA cap")
        t.fail(f"{len(over)} buildings over their cap")
    else:
        t.pass_("no building over its cap")
    return t


# ══ 8 ══ RECIPES ═════════════════════════════════════════════════════════════════════
def t_recipes(M):
    """What we offered the solver against what it chose, for processes and production policies.

    An unused recipe is not a bug — the point of offering two ways to unload is that the solver
    picks one. It IS a finding when a whole process is unused, because that is a work centre we
    modelled, costed and sized for nothing, and when a process runs that we did not define.
    """
    t = Table(8, "RECIPES — offered vs chosen",
              ["step", "processes offered", "used", "EA processed"], [26, 20, 10, 18],
              t_recipes.__doc__)
    defined = {p["processname"]: p for p in M.m["Processes"]}
    used, step_q = defaultdict(float), defaultdict(float)
    for p in M.r["OptimizationProcessSummary"]:
        q = num(p["processedquantity"])
        if q:
            used[p["processname"]] += q
    by_step = defaultdict(lambda: [set(), set(), 0.0])
    for name, p in defined.items():
        s = by_step[p["stepname"]]
        s[0].add(name)
        if used.get(name, 0) > TOL:
            s[1].add(name)
            s[2] += used[name]
    for s in sorted(by_step, key=lambda k: -by_step[k][2]):
        a, b, q = by_step[s]
        t.row(s, len(a), len(b), fmt(q))
    t.row("TOTAL", len(defined), sum(1 for n in defined if used.get(n, 0) > TOL),
          fmt(sum(used.values())))

    pol = {(p["facilityname"], p["productname"], p["bomname"], p["processname"])
           for p in M.m["ProductionPolicies"]}
    ran = {(p["facilityname"], p["productname"], p["bomname"], p["processname"])
           for p in M.r["OptimizationProductionSummary"] if num(p["productionquantity"])}
    dead_proc = sorted(n for n in defined if used.get(n, 0) <= TOL)
    ghost = sorted(n for n in used if n not in defined)
    t.say()
    t.say(f"production policies offered {len(pol):,}, used {len(ran & pol):,} "
          f"({pct(len(ran & pol), len(pol))}) — an unused recipe is a road not taken, not an error")
    if ran - pol:
        t.say(f"!! {len(ran - pol)} production rows in the run have no policy in the model")
    if dead_proc:
        t.say(f"{len(dead_proc)} of {len(defined)} processes never ran — "
              f"each is a sized work centre doing nothing:")
        for n in dead_proc[:8]:
            t.say(f"    {n}")
        if len(dead_proc) > 8:
            t.say(f"    ... and {len(dead_proc) - 8} more")
    if ghost:
        t.say(f"!! {len(ghost)} processes ran that the model does not define: {', '.join(ghost[:3])}")
    if ghost or (ran - pol):
        t.fail("the run contains recipes this model folder does not define — wrong folder?")
    else:
        t.pass_(f"{len(defined) - len(dead_proc)} of {len(defined)} processes ran; "
                f"nothing ran that we did not offer")
    return t


# ══ 9 ══ CONSERVATION ════════════════════════════════════════════════════════════════
def t_conservation(M):
    """Per building: what came in, what it forwarded on, and what it delivered.

    Every BOM in this model is one component at quantity 1 and no process has a yield, so a
    building cannot create or destroy a parcel: in == forwarded + delivered, exactly, and a
    residual is a real modelling error rather than rounding. The split is the useful part — a
    building that forwards most of what it receives is a sort site, one that delivers most of it
    is a depot, and a building doing both is where the second sorts live.
    """
    t = Table(9, "CONSERVATION — in == forwarded + delivered, per building",
              ["facility", "in EA", "forwarded EA", "delivered EA", "delivers", "residual EA"],
              [30, 14, 15, 15, 11, 14], t_conservation.__doc__)
    inn, fwd, dlv = defaultdict(float), defaultdict(float), defaultdict(float)
    for f, q in M.flows():
        o, d = f["originname"], f["destinationname"]
        if d in M.facilities:
            inn[d] += q
        if o in M.facilities:
            (dlv if f["flowtype"] == "CustomerFulfillment" else fwd)[o] += q
    bad = []
    for n in sorted(M.facilities, key=lambda x: -inn[x]):
        out = fwd[n] + dlv[n]
        if inn[n] <= TOL and out <= TOL:
            continue
        res = inn[n] - out
        t.row(n, fmt(inn[n]), fmt(fwd[n]), fmt(dlv[n]), pct(dlv[n], inn[n]), fmt(res, 2))
        if abs(res) > TOL:
            bad.append((n, res))
    t.say()
    t.say("a parcel a building delivers leaves it on a CustomerFulfillment leg; one it passes on "
          "leaves on a Replenishment leg. Nothing else leaves, so the residual is 0.")
    if bad:
        for n, r in sorted(bad, key=lambda x: -abs(x[1]))[:10]:
            t.say(f"!! {n}: {fmt(r, 2)} EA unaccounted")
        t.fail(f"{len(bad)} buildings do not balance")
    else:
        t.pass_("every building balances to the parcel")
    return t


TABLES = [t_balance, t_demand, t_supply, t_flowconstraints, t_udc,
          t_workcentres, t_facilities, t_recipes, t_conservation]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.path.join(HERE, "outputs", "melbourne_optilogic_final"),
                    help="the folder that was uploaded")
    ap.add_argument("--run", default=os.path.join(HERE, "outputs", "run_outputs"),
                    help="the solved CSVs downloaded from Optilogic")
    ap.add_argument("--scenario", help="which scenarioname to keep, if the run has more than one")
    ap.add_argument("--table", nargs="+", type=int, help="only these table numbers")
    ap.add_argument("--csv", help="also write every table to this directory")
    a = ap.parse_args()

    M = Model(a.model, a.run)
    if a.scenario:
        if a.scenario not in M.scenarios:
            sys.exit(f"no scenario '{a.scenario}' in the run; it has {', '.join(M.scenarios)}")
        M.filter_scenario(a.scenario)

    print(f"MODEL  {a.model}")
    print(f"RUN    {a.run}   scenario: {M.scenario}"
          + (f"   (of {len(M.scenarios)}: {', '.join(M.scenarios)})" if len(M.scenarios) > 1 else ""))
    print(f"       {len(M.r['OptimizationFlowSummary']):,} flow rows, "
          f"{len(M.r['OptimizationProcessSummary']):,} process rows, "
          f"{len(M.r['OptimizationProductionSummary']):,} production rows, "
          f"{len(M.r['OptimizationWorkCenterSummary']):,} work-centre rows")

    want = set(a.table) if a.table else set(range(1, len(TABLES) + 1))
    built = []
    for i, fn in enumerate(TABLES, 1):
        if i in want:
            tb = fn(M)
            tb.print()
            built.append(tb)

    if a.csv:
        os.makedirs(a.csv, exist_ok=True)
        for tb in built:
            # tables 4, 5, 6 and 8 keep a wider row set than they print
            if getattr(tb, "detail", None):
                tb.cols, tb.body = tb.cols_detail, tb.detail
            print(f"wrote {tb.write(a.csv)}")

    print(f"\n{'=' * 100}\nVERDICT\n{'=' * 100}")
    for tb in built:
        print(f"  {'PASS' if tb.ok else 'FAIL'}  {tb.n}. {tb.title.split(' — ')[0]:<20} {tb.verdict}")
    bad = [tb.n for tb in built if not tb.ok]
    print(f"\n  {len(built) - len(bad)} of {len(built)} tables pass"
          + (f"; failed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
