"""The nine reconciliation tables as a page.  -- ASKED vs SOLVED, one section per promise --

    IN   outputs/melbourne_optilogic_final/*.csv   what we asked for
         outputs/run_outputs/*.csv                 what NEO sent back
    OUT  outputs/reconcile-run.html

Not a second implementation: it imports `utilities/reconcile_run.py` and renders the Table objects
that script already builds, so the page and the console print the same numbers by construction.
Re-run it after any solve and the page is the new solve.

Run:  uv run python plotting/reconcile_page.py
      uv run python plotting/reconcile_page.py --run outputs/some_other_run
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))
import reconcile_run as R                                                # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "outputs", "reconcile-run.html")


def paragraphs(doc):
    """A docstring is written as prose: blank lines separate paragraphs and `*` starts a bullet
    that may wrap over several source lines. Flattening all of it into one line loses both."""
    out, buf, in_bullet = [], [], False
    def flush():
        nonlocal buf, in_bullet
        if buf:
            out.append(("* " if in_bullet else "") + " ".join(buf))
        buf, in_bullet = [], False
    for ln in (doc or "").strip().splitlines():
        ln = ln.strip()
        if not ln:
            flush()
        elif ln.startswith("*"):
            flush()
            in_bullet = True
            buf.append(ln.lstrip("* "))
        else:
            buf.append(ln)
    flush()
    return out


def collect(M):
    """Run every table and keep the wide row set where one exists."""
    out = []
    for i, fn in enumerate(R.TABLES, 1):
        t = fn(M)
        cols = getattr(t, "cols_detail", None) or t.cols
        body = getattr(t, "detail", None) or t.body
        name, _, _ = t.title.partition(" — ")
        out.append({
            "n": t.n, "name": name, "sub": t.title.partition(" — ")[2],
            "note": paragraphs(t.note),
            "cols": cols, "rows": [[str(c) for c in r] for r in body],
            "lines": t.lines, "ok": t.ok, "verdict": t.verdict,
        })
    return out


HTML = r"""<title>Asked vs Solved</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  color-scheme: light;
  --ground:#eef2f4; --surface:#ffffff; --surface-2:#f6f9fa; --rule:#d6e0e4; --rule-soft:#e6edf0;
  --ink:#0d1519; --ink-2:#43545c; --muted:#6d8089;
  --steel:#17607f; --steel-soft:rgba(23,96,127,.09); --steel-line:rgba(23,96,127,.28);
  --good:#0ca30c; --good-ink:#0a6f0a; --good-bg:rgba(12,163,12,.10);
  --warn:#fab219; --warn-ink:#8a6100; --warn-bg:rgba(250,178,25,.16);
  --crit:#d03b3b; --crit-ink:#a52424; --crit-bg:rgba(208,59,59,.11);
  --sans:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --cond:"IBM Plex Sans Condensed","IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  color-scheme: dark;
  --ground:#090f12; --surface:#121b20; --surface-2:#172227; --rule:#223239; --rule-soft:#1b272d;
  --ink:#e7eff2; --ink-2:#a7bbc3; --muted:#7d929b;
  --steel:#5fb3d6; --steel-soft:rgba(95,179,214,.12); --steel-line:rgba(95,179,214,.34);
  --good:#0ca30c; --good-ink:#5cc95c; --good-bg:rgba(12,163,12,.16);
  --warn:#fab219; --warn-ink:#f0ae23; --warn-bg:rgba(250,178,25,.14);
  --crit:#d03b3b; --crit-ink:#ef7676; --crit-bg:rgba(208,59,59,.16);
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --ground:#090f12; --surface:#121b20; --surface-2:#172227; --rule:#223239; --rule-soft:#1b272d;
  --ink:#e7eff2; --ink-2:#a7bbc3; --muted:#7d929b;
  --steel:#5fb3d6; --steel-soft:rgba(95,179,214,.12); --steel-line:rgba(95,179,214,.34);
  --good:#0ca30c; --good-ink:#5cc95c; --good-bg:rgba(12,163,12,.16);
  --warn:#fab219; --warn-ink:#f0ae23; --warn-bg:rgba(250,178,25,.14);
  --crit:#d03b3b; --crit-ink:#ef7676; --crit-bg:rgba(208,59,59,.16);
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);
     font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding-inline:20px;padding-block:0 64px}
