"""Map every pickup point, by collecting site, over the postcode boundaries.

    outputs/pickup-points-map.html

WHAT IT SHOWS. The 6,765 physical addresses the network collects from in a Mon-Fri week, from
utilities/pickup_points.py, drawn over the postcode polygons that make up the catchments.
Point area is the number of stops in the week, so a daily customer reads heavier than a weekly
locker clear.

COLOUR IS THE VEHICLE, NOT THE SITE — deliberately. Seven sites would need seven categorical
hues, and on a scatter every pair has to be separable from every other pair, which eight-slot
palettes do not clear; past three slots the guidance is to facet or fold rather than invent
hues. So colour carries the two-value split that matters (Red_Van at the five PDC van
operations, Truck at the two transport facilities) and the SITE is carried by selection: pick
one and its points come forward while the rest fall back to a neutral wash.

THE BASE LAYER is the catchment polygons, one geometry per postcode, simplified to 0.0002
degrees. Four points sit in postcodes with no polygon (Bendigo 3555, Broadford 3658, 3164, and
the malformed 30303 at Pacific Werribee) — they are drawn, they just have no boundary under
them.

    python plotting/pickup_points_map.py
"""

import json
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
POINTS = ROOT / "outputs/pickup_points.csv"
POLY = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"
OUT = ROOT / "outputs/pickup-points-map.html"
SIMPLIFY, SCALE = 0.0002, 10000

SHELL = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
         '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
         '<style>img{max-width:100%}[hidden]{display:none!important}</style>\n')

