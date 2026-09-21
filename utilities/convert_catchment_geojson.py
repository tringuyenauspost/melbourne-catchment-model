"""Convert the first-mile catchment GeoJSON to a plain CSV of WKT geometry.

Optilogic DataStar has no geopandas, and s2b_build_chain1.py used it for exactly two things:
reading this file, and taking each polygon's centroid. Both are shapely operations underneath
— geopandas was carrying GDAL and pyogrio along for the ride — so the polygons move to a CSV
here and the build reads them with pandas + shapely, the same way s1a already reads the state
boundaries and the first-mile catchment.

WHAT IT CHECKS BEFORE IT WRITES. The centroid is the whole point of the file: every catchment
supplier in chain 1 stands at one. So this re-reads the GeoJSON through geopandas, takes the
centroids geopandas would have produced, and compares them to the centroids shapely gets from
the WKT it is about to write. A single coordinate off by more than TOLERANCE and it refuses to
write, because a silently moved supplier is a silently changed lane distance.

FULL PRECISION, DELIBERATELY. shapely's to_wkt() rounds to six decimals by default. Six
decimals is ~0.1 m and would almost certainly be harmless, but "almost certainly" is not a
thing to assume about the coordinates the whole collection entity hangs off, and the file is
small enough at full precision that there is nothing to buy by rounding.

    python utilities/convert_catchment_geojson.py
"""

import json
from pathlib import Path

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
GEOJSON = ROOT / "inputs/melbourne/first_mile_manifest_catchment_include_transport_facility.geojson"
OUT = ROOT / "inputs/melbourne/first_mile_catchment_polygons.csv"

KEEP = ["facility_name", "post_code"]      # the only properties the build reads
TOLERANCE = 1e-12                          # degrees; a centroid may not move at all


def main():
    raw = json.loads(GEOJSON.read_text())
    feats = raw["features"] if isinstance(raw, dict) else raw
    print(f"{GEOJSON.name}: {len(feats):,} features, {GEOJSON.stat().st_size / 1e6:.1f} MB")

    rows = []
    for f in feats:
        props = f["properties"]
        missing = [k for k in KEEP if k not in props]
        if missing:
            raise SystemExit(f"a feature is missing {missing}; it has {sorted(props)}")
        geom = shapely.geometry.shape(f["geometry"])
        rows.append({**{k: props[k] for k in KEEP},
                     "geometry": shapely.to_wkt(geom, rounding_precision=-1)})
    out = pd.DataFrame(rows)

    # ---- the check: would a supplier move? ----
    import geopandas as gpd            # only this script needs it, and only to verify
    import warnings
    gj = gpd.read_file(GEOJSON)
    with warnings.catch_warnings():    # "centroid in a geographic CRS" — immaterial, see s2b
        warnings.simplefilter("ignore")
        want = gj.geometry.centroid
    got = shapely.centroid(shapely.from_wkt(out.geometry))
    dx = (shapely.get_x(got) - want.x.to_numpy())
    dy = (shapely.get_y(got) - want.y.to_numpy())
    worst = max(abs(dx).max(), abs(dy).max())
    print(f"centroid check: worst move {worst:.3e} degrees over {len(out):,} polygons")
    if worst > TOLERANCE:
        raise SystemExit(f"REFUSING TO WRITE — a centroid moves by {worst:.3e} degrees, more "
                         f"than {TOLERANCE:.0e}. Every catchment supplier sits on one of these.")
    if list(gj.facility_name) != list(out.facility_name):
        raise SystemExit("REFUSING TO WRITE — feature order differs from geopandas'")

    out.to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)}  {len(out):,} rows, {OUT.stat().st_size / 1e6:.1f} MB")
    print(f"  sites: {out.facility_name.nunique()}   postcodes: {out.post_code.nunique()}")


if __name__ == "__main__":
    main()