@media(max-width:520px){.wrap{padding-inline:16px}}

/* ---- masthead ------------------------------------------------------------- */
header.top{padding-block:44px 28px;border-bottom:2px solid var(--ink)}
.eyebrow{font-family:var(--cond);font-size:12px;font-weight:600;letter-spacing:.14em;
         text-transform:uppercase;color:var(--steel);margin:0 0 10px}
h1{font-family:var(--cond);font-weight:700;font-size:clamp(38px,7vw,60px);line-height:.98;
   letter-spacing:-.015em;margin:0;text-wrap:balance}
.lede{margin:14px 0 0;max-width:62ch;color:var(--ink-2);font-size:16.5px}
.ident{margin-top:26px;display:grid;gap:0 26px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
       border-top:1px solid var(--rule);padding-top:14px}
.ident div{padding-block:7px;border-bottom:1px solid var(--rule-soft)}
.ident dt{font-family:var(--cond);font-size:11px;font-weight:600;letter-spacing:.1em;
          text-transform:uppercase;color:var(--muted)}
.ident dd{margin:2px 0 0;font-family:var(--mono);font-size:12.5px;color:var(--ink-2);
          word-break:break-word}

/* ---- headline figures ----------------------------------------------------- */
.heads{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1px;
       background:var(--rule);border:1px solid var(--rule);margin-top:32px}
.head{background:var(--surface);padding:20px 22px}
.head .k{font-family:var(--cond);font-size:11.5px;font-weight:600;letter-spacing:.1em;
         text-transform:uppercase;color:var(--muted)}
.head .v{font-family:var(--cond);font-weight:700;font-size:42px;line-height:1.05;margin-top:6px;
         letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.head .v small{font-size:17px;font-weight:600;color:var(--muted);letter-spacing:0}
.head .c{margin-top:5px;font-size:13.5px;color:var(--ink-2);line-height:1.4}
.head.ok .v{color:var(--good-ink)} .head.pin .v{color:var(--warn-ink)}

/* ---- verdict strip -------------------------------------------------------- */
.strip{margin-top:30px;display:flex;flex-wrap:wrap;gap:7px}
.chip{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--rule);
      background:var(--surface);padding:5px 11px 5px 7px;text-decoration:none;color:var(--ink);
      font-size:13px;transition:border-color .12s}
.chip:hover{border-color:var(--steel)}
.chip .no{font-family:var(--mono);font-size:11px;font-weight:600;color:var(--muted);
          background:var(--surface-2);border:1px solid var(--rule-soft);padding:1px 5px}
.chip .dot{width:8px;height:8px;border-radius:50%}
.chip.p .dot{background:var(--good)} .chip.f .dot{background:var(--crit)}
.chip b{font-weight:500;font-family:var(--cond);letter-spacing:.01em}

/* ---- sections ------------------------------------------------------------- */
section{padding-block:40px 8px;border-bottom:1px solid var(--rule);scroll-margin-top:16px}
section:last-of-type{border-bottom:0}
.shead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.shead .n{font-family:var(--mono);font-size:12px;font-weight:600;color:var(--steel);
          border:1px solid var(--steel-line);padding:2px 7px;background:var(--steel-soft)}
h2{font-family:var(--cond);font-weight:600;font-size:26px;letter-spacing:-.01em;margin:0;
   text-wrap:balance}
.sub{font-size:14px;color:var(--muted);font-family:var(--cond);letter-spacing:.01em}
.note{margin:12px 0 0;max-width:74ch;color:var(--ink-2);font-size:14.5px}
.note.bullet{padding-left:18px;position:relative}
.note.bullet:before{content:"";position:absolute;left:2px;top:11px;width:8px;height:1px;
                    background:var(--steel)}
.note+.note{margin-top:9px}
.note code{font-family:var(--mono);font-size:12.5px;background:var(--surface-2);
           border:1px solid var(--rule-soft);padding:.5px 4px}