HEAD = """<title>Where We Collect</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --page:#f4f6f8; --surface:#ffffff; --surface-2:#fafbfc; --surface-3:#eef1f4;
  --ink:#0e1419; --ink-2:#4b555e; --muted:#7b858e;
  --line:#e2e7ec; --line-strong:#cfd6dd; --accent:#2a78d6;
  --van:#2a78d6; --truck:#eb6834; --other:#aab3bb;
  --land:#f0f3f6; --border:#d6dce2;
  --ring:rgba(14,20,25,.10); --shadow:0 1px 2px rgba(14,20,25,.06);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --van:#3987e5; --truck:#d95926; --other:#5a656e;
  --land:#171d23; --border:#2b343c;
  --ring:rgba(255,255,255,.12); --shadow:0 1px 2px rgba(0,0,0,.4);
}}
:root[data-theme="dark"]{
  --page:#0a0d10; --surface:#141a1f; --surface-2:#1a2127; --surface-3:#202830;
  --ink:#ffffff; --ink-2:#b9c2ca; --muted:#828c95;
  --line:#242c34; --line-strong:#333d46; --accent:#3987e5;
  --van:#3987e5; --truck:#d95926; --other:#5a656e;
  --land:#171d23; --border:#2b343c;
  --ring:rgba(255,255,255,.12); --shadow:0 1px 2px rgba(0,0,0,.4);
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.5}
.wrap{max-width:1400px;margin:0 auto;padding-inline:20px;padding-block:22px 40px}
.num{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;gap:14px 28px;align-items:baseline;
  padding-bottom:16px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:19px;font-weight:600;letter-spacing:-.01em}
.sub{margin:2px 0 0;color:var(--ink-2);font-size:13px;max-width:72ch}
.basis{display:flex;flex-wrap:wrap;gap:6px 18px}
.basis span{color:var(--muted);font-size:12px} .basis b{color:var(--ink-2);font-weight:500}
.controls{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:flex-end;margin:18px 0 16px}
.ctl{display:flex;flex-direction:column;gap:6px}
.ctl>label{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:500}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font:inherit;font-size:12.5px;cursor:pointer;padding:5px 11px;border-radius:999px;
  border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);
  display:inline-flex;align-items:center;gap:6px;transition:background .12s,color .12s}
.chip:hover{background:var(--surface-3);color:var(--ink)}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.chip i{width:8px;height:8px;border-radius:50%;display:block}
.chip:focus-visible,tbody tr:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.toggle{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink-2);
  cursor:pointer;padding-bottom:5px}
.toggle input{accent-color:var(--accent);width:15px;height:15px;margin:0;cursor:pointer}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:16px}
.stat{background:var(--surface);padding:10px 13px}
.stat dt{margin:0;font-size:11px;color:var(--muted)}
.stat dd{margin:2px 0 0;font-size:17px;font-weight:600}
.stat dd small{font-size:11.5px;font-weight:400;color:var(--muted);
  font-family:"IBM Plex Sans",sans-serif}
.main{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);gap:16px;align-items:start}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}
.panel-hd{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;justify-content:space-between;
  padding:11px 14px;border-bottom:1px solid var(--line)}
.panel-hd h2{margin:0;font-size:13px;font-weight:600}
.panel-hd .note{font-size:11.5px;color:var(--muted)}
svg.map{display:block;width:100%;height:auto}
.pc{fill:var(--land);stroke:var(--border);stroke-width:.4px}
.pc.on{fill:color-mix(in srgb,var(--accent) 14%,var(--land))}
.pt{stroke:var(--surface);stroke-width:.5px}
.pt.dim{fill:var(--other);opacity:.5}
.legend{display:flex;flex-wrap:wrap;gap:10px 20px;align-items:center;padding:10px 14px;
  border-top:1px solid var(--line);font-size:11.5px;color:var(--ink-2)}
.key{display:flex;align-items:center;gap:6px}
.key i{width:10px;height:10px;border-radius:50%;display:block}
.sizes{display:flex;align-items:flex-end;gap:5px}
.sizes i{border-radius:50%;background:var(--muted);display:block;opacity:.6}
.tblwrap{max-height:560px;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
thead th{position:sticky;top:0;background:var(--surface-2);text-align:right;font-weight:500;
  color:var(--muted);font-size:11px;padding:7px 10px;border-bottom:1px solid var(--line);z-index:1}
thead th:first-child,tbody td:first-child{text-align:left}
tbody td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
tbody tr{cursor:pointer} tbody tr:hover,tbody tr.on{background:var(--surface-3)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;vertical-align:-1px}
#tip{position:fixed;z-index:20;pointer-events:none;opacity:0;transition:opacity .1s;
  background:var(--surface);border:1px solid var(--line-strong);border-radius:8px;
  box-shadow:0 6px 20px rgba(14,20,25,.16);padding:9px 11px;font-size:12px;max-width:290px}
#tip.on{opacity:1}
#tip h4{margin:0 0 3px;font-size:12.5px;font-weight:600}
#tip .addr{color:var(--muted);font-size:11px;margin-bottom:6px}
#tip dl{margin:0;display:grid;grid-template-columns:auto auto;gap:2px 14px}
#tip dt{color:var(--muted)} #tip dd{margin:0;text-align:right}
footer{margin-top:22px;padding-top:14px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;max-width:84ch}
footer code{font-family:"IBM Plex Mono",monospace;font-size:11.5px;color:var(--ink-2)}
@media (max-width:900px){ .main{grid-template-columns:1fr} }
@media (max-width:460px){ .wrap{padding-inline:16px} .stats{grid-template-columns:repeat(2,1fr)} }
@media (prefers-reduced-motion:reduce){ *{transition:none!important} }
</style>"""

BODY = """<div class="wrap">
<header>
  <div style="flex:1 1 420px">
    <h1>Where we collect</h1>
    <p class="sub">Every address the network picked up from in one Mon&ndash;Fri week, over the postcode boundaries that make up the catchments. Point size is how many times it was visited.</p>
  </div>
  <div class="basis">
    <span><b>Week</b> 18&ndash;22 May 2026</span>
    <span><b>Points</b> <span class="num">{{n_points}}</span></span>
    <span><b>Stops</b> <span class="num">{{n_stops}}</span></span>
  </div>
</header>
<div class="controls">
  <div class="ctl"><label id="l-site">Collecting site</label>
    <div class="chips" id="sites" role="group" aria-labelledby="l-site"></div></div>
  <label class="toggle"><input type="checkbox" id="bounds" checked> Postcode boundaries</label>
  <label class="toggle"><input type="checkbox" id="others" checked> Other sites' points</label>
</div>
<dl class="stats" id="stats"></dl>
<div class="main">
  <section class="panel">
    <div class="panel-hd"><h2 id="maptitle">All sites</h2><span class="note" id="mapnote"></span></div>
    <div id="mapholder"></div>
    <div class="legend" id="legend"></div>
  </section>
  <section class="panel">
    <div class="panel-hd"><h2>Sites</h2><span class="note">click to focus</span></div>
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">Site</th><th scope="col">Points</th><th scope="col">Stops</th>
        <th scope="col">Postcodes</th><th scope="col">Stops/point</th></tr></thead>
      <tbody id="tbody"></tbody></table></div>
  </section>
</div>
<footer>
  One row per collecting site and address, from <code>utilities/pickup_points.py</code>; coordinates are the Google geocode of the booking address. Colour is the vehicle the site collects on, read from <code>first_mile_pickup.csv</code> &mdash; seven sites would need seven hues that a scatter cannot keep apart, so the site is carried by selection instead. {{n_bad}} point is drawn at a bad geocode and excluded here (<code>in_victoria</code> in the CSV). {{n_nopoly}} points sit in postcodes with no boundary polygon.
</footer>
</div>
<div id="tip" role="status" aria-live="polite"></div>"""

