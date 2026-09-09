"""Sankey of what the SOLVER decided.  ── model output, not measurement ──

    IN   outputs/run_outputs/OptimizationFlowSummary.csv  (+ the work-centre and production
         summaries, for the machine and sortation panels)
    OUT  outputs/sankey-from-optilogic.html

The other one is `sankey_from_scans.py`, which draws what the SCANS say actually happened.
Read them side by side: same columns, same node order, one is the network, one is the plan.

The map answers "which lanes carry what". This answers "what is the whole path" — every
stage from the source that creates volume to the sink that consumes it, with the width
of each ribbon proportional to EA/day.

  CHAIN 1  origin catchment → first-mile depot → hub → terminates
           (interstate export, or the local keep that never leaves the site)

  CHAIN 2  interstate / Victoria same-day / kept at depot → 1st building → 2nd building
           → delivering depot → delivered

NOTHING about the network's shape is hardcoded here. Facility roles are read off the
PRODUCT STATE in each flow row (``_Pickup`` / ``_XDock_<site>`` / ``_Despatch1_<site>`` /
``_<tag>_Despatch2`` / ``_Delivered``), so the arrival sites, the Victoria same-day
family and the cross-dock hop all appear on their own. The previous version carried a
three-hub ``HUB_OF`` dict and died with ``KeyError: 'PUD_Bayswater'`` the moment the
sorting depots joined the arrival set; a name list is exactly the wrong thing to keep in
a script that has the run's own product names in front of it.

That rule now holds for the IDENTITY too (2026-08-28). Which code is which building comes
from ``model_common`` — the join between the exporter's scan vocabulary and sites.csv —
and which origin clusters and which arrival sites this particular run carries is read off
the built model in ``outputs/melbourne_optilogic_final/``. Both used to be typed here, in
lists that had to agree with the chain-2 notebook, sites.csv and the exporter, and nothing
checked that they did.

Two columns are RESIDUALS, not flow rows, and they have to be: freight that is not
cross-docked rides no leg-6x lane, freight sorted once rides no leg-6 lane, and a depot
that sorts its own delivery volume rides no despatch lane at all. Each is computed as
"what came into this node, minus what left it on a lane", which is also what makes every
column of the diagram sum to the same total.

Run:  uv run python plotting/sankey_from_optilogic.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))               # ad-hoc tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the reduction lives there
import csv
import json
import os
import re
from collections import defaultdict

# the repo ROOT, not this file's folder: outputs/ sits beside the notebooks, and this
# script moved into plotting/ (2026-08-28) without its data moving with it
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(HERE, "outputs", "run_outputs")
SRC = os.path.join(RUN, "OptimizationFlowSummary.csv")
WCS = os.path.join(RUN, "OptimizationWorkCenterSummary.csv")
PRS = os.path.join(RUN, "OptimizationProductionSummary.csv")
PCS = os.path.join(RUN, "OptimizationProcessSummary.csv")
MODEL = os.path.join(HERE, "outputs", "melbourne_optilogic_final")
OUT = os.path.join(HERE, "outputs", "sankey-from-optilogic.html")

# ── THE FOUR SOURCE FAMILIES (Change 46) ─────────────────────────────────────────────
# The exporter has always derived four; chain 2 used to fold METRO and REGION into one "VIC"
# family, so this page could only ever draw three while sankey-facility-path.html drew four.
# The tag is the model's product token; the label is what the reader sees, and it says which
# way the parcel travelled rather than naming a band nobody outside the model would recognise.
FAM_LABEL = {"INTERSTATE": "Interstate", "MET": "Vic Metro to Metro",
             "REG": "Regional Vic to Metro Vic", "STG": "Kept at depot"}
ARR_FAMS = ("INTERSTATE", "MET", "REG")          # the families that ARRIVE somewhere
FAMILIES = ARR_FAMS + ("STG",)
FAM_RE = "|".join(ARR_FAMS)
SITE = r"[A-Z]{3}"                      # an arrival-site code, whichever ones the run has


# ── THE IDENTITY IS READ, NEVER TYPED (2026-08-28) ───────────────────────────────────
# `model_common` is the join between the exporter's SCAN vocabulary (which scan name is which
# building) and sites.csv's MODEL vocabulary (node name, display name, role). It postdates this
# script, which had been carrying its own copy of the code -> node map and its own list of origin
# clusters. Two hand-typed lists that have to agree with three other files is exactly the drift
# `model_common.check()` exists to catch, and this page had already been bitten once by it —
# see the note on SITE_FACILITY below.
from model_common import CODE_SITE                       # noqa: E402  (path set up above)

# Chain 1's LAST column and chain 2's FIRST column are the same parcels seen from either side, so
# they carry the same words: "<site> · <family>", the family named as FAM_LABEL names it. The site
# token is the three-letter code where the network has one and the short building name where it
# does not — which is what chain 2's own sink labels already do.
_SHORT_CODE = {n.replace("HUB_", "").replace("PUD_", ""): c for c, n in CODE_SITE.items()}
def sink_label(short_name, family):
    return f"{_SHORT_CODE.get(short_name, short_name.replace('_', ' '))} · {family}"


def _model_col(table, column):
    """One column of a table in the BUILT model, so the run's own shape answers for itself."""
    path = os.path.join(MODEL, f"{table}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} — the built model is what says which origin clusters and which arrival "
            f"sites this run has. Rebuild it (melbourne-optilogic-final.ipynb) or point MODEL "
            f"at the folder the run in outputs/run_outputs was uploaded from.")
    return [r[column] for r in csv.DictReader(open(path, encoding="utf-8-sig"))]


# The origin clusters, from the run's own pickup suppliers (`SUP_PKP_<cluster>_<postcode>`).
# LONGEST FIRST, and that is load-bearing: the regex below is an alternation, so DANDENONG would
# shadow DANDENONG_TR and quietly fold the two catchments into one if the order were alphabetical.
# The last segment is NOT required to be a postcode: chain 1 gained a REGIONAL origin
# (`SUP_PKP_REGIONAL_MPF`) that is lodged at a hub and has no van catchment, and a numeric
# tail silently excluded it — which then read as an unknown family and tripped the stale-run
# guard on a run that was not stale. `(.+)` stays greedy so DANDENONG_TR keeps its second word.
CLUSTERS = sorted({m.group(1) for m in
                   (re.match(r"^SUP_PKP_(.+)_[^_]+$", s) for s in _model_col("Suppliers",
                                                                            "suppliername"))
                   if m}, key=lambda c: (-len(c), c))

# obs_legs speaks in site CODES and the run speaks in facility names. Map the code to the
# FACILITY NAME and let `nice` do the shortening — writing the short label here by hand put
# "Tullamarine Facility" against nice()'s "Tullamarine PF" and silently split that lane in two,
# so the model read 0 on a lane it runs 6,335 EA of and the scans read 0 on the same lane.
# The map itself now comes from model_common; only WHICH codes chain 2 carries is decided here,
# and that is read off the built model's product names rather than repeated from the notebook.
# It matters that this is the run's set and not all eight sort sites: Avalon and Dandenong LC
# sort in the measurement but chain 2 folds them away, so a leg out of either can only ever read
# zero here and must be reported as "no node for it", not as a lane the solver declined.
SITE_CODES = sorted({m.group(1) for p in _model_col("Products", "productname")
                     for m in re.finditer(r"_(?:%s)_(%s)(?:_|$)" % (FAM_RE, SITE), p)})
_orphan = [c for c in SITE_CODES if c not in CODE_SITE]
assert not _orphan, (
    f"the run carries arrival site(s) {_orphan} that sites.csv has no building for — add the row "
    f"in inputs/factors_assumed/sites.csv, or the lane comparison will silently read them as "
    f"outside the model")
SITE_FACILITY = {c: CODE_SITE[c] for c in SITE_CODES}


def nice(n):
    return (n.replace("HUB_", "").replace("PUD_", "")
             .replace("CZ_Interstate_", "").replace("CZ_LocalTerm_", "")
             .replace("SUP_INT_", "").replace("SUP_MET_", "").replace("SUP_REG_", "")
             .replace("SUP_STAGE_", "")
             .replace("_", " ")
             # "Tullamarine Facility" is the longest label on the page and the only one that
             # needs two words to say what "Tullamarine" already says
             .replace("Tullamarine Facility", "Tullamarine PF"))


def check_run_matches_model():
    """THE RUN AND THE MODEL MUST SPEAK THE SAME VOCABULARY.

    This page draws `outputs/run_outputs/`, a solve downloaded from Optilogic, but reads the shape
    of the network out of `outputs/melbourne_optilogic_final/`, the model that was uploaded. Those
    two go stale independently: rebuild the model and the run is a solve of something else.

    Change 46 is exactly that case — chain 2 gained a fourth family, so the model has MET/REG
    products and the last solve has VIC ones. Nothing errored; 272,927 EA simply matched no rule
    and the page drew a third of the network as if the rest did not exist. A picture that is
    quietly wrong is worse than no picture, so this refuses rather than warns.
    """
    # chain 1 names its products after the origin CLUSTER (PP_OUTER_EAST_Despatch1_MGF), and a
    # cluster can be two words — matching the first token alone reported OUTER and SOUTH as
    # missing families. Drop chain 1 by PREFIX, then read the family token off what is left.
    c1 = tuple(f"{c}_{k}_" for c in ("EP", "PP") for k in CLUSTERS)
    def fam(names):
        out = set()
        for n in names:
            if n.startswith(c1):
                continue
            m = re.match(r"^[A-Z]{2}_([A-Z]+)_", n)
            if m:
                out.add(m.group(1))
        return out
    # the MODEL side is narrowed to the families this page knows; the RUN side is NOT, because
    # an unknown token in the run is precisely the thing being looked for. Narrowing both hid it.
    model = fam(_model_col("Products", "productname")) & set(FAMILIES)
    run = fam({r["productname"] for r in
               csv.DictReader(open(SRC, encoding="utf-8-sig"))})
    # ── AND THE SAME VOCABULARY IS NOT ENOUGH: the VOLUMES have to agree too ──────────
    # A dial change re-derives quantities without touching a single product name, so the family
    # test above passes on a solve of different numbers. That happened on 2026-09-07: dropping
    # CHAIN2_PEAK_UPLIFT moved 26,583 EA from the metro handover to interstate and the page would
    # have drawn the previous split with nothing to say it was doing so. Chain 1's sinks are the
    # cheapest place to notice, because the model states each one as a single demand row total.
    want = defaultdict(float)
    for r in csv.DictReader(open(os.path.join(MODEL, "CustomerDemand.csv"), encoding="utf-8-sig")):
        if r["customername"].startswith(("CZ_Interstate_", "CZ_LocalTerm_",
                                         "CZ_MetroTerm_", "CZ_Regional_", "CZ_PdoTerm_")):
            want[r["customername"]] += float(r["quantity"])
    got = defaultdict(float)
    for r in csv.DictReader(open(SRC, encoding="utf-8-sig")):
        if r["flowtype"] == "CustomerFulfillment" and r["destinationname"] in want:
            got[r["destinationname"]] += float(r["flowquantity"])
    drift = sorted(((k, got.get(k, 0.0), v) for k, v in want.items()
                    if abs(got.get(k, 0.0) - v) > max(1.0, 0.001 * v)), key=lambda x: -abs(x[1] - x[2]))
    if drift:
        raise SystemExit(
            f"\n  STALE RUN — the solve in {os.path.relpath(RUN, HERE)} carries different "
            f"VOLUMES from the model in {os.path.relpath(MODEL, HERE)}. The product names still "
            f"agree, so this is a dial change, not a restructure:\n"
            + "\n".join(f"      {k:<36} run {a:>10,.0f}   model {b:>10,.0f}   {a-b:>+10,.0f}"
                         for k, a, b in drift[:8])
            + f"\n  Re-upload {os.path.relpath(MODEL, HERE)} to Optilogic, re-run NEO, and "
              f"download the new summaries into {os.path.relpath(RUN, HERE)}.")

    missing = run - model
    if missing:
        raise SystemExit(
            f"\n  STALE RUN — the solve in {os.path.relpath(RUN, HERE)} carries product families "
            f"{sorted(missing)} that the model in {os.path.relpath(MODEL, HERE)} no longer has "
            f"(it now has {sorted(model)}).\n"
            f"  The model was rebuilt after this solve was downloaded. Re-upload "
            f"{os.path.relpath(MODEL, HERE)} to Optilogic, re-run NEO, and download the new\n"
            f"  OptimizationFlowSummary/WorkCenterSummary/ProcessSummary into "
            f"{os.path.relpath(RUN, HERE)}. Drawing this run against this model would show a "
            f"network neither of them describes.")
    return model