.verdict{margin-top:18px;display:flex;gap:10px;align-items:flex-start;border-left:3px solid var(--good);
         background:var(--good-bg);padding:10px 14px;font-size:14px}
.verdict.f{border-left-color:var(--crit);background:var(--crit-bg)}
.verdict .lab{font-family:var(--cond);font-weight:700;letter-spacing:.08em;font-size:12px;
              color:var(--good-ink);padding-top:2px}
.verdict.f .lab{color:var(--crit-ink)}
.lines{margin:16px 0 0;padding:0;list-style:none;font-size:13.5px;color:var(--ink-2)}
.lines li{padding:3px 0 3px 16px;position:relative}
.lines li:before{content:"";position:absolute;left:0;top:12px;width:7px;height:1px;background:var(--muted)}
.lines li.warn{color:var(--warn-ink);font-weight:500}
.lines li.warn:before{background:var(--warn)}
.lines li.mono{font-family:var(--mono);font-size:12px;white-space:pre-wrap;
               overflow-x:auto;color:var(--muted)}

/* ---- tables --------------------------------------------------------------- */
.tw{margin-top:18px;border:1px solid var(--rule);background:var(--surface);overflow:auto}
.tw.tall{max-height:min(70vh,620px)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
thead th{position:sticky;top:0;z-index:2;background:var(--surface-2);text-align:right;
         font-family:var(--cond);font-weight:600;font-size:11.5px;letter-spacing:.08em;
         text-transform:uppercase;color:var(--muted);padding:9px 12px;white-space:nowrap;
         border-bottom:1px solid var(--rule)}
thead th:first-child{text-align:left}
tbody td{padding:7px 12px;text-align:right;border-bottom:1px solid var(--rule-soft);
         font-variant-numeric:tabular-nums;white-space:nowrap}
tbody td:first-child{text-align:left;font-family:var(--mono);font-size:12px;color:var(--ink)}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--steel-soft)}
tbody tr.total td{font-weight:600;border-top:1px solid var(--rule);background:var(--surface-2)}
td.num{font-family:var(--mono);font-size:12.5px}
td.txt{text-align:left;font-family:var(--mono);font-size:12px;color:var(--ink-2)}
td.wrap{white-space:normal;text-align:left;color:var(--muted);font-size:12.5px;min-width:210px}

/* state pills: colour never carries the meaning alone -- the word is always there */
.pill{display:inline-flex;align-items:center;gap:5px;font-family:var(--cond);font-size:11.5px;
      font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:2px 8px;
      border:1px solid transparent;white-space:nowrap}
.pill:before{content:"";width:6px;height:6px;flex:none}
.pill.bind{color:var(--warn-ink);background:var(--warn-bg);border-color:var(--warn)}
.pill.bind:before{background:var(--warn)}
.pill.slack{color:var(--ink-2);background:var(--surface-2);border-color:var(--rule)}
.pill.slack:before{background:var(--muted);border-radius:50%}
.pill.bad{color:var(--crit-ink);background:var(--crit-bg);border-color:var(--crit)}
.pill.bad:before{background:var(--crit);clip-path:polygon(50% 0,100% 100%,0 100%)}
.pill.ok{color:var(--good-ink);background:var(--good-bg);border-color:transparent}
.pill.ok:before{background:var(--good);border-radius:50%}
.pill.off{color:var(--muted);background:transparent;border-color:var(--rule)}
.pill.off:before{background:var(--muted);opacity:.5}

/* utilisation meter: one hue, magnitude; at-cap switches to the warning step */
.meter{display:inline-flex;align-items:center;gap:8px;justify-content:flex-end}
.meter .track{width:78px;height:7px;background:var(--rule-soft);border:1px solid var(--rule);
              position:relative;flex:none}
.meter .fill{position:absolute;inset:0 auto 0 0;background:var(--steel);border-radius:0 2px 2px 0}
.meter.cap .fill{background:var(--warn)}
.meter .pc{font-family:var(--mono);font-size:12px;min-width:44px;text-align:right}

/* the slack rail -- a constraint at zero slack is drawn flush to its bound */
.rail{display:inline-flex;align-items:center;gap:8px;justify-content:flex-end}
.rail .bar{width:86px;height:7px;position:relative;flex:none;background:var(--rule-soft);
           border:1px solid var(--rule)}
