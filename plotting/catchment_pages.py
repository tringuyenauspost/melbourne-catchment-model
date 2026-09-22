"""Build the two first-mile catchment pages from the CSVs, so they cannot go stale.

    outputs/first-mile-catchment-map.html   the 7 catchments, coloured by measured weight
    outputs/first-mile-catchment-pct.html   how a postcode's percentage is worked out

WHY THIS EXISTS. Both pages were hand-assembled once and immediately went stale when the
weights changed — the map still claimed 395 cells after the lodgement fix took it to 402.
Everything upstream of them was a committed script and the last step was not, so the pages were
dead artefacts rather than build products. They are now generated, like the sankey pages.

WHERE THE NUMBERS COME FROM. The peak volumes and PEAK_FACTOR are read from
inputs/factors_assumed/dials_chain1.csv — the model's own source of truth — NOT hardcoded, so
a page can never quietly disagree with the build about what a site collects. The stop counts
come from utilities/catchment_visit_weights.py, imported rather than reimplemented, so the
page and the weights file can never be computed two different ways.

THE MAP'S GEOMETRY is simplified to 0.0002 degrees (~22 m) and delta-encoded as integers at
1e-4, which takes the polygons from 19 MB and 495k vertices to a ~0.25 MB payload. One geometry
per POSTCODE, not per cell: 107 postcodes are served by 2-4 sites and the polygon repeats
verbatim, which is asserted before the payload is built.

DEPOT COORDINATES come from chain 2's Facilities.csv, the same source s2b uses for its lane
distances — sites.csv leaves lat/long blank for both transport facilities.

    python plotting/catchment_pages.py
"""

import json
import math
import re
import sys
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utilities"))          # cross-folder import needs the shim
import catchment_visit_weights as cvw                # noqa: E402

TPL = Path(__file__).resolve().parent / "templates"
POLY = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"
REPORT = ROOT / "outputs/first_mile_catchment_visit_frequency.csv"
SITES = ROOT / "inputs/factors_assumed/sites.csv"
DIALS = ROOT / "inputs/factors_assumed/dials_chain1.csv"
FAC2 = ROOT / "outputs/melbourne_optilogic_chain2_observed/Facilities.csv"
OUT_MAP = ROOT / "outputs/first-mile-catchment-map.html"
OUT_PCT = ROOT / "outputs/first-mile-catchment-pct.html"

SIMPLIFY, SCALE = 0.0002, 10000
SHELL = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
         '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
         '<style>img{max-width:100%}[hidden]{display:none!important}</style>\n')
WORDS = {7: "Seven", 8: "Eight", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 23: "Twenty-three"}


def dials():
    d = pd.read_csv(DIALS).set_index("parameter")
    # keyed by NODE (PUD_Sunshine_West), which is how the dial names them; the pages key off
    # the van-arm name, so sites.csv maps between the two
    peak = {k[len("PEAK_2025_"):]: int(d.loc[k, "value"])
            for k in d.index if k.startswith("PEAK_2025_")}
    return peak, float(d.loc["PEAK_FACTOR", "value"])


def rings(geom):
    """Delta-encoded integer rings. Holes and multipolygons are kept as separate rings."""
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


