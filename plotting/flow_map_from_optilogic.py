"""Every lane the SOLVER uses, on a real map.  ── model output, not measurement ──

    IN   outputs/run_outputs/OptimizationFlowSummary.csv     what NEO decided (coords included)
         inputs/factors_assumed/sites.csv                    building names, codes and roles
    OUT  outputs/flow-map-from-optilogic.html                a Leaflet page, one file

The Sankey (`sankey_from_optilogic.py`) says how much goes through each stage; this page says
WHERE — every origin -> destination lane as a curved arrow, its width the EA/day the solve put
on it, its colour the leg (first / middle / last mile, as the flow type names it). Filters for a
building (out of / into / either), service, stage, origin family, a single product and vehicle.

ZERO-LENGTH ROWS ARE NOT LANES. 664k EA of the solve is ledger — an entry supplier sitting on
the hub it feeds, or an interstate / PDO / kept-at-depot sink sitting on the building that sorted
it. They have the same coordinates at both ends, so drawing them would be a dot. They are folded
onto the building they sit on (matched by coordinate, never by name) and shown there as
"starts here" / "ends here", with a ring sized by what ends there.

NOTHING about the network is typed here: building identity comes from sites.csv, product parts
are parsed off the run's own product names, and a node is "at a building" because its
coordinates are.

Run:  uv run python plotting/flow_map_from_optilogic.py
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "outputs/run_outputs/OptimizationFlowSummary.csv"
SITES = ROOT / "inputs/factors_assumed/sites.csv"
OUT = ROOT / "outputs/flow-map-from-optilogic.html"

# Legs. First and middle mile are the flow type (mile-terminology convention). The model's
# CustomerFulfillment is NOT one thing, so it is split by where the freight goes:
#   CZ_PUD_*            Delivery — out on the vans to a delivery area, WHATEVER the family:
#                       chain 2's STG_ (kept at depot) volume is delivered from its depot like
#                       any other, so it is green here and found with the origin-family chip
#   CZ_LocalTerm_       Kept at depot — STAYS at the depot (zero-length): the solid ring, never
#                       an arrow
#   any other CZ_ sink  Hand-over — a chain-1 sink; drawn only when it moves between buildings
#                       (Vic Metro to Metro), otherwise it is "ends here" on the building
LEGS = ["First mile", "Middle mile", "Hand-over", "Delivery", "Kept at depot"]
LEG_SHORT = ["First", "Middle", "Hand-over", "Delivery", "Kept"]
LANE_LEGS = [0, 1, 2, 3]                 # the legs that can be a lane; kept at depot never is


def leg_of(flowtype, dest, product):
    if flowtype == "Procurement":
        return 0
    if flowtype == "Replenishment":
        return 1
    if dest.startswith("CZ_LocalTerm_"):
        return 4
    return 2 if not dest.startswith("CZ_PUD_") else 3
PROD_RE = re.compile(r"^(EP|PP)_(.+?)_(Pickup|Sort0|Despatch1_[A-Z]{3}|Despatch2R?|Delivered)$")
SERVICE = {"EP": "Express", "PP": "Parcel Post"}
STAGE = {"Pickup": "Collected (unsorted)", "Sort0": "Sorted at pickup depot",
         "Despatch1": "Round-1 sorted", "Despatch2": "Sorted once, to depot",
         "Despatch2R": "Sorted twice, to depot", "Delivered": "Out for delivery"}
CHAIN2 = {"INTERSTATE": "Interstate", "MET": "Vic Metro to Metro",
          "REG": "Regional Vic to Metro Vic", "STG": "Kept at depot"}
SINKS = {"CZ_Interstate_": "Interstate export", "CZ_PdoTerm_": "PDO terminate",
         "CZ_MetroTerm_": "Vic Metro to Metro hand-over", "CZ_LocalTerm_": "Kept at depot",
         "CZ_Regional_": "Regional"}


def parse_product(p):
    svc, fam, stage = PROD_RE.match(p).groups()
    stage = stage.split("_")[0]
    head = fam.split("_")[0]
    if head in CHAIN2:                        # INTERSTATE_MNP, STG_Mount_Waverley, MET ...
        family, chain = CHAIN2[head], 2
    else:                                     # a chain-1 pickup cluster: DANDENONG_TR, WEST ...
        family, chain = "Pickup · " + fam.replace("_", " ").title(), 1
    return {"n": p, "s": svc, "f": family, "g": stage, "c": chain}


def node_kind(n):
    if n.startswith("SUP_PKP_"):
        return "pickup"
    if n.startswith("CZ_PUD_"):
        return "delivery"
    if n.startswith("SUP_"):
        return "entry"
    if n.startswith("CZ_"):
        return "sink"
    return "building"


def node_label(n):
    for k, v in SINKS.items():
        if n.startswith(k):
            return f"{v} · {n[len(k):].replace('_', ' ')}"
    if n.startswith("SUP_PKP_"):
        return "Pickup round " + n[8:].replace("_", " ")
    if n.startswith("CZ_PUD_"):
        return "Delivery area " + n[7:].replace("_", " ")
    for k, v in (("SUP_INT_", "Interstate arrivals"), ("SUP_MET_", "Vic Metro arrivals"),
                 ("SUP_REG_", "Regional Vic arrivals"), ("SUP_STAGE_", "Kept at depot")):
        if n.startswith(k):
            return f"{v} · {n[len(k):].replace('_', ' ')}"
    return n.replace("_", " ")


def build():
    fl = pd.read_csv(RUN, low_memory=False)
    fl = fl[fl.flowquantity > 0].copy()
    fl["modename"] = fl.modename.fillna("None_(ledger)")         # the zero-length ledger rows
    sites = pd.read_csv(SITES).set_index("node")

    # ── nodes ─────────────────────────────────────────────────────────────────────
    ends = pd.concat([
        fl[["originname", "originlatitude", "originlongitude"]].set_axis(["n", "lat", "lon"], axis=1),
        fl[["destinationname", "destinationlatitude", "destinationlongitude"]]
        .set_axis(["n", "lat", "lon"], axis=1)]).drop_duplicates("n")
    ends["k"] = ends.n.map(node_kind)
    blds = ends[ends.k == "building"]
    unknown = sorted(set(blds.n) - set(sites.index))
    assert not unknown, f"buildings in the run that sites.csv lacks: {unknown}"
    # a node "is at" a building when its coordinates are (to ~1 m) — that is the whole test
    at_xy = {(round(r.lat, 5), round(r.lon, 5)): r.n for r in blds.itertuples()}

    nodes, idx = [], {}
    def add(n, lat, lon, kind):
        idx[n] = len(nodes)
        if kind == "building":
            s = sites.loc[n]
            code = s.code if isinstance(s.code, str) else "".join(
                w[0] for w in n.split("_", 1)[1].split("_"))
            nodes.append({"id": n, "name": s.display, "code": code, "k": s.role,
                          "lat": round(lat, 5), "lon": round(lon, 5)})
        else:
            nodes.append({"id": n, "name": node_label(n), "k": kind,
                          "lat": round(lat, 5), "lon": round(lon, 5)})
    for r in blds.sort_values("n").itertuples():
        add(r.n, r.lat, r.lon, "building")
    for r in ends[ends.k != "building"].sort_values("n").itertuples():
        add(r.n, r.lat, r.lon, r.k)
    # where each node is DRAWN: on its building if it sits on one, else itself
    for nd in nodes:
        b = at_xy.get((nd["lat"], nd["lon"]))           # both sides rounded to 5 dp, once
        nd["at"] = idx[b] if b else idx[nd["id"]]

    # ── products, vehicles, rows ──────────────────────────────────────────────────
    prods = sorted(fl.productname.unique())
    pidx = {p: i for i, p in enumerate(prods)}
    modes = sorted(fl.modename.unique())
    midx = {m: i for i, m in enumerate(modes)}
    rows = [[idx[r.originname], idx[r.destinationname],
             leg_of(r.flowtype, r.destinationname, r.productname),
             midx[r.modename], pidx[r.productname], round(r.flowquantity, 1),
             round(r.transportdistance, 1)] for r in fl.itertuples()]

    # the conservation the reader should be able to check: first mile in == last mile out
    tot = fl.groupby("flowtype").flowquantity.sum()
    data = {"nodes": nodes, "products": [parse_product(p) for p in prods], "modes": modes,
            "legs": LEGS, "legShort": LEG_SHORT, "laneLegs": LANE_LEGS, "stages": STAGE, "services": SERVICE,
            "rows": rows, "scenario": str(fl.scenarioname.iloc[0]),
            "total": round(float(tot.get("Procurement", 0)))}
    js = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    OUT.write_text(PAGE.replace("/*DATA*/", js), encoding="utf-8")
    zero = sum(r[5] for r in rows if nodes[r[0]]["at"] == nodes[r[1]]["at"])
    print(f"{OUT.relative_to(ROOT)}  {len(rows):,} rows, {len(nodes):,} nodes, "
          f"{len(prods)} products · {zero:,.0f} EA is zero-length (folded onto buildings)")


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Flow Map</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{
  --page:#f4f6f8; --surface:#ffffff; --surface-2:#fafbfc; --surface-3:#eef1f4;
  --ink:#0e1419; --ink-2:#4b555e; --muted:#7b858e;
  --line:#e2e7ec; --line-strong:#cfd6dd; --accent:#2a78d6;
  --shadow:0 1px 2px rgba(14,20,25,.06);
  --leg-0:#2a78d6; --leg-1:#eb6834; --leg-2:#eb6834; --leg-3:#1baf7a; --leg-4:#9c3f93;
  --hl:#0e1419;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --shadow:0 1px 2px rgba(0,0,0,.4);
  --leg-0:#3987e5; --leg-1:#d95926; --leg-2:#d95926; --leg-3:#199e70; --leg-4:#c25bb8; --hl:#ffffff;
}}
:root[data-theme="dark"]{
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --shadow:0 1px 2px rgba(0,0,0,.4);
  --leg-0:#3987e5; --leg-1:#d95926; --leg-2:#d95926; --leg-3:#199e70; --leg-4:#c25bb8; --hl:#ffffff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1500px;margin:0 auto;padding:22px 20px 40px}
.num{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;gap:10px 28px;align-items:baseline;padding-bottom:14px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:19px;font-weight:600;letter-spacing:-.01em}
.sub{margin:0;color:var(--ink-2);font-size:13px;max-width:78ch}
.basis{font-size:12px;color:var(--muted)} .basis b{color:var(--ink-2);font-weight:500}

.controls{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px 22px;margin:16px 0 14px}
.ctl{display:flex;flex-direction:column;gap:6px;min-width:0}
.ctl>label,.ctl>.lab{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:500;
  display:flex;justify-content:space-between;gap:8px}
.ctl .lab button{all:unset;cursor:pointer;color:var(--accent);text-transform:none;letter-spacing:0;font-size:11.5px}
.chips{display:flex;flex-wrap:wrap;gap:5px}
.chip{font:inherit;font-size:12px;cursor:pointer;padding:3px 10px;border-radius:999px;
  border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);display:inline-flex;align-items:center;gap:6px}
.chip:hover{background:var(--surface-3);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip .sw{width:14px;height:3px;border-radius:2px;display:inline-block}
.chip:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
select,input[type=range]{font:inherit;font-size:12.5px;color:var(--ink);background:var(--surface);
  border:1px solid var(--line-strong);border-radius:6px;padding:4px 6px;max-width:100%}
input[type=range]{padding:0;accent-color:var(--accent)}
.row2{display:flex;gap:8px;flex-wrap:wrap;align-items:center}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:14px}
.stat{background:var(--surface);padding:9px 13px}
.stat dt{font-size:11px;color:var(--muted)} .stat dd{margin:2px 0 0;font-size:17px;font-weight:600}

.main{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);overflow:hidden}
.panel-hd{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;justify-content:space-between;
  padding:10px 14px;border-bottom:1px solid var(--line)}
.panel-hd h2{margin:0;font-size:13px;font-weight:600}
.note{font-size:11.5px;color:var(--muted)}
#map{height:720px;background:var(--surface-2)}
.legend{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;padding:9px 14px;border-top:1px solid var(--line);
  font-size:11.5px;color:var(--ink-2)}
.legend .k{display:inline-flex;align-items:center;gap:6px}
.legend .ln{display:inline-block;border-radius:2px}
.legend .dot{width:8px;height:8px;border-radius:50%;display:inline-block;border:1.5px solid var(--surface)}

.tblwrap{max-height:460px;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
thead th{position:sticky;top:0;background:var(--surface-2);text-align:right;font-weight:500;color:var(--muted);
  font-size:11px;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;z-index:1}
thead th:first-child,tbody td:first-child{text-align:left}
tbody td{padding:5px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
tbody td:first-child{white-space:normal}
tbody tr{cursor:pointer} tbody tr:hover,tbody tr.on{background:var(--surface-3)}
.lg{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.fac{padding:12px 14px;border-bottom:1px solid var(--line);font-size:12.5px}
.fac h3{margin:0 0 6px;font-size:14px}
.fac dl{display:grid;gap:3px 24px;margin:0}
.fac dt{color:var(--muted)} .fac dd{margin:0;text-align:right}
.fac .hd{font-size:11px;color:var(--muted);text-align:right}

/* building badges */
.bld{display:flex;align-items:center;justify-content:center;font:600 10px/1 "IBM Plex Mono",monospace;
  color:var(--ink);background:var(--surface);border:2px solid var(--ink);min-width:30px;height:20px;padding:0 4px;
  border-radius:10px;box-shadow:0 0 0 2px var(--surface);cursor:pointer;white-space:nowrap}
.bld.hub{border-radius:3px;background:var(--ink);color:var(--surface)}
.bld.sort_only{border-radius:3px;border-style:dashed}
.bld.transport{border-radius:3px 10px 10px 3px}
.bld.on{outline:3px solid var(--accent);outline-offset:1px}
.bld.dim{opacity:.35}
.leaflet-tooltip.tt{background:var(--surface);color:var(--ink);border:1px solid var(--line-strong);border-radius:8px;
  box-shadow:0 6px 20px rgba(14,20,25,.16);font:12px/1.45 "IBM Plex Sans",system-ui,sans-serif;padding:8px 10px;max-width:340px;white-space:normal}
.leaflet-tooltip.tt::before{display:none}
.tt h4{margin:0 0 4px;font-size:12.5px;font-weight:600}
.tt .m{color:var(--muted)} .tt table{font-size:11.5px;margin-top:4px}
.tt td{padding:1px 0;border:0;text-align:left} .tt td+td{text-align:right;padding-left:12px}
.leaflet-container{font-family:inherit}
footer{margin-top:18px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;max-width:90ch}
footer code{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--ink-2)}
@media (max-width:980px){ #map{height:480px} .tblwrap{max-height:420px} }
@media (max-width:460px){ .wrap{padding-inline:16px} .controls{grid-template-columns:1fr} }
</style></head><body>
<div class="wrap">
<header>
  <div style="flex:1 1 420px">
    <h1>Where the network sends each parcel</h1>
    <p class="sub">Every origin&nbsp;&rarr;&nbsp;destination lane in the solved model. Arrow width is EA/day, colour is the leg.
      Pick a building to see what leaves it and what arrives, or narrow by product.</p>
  </div>
  <div class="basis"><b>Scenario</b> <span id="scen"></span> &middot; <b>Total</b> <span class="num" id="tot"></span> EA/day collected and delivered</div>
</header>

<div class="controls">
  <div class="ctl"><div class="lab"><span>Building</span><button type="button" id="clrFac">Clear</button></div>
    <div class="row2"><select id="fac" aria-label="Building"></select>
      <div class="chips" id="dir" role="group" aria-label="Direction"></div></div></div>
  <div class="ctl"><div class="lab"><span>Leg</span><button type="button" data-all="leg">All</button></div><div class="chips" id="leg"></div></div>
  <div class="ctl"><div class="lab"><span>Service</span><button type="button" data-all="svc">All</button></div><div class="chips" id="svc"></div></div>
  <div class="ctl"><div class="lab"><span>Stage</span><button type="button" data-all="stg">All</button></div><div class="chips" id="stg"></div></div>
  <div class="ctl"><div class="lab"><span>Origin family</span><button type="button" data-all="fam">All</button></div><div class="chips" id="fam"></div></div>
  <div class="ctl"><div class="lab"><span>Vehicle</span><button type="button" data-all="veh">All</button></div><div class="chips" id="veh"></div></div>
  <div class="ctl"><label for="prod">Single product <span style="text-transform:none;letter-spacing:0">(shift-click a chip = only that one)</span></label><select id="prod"></select></div>
  <div class="ctl"><label for="min">Hide lanes under <span class="num" id="minv"></span> EA/day</label>
    <input type="range" id="min" min="0" max="12" step="1" value="0"></div>
</div>

<dl class="stats" id="stats"></dl>

<div class="main">
  <section class="panel">
    <div class="panel-hd"><h2 id="maptitle">All lanes</h2><span class="note">Hover an arrow or building for detail &middot; click a building to select it</span></div>
    <div id="map"></div>
    <div class="legend" id="legend"></div>
  </section>
  <section class="panel">
    <div id="facbox"></div>
    <div class="panel-hd"><h2>Largest lanes in view</h2><span class="note" id="tblnote"></span></div>
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">From &rarr; To</th><th scope="col">EA/day</th><th scope="col">km</th></tr></thead>
      <tbody id="tbody"></tbody></table></div>
  </section>
</div>
<footer>
  Lanes with the same coordinates at both ends are not drawn: they are ledger entries — an arrival supplier sitting on the
  building it feeds, or a sink (interstate export, PDO terminate, kept at depot) sitting on the building that sorted it.
  They are counted on that building as <b>starts here</b> / <b>ends here</b>; the dashed ring is sized by what ends there.
  First and middle mile follow the model's flow type. Its CustomerFulfillment is split by destination: <b>Delivery</b> goes out to a
  delivery area; <b>Kept at depot</b> (plum ring) is pickup that stays at the depot it was collected at — it never moves
  between buildings, so it is never an arrow (its delivery to customers is ordinary Delivery: pick Origin family
  &ldquo;Kept at depot&rdquo; to see it); <b>Hand-over</b> (dashed) is a chain-1 sink in another building (Vic Metro to Metro).
  <br><br>Source: <code>outputs/run_outputs/OptimizationFlowSummary.csv</code> via <code>plotting/flow_map_from_optilogic.py</code>.
</footer>
</div>
<script id="data" type="application/json">/*DATA*/</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const N = D.nodes, P = D.products, R = D.rows;
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = v => Math.round(v).toLocaleString("en-AU");
const fkm = v => v < 10 ? v.toFixed(1) : fmt(v);
const legCol = i => css("--leg-" + i);
const DASH = { 2: "7 6" };                  // hand-over: a truck between buildings, not a mile of its own
// a swatch that carries the dash too, so the legend matches the line
const sw = (k, w = 14, h = 3) => `<i class="ln" style="display:inline-block;width:${w}px;height:${h}px;border-radius:2px;background:${
  DASH[k] ? `repeating-linear-gradient(90deg,${legCol(k)} 0 5px,transparent 5px 8px)` : legCol(k)}"></i>`;
const zeros = () => D.legs.map(() => 0);
const isBld = n => !["pickup","delivery","entry","sink"].includes(n.k);
const STEPS = [0,1,5,10,25,50,100,250,500,1000,2500,5000,10000];
$("scen").textContent = D.scenario; $("tot").textContent = fmt(D.total);

/* ── filter state ─────────────────────────────────────────────────────── */
const uniq = a => [...new Set(a)];
const FAMS = uniq(P.map(p => p.f)).sort((a, b) => (a.startsWith("Pickup") - b.startsWith("Pickup")) || a.localeCompare(b));
const STG = Object.keys(D.stages).filter(s => P.some(p => p.g === s));
const groups = {
  leg: { opts: D.legs.map((l, i) => [i, l]), on: new Set(D.legs.map((_, i) => i)) },
  svc: { opts: Object.entries(D.services), on: new Set(Object.keys(D.services)) },
  stg: { opts: STG.map(s => [s, D.stages[s]]), on: new Set(STG) },
  fam: { opts: FAMS.map(f => [f, f]), on: new Set(FAMS) },
  veh: { opts: D.modes.map((m, i) => [i, m.replace(/_/g, " ")]), on: new Set(D.modes.map((_, i) => i)) },
};
let fac = null, dir = "both", prod = -1, minEA = 0;

function chips(key) {
  const g = groups[key], box = $(key); box.innerHTML = "";
  g.opts.forEach(([v, lab]) => {
    const b = document.createElement("button");
    b.className = "chip"; b.type = "button"; b.setAttribute("aria-pressed", g.on.has(v));
    if (key === "leg") b.innerHTML = sw(v);
    b.append(lab);
    // click = toggle; alt/shift-click = this one only
    b.onclick = e => {
      if (e.altKey || e.shiftKey) { g.on = new Set([v]); }
      else g.on.has(v) ? g.on.delete(v) : g.on.add(v);
      chips(key); draw();
    };
    box.append(b);
  });
}
Object.keys(groups).forEach(chips);
document.querySelectorAll("[data-all]").forEach(b => b.onclick = () => {
  const g = groups[b.dataset.all]; g.on = new Set(g.opts.map(o => o[0])); chips(b.dataset.all); draw();
});

const facSel = $("fac");
const BLD = N.map((n, i) => [n, i]).filter(([n]) => isBld(n)).sort((a, b) => a[0].name.localeCompare(b[0].name));
facSel.innerHTML = `<option value="">All buildings</option>` + BLD.map(([n, i]) => `<option value="${i}">${n.name} (${n.code})</option>`).join("");
facSel.onchange = () => { fac = facSel.value === "" ? null : +facSel.value; draw(true); };
$("clrFac").onclick = () => { fac = null; facSel.value = ""; draw(); };
function dirChips() {
  $("dir").innerHTML = "";
  [["both", "In & out"], ["out", "Out of"], ["in", "Into"]].forEach(([v, l]) => {
    const b = document.createElement("button"); b.className = "chip"; b.type = "button";
    b.setAttribute("aria-pressed", dir === v); b.textContent = l; b.disabled = fac === null;
    b.style.opacity = fac === null ? .45 : 1;
    b.onclick = () => { dir = v; dirChips(); draw(); }; $("dir").append(b);
  });
}
$("min").oninput = e => { minEA = STEPS[+e.target.value]; $("minv").textContent = fmt(minEA); draw(); };
$("minv").textContent = "0";

function prodOptions() {
  const keep = P.map((p, i) => [p, i]).filter(([p]) => passProd(p));
  if (prod >= 0 && !keep.some(([, i]) => i === prod)) prod = -1;
  $("prod").innerHTML = `<option value="-1">All products (${keep.length})</option>` +
    keep.map(([p, i]) => `<option value="${i}" ${i === prod ? "selected" : ""}>${p.n}</option>`).join("");
}
$("prod").onchange = e => { prod = +e.target.value; draw(); };
function passProd(p) {
  return groups.svc.on.has(p.s) && groups.stg.on.has(p.g) && groups.fam.on.has(p.f);
}

/* ── map ──────────────────────────────────────────────────────────────── */
const map = L.map("map", { preferCanvas: true, zoomSnap: .25 });
const dark = () => document.documentElement.dataset.theme === "dark" ||
  (document.documentElement.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
// Esri's grey canvas needs no key and loads from a local file:// page (CARTO refuses one)
const tiles = L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark() ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
  { attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap", maxZoom: 16 }).addTo(map);
const arcLayer = L.layerGroup().addTo(map), dotLayer = L.layerGroup().addTo(map),
      ringLayer = L.layerGroup().addTo(map), bldLayer = L.layerGroup().addTo(map);
const renderer = L.canvas({ padding: .3, tolerance: 4 });
// rings get their own pane ABOVE the lanes — on the shared canvas, a depot's spokes bury its ring
map.createPane("rings").style.zIndex = 450; map.getPane("rings").style.pointerEvents = "none";
const ringRenderer = L.canvas({ pane: "rings", padding: .3 });
const ll = N.map(n => L.latLng(n.lat, n.lon));
map.fitBounds(L.latLngBounds(N.filter(isBld).map(n => [n.lat, n.lon])).pad(.25));

let MAXW = 1;      // fixed width scale = the largest lane in the whole solve, so filters compare honestly
const width = ea => 1 + 13 * Math.sqrt(ea / MAXW);

/* screen-space curved arrow: quadratic bezier bowed to the RIGHT of travel, so A->B and B->A separate */
function arcPts(a, b, bend) {
  const A = map.latLngToLayerPoint(ll[a]), B = map.latLngToLayerPoint(ll[b]);
  const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy) || 1;
  const C = L.point((A.x + B.x) / 2 - dy * bend, (A.y + B.y) / 2 + dx * bend);
  const pts = [];
  for (let i = 0; i <= 24; i++) {
    const t = i / 24, u = 1 - t;
    pts.push(L.point(u * u * A.x + 2 * u * t * C.x + t * t * B.x, u * u * A.y + 2 * u * t * C.y + t * t * B.y));
  }
  return { pts, len };
}
function arrowHead(pts, w) {
  // place the head at 62% along so it never hides under the destination badge
  const i = 15, p = pts[i], q = pts[i - 1];
  const ang = Math.atan2(p.y - q.y, p.x - q.x), s = 4 + w * 1.3;
  const tip = L.point(p.x + Math.cos(ang) * s, p.y + Math.sin(ang) * s);
  const l = L.point(p.x + Math.cos(ang + 2.5) * s, p.y + Math.sin(ang + 2.5) * s);
  const r = L.point(p.x + Math.cos(ang - 2.5) * s, p.y + Math.sin(ang - 2.5) * s);
  return [tip, l, r].map(x => map.layerPointToLatLng(x));
}

/* ── aggregate under the current filter ───────────────────────────────── */
let ARCS = [], LOCAL = {}, TOUCH = {};
function aggregate() {
  const arcs = new Map(); LOCAL = {}; TOUCH = {};
  for (const r of R) {
    const [o, d, lg, m, pi, ea, km] = r, p = P[pi];
    if (!groups.leg.on.has(lg) || !groups.veh.on.has(m)) continue;
    if (prod >= 0 ? pi !== prod : !passProd(p)) continue;
    const a = N[o].at, b = N[d].at;
    if (fac !== null) {
      const out = a === fac, inn = b === fac;
      if (dir === "out" ? !out : dir === "in" ? !inn : !(out || inn)) continue;
    }
    if (a === b) {                                    // ledger, not a lane
      const L_ = LOCAL[a] ??= { start: 0, end: 0, kept: 0, sm: {}, em: {} };
      if (N[o].k === "entry" || N[o].k === "pickup") { L_.start += ea; L_.sm[N[o].name] = (L_.sm[N[o].name] || 0) + ea; }
      if (N[d].k === "sink") { if (N[d].id.startsWith("CZ_LocalTerm_")) L_.kept += ea; L_.end += ea; L_.em[N[d].name] = (L_.em[N[d].name] || 0) + ea; }
      continue;
    }
    const k = a + ">" + b + ">" + lg;
    let A = arcs.get(k);
    if (!A) arcs.set(k, A = { a, b, lg, ea: 0, km, prods: {}, modes: {} });
    A.ea += ea; A.prods[pi] = (A.prods[pi] || 0) + ea; A.modes[m] = (A.modes[m] || 0) + ea;
    for (const [x, io] of [[a, "out"], [b, "in"]]) {
      const T = TOUCH[x] ??= { in: zeros(), out: zeros() }; T[io][lg] += ea;
    }
  }
  ARCS = [...arcs.values()].filter(A => A.ea >= minEA).sort((x, y) => x.ea - y.ea);
}
// the fixed width scale, computed once over everything
(function () { const s = {}; for (const r of R) { const a = N[r[0]].at, b = N[r[1]].at; if (a !== b) { const k = a + ">" + b + ">" + r[2]; s[k] = (s[k] || 0) + r[5]; } }
  MAXW = Math.max(...Object.values(s)); })();

/* ── draw ─────────────────────────────────────────────────────────────── */
let lineOf = new Map(), selArc = null;
function arcTip(A) {
  const top = Object.entries(A.prods).sort((x, y) => y[1] - x[1]);
  const veh = Object.entries(A.modes).sort((x, y) => y[1] - x[1]).map(([m, v]) => D.modes[m].replace(/_/g, " ")).join(", ");
  return `<h4>${N[A.a].name} &rarr; ${N[A.b].name}</h4>
    <div class="m">${D.legs[A.lg]} &middot; ${fkm(A.km)} km &middot; ${veh}</div>
    <div><b class="num">${fmt(A.ea)}</b> EA/day</div>
    <table>${top.slice(0, 7).map(([pi, v]) => `<tr><td>${P[pi].n}</td><td class="num">${fmt(v)}</td></tr>`).join("")}
    ${top.length > 7 ? `<tr><td class="m">+ ${top.length - 7} more products</td><td class="num m">${fmt(top.slice(7).reduce((s, x) => s + x[1], 0))}</td></tr>` : ""}</table>`;
}
function renderArcs() {
  arcLayer.clearLayers(); lineOf = new Map();
  const pair = new Set(ARCS.map(A => A.a + ">" + A.b));
  // big lanes first, so a small one (a depot's kept volume going out) is never buried under them
  for (const A of ARCS.slice().reverse()) {
    const w = width(A.ea), col = legCol(A.lg);
    // a little more bow when the reverse lane exists or the same pair carries another leg
    const bend = .12 + (pair.has(A.b + ">" + A.a) ? .04 : 0) + A.lg * .03;
    const { pts } = arcPts(A.a, A.b, bend);
    const lls = pts.map(p => map.layerPointToLatLng(p));
    const on = selArc === A.a + ">" + A.b + ">" + A.lg;
    const op = selArc && !on ? .25 : .78;
    const line = L.polyline(lls, { renderer, color: on ? css("--hl") : col, weight: w, opacity: op, lineCap: DASH[A.lg] ? "butt" : "round", dashArray: DASH[A.lg] })
      .bindTooltip(() => arcTip(A), { sticky: true, className: "tt", direction: "top", offset: [0, -8] });
    const head = L.polygon(arrowHead(pts, w), { renderer, stroke: false, fillColor: on ? css("--hl") : col, fillOpacity: Math.min(1, op + .15), interactive: false });
    line.on("mouseover", () => line.setStyle({ opacity: 1 })).on("mouseout", () => line.setStyle({ opacity: op }));
    line.on("click", () => { selArc = on ? null : A.a + ">" + A.b + ">" + A.lg; renderArcs(); renderTable(); });
    arcLayer.addLayer(line); arcLayer.addLayer(head); lineOf.set(A.a + ">" + A.b + ">" + A.lg, line);
  }
}
function renderNodes() {
  dotLayer.clearLayers(); ringLayer.clearLayers(); bldLayer.clearLayers();
  const used = new Set(ARCS.flatMap(A => [A.a, A.b]));
  const kc = { pickup: css("--leg-0"), delivery: css("--leg-3") };
  N.forEach((n, i) => {
    if (isBld(n) || n.at !== i || !used.has(i)) return;
    const T = TOUCH[i] || { in: zeros(), out: zeros() };
    const tot = T.in.reduce((s, x) => s + x, 0) + T.out.reduce((s, x) => s + x, 0);
    dotLayer.addLayer(L.circleMarker(ll[i], { renderer, radius: 2.5, color: css("--surface"), weight: 1,
      fillColor: kc[n.k] || css("--muted"), fillOpacity: .85 })
      .bindTooltip(`<h4>${n.name}</h4><div class="num">${fmt(tot)} EA/day</div>`, { className: "tt", direction: "top" }));
  });
  const endMax = Math.max(1, ...Object.values(LOCAL).map(x => x.end));
  BLD.forEach(([n, i]) => {
    const T = TOUCH[i], Lc = LOCAL[i];
    const active = T || Lc;
    const other = Lc ? Lc.end - Lc.kept : 0;
    if (other > 0) ringLayer.addLayer(L.circleMarker(ll[i], { renderer: ringRenderer, radius: 26 + 24 * Math.sqrt(other / endMax),
      color: css("--ink-2"), weight: 1.5, dashArray: "4 4", fill: false, interactive: false }));
    if (Lc && Lc.kept > 0) ringLayer.addLayer(L.circleMarker(ll[i], { renderer: ringRenderer, radius: 21,
      color: legCol(4), weight: 3, fill: false, interactive: false }));
    const cls = `bld ${n.k}${fac === i ? " on" : ""}${active || fac === i ? "" : " dim"}`;
    const m = L.marker(ll[i], { icon: L.divIcon({ className: "", html: `<div class="${cls}">${n.code}</div>`, iconSize: null, iconAnchor: [17, 10] }),
      zIndexOffset: fac === i ? 3000 : n.k === "hub" ? 1000 : 500, keyboard: true, title: n.name });
    m.bindTooltip(() => bldTip(i), { className: "tt", direction: "top", offset: [0, -10] });
    m.on("click", () => { fac = fac === i ? null : i; facSel.value = fac ?? ""; draw(); });
    bldLayer.addLayer(m);
  });
}
function bldTip(i) {
  const T = TOUCH[i] || { in: zeros(), out: zeros() }, Lc = LOCAL[i];
  const row = (l, a) => a.some(x => x) ? `<tr><td>${l}</td>${a.map(x => `<td class="num">${x ? fmt(x) : "–"}</td>`).join("")}</tr>` : "";
  let h = `<h4>${N[i].name} <span class="m">${N[i].code}</span></h4>
    <table><tr><td></td>${D.laneLegs.map(k => `<td class="m">${sw(k, 10)} ${D.legShort[k]}</td>`).join("")}</tr>
    ${row("Arrives", D.laneLegs.map(k => T.in[k]))}${row("Leaves", D.laneLegs.map(k => T.out[k]))}</table>`;
  if (Lc) {
    const list = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${k}</td><td class="num">${fmt(v)}</td></tr>`).join("");
    if (Lc.start) h += `<div class="m" style="margin-top:6px">Starts here &middot; <b class="num">${fmt(Lc.start)}</b></div><table>${list(Lc.sm)}</table>`;
    if (Lc.kept) h += `<div class="m" style="margin-top:6px">${sw(4, 10)} Kept at depot (stays) &middot; <b class="num">${fmt(Lc.kept)}</b></div>`;
    if (Lc.end) h += `<div class="m" style="margin-top:6px">Ends here &middot; <b class="num">${fmt(Lc.end)}</b></div><table>${list(Lc.em)}</table>`;
  }
  return h;
}
function renderTable() {
  const top = ARCS.slice().sort((x, y) => y.ea - x.ea).slice(0, 60);
  $("tblnote").textContent = `${Math.min(60, ARCS.length)} of ${ARCS.length.toLocaleString()} lanes`;
  $("tbody").innerHTML = top.map(A => {
    const k = A.a + ">" + A.b + ">" + A.lg;
    return `<tr data-k="${k}" class="${selArc === k ? "on" : ""}"><td>${sw(A.lg, 12)} ${N[A.a].name} &rarr; ${N[A.b].name}</td>
      <td class="num">${fmt(A.ea)}</td><td class="num">${fkm(A.km)}</td></tr>`; }).join("");
  $("tbody").querySelectorAll("tr").forEach(tr => tr.onclick = () => {
    const k = tr.dataset.k; selArc = selArc === k ? null : k;
    const A = ARCS.find(x => x.a + ">" + x.b + ">" + x.lg === k);
    if (selArc) map.fitBounds(L.latLngBounds([ll[A.a], ll[A.b]]).pad(.4), { maxZoom: 13 });
    renderArcs(); renderTable();
    if (selArc) lineOf.get(k)?.openTooltip(L.latLngBounds([ll[A.a], ll[A.b]]).getCenter());
  });
}
function renderFac() {
  if (fac === null) { $("facbox").innerHTML = ""; return; }
  const T = TOUCH[fac] || { in: zeros(), out: zeros() }, Lc = LOCAL[fac] || { start: 0, end: 0, kept: 0 }, n = D.laneLegs.length;
  $("facbox").innerHTML = `<div class="fac"><h3>${N[fac].name} <span class="note">${N[fac].code} &middot; ${N[fac].k.replace("_", " ")}</span></h3>
    <dl style="grid-template-columns:minmax(150px,max-content) repeat(${n},minmax(80px,max-content))"><dt></dt>${D.laneLegs.map(k => `<dd class="hd">${sw(k, 10)} ${D.legs[k]}</dd>`).join("")}
    <dt>Arrives on a lane</dt>${D.laneLegs.map(k => T.in[k]).map(x => `<dd class="num">${fmt(x)}</dd>`).join("")}
    <dt>Leaves on a lane</dt>${D.laneLegs.map(k => T.out[k]).map(x => `<dd class="num">${fmt(x)}</dd>`).join("")}
    <dt>Starts here (ledger)</dt><dd class="num" style="grid-column:span ${n}">${fmt(Lc.start)}</dd>
    <dt>${sw(4, 10)} Kept at depot (stays)</dt><dd class="num" style="grid-column:span ${n}">${fmt(Lc.kept)}</dd>
    <dt>Ends here otherwise</dt><dd class="num" style="grid-column:span ${n}">${fmt(Lc.end - Lc.kept)}</dd></dl></div>`;
}
function renderStats() {
  const sum = f => Object.values(LOCAL).reduce((s, x) => s + f(x), 0);
  const kept = sum(x => x.kept), en = sum(x => x.end) - kept;
  const byLeg = D.legs.map((_, k) => ARCS.filter(A => A.lg === k).reduce((s, A) => s + A.ea, 0));
  $("stats").innerHTML = [
    ["Lanes drawn", ARCS.length.toLocaleString()],
    ...D.laneLegs.map(k => [D.legs[k] + " EA", fmt(byLeg[k])]),
    ["Kept at depot (stays)", fmt(kept)], ["Ends at a building otherwise", fmt(en)],
  ].map(([t, v]) => `<div class="stat"><dt>${t}</dt><dd class="num">${v}</dd></div>`).join("");
  const parts = [];
  if (fac !== null) parts.push(`${dir === "out" ? "Out of" : dir === "in" ? "Into" : "In & out of"} ${N[fac].name}`);
  if (prod >= 0) parts.push(P[prod].n);
  $("maptitle").textContent = parts.join(" · ") || "All lanes";
}
function renderLegend() {
  // reactive: an entry only for what is on the map right now
  const legs = new Set(ARCS.map(A => A.lg)), kinds = new Set(ARCS.flatMap(A => [N[A.a].k, N[A.b].k]));
  const top = ARCS.length ? ARCS[ARCS.length - 1].ea : 0;
  const w = [100, 1000, 10000, 50000].filter(x => x <= Math.max(top, 100) * 1.05);
  $("legend").innerHTML = !ARCS.length ? "No lanes match these filters" :
    D.legs.map((l, k) => legs.has(k) ? `<span class="k">${sw(k, 22)}${l}</span>` : "").join("") +
    `<span class="k">${w.map(x => `<i class="ln" style="width:18px;height:${width(x).toFixed(1)}px;background:var(--ink-2)"></i><span class="num">${fmt(x)}</span>`).join(" ")}&nbsp;EA/day</span>` +
    (kinds.has("pickup") ? `<span class="k"><i class="dot" style="background:${legCol(0)}"></i>Pickup round</span>` : "") +
    (kinds.has("delivery") ? `<span class="k"><i class="dot" style="background:${legCol(3)}"></i>Delivery area</span>` : "") +
    (Object.values(LOCAL).some(x => x.kept) ? `<span class="k"><i class="ln" style="width:14px;height:14px;border:3px solid ${legCol(4)};border-radius:50%"></i>Kept at depot (stays at the depot)</span>` : "") +
    (Object.values(LOCAL).some(x => x.end > x.kept) ? `<span class="k"><i class="ln" style="width:14px;height:14px;border:1.5px dashed var(--ink-2);border-radius:50%"></i>Ends in the building (interstate, PDO, regional)</span>` : "");
}
function draw(fit) {
  dirChips(); prodOptions(); aggregate();
  renderArcs(); renderNodes(); renderTable(); renderFac(); renderStats(); renderLegend();
  if (fit && fac !== null) {
    const pts = ARCS.flatMap(A => [ll[A.a], ll[A.b]]);
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(.15), { maxZoom: 12, animate: false });
  }
}
map.on("zoomend", renderArcs);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => location.reload());
draw();
</script></body></html>
"""

if __name__ == "__main__":
    build()
