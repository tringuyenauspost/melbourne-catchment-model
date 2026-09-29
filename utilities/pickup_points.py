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
FAC2 = ROOT / "outputs/melbourne_optilogic_chain2_observed/Facilities.csv"
OUT = ROOT / "outputs/pickup_points.csv"          # --day writes a dated sibling
OUT_STOPS = ROOT / "outputs/pickup_stops.csv"     # --per-stop, one row per collection
OUT_FLEET = ROOT / "outputs/pickup_fleet_metrics.csv"   # routes per day and volume per route

# What --per-stop calls each vehicle. A RELABEL for the downstream tool, not a model change:
# the model's modes stay Red_Van and Truck in first_mile_pickup.csv and transport_modes.csv,
# and pickup_points.csv still carries those. Note white_van is itself a mode name in
# transport_modes.csv with quite different numbers (160 EA at $1.1/km, against Truck's 1,500 at
# $0.9), so anything joining this column back to the model by name will pick up the wrong
# vehicle — map through this dict rather than treating the label as the mode.
VEHICLE_LABEL = {"Red_Van": "red_van", "Truck": "white_van"}

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


def site_volume():
    """site van-arm name -> the model's daily collection, floor(PEAK_2025_<node> x PEAK_FACTOR).

    Read from dials_chain1.csv, so it is the number chain 1 pins, not a copy of it."""
    d = pd.read_csv(DIALS).set_index("parameter")["value"]
    factor = float(d["PEAK_FACTOR"])
    sites = pd.read_csv(SITES)
    return {r.van_arm: int(float(d[f"PEAK_2025_{r.node}"]) * factor)
            for r in sites[sites.van_arm.notna()].itertuples()}


def route_rounds(jobs, col, veh):
    """Pickup ROUNDS per (facility, day, route): runs of pickups each ending at an unload.

    A van often collects, drives back to unload, and goes out again — HX000 at Sunshine West on
    18 May does 8 pickups, unloads at SWPDC at 13:25 ("Deliver All Available - Customer
    Collections"), then 13 more and unloads again at 17:50. Stops per ROUTE says 21; stops per
    TRIP says 8 and 13, and a trip is what a vehicle's capacity has to hold.

    An UNLOAD is a delivery booking at one of our own buildings (the OWN name test, Network
    rows only), except a red van's drop at a delivery centre: those are relay drops ("Deliver
    all ex-Moorabbin DC"), not the van emptying what it collected. Jobs run in planned-time
    order within the route and day. Pickups after the last unload are one more round — the
    return to base is often simply not booked — so a route with pickups has >= 1 round."""
    n = jobs.loc_name.astype(str).str.upper()
    van = jobs.facility.map(veh).eq("Red_Van")
    dc = van & n.str.contains(cvw.DELIVERY_CENTRE, regex=True, na=False)
    unload = (jobs.loc_type.eq("Network") & n.str.contains(cvw.OWN, regex=True, na=False) & ~dc
              & jobs.booking_type.isin(["Delivery", "Pickup & Delivery"]))
    j = jobs.assign(unload=unload, pick=jobs.index.isin(col.index),
                    t=pd.to_timedelta(jobs["Job Planned Time"].astype(str)))
    j = j[j.facility.isin(set(col.facility)) & j.day.isin(set(col.day))]
    j = j.sort_values(["facility", "day", "route", "t"], kind="stable")
    rows = []
    for (fac, d, route), g in j.groupby(["facility", "day", "route"], sort=False):
        if not g.pick.any():
            continue
        rounds, cur = 0, 0
        for p, u in zip(g.pick, g.unload):
            cur += p
            if u and cur:
                rounds, cur = rounds + 1, 0
        rows.append((fac, str(d), route, int(g.pick.sum()), rounds + (cur > 0)))
    return pd.DataFrame(rows, columns=["facility", "day", "route", "stops", "rounds"])


