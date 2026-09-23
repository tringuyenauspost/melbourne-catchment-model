"""Every pickup point the model collects from, with its coordinate and the vehicle that calls.

One row per (collection site, address): where the freight is picked up, how often, and on what.
This is the stop-level detail behind the postcode weights — the weights aggregate these rows to
a postcode, so the two always agree on which stops count.

THE VEHICLE IS NOT HARDCODED. It comes from the model's own
inputs/factors_assumed/first_mile_pickup.csv, which lists the sites that deviate from
PICKUP_MODE_DEFAULT in dials_chain1.csv. Today that means the two transport facilities collect
on `Truck` and the five PDC van operations fall back to `Red_Van` — but if ops re-equip a site,
this follows the model rather than disagreeing with it.

THE COORDINATE comes from inputs/red_vans_inputs/geocoding_result.csv, joined on the address
string exactly as the catchment notebook joins it. The file has 369 duplicate addresses, so it
is de-duplicated first — they are repeats, not competing geocodes.

ONE GEOCODE IS WRONG AND IS KEPT, FLAGGED. "SANDHURST SHOPPING CENTRE ... CRANBOURNE WEST 3977
VIC" geocodes to (-26.11, 28.04), which is Johannesburg. `in_victoria` marks it False rather
than dropping the row, because the stop is real and only its coordinate is bad; anything
mapping these points should filter on that column.

    python utilities/pickup_points.py
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utilities"))
import catchment_visit_weights as cvw                # noqa: E402

GEOCODE = ROOT / "inputs/red_vans_inputs/geocoding_result.csv"
PICKUP = ROOT / "inputs/factors_assumed/first_mile_pickup.csv"
DIALS = ROOT / "inputs/factors_assumed/dials_chain1.csv"
SITES = ROOT / "inputs/factors_assumed/sites.csv"
OUT = ROOT / "outputs/pickup_points.csv"          # --day writes a dated sibling

# a generous Victoria box — it is a sanity flag on the geocoder, not a catchment test
VIC = dict(lat=(-39.2, -33.9), lon=(140.9, 150.1))


def vehicle_by_site():
    """site van-arm name -> the mode its collection runs on, from the model's own files."""
    default = pd.read_csv(DIALS).set_index("parameter").loc["PICKUP_MODE_DEFAULT", "value"]
    per_site = {r.site: r.mode for r in pd.read_csv(PICKUP).itertuples()}
    sites = pd.read_csv(SITES)
    out = {r.van_arm: per_site.get(r.node, default)
           for r in sites[sites.van_arm.notna()].itertuples()}
    print(f"  vehicle from first_mile_pickup.csv, default {default}:")
    for mode in sorted(set(out.values())):
        print(f"    {mode:10s} {', '.join(sorted(s for s, m in out.items() if m == mode))}")
    return out


def collection_stops(day=None):
    """The stops that count as a collection, on the same rule the weights use — imported, so
    the extract and the weights can never drift into two different definitions.

    `day` narrows to a single date. `stops` counts JOB ROWS either way, so an address called at
    twice in a day counts twice — the duplicates are the point, not noise to be folded away."""
    col = cvw.collection_slice(cvw.load_ccp())
    col = col[~col.own].copy()
    if day:
        n0 = len(col)
        col = col[col.day.astype(str).eq(day)]
        assert len(col), f"no collection stops on {day} — the window is {cvw.WINDOW}"
        print(f"  {day} only: {len(col):,} of {n0:,} stops in the week")
    return col


def main(day=None):
    out = (OUT if not day else OUT.with_name(f"{OUT.stem}_{day}{OUT.suffix}"))
    print(f"building the pickup-point extract{' for ' + day if day else ''}")
    veh = vehicle_by_site()
    col = collection_stops(day)

    geo = (pd.read_csv(GEOCODE, usecols=["Location Address", "google_latitude",
                                         "google_longitude", "suburb"])
             .drop_duplicates("Location Address"))
    pts = col.groupby(["facility", "addr"], as_index=False).agg(
        location_name=("loc_name", "first"), location_type=("loc_type", "first"),
        post_code=("post_code", "first"), stops=("addr", "size"),
        days_seen=("day", "nunique"), routes=("route", "nunique"))
    n0 = len(pts)
    pts = pts.merge(geo, left_on="addr", right_on="Location Address", how="left")
    assert len(pts) == n0, "the geocode join duplicated rows — de-duplicate the geocode file"

    pts["vehicle_type"] = pts.facility.map(veh)
    assert pts.vehicle_type.notna().all(), (
        f"no vehicle for {sorted(set(pts.loc[pts.vehicle_type.isna(), 'facility']))} — "
        "sites.csv has no van_arm for it")
    pts["in_victoria"] = (pts.google_latitude.between(*VIC["lat"])
                          & pts.google_longitude.between(*VIC["lon"]))

    pts = pts.rename(columns={"facility": "facility_name", "addr": "address",
                              "google_latitude": "latitude", "google_longitude": "longitude"})
    cols = ["facility_name", "vehicle_type", "location_name", "location_type", "address",
            "suburb", "post_code", "latitude", "longitude", "in_victoria",
            "stops", "days_seen", "routes"]
    pts = pts[cols].sort_values(["facility_name", "stops"], ascending=[True, False])
    pts.to_csv(out, index=False)

    rep = (day if day else "Mon-Fri")
    print(f"\n  {len(pts):,} pickup points over {pts.facility_name.nunique()} sites, "
          f"{pts.stops.sum():,} stops {rep}")
    rev = pts[pts.stops > 1]
    print(f"  addresses called at more than once: {len(rev):,} "
          f"({rev.stops.sum() - len(rev):,} stops above one visit each)")
    print(f"  geocoded {int(pts.latitude.notna().sum()):,} "
          f"({100 * pts.latitude.notna().mean():.1f}%)   "
          f"outside Victoria (bad geocode) {int((~pts.in_victoria).sum())}")
    print()
    print(pts.groupby(["vehicle_type", "facility_name"]).agg(
        points=("address", "size"), stops=("stops", "sum"),
        postcodes=("post_code", "nunique")).to_string())
    bad = pts[~pts.in_victoria]
    if len(bad):
        print("\n  flagged in_victoria=False:")
        for r in bad.itertuples():
            print(f"    {r.facility_name} · {r.address[:58]} -> {r.latitude:.3f}, {r.longitude:.3f}")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return pts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", help="a single date, YYYY-MM-DD, instead of the whole Mon-Fri week")
    main(ap.parse_args().day)