.rail .sol{position:absolute;top:0;bottom:0;left:0;background:var(--steel)}
.rail .gap{position:absolute;top:0;bottom:0;background:var(--warn-bg);
           border-left:1px solid var(--warn)}
.rail .v{font-family:var(--mono);font-size:12px;min-width:64px;text-align:right}

/* ---- filters -------------------------------------------------------------- */
.controls{margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.controls label{font-family:var(--cond);font-size:11.5px;font-weight:600;letter-spacing:.08em;
                text-transform:uppercase;color:var(--muted)}
input[type=search],select{font-family:var(--sans);font-size:13.5px;color:var(--ink);
  background:var(--surface);border:1px solid var(--rule);padding:6px 9px;min-width:0}
input[type=search]{flex:1 1 220px;max-width:340px;font-family:var(--mono);font-size:12.5px}
input:focus-visible,select:focus-visible,.chip:focus-visible{outline:2px solid var(--steel);
  outline-offset:2px}
.count{font-family:var(--mono);font-size:12.5px;color:var(--muted);margin-left:auto}
.stats{margin-top:16px;display:flex;flex-wrap:wrap;gap:1px;background:var(--rule);
       border:1px solid var(--rule)}
.stats div{background:var(--surface);padding:11px 16px;flex:1 1 150px}
.stats .k{font-family:var(--cond);font-size:11px;font-weight:600;letter-spacing:.09em;
          text-transform:uppercase;color:var(--muted)}
.stats .v{font-family:var(--mono);font-size:21px;font-weight:600;margin-top:3px;
          font-variant-numeric:tabular-nums}
footer{padding-block:34px 0;color:var(--muted);font-size:13px;border-top:2px solid var(--ink);
       margin-top:44px}
footer code{font-family:var(--mono);font-size:12px;color:var(--ink-2)}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}
</style>

<div class="wrap">
<header class="top">
  <p class="eyebrow">Optilogic NEO &middot; model reconciliation</p>
  <h1>Asked vs Solved</h1>
  <p class="lede">Every promise the uploaded model makes, recomputed against the solve NEO
     returned. One table per promise, an ASKED column off the model folder and a SOLVED column
     off the run &mdash; nothing copied from the Sankey page, so agreeing with it is evidence
     rather than restatement.</p>
  <dl class="ident" id="ident"></dl>
  <div class="heads" id="heads"></div>
  <nav class="strip" id="strip"></nav>
</header>
<main id="main"></main>
<footer>
  <p>Generated by <code>plotting/reconcile_page.py</code>, which renders the Table objects built by
     <code>utilities/reconcile_run.py</code> &mdash; the console run and this page cannot disagree.
     Quantities are EA (articles) for one period. Re-run either after the next solve.</p>
</footer>
</div>

<script>
const D = __DATA__;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const numish = s => /^-?[\d,]+(\.\d+)?$/.test(String(s).trim());
const val = s => { const t=String(s).replace(/,/g,''); return numish(s) ? parseFloat(t) : NaN; };

