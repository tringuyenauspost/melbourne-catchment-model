"""Australia Post postcode boundaries -> one small GeoJSON for the browser maps.

AP_Postcodes.shp is 135 MB of Web Mercator polygons, far too heavy to drop into a web page.
This simplifies every outline (in metres, before reprojecting), moves it to WGS84 lat/lon,
keeps only POSTCODE and STATE, and rounds coordinates to 5 decimals (~1 m). At the default
50 m tolerance the whole country is ~16 MB and still reads cleanly at suburb zoom.

FOR DISPLAY ONLY. The outlines are simplified, so never use this file for point-in-polygon
assignment; the pipeline's postcode WKT stays unsimplified for that reason.

    python utilities/convert_postcode_boundaries.py
    python utilities/convert_postcode_boundaries.py --tolerance 25 --states VIC
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import shapely

ROOT = Path(__file__).resolve().parents[1]
SHP = ROOT / "inputs/AP_Postcodes_SHP/AP_Postcodes.shp"
OUT = ROOT / "inputs/AP_Postcodes_SHP/AP_Postcodes_simplified.geojson"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shp", type=Path, default=SHP)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--tolerance", type=float, default=50, help="simplification, metres")
    ap.add_argument("--states", nargs="+", help="keep only these STATE_ABBR values, e.g. VIC QLD")
    a = ap.parse_args()

    g = gpd.read_file(a.shp)
    if a.states:
        g = g[g["STATE_ABBR"].isin(a.states)]
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    # simplify in the source projection's metres, then reproject
    g["geometry"] = g.geometry.simplify(a.tolerance, preserve_topology=True)
    g = g.to_crs(4326)
    g["geometry"] = shapely.set_precision(g.geometry.values, 1e-5)

    features = [{"type": "Feature",
                 "properties": {"POSTCODE": str(r.POSTCODE).strip(), "STATE": r.STATE_ABBR},
                 "geometry": shapely.geometry.mapping(r.geometry)}
                for r in g.itertuples() if not r.geometry.is_empty]
    a.out.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                separators=(",", ":")))
    print(f"{len(features):,} postcodes -> {a.out} ({a.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
