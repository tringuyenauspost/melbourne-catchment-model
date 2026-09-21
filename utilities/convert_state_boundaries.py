"""Convert the ABS state/territory shapefile to a plain CSV of WKT geometry.

Optilogic DataStar does not accept .shp uploads (and reading one needs geopandas +
pyogrio + pyproj). This writes a single CSV carrying the same polygons as WKT, already
reprojected to EPSG:4326, so the downstream point-in-polygon step needs only pandas,
numpy and shapely.

The geometry is simplified to SIMPLIFY_TOLERANCE and rounded to WKT_PRECISION decimals.
Both are verified lossless for this pipeline: the script re-runs the point-in-polygon
classification for every distinct coordinate in the scan extract against the original
full-resolution shapefile and refuses to write if any coordinate changes state.

    python utilities/convert_state_boundaries.py
"""

from pathlib import Path

import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
SHP = ROOT / "inputs/melbourne/STE_2021_AUST_SHP_GDA2020/STE_2021_AUST_GDA2020.shp"
OUT = ROOT / "inputs/melbourne/aus_state_boundaries.csv"
SCANS = ROOT / "inputs/melbourne/melbourne-delivery-volume-all-scan-events.csv"

SIMPLIFY_TOLERANCE = 0.0001  # ~11 m in degrees
WKT_PRECISION = 6  # ~0.1 m


def classify(geoms, names, lon, lat):
    """State name per point, or None. First match wins on a border."""
    tree = shapely.STRtree(geoms)
    pts = shapely.points(lon, lat)
    hit_pt, hit_poly = tree.query(pts, predicate="intersects")
    out = np.full(len(lon), None, dtype=object)
    # assign in reverse so the lowest polygon index wins for a border point
    order = np.argsort(hit_poly, kind="mergesort")[::-1]
    out[hit_pt[order]] = names[hit_poly[order]]
    return out


def main():
    import geopandas as gpd  # only needed to READ the shapefile, not downstream

    states = gpd.read_file(SHP, engine="pyogrio").to_crs("EPSG:4326")
    states = states[states.geometry.notna()].reset_index(drop=True)
    names = states["STE_NAME21"].to_numpy()
    full = states.geometry.values

    simplified = shapely.simplify(full, SIMPLIFY_TOLERANCE)
    wkt = shapely.to_wkt(simplified, rounding_precision=WKT_PRECISION)
    reparsed = shapely.from_wkt(wkt)

    scans = pd.read_csv(SCANS, usecols=["Event_Lat", "Event_Long"], low_memory=False)
    uniq = scans.drop_duplicates().dropna()
    lon = uniq["Event_Long"].to_numpy(float)
    lat = uniq["Event_Lat"].to_numpy(float)
    print(f"verifying against {len(lon):,} distinct scan coordinates")

    before = classify(full, names, lon, lat)
    after = classify(reparsed, names, lon, lat)
    changed = int((before != after).sum())
    print(f"vertices {shapely.get_num_coordinates(full).sum():,} "
          f"-> {shapely.get_num_coordinates(reparsed).sum():,}")
    if changed:
        raise SystemExit(f"REFUSING TO WRITE: {changed:,} coordinates change state")
    print("coordinates changing state: 0")

    pd.DataFrame({"STE_NAME21": names, "geometry": wkt}).to_csv(OUT, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.1f} MB, {len(names)} rows)")


if __name__ == "__main__":
    main()