def read_rows():
    for r in csv.DictReader(open(SRC, encoding="utf-8-sig")):
        q = float(r["flowquantity"] or 0)
        if q > 0:
            yield r, q, r["originname"], r["destinationname"], r["productname"], r["productname"][:2]


def build():
    """Turn the solved flow rows into two column-by-column Sankeys."""
    c1n, c1l = {}, defaultdict(float)     # nodes, links keyed (s, t, class, colour-key)
    c2n, c2l = {}, defaultdict(float)
    # what leaves each chain-2 node on a real lane, so the residual links can be derived
    landed, second_out = defaultdict(float), defaultdict(float)
    second_at, despatch_out = defaultdict(float), defaultdict(float)
    onward_out = defaultdict(float)      # despatched from the site that sorted it: 1 building
    onward_at = defaultdict(float)       # arrived at ONWARD from this site
    stage_at, delivered_at = defaultdict(float), defaultdict(float)
    unknown = []

    def node(store, nid, col, label, sub=""):
        store.setdefault(nid, {"id": nid, "col": col, "label": label, "sub": sub})

    def c2(s, t, cls, key, v, kind="flat"):
        # `kind` is what lets the page isolate a movement. The second sort is 4.7% of the day
        # and sits among ribbons twenty times its width — without a filter it is invisible,
        # which is a bad way to present the number the whole sort-capacity argument turns on.
        c2l[(s, t, cls, key, kind)] += v

    for r, q, o, d, p, cls in read_rows():
        # ── CHAIN 1 — collection, and where it terminates ─────────────────────────
        m = re.match(r"^SUP_PKP_(%s)_" % "|".join(CLUSTERS), o)
        if m and p.endswith("_Pickup"):
            # Regional lodgement happens AT a hub, so it has no first-mile depot to draw. The
            # ribbon spans column 1, which check_columns already counts towards every column it
            # crosses — the freight is in the diagram, it just took a shorter road.
            _hub = d.startswith("HUB_")
            node(c1n, f"C:{m.group(1)}", 0, m.group(1).replace("_", " "),
                 "lodged at the hub" if _hub else "origin catchment")
            node(c1n, f"{'H' if _hub else 'P'}:{d}", 2 if _hub else 1, nice(d),
                 "round-1 hub" if _hub else "first-mile depot")
            c1l[(f"C:{m.group(1)}", f"{'H' if _hub else 'P'}:{d}", cls, "PICKUP", "arrive")] += q
            continue
        if o.startswith("PUD_") and d.startswith("HUB_") and p.endswith("_Pickup"):
            node(c1n, f"P:{o}", 1, nice(o), "first-mile depot")
            node(c1n, f"H:{d}", 2, nice(d), "round-1 hub")
            c1l[(f"P:{o}", f"H:{d}", cls, "PICKUP", "despatch")] += q
            continue
        # ONE NODE PER BUILDING, not one per family. Chain 1 now terminates its metro volume at
        # the ten buildings chain 2 measures it entering the delivery stream at, and keeps volume
        # at five depots rather than three — pooling those into "Kept on site" / "Stays in
        # Melbourne" would hide the very thing the sinks were rebuilt to say.
        if d.startswith("CZ_LocalTerm_"):
            _b = d[len("CZ_LocalTerm_"):]
            node(c1n, f"P:{o}", 1, nice(o), "first-mile depot")
            node(c1n, f"K:{_b}", 3, sink_label(_b, FAM_LABEL["STG"]),
                 "sink · kept at the depot that collected it")
            c1l[(f"P:{o}", f"K:{_b}", cls, "LOCAL", "stage")] += q
            continue
        if d.startswith("CZ_Interstate_") and "_INTERSTATE_" not in p:
            node(c1n, f"H:{o}", 2, nice(o), "round-1 hub")
            node(c1n, "X:OUT", 3, "Leaves Melbourne", "sink · interstate export")
            c1l[(f"H:{o}", "X:OUT", cls, "EXPORT", "deliver")] += q
            continue
        # The other half of what used to be one export sink. It stands in the SAME hub building
        # the interstate sink does — one node, like interstate and regional, because the split is
        # in the ledger and the destination never moved.
        if d.startswith("CZ_PdoTerm_"):
            node(c1n, f"H:{o}", 2, nice(o), "round-1 hub")
            node(c1n, "P:PDO", 3, "PDO terminate", "sink · metro-bound, handed over at the hub")
            c1l[(f"H:{o}", "P:PDO", cls, "PDOTERM", "deliver")] += q
            continue
        # The handover, added when chain 1 was rebuilt on the peak basis: metro-bound volume
        # leaves the collection entity here so that chain 2, which already books every metro
        # delivery, is not asked to book it a second time.
        if d.startswith("CZ_MetroTerm_"):
            _b = d[len("CZ_MetroTerm_"):]
            node(c1n, f"H:{o}", 2, nice(o), "round-1 hub")
            node(c1n, f"M:{_b}", 3, sink_label(_b, FAM_LABEL["MET"]),
                 "sink · chain 2 collects it here")
            c1l[(f"H:{o}", f"M:{_b}", cls, "METROTERM", "deliver")] += q
            continue
        if d.startswith("CZ_Regional_"):
            node(c1n, f"H:{o}", 2, nice(o), "round-1 hub")
            # FROM regional Victoria, not to it: this is regional PICKUP, lodged at the hub
            # and terminated there. The model does not follow it any further.
            node(c1n, "R:REG", 3, "Regional pickup", "sink · lodged and ended at the hub")
            c1l[(f"H:{o}", "R:REG", cls, "REGTERM", "deliver")] += q
            continue

        # ── CHAIN 2 — the delivery entity, six columns ────────────────────────────
        # 4 / 4v — a supplier lands freight at the site that will sort it
        _sup = {"SUP_INT_": "INTERSTATE", "SUP_MET_": "MET", "SUP_REG_": "REG"}
        if o.startswith(tuple(_sup)):
            fam = _sup[next(k for k in _sup if o.startswith(k))]
            node(c2n, f"S:{fam}", 0, FAM_LABEL[fam], "source")
            # `A:`, NOT a separate `L:` node. Until 2026-08-31 this wrote `L:{d}` ("landed at"),
            # the name column 1 carried under the OLD six-column chain 2 (source -> landed at ->
            # first sort -> despatched from -> depot -> delivered). Change 44 renamed the columns
            # to 1st/2nd building and gave them the `A:`/`B:` ids, and converted the stage branch
            # below, but not this one — so every arrival site had TWO nodes sitting in column 1
            # under the same label: `L:` taking the whole source ribbon and passing nothing on,
            # and `A:` feeding column 2 with nothing coming in. The column read 320,046 EA against
            # 167,431 everywhere else, and the source ribbon terminated in mid-diagram.
            node(c2n, f"A:{d}", 1, nice(d), "1st building")
            c2(f"S:{fam}", f"A:{d}", cls, fam, q, "arrive")
            landed[d] += q
            continue
        # 5 — the overnight stage is already standing at the depot that delivers it: ZERO
        # buildings, which is the facility-path page's own zero bucket
        if o.startswith("SUP_STAGE_"):
            # KEPT AT DEPOT, the exporter's own word for it (`kept_on_site` /
            # `compute_kept_on_site` in export_chain2_factors.py, family METRO_DEPOT). The page
            # used to say "Overnight stage" at the source and "No building" across the two
            # building columns, which is three names for one thing and none of them the name
            # the measurement uses. It is not "no building" either — it is a building, the
            # depot's own, and the parcel simply never left it.
            node(c2n, "S:STG", 0, "Kept at depot", "source · yesterday's carry")
            node(c2n, "A:STG", 1, "Kept at depot", "never left its depot")
            node(c2n, "B:STG", 2, "Kept at depot", "no second building")
            node(c2n, f"D:{d}", 3, nice(d), "delivering depot")
            for _a, _b in (("S:STG", "A:STG"), ("A:STG", "B:STG"), ("B:STG", f"D:{d}")):
                c2(_a, _b, cls, "STG", q, "stage")
            stage_at[d] += q
            continue
        # 6x / 6 — A SECOND BUILDING, whichever mechanism took it there. Change 44 merged
        # the two: a cross-dock hop and a round-2 leg are one truck between two buildings, and
        # the measurement stopped telling them apart, so the picture must not either. The
        # cross-dock recipe is inert on the facility-path basis (6 products, 0 EA in this run),
        # but the rule stays wired so the column is right if XDOCK_ENABLED is ever turned back on.
        m = re.search(r"_XDock_(%s)$" % SITE, p)
        if m:
            node(c2n, f"A:{o}", 1, nice(o), "1st building")
            node(c2n, f"B:{d}", 2, nice(d), "2nd building")
            c2(f"A:{o}", f"B:{d}", cls, "INTERSTATE", q, "round2")
            second_out[o] += q
            second_at[d] += q
            continue
        m = re.search(r"_(%s)_Despatch1_(%s)$" % (FAM_RE, SITE), p)
        if m and (d.startswith("PUD_") or d.startswith("HUB_")):
            node(c2n, f"A:{o}", 1, nice(o), "1st building")
            node(c2n, f"B:{d}", 2, nice(d), "2nd building")
            c2(f"A:{o}", f"B:{d}", cls, m.group(1), q, "round2")
            second_out[o] += q
            second_at[d] += q
            continue
        # 7 / 7c — the despatch leg out to a delivering depot, from the LAST building
        m = re.search(r"_(%s)_(%s)_Despatch2R?$" % (FAM_RE, SITE), p)
        if m and d.startswith("PUD_"):
            # ONE BUILDING OR TWO, AND THE PRODUCT NAME SAYS WHICH — no allocation needed.
            # `EP_INTERSTATE_MPF_Despatch2` leaving MPF is freight that landed at MPF, was
            # sorted there and left: ONE building. The same product leaving Sunshine West is
            # freight that landed at MPF and travelled: TWO. So the despatch leg starts at the
            # ONWARD node in the first case and at B:<site> in the second, and the split is
            # exact rather than a share of a residual.
            _own = SITE_FACILITY.get(m.group(2)) == o
            _src = f"ONWARD:{o}" if _own else f"B:{o}"
            if _own:
                node(c2n, _src, 2, "On to the depot", f"sorted once at {nice(o)}, no 2nd site")
            else:
                node(c2n, f"B:{o}", 2, nice(o), "2nd building")
            node(c2n, f"D:{d}", 3, nice(d), "delivering depot")
            c2(_src, f"D:{d}", cls, m.group(1), q, "onward" if _own else "despatch")
            (onward_out if _own else despatch_out)[o] += q
            continue
        # 8 — the van run, labelled by where the parcel entered the network
        m = re.search(r"_(%s|STG)_(.+)_Delivered$" % FAM_RE, p)
        if m and d.startswith("CZ_"):
            fam = m.group(1)
            # One node per ARRIVAL SITE for the two same-day families; the overnight stage
            # collapses to a single node. Keeping it per depot produced eleven identical
            # "own stage" labels, some of them 19 EA, stacked on top of each other.
            vid = "V:STG" if fam == "STG" else f"V:{fam}:{m.group(2)}"
            lab = ("Kept at depot" if fam == "STG"
                   else f"{m.group(2)} · {FAM_LABEL[fam]}")
            node(c2n, f"D:{o}", 3, nice(o), "delivering depot")
            node(c2n, vid, 4, lab, "sink")
            c2(f"D:{o}", vid, cls, fam, q, "deliver")
            delivered_at[o] += q
            continue
        unknown.append((o, d, p, q))

    # ── the residual links: the freight that rides no lane at this stage ──────────
    # Not decoration — without them a column loses volume and every ribbon downstream
    # is drawn too thin. Class is lost here (a residual is a difference of totals, and
    # the class split of a difference is not recoverable), so it is booked to PP/EP in
    # proportion to what the node did carry.
    def split_by_class(links, node_id, total, col_from, col_to, tgt, key, kind="flat"):
        seen = defaultdict(float)
        for (s, t, c, k, _kind), v in list(links.items()):
            if s == node_id:
                seen[c] += v
        base = sum(seen.values())
        if base <= 0:
            links[(node_id, tgt, "PP", key, kind)] += total
            return
        for c, v in seen.items():
            links[(node_id, tgt, c, key, kind)] += total * v / base

    # ── ONE BUILDING: "ON TO THE DEPOT", NOT A FAKE SECOND BUILDING ─────────────────
    # This used to draw A:<site> -> B:<site>, the same building repeated across two columns,
    # and every reader took it for a second building — the columns both read 167,431 and 78%
    # of the ribbons between them were that repeat. sankey-facility-path.html had already
    # settled the right answer for the measurement side and said so in its own header:
    # "shorter journeys are not padded with a fake building: they flow into a grey 'on to the
    # depot' node and ride it to the right". This is that node. Its width in the 2nd-building
    # column is exactly the volume that finished in one building.
    for site, v in landed.items():
        rest = v - second_out.get(site, 0.0)
        if rest > 1e-6:
            node(c2n, f"A:{site}", 1, nice(site), "1st building")
            node(c2n, f"ONWARD:{site}", 2, "On to the depot",
                 f"sorted once at {nice(site)}, no 2nd site")
            split_by_class(c2l, f"A:{site}", rest, 1, 2, f"ONWARD:{site}", "INTERSTATE",
                           "onward")
            onward_at[site] += rest
    # ── PASS-THROUGH: a site that FORWARDS finished freight it never sorted ───────────
    # The solver runs Sunshine West as a transshipment yard: EP_INTERSTATE_TPF_Despatch2 comes
    # in from Tullamarine and the same product goes straight back out to Dandenong South,
    # Pakenham and Bayswater without being opened. Those parcels touched TWO buildings (TPF,
    # then SWP). Both legs are already on the page and both are mis-labelled: the FIRST rides
    # `ONWARD:TPF -> D:SWP` (own flavour, PUD destination — it reads as "delivered by Sunshine
    # West"), and the second rides `B:SWP -> D:<depot>`, which is right but leaves B:SWP
    # despatching freight nothing brought it. So the repair is to move the first leg, per lane.
    #
    # THE QUANTITY COMES FROM `relay_measured()`, NOT FROM A RESIDUAL (2026-09-02). This used to
    # size the move as `excess = despatch_out[site] - second_at[site]` — foreign-flavoured
    # despatch out, minus round-2 arrivals in. That subtraction is exact only if every parcel a
    # site round-2 sorts leaves again on a despatch lane, and at the two sites that relay, most
    # of it does not: Sunshine West DELIVERS 6,921 of its 10,039 round-2 sorts itself and
    # Melbourne North 3,881 of 5,637, so `second_at` swamped `despatch_out`, `excess` went
    # negative and the relay hiding in the same subtraction became invisible. The page drew
    # 38,768 second buildings against a solved 42,677 and put the missing 3,909 EA in the grey
    # "finished in one building" node. `min(in, out)` per (building, product) measures the relay
    # directly and cannot be masked, which is what relay_measured() has always returned — the
    # page just was not built from it.
    #
    # The detector got WORSE as the model got better, which is the tell that it was the wrong
    # shape: narrowing the round-2 band raised second_at at exactly the relaying sites, so the
    # old test found 7,587 of a 15,274 relay before the band patch and 647 of 4,556 after it.
    passthru = defaultdict(float)
    _rl = relay_measured()
    for (o, site), want in sorted(_rl["lanes"].items(), key=lambda kv: -kv[1]):
        if o == site:
            continue
        # the mis-labelled first leg, which is the only place this volume can come from
        leg = [k for k in c2l if k[0] == f"ONWARD:{o}" and k[1] == f"D:{site}" and c2l[k] > 0]
        avail = sum(c2l[k] for k in leg)
        take = min(want, avail)
        if take <= 1e-6:
            print(f"  !! {nice(o)}->{nice(site)} relays {want:,.0f} EA but no ONWARD leg carries "
                  f"it — column 2 will read that much low")
            continue
        # BOTH legs have to move, or the parcel is despatched twice. It was booked as
        # "sorted once at o, then delivered by site" — A:o -> ONWARD:o -> D:site. It is
        # really "sorted at o, handled at site, delivered somewhere else", so the first
        # leg into D:site goes away and the freight becomes a real A:o -> B:site move.
        for k in leg:                            # it does not end its journey at D:site
            c2l[k] -= take * c2l[k] / avail
        room = [k for k in c2l if k[0] == f"A:{o}" and k[1] == f"ONWARD:{o}" and c2l[k] > 0]
        _r = sum(c2l[k] for k in room)
        if _r <= 1e-6:
            print(f"  !! nothing left in ONWARD:{nice(o)} to debit {take:,.0f} EA against")
            continue
        for k in room:                           # nor was o its only building
            cut = take * c2l[k] / _r
            c2l[k] -= cut
            # KIND "relay", not "round2": both are a second building, but only one is a
            # second SORT. Drawn as round2 the page could not show the difference, and the
            # difference is the whole finding — freight reaches a building that never opens it.
            # Everything that asks "did it move to another building" must test BOTH.
            c2(k[0], f"B:{site}", k[2], k[3], cut, "relay")
        node(c2n, f"B:{site}", 2, nice(site), "2nd building")
        onward_at[o] -= take
        onward_out[o] -= take
        second_at[site] += take
        passthru[(o, site)] += take
        if want - take > 0.5:
            print(f"  !! {nice(o)}->{nice(site)}: only {take:,.0f} of {want:,.0f} EA of relay "
                  f"could be moved — the rest has no ONWARD leg to come from")
    if passthru:
        print("  relay (finished freight forwarded by a site that did not sort it): "
              + ", ".join(f"{nice(a)}->{nice(b)} {v:,.0f}"
                          for (a, b), v in sorted(passthru.items(), key=lambda x: -x[1]))
              + f"  = {sum(passthru.values()):,.0f} EA")
    # ── A THIRD BUILDING CANNOT BE DRAWN, SO ITS HOP MUST BE UNDRAWN ────────────────
    # `X_Despatch2R` sorted at TPF, railed to Sunshine West and forwarded from there puts the
    # parcel at TWO `B:` nodes — leg 7 books `B:TPF -> D:Sunshine West` for the first hop and
    # `B:Sunshine West -> D:<depot>` for the second — so columns 2 and 3 carry it twice and the
    # diagram stops balancing. It is one parcel in three buildings and the page has room for two.
    # The hop is therefore collapsed: the leg INTO the relaying site is removed and the leg OUT
    # of it is re-based on the building that sorted it, which is the same repair the second-
    # building block makes, one hop later. The parcel keeps its first two buildings and the
    # relaying site disappears, which is what a depth-2 page can honestly say about it.
    for (came_from, site), want in sorted(_rl["third_lanes"].items(), key=lambda kv: -kv[1]):
        legs_in = [k for k in c2l if k[0] == f"B:{came_from}" and k[1] == f"D:{site}" and c2l[k] > 0]
        # the link key is (source, target, class, colour-key, KIND) — kind is k[4], and reading
        # k[3] here silently matched nothing, so the collapse ran on an empty list and the
        # columns stayed 449 EA apart while the code looked like it had done the work
        legs_out = [k for k in c2l if k[0] == f"B:{site}" and k[4] == "despatch" and c2l[k] > 0]
        avail = min(sum(c2l[k] for k in legs_in), sum(c2l[k] for k in legs_out))
        take = min(want, avail)
        if take <= 1e-6:
            print(f"  !! third-building hop {nice(came_from)}->{nice(site)} {want:,.0f} EA could "
                  f"not be collapsed (in {sum(c2l[k] for k in legs_in):,.0f}, "
                  f"out {sum(c2l[k] for k in legs_out):,.0f}) — columns will not balance")
            continue
        _in = sum(c2l[k] for k in legs_in)
        for k in legs_in:                     # it did not end its journey at D:<relaying site>
            c2l[k] -= take * c2l[k] / _in
        _out = sum(c2l[k] for k in legs_out)
        for k in list(legs_out):              # and it left from the building that SORTED it
            cut = take * c2l[k] / _out
            c2l[k] -= cut
            c2(f"B:{came_from}", k[1], k[2], k[3], cut, "despatch")
        second_at[site] -= take
        despatch_out[site] -= take

    if _rl["third"] > 0.5:
        # A THIRD BUILDING, WHICH A TWO-BUILDING DIAGRAM CANNOT DRAW. Reported rather than
        # absorbed: the parcel was relayed twice, so its 3rd building is drawn as the
        # DELIVERING depot and the page understates the journey by that much. Saying so is the
        # honest option; silently folding it into column 2 would double-count the parcel.
        print(f"  {_rl['third']:,.0f} EA is relayed a SECOND time — a 3rd building, drawn here "
              f"as the delivering depot because the diagram stops at two")

    # ── what the ONWARD stream still owes: the depot that DELIVERS its own single-sort ──
    # A site that sorted a parcel once and also delivers it rides no despatch lane at all, so
    # the ribbon out of ONWARD is the difference, per site, and it lands on that site's depot.
    for site in sorted(onward_at):
        rest = onward_at[site] - onward_out.get(site, 0.0)
        if rest > 1e-6:
            node(c2n, f"ONWARD:{site}", 2, "On to the depot",
                 f"sorted once at {nice(site)}, no 2nd site")
            node(c2n, f"D:{site}", 3, nice(site), "delivering depot")
            split_by_class(c2l, f"ONWARD:{site}", rest, 2, 3, f"D:{site}", "INTERSTATE",
                           "onward")
        elif rest < -0.5:
            print(f"  !! ONWARD despatches {-rest:,.0f} EA more than {nice(site)} sorted once")

    # and the same for a SECOND building that delivers what it sorted
    for site, v in sorted(second_at.items()):
        rest = v - despatch_out.get(site, 0.0)
        if rest > 1e-6:                       # this building DELIVERS it: no despatch lane
            node(c2n, f"B:{site}", 2, nice(site), "2nd building")
            node(c2n, f"D:{site}", 3, nice(site), "delivering depot")
            split_by_class(c2l, f"B:{site}", rest, 2, 3, f"D:{site}", "INTERSTATE")

    # ── ONE "On to the depot" CELL, NOT SIX ──────────────────────────────────────────
    # The per-site ids above are load-bearing right up to this point: a forwarded parcel is
    # despatched twice, and BOTH legs have to be debited against the site that sorted it, which
    # a merged node has no origin to do — that is what put the columns 6,922 EA out when this
    # was tried as a single node from the start. Once the pass-through is settled the split has
    # no reader value: six grey bands all saying the same thing, in a column whose whole job is
    # to separate "finished here" from "went on to a second building". So build exact, then
    # collapse for display. The ribbons INTO it stay per site, so which site finished the
    # journey is still on the page and still in the cell sheet.
    for _nid in [n for n in list(c2n) if n.startswith("ONWARD:")]:
        del c2n[_nid]
    _onward = {k: v for k, v in c2l.items()
               if k[0].startswith("ONWARD:") or k[1].startswith("ONWARD:")}
    if _onward:
        node(c2n, "ONWARD", 2, "On to the depot", "finished in one building")
        for k, v in _onward.items():
            del c2l[k]
            _s = "ONWARD" if k[0].startswith("ONWARD:") else k[0]
            _t = "ONWARD" if k[1].startswith("ONWARD:") else k[1]
            c2l[(_s, _t, k[2], k[3], k[4])] += v

    def pack(nodes, links):
        L = [{"s": s, "t": t, "c": c, "h": h, "k": k, "v": round(v, 2)}
             for (s, t, c, h, k), v in links.items() if v > 0.5]
        L.sort(key=lambda x: -x["v"])
        return {"nodes": list(nodes.values()), "links": L}

    def check_columns(name, nodes, links, cols):
        """EVERY COLUMN CARRIES THE SAME VOLUME. That is the whole claim a Sankey makes, and
        the page prints the column totals as if it were guaranteed. It is not: two defects that
        broke it (a duplicated column-1 node, and freight forwarded by a site that never sorted
        it) both sat there for weeks reading plausibly. So it is checked, not assumed."""
        inn, out = defaultdict(float), defaultdict(float)
        for l in links:
            out[l["s"]] += l["v"]
            inn[l["t"]] += l["v"]
        col = {n["id"]: n["col"] for n in nodes}
        tot = defaultdict(float)
        for n in nodes:
            tot[n["col"]] += max(inn[n["id"]], out[n["id"]])
        # A LINK MAY SPAN A COLUMN, and that is not a leak. Chain 1's local keep goes from the
        # first-mile depot straight to its sink and never sees a hub, so the hub column really
        # does handle 4,342 EA less — the freight is still in the diagram, just not in that
        # column. Count a spanning ribbon towards every column it crosses, so the test measures
        # volume that has VANISHED rather than volume that took a shorter road.
        for l in links:
            for c in range(col[l["s"]] + 1, col[l["t"]]):
                tot[c] += l["v"]
        span = max(tot.values()) - min(tot.values())
        if span > max(1.0, 0.001 * max(tot.values())):
            print(f"  !! {name}: columns do not carry the same volume — "
                  + ", ".join(f"{cols[c]} {tot[c]:,.0f}" for c in sorted(tot))
                  + f"  (spread {span:,.0f} EA)")
        return tot

    stage_rows = sorted(
        ({"f": nice(f), "stage": round(v), "delivered": round(delivered_at.get(f, 0)),
          "share": round(v / delivered_at[f], 4) if delivered_at.get(f) else 0}
         for f, v in stage_at.items()), key=lambda r: -r["stage"])

    if unknown:
        tot = sum(q for *_, q in unknown)
        print(f"  !! {len(unknown)} flow rows ({tot:,.0f} EA) matched no stage rule — "
              f"e.g. {unknown[0][2]} on {nice(unknown[0][0])} -> {nice(unknown[0][1])}")

    def moved_note(nodes, links, cols):
        """How much of each column ARRIVED FROM SOMEWHERE ELSE, as a share.

        The reason this exists: a Sankey column carries the same total as every other column,
        so "1st building 167,431 / 2nd building 167,431" is true and reads as "everything
        touched two buildings". It does not — 78% of the ribbons between those two columns are
        FLAT, running from `A:Melbourne Parcel` to `B:Melbourne Parcel`, the same building drawn
        twice. Only the kept-at-depot band was legible, because it is labelled "Kept at depot"
        in both columns while every other flat band repeats a site name and looks like two
        different sites. So the share goes in the column header, where the eye already is.
        """
        # THE RIBBON'S KIND, not its endpoint names. Comparing names worked while a finished
        # journey was drawn as `A:MPF -> B:MPF`; once those collapsed into one `ONWARD` node the
        # names stopped matching and the figure jumped to 91%. The kind is what the rule was
        # always about: `round2` (and the cross-dock hop) are a second building, `onward`,
        # `stage` and `flat` are not.
        MOVE = ("round2", "xdock", "relay")
        col = {n["id"]: n["col"] for n in nodes}
        tot, moved = defaultdict(float), defaultdict(float)
        for l in links:
            c = col[l["t"]]
            tot[c] += l["v"]
            if l["k"] in MOVE:
                moved[c] += l["v"]
        # ONLY where the question means something: between two BUILDING columns. Column 1 also
        # has "flat" inbound (the kept-at-depot band, S:STG -> A:STG) and column 3 has the
        # depot-delivers-its-own residual, but "did it move to a different building" is not
        # what either of those is asking, and annotating them was three numbers of noise
        # around the one that matters.
        pair = lambda i: (i > 0 and "building" in cols[i].lower()
                          and "building" in cols[i - 1].lower())
        return ["" if not pair(i) or not tot.get(i)
                else f"{moved[i] / tot[i]:.0%} a different building"
                for i in range(len(cols))]

    C1COLS = ["Origin catchment", "First-mile depot", "Round-1 hub", "Terminates"]
    C2COLS = ["Source", "1st building", "2nd building", "Delivering depot", "Delivered"]
    _p1, _p2 = pack(c1n, c1l), pack(c2n, c2l)
    check_columns("chain 1", _p1["nodes"], _p1["links"], C1COLS)
    check_columns("chain 2", _p2["nodes"], _p2["links"], C2COLS)
    _n1 = moved_note(_p1["nodes"], _p1["links"], C1COLS)
    _n2 = moved_note(_p2["nodes"], _p2["links"], C2COLS)

    return {
        "1": {"title": "Chain 1 — collection, and where it terminates",
              "note": "Nothing in this chain reaches a Melbourne delivery zone. It is a separate "
                      "daily activity, run under today's assumptions.",
              "cols": C1COLS,
              "frac": [0, 0.34, 0.68, 1.0], "moved": _n1,
              **_p1},
        "2": {"title": "Chain 2 — everything that feeds a van",
              "note": "Three measured sources, then the BUILDINGS the parcel passes through, in "
                      "order — the same question sankey-facility-path.html asks of the scans, so "
                      "the two pages can be read column against column. Freight that touches one "
                      "building rides no lane into the second column and is drawn flat.",
              "cols": C2COLS,
              "frac": [0, 0.20, 0.44, 0.80, 1.0], "moved": _n2,
              **_p2},
        "stage": stage_rows,
    }


