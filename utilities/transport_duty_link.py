"""Link CCP bookings to the driver duties, and weight the two transports by containers moved.

WHY ONLY THE TRANSPORTS. The five PDC sites are red-van operations: one van, one vehicle class,
so a vehicle-type weighting has nothing to bite on and their duties live in
driver_duties_summary/pdc/. Melbourne Transport and Dandenong Transport Facility run a mixed
fleet — rigids from 16 to 32 ULD, prime movers, A- and B-trailers to 60 ULD — so a stop served
by a B-double is not the same quantity of freight as a stop served by a 10-pallet rigid. Those
two sites are also 58% of modelled metro pickup, so the shape there matters most.

THE JOIN. `Route Name` is the SAME identifier in both files — no normalisation, no fuzzy
matching, and route names alone answer the coverage question. Joining on (facility, date,
route) over Mon-Fri covers:

                          route names        collection stops   recorded EA   postcodes
    Dandenong Transport   115/123 (93%)      1,053/1,058 99.5%     97.3%      49 of 49
    Melbourne Transport   195/239 (82%)      1,511/1,798 84.0%    100.0%      39 of 40

with 6 and 1 duties-only routes respectively (duties that never appear in CCP).

Melbourne's 287 unmatched stops carry ONE article of recorded volume between them — they are
MTECTR* contractor runs and 'Netw'/'Semi' workings, not collections — so the 84% is a stop
count, not a volume gap. 13 of its 19 unmatched routes appear in the duties on another date,
so the residual is duty-file date coverage rather than unknown routes.

WHAT THE DUTIES ADD. `Total Container Actual Qty` — containers actually moved on the duty —
and `Group Vehicle Description`, which gives ULD capacity. Note the container count is a
CUMULATIVE day total, not a vehicle load: it runs ~4x the vehicle's ULD capacity at the median,
consistent with a duty turning its vehicle over several times across 13-15 stops.

THE RESULT, AND A NEGATIVE ONE. Containers beat stop-events at reproducing the volume CCP
actually records, at both sites and on both share- and rank-correlation:

    site                  stops   containers   ULD capacity   (vs recorded collected volume)
    Melbourne Transport   0.985     0.995         0.980
    Dandenong Transport   0.972     0.982         0.976

But VEHICLE CAPACITY ALONE IS NOT THE WIN — it is a wash against stops (0.980/0.976 vs
0.985/0.972). Weighting a stop by the size of the truck that served it does not help; what
helps is how much that duty actually moved. Do not carry the capacity weighting forward.
The ordering is stable however the duty's containers are divided over its stops (by CCP job
count, by the duty's own job count, or not at all), so it is not an artefact of that choice.

THE ALLOCATION AND ITS ASSUMPTION. A duty's containers cover every job on it — deliveries and
collections, our own buildings and customers — and CCP shows us which jobs are collections. So
containers are spread EQUALLY over the duty's jobs and only the share landing on a
Customer/Pickup job is counted to that postcode. Equal-per-stop within a duty is the
assumption; it still recovers the between-duty variation that stop-counting throws away, which
is where the gain comes from. Postcode 3064 is the case in point: 20 stops (1.3% of Melbourne
Transport's stops) but 4.8% of its recorded volume, because those stops are big loads.

    python utilities/transport_duty_link.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CCP = ROOT / "inputs/red_vans_inputs/CCP-18-05_to_25-05.csv"
DUTIES = ROOT / "inputs/red_vans_inputs/driver_duties_summary/transport-facilities"
WEIGHTS = ROOT / "inputs/melbourne/first_mile_catchment_weights.csv"
OUT = ROOT / "outputs/transport_duty_link.csv"
OUT_R = ROOT / "outputs/transport_route_profile.csv"
OUT_V = ROOT / "outputs/transport_vehicle_rates.csv"
OUT_W = ROOT / "outputs/first_mile_catchment_weights_container.csv"

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
    """ULD capacity. The field is a comma-separated RIG: a prime mover plus its trailers, so
    the capacities add. A bare 'Prime Mover' has none of its own and scores 0."""
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
                              "Total Container Actual Qty": "cont", "Job Count": "duty_jobs",
                              "Total Stops": "duty_stops", "Est. Shift Time (hrs)": "hrs"})
        d["facility"] = site
        d["uld"] = d.veh.map(uld)
        d["cont"] = pd.to_numeric(d.cont, errors="coerce")
        d["day"] = pd.to_datetime(d["Start Date"], format="%d/%m/%Y", errors="coerce").dt.date
        out.append(d[["facility", "day", "route", "veh", "uld", "cont",
                      "duty_stops", "duty_jobs", "hrs"]])
    du = pd.concat(out, ignore_index=True)
    # two (facility, day, route) rows repeat — a duty handed over mid-shift; fold them
    return du.groupby(["facility", "day", "route"], as_index=False).agg(
        veh=("veh", "first"), uld=("uld", "max"), cont=("cont", "sum"),
        duty_stops=("duty_stops", "sum"), duty_jobs=("duty_jobs", "sum"), hrs=("hrs", "sum"))


def vehicle_rates(ok):
    """Containers per collection stop, calibrated by vehicle class.

    WHY THIS IS NOT THE RECOMMENDED WEIGHT, despite vehicle type being the obvious thing to
    reach for once you know the fleet is mixed. Three findings, in order of how decisive:

      1. THE FALLBACK TIER IS EMPTY. Of 2,856 collection stops, 2,169 have both a vehicle and
         a container count, 395 have containers but no vehicle, 292 have neither, and
         **ZERO have a vehicle but no containers**. Wherever the vehicle is known the
         containers are known too, so vehicle type can only ever SUBSTITUTE for containers,
         never fill a gap they leave.
      2. ROUTE BEATS VEHICLE as the unit of variation. Route identity explains 70.3% / 74.0%
         of the variance in containers per stop (Dandenong / Melbourne); vehicle class
         explains 12.7% / 38.2%. Containers are already a route-level measure, so they capture
         what vehicle class would say and more.
      3. IT SCORES WORSE. Weighting stops by these rates gives 0.980/0.872 at Dandenong and
         0.984/0.796 at Melbourne against recorded volume — at Melbourne that is below plain
         stop-counting on rank correlation (0.813). Containers give 0.982/0.885 and
         0.995/0.917.

    A tiered weight (containers, then this table, then a site mean) also scores worse than
    containers alone — 0.992/0.870 at Melbourne — because the 292 stops with neither are the
    MTECTR* contractor runs and 'Semi'/'Netw' workings, which carry 172 EA between them.
    Imputing them an average load invents volume that is not there.

    WHERE IT IS STILL WORTH HAVING: a period where ops supply the duties WITHOUT container
    counts, or a CCP extract with no duties join at all. Then this table weights a stop from
    its vehicle alone, and at Dandenong that still beats stop-counting. The rates are also a
    sanity check on the fleet: they rise monotonically with capacity at Melbourne (6.5 -> 26.1
    containers/stop from a 20 ULD rigid to a 44 ULD B-trailer), which is the evidence that the
    vehicle field means what it says.
    """
    rows = []
    for site, t in ok.groupby("facility"):
        k = t[t.cont_per_stop.notna() & t.veh.notna()]
        n = k.groupby("veh").size()
        r = k.groupby("veh").cont_per_stop.agg(["mean", "median"])
        r["stops"], r["facility"], r["uld_capacity"] = n, site, [uld(v) for v in r.index]
        rows.append(r.reset_index().rename(columns={"veh": "vehicle"}))
    v = pd.concat(rows, ignore_index=True).sort_values(["facility", "uld_capacity"])
    v["supported"] = v.stops >= 10          # classes thin enough that the mean is noise
    v.to_csv(OUT_V, index=False)
    print("\n=== CONTAINERS PER COLLECTION STOP, BY VEHICLE CLASS ===")
    for site, t in v[v.supported].groupby("facility"):
        print(f"\n  {site}")
        for r in t.itertuples():
            print(f"    {r.mean:6.1f} mean  {r.median:6.1f} median  n={int(r.stops):4d}  "
                  f"{int(r.uld_capacity):3d} ULD  {r.vehicle[:44]}")
    print(f"\n  wrote {OUT_V.relative_to(ROOT)}  — reference only, NOT the recommended weight")
    print("    (vehicle type can only substitute for containers, never supplement them:")
    print("     zero stops have a vehicle without a container count — see the docstring)")
    return v


def route_profile(ccp, du, coll):
    """One row per (facility, route): what it is, how big the vehicle, how much it moved.

    Three stop counts, because they answer different questions and they disagree:
      ccp_pickup_stops  Customer/Pickup jobs — the stops that feed a catchment weight
      ccp_jobs          every CCP job on the route, deliveries and our own buildings included
      duty_stops        the duty's own Total Stops, which counts stops CCP does not show
    A route's vehicle can change day to day, so `vehicle` is the one used on the most days and
    `vehicle_varies` flags the rest rather than hiding them behind a first().
    """
    win = du[du.day.astype(str).between(*WINDOW)]
    g = win.groupby(["facility", "route"])
    prof = g.agg(days_run=("day", "nunique"), duty_stops=("duty_stops", "sum"),
                 duty_jobs=("duty_jobs", "sum"), containers=("cont", "sum"),
                 uld_max=("uld", "max"), hrs=("hrs", "sum")).reset_index()
    veh = (win.dropna(subset=["veh"]).groupby(["facility", "route", "veh"]).day.nunique()
              .reset_index().sort_values("day", ascending=False)
              .drop_duplicates(["facility", "route"]))
    prof = prof.merge(veh.rename(columns={"veh": "vehicle"})[["facility", "route", "vehicle"]],
                      on=["facility", "route"], how="left")
    nveh = win.groupby(["facility", "route"]).veh.nunique().rename("n_veh")
    prof = prof.merge(nveh.reset_index(), on=["facility", "route"], how="left")
    prof["vehicle_varies"] = prof.n_veh.fillna(0) > 1
    prof["uld_capacity"] = prof.vehicle.map(uld)

    a = ccp.groupby(["facility", "route"]).agg(ccp_jobs=("addr", "size"),
                                               ccp_days=("day", "nunique")).reset_index()
    c = coll.groupby(["facility", "route"]).agg(ccp_pickup_stops=("addr", "size"),
                                                postcodes=("post_code", "nunique"),
                                                recorded_EA=("Volumes Collected", "sum")).reset_index()
    prof = prof.merge(a, on=["facility", "route"], how="outer").merge(
        c, on=["facility", "route"], how="outer")
    prof["in_duties"] = prof.days_run.notna()
    prof["in_ccp"] = prof.ccp_jobs.notna()
    for col in ["days_run", "duty_stops", "duty_jobs", "containers", "ccp_jobs",
                "ccp_pickup_stops", "postcodes", "recorded_EA", "ccp_days"]:
        prof[col] = prof[col].fillna(0)
    # the outer merges add CCP-only routes AFTER vehicle_varies was computed, leaving NaN —
    # and NaN is truthy, so it would print as "varies"
    prof["vehicle_varies"] = prof.vehicle_varies.fillna(False).astype(bool)
    prof["uld_capacity"] = prof.uld_capacity.fillna(0)
    # two different absences, and they must not share a label: a route with no duties row at
    # all, versus one that HAS duties rows whose vehicle field is blank (those still carry
    # containers, so lumping them together invents 12,581 containers on "missing" routes)
    prof.loc[prof.vehicle.isna() & prof.in_duties, "vehicle"] = "in duties, vehicle not recorded"
    prof.loc[prof.vehicle.isna(), "vehicle"] = "no duties row"
    prof["containers_per_pickup_stop"] = (prof.containers / prof.ccp_pickup_stops
                                          .replace(0, np.nan)).round(1)
    prof = prof.sort_values(["facility", "ccp_pickup_stops", "containers"],
                            ascending=[True, False, False])
    cols = ["facility", "route", "vehicle", "vehicle_varies", "uld_capacity", "days_run",
            "ccp_pickup_stops", "ccp_jobs", "duty_stops", "duty_jobs", "containers",
            "containers_per_pickup_stop", "postcodes", "recorded_EA", "hrs",
            "in_ccp", "in_duties"]
    prof[cols].to_csv(OUT_R, index=False)

    print("\n=== ROUTE PROFILE ===")
    for site, t in prof.groupby("facility"):
        both = t[t.in_ccp & t.in_duties]
        print(f"\n  {site}: {len(t)} routes "
              f"({int(t.in_ccp.sum())} in CCP, {int(t.in_duties.sum())} in duties, {len(both)} in both)")
        print(f"    {int(t.vehicle_varies.sum())} routes change vehicle during the week")
        pick = t[t.ccp_pickup_stops > 0]
        print(f"    {len(pick)} routes make a Customer/Pickup stop; the rest are delivery or linehaul")
        print(f"\n    top 12 by collection stops")
        print(f"      {'route':14s} {'stops':>6s} {'cont':>7s} {'ULD':>4s} {'pc':>3s} {'EA':>6s}  vehicle")
        for r in pick.head(12).itertuples():
            v = r.vehicle.replace("Rigid Vehicle ", "Rigid ").replace(" Pallet", "p")
            print(f"      {r.route[:14]:14s} {int(r.ccp_pickup_stops):6d} {int(r.containers):7d} "
                  f"{r.uld_capacity:4.0f} "
                  f"{int(r.postcodes):3d} {int(r.recorded_EA):6d}  {v[:34]}{' *' if r.vehicle_varies is True else ''}")
    print(f"\n  wrote {OUT_R.relative_to(ROOT)}   (* = vehicle changes during the week)")
    return prof


def main():
    ccp, du = load_ccp(), load_duties()
    # the transports' own filter: their Network stops are our buildings, i.e. linehaul
    coll = ccp[ccp.loc_type.eq("Customer") & ccp.booking_type.eq("Pickup")].copy()
    jobs = ccp.groupby(["facility", "day", "route"]).size().rename("ccp_jobs")
    m = coll.merge(du, on=["facility", "day", "route"], how="left", indicator=True)
    m = m.merge(jobs.reset_index(), on=["facility", "day", "route"], how="left")

    print("=== ROUTE-NAME COVERAGE, CCP vs the duties file ===")
    win = du[du.day.astype(str).between(*WINDOW)]
    for site, t in m.groupby("facility"):
        hit = t._merge.eq("both")
        # every route the facility ran, not just the ones with a collection stop — this is the
        # route-name coverage question; the collection subset is the line below it
        all_r = set(ccp[ccp.facility.eq(site)].route.dropna())
        du_r = set(win[win.facility.eq(site)].route)
        ccp_r = set(t.route.dropna())
        print(f"\n  {site}")
        print(f"    route names   {len(all_r & du_r):4d} of {len(all_r):3d} CCP routes matched "
              f"({100 * len(all_r & du_r) / len(all_r):.0f}%) · {len(du_r - all_r):3d} duties-only"
              f"   (exact match, no normalisation)")
        print(f"    of which collect: {len(ccp_r & du_r)} of {len(ccp_r)} routes carrying a "
              f"Customer/Pickup stop")
        print(f"    stops         {hit.sum():5,} / {len(t):5,}  ({100 * hit.mean():5.1f}%)")
        print(f"    recorded EA   {t.loc[hit, 'Volumes Collected'].sum():8,.0f} / "
              f"{t['Volumes Collected'].sum():8,.0f}  "
              f"({100 * t.loc[hit, 'Volumes Collected'].sum() / t['Volumes Collected'].sum():5.1f}%)")
        print(f"    postcodes     {t.loc[hit, 'post_code'].nunique():5,} / {t.post_code.nunique():5,}")
        miss = t[~hit]
        if len(miss):
            print(f"    unmatched carry {miss['Volumes Collected'].sum():,.0f} EA over "
                  f"{miss.route.nunique()} routes — {sorted(miss.route.unique())[:4]}…")

    ok = m[m._merge.eq("both")].copy()
    ok["cont_per_stop"] = ok.cont / ok.ccp_jobs.replace(0, np.nan)

    print("\n=== VEHICLE TYPE on the stops that actually collect ===")
    for site, t in ok.groupby("facility"):
        print(f"\n  {site}   median {t.uld.median():.0f} ULD, range {t.uld.min():.0f}-{t.uld.max():.0f}")
        vc = t.veh.value_counts().head(5)
        for v, n in vc.items():
            print(f"    {n:5,} stops  {uld(v):3.0f} ULD  {v[:62]}")

    # ── postcode weights ─────────────────────────────────────────────────────────────
    cur = pd.read_csv(WEIGHTS)
    rows = []
    for site, t in ok.groupby("facility"):
        g = t.groupby("post_code", as_index=False).agg(
            m_stops=("addr", "size"), cont=("cont_per_stop", "sum"),
            uld_sum=("uld", "sum"), vol=("Volumes Collected", "sum"))
        base = cur[cur.facility_name.eq(site)][["facility_name", "post_code", "weight"]]
        j = base.merge(g, on="post_code", how="left")
        j = j.merge(coll[coll.facility.eq(site)].groupby("post_code").size().rename("stops"),
                    on="post_code", how="left")
        # a postcode with no matched route-day keeps its stop share, priced at this site's
        # median containers per stop — one cell at Melbourne Transport (3215, Geelong North)
        rate = t.cont_per_stop.median()
        j["imputed"] = j.cont.isna()
        j["cont"] = j.cont.fillna(j.stops * rate)
        j["w_cont"] = j.cont / j.cont.sum()
        j["facility_name"] = site
        rows.append(j)
        if j.imputed.any():
            print(f"\n  fallback at {site}: {int(j.imputed.sum())} postcode(s) with no matched "
                  f"route-day priced at {rate:.1f} containers/stop — "
                  f"{sorted(j.loc[j.imputed, 'post_code'])}")
    W = pd.concat(rows, ignore_index=True)

    print("\n=== WHICH WEIGHT REPRODUCES THE VOLUME CCP RECORDS? (share r / rank r) ===")
    print(f"  {'site':30s} {'stops':>14s} {'containers':>14s} {'ULD capacity':>14s}")
    for site, t in W.groupby("facility_name"):
        v = t[t.vol > 0]
        sh = lambda c: v[c] / v[c].sum()
        rk = lambda c: v[c].rank()
        print(f"  {site:30s} "
              f"{sh('stops').corr(sh('vol')):.3f}/{rk('stops').corr(rk('vol')):.3f}   "
              f"{sh('cont').corr(sh('vol')):.3f}/{rk('cont').corr(rk('vol')):.3f}   "
              f"{sh('uld_sum').corr(sh('vol')):.3f}/{rk('uld_sum').corr(rk('vol')):.3f}")

    W.to_csv(OUT, index=False)
    # a drop-in replacement: the five red-van sites keep their stop weights, the two
    # transports take container weights. Same 395 cells, same three columns.
    swap = cur.merge(W[["facility_name", "post_code", "w_cont"]],
                     on=["facility_name", "post_code"], how="left")
    swap["weight"] = swap.w_cont.fillna(swap.weight).round(8)
    bad = swap.groupby("facility_name").weight.sum().sub(1).abs().max()
    assert bad < 1e-6, f"weights do not sum to 1 within a site (off by {bad})"
    swap[["facility_name", "post_code", "weight"]].to_csv(OUT_W, index=False)
    vehicle_rates(ok)
    route_profile(ccp, du, coll)
    print(f"\nwrote {OUT.relative_to(ROOT)}          (per postcode, every measure)")
    print(f"wrote {OUT_W.relative_to(ROOT)}  (drop-in: transports on containers, PDCs on stops)")
    return W


if __name__ == "__main__":
    main()
