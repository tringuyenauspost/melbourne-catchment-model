"""Convert the first-mile geo inputs to plain CSV for Optilogic DataStar.

DataStar does not accept .geojson (and reading one needs geopandas + pyogrio). The
METRO/REGION lodgement band needs two of them, so this writes:

  first_mile_catchment.csv    one row: the dissolved catchment boundary as WKT, EPSG:4326
  first_mile_route_stops.csv  10,606 pickup points, name / address / lon / lat only

`sites.csv` is already a CSV and is uploaded as-is.

Verification: reclassifies every distinct lodgement facility in the scan-clean extract
through the real lodgement_geography() and through the CSVs, and refuses to write if a
single facility changes its metro/region/unplaced answer.

    python utilities/convert_first_mile_geo.py
"""

from pathlib import Path
import sys

import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

RAW = ROOT / "inputs/melbourne"
CATCHMENT_IN = RAW / "first_mile_catchment_dissolved_all.geojson"
STOPS_IN = RAW / "first_mile_route_stops.geojson"
CATCHMENT_OUT = RAW / "first_mile_catchment.csv"
STOPS_OUT = RAW / "first_mile_route_stops.csv"
SCAN = RAW / "all_scan_for_melbourne_pdc_20052026.csv"

STOP_COLS = ["location_name", "location_address", "google_longitude", "google_latitude"]
WKT_PRECISION = 6  # ~0.1 m


def main():
    import geopandas as gpd
    import s1a_export_chain2_factors as s1a

    # ---- convert ----
    boundary = gpd.read_file(CATCHMENT_IN, engine="pyogrio").to_crs("EPSG:4326")
    union = boundary.geometry.union_all()
    wkt = shapely.to_wkt(union, rounding_precision=WKT_PRECISION)
    stops = gpd.read_file(STOPS_IN, engine="pyogrio").to_crs("EPSG:4326")[STOP_COLS]

    # ---- verify against the real implementation, on the real lodgement names ----
    scans = pd.read_csv(SCAN, usecols=["Event_type", "Event_facility_name"], low_memory=False)
    names = pd.Series(sorted(scans.loc[scans.Event_type == "ZPT_LODGE",
                                       "Event_facility_name"].dropna().unique()))
    print(f"verifying {len(names):,} distinct lodgement facilities")

    before = s1a.lodgement_geography(names)

    # same call, but with the boundary and stops re-read from the CSVs
    reparsed = shapely.from_wkt(wkt)
    orig_read_file = gpd.read_file

    def patched(path, *a, **k):
        if Path(path) == STOPS_IN:
            df = pd.read_csv(STOPS_OUT)
            return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(
                df.google_longitude, df.google_latitude), crs="EPSG:4326")
        if Path(path) == CATCHMENT_IN:
            return gpd.GeoDataFrame(geometry=[reparsed], crs="EPSG:4326")
        return orig_read_file(path, *a, **k)

    stops.to_csv(STOPS_OUT, index=False)
    gpd.read_file = patched
    try:
        after = s1a.lodgement_geography(names)
    finally:
        gpd.read_file = orig_read_file

    changed = 0
    for col in ("lodge_metro", "lodge_geo_from"):
        b, a = before[col], after[col]
        diff = int((b.isna() != a.isna()).sum() + (b.notna() & a.notna() & (b != a)).sum())
        print(f"  {col}: {diff} facilities differ")
        changed += diff
    if changed:
        STOPS_OUT.unlink(missing_ok=True)
        raise SystemExit(f"REFUSING TO WRITE: {changed} classification changes")

    pd.DataFrame([{"name": "first_mile_catchment", "geometry": wkt}]).to_csv(
        CATCHMENT_OUT, index=False)
    for p in (CATCHMENT_OUT, STOPS_OUT):
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size / 1e6:.1f} MB)")
    print(f"catchment vertices: {shapely.get_num_coordinates(union):,}")


if __name__ == "__main__":
    main()
