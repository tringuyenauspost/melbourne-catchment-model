"""Give the two transport facilities a catchment polygon at the lodgement points they service.

WHY THIS EXISTS. The catchment polygons come from
notebooks/first-mile-red-vans-and-facility-transport-catchment-extraction-270726.ipynb, whose
transport branch filters to `Customer & Pickup`. That drops the post offices those trucks clear
on their country runs, so the polygon set has no cell for them and a weight computed for one
would have nowhere to land. This adds the missing (facility, post_code) rows.

WHAT IT ADDS. Seven cells, all of them real lodgement the transports collect on a Mon-Fri week:

    Melbourne Transport           3066 Abbotsford LPO · 3434 Romsey LPO · 3435 Lancefield LPO
                                  3437 Gisborne RP · 3442 Woodend LPO
    Dandenong Transport Facility  3781 Cockatoo LPO · 3783 Gembrook LPO

The geometry is copied from the same postcode at another site. That is safe and checked below:
every one of the 273 postcodes in the file has ONE geometry, repeated verbatim wherever it
appears, so a postcode's polygon does not depend on which site collects it.

IT IS IDEMPOTENT — run it twice and the second run writes nothing.

**RE-RUN IT AFTER utilities/convert_catchment_geojson.py.** That script regenerates the polygon
CSV from the GeoJSON and would drop these rows again. The permanent fix is to widen the
notebook's transport filter and re-cut the GeoJSON; until then this is the patch.

    python utilities/add_transport_lodgement_catchments.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POLY = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"

ADD = {
    "Melbourne Transport": [3066, 3434, 3435, 3437, 3442],
    "Dandenong Transport Facility": [3781, 3783],
}


def main():
    p = pd.read_csv(POLY)
    n0 = len(p)
    shapes = p.groupby("post_code").geometry.nunique()
    assert (shapes == 1).all(), (
        "a postcode has more than one geometry in the polygon file, so copying it between "
        f"sites is not safe: {sorted(shapes[shapes > 1].index)}")
    geom = p.drop_duplicates("post_code").set_index("post_code").geometry

    rows, already = [], []
    have = set(zip(p.facility_name, p.post_code))
    for site, pcs in ADD.items():
        for pc in pcs:
            if (site, pc) in have:
                already.append((site, pc))
                continue
            assert pc in geom.index, (
                f"postcode {pc} is not in the polygon file at any site, so there is no geometry "
                "to copy — it has to come from a re-cut of the GeoJSON")
            rows.append({"facility_name": site, "post_code": pc, "geometry": geom[pc]})

    if already:
        print(f"  already present, left alone: {already}")
    if not rows:
        print(f"nothing to add — {POLY.name} already has all {sum(len(v) for v in ADD.values())} cells")
        return p

    out = pd.concat([p, pd.DataFrame(rows)], ignore_index=True)
    out = out.sort_values(["facility_name", "post_code"], ignore_index=True)
    out.to_csv(POLY, index=False)
    print(f"{POLY.relative_to(ROOT)}: {n0} -> {len(out)} rows (+{len(rows)})")
    for r in rows:
        print(f"    + {r['facility_name']:30s} {r['post_code']}")
    return out


if __name__ == "__main__":
    main()