def _stage_of(step, product):
    """Which production stage a ProcessSummary row is, from the machine step and what it made."""
    if step == "BAG_UNLOAD":
        return "UNLOAD0"
    if step == "DRIVER_WAVE":
        return "DRIVER"
    if step.startswith("UNLOAD"):
        return "UNLOAD2" if product.endswith("Unloaded2") else "UNLOAD"
    if step.startswith("SORT"):
        return "SORT0" if product.endswith("Sort0") else \
               "SORT2" if product.endswith("Sorted2") else "SORT"
    if step.startswith("LOAD"):
        return "LOAD2" if product.endswith(("Despatch2", "Despatch2R")) else "LOAD"
    return None


def _is_chain2(product):
    """Chain 2's families all name themselves in the product; chain 1 carries a catchment.

    READ FROM `FAMILIES`, never typed. This was a hand-written tuple carrying `_VIC_`, the token
    Change 46 split into `_MET_` / `_REG_`, and nothing checked that it still agreed with the
    families the rest of the page draws. No product in the run matched it any more, so 22,263 EA
    of chain-2 round-2 sorts were booked to chain 1 and the sortation panel read
    "6.8% sorted twice / 29,185 EA forwarded" (truth: 21.3% / 6,922) and 3.3 chain-1 sorts per
    collected parcel. Three identities say the tuple is right: chain-2 round-1 sorts == same_day
    (one sort each), forwarded == the pass-through print, chain-1 sorts == collected.
    """
    return any(f"_{k}_" in product for k in FAMILIES)


