"""Link the CCP bookings to the driver duties, for the two transport facilities.

WHAT THIS IS FOR. The catchment weights come from STOP COUNTS and nothing else — see
utilities/catchment_visit_weights.py. This script does not produce weights. It answers two
operational questions about the two transport facilities:

    1. how well do the two files agree on route names, and
    2. what vehicle runs each route, and how many collection stops does it make.

WHY ONLY THE TRANSPORTS. The five PDC sites are red-van operations: their duties record the
vehicle as `Van` on 95%+ of rows, so there is nothing to profile. Melbourne Transport and
Dandenong Transport Facility run a mixed fleet — rigids from 16 to 32 ULD, prime movers, A- and
B-trailers to 60 ULD.

WHAT WAS REMOVED, AND WHY IT SHOULD STAY REMOVED. An earlier version weighted the transport
catchments by `Total Container Actual Qty` instead of stops. That was wrong and the decision is
settled (user, 2026-09-22): the field is undocumented, and it counts freight moved in BOTH
directions — 493 duty-days that make no customer pickup at all still record 62,368 containers —
so it scores how busy a route is, not how much it collects. Weight by stops. Do not reintroduce
a container basis, and do not reintroduce a vehicle-capacity basis either: capacity alone scored
no better than stops, and no stop in the data has a vehicle recorded without also having a
container count, so vehicle type could only ever substitute for containers, never add to them.

THE JOIN. `Route Name` is the SAME identifier in both files — no normalisation, no fuzzy
matching. Joining on (facility, date, route) over Mon-Fri covers:

                          route names        collection stops
    Dandenong Transport   115/123 (93%)      1,053/1,058  99.5%
    Melbourne Transport   195/239 (82%)      1,511/1,798  84.0%

Melbourne's unmatched stops are MTECTR* contractor runs and 'Netw'/'Semi' workings; 13 of its 19
unmatched routes appear in the duties on another date, so the residual is duty-file date
coverage rather than unknown routes.

    python utilities/transport_duty_link.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CCP = ROOT / "inputs/red_vans_inputs/CCP-18-05_to_25-05.csv"
DUTIES = ROOT / "inputs/red_vans_inputs/driver_duties_summary/transport-facilities"
OUT_R = ROOT / "outputs/transport_route_profile.csv"

WINDOW = ("2026-05-18", "2026-05-22")
FILES = {"Melbourne Transport": "melbourne_transport.csv",
         "Dandenong Transport Facility": "dandenong_transport_facility.csv"}
MEASURES = ["Volumes Delivered", "Volumes Collected",
            "Expected Volumes Delivered", "Expected Volumes Collected"]


def load_ccp():
    """One row per job — CCP repeats each job over four measures, in blocks of four."""
    df = pd.read_csv(CCP, low_memory=False)
    key = [c for c in df.columns if c not in ("Measure Names", "Measure Values")]
    w = df.iloc[::4][key].reset_index(drop=True)
    for i, m in enumerate(MEASURES):
        w[m] = pd.to_numeric(df.iloc[i::4]["Measure Values"].values, errors="coerce")
    w = w.rename(columns={"Location Address": "addr", "Facility Wcc Name": "facility",
                          "Route Name": "route", "Location Type": "loc_type",
                          "Booking Type": "booking_type"})
    w["facility"] = w.facility.str.title()
    w["day"] = pd.to_datetime(w["Job planned dwell date"], format="%d/%m/%Y", errors="coerce").dt.date
    w["post_code"] = pd.to_numeric(w.addr.astype(str).str.split(" ").str[-2], errors="coerce")
    w = w[w.post_code.notna() & w.facility.isin(FILES)].copy()
    w["post_code"] = w.post_code.astype(int)
    return w[w.day.astype(str).between(*WINDOW)]


def uld(v):
    """ULD capacity, for description only. The field is a comma-separated RIG — a prime mover
    plus its trailers — so the capacities add; a bare 'Prime Mover' has none of its own."""
    if not isinstance(v, str):
        return np.nan
    caps = [int(m) for m in re.findall(r"(\d+)\s*ULD", v)]
    return sum(caps) if caps else 0


def load_duties():
    """The duties export is UTF-16, tab separated, and one header has a double space."""
    out = []
    for site, fn in FILES.items():
        d = pd.read_csv(DUTIES / fn, sep="\t", encoding="utf-16")
        d.columns = [re.sub(r"\s+", " ", c).strip() for c in d.columns]
        d = d.rename(columns={"Route Name": "route", "Group Vehicle Description": "veh",
                              "Job Count": "duty_jobs", "Total Stops": "duty_stops",
                              "Est. Shift Time (hrs)": "hrs"})
        d["facility"] = site
        d["uld"] = d.veh.map(uld)
        d["day"] = pd.to_datetime(d["Start Date"], format="%d/%m/%Y", errors="coerce").dt.date
        out.append(d[["facility", "day", "route", "veh", "uld", "duty_stops", "duty_jobs", "hrs"]])
    du = pd.concat(out, ignore_index=True)
    # two (facility, day, route) rows repeat — a duty handed over mid-shift; fold them
    return du.groupby(["facility", "day", "route"], as_index=False).agg(
        veh=("veh", "first"), uld=("uld", "max"), duty_stops=("duty_stops", "sum"),
        duty_jobs=("duty_jobs", "sum"), hrs=("hrs", "sum"))


def coverage(ccp, du, coll):
    print("=== ROUTE-NAME COVERAGE, CCP vs the duties file ===")
    win = du[du.day.astype(str).between(*WINDOW)]
    m = coll.merge(du, on=["facility", "day", "route"], how="left", indicator=True)
    for site, t in m.groupby("facility"):
        hit = t._merge.eq("both")
        all_r = set(ccp[ccp.facility.eq(site)].route.dropna())
        du_r = set(win[win.facility.eq(site)].route)
        print(f"\n  {site}")
        print(f"    route names   {len(all_r & du_r):4d} of {len(all_r):3d} CCP routes matched "
              f"({100 * len(all_r & du_r) / len(all_r):.0f}%) · {len(du_r - all_r):3d} duties-only")
        print(f"    stops         {hit.sum():5,} / {len(t):5,}  ({100 * hit.mean():5.1f}%)")
        print(f"    postcodes     {t.loc[hit, 'post_code'].nunique():5,} / {t.post_code.nunique():5,}")
    return m


def route_profile(ccp, du, coll):
    """One row per (facility, route): what it is, how big the vehicle, how many stops.

    Three stop counts, because they answer different questions and they disagree:
      ccp_pickup_stops  Customer/Pickup jobs — the stops that feed a catchment weight
      ccp_jobs          every CCP job on the route, deliveries and our own buildings included
      duty_stops        the duty's own Total Stops, which counts stops CCP does not show
    A route's vehicle can change day to day, so `vehicle` is the one used on the most days and
    `vehicle_varies` flags the rest rather than hiding them behind a first().
    """
    win = du[du.day.astype(str).between(*WINDOW)]
    prof = win.groupby(["facility", "route"]).agg(
        days_run=("day", "nunique"), duty_stops=("duty_stops", "sum"),
        duty_jobs=("duty_jobs", "sum"), uld_max=("uld", "max"), hrs=("hrs", "sum")).reset_index()
    veh = (win.dropna(subset=["veh"]).groupby(["facility", "route", "veh"]).day.nunique()
              .reset_index().sort_values("day", ascending=False)
              .drop_duplicates(["facility", "route"]))
    prof = prof.merge(veh.rename(columns={"veh": "vehicle"})[["facility", "route", "vehicle"]],
                      on=["facility", "route"], how="left")
    nveh = win.groupby(["facility", "route"]).veh.nunique().rename("n_veh")
    prof = prof.merge(nveh.reset_index(), on=["facility", "route"], how="left")
    prof["vehicle_varies"] = prof.n_veh.fillna(0) > 1
    prof["uld_capacity"] = prof.vehicle.map(uld)

    a = ccp.groupby(["facility", "route"]).agg(ccp_jobs=("addr", "size")).reset_index()
    c = coll.groupby(["facility", "route"]).agg(ccp_pickup_stops=("addr", "size"),
                                                postcodes=("post_code", "nunique")).reset_index()
    prof = prof.merge(a, on=["facility", "route"], how="outer").merge(
        c, on=["facility", "route"], how="outer")
    prof["in_duties"] = prof.days_run.notna()
    prof["in_ccp"] = prof.ccp_jobs.notna()
    for col in ["days_run", "duty_stops", "duty_jobs", "ccp_jobs", "ccp_pickup_stops", "postcodes"]:
        prof[col] = prof[col].fillna(0)
    prof["vehicle_varies"] = prof.vehicle_varies.fillna(False).astype(bool)
    prof["uld_capacity"] = prof.uld_capacity.fillna(0)
    # two different absences, and they must not share a label: a route with no duties row at
    # all, versus one that HAS duties rows whose vehicle field is blank
    prof.loc[prof.vehicle.isna() & prof.in_duties, "vehicle"] = "in duties, vehicle not recorded"
    prof.loc[prof.vehicle.isna(), "vehicle"] = "no duties row"

    prof = prof.sort_values(["facility", "ccp_pickup_stops"], ascending=[True, False])
    cols = ["facility", "route", "vehicle", "vehicle_varies", "uld_capacity", "days_run",
            "ccp_pickup_stops", "ccp_jobs", "duty_stops", "duty_jobs", "postcodes", "hrs",
            "in_ccp", "in_duties"]
    prof[cols].to_csv(OUT_R, index=False)

    print("\n=== ROUTE PROFILE ===")
    for site, t in prof.groupby("facility"):
        pick = t[t.ccp_pickup_stops > 0]
        print(f"\n  {site}: {len(t)} routes "
              f"({int(t.in_ccp.sum())} in CCP, {int(t.in_duties.sum())} in duties) · "
              f"{int(t.vehicle_varies.sum())} change vehicle during the week")
        print(f"    {len(pick)} routes make a Customer/Pickup stop; the rest are delivery or linehaul")
        print(f"      {'route':14s} {'stops':>6s} {'ULD':>4s} {'pc':>3s}  vehicle")
        for r in pick.head(10).itertuples():
            v = r.vehicle.replace("Rigid Vehicle ", "Rigid ").replace(" Pallet", "p")
            print(f"      {r.route[:14]:14s} {int(r.ccp_pickup_stops):6d} {r.uld_capacity:4.0f} "
                  f"{int(r.postcodes):3d}  {v[:40]}{' *' if r.vehicle_varies is True else ''}")
    print(f"\n  wrote {OUT_R.relative_to(ROOT)}   (* = vehicle changes during the week)")
    return prof


def main():
    ccp, du = load_ccp(), load_duties()
    # the transports' own filter: their Network stops are our own buildings, i.e. linehaul
    coll = ccp[ccp.loc_type.eq("Customer") & ccp.booking_type.eq("Pickup")].copy()
    coverage(ccp, du, coll)
    route_profile(ccp, du, coll)


if __name__ == "__main__":
    main()