APP = """<script>
const D = JSON.parse(document.getElementById("ptdata").textContent);
const SC = D.scale, NS = "http://www.w3.org/2000/svg";
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const GEO = {};
for (const [pc, rings] of Object.entries(D.geo)) {
  GEO[pc] = rings.map(r => { const p = []; let x = 0, y = 0;
    for (let i = 0; i < r.length; i += 2) { x += r[i]; y += r[i + 1]; p.push([x / SC, y / SC]); }
    return p; });
}
const SITES = D.sites.map(s => s.name);
const byNode = {}; SITES.forEach(s => byNode[s] = []);
D.points.forEach(p => byNode[p.f].push(p));
const VEH = {}; D.sites.forEach(s => VEH[s.name] = s.vehicle);
const colourOf = v => v === "Truck" ? css("--truck") : css("--van");

let site = "ALL", showBounds = true, showOthers = true, pinned = null, OFF = 0;
const $ = id => document.getElementById(id);
const fmt = n => Math.round(n).toLocaleString();

// Fit to the 1st-99th percentile, not the extremes. A handful of country stops -- Bendigo,
// Broadford -- otherwise set the frame and squeeze metro Melbourne into a corner. Points
// outside the frame are still drawn if they land on canvas, and counted in the map note.
function quantile(v, q) {
  const s = v.slice().sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.max(0, Math.round((s.length - 1) * q)))];
}
function fit(pts, W, H, pad) {
  let x0 = quantile(pts.map(p => p[0]), .01), x1 = quantile(pts.map(p => p[0]), .99);
  let y0 = quantile(pts.map(p => p[1]), .01), y1 = quantile(pts.map(p => p[1]), .99);
  if (x1 - x0 < 0.02) { const m = (x0 + x1) / 2; x0 = m - 0.01; x1 = m + 0.01; }
  if (y1 - y0 < 0.02) { const m = (y0 + y1) / 2; y0 = m - 0.01; y1 = m + 0.01; }
  const k = Math.cos((y0 + y1) / 2 * Math.PI / 180);
  const w = (x1 - x0) * k, h = y1 - y0;
  const s = Math.min((W - 2 * pad) / w, (H - 2 * pad) / h);
  const ox = (W - w * s) / 2, oy = (H - h * s) / 2;
  return (lon, lat) => [ox + (lon - x0) * k * s, oy + (y1 - lat) * s];
}
const path = (pc, P) => (GEO[pc] || []).map(r =>
  "M" + r.map(([x, y]) => { const q = P(x, y); return q[0].toFixed(1) + " " + q[1].toFixed(1); })
    .join("L") + "Z").join("");
const radius = st => 1.5 + Math.sqrt(st) * 0.62;      // area tracks the visit count

const chips = $("sites");
const mk = (label, val, veh) => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.dataset.site = val;
  b.innerHTML = (veh ? `<i style="background:${colourOf(veh)}"></i>` : "") + label;
  b.setAttribute("aria-pressed", String(val === site));
  b.onclick = () => { site = val; pinned = null; render(); };
  chips.appendChild(b);
};

const tip = $("tip");
function showTip(p, ev) {
  tip.innerHTML = `<h4>${p.n}</h4><div class="addr">${p.a}</div>
    <dl><dt>Site</dt><dd>${p.f.replace(/ Van (Operations|Services)$/, "")}</dd>
    <dt>Vehicle</dt><dd>${VEH[p.f]}</dd>
    <dt>Postcode</dt><dd class="num">${p.p}</dd>
    <dt>Stops</dt><dd class="num">${p.s}</dd>
    <dt>Days seen</dt><dd class="num">${p.d}&nbsp;/&nbsp;5</dd>
    <dt>Routes</dt><dd class="num">${p.r}</dd></dl>`;
  tip.classList.add("on");
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => tip.classList.remove("on");

function drawMap() {
  const holder = $("mapholder"); holder.innerHTML = "";
  const W = 880, H = 620;
  const all = D.points.map(p => [p.x, p.y]);
  const focus = site === "ALL" ? all : byNode[site].map(p => [p.x, p.y]);
  const P = fit(focus, W, H, 24);
  const inFrame = p => { const [a, b] = P(p.x, p.y); return a >= -20 && a <= W + 20 && b >= -20 && b <= H + 20; };
  OFF = (site === "ALL" ? D.points : byNode[site]).filter(p => !inFrame(p)).length;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "map");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", site === "ALL"
    ? `${D.points.length} pickup points across seven collecting sites`
    : `${byNode[site].length} pickup points for ${site}`);

  if (showBounds) {
    const g = document.createElementNS(NS, "g");
    const mine = site === "ALL" ? null : new Set(byNode[site].map(p => p.p));
    let h = "";
    for (const pc of Object.keys(GEO))
      h += `<path d="${path(pc, P)}" class="pc${mine && mine.has(+pc) ? " on" : ""}"/>`;
    g.innerHTML = h; svg.appendChild(g);
  }
  // context first, focus on top, so the selected site is never buried
  const draw = (list, dim) => {
    const g = document.createElementNS(NS, "g");
    for (const p of list) {
      const [cx, cy] = P(p.x, p.y);
      if (cx < -20 || cx > W + 20 || cy < -20 || cy > H + 20) continue;
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", cx.toFixed(1)); c.setAttribute("cy", cy.toFixed(1));
      c.setAttribute("r", radius(p.s).toFixed(2));
      c.setAttribute("class", "pt" + (dim ? " dim" : ""));
      if (!dim) c.setAttribute("fill", colourOf(VEH[p.f]));
      if (!dim) {
        c.addEventListener("pointerenter", e => showTip(p, e));
        c.addEventListener("pointermove", e => showTip(p, e));
        c.addEventListener("pointerleave", hideTip);
      } else { c.style.pointerEvents = "none"; }
      g.appendChild(c);
    }
    svg.appendChild(g);
  };
  if (site !== "ALL" && showOthers) draw(D.points.filter(p => p.f !== site), true);
  draw(site === "ALL" ? D.points : byNode[site], false);
  svg.addEventListener("pointerleave", hideTip);
  holder.appendChild(svg);
}

function drawTable() {
  const tb = $("tbody"); tb.innerHTML = "";
  for (const s of SITES.slice().sort((a, b) => byNode[b].length - byNode[a].length)) {
    const t = byNode[s], stops = t.reduce((a, p) => a + p.s, 0);
    const tr = document.createElement("tr");
    tr.tabIndex = 0; if (s === site) tr.className = "on";
    tr.innerHTML = `<td><span class="dot" style="background:${colourOf(VEH[s])}"></span>${
      s.replace(/ Van (Operations|Services)$/, "")}</td>
      <td class="num">${fmt(t.length)}</td><td class="num">${fmt(stops)}</td>
      <td class="num">${new Set(t.map(p => p.p)).size}</td>
      <td class="num">${(stops / t.length).toFixed(1)}</td>`;
    const go = () => { site = site === s ? "ALL" : s; render(); };
    tr.onclick = go;
    tr.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
    tb.appendChild(tr);
  }
}

function drawStats() {
  const list = site === "ALL" ? D.points : byNode[site];
  const stops = list.reduce((a, p) => a + p.s, 0);
  const daily = list.filter(p => p.d === 5).length;
  const tile = (k, v, s) => `<div class="stat"><dt>${k}</dt><dd class="num">${v}<small> ${s}</small></dd></div>`;
  $("stats").innerHTML =
    tile("Pickup points", fmt(list.length), site === "ALL" ? "addresses" : "at this site")
    + tile("Stops", fmt(stops), "in the week")
    + tile("Postcodes", new Set(list.map(p => p.p)).size, "touched")
    + tile("Every weekday", fmt(daily), `${(100 * daily / list.length).toFixed(0)}% of points`)
    + tile("Busiest", fmt(Math.max(...list.map(p => p.s))), "stops at one address");
}

function drawLegend() {
  const L = $("legend");
  L.innerHTML = `<span class="key"><i style="background:${css("--van")}"></i>Red_Van — the five PDC van operations</span>
    <span class="key"><i style="background:${css("--truck")}"></i>Truck — the two transport facilities</span>
    <span class="key sizes">${[1, 10, 40].map(s =>
      `<i style="width:${2 * radius(s)}px;height:${2 * radius(s)}px"></i>`).join("")}
      <span style="margin-left:3px">1 · 10 · 40 stops a week</span></span>`;
  if (site !== "ALL" && showOthers)
    L.innerHTML += `<span class="key"><i style="background:${css("--other")};opacity:.5"></i>other sites</span>`;
}

function render() {
  document.querySelectorAll("#sites .chip").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.site === site)));
  hideTip();
  $("maptitle").textContent = site === "ALL" ? "All seven collecting sites" : site;
  drawMap();
  const off = OFF ? ` · ${OFF} outside the frame` : "";
  $("mapnote").textContent = (site === "ALL"
    ? "colour is the vehicle; pick a site to bring it forward"
    : `${byNode[site].length.toLocaleString()} points · its catchment postcodes are tinted`) + off;
  drawTable(); drawStats(); drawLegend();
}
mk("All sites", "ALL", null);
SITES.slice().sort().forEach(s => mk(s.replace(/ Van (Operations|Services)$/, ""), s, VEH[s]));
$("bounds").onchange = e => { showBounds = e.target.checked; render(); };
$("others").onchange = e => { showOthers = e.target.checked; render(); };
render();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
</script>"""


