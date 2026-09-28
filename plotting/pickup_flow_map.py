"""Where the SOLVE sends the pickup — chain 1 on a map, in three steps.  ── model output ──

    IN   outputs/run_outputs/OptimizationFlowSummary.csv     what NEO decided
         inputs/factors_assumed/sites.csv                    building names and codes
         inputs/factors_assumed/first_mile_pickup.csv        the leg-1 vehicle per site
         inputs/pick_up_cluster/cluster_summary.csv          stops per collection round (tooltip)
         inputs/melbourne/first_mile_catchment_polygons.csv  the land under it (base layer only)
    OUT  outputs/pickup-flow-map.html

The Sankey says how much goes where; it cannot say that half the collection is a truck that
never stops at a depot, or that a round in Werribee is sorted in Bayswater. This page draws the
same solve on the ground, one leg per step:

  1 COLLECT     every collection round (a cluster of stops) at its centroid, sized by what the
                solve drew from it, with a spoke to the building it is delivered to. A depot's
                red vans go home; a transport facility's truck drives straight to a sorting
                building, which is why its spokes cross the map.
  2 TO THE SORT the round-1 linehaul — depot -> hub, raw pickup (`*_Pickup` replenishment).
  3 HAND-OVER   where the collection entity lets go of it. Most of it ends in the building that
                sorted it (interstate, PDO terminate, kept at depot, regional); the Vic Metro to
                Metro share that chain 2 collects elsewhere rides a real truck (leg 3c) and is
                drawn as an arrow.

COLOUR IS THE VEHICLE and nothing else (Red_Van / Truck, read from first_mile_pickup.csv), as on
pickup-points-map.html. Buildings carry identity by a labelled badge; a site is picked with the
chips, never by a hue. Linehaul and hand-over arrows are neutral ink — they are trucks between
buildings, not a vehicle choice the collection made.

NOTHING about the network is typed here. Which tag is which site is sites.csv's
`origin_cluster`; which building is which is read off the flow rows' own coordinates.

Run:  uv run python plotting/pickup_flow_map.py
"""
import json
import math
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "outputs/run_outputs/OptimizationFlowSummary.csv"
FASS = ROOT / "inputs/factors_assumed"
CLUSTERS = ROOT / "inputs/pick_up_cluster/cluster_summary.csv"
POLY = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"
OUT = ROOT / "outputs/pickup-flow-map.html"
W = 1000                                        # SVG width; height follows the data's aspect
SINKS = {"CZ_Interstate_": "Interstate", "CZ_PdoTerm_": "PDO terminate",
         "CZ_MetroTerm_": "Vic Metro to Metro", "CZ_LocalTerm_": "Kept at depot",
         "CZ_Regional_": "Regional"}