def fleet_metrics(col, veh, day, rounds):
    """Routes per day per site, and the model's volume per route, per round and per stop.

    A ROUTE is a CCP route name with at least one counted pickup that day, and it stands in for
    one vehicle. That is an upper bound on vehicles if a van runs two routes in a day, and it
    counts only the routes that collect: a truck route running nothing but network linehaul is
    not in it. Volume is the model's daily peak, not a CCP measurement — CCP records none at the
    red-van sites — so vol/route is "what each collecting vehicle would carry at peak"."""
    daily = (col.assign(day=col.day.astype(str))
                .groupby(["facility", "day"])
                .agg(stops=("addr", "size"), routes=("route", "nunique")).reset_index())
    vol = site_volume()
    m = daily.groupby("facility").agg(
        days=("day", "nunique"), stops_per_day=("stops", "mean"),
        routes_per_day=("routes", "mean"), routes_min=("routes", "min"),
        routes_max=("routes", "max"))
    m.insert(0, "vehicle_type", m.index.map(veh))
    m["stops_per_route"] = m.stops_per_day / m.routes_per_day
    ndays = rounds.groupby("facility").day.nunique()
    m["rounds_per_day"] = rounds.groupby("facility").rounds.sum() / ndays
    m["multi_round_pct"] = 100 * rounds.rounds.gt(1).groupby(rounds.facility).mean()
    m["rounds_per_route"] = m.rounds_per_day / m.routes_per_day
    m["stops_per_round"] = m.stops_per_day / m.rounds_per_day
    m["peak_volume_ea"] = m.index.map(vol)
    m["vol_per_route"] = m.peak_volume_ea / m.routes_per_day
    m["vol_per_round"] = m.peak_volume_ea / m.rounds_per_day
    m["vol_per_stop"] = m.peak_volume_ea / m.stops_per_day
    m = m.reset_index().sort_values(["vehicle_type", "facility"])

    # the same ratios over each vehicle type, weighted by what each site actually runs
    agg = m.groupby("vehicle_type")[["stops_per_day", "routes_per_day", "rounds_per_day",
                                      "peak_volume_ea"]].sum()
    agg["stops_per_route"] = agg.stops_per_day / agg.routes_per_day
    agg["multi_round_pct"] = 100 * rounds.rounds.gt(1).groupby(
        rounds.facility.map(veh)).mean()
    agg["rounds_per_route"] = agg.rounds_per_day / agg.routes_per_day
    agg["stops_per_round"] = agg.stops_per_day / agg.rounds_per_day
    agg["vol_per_round"] = agg.peak_volume_ea / agg.rounds_per_day
    agg["vol_per_route"] = agg.peak_volume_ea / agg.routes_per_day
    agg["vol_per_stop"] = agg.peak_volume_ea / agg.stops_per_day
    spread = m.groupby("vehicle_type").vol_per_route.agg(["min", "max"])
    agg["vol_per_route_site_min"], agg["vol_per_route_site_max"] = spread["min"], spread["max"]
    agg = agg.reset_index().assign(facility="ALL " + agg.index.str.upper())

    out = pd.concat([m, agg], ignore_index=True)
    rnd = {c: 1 for c in ["stops_per_day", "routes_per_day", "stops_per_route",
                          "rounds_per_day", "multi_round_pct", "stops_per_round",
                          "vol_per_route", "vol_per_round", "vol_per_stop",
                          "vol_per_route_site_min", "vol_per_route_site_max"]}
    rnd["rounds_per_route"] = 2
    out = out.round(rnd)
    path = OUT_FLEET if not day else OUT_FLEET.with_name(f"{OUT_FLEET.stem}_{day}{OUT_FLEET.suffix}")
    out.to_csv(path, index=False)

    basis = day if day else f"mean of {int(m.days.max())} days"
    print(f"\n  fleet metrics ({basis}; a route = one collecting vehicle):")
    show = ["vehicle_type", "facility", "stops_per_day", "routes_per_day", "stops_per_route",
            "peak_volume_ea", "vol_per_route", "vol_per_stop"]
    print(out[show].to_string(index=False))
    print("\n  pickup rounds (a round = pickups ending at an unload at our own building):")
    show = ["vehicle_type", "facility", "rounds_per_day", "rounds_per_route", "multi_round_pct",
            "stops_per_round", "vol_per_round"]
    print(out[show].to_string(index=False))
    print(f"wrote {path.relative_to(ROOT)}")
    return out


def warehouse_xy():
    """Each collecting site's own coordinate — the WAREHOUSE end of a pickup leg.

    From chain 2's Facilities.csv, which is where s2b takes its lane distances from. NOT from
    sites.csv: its lat/long are blank for both transport facilities."""
    sites = pd.read_csv(SITES)
    fac = pd.read_csv(FAC2).set_index("facilityname")
    out = {}
    for r in sites[sites.van_arm.notna()].itertuples():
        assert r.node in fac.index, f"chain 2 has no facility row for {r.node}"
        out[r.van_arm] = (float(fac.loc[r.node, "latitude"]), float(fac.loc[r.node, "longitude"]))
    return out