def process_rows():
    """(facility, stage, product, qty) per machine touch, from whichever summary was downloaded.

    OptimizationProcessSummary is preferred: it is the MACHINE's own throughput, which is what a
    "how many times was this sorted" question is actually asking, and it is written on every run.
    OptimizationProductionSummary counts the same touches by BOM family and is the fallback for
    an older download; the two agree to the parcel.
    """
    if os.path.exists(PCS):
        for r in csv.DictReader(open(PCS, encoding="utf-8-sig")):
            q = float(r["processedquantity"] or 0)
            if q <= 0:
                continue
            st = _stage_of(r["currentstepname"] or "", r["productname"] or "")
            if st:
                yield r["facilityname"], st, r["productname"] or "", q
    elif os.path.exists(PRS):
        for r in csv.DictReader(open(PRS, encoding="utf-8-sig")):
            q = float(r["productionquantity"] or 0)
            m = re.match(r"^BOM_([A-Z0-9]+)", r["bomname"] or "")
            if q > 0 and m and m.group(1) in STAGE_LABEL:
                yield r["facilityname"], m.group(1), r["productname"] or "", q


_RELAY = {}          # relay_measured() reads the 7 MB flow file; build() and sortation() share it


def relay_measured():
    """What the SOLVER relays: finished freight that arrives at a building and leaves again.

    BOTH HALVES OF THE SPLIT COUNT. `split_despatch2_by_route.py` (2 Sep) gave the round-2 route
    its own `..._Despatch2R` product, and every test here reads `Despatch2R?` so a relay of either
    half is seen. Matching only `Despatch2` reported a relay of ZERO and 28,679 EA of flow that
    fitted no stage rule, which reads as a clean model and is not one.

    A Despatch2 product that comes INTO a site and goes back OUT of it was never opened there —
    no `Sorted2`, no machine touch, no sortation cost — but the parcel WAS in that building, which
    is what a scan measures. `min(in, out)` per product is that volume exactly.

    Split by whether the relaying site is the parcel's SECOND building or a later one: the
    product flavour names the site that SORTED it, the flow row names where the truck came from,
    and when those differ the parcel had already moved once, so the relay is a third building the
    PATH_DEPTH=2 comparison cannot hold.

    This is measured here rather than inferred because the page's own pass-through block
    (`excess = despatch_out - second_at`) detects only part of it — see the note there.
    """
    if _RELAY:
        return _RELAY
    inn, out, src = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in csv.DictReader(open(SRC, encoding="utf-8-sig")):
        q = float(r["flowquantity"] or 0)
        p_, o, d = r["productname"], r["originname"], r["destinationname"]
        if q <= 0 or not p_.endswith(("Despatch2", "Despatch2R")):
            continue
        if d.startswith(("PUD_", "HUB_")):
            inn[(d, p_)] += q
            src[(d, p_, o)] += q
        if o.startswith(("PUD_", "HUB_")):
            out[(o, p_)] += q
    second, third = 0.0, 0.0
    lanes = defaultdict(float)
    third_lanes = defaultdict(float)
    for (site, p_), v in out.items():
        rel = min(v, inn.get((site, p_), 0.0))
        if rel <= 1e-6:
            continue
        m = re.search(r"_(%s|STG)_([A-Z]{3})_Despatch2R?$" % FAM_RE, p_)
        sorted_at = SITE_FACILITY.get(m.group(2)) if m else None
        base = inn[(site, p_)]
        for (dd, pp, o), sv in src.items():
            if (dd, pp) != (site, p_):
                continue
            share = rel * sv / base
            if o == sorted_at:
                second += share
                lanes[(o, site)] += share
            else:
                third += share
                third_lanes[(o, site)] += share
    _RELAY.update(second=second, third=third, total=second + third,
                  lanes={k: v for k, v in lanes.items() if v > 0.5},
                  third_lanes={k: v for k, v in third_lanes.items() if v > 0.5})
    return _RELAY