def load():
    fl = pd.read_csv(RUN, low_memory=False)
    fl = fl[fl.flowquantity > 0]
    sites = pd.read_csv(FASS / "sites.csv")
    tag_site = {r.origin_cluster: r.node for r in sites.itertuples() if isinstance(r.origin_cluster, str)}
    vehicle = dict(pd.read_csv(FASS / "first_mile_pickup.csv")[["site", "mode"]].values)
    stops = dict(pd.read_csv(CLUSTERS, usecols=["cluster_id", "stops"]).values)

    def label(node):
        r = sites.set_index("node").loc[node]
        code = r.code if isinstance(r.code, str) else "".join(
            w[0] for w in node.split("_", 1)[1].split("_"))
        return code, r.display

    # ── leg 1: one row per (round, building it went to), both classes summed ────────
    p = fl[(fl.flowtype == "Procurement") & fl.originname.str.startswith("SUP_PKP_")].copy()
    reg = p[p.originname.str.startswith("SUP_PKP_REGIONAL")]
    p = p.drop(reg.index)
    # the longest tag first, so DANDENONG_TR is not read as DANDENONG
    tags = sorted(tag_site, key=len, reverse=True)
    def split(s):
        rest = s[len("SUP_PKP_"):]
        t = next(t for t in tags if rest.startswith(t + "_"))
        return t, rest[len(t) + 1:]
    p[["tag", "cell"]] = p.originname.apply(lambda s: pd.Series(split(s)))
    p["site"] = p.tag.map(tag_site)
    legs = (p.groupby(["originname", "cell", "site", "destinationname"], as_index=False)
             .agg(ea=("flowquantity", "sum"), km=("transportdistance", "first"),
                  cost=("totalcost", "sum"), lat=("originlatitude", "first"),
                  lon=("originlongitude", "first"), dlat=("destinationlatitude", "first"),
                  dlon=("destinationlongitude", "first")))
    # ── leg 2: raw pickup, depot -> the building that sorts it ──────────────────────
    r = fl[(fl.flowtype == "Replenishment") & fl.productname.str.endswith("_Pickup")]
    lh = (r.groupby(["originname", "destinationname"], as_index=False)
           .agg(ea=("flowquantity", "sum"), km=("transportdistance", "first"),
                lat=("originlatitude", "first"), lon=("originlongitude", "first"),
                dlat=("destinationlatitude", "first"), dlon=("destinationlongitude", "first")))
    # ── leg 3: where chain 1 lets go ─────────────────────────────────────────────────
    c = fl[(fl.flowtype == "CustomerFulfillment")
           & fl.destinationname.str.startswith(tuple(SINKS))].copy()
    c["fam"] = c.destinationname.apply(lambda d: next(v for k, v in SINKS.items() if d.startswith(k)))
    ends = c.groupby(["originname", "fam"]).flowquantity.sum().unstack(fill_value=0)
    moved = c[(c.transportdistance > 0)].groupby(
        ["originname", "destinationname"], as_index=False).agg(
        ea=("flowquantity", "sum"), km=("transportdistance", "first"),
        lat=("originlatitude", "first"), lon=("originlongitude", "first"),
        dlat=("destinationlatitude", "first"), dlon=("destinationlongitude", "first"))
    return dict(fl=fl, sites=sites, vehicle=vehicle, stops=stops, label=label, legs=legs,
                reg=reg, lh=lh, ends=ends, moved=moved, tag_site=tag_site)


