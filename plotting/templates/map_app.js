<script>
const D = JSON.parse(document.getElementById("mapdata").textContent);
const SC = D.scale, SEQ = 8, NS = "http://www.w3.org/2000/svg";
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ── decode delta-encoded rings once ─────────────────────────────────────── */
const GEO = {};
for (const [pc, rings] of Object.entries(D.geo)) {
  GEO[pc] = rings.map(r => {
    const pts = []; let x = 0, y = 0;
    for (let i = 0; i < r.length; i += 2) { x += r[i]; y += r[i + 1]; pts.push([x / SC, y / SC]); }
    return pts;
  });
}
const SITES = Object.keys(D.sites).sort();
const byNode = {}; SITES.forEach(s => byNode[s] = []);
D.cells.forEach(c => byNode[c.f].push(c));
SITES.forEach(s => byNode[s].sort((a, b) => (b.w || -1) - (a.w || -1)));

/* ── per-site derived numbers ────────────────────────────────────────────── */
// one basis: stop-events. A container basis was tried and rejected — see
// utilities/transport_duty_link.py for why it must not come back.
const wOf = c => c.w;

function buildAgg() {
 const A = {};
 for (const s of SITES) {
  const kept = byNode[s].filter(c => !c.drop);
  const tot = kept[0].ea / kept[0].w;
  kept.forEach(c => c._ea = wOf(c) * tot);
  const ea = kept.map(c => c._ea).sort((a, b) => a - b);
  const byW = kept.slice().sort((x, y) => wOf(y) - wOf(x));
  A[s] = {
    cells: kept.length, dropped: byNode[s].length - kept.length,
    siteEA: Math.round(tot), equalEA: kept[0].eea,
    maxW: Math.max(...kept.map(wOf)),
    medEA: ea[Math.floor(ea.length / 2)], minEA: ea[0], maxEA: ea[ea.length - 1],
    top5: byW.slice(0, 5).reduce((a, c) => a + wOf(c), 0),
    stops: kept.reduce((a, c) => a + c.st, 0)
  };
 }
 return A;
}
let AGG = buildAgg();