def rings(geom):
    out = []
    for part in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        for r in [part.exterior] + list(part.interiors):
            xs, ys = r.coords.xy
            ring, px, py = [], 0, 0
            for x, y in zip(xs, ys):
                ix, iy = round(x * SCALE), round(y * SCALE)
                ring += [ix - px, iy - py]
                px, py = ix, iy
            if len(ring) >= 8:
                out.append(ring)
    return out


def main():
    pts = pd.read_csv(POINTS)
    n_bad = int((~pts.in_victoria).sum())
    pts = pts[pts.in_victoria].copy()          # a bad geocode would blow the projection open
    poly = pd.read_csv(POLY)
    uniq = poly.drop_duplicates("post_code")
    simp = shapely.simplify(shapely.from_wkt(uniq.geometry.values), SIMPLIFY, preserve_topology=True)
    geo = {int(pc): rings(g) for pc, g in zip(uniq.post_code, simp)}
    n_nopoly = int((~pts.post_code.isin(geo)).sum())

    points = [dict(f=r.facility_name, n=r.location_name, a=r.address, p=int(r.post_code),
                   s=int(r.stops), d=int(r.days_seen), r=int(r.routes),
                   x=round(float(r.longitude), 5), y=round(float(r.latitude), 5))
              for r in pts.itertuples()]
    sites = [dict(name=s, vehicle=t.vehicle_type.iloc[0])
             for s, t in pts.groupby("facility_name")]
    payload = json.dumps(dict(scale=SCALE, geo=geo, points=points, sites=sites),
                         separators=(",", ":"), allow_nan=False)

    body = BODY
    for k, v in {"n_points": f"{len(pts):,}", "n_stops": f"{pts.stops.sum():,}",
                 "n_bad": n_bad, "n_nopoly": n_nopoly}.items():
        body = body.replace("{{" + k + "}}", str(v))
    assert "{{" not in body, "a placeholder was left unfilled"
    page = (SHELL + HEAD + "\n</head>\n<body>\n" + body
            + '<script type="application/json" id="ptdata">' + payload + "</script>\n"
            + APP + "\n</body>\n</html>\n")
    OUT.write_text(page)
    print(f"{len(pts):,} points, {pts.stops.sum():,} stops, {len(geo)} postcode polygons")
    print(f"  {n_bad} excluded on a bad geocode · {n_nopoly} in postcodes with no polygon")
    print(f"wrote {OUT.relative_to(ROOT)}  {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
