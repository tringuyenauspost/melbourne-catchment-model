"""Convert the Australia Post postcode shapefile to a plain CSV of WKT geometry.

The postcode sibling of `convert_state_boundaries.py`, for the delivery-catchment step
(`datastar/wip_c02_delivery_catchment.py`): DataStar does not accept .shp uploads, so this
writes one CSV carrying the polygons as WKT in EPSG:4326 and the point-in-polygon step needs
only pandas, numpy and shapely.

Only the Victorian polygons are kept (the national file is 192 MB of WKT, Victoria 27 MB) and
rounded to WKT_PRECISION decimals. Unlike the state file they are NOT simplified: postcode
boundaries run down streets, and even a 0.5 m simplification (5e-6 deg) moves 3 delivery
points into the neighbouring postcode; 11 m (the state file's tolerance) moves 46. Both cuts are verified
lossless: every distinct delivery coordinate in the last-mile extract is classified against
the ORIGINAL full-resolution NATIONAL shapefile and against the written polygons, and the
script refuses to write if any coordinate changes postcode. A delivery point that fell in a
NSW postcode would show up here as a change, not silently go missing.

    python utilities/convert_postcode_boundaries.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
SHP = ROOT / "inputs/red_vans_inputs/AP_Postcodes_SHP/AP_Postcodes.shp"
OUT = ROOT / "inputs/deilvery_events/vic_postcode_boundaries.csv"
DELIVERIES = ROOT / "inputs/deilvery_events/melbourne_pdc_last_mile_catchment.csv"

KEEP_STATES = {"VIC"}
WKT_PRECISION = 6  # ~0.1 m


def classify(geoms, names, lon, lat):
    """Postcode per point, or None. Lowest polygon index wins on a boundary."""
    tree = shapely.STRtree(geoms)
    hit_pt, hit_poly = tree.query(shapely.points(lon, lat), predicate="intersects")
    out = np.full(len(lon), None, dtype=object)
    order = np.argsort(hit_poly, kind="mergesort")[::-1]
    out[hit_pt[order]] = names[hit_poly[order]]
    return out


def main():
    import geopandas as gpd  # only needed to READ the shapefile, not downstream

    pc = gpd.read_file(SHP, engine="pyogrio").to_crs("EPSG:4326")
    pc = pc[pc.geometry.notna()].reset_index(drop=True)
    full_names = pc["POSTCODE"].to_numpy()
    full = pc.geometry.values

    keep = pc["STATE_ABBR"].isin(KEEP_STATES).to_numpy()
    names = full_names[keep]
    wkt = shapely.to_wkt(full[keep], rounding_precision=WKT_PRECISION)
    reparsed = shapely.from_wkt(wkt)

    pts = pd.read_csv(DELIVERIES, usecols=["Del_Lat", "Del_Long"]).dropna().drop_duplicates()
    lon = pts["Del_Long"].to_numpy(float)
    lat = pts["Del_Lat"].to_numpy(float)
    print(f"verifying against {len(lon):,} distinct delivery coordinates")

    before = classify(full, full_names, lon, lat)
    after = classify(reparsed, names, lon, lat)
    changed = int((before != after).sum())
    print(f"polygons {len(full):,} -> {len(names):,} ({', '.join(sorted(KEEP_STATES))}); "
          f"vertices {shapely.get_num_coordinates(full).sum():,} "
          f"-> {shapely.get_num_coordinates(reparsed).sum():,}")
    print(f"coordinates outside every postcode: {int(pd.isna(before).sum()):,}")
    if changed:
        raise SystemExit(f"REFUSING TO WRITE: {changed:,} coordinates change postcode")
    print("coordinates changing postcode: 0")

    pd.DataFrame({"POSTCODE": names, "geometry": wkt}).to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB, {len(names)} rows)")


if __name__ == "__main__":
    main()