/* ── projection ──────────────────────────────────────────────────────────── */
function fit(pcs, W, H, pad) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const pc of pcs) for (const r of (GEO[pc] || [])) for (const [x, y] of r) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  const k = Math.cos((y0 + y1) / 2 * Math.PI / 180);            // equirectangular
  const w = (x1 - x0) * k, h = y1 - y0;
  const s = Math.min((W - 2 * pad) / w, (H - 2 * pad) / h);
  const ox = (W - w * s) / 2, oy = (H - h * s) / 2;
  return (lon, lat) => [ox + (lon - x0) * k * s, oy + (y1 - lat) * s];
}
const d = (pc, P) => (GEO[pc] || []).map(r =>
  "M" + r.map(([x, y]) => { const p = P(x, y); return p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("L") + "Z"
).join("");

/* ── colour ──────────────────────────────────────────────────────────────── */
const seqRamp = () => Array.from({ length: SEQ }, (_, i) => css("--seq-" + i));
const divRamp = () => ["--div-n3","--div-n2","--div-n1","--div-0","--div-p1","--div-p2","--div-p3"].map(css);
function colourOf(c, mode, maxW) {
  if (c.drop) return "transparent";
  if (mode === "share") {
    const t = maxW > 0 ? wOf(c) / maxW : 0;
    // sqrt so the long tail of small postcodes stays distinguishable
    return seqRamp()[Math.min(SEQ - 1, Math.floor(Math.sqrt(t) * SEQ))];
  }
  const lg = Math.log2(wOf(c) / c.eq), R = divRamp();
  return lg < -2 ? R[0] : lg < -1 ? R[1] : lg < -0.35 ? R[2]
       : lg <= 0.35 ? R[3] : lg <= 1 ? R[4] : lg <= 2 ? R[5] : R[6];
}

/* ── state ───────────────────────────────────────────────────────────────── */
let site = "Melbourne Transport", mode = "share", showDrop = false, pinned = null;

const $ = id => document.getElementById(id);
const fmt = n => Math.round(n).toLocaleString();
const pct = n => (n * 100).toFixed(n < 0.01 ? 2 : 1) + "%";

/* ── controls ────────────────────────────────────────────────────────────── */
const chips = $("sites");
const mk = (label, val) => {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.textContent = label; b.dataset.site = val;
  b.setAttribute("aria-pressed", String(val === site));
  b.onclick = () => { site = val; pinned = null; render(); };
  chips.appendChild(b);
};
mk("All sites", "ALL");
SITES.forEach(s => mk(D.sites[s].display.replace(/ (PDC|Facility)$/, ""), s));
$("modes").onclick = e => {
  const b = e.target.closest("[data-mode]"); if (!b) return;
  mode = b.dataset.mode; render();
};
$("showdrop").onchange = e => { showDrop = e.target.checked; render(); };

/* ── tooltip ─────────────────────────────────────────────────────────────── */
const tip = $("tip");
function showTip(c, ev) {
  const a = AGG[c.f];
  if (c.drop) {
    tip.innerHTML = `<h4><span class="num">${c.p}</span><span class="pill">dropped</span></h4>
      <div class="sup num">no supplier built</div>
      <dl><dt>Reason</dt><dd>${c.why}</dd></dl>`;
  } else {
    const r = c.w / c.eq, up = r >= 1;
    tip.innerHTML = `<h4><span class="num">${c.p}</span><span class="num ${up ? "up" : "dn"}">${r.toFixed(2)}×</span></h4>
      <div class="sup num">SUP_PKP_${D.sites[c.f].cluster}_${c.p}</div>
      <dl>
        <dt>Stop-events</dt><dd class="num">${fmt(c.st)}</dd>
        <dt>Addresses</dt><dd class="num">${fmt(c.ad)}</dd>
        <dt>Run visits</dt><dd class="num">${fmt(c.rv)}</dd>
        <dt>Days seen</dt><dd class="num">${c.dy}&nbsp;/&nbsp;5</dd>
      </dl><div class="hr"></div><dl>
        <dt>Share of site</dt><dd class="num">${pct(wOf(c))}</dd>
        <dt>Equal split</dt><dd class="num">${pct(c.eq)}</dd>
        <dt>EA weighted</dt><dd class="num">${fmt(c._ea)}</dd>
        <dt>EA if equal</dt><dd class="num">${fmt(c.eea)}</dd>
      </dl>${c.vc > 0 ? `<div class="hr"></div><dl><dt>CCP recorded</dt>
        <dd class="num">${fmt(c.vc)} EA</dd></dl>` : ""}`;
  }
  tip.classList.add("on");
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tip.style.left = Math.max(8, x) + "px"; tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => tip.classList.remove("on");

function highlight(pc) {
  document.querySelectorAll(".cell.on,tbody tr.on").forEach(n => n.classList.remove("on"));
  if (pc == null) return;
  document.querySelectorAll(`.cell[data-pc="${pc}"],tbody tr[data-pc="${pc}"]`)
    .forEach(n => n.classList.add("on"));
}

/* ── the map ─────────────────────────────────────────────────────────────── */
function drawMap() {
  const holder = $("mapholder"); holder.innerHTML = "";
  const cells = byNode[site].filter(c => !c.drop || showDrop);
  const W = 860, H = 560;
  const P = fit(cells.map(c => c.p), W, H, 26);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "map");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label",
    `Catchment of ${D.sites[site].display}: ${AGG[site].cells} postcodes coloured by ${mode === "share" ? "share of site volume" : "change against the equal split"}.`);
  svg.innerHTML = `<defs><pattern id="hx" width="6" height="6" patternUnits="userSpaceOnUse"
      patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6"
      stroke="${css("--hatch")}" stroke-width="1.6" opacity=".5"/></pattern></defs>`;

  // context: every other site's catchment, as a hairline
  const mine = new Set(cells.map(c => c.p));
  const ctx = new Set();
  D.cells.forEach(c => { if (!mine.has(c.p)) ctx.add(c.p); });
  const gc = document.createElementNS(NS, "g");
  ctx.forEach(pc => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d(pc, P)); p.setAttribute("class", "ctx"); gc.appendChild(p);
  });
  svg.appendChild(gc);

  const maxW = AGG[site].maxW;
  const g = document.createElementNS(NS, "g");
  for (const c of cells) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d(c.p, P));
    p.setAttribute("fill", c.drop ? "url(#hx)" : colourOf(c, mode, maxW));
    p.setAttribute("stroke", css("--surface"));
    p.setAttribute("stroke-width", c.drop ? "1" : ".8");
    if (c.drop) p.setAttribute("stroke-dasharray", "3 2");
    p.setAttribute("class", "cell"); p.dataset.pc = c.p;
    p.addEventListener("pointerenter", e => { highlight(c.p); showTip(c, e); });
    p.addEventListener("pointermove", e => showTip(c, e));
    p.addEventListener("pointerleave", () => { if (pinned !== c.p) { hideTip(); highlight(pinned); } });
    p.addEventListener("click", () => {
      pinned = pinned === c.p ? null : c.p; highlight(pinned);
      const row = document.querySelector(`tbody tr[data-pc="${c.p}"]`);
      if (row) row.scrollIntoView({ block: "nearest" });
    });
    g.appendChild(p);
  }
  svg.appendChild(g);

  // the depot itself — a ringed badge, so it reads as a place, not a data mark
  const s = D.sites[site], [mx, my] = P(s.lon, s.lat);
  if (mx > -40 && mx < W + 40 && my > -40 && my < H + 40) {
    const mg = document.createElementNS(NS, "g");
    mg.innerHTML =
      `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="7.5" class="mk-ring"/>` +
      `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="3" class="mk-dot"/>` +
      `<text x="${mx.toFixed(1)}" y="${(my - 12).toFixed(1)}" text-anchor="middle" class="mk-lab">${s.cluster}</text>`;
    svg.appendChild(mg);
  }
  svg.addEventListener("pointerleave", () => { if (pinned == null) hideTip(); });
  holder.appendChild(svg);
}