def build_payload(poly, rep, sites, fac2):
    shapes = poly.groupby("post_code").geometry.nunique()
    assert (shapes == 1).all(), (
        "a postcode has more than one geometry, so one-per-postcode is not safe: "
        f"{sorted(shapes[shapes > 1].index)}")
    uniq = poly.drop_duplicates("post_code")
    simp = shapely.simplify(shapely.from_wkt(uniq.geometry.values), SIMPLIFY, preserve_topology=True)
    geo = {int(pc): rings(g) for pc, g in zip(uniq.post_code, simp)}

    kept = rep.set_index(["facility_name", "post_code"])
    cells = []
    for r in poly.itertuples():
        k = (r.facility_name, int(r.post_code))
        if k in kept.index:
            d = kept.loc[k]
            cells.append(dict(f=r.facility_name, p=int(r.post_code), st=int(d.stops),
                              ad=int(d.uniq_addr), rv=int(d.route_visits), dy=int(d.days),
                              vc=float(d.vol_coll), w=float(d.weight), eq=float(d.share_equal),
                              ea=float(d.weighted_EA), eea=float(d.equal_EA), drop=0))
        else:
            cells.append(dict(f=r.facility_name, p=int(r.post_code), drop=1, why="collects nothing"))

    site = {}
    for r in sites[sites.van_arm.notna()].itertuples():
        lat, lon = float(fac2.loc[r.node, "latitude"]), float(fac2.loc[r.node, "longitude"])
        site[r.van_arm] = dict(cluster=r.origin_cluster, display=r.display, node=r.node,
                               lat=lat, lon=lon,
                               shared=[n for n in fac2.index if n != r.node
                                       and abs(fac2.loc[n, "latitude"] - lat) < 1e-6
                                       and abs(fac2.loc[n, "longitude"] - lon) < 1e-6])
    missing = set(poly.facility_name) - set(site)
    assert not missing, f"the polygons name a site sites.csv has no van_arm for: {sorted(missing)}"
    # allow_nan=False: json.dumps writes a bare NaN, which is not valid JSON in a browser
    return json.dumps(dict(scale=SCALE, geo=geo, cells=cells, sites=site),
                      separators=(",", ":"), allow_nan=False)


def write_map(payload, rep, poly):
    body = (TPL / "map_body.html").read_text()
    for k, val in {"n_cells": f"{len(rep):,}", "n_dropped": len(poly) - len(rep)}.items():
        body = body.replace("{{" + k + "}}", str(val))
    left = re.findall(r"\{\{(\w+)\}\}", body)
    assert not left, f"the map body has placeholders nothing filled: {sorted(set(left))}"
    page = ((TPL / "map_head.html").read_text() + body
            + '<script type="application/json" id="mapdata">' + payload + "</script>\n"
            + (TPL / "map_app.js").read_text())
    OUT_MAP.write_text(SHELL + page.replace("</head>\n", "") + "\n</body>\n</html>\n"
                       if False else SHELL + "</head>\n<body>\n" + page + "\n</body>\n</html>\n")
    print(f"  {OUT_MAP.relative_to(ROOT)}  {OUT_MAP.stat().st_size / 1e6:.2f} MB")