def build(d):
    legs, lh, moved = d["legs"], d["lh"], d["moved"]
    # the frame: every round and every building, padded; equirectangular at the mean latitude
    lats = pd.concat([legs.lat, legs.dlat, lh.dlat, moved.dlat])
    lons = pd.concat([legs.lon, legs.dlon, lh.dlon, moved.dlon])
    k = math.cos(math.radians(lats.mean()))
    x0, x1 = lons.min() - .03, lons.max() + .03
    y0, y1 = lats.min() - .03, lats.max() + .03
    s = W / ((x1 - x0) * k)
    H = round((y1 - y0) * s)
    def xy(lat, lon):
        return round((lon - x0) * k * s, 1), round((y1 - lat) * s, 1)

    # the land: catchment postcodes, simplified — context, not data
    poly = pd.read_csv(POLY)
    geo = []
    for g in shapely.from_wkt(poly.geometry):
        g = shapely.simplify(g, 0.001)
        for part in getattr(g, "geoms", [g]):
            pts = [xy(la, lo) for lo, la in part.exterior.coords]
            geo.append("M" + "L".join(f"{a},{b}" for a, b in pts) + "Z")

    buildings = {}
    def bld(node, lat, lon):
        if node not in buildings:
            code, name = d["label"](node)
            x, y = xy(lat, lon)
            buildings[node] = {"id": node, "code": code, "name": name, "x": x, "y": y}
    for r in legs.itertuples():
        bld(r.destinationname, r.dlat, r.dlon)
    for t in (lh, moved):
        for r in t.itertuples():
            bld(r.originname, r.lat, r.lon)
            if not r.destinationname.startswith("CZ_"):
                bld(r.destinationname, r.dlat, r.dlon)
    sink_bld = {}                                  # a CZ_ sink stands in a building: find which
    for r in moved.itertuples():
        near = min(buildings.values(), key=lambda b: math.dist((b["x"], b["y"]), xy(r.dlat, r.dlon)))
        sink_bld[r.destinationname] = near["id"]

    rounds = {}
    spokes = []
    for r in legs.itertuples():
        x, y = xy(r.lat, r.lon)
        v = d["vehicle"].get(r.site, "Red_Van")
        rd = rounds.setdefault(r.originname, {"id": r.cell, "site": r.site, "v": v, "x": x, "y": y,
                                              "ea": 0.0, "stops": int(d["stops"].get(r.cell, 0)),
                                              "to": []})
        rd["ea"] += r.ea
        rd["to"].append([r.destinationname, round(r.ea), r.km])
        spokes.append({"r": r.originname, "site": r.site, "v": v, "to": r.destinationname,
                       "ea": round(r.ea)})

    sites = {}
    for r in legs.itertuples():
        st = sites.setdefault(r.site, {"id": r.site, "name": d["label"](r.site)[1],
                                       "v": d["vehicle"].get(r.site, "Red_Van"),
                                       "ea": 0.0, "wkm": 0.0, "cost": 0.0, "rounds": set(),
                                       "to": {}})
        st["ea"] += r.ea; st["wkm"] += r.ea * r.km; st["cost"] += r.cost
        st["rounds"].add(r.originname)
        st["to"][r.destinationname] = st["to"].get(r.destinationname, 0) + r.ea
    site_rows = []
    for st in sorted(sites.values(), key=lambda s: -s["ea"]):
        site_rows.append({"id": st["id"], "name": st["name"], "v": st["v"], "ea": round(st["ea"]),
                          "rounds": len(st["rounds"]), "km": round(st["wkm"] / st["ea"], 1),
                          "cpe": round(st["cost"] / st["ea"], 3),
                          "to": sorted(([buildings[b]["code"], round(v)] for b, v in st["to"].items()),
                                       key=lambda t: -t[1])})

    arrows = [{"from": r.originname, "to": r.destinationname, "ea": round(r.ea), "km": r.km,
               "step": 2} for r in lh.itertuples()]
    arrows += [{"from": r.originname, "to": sink_bld[r.destinationname], "ea": round(r.ea),
                "km": r.km, "step": 3} for r in moved.itertuples()]

    ends = d["ends"]
    fams = [f for f in SINKS.values() if f in ends.columns]
    end_rows = [{"id": n, "code": buildings[n]["code"] if n in buildings else d["label"](n)[0],
                 "name": d["label"](n)[1],
                 **{f: round(float(ends.loc[n, f])) for f in fams}}
                for n in ends.index]
    end_rows.sort(key=lambda r: -sum(r[f] for f in fams))
    for r in end_rows:
        if r["id"] in buildings:
            buildings[r["id"]]["ends"] = {f: r[f] for f in fams}

    # CO-LOCATED BUILDINGS: MPF and Sunshine West are 0.4 km apart, so one badge hid the other.
    # A badge that lands on another is lifted clear (bx/by) and keeps a leader to its real point;
    # spokes and arrows still end at the real point (x/y).
    placed = []
    for b in sorted(buildings.values(), key=lambda b: (b["y"], b["x"])):
        b["bx"], b["by"] = b["x"], b["y"]
        while any(abs(b["bx"] - o["bx"]) < 44 and abs(b["by"] - o["by"]) < 22 for o in placed):
            b["by"] -= 24
        placed.append(b)

    reg_ea = round(d["reg"].flowquantity.sum())
    metro = round(legs.ea.sum())
    truck = round(legs[legs.site.map(d["vehicle"]).fillna("Red_Van") == "Truck"].ea.sum())
    wkm = (legs.ea * legs.km).sum() / legs.ea.sum()
    stats = {"total": metro + reg_ea, "metro": metro, "regional": reg_ea,
             "reg_at": d["label"](d["reg"].destinationname.iloc[0])[0] if len(d["reg"]) else "",
             "rounds": len(rounds), "truck": truck, "km": round(wkm, 1),
             "linehaul": round(lh.ea.sum()), "moved": round(moved.ea.sum()),
             "scenario": str(d["fl"].scenarioname.iloc[0])}
    return {"W": W, "H": H, "geo": geo, "buildings": list(buildings.values()),
            "rounds": list(rounds.values()), "rkey": list(rounds.keys()), "spokes": spokes,
            "arrows": arrows, "sites": site_rows, "ends": end_rows, "fams": fams, "stats": stats}


HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pickup Flow</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{
  --page:#f4f6f8; --surface:#ffffff; --surface-2:#fafbfc; --surface-3:#eef1f4;
  --ink:#0e1419; --ink-2:#4b555e; --muted:#7b858e;
  --line:#e2e7ec; --line-strong:#cfd6dd; --accent:#2a78d6;
  --van:#2a78d6; --truck:#eb6834; --flow:#4b555e; --other:#aab3bb;
  --land:#eef1f4; --border:#d6dce2; --shadow:0 1px 2px rgba(14,20,25,.06);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --van:#3987e5; --truck:#d95926; --flow:#b9c2ca; --other:#5a656e;
  --land:#182028; --border:#2b343c; --shadow:0 1px 2px rgba(0,0,0,.4);
}}
:root[data-theme="dark"]{
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --van:#3987e5; --truck:#d95926; --flow:#b9c2ca; --other:#5a656e;
  --land:#182028; --border:#2b343c; --shadow:0 1px 2px rgba(0,0,0,.4);
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 "IBM Plex Sans",system-ui,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:24px 24px 40px}
h1{font-size:22px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--ink-2);margin:0;max-width:92ch}
.num{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
.controls{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;margin:18px 0 12px}
.seg,.chips{display:flex;flex-wrap:wrap;gap:6px}
button{font:inherit;font-size:12.5px;color:var(--ink-2);background:var(--surface);border:1px solid var(--line-strong);
  border-radius:999px;padding:4px 11px;cursor:pointer}
button[aria-pressed="true"]{background:var(--ink);color:var(--surface);border-color:var(--ink)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.lab{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-right:4px}
.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:0 0 14px}
.stats div{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stats dt{font-size:11.5px;color:var(--muted)} .stats dd{margin:2px 0 0;font-size:19px;font-weight:600}
.stats dd small{font-size:12px;font-weight:400;color:var(--ink-2)}
.main{display:grid;grid-template-columns:minmax(0,1.75fr) minmax(0,1fr);gap:16px;align-items:start}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);min-width:0}
.panel+.panel{margin-top:16px}
.hd{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;justify-content:space-between;padding:11px 14px;border-bottom:1px solid var(--line)}
.hd h2{margin:0;font-size:13px;font-weight:600} .hd .note{font-size:11.5px;color:var(--muted)}
.step-note{padding:10px 14px 0;font-size:12.5px;color:var(--ink-2);max-width:90ch;min-height:2.6em}
svg.map{display:block;width:100%;height:auto}
.land{fill:var(--land);stroke:var(--border);stroke-width:.5}
.spoke{fill:none;stroke-width:1.1;opacity:.28}
.dot{stroke:var(--surface);stroke-width:1}
.arrow{fill:none;stroke:var(--flow);opacity:.8;stroke-linecap:round}
.hit{fill:none;stroke:transparent;stroke-width:14;cursor:pointer}
.dim{opacity:.07!important}
.badge rect{fill:var(--surface);stroke:var(--ink);stroke-width:1.2}
.badge text{font:600 11px "IBM Plex Mono",monospace;fill:var(--ink);text-anchor:middle;dominant-baseline:central}
.badge{cursor:pointer}
.legend{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;padding:10px 14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--ink-2)}
.key{display:flex;align-items:center;gap:6px}
.key i{width:10px;height:10px;border-radius:50%;display:block}
.key b{display:block;height:0;border-top:3px solid var(--flow);width:24px}
.tbl{overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
thead th{position:sticky;top:0;background:var(--surface-2);text-align:right;font-weight:500;color:var(--muted);font-size:11px;padding:7px 10px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
tbody tr.pick{cursor:pointer} tbody tr.pick:hover,tbody tr.on{background:var(--surface-3)}
.sw{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:0}
.to{color:var(--ink-2);font-size:11.5px;text-align:left;white-space:normal}
#tip{position:fixed;z-index:20;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--surface);
  border:1px solid var(--line-strong);border-radius:8px;box-shadow:0 6px 20px rgba(14,20,25,.16);padding:9px 11px;font-size:12px;max-width:300px}
#tip.on{opacity:1} #tip h4{margin:0 0 5px;font-size:12.5px;font-weight:600}
#tip dl{margin:0;display:grid;grid-template-columns:auto auto;gap:2px 14px} #tip dt{color:var(--muted)} #tip dd{margin:0;text-align:right}
footer{margin-top:22px;padding-top:14px;border-top:1px solid var(--line);color:var(--muted);font-size:12px;max-width:96ch}
footer code{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--ink-2)}
@media (max-width:980px){.main{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:520px){.wrap{padding:16px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head>"""

BODY = """<body><div class="wrap">
<header>
  <h1>Where the pickup goes</h1>
  <p class="sub">The solved collection, on the ground: each round of stops, the building it is delivered to, the linehaul to the sort, and where the collection hands the parcels over. Scenario <b id="scn"></b>, one day.</p>
</header>
<div class="controls">
  <div class="seg" role="group" aria-label="Step"><span class="lab">Step</span>
    <button data-step="1" aria-pressed="true">1 · Collect</button>
    <button data-step="2" aria-pressed="false">2 · To the sort</button>
    <button data-step="3" aria-pressed="false">3 · Hand-over</button>
    <button data-step="0" aria-pressed="false">All</button></div>
  <div class="chips" id="chips" role="group" aria-label="Collecting site"><span class="lab">Site</span></div>
</div>
<dl class="stats" id="stats"></dl>
<div class="main">
  <section class="panel">
    <div class="hd"><h2 id="maptitle">Collect</h2><span class="note">hover for detail · click a site or building to focus</span></div>
    <p class="step-note" id="stepnote"></p>
    <svg class="map" id="map" role="img" aria-label="Map of the solved pickup flow"></svg>
    <div class="legend" id="legend"></div>
  </section>
  <div>
    <section class="panel">
      <div class="hd"><h2>Collecting sites</h2><span class="note">leg 1, as solved</span></div>
      <div class="tbl"><table><thead><tr><th scope="col">Site</th><th scope="col">Rounds</th><th scope="col">EA/day</th>
        <th scope="col">km*</th><th scope="col">$/EA</th></tr></thead><tbody id="sitebody"></tbody></table></div>
    </section>
    <section class="panel">
      <div class="hd"><h2>Where it is handed over</h2><span class="note">EA/day, by the building that sorted it</span></div>
      <div class="tbl"><table><thead id="endhead"></thead><tbody id="endbody"></tbody></table></div>
    </section>
  </div>
</div>
<footer>
Read off <code>outputs/run_outputs/OptimizationFlowSummary.csv</code> by <code>plotting/pickup_flow_map.py</code>: Procurement rows from <code>SUP_PKP_*</code> for step 1, Replenishment of <code>*_Pickup</code> for step 2, Customer fulfilment to the chain-1 sinks for step 3. A round is one cluster of the first-mile routing run, drawn at its centroid; spokes are drawn curved from the centroid to the building, not along roads. *km is the leg-1 distance the model priced, weighted by volume. Colour is the leg-1 vehicle from <code>first_mile_pickup.csv</code>. Regional pickup (<span id="regfoot"></span>) is lodged at the hub and has no round to draw.
</footer>
</div>
<div id="tip" role="status" aria-live="polite"></div>"""

APP = """<script>
const D = JSON.parse(document.getElementById("data").textContent);
const NS = "http://www.w3.org/2000/svg", $ = id => document.getElementById(id);
const fmt = n => Math.round(n).toLocaleString();
const B = Object.fromEntries(D.buildings.map(b => [b.id, b]));
const R = Object.fromEntries(D.rkey.map((k, i) => [k, D.rounds[i]]));
const SITE = Object.fromEntries(D.sites.map(s => [s.id, s]));
const col = v => v === "Truck" ? "var(--truck)" : "var(--van)";
const VNAME = v => v === "Truck" ? "Truck" : "Red van";
let step = 1, focus = null;                      // focus: a site id, or "B:"+building id
const NOTES = {
  1: "Every round is a dot at the middle of its stops, sized by what the solve drew from it. Red vans take their round home to the depot; the transport trucks drive straight to a sorting building, which is why their spokes run across the map.",
  2: "Round-1 linehaul: what a depot collected and sends on unsorted, to the building that sorts it. Width is EA/day. The transports have no arrow here — their trucks already delivered to the sort in step 1.",
  3: "Where the collection entity lets go. Most of it ends in the building that sorted it (table, right). The arrows are the Vic Metro to Metro share that chain 2 collects in a different building, so it rides a truck.",
  0: "All three legs together."};

function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (parent) parent.appendChild(e); return e; }
function curve(a, b, bend) { const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, dx = b.x - a.x, dy = b.y - a.y;
  return `M${a.x},${a.y} Q${mx - dy * bend},${my + dx * bend} ${b.x},${b.y}`; }

const svg = $("map"); svg.setAttribute("viewBox", `0 0 ${D.W} ${D.H}`);
const defs = el("defs", {}, svg);
// fixed-size heads: in strokeWidth units a 14px ribbon grew a 56px triangle over the hub it points at
const mk = el("marker", {id: "ah", viewBox: "0 0 10 10", refX: 8, refY: 5, markerUnits: "userSpaceOnUse", markerWidth: 11, markerHeight: 11, orient: "auto-start-reverse"}, defs);
el("path", {d: "M0,0 L10,5 L0,10 z", fill: "var(--flow)"}, mk);
const gLand = el("g", {}, svg), gSpoke = el("g", {}, svg), gArrow = el("g", {}, svg),
      gDot = el("g", {}, svg), gLead = el("g", {}, svg), gBadge = el("g", {}, svg);
el("path", {class: "land", d: D.geo.join("")}, gLand);

const maxEA = Math.max(...D.rounds.map(r => r.ea));
const rad = ea => 2.2 + 9 * Math.sqrt(ea / maxEA);
const spokes = D.spokes.map(s => { const r = R[s.r], b = B[s.to];
  const p = el("path", {class: "spoke", d: curve(r, b, .12), stroke: col(s.v)}, gSpoke);
  return {s, p}; });
const dots = D.rkey.map(k => { const r = R[k];
  const c = el("circle", {class: "dot", cx: r.x, cy: r.y, r: rad(r.ea), fill: col(r.v)}, gDot);
  c.addEventListener("mousemove", e => tip(e, `Round ${r.id}`, [["Site", SITE[r.site].name], ["Vehicle", VNAME(r.v)],
    ["Stops in the round", fmt(r.stops)], ["Collected", fmt(r.ea) + " EA/day"],
    ...r.to.map(([b, ea, km]) => ["→ " + B[b].code, `${fmt(ea)} EA · ${km.toFixed(1)} km`])]));
  c.addEventListener("mouseleave", untip); c.addEventListener("click", () => setFocus(r.site));
  return {r, c}; });
const maxA = Math.max(...D.arrows.map(a => a.ea));
const arrows = D.arrows.map(a => { const f = B[a.from], t = B[a.to];
  const g = el("g", {}, gArrow), d = curve(f, t, .18);
  const p = el("path", {class: "arrow", d, "stroke-width": 1.5 + 13 * a.ea / maxA, "marker-end": "url(#ah)"}, g);
  const h = el("path", {class: "hit", d}, g);
  h.addEventListener("mousemove", e => tip(e, `${f.code} → ${t.code}`, [
    [a.step === 2 ? "Round-1 linehaul" : "Vic Metro to Metro hand-over", ""],
    ["Volume", fmt(a.ea) + " EA/day"], ["Distance", a.km.toFixed(1) + " km"]]));
  h.addEventListener("mouseleave", untip);
  return {a, g}; });
const badges = D.buildings.map(b => { if (b.by !== b.y) el("path", {d: `M${b.x},${b.y} L${b.bx},${b.by + 10}`, stroke: "var(--ink)", "stroke-width": 1}, gLead);
  el("circle", {cx: b.x, cy: b.y, r: 2.5, fill: "var(--ink)"}, gLead);
  const g = el("g", {class: "badge", transform: `translate(${b.bx},${b.by})`}, gBadge);
  const w = 12 + 7.2 * b.code.length; el("rect", {x: -w / 2, y: -10, width: w, height: 20, rx: 5}, g);
  el("text", {x: 0, y: 0.5}, g).textContent = b.code;
  g.addEventListener("mousemove", e => { const rows = [["", b.name]];
    const inn = D.spokes.filter(s => s.to === b.id).reduce((t, s) => t + s.ea, 0);
    if (inn) rows.push(["Collection delivered here", fmt(inn) + " EA"]);
    const lin = D.arrows.filter(a => a.step === 2 && a.to === b.id).reduce((t, a) => t + a.ea, 0);
    if (lin) rows.push(["Linehaul in", fmt(lin) + " EA"]);
    if (b.ends) for (const [f, v] of Object.entries(b.ends)) if (v) rows.push([f, fmt(v) + " EA"]);
    tip(e, b.code, rows); });
  g.addEventListener("mouseleave", untip); g.addEventListener("click", () => setFocus("B:" + b.id));
  return {b, g}; });

function tip(e, title, rows) { const t = $("tip");
  t.innerHTML = `<h4>${title}</h4><dl>` + rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("") + "</dl>";
  t.classList.add("on"); const x = Math.min(e.clientX + 14, innerWidth - t.offsetWidth - 8);
  t.style.left = x + "px"; t.style.top = Math.min(e.clientY + 14, innerHeight - t.offsetHeight - 8) + "px"; }
function untip() { $("tip").classList.remove("on"); }

function draw() {
  const on = s => step === 0 || step === s;
  const bf = focus && focus.startsWith("B:") ? focus.slice(2) : null, sf = bf ? null : focus;
  gSpoke.style.display = on(1) ? "" : "none";
  gDot.style.opacity = on(1) ? 1 : .25;
  spokes.forEach(({s, p}) => p.classList.toggle("dim", !!(sf && s.site !== sf) || !!(bf && s.to !== bf)));
  dots.forEach(({r, c}) => c.classList.toggle("dim", !!(sf && r.site !== sf) || !!(bf && !r.to.some(t => t[0] === bf))));
  arrows.forEach(({a, g}) => { g.style.display = on(a.step) ? "" : "none";
    g.classList.toggle("dim", !!(bf && a.from !== bf && a.to !== bf) || !!(sf && !(a.step === 2 && a.from === sf))); });
  $("maptitle").textContent = {1: "Collect", 2: "To the sort", 3: "Hand-over", 0: "All legs"}[step]
    + (sf ? " — " + SITE[sf].name : bf ? " — " + B[bf].name : "");
  $("stepnote").textContent = NOTES[step];
  document.querySelectorAll("[data-step]").forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.step === step)));
  document.querySelectorAll("#chips button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.site === (sf || "ALL"))));
  document.querySelectorAll("#sitebody tr").forEach(tr => tr.classList.toggle("on", tr.dataset.site === sf));
  document.querySelectorAll("#endbody tr").forEach(tr => tr.classList.toggle("on", tr.dataset.b === bf));
  legend();
}
function setFocus(f) { focus = (focus === f || f === "ALL") ? null : f; draw(); }
function legend() {
  const L = $("legend"), parts = [];
  if (step === 0 || step === 1) {
    parts.push(`<span class="key"><i style="background:var(--van)"></i>Red van round</span>`,
               `<span class="key"><i style="background:var(--truck)"></i>Truck round (transport, direct to the sort)</span>`,
               `<span class="key">dot area = EA collected · ${fmt(maxEA)} EA largest</span>`); }
  if (step !== 1) parts.push(`<span class="key"><b></b>truck between buildings · width = EA/day, ${fmt(maxA)} widest</span>`);
  parts.push(`<span class="key"><svg width="30" height="16" aria-hidden="true"><rect x="1" y="1" width="28" height="14" rx="4" fill="var(--surface)" stroke="var(--ink)"/></svg>building</span>`);
  L.innerHTML = parts.join("");
}

