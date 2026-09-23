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
OUT = ROOT / "outputs/pickup_points.csv"

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


def collection_stops():
    """The stops that count as a collection, on the same rule the weights use."""
    col = cvw.collection_slice(cvw.load_ccp())
    if not hasattr(cvw, "LODGE"):
        # This checkout predates the rule that lets the transports count lodgement points
        # (post offices, lockers, posting boxes) as well as customer pickups — it lives on the
        # pickup-catchment-weights branch. Applying it here keeps this extract consistent with
        # the weights file on disk. DELETE this block once the branches are merged; it is a
        # second copy of a rule that should have exactly one.
        print("  NOTE: this branch has no cvw.LODGE — applying the transport lodgement rule here")
        w = cvw.load_ccp()
        wk = w[w.day.astype(str).between(*cvw.WINDOW)].copy()
        name = wk.loc_name.astype(str).str.upper()
        own = wk.loc_type.eq("Network") & name.str.contains(cvw.OWN, regex=True, na=False)
        lodge = wk.loc_type.eq("Network") & name.str.contains(
            r"\bLPO\b|POST OFFICE|POSTSHOP|\bRP\b|LOCKER|POST BOX|POSTING BOX|\bSPB\b",
            regex=True, na=False)
        extra = wk[wk.facility.isin(cvw.TRANSPORTS) & wk.booking_type.isin(cvw.COLLECTING)
                   & lodge & ~own].copy()
        extra["own"] = False
        col = pd.concat([col, extra], ignore_index=True)
        print(f"        +{len(extra)} transport lodgement stops")
    return col[~col.own].copy()


def main():
    print("building the pickup-point extract")
    veh = vehicle_by_site()
    col = collection_stops()

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
    pts.to_csv(OUT, index=False)

    print(f"\n  {len(pts):,} pickup points over {pts.facility_name.nunique()} sites, "
          f"{pts.stops.sum():,} stops Mon-Fri")
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
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return pts


if __name__ == "__main__":
    main()