/* ── small multiples ─────────────────────────────────────────────────────── */
function drawSmall() {
  const holder = $("mapholder"); holder.innerHTML = "";
  const box = document.createElement("div"); box.className = "sms";
  for (const s of SITES.slice().sort((a, b) => AGG[b].siteEA - AGG[a].siteEA)) {
    const cells = byNode[s].filter(c => !c.drop);
    const W = 300, H = 210, P = fit(cells.map(c => c.p), W, H, 10), maxW = AGG[s].maxW;
    const btn = document.createElement("button");
    btn.className = "sm"; btn.type = "button";
    btn.innerHTML = `<h3>${D.sites[s].display}</h3><p class="num">${AGG[s].cells} postcodes · ${fmt(AGG[s].siteEA)} EA</p>`;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${D.sites[s].display}, ${AGG[s].cells} postcodes`);
    let h = "";
    for (const c of cells)
      h += `<path d="${d(c.p, P)}" fill="${colourOf(c, mode, maxW)}" stroke="${css("--surface-2")}" stroke-width=".6"/>`;
    const [mx, my] = P(D.sites[s].lon, D.sites[s].lat);
    h += `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="4.5" class="mk-ring"/>` +
         `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="1.8" class="mk-dot"/>`;
    svg.innerHTML = h; btn.appendChild(svg);
    btn.onclick = () => { site = s; pinned = null; render(); scrollTo({ top: 0, behavior: "smooth" }); };
    box.appendChild(btn);
  }
  holder.appendChild(box);
}

/* ── table ───────────────────────────────────────────────────────────────── */
function drawTable() {
  const tb = $("tbody"); tb.innerHTML = "";
  if (site === "ALL") {
    $("tbltitle").textContent = "Sites by volume";
    $("tblnote").textContent = "equal split vs measured";
    for (const s of SITES.slice().sort((a, b) => AGG[b].siteEA - AGG[a].siteEA)) {
      const a = AGG[s], tr = document.createElement("tr");
      tr.innerHTML = `<td>${D.sites[s].display}</td><td class="num">${fmt(a.stops)}</td>
        <td class="num">${a.cells}</td><td class="num">${pct(a.top5)}</td>
        <td class="num">${fmt(a.maxEA)}</td><td class="num">${(a.maxEA / a.equalEA).toFixed(1)}×</td>`;
      tr.onclick = () => { site = s; pinned = null; render(); };
      tb.appendChild(tr);
    }
    return;
  }
  $("tbltitle").textContent = "Postcodes by weight";
  $("tblnote").textContent = `${AGG[site].cells} weighted${showDrop && AGG[site].dropped ? ` · ${AGG[site].dropped} dropped` : ""}`;
  const maxW = AGG[site].maxW;
  const ordered = byNode[site].slice().sort((a, b) =>
    (a.drop ? 1 : 0) - (b.drop ? 1 : 0) || wOf(b) - wOf(a));
  for (const c of ordered) {
    if (c.drop && !showDrop) continue;
    const tr = document.createElement("tr"); tr.dataset.pc = c.p; tr.tabIndex = 0;
    tr.className = "row" + (c.drop ? " dropped" : "");
    if (c.drop) {
      tr.innerHTML = `<td class="num">${c.p}</td><td colspan="4" style="text-align:left">
        <span class="pill">dropped — ${c.why}</span></td><td class="num">—</td>`;
    } else {
      const r = wOf(c) / c.eq;
      tr.innerHTML = `<td class="num">${c.p}</td><td class="num">${fmt(c.st)}</td>
        <td class="num">${fmt(c.ad)}</td>
        <td class="bar"><i style="width:${(wOf(c) / maxW * 100).toFixed(1)}%"></i><span class="num">${pct(wOf(c))}</span></td>
        <td class="num">${fmt(c._ea)}</td>
        <td class="num ${r >= 1 ? "up" : "dn"}">${r.toFixed(2)}×</td>`;
    }
    const enter = e => { highlight(c.p); showTip(c, e.touches ? e.touches[0] : e); };
    tr.addEventListener("pointerenter", enter);
    tr.addEventListener("focus", e => { highlight(c.p); const b = tr.getBoundingClientRect(); showTip(c, { clientX: b.left, clientY: b.bottom }); });
    tr.addEventListener("pointerleave", () => { if (pinned !== c.p) { hideTip(); highlight(pinned); } });
    tr.addEventListener("blur", hideTip);
    tr.addEventListener("click", () => { pinned = pinned === c.p ? null : c.p; highlight(pinned); });
    tb.appendChild(tr);
  }
}

/* ── stats + legend ──────────────────────────────────────────────────────── */
function drawStats() {
  const el = $("stats");
  if (site === "ALL") {
    const t = SITES.reduce((a, s) => a + AGG[s].siteEA, 0);
    const cells = SITES.reduce((a, s) => a + AGG[s].cells, 0);
    el.innerHTML = tile("Metro pickup", fmt(t), "EA/day, pinned")
      + tile("Catchments", cells, "postcode cells")
      + tile("Dropped", SITES.reduce((a, s) => a + AGG[s].dropped, 0), "collect nothing")
      + tile("Stop-events", fmt(SITES.reduce((a, s) => a + AGG[s].stops, 0)), "Mon–Fri")
      + tile("Sites", SITES.length, "collecting");
    return;
  }
  const a = AGG[site];
  el.innerHTML = tile("Site volume", fmt(a.siteEA), "EA/day, unchanged")
    + tile("Equal split gave", fmt(a.equalEA), "EA to every postcode")
    + tile("Measured range", fmt(a.minEA) + "–" + fmt(a.maxEA), `EA · ${(a.maxEA / Math.max(a.minEA, 1)).toFixed(0)}× spread`)
    + tile("Median", fmt(a.medEA), "EA per postcode")
    + tile("Top 5 take", pct(a.top5), `equal would be ${pct(5 / a.cells)}`);
}
const tile = (k, v, s) => `<div class="stat"><dt>${k}</dt><dd class="num">${v}<small> ${s}</small></dd></div>`;

function drawLegend() {
  const L = $("legend");
  if (mode === "share") {
    L.innerHTML = `<div class="ramp"><span class="cap">Less</span>
      <span class="sw">${seqRamp().map(c => `<i style="background:${c}"></i>`).join("")}</span>
      <span class="cap">More</span><span>share of the site's volume${site === "ALL" ? " (each site to its own maximum)" : ""}</span></div>`;
  } else {
    const R = divRamp();
    L.innerHTML = `<div class="ramp"><span class="cap num">¼×</span>
      <span class="sw">${R.map(c => `<i style="background:${c}"></i>`).join("")}</span>
      <span class="cap num">4×</span><span>against the equal split · grey = within ±25%</span></div>`;
  }
  if (showDrop) L.innerHTML += `<span class="lk"><i style="background:var(--surface);
    border-style:dashed"></i>dropped — collects nothing</span>`;
  L.innerHTML += `<span class="lk"><svg width="15" height="15" viewBox="0 0 15 15"><circle cx="7.5" cy="7.5" r="5" class="mk-ring"/><circle cx="7.5" cy="7.5" r="2" class="mk-dot"/></svg>the depot</span>`;
}

/* ── render ──────────────────────────────────────────────────────────────── */
function render() {
  document.querySelectorAll("#sites .chip").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.site === site)));
  document.querySelectorAll("#modes .chip").forEach(b =>
    b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  hideTip();
  if (site === "ALL") {
    $("maptitle").textContent = "All seven collection sites";
    $("mapnote").textContent = "each to its own scale — pick one to compare postcodes";
    drawSmall();
  } else {
    $("maptitle").textContent = D.sites[site].display;
    const sh = D.sites[site].shared;
    $("mapnote").textContent = `${AGG[site].cells} postcodes · hairlines are the other sites' catchments`
      + (sh.length ? ` · depot shares a site with ${sh[0].replace(/^(HUB|PUD)_/, "").replace(/_/g, " ")}` : "");
    drawMap();
  }
  drawTable(); drawStats(); drawLegend();
}
render();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
</script>