// ── controls, stats, tables ────────────────────────────────────────────────────
document.querySelectorAll("[data-step]").forEach(b => b.addEventListener("click", () => { step = +b.dataset.step;
  history.replaceState(null, "", "#step=" + step); draw(); }));
const chips = $("chips");
[{id: "ALL", name: "All"}, ...D.sites].forEach(s => { const b = document.createElement("button");
  b.dataset.site = s.id; b.textContent = s.id === "ALL" ? "All" : s.name.replace(" PDC", "").replace(" Facility", "");
  b.setAttribute("aria-pressed", "false"); b.addEventListener("click", () => setFocus(s.id)); chips.appendChild(b); });
const S = D.stats;
$("scn").textContent = S.scenario; $("regfoot").textContent = fmt(S.regional) + " EA/day at " + S.reg_at;
$("stats").innerHTML = [
  ["Pickup, total", fmt(S.total), "EA/day"], ["Collected in rounds", fmt(S.rounds), "rounds"],
  ["By transport truck", Math.round(100 * S.truck / S.metro) + "%", "of metro, direct to the sort"],
  ["Leg 1, average", S.km, "km, volume-weighted"], ["Linehaul to the sort", fmt(S.linehaul), "EA/day"],
  ["Handed over elsewhere", fmt(S.moved), "EA/day, leg 3c"]]
  .map(([k, v, u]) => `<div><dt>${k}</dt><dd><span class="num">${v}</span> <small>${u}</small></dd></div>`).join("");