def sortation(data):
    """How many times does a parcel meet a sorting machine? Model vs measurement.

    Chain 1 is excluded from BOTH sides: its collection sorts are counted separately and its
    parcels never enter the denominator, which is chain 2's same-day delivery volume.

    The comparison that means something is over SAME-DAY freight. Staged freight was sorted on
    the day it arrived, which a one-day model has no way to book, so including it flatters the
    measurement and understates the model by the same 26%.
    """
    fam = defaultdict(float)
    for _f, st, product, q in process_rows():
        if st not in ("SORT", "SORT2", "SORT0"):
            continue
        if _is_chain2(product):
            fam["c2_round2" if st == "SORT2" else "c2_round1"] += q
        else:
            fam["c1_round0" if st == "SORT0" else "c1_round1"] += q
    if not fam:
        return None

    delivered = sum(l["v"] for l in data["2"]["links"] if l["k"] == "deliver")
    stage = sum(l["v"] for l in data["2"]["links"] if l["s"] == "S:STG")
    same_day = delivered - stage
    collected = sum(l["v"] for l in data["1"]["links"] if l["k"] == "arrive")
    c2 = fam["c2_round1"] + fam["c2_round2"]

    out = {"round1": round(fam["c2_round1"]), "round2": round(fam["c2_round2"]),
           "sorts": round(c2), "delivered": round(delivered), "stage": round(stage),
           "same_day": round(same_day),
           "model_same_day": round(c2 / same_day, 3) if same_day else 0,
           "model_all": round(c2 / delivered, 3) if delivered else 0,
           "c1_sorts": round(fam["c1_round0"] + fam["c1_round1"]),
           "c1_per": round((fam["c1_round0"] + fam["c1_round1"]) / collected, 3) if collected else 0,
           "collected": round(collected), "obs": None, "lanes": [],
           "floor": None, "basis": "", "r2_lanes": None, "cohort": "", "sites": None,
           "model_two_sorted": None, "forwarded": 0,
           "outside": 0, "outside_at": [], "sites_modelled": len(SITE_FACILITY), "band": None,
           "relay": None, "solved_two": None, "solved_two_share": None}
    # ══ THE COMPARISON, ON THE FACILITY-PATH BASIS (Change 44) ═══════════════════════
    # This used to count SORTS — entries of the machine-sort chain that land at a modelled site —
    # against a model that also thought in sorts. Both sides moved: the exporter measures the
    # BUILDINGS a parcel was in (`path_sites`, capped at PATH_DEPTH) and chain 2 is built from
    # that, so the honest comparison is buildings against buildings. It is also the same
    # question sankey-facility-path.html puts to the scans, which is what makes the two pages
    # readable side by side.
    #
    # The capped count is the one to use: the model has two sort rounds and cannot represent a
    # third building, so comparing against the uncapped path would score it against a journey it
    # has no way to draw.
    try:                                   # the measurement, if the scan reduction is cached
        import csv as _csv
        from export_chain2_factors import (CACHE, FOBS, PATH_BASIS, PATH_DEPTH, PATH_TOUCH_BAR,
                                          cohort, load_paths, read_dials)
        _d = read_dials()
        # SORT_BAND is the NOTEBOOK's dial, not one the exporter applies, so it comes through
        # factors.py rather than read_dials(). It was typed into the page's prose as "±5%",
        # which stops being true the moment anyone widens the band.
        from factors import F as _F
        out.update(floor=_d["fold"], basis=f"{PATH_BASIS} / {PATH_TOUCH_BAR} bar",
                   cohort=_d["cohort"].replace("_", " "), depth=PATH_DEPTH,
                   band=_F.dial("SORT_BAND"))
        _legs = list(_csv.DictReader(open(FOBS / "obs_legs.csv")))
        out["r2_lanes"] = sum(1 for r in _legs if r["dest"] != "ONCE")
        out["sites"] = len({r["entry"] for r in _legs})
        if not os.path.exists(CACHE):
            raise FileNotFoundError(CACHE)
        q = cohort(load_paths().set_index("Consignment_ID"))
        n = q.path_sites.apply(len)                     # buildings the model could carry
        a, sd = q.articles, q.fam != "METRO_DEPOT"      # same-day, as derive_stages defines it
        _sa = a[sd]
        out["obs"] = {"all": round(float((n * a).sum() / a.sum()), 3),
                      "same_day": round(float((n[sd] * _sa).sum() / _sa.sum()), 3),
                      "one": round(float(_sa[n[sd] == 1].sum() / _sa.sum()), 4),
                      "two": round(float(_sa[n[sd] >= 2].sum() / _sa.sum()), 4),
                      "two_ea": int(_sa[n[sd] >= 2].sum()),      # before any export filter
                      "same_day_ea": int(_sa.sum()),
                      # THE OBSERVED EQUIVALENT OF THE MODEL'S RELAY. A parcel the scans put in a
                      # second building while recording fewer than two machine sorts was carried
                      # there and not opened — which is what the model's relay is. `obs_legs`
                      # cannot answer this: the path basis merges a cross-dock leg and a round-2
                      # leg into ONE row on purpose, so the export has no cross-dock column and
                      # this has to come off the reduction itself.
                      "xdock_ea": int(_sa[(n[sd] >= 2) & (q.nrounds[sd] < 2)].sum()),
                      "cohort": int(a.sum())}
        # ── lane by lane: what the solver ran against what the scans measured ────────
        _m = defaultdict(float)
        for l in data["2"]["links"]:
            if l["k"] in ("round2", "relay"):
                _m[(l["s"][2:], l["t"][2:])] += l["v"]     # "A:HUB_x" -> "HUB_x"
        # A LANE THE MODEL HAS NO NODE FOR IS NOT A LANE IT DECLINED TO RUN. chain 2 carries the
        # buildings in SITE_FACILITY; the scans measure legs out of the sort sites it folds away
        # and out of the non-sorting depots too, and those can only ever read zero here. They are
        # reported apart rather than listed as misses, because "the solver ran none" and "the
        # model cannot express it" are different findings and only the first is about the solve.
        _o, _outside, _outside_at = defaultdict(float), 0.0, set()
        for r in _legs:
            if r["dest"] == "ONCE":
                continue
            if r["entry"] in SITE_FACILITY and r["dest"] in SITE_FACILITY:
                _o[(SITE_FACILITY[r["entry"]], SITE_FACILITY[r["dest"]])] += float(r["articles"])
            else:
                _outside += float(r["articles"])
                # name the buildings rather than typing them into the print: which sites fall
                # outside changes with SITE_FACILITY, and a stale parenthetical in a findings
                # line is the kind of wrong that gets quoted
                _outside_at |= {s for s in (r["entry"], r["dest"]) if s not in SITE_FACILITY}
        out["outside"] = round(_outside)
        out["outside_at"] = sorted(nice(CODE_SITE.get(s, s)) for s in _outside_at)
        out["lanes"] = sorted(
            ({"a": nice(x), "b": nice(y),
              "m": round(_m.get((x, y), 0.0)), "o": round(_o.get((x, y), 0.0))}
             for x, y in set(_m) | set(_o)), key=lambda r: -(r["m"] + r["o"]))
        # A SECOND BUILDING IS NOT A SECOND SORT, and the two are different numbers: 38,121
        # round-2 sorts against 42,677 second buildings on the 2 Sep run. This share is
        # BUILDINGS — the freight that moved from one of our buildings to another — because that
        # is what the column is, what the scans measure on this basis, and what the lane table
        # adds up to. `model_two_sorted` beside it is the sort count, for the same denominator.
        out["model_two"] = round(sum(_m.values()) / same_day, 4) if same_day else 0
        out["model_two_sorted"] = round(fam["c2_round2"] / same_day, 4) if same_day else 0
        out["sites_modelled"] = len(SITE_FACILITY)
        # and the per-parcel figures with it, for the same reason: the panel is headed
        # "building touches", so it must count buildings. Every same-day parcel has a first
        # building; the second is the lane volume above. The stage has none — it never left
        # the depot — which is why `model_all` divides by everything delivered.
        _touch = same_day + sum(_m.values())
        out["sorts"] = round(_touch)
        # the freight that reaches a second building WITHOUT a second sort — the solver
        # transshipping finished Despatch2 through a depot. A real result, not a rounding
        # artefact, and it is the whole difference between the two shares.
        out["forwarded"] = round(sum(_m.values()) - fam["c2_round2"])
        out["model_same_day"] = round(_touch / same_day, 3) if same_day else 0
        out["model_all"] = round(_touch / delivered, 3) if delivered else 0
        # the measured second-building share on the SAME buildings the model has, so the
        # headline gap is like for like; `obs["two"]` above is the whole measurement
        _leg_ea = sum(r["o"] for r in out["lanes"])
        _same_ea = sum(float(r["articles"]) for r in _legs
                       if r["entry"] in SITE_FACILITY)
        out["obs"]["two_modelled"] = round(_leg_ea / _same_ea, 4) if _same_ea else 0
        out["obs"]["two_modelled_ea"] = round(_leg_ea)      # the like-for-like numerator, in EA
        # WHAT THE SOLVER ACTUALLY DID. `solved_two` is built from the measured relay and
        # `model_two` from the drawn ribbons, so the two USED to differ — the pass-through block
        # sized the relay as a residual and missed most of it. Since the rewire (2026-09-02) the
        # ribbons ARE the measurement, so these agree by construction, and the assert is what
        # keeps them agreeing: a drawn figure that drifts from the solved one means the diagram
        # has stopped showing the solve, which is exactly the failure that hid 3,909 EA.
        _rl = relay_measured()
        out["relay"] = _rl
        out["solved_two"] = round(fam["c2_round2"] + _rl["second"])
        out["solved_two_share"] = round(out["solved_two"] / same_day, 4) if same_day else 0
        assert abs(sum(_m.values()) - out["solved_two"]) < 2, (
            f"the page draws {sum(_m.values()):,.0f} EA of second buildings but the solve has "
            f"{out['solved_two']:,} — the diagram is no longer showing what the model did")
    except Exception as e:
        print(f"  (measured comparison skipped — {type(e).__name__}: {e})")
    return out