/* ---- identity ------------------------------------------------------------- */
document.getElementById('ident').innerHTML = D.ident
  .map(([k,v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');

/* ---- headline figures ----------------------------------------------------- */
document.getElementById('heads').innerHTML = D.heads.map(h =>
  `<div class="head ${h.cls||''}"><div class="k">${esc(h.k)}</div>
   <div class="v">${h.v}</div><div class="c">${h.c}</div></div>`).join('');

/* ---- verdict strip -------------------------------------------------------- */
document.getElementById('strip').innerHTML = D.tables.map(t =>
  `<a class="chip ${t.ok?'p':'f'}" href="#t${t.n}"><span class="no">${t.n}</span>
   <span class="dot"></span><b>${esc(t.name)}</b></a>`).join('');

/* ---- cell rendering ------------------------------------------------------- */
const STATE = {
  'binding':'bind', 'slack':'slack', 'VIOLATED':'bad', 'BROKEN':'bad', 'INERT':'bad',
  'AT CAP':'bind', 'OVER CAP':'bad', 'UNDER MIN':'bad', 'ok':'ok',
  'unused':'off', 'NOT IN RUN':'off'
};
function cell(v, col, row, t){
  const c = col.toLowerCase();
  if (c === 'state') {
    const k = STATE[v] || 'slack';
    return `<td><span class="pill ${k}">${esc(v)}</span></td>`;
  }
  if (/%$/.test(String(v).trim())) {
    const p = parseFloat(String(v));
    if (isNaN(p)) return `<td class="num">${esc(v)}</td>`;
    const cap = p >= 99.5;
    return `<td><span class="meter ${cap?'cap':''}"><span class="track">
      <span class="fill" style="width:${Math.min(100,p)}%"></span></span>
      <span class="pc">${esc(v)}</span></span></td>`;
  }
  if (c === 'notes' || c === 'terms') return `<td class="wrap">${esc(v)}</td>`;
  return numish(v) ? `<td class="num">${esc(v)}</td>` : `<td class="txt">${esc(v)}</td>`;
}

/* the slack rail, for the two constraint tables: solved drawn against its bound */
function rail(asked, solved){
  const a = val(asked), s = val(solved);
  if (isNaN(a) || isNaN(s)) return `<td class="num">${esc(solved)}</td>`;
  const span = Math.max(Math.abs(a), Math.abs(s)) || 1;
  const sw = Math.min(100, Math.abs(s) / span * 100);
  const gap = Math.min(100, Math.abs(a - s) / span * 100);
  const flush = gap < 0.5;
  return `<td><span class="rail"><span class="bar">
    <span class="sol" style="width:${sw}%"></span>
    ${flush ? '' : `<span class="gap" style="left:${sw}%;width:${gap}%"></span>`}
    </span><span class="v">${esc(solved)}</span></span></td>`;
}

function table(t, rows, tall){
  const ci = t.cols.map(c => c.toLowerCase());
  const iAsk = t.n === 4 ? ci.indexOf('asked') : -1;
  const iSol = t.n === 4 ? ci.indexOf('solved') : -1;
  const head = t.cols.map(c => `<th>${esc(c)}</th>`).join('');
  const body = rows.map(r => {
    const total = String(r[0]).trim() === 'TOTAL';
    const tds = r.map((v, i) => (i === iSol && iAsk >= 0) ? rail(r[iAsk], v)
                                                          : cell(v, t.cols[i], r, t)).join('');
    return `<tr class="${total?'total':''}">${tds}</tr>`;
  }).join('');
  return `<div class="tw ${tall?'tall':''}"><table><thead><tr>${head}</tr></thead>
          <tbody>${body}</tbody></table></div>`;
}

/* ---- sections ------------------------------------------------------------- */
const main = document.getElementById('main');
main.innerHTML = D.tables.map(t => {
  const tall = t.rows.length > 24;
  const notes = t.lines.filter(l => l.trim()).map(l =>
    `<li class="${l.startsWith('!!') ? 'warn' : (/^ {4}/.test(l) ? 'mono' : '')}">${esc(l.replace(/^!!\s*/,''))}</li>`
  ).join('');
  return `<section id="t${t.n}">
    <div class="shead"><span class="n">${t.n}</span><h2>${esc(t.name)}</h2>
      <span class="sub">${esc(t.sub)}</span></div>
    ${t.note.map(p => p.startsWith('* ')
        ? `<p class="note bullet">${esc(p.slice(2))}</p>`
        : `<p class="note">${esc(p)}</p>`).join('')}
    ${t.n === 4 ? '<div class="stats" id="fcstats"></div><div class="controls" id="fcctl"></div>' : ''}
    <div id="body${t.n}">${table(t, t.rows, tall)}</div>
    <div class="verdict ${t.ok?'':'f'}"><span class="lab">${t.ok?'PASS':'FAIL'}</span>
      <span>${esc(t.verdict)}</span></div>
    ${notes ? `<ul class="lines">${notes}</ul>` : ''}
  </section>`;
}).join('');

/* ---- table 4 is 1,000 rows, so it gets a filter --------------------------- */
const T4 = D.tables.find(t => t.n === 4);
if (T4) {
  const ci = T4.cols.map(c => c.toLowerCase());
  const iState = ci.indexOf('state'), iType = ci.indexOf('type');
  const counts = {};
  T4.rows.forEach(r => { const k = r[iType] + ' ' + r[iState]; counts[k] = (counts[k]||0)+1; });
  document.getElementById('fcstats').innerHTML = Object.keys(counts).sort()
    .map(k => `<div><div class="k">${esc(k)}</div><div class="v">${counts[k].toLocaleString()}</div></div>`)
    .join('') + `<div><div class="k">rows</div><div class="v">${T4.rows.length.toLocaleString()}</div></div>`;
  document.getElementById('fcctl').innerHTML =
    `<label for="fcq">find</label>
     <input type="search" id="fcq" placeholder="lane, product or note">
     <label for="fcs">state</label>
     <select id="fcs"><option value="">all</option>${
       [...new Set(T4.rows.map(r=>r[iState]))].sort().map(s=>`<option>${esc(s)}</option>`).join('')}</select>
     <label for="fct">type</label>
     <select id="fct"><option value="">all</option>${
       [...new Set(T4.rows.map(r=>r[iType]))].sort().map(s=>`<option>${esc(s)}</option>`).join('')}</select>
     <span class="count" id="fcn"></span>`;
  const q = document.getElementById('fcq'), s = document.getElementById('fcs'),
        ty = document.getElementById('fct'), n = document.getElementById('fcn'),
        host = document.getElementById('body4');
  const draw = () => {
    const needle = q.value.trim().toLowerCase();
    const rows = T4.rows.filter(r =>
      (!s.value || r[iState] === s.value) && (!ty.value || r[iType] === ty.value) &&
      (!needle || r.join(' ').toLowerCase().includes(needle)));
    host.innerHTML = table(T4, rows, true);
    n.textContent = `${rows.length.toLocaleString()} of ${T4.rows.length.toLocaleString()} rows`;
  };
  [q, s, ty].forEach(el => el.addEventListener('input', draw));
  draw();
}
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.path.join(HERE, "outputs", "melbourne_optilogic_final"))
    ap.add_argument("--run", default=os.path.join(HERE, "outputs", "run_outputs"))
    ap.add_argument("--scenario")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    M = R.Model(a.model, a.run)
    if a.scenario:
        M.filter_scenario(a.scenario)
    tables = collect(M)

    # the three figures the page exists to put in front of someone
    bal = next(t for t in tables if t["n"] == 1)
    ful = sum(float(str(r[2]).replace(",", ""))
              for r in bal["rows"] if r[0] == "CustomerFulfillment")
    fc = next(t for t in tables if t["n"] == 4)
    st = fc["cols"].index("state")
    bind = sum(1 for r in fc["rows"] if r[st] == "binding")
    passes = sum(1 for t in tables if t["ok"])

    data = {
        "ident": [
            ["model uploaded", os.path.relpath(a.model, HERE)],
            ["run downloaded", os.path.relpath(a.run, HERE)],
            ["scenario", M.scenario],
            ["rows read", f"{len(M.r['OptimizationFlowSummary']):,} flow / "
                          f"{len(M.r['OptimizationProcessSummary']):,} process / "
                          f"{len(M.r['OptimizationProductionSummary']):,} production / "
                          f"{len(M.r['OptimizationWorkCenterSummary']):,} work centre"],
        ],
        "heads": [
            {"k": "tables passing", "v": f"{passes}<small> / {len(tables)}</small>",
             "cls": "ok" if passes == len(tables) else "",
             "c": "every promise the model makes, checked against the solve"
                  if passes == len(tables) else "see the failing sections below"},
            {"k": "parcels in and out", "v": f"{ful:,.0f}<small> EA</small>",
             "c": "procurement equals fulfilment to the parcel; no process in this model "
                  "loses or makes one"},
            {"k": "constraints binding", "v": f"{bind:,}<small> / {len(fc['rows']):,}</small>",
             "cls": "pin",
             "c": "the solve is sitting on a number we chose, not one it found &mdash; "
                  "this model is pinned far more than it is optimised"},
        ],
        "tables": tables,
    }
    html = HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {a.out}  ({os.path.getsize(a.out) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