$("sitebody").innerHTML = D.sites.map(s => `<tr class="pick" data-site="${s.id}"><td><span class="sw" style="background:${col(s.v)}"></span>${s.name}
  <div class="to">to ${s.to.map(([c, v]) => `${c} ${Math.round(100 * v / s.ea)}%`).join(" · ")}</div></td>
  <td class="num">${s.rounds}</td><td class="num">${fmt(s.ea)}</td><td class="num">${s.km}</td><td class="num">${s.cpe.toFixed(3)}</td></tr>`).join("");
document.querySelectorAll("#sitebody tr").forEach(tr => tr.addEventListener("click", () => setFocus(tr.dataset.site)));
const SHORT = {"Interstate": "Interstate", "PDO terminate": "PDO", "Vic Metro to Metro": "Metro to Metro", "Kept at depot": "Kept", "Regional": "Regional"};
$("endhead").innerHTML = "<tr><th scope='col'>Sorted at</th>" + D.fams.map(f => `<th scope="col" title="${f}">${SHORT[f] || f}</th>`).join("") + "<th scope='col'>Total</th></tr>";
const tot = D.fams.map(f => D.ends.reduce((t, r) => t + r[f], 0));
$("endbody").innerHTML = D.ends.map(r => `<tr class="pick" data-b="${r.id}" title="${r.name}"><td>${r.code}</td>`
  + D.fams.map(f => `<td class="num">${r[f] ? fmt(r[f]) : "—"}</td>`).join("")
  + `<td class="num">${fmt(D.fams.reduce((t, f) => t + r[f], 0))}</td></tr>`).join("")
  + `<tr><td><b>Total</b></td>` + tot.map(v => `<td class="num"><b>${fmt(v)}</b></td>`).join("")
  + `<td class="num"><b>${fmt(tot.reduce((a, b) => a + b, 0))}</b></td></tr>`;