def write_pct(rep, peak, factor, col, sites):
    f = lambda n: f"{round(n):,}"
    node = {r.van_arm: r.node for r in sites[sites.van_arm.notna()].itertuples()}
    peak = {arm: peak[n] for arm, n in node.items() if n in peak}
    site_ea = rep.groupby("facility_name").site_EA.first()
    mt, sw = "Melbourne Transport", "Sunshine West Van Services"
    mt_t, sw_t = rep[rep.facility_name == mt], rep[rep.facility_name == sw]
    mt_r = mt_t[mt_t.post_code == 3029].iloc[0]
    sw_r = sw_t[sw_t.post_code == 3000].iloc[0]
    own = col[col.own]
    _net = (~col.own & col.loc_type.eq("Network")
            & col.loc_name.astype(str).str.upper().str.contains(cvw.LODGE, regex=True, na=False))
    lodge = col[_net & ~col.facility.isin(cvw.TRANSPORTS)]
    tr_lodge = col[_net & col.facility.isin(cvw.TRANSPORTS)]
    # Evidence that the transports' EXCLUDED own-building stops are not lodgement. It has to be
    # counted on the stops the filter threw away, which are not in `col` at all — counting it on
    # col.own would silently answer about the red vans instead.
    _all = cvw.load_ccp()
    _wk = _all[_all.day.astype(str).between(*cvw.WINDOW)]
    _ex = _wk[_wk.facility.isin(cvw.TRANSPORTS) & _wk.loc_type.eq("Network")
              & _wk.booking_type.isin(cvw.COLLECTING)
              & _wk.loc_name.astype(str).str.upper().str.contains(cvw.OWN, regex=True, na=False)]
    empties = int(_ex["Booking Comments"].fillna("").str.upper()
                  .str.contains("EMPT|EQUIP|CAGE|ULD").sum())
    print(f"    transport own-building stops excluded: {len(_ex):,}, of which {empties:,} collect empties")
    rows = "".join(
        f"<tr><td>{s}</td><td>{len(t)}</td><td>{f(t.equal_EA.iloc[0])}</td>"
        f"<td>{f(t.weighted_EA.min())} – {f(t.weighted_EA.max())}</td>"
        f"<td>{100 * t.nlargest(5, 'weight').weight.sum():.1f}%</td></tr>"
        for s, t in sorted(rep.groupby("facility_name"), key=lambda kv: -kv[1].site_EA.iloc[0]))
    v = dict(
        n_sites=rep.facility_name.nunique(), n_cells=f(len(rep)), n_poly=f(len(pd.read_csv(POLY))),
        factor=f"{factor:.2f}", metro_ea=f(site_ea.sum()), all_stops=f(len(col)),
        own_stops=f(len(own)), rv_lodge=f(len(lodge)), tr_lodge=f(len(tr_lodge)),
        empties=f(empties),
        mt_cells=len(mt_t), mt_peak=f(peak[mt]), mt_ea=f(site_ea[mt]), mt_base=f(mt_t.stops.sum()),
        mt_equal=f(mt_r.equal_EA), mt_3029=f(mt_r.stops), mt_3029_addr=int(mt_r.uniq_addr),
        mt_3029_pct=f"{100 * mt_r.weight:.1f}%", mt_3029_ea=f(mt_r.weighted_EA),
        mt_3029_x=f"{mt_r.weighted_EA / mt_r.equal_EA:.1f}",
        mt_3757_ea=f(mt_t[mt_t.post_code == 3757].weighted_EA.iloc[0]),
        sw_cells=len(sw_t), sw_peak=f(peak[sw]), sw_ea=f(site_ea[sw]), sw_base=f(sw_t.stops.sum()),
        sw_equal=f(sw_r.equal_EA), sw_3000=f(sw_r.stops), sw_3000_addr=int(sw_r.uniq_addr),
        sw_3000_pct=f"{100 * sw_r.weight:.1f}%", sw_3000_ea=f(sw_r.weighted_EA),
        n_halve=int((rep.weighted_EA < rep.equal_EA / 2).sum()),
        n_double=int((rep.weighted_EA > rep.equal_EA * 2).sum()),
        n_drop_word=WORDS.get(len(pd.read_csv(POLY)) - len(rep), str(len(pd.read_csv(POLY)) - len(rep))),
        ccp_day=f(col["Volumes Collected"].sum() / 5), site_rows=rows)
    for site, short in ((mt, "r_mt"), ("Dandenong Transport Facility", "r_dt")):
        t = rep[(rep.facility_name == site) & (rep.vol_coll > 0)]
        v[short] = f"{(t.stops / t.stops.sum()).corr(t.vol_coll / t.vol_coll.sum()):.3f}"

    s = (TPL / "pct.html").read_text()
    for k, val in v.items():
        s = s.replace("{{" + k + "}}", str(val))
    left = re.findall(r"\{\{(\w+)\}\}", s)
    assert not left, f"the template has placeholders nothing filled: {sorted(set(left))}"
    OUT_PCT.write_text(SHELL + "</head>\n<body>\n" + s + "\n</body>\n</html>\n")
    print(f"  {OUT_PCT.relative_to(ROOT)}  {OUT_PCT.stat().st_size / 1e3:.0f} KB")


def main():
    peak, factor = dials()
    poly = pd.read_csv(POLY)
    rep = pd.read_csv(REPORT)
    sites = pd.read_csv(SITES)
    fac2 = pd.read_csv(FAC2).set_index("facilityname")
    col = cvw.collection_slice(cvw.load_ccp())
    print(f"{len(rep)} weighted cells over {rep.facility_name.nunique()} sites, "
          f"{len(poly)} polygons, peak factor {factor}")
    write_map(build_payload(poly, rep, sites, fac2), rep, poly)
    write_pct(rep, peak, factor, col, sites)


if __name__ == "__main__":
    main()