def write_per_stop(col, geo, day, veh):
    # fail here, not silently, if a site ever collects on a mode with no label

    """One row per COLLECTION, not per address — an address called at three times is three rows.

    The shape a routing tool wants: where the vehicle comes from, where it goes, how much it
    lifts. Total Articles is 1 on every row: CCP records no per-stop article count for pickups
    (its Measure Values are zero on every Pickup booking), so a stop is one unit of demand and
    the volume, if it is ever needed, has to come from somewhere else."""
    unknown = sorted(set(veh.values()) - set(VEHICLE_LABEL))
    assert not unknown, f"no Vehicle_Type label for mode(s) {unknown} — add them to VEHICLE_LABEL"
    wh = warehouse_xy()
    d = col.merge(geo, left_on="addr", right_on="Location Address", how="left")
    assert len(d) == len(col), "the geocode join duplicated rows"
    assert d.google_latitude.notna().all(), (
        f"{int(d.google_latitude.isna().sum())} stops have no geocode")
    bad = ~(d.google_latitude.between(*VIC["lat"]) & d.google_longitude.between(*VIC["lon"]))
    if bad.any():
        print(f"  dropping {int(bad.sum())} stop(s) on a geocode outside Victoria — "
              "a bad coordinate is worse than a missing row for a routing input")
        d = d[~bad]
    out = pd.DataFrame({
        "Facility Name": d.facility.values,
        "Vehicle_Type": [VEHICLE_LABEL[veh[f]] for f in d.facility],
        "Warehouse Latitude": [wh[f][0] for f in d.facility],
        "Warehouse Longitude": [wh[f][1] for f in d.facility],
        "Total Articles": 1,
        "Delivery Latitude": d.google_latitude.values,
        "Delivery Longitude": d.google_longitude.values})
    path = OUT_STOPS if not day else OUT_STOPS.with_name(f"{OUT_STOPS.stem}_{day}{OUT_STOPS.suffix}")
    out.to_csv(path, index=False)
    print(f"\n  {len(out):,} rows — one per collection stop")
    print(out.groupby(["Vehicle_Type", "Facility Name"]).agg(
        stops=("Total Articles", "size"),
        warehouse_lat=("Warehouse Latitude", lambda s: f"{s.iloc[0]:.4f}"),
        warehouse_lon=("Warehouse Longitude", lambda s: f"{s.iloc[0]:.4f}")).to_string())
    print(f"\nwrote {path.relative_to(ROOT)}")
    return out


def collection_stops(day=None):
    """The stops that count as a collection, on the same rule the weights use — imported, so
    the extract and the weights can never drift into two different definitions.

    `day` narrows to a single date. `stops` counts JOB ROWS either way, so an address called at
    twice in a day counts twice — the duplicates are the point, not noise to be folded away."""
    jobs = cvw.load_ccp()
    col = cvw.collection_slice(jobs)
    col = col[~col.own].copy()
    if day:
        n0 = len(col)
        col = col[col.day.astype(str).eq(day)]
        assert len(col), f"no collection stops on {day} — the window is {cvw.WINDOW}"
        print(f"  {day} only: {len(col):,} of {n0:,} stops in the week")
    return col, jobs   # every CCP job too: route_rounds needs the unloads the filter drops


def main(day=None, per_stop=False):
    out = (OUT if not day else OUT.with_name(f"{OUT.stem}_{day}{OUT.suffix}"))
    print(f"building the pickup-point extract{' for ' + day if day else ''}")
    veh = vehicle_by_site()
    col, jobs = collection_stops(day)

    geo = (pd.read_csv(GEOCODE, usecols=["Location Address", "google_latitude",
                                         "google_longitude", "suburb"])
             .drop_duplicates("Location Address"))
    if per_stop:
        return write_per_stop(col, geo, day, veh)
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
    print("\n  stops by date:")
    by_day = (col.assign(vehicle_type=col.facility.map(veh), day=col.day.astype(str))
                 .pivot_table(index=["vehicle_type", "facility"], columns="day",
                              values="addr", aggfunc="size", fill_value=0))
    by_day["total"] = by_day.sum(axis=1)
    by_day.loc[("", "all sites"), :] = by_day.sum()
    print(by_day.astype(int).to_string())
    fleet_metrics(col, veh, day, route_rounds(jobs, col, veh))
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
    ap.add_argument("--per-stop", action="store_true",
                    help="one row per collection instead of per address, as warehouse -> "
                         "delivery pairs for a routing tool")
    a = ap.parse_args()
    main(a.day, a.per_stop)