document.querySelectorAll("#endbody tr.pick").forEach(tr => tr.addEventListener("click", () => setFocus("B:" + tr.dataset.b)));
const _h = /step=([0-9])/.exec(location.hash); if (_h && NOTES[+_h[1]]) step = +_h[1];
draw();
</script></body></html>"""


def main():
    d = load()
    data = build(d)
    s = data["stats"]
    # the page must hold the whole pickup: rounds + regional == what the solve procured
    procured = d["fl"][(d["fl"].flowtype == "Procurement")
                       & d["fl"].originname.str.startswith("SUP_PKP_")].flowquantity.sum()
    assert abs(s["metro"] + s["regional"] - procured) < 2, (s["metro"], s["regional"], procured)
    handed = sum(sum(r[f] for f in data["fams"]) for r in data["ends"])
    assert abs(handed - procured) < 2, f"hand-over {handed:,} vs pickup {procured:,.0f}"
    html = (HEAD + BODY + '\n<script type="application/json" id="data">'
            + json.dumps(data, separators=(",", ":")).replace("</", "<\\/") + "</script>\n" + APP)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(html) // 1024} KB)")
    print(f"  {s['rounds']} rounds, {s['metro']:,} EA metro + {s['regional']:,} regional = {s['total']:,}"
          f" | truck {s['truck']:,} ({s['truck'] / s['metro']:.0%}) | leg 1 {s['km']} km"
          f" | linehaul {s['linehaul']:,} | handed over elsewhere {s['moved']:,}")


if __name__ == "__main__":
    main()