STAGE_ORDER = ["UNLOAD0", "SORT0", "UNLOAD", "SORT", "LOAD", "UNLOAD2", "SORT2", "LOAD2", "DRIVER"]
STAGE_LABEL = {"UNLOAD0": "round-0 bag unload", "SORT0": "round-0 sort",
               "UNLOAD": "round-1 unload", "SORT": "round-1 sort", "LOAD": "round-1 load",
               "UNLOAD2": "round-2 unload", "SORT2": "round-2 sort", "LOAD2": "round-2 load",
               "DRIVER": "driver wave"}


def inside():
    """What happened INSIDE each building: machine utilisation + the production stages.

    Two things this exposes that the flow file cannot:
      * which machines are pinned at 100% (the binding constraints), and
      * a driver-wave utilisation that is an ARTEFACT — capacity is loading-rate x 6 h while
        the driver count is set by van capacity (180), so ~37% means "vans full, loading hours
        spare", not spare delivery capacity.
    """
    if not os.path.exists(WCS):
        return None
    wc = defaultdict(list)
    for r in csv.DictReader(open(WCS, encoding="utf-8-sig")):
        q, cap = float(r["throughputquantity"] or 0), float(r["throughputcapacity"] or 0)
        name = re.sub(r"^WC_", "", r["workcentername"])
        fac = r["facilityname"]
        mach = name[:-(len(fac.split("_", 1)[1]) + 1)] if name.endswith(fac.split("_", 1)[1]) else name
        wc[fac].append({"m": mach, "q": round(q), "cap": round(cap),
                        "u": round(q / cap, 4) if cap else 0,
                        "driver": "DRIVER" in mach})
    prod = defaultdict(float)
    for f, st, _p, q in process_rows():
        prod[(f, st)] += q
    facs = sorted(wc, key=lambda f: (0 if f.startswith("HUB_") else 1, f))
    out = []
    for f in facs:
        stages = [{"s": st, "l": STAGE_LABEL[st], "q": round(prod[(f, st)])}
                  for st in STAGE_ORDER if prod.get((f, st))]
        out.append({"f": nice(f), "kind": "hub" if f.startswith("HUB_") else "pdc",
                    "wcs": sorted(wc[f], key=lambda x: -x["u"]), "stages": stages})
    return out


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Melbourne — parcel path, source to sink</title>
<style>
:root{
  --bg:#f6f6f4; --card:#fff; --ink:#0b0b0b; --ink2:#52514e; --ink3:#8a8880; --line:#e4e3de;
  --pp:#2a78d6; --ep:#eb6834;
  /* Three hues, reused across both chains — validated for colour-vision separation
     (worst adjacent pair dE 18.2 normal / 10.4 deutan) and against this surface.
     Change 46 needed a FOURTH, and took it as a VALUE step on the Victorian ochre rather
     than a new hue: the two Victorian bands are halves of what used to be one family, so
     sharing a hue says they are related, and a lightness difference survives every kind of
     colour vision where a fourth hue would not. */
  --INTERSTATE:#2a6fd6; --EXPORT:#2a6fd6;
  --MET:#a8641a;
  --REG:#5e3407;                           /* the same hue, much darker */
  --STG:#1f8a70;
  /* Chain 1's outcomes ARE chain 2's source families, seen from the collection side, so each
     takes its counterpart's exact colour: the metro handover is MET's ochre, the kept volume is
     STG's teal, regional is REG's dark ochre. That leaves PICKUP — which is not an outcome at
     all but the freight in transit towards one — and it goes neutral, so the four terminations
     carry the only colour in the diagram. */
  --METROTERM:#a8641a;  --LOCAL:#1f8a70;   --REGTERM:#5e3407;
  /* PDO terminate has no chain-2 counterpart to borrow from — it is carved out of the
     export — so it takes MET's hue lightened, saying metro-bound without claiming to be
     the measured handover. */
  --PDOTERM:#d5a05a;
  --PICKUP:#7b8a92;
  --none:#8a8880;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:22px 20px 60px}
h1{margin:0 0 3px;font-size:20px;font-weight:680;letter-spacing:-.015em}
.sub{margin:0 0 16px;color:var(--ink3);font-size:12.5px;max-width:88ch;line-height:1.5}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
.seg button{border:0;background:#fff;padding:6px 12px;font:inherit;font-size:12px;color:var(--ink2);cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-pressed=true]{background:#eef4fd;color:#1a5fb4;font-weight:620}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px 10px;
  box-shadow:0 1px 2px rgba(0,0,0,.04);margin-bottom:18px}
.card h2{margin:0 0 2px;font-size:15px;font-weight:660;letter-spacing:-.01em}
.card .cn{margin:0 0 10px;color:var(--ink3);font-size:12px;line-height:1.45}
svg{display:block;width:100%;height:auto;overflow:visible}
.colhdr{font-size:10px;font-weight:660;text-transform:uppercase;letter-spacing:.06em;fill:var(--ink3)}
.coltot{font-size:10px;font-weight:600;letter-spacing:0;fill:var(--ink2);font-variant-numeric:tabular-nums}
.colmoved{font-size:9.5px;font-weight:600;letter-spacing:.02em;fill:#a8641a;
  font-variant-numeric:tabular-nums}
.nlab{font-size:11.5px;font-weight:560;fill:var(--ink)}
.nsub{font-size:9.5px;fill:var(--ink3)}
.nval{font-size:10px;fill:var(--ink3);font-variant-numeric:tabular-nums}
.nrect{fill:#3f3e3a;cursor:pointer}
.nrect.onward{fill:#a9a69d}          /* not a building — a journey that ended */
.nrect:hover{fill:#1b1a18}
.nrect.sel{fill:#1b1a18;stroke:#0b0b0b;stroke-width:2}
.lk{mix-blend-mode:multiply;transition:opacity .12s}
/* ── THE CELL SHEET ──────────────────────────────────────────────────────────────────
   Same idea, and deliberately the same shape, as sankey-facility-path.html: hovering a
   ribbon answers one question about one lane, which is a poor way to read a building on a
   page with 377 of them. Clicking a cell opens everything into it and everything out of it
   as two tables, biggest first, and every row is itself a link so a route can be walked
   end to end without going back to the picture. */
#pop[hidden]{display:none}
#pop{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;
  padding:24px;background:rgba(20,19,17,.46)}
.popcard{background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:0 10px 40px rgba(0,0,0,.22);width:min(1040px,100%);max-height:86vh;overflow:auto}
.pophead{position:sticky;top:0;z-index:1;background:var(--card);border-bottom:1px solid var(--line);
  padding:15px 18px;display:flex;gap:18px;justify-content:space-between;align-items:flex-start}
.pophead h3{margin:0 0 3px;font-size:17px;font-weight:660;letter-spacing:-.01em}
.pophead .psub{color:var(--ink2);font-size:12.5px;max-width:70ch}
.pophead .pnum{color:var(--ink3);font-size:12px;margin-top:5px;font-variant-numeric:tabular-nums}
.popclose{border:1px solid var(--line);background:var(--bg);color:var(--ink2);border-radius:7px;
  font:inherit;font-size:12.5px;padding:5px 11px;cursor:pointer;flex:none}
.popclose:hover{color:var(--ink);border-color:var(--ink3)}
.popbody{padding:14px 18px 18px;display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:900px){.popbody{grid-template-columns:1fr}}
.popbody h4{margin:0 0 7px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink3);font-weight:660}
.popbody p.empty{margin:0;color:var(--ink3);font-size:12.5px}
table.cell{border-collapse:collapse;width:100%;font-size:12.5px}
table.cell th{text-align:right;padding:4px 9px;border-bottom:1px solid var(--line);
  color:var(--ink3);font-weight:600;font-size:10px;text-transform:uppercase;letter-spacing:.06em}
table.cell th:first-child{text-align:left}
table.cell td{text-align:right;padding:4px 9px;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
table.cell td:first-child{text-align:left}
table.cell tr.go{cursor:pointer}
table.cell tr.go:hover td{background:var(--bg)}
table.cell tr.go:hover td:first-child{font-weight:640}
table.cell tr.tot td{font-weight:700;border-bottom:none}
table.cell tr.tot td:first-child{color:var(--ink2)}
.mixbar{display:flex;height:8px;width:92px;border-radius:4px;overflow:hidden;background:#eeede9;
  margin-left:auto}
.mixbar i{display:block;height:100%}
.popnote{padding:0 18px 16px;color:var(--ink3);font-size:11.5px;line-height:1.5;max-width:92ch}
.kindtag{font-size:9.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink3);
  background:var(--bg);border:1px solid var(--line);border-radius:3px;padding:0 4px;margin-left:6px}
.dim .lk{opacity:.09}
.dim .lk.on{opacity:.62}
.legend{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:2px 0 4px;font-size:11.5px;color:var(--ink2)}
.legend i{width:11px;height:11px;border-radius:3px;display:inline-block;vertical-align:-1px;margin-right:5px}
#tip{position:fixed;pointer-events:none;z-index:50;background:#fff;border:1px solid var(--line);
  border-radius:8px;padding:8px 10px;box-shadow:0 2px 10px rgba(0,0,0,.14);font-size:12px;display:none;max-width:290px}
#tip b{font-weight:650}
#tip .r{display:flex;justify-content:space-between;gap:16px;color:var(--ink2)}
#tip .r span:last-child{font-variant-numeric:tabular-nums;font-weight:560;color:var(--ink)}
.note{font-size:11.5px;color:var(--ink3);line-height:1.5;border-top:1px solid var(--line);
  padding-top:9px;margin-top:6px}
.fac{border-top:1px solid var(--line);padding:9px 0 4px}
.fac:first-child{border-top:0}
.fac h3{margin:0 0 6px;font-size:12.5px;font-weight:640}
.fac h3 .k{font-size:9.5px;font-weight:660;text-transform:uppercase;letter-spacing:.05em;
  color:#fff;background:var(--ink3);border-radius:3px;padding:1px 5px;margin-left:6px;vertical-align:1px}
.wcrow{display:grid;grid-template-columns:150px 1fr 84px 46px;gap:9px;align-items:center;
  padding:1.5px 0;font-size:11.5px}
.wcrow .bar{height:8px;border-radius:4px;background:#eeede9;overflow:hidden}
.wcrow .bar i{display:block;height:100%;border-radius:4px}
.wcrow .num{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink2)}
.wcrow .pct{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.wcrow.full .pct{color:#c0392b}
.wcrow.idle{opacity:.5}
.stg{display:flex;gap:5px;flex-wrap:wrap;margin:5px 0 2px}
.stg span{font-size:10.5px;background:#f2f1ec;border-radius:4px;padding:2px 6px;color:var(--ink2)}
.stg span b{font-variant-numeric:tabular-nums}
code{background:#f2f1ec;border-radius:3px;padding:1px 4px;font-size:11px}
.stgrow{display:grid;grid-template-columns:160px 1fr 96px 54px;gap:10px;align-items:center;
  padding:2.5px 0;font-size:11.5px;border-top:1px solid var(--line)}
.stgrow:first-child{border-top:0}
.stgrow .bar{height:9px;border-radius:5px;background:#eeede9;overflow:hidden}
.stgrow .bar i{display:block;height:100%;border-radius:5px;background:var(--STG)}
.stgrow .num,.stgrow .pct{text-align:right;font-variant-numeric:tabular-nums}
.stgrow .pct{font-weight:620}
.stghdr{display:grid;grid-template-columns:160px 1fr 96px 54px;gap:10px;font-size:9.5px;
  text-transform:uppercase;letter-spacing:.06em;color:var(--ink3);font-weight:660;padding-bottom:4px}
.stghdr span:nth-child(3),.stghdr span:nth-child(4){text-align:right}
</style></head><body>
<div class="wrap">
  <h1>Parcel path — source to sink</h1>
  <p class="sub">Solved run <b>__SCEN__</b>. Every stage from the source that creates volume to the
     sink that consumes it; ribbon width is EA/day. The two chains are <b>separate daily
     activities</b> and share no volume — collection on one side, delivery on the other.</p>
  <div class="bar">
    <div class="seg" id="mode">
      <button data-m="c" aria-pressed="true">Colour by class</button>
      <button data-m="h" aria-pressed="false">by origin</button>
    </div>
    <div class="seg" id="focus">
      <button data-f="" aria-pressed="true">All flows</button>
      <button data-f="2nd" aria-pressed="false">Moved to a 2nd building</button>
      <button data-f="round2" aria-pressed="false">&hellip; and sorted there</button>
      <button data-f="relay" aria-pressed="false">&hellip; cross-docked, not sorted</button>
      <button data-f="onward" aria-pressed="false">Finished in one building</button>
      <button data-f="stage" aria-pressed="false">Kept at depot</button>
    </div>
    <div class="legend" id="legend"></div>
  </div>
  <p class="sub" style="margin-top:-4px">Hover a ribbon for one lane;
     <b>click any node</b> for everything in and out of it.</p>
  <div id="charts"></div>
</div>
<div id="tip"></div>
<div id="pop" hidden role="dialog" aria-modal="true"
     aria-label="Everything in and out of this node"></div>
<script>
const DATA = __DATA__;
const CV = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COL_C = {PP:CV("--pp"), EP:CV("--ep")};
const COL_H = {INTERSTATE:CV("--INTERSTATE"), MET:CV("--MET"), REG:CV("--REG"),
               STG:CV("--STG"), PICKUP:CV("--PICKUP"), EXPORT:CV("--EXPORT"),
               LOCAL:CV("--LOCAL"), METROTERM:CV("--METROTERM"), REGTERM:CV("--REGTERM"),
               PDOTERM:CV("--PDOTERM"), "":CV("--none")};
const HNAME = {INTERSTATE:"Interstate", MET:"Vic Metro to Metro",
               REG:"Regional Vic to Metro Vic", STG:"Kept at depot",
               PICKUP:"Collected today", EXPORT:"Leaves Melbourne",
               LOCAL:"Kept at depot", METROTERM:"Vic Metro to Metro",
               REGTERM:"Regional pickup, ends at the hub",
               PDOTERM:"PDO terminate, ends at the hub", "":"unclassified"};
let mode = "c", focus = "", SEL = null;
// A SECOND BUILDING IS TWO KINDS. `round2` was sorted at the far end, `relay` was not opened at
// all — the same move, a different amount of work — so the button that asks "did it move" has to
// accept both, and the two narrower buttons isolate one each.
const FOCUS_KINDS = {"2nd": ["round2", "relay", "xdock"]};
const inFocus = l => (FOCUS_KINDS[focus] || [focus]).includes(l.k);
const fmt = n => Math.round(n).toLocaleString();
// module scope on purpose: renderStage() had its own copy, and a second local would be the
// same line waiting to drift apart. "0%" beside "10 EA" reads as an error rather than as a
// small number, hence the <1% case.
const pc = v => v===0 ? "0%" : v < 0.005 ? "&lt;1%" : (v*100).toFixed(0)+"%";
const colOf = l => mode==="c" ? COL_C[l.c] : (COL_H[l.h]!==undefined?COL_H[l.h]:COL_H[""]);
const keyOf = l => mode==="c" ? l.c : l.h;

const W = 1560, NW = 13, PAD = 15, ROW_GAP = 26;

function layout(ch){
  const cols = ch.cols.length;
  const byCol = {};
  ch.nodes.forEach(n=>(byCol[n.col] ||= []).push(n));
  // node value = max(in,out) so a node is never thinner than the flow through it
  const inn={}, out={};
  ch.links.forEach(l=>{ out[l.s]=(out[l.s]||0)+l.v; inn[l.t]=(inn[l.t]||0)+l.v; });
  ch.nodes.forEach(n=> n.v = Math.max(inn[n.id]||0, out[n.id]||0));
  const maxColSum = Math.max(...Object.values(byCol).map(a=>a.reduce((s,n)=>s+n.v,0)));
  const maxCount  = Math.max(...Object.values(byCol).map(a=>a.length));
  const H = Math.max(230, maxColSum*0.00092 + (maxCount-1)*PAD + 40);
  const scale = (H - (maxCount-1)*PAD - 30) / maxColSum;
  // Columns are NOT evenly spaced. Label direction decides where white space is needed:
  // "Delivering depot" reads leftward, so the gap before it has to hold two label sets and
  // gets a third of the width; "Delivered" reads into the right margin, so the gap before
  // IT carries ribbons only and can be tight. Even spacing put "Melbourne Parcel" on top of
  // "Oakleigh South".
  const FR = ch.frac || Array.from({length:cols}, (_,i)=> i/(cols-1));
  // THE RIGHT MARGIN IS MEASURED, NOT TYPED (2026-08-31). It was a flat 320 with the last column
  // at W-188, leaving 169px for a label that reads INTO the margin — and chain 2's sink names run
  // to 31 characters ("MPF · Regional Vic to Metro Vic"), which is ~185px before the value is
  // appended. They ran off the viewBox and were clipped. A thin node puts label and value on one
  // line, so the widest case is both together; 6.15px/char is the measured advance of the 11.5px
  // label face on this surface, and 96 is the widest the margin is allowed to get before it
  // starts squeezing the ribbons instead.
  const last = ch.nodes.filter(n => n.col === cols-1);
  const wide = Math.max(0, ...last.map(n => n.label.length + String(Math.round(n.v)).length + 3));
  // +30, not +18: the label starts NW+6 = 19px past the node's left edge, so the budget has
  // to clear that before the text begins or the last character still lands on the boundary
  const RPAD = Math.min(320, Math.max(169, Math.round(6.15*wide) + 30));
  const colX = i => 132 + FR[i]*(W - 132 - RPAD);
  ch.colTot = Object.keys(byCol).sort().map(c => byCol[c].reduce((s,n)=>s+n.v,0));
  for(const c in byCol){
    const arr = byCol[c].sort((a,b)=>b.v-a.v);
    const tot = arr.reduce((s,n)=>s+n.v,0)*scale + (arr.length-1)*PAD;
    let y = 26 + (H-26-tot)/2;
    for(const n of arr){ n.x=colX(+c); n.h=Math.max(2,n.v*scale); n.y=y; y+=n.h+PAD; }
  }
  // stack ribbons at each end, biggest first
  const so={}, ti={};
  ch.links.slice().sort((a,b)=>b.v-a.v).forEach(l=>{
    const s=ch.nodes.find(n=>n.id===l.s), t=ch.nodes.find(n=>n.id===l.t);
    l.sy = s.y + (so[l.s]||0); so[l.s]=(so[l.s]||0)+l.v*scale;
    l.ty = t.y + (ti[l.t]||0); ti[l.t]=(ti[l.t]||0)+l.v*scale;
    l.w  = Math.max(0.8, l.v*scale); l.sx=s.x+NW; l.tx=t.x;
    l.sn=s.label; l.tn=t.label;
  });
  return {H, colX};
}

function ribbon(l){
  const mx=(l.sx+l.tx)/2, y0=l.sy+l.w/2, y1=l.ty+l.w/2;
  return `M${l.sx},${y0} C${mx},${y0} ${mx},${y1} ${l.tx},${y1}`;
}

function render(){
  const host = document.getElementById("charts");
  host.innerHTML = "";
  for(const k of ["1","2"]){
    const ch = DATA[k];
    const {H, colX} = layout(ch);
    const card = document.createElement("div");
    card.className = "card";
    const parts = [];
    parts.push(`<svg viewBox="0 0 ${W} ${H+16}" role="img">`);
    ch.cols.forEach((c,i)=>{
      const anchor = i===0?"start":i===ch.cols.length-1?"end":"middle";
      const x = i===0?colX(0)-NW/2-108 : i===ch.cols.length-1?colX(i)+NW : colX(i)+NW/2;
      // The total under each header is the point of a Sankey: every column carries the same
      // volume, and where it does not, a stage has lost freight.
      parts.push(`<text class="colhdr" x="${x}" y="12" text-anchor="${anchor}">${c}`+
        `<tspan class="coltot" dx="7">${fmt(ch.colTot[i]||0)}</tspan></text>`);
      // and, where it differs from the total, how much of it actually CAME FROM ELSEWHERE
      if((ch.moved||[])[i])
        parts.push(`<text class="colmoved" x="${x}" y="23" text-anchor="${anchor}">`+
          `${ch.moved[i]}</text>`);
    });
    for(const l of ch.links)
      // Focus is baked into the opacity rather than a CSS class, so that hovering a ribbon and
      // then leaving it cannot silently clear the filter the reader chose.
      parts.push(`<path class="lk" d="${ribbon(l)}" stroke="${colOf(l)}" stroke-width="${l.w}"
        fill="none" stroke-linecap="butt" opacity="${focus ? (inFocus(l) ? .85 : .05)
          : ((l.k==="flat"||l.k==="onward") ? .26 : .64)}"
        data-k="${keyOf(l)}" data-s="${l.s}" data-t="${l.t}" data-kind="${l.k}"
        data-tip="${encodeURIComponent(JSON.stringify({a:l.sn,b:l.tn,c:l.c,h:l.h,v:l.v}))}"></path>`);
    for(const n of ch.nodes){
      // Label direction, so no two columns compete for the same strip of white space: every
      // column reads rightward EXCEPT the second-to-last, which reads leftward. That leaves the
      // final column the whole right margin to itself — it has the longest labels and the
      // thinnest nodes, and it was unreadable when both columns pushed right.
      const flip = n.col === ch.cols.length-2 && ch.cols.length > 4;
      const tx = flip ? n.x-6 : n.x+NW+6, an = flip?"end":"start";
      parts.push(`<rect class="nrect${n.id===SEL?" sel":""}${n.id.startsWith("ONWARD")?" onward":""}" x="${n.x}" y="${n.y}"
        width="${NW}" height="${n.h}" rx="2.5" tabindex="0" role="button"
        aria-label="${n.label} — open flows" data-node="${n.id}"></rect>`);
      // Thin nodes put label and value on ONE line — stacking them made the small PDCs
      // (Mulgrave / Abbotsford / Mount Waverley) collide with the row below.
      const cy = n.y + n.h/2;
      if(n.h < 26){
        parts.push(`<text class="nlab" x="${tx}" y="${cy+3.5}" text-anchor="${an}">${n.label}`+
          `<tspan class="nval" dx="6">${fmt(n.v)}</tspan></text>`);
      }else{
        parts.push(`<text class="nlab" x="${tx}" y="${cy-2}" text-anchor="${an}">${n.label}</text>`);
        parts.push(`<text class="nval" x="${tx}" y="${cy+10}" text-anchor="${an}">${fmt(n.v)}</text>`);
      }
    }
    parts.push(`</svg>`);
    card.innerHTML = `<h2>${ch.title}</h2><p class="cn">${ch.note}</p>${parts.join("")}`;
    host.appendChild(card);
  }
  wireHover();
  drawLegend();
}

function drawLegend(){
  const keys = new Set();
  for(const k of ["1","2"]) DATA[k].links.forEach(l=>keys.add(keyOf(l)));
  const order = mode==="c" ? ["PP","EP"]
                          : ["INTERSTATE","MET","REG","STG","PICKUP","EXPORT","PDOTERM",
                             "LOCAL","METROTERM","REGTERM",""];
  document.getElementById("legend").innerHTML = order.filter(k=>keys.has(k)).map(k=>{
    const col = mode==="c" ? COL_C[k] : COL_H[k];
    const nm  = mode==="c" ? (k==="PP"?"PP · normal":"EP · express") : (HNAME[k]||k);
    return `<span><i style="background:${col}"></i>${nm}</span>`;
  }).join("");
}

const tip = document.getElementById("tip");
function wireHover(){
  document.querySelectorAll("path.lk").forEach(p=>{
    p.addEventListener("mousemove", e=>{
      const d = JSON.parse(decodeURIComponent(p.dataset.tip));
      tip.innerHTML = `<b>${d.a} → ${d.b}</b>`+
        `<div class="r"><span>Class</span><span>${d.c==="PP"?"PP · normal":"EP · express"}</span></div>`+
        (d.h?`<div class="r"><span>Origin</span><span>${HNAME[d.h]||d.h}</span></div>`:"")+
        `<div class="r"><span>Volume</span><span>${fmt(d.v)} EA/day</span></div>`;
      tip.style.display="block";
      tip.style.left = Math.min(e.clientX+14, innerWidth-300)+"px";
      tip.style.top  = (e.clientY+14)+"px";
      p.closest("svg").classList.add("dim"); p.classList.add("on");
    });
    p.addEventListener("mouseleave", ()=>{
      tip.style.display="none";
      p.closest("svg").classList.remove("dim"); p.classList.remove("on");
    });
  });
  // hover a node: light up everything touching it
  document.querySelectorAll("rect[data-node]").forEach(r=>{
    r.addEventListener("mouseenter", ()=>{
      const svg = r.closest("svg"); svg.classList.add("dim");
      svg.querySelectorAll("path.lk").forEach(p=>{
        if(p.dataset.s===r.dataset.node || p.dataset.t===r.dataset.node) p.classList.add("on");
      });
    });
    r.addEventListener("mouseleave", ()=>{
      const svg = r.closest("svg"); svg.classList.remove("dim");
      svg.querySelectorAll("path.lk").forEach(p=>p.classList.remove("on"));
    });
    r.addEventListener("click", ()=> openCell(SEL===r.dataset.node ? null : r.dataset.node));
    r.addEventListener("keydown", e=>{
      if(e.key==="Enter" || e.key===" "){ e.preventDefault();
        openCell(SEL===r.dataset.node ? null : r.dataset.node); }
    });
  });
  // A ribbon is a way INTO a node too: clicking one opens the node it lands in, which is what
  // you want after following a lane across with your eye.
  document.querySelectorAll("path.lk").forEach(p=>
    p.addEventListener("click", ()=>{ tip.style.display="none"; openCell(p.dataset.t); }));
}

// ── THE CELL SHEET ───────────────────────────────────────────────────────────────────
// The numbers are AS DRAWN — the same ribbons, the residual ones included — because a sheet
// that disagreed with the picture beside it would be worse than no sheet.
function chainOf(id){
  for(const k of ["1","2"]) if(DATA[k].nodes.some(n=>n.id===id)) return DATA[k];
  return null;
}
function mixbar(map, tot){
  const ks = [...map.keys()].sort();
  return `<span class="mixbar" title="${ks.map(k=>mixName(k)+": "+fmt(map.get(k))).join(", ")}">`
    + ks.map(k=>`<i style="width:${100*map.get(k)/Math.max(tot,1e-9)}%;background:${mixCol(k)}"></i>`)
        .join("") + `</span>`;
}
const mixCol  = k => mode==="c" ? COL_C[k] : (COL_H[k]!==undefined?COL_H[k]:COL_H[""]);
const mixName = k => mode==="c" ? (k==="PP"?"PP · normal":"EP · express") : (HNAME[k]||k||"—");

function openCell(id){
  SEL = id || null;
  render();          // repaints the selected node; the sheet is rebuilt underneath
  drawPop();
  const btn = SEL && document.querySelector(".popclose");
  if(btn) btn.focus();
}

function drawPop(){
  const box = document.getElementById("pop");
  const ch = SEL && chainOf(SEL);
  if(!ch){ box.hidden = true; box.innerHTML = ""; return; }
  const meta = ch.nodes.find(n=>n.id===SEL);
  const label = id => (ch.nodes.find(n=>n.id===id)||{label:id}).label;
  const sub   = id => (ch.nodes.find(n=>n.id===id)||{sub:""}).sub;
  const col   = id => ch.cols[(ch.nodes.find(n=>n.id===id)||{col:0}).col];
  const side = (test, other) => {
    const g = new Map();
    ch.links.forEach(l=>{
      if(!test(l)) return;
      const o = other(l), row = g.get(o) || {id:o, v:0, mix:new Map(), kinds:new Set()};
      row.v += l.v;
      row.mix.set(keyOf(l), (row.mix.get(keyOf(l))||0) + l.v);
      row.kinds.add(l.k);
      g.set(o, row);
    });
    return [...g.values()].sort((a,b)=>b.v-a.v);
  };
  const ins  = side(l=>l.t===SEL, l=>l.s);
  const outs = side(l=>l.s===SEL, l=>l.t);
  const sum  = rows => rows.reduce((a,r)=>a+r.v,0);
  const tot  = Math.max(sum(ins), sum(outs));
  const colTot = ch.colTot[meta.col] || 0;
  const KIND = {arrive:"arrives", round2:"2nd building", despatch:"despatch", deliver:"van",
                stage:"kept", flat:"same building", xdock:"cross-dock"};
  const table = (rows, head, empty) => {
    const total = sum(rows);
    if(!rows.length) return `<h4>${head}</h4><p class="empty">${empty}</p>`;
    return `<h4>${head}</h4><table class="cell"><thead><tr><th>Node</th>
      <th>EA/day</th><th>Share</th><th>Mix</th></tr></thead><tbody>`
      // The three stage nodes deliberately share one label ("Kept at depot" across all three
      // columns is the point of the flat band), so a row would otherwise read "Kept at depot ->
      // Kept at depot" and say nothing. Name the column only when the labels collide.
      + rows.map(r=>`<tr class="go" data-go="${r.id}">
          <td>${label(r.id)}${label(r.id)===meta.label
            ? ` <em style="color:var(--ink3);font-style:normal">· ${col(r.id)}</em>` : ""}
          <span class="kindtag">${
            [...r.kinds].map(k=>KIND[k]||k).join(" · ")}</span></td>
          <td>${fmt(r.v)}</td><td>${(100*r.v/total).toFixed(1)}%</td>
          <td>${mixbar(r.mix, r.v)}</td></tr>`).join("")
      + `<tr class="tot"><td>Total</td><td>${fmt(total)}</td><td></td><td></td></tr>`
      + `</tbody></table>`;
  };
  box.innerHTML = `<div class="popcard">
    <div class="pophead"><div>
      <h3>${meta.label}</h3>
      <div class="psub">${meta.sub}</div>
      <div class="pnum">${meta.sub===ch.cols[meta.col].toLowerCase()||meta.sub===ch.cols[meta.col] ? "" : ch.cols[meta.col]+" &middot; "}${fmt(tot)} EA/day &middot; ${
        colTot ? (100*tot/colTot).toFixed(1)+"% of this column" : "—"} &middot; ${
        ins.length} in, ${outs.length} out</div>
    </div><button class="popclose">Close &times;</button></div>
    <div class="popbody">
      <div>${table(ins, "Arriving from", "Nothing — this is where the column starts.")}</div>
      <div>${table(outs, "Leaving for", "Nothing — the journey ends here.")}</div>
    </div>
    <p class="popnote">Click a row to open that node. The bar is the ${
      mode==="c" ? "class" : "origin"} mix on that lane, in the diagram's colours — it follows
      the colour switch above. Figures are <b>as drawn</b>, so the flat ribbons (freight that
      stayed in the same building) are included and a node's in and out will differ only where
      the diagram itself shows them differing. Press <b>Esc</b> to close.</p>
  </div>`;
  box.hidden = false;
  // assigned, not added: #pop outlives every redraw, and addEventListener would stack one more
  // backdrop handler on it each time the sheet is rebuilt
  box.onclick = e => { if(e.target === box) openCell(null); };
  box.querySelector(".popclose").addEventListener("click", ()=>openCell(null));
  box.querySelectorAll("[data-go]").forEach(tr=>
    tr.addEventListener("click", ()=>openCell(tr.dataset.go)));
}
addEventListener("keydown", e => { if(e.key==="Escape" && SEL) openCell(null); });


document.querySelectorAll("#focus button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#focus button").forEach(x=>x.setAttribute("aria-pressed", x===b));
  focus = b.dataset.f; render(); drawPop();   // the sheet follows the filter
});
document.querySelectorAll("#mode button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#mode button").forEach(x=>x.setAttribute("aria-pressed", x===b));
  mode = b.dataset.m; render(); drawPop();    // ...and the colour mode, for the mix bars
});
render();
</script></body></html>
"""


def main():
    check_run_matches_model()
    data = build()
    stage = sum(l["v"] for l in data["2"]["links"] if l["s"] == "S:STG")
    kept = sum(l["v"] for l in data["1"]["links"] if l["t"].startswith("K:"))
    scen = next(r["scenarioname"] for r, *_ in read_rows())
    ins = inside()
    srt = sortation(data)
    if srt:
        print(f"  buildings per parcel — model {srt['model_same_day']} on same-day freight"
              + (f", measured {srt['obs']['same_day']}" if srt["obs"] else ""))
        if srt.get("lanes"):
            _dead = [l for l in srt["lanes"] if l["m"] == 0 and l["o"] > 0]
            _mt, _ot = (sum(l[k] for l in srt["lanes"]) for k in ("m", "o"))
            print(f"    second building: model {srt['model_two']:.1%} of same-day vs measured "
                  f"{srt['obs']['two_modelled']:.1%} over the same {srt['sites_modelled']} "
                  f"buildings ({_mt:,} vs {_ot:,} EA on {len(srt['lanes'])} lanes)")
            if _dead:
                print(f"    {len(_dead)} lanes the solver ran NONE of, worth "
                      f"{sum(l['o'] for l in _dead):,} EA measured: "
                      + ", ".join(f"{l['a']}->{l['b']}" for l in _dead))
            if srt.get("outside"):
                print(f"    a further {srt['outside']:,} EA of measured second buildings is at a "
                      f"site chain 2 has no node for ({' / '.join(srt['outside_at'])})")
    html = (HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
                .replace("__SCEN__", scen))
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB)")
    for k in ("1", "2"):
        d = data[k]
        print(f"  chain {k}: {len(d['nodes'])} nodes, {len(d['links'])} ribbons, "
              f"{len(d['cols'])} stages — {' → '.join(d['cols'])}")


if __name__ == "__main__":
    main()
