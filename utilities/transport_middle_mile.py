"""Where the transport facilities take what they pick up — each pickup paired with the next delivery.

THE RULE. For each route, walk its jobs in planned-time order. Every pickup is paired with the
FIRST delivery event that comes after it on the same route. Nothing more: 3 pickups then 1
delivery gives three rows all pointing at that delivery; 1 pickup then 2 deliveries gives one
row pointing at the first of them.

THE ORDER runs over the whole extract, not per job date, because the runs cross midnight — a
23:05 pickup is delivered at 00:40 under the next job date. Ties on planned time fall back to
the departure time, then file order.

EVERY PICKUP IS WRITTEN, from any kind of location, with the kind of both ends so the file can
be filtered: `network` is Location Type == Network minus the public lodgement points (LPO / RP /
locker / SPB, catchment_visit_weights.LODGE), `lodgement` is those, `customer` is the rest.
`pooled_route` marks a route that is at two different sites in the same planned minute
POOL_CLASH_MINUTES or more times in the week — seven Melbourne Transport ad-hoc routes
(MT_Network, MTN5015 NS, ...), which look like jobs from several trucks under one name, so
their "next delivery" may be another truck's. Flagged, not dropped.

THE MIDDLE MILE is a pair with `network` at BOTH ends (DT148 on 18 May: Dandenong Letters Centre
05:10 -> Mount Waverley DC 05:50). OUT_DEST counts those movements by facility, day and
destination, one movement per pickup.

    python utilities/transport_middle_mile.py
"""

import contextlib
import io
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utilities"))
import catchment_visit_weights as cvw                # noqa: E402

OUT = ROOT / "outputs/transport_pickup_next_delivery.csv"
OUT_DEST = ROOT / "outputs/transport_middle_mile_destinations.csv"   # network -> network only
POOL_CLASH_MINUTES = 5          # the pools score 5-93; MT502, a scheduled route, scores 3


def site_name(name):
    """One name per building: drop the access notes, which split a site over several strings."""
    return re.sub(r"\s*\([^)]*\bvia [^)]*\)", "", str(name), flags=re.I).strip()


def stop_kind(loc_type, loc_name):
    if loc_type != "Network":
        return "customer"
    return "lodgement" if re.search(cvw.LODGE, str(loc_name).upper()) else "network"


def main():
    with contextlib.redirect_stdout(io.StringIO()):
        w = cvw.load_ccp()
    t = w[w.facility.isin(cvw.TRANSPORTS)].copy()
    assert set(t.booking_type) <= {"Pickup", "Delivery"}, "a combined job type needs its own rule"
    t["when"] = t.d + pd.to_timedelta(t["Job Planned Time"].astype(str))
    t["dep"] = pd.to_datetime(t["Departure Datetime"], format="%d/%m/%Y %I:%M:%S %p",
                              errors="coerce")
    t["kind"] = [stop_kind(a, b) for a, b in zip(t.loc_type, t.loc_name)]
    t["site"] = t.loc_name.map(site_name)

    wk = t[t.day.astype(str).between(*cvw.WINDOW)]
    clash = (wk.groupby(["facility", "route", "day", "Job Planned Time"]).site.nunique().gt(1)
               .groupby(level=[0, 1]).sum())
    pooled = set(clash[clash >= POOL_CLASH_MINUTES].index)

    t = t.sort_values(["facility", "route", "when", "dep"], kind="stable").reset_index(drop=True)
    # the next delivery after each row, within its route: carry each delivery's details BACKWARD
    is_del = t.booking_type.eq("Delivery")
    nxt = t[["when", "site", "kind", "Booking Comments"]].where(is_del)
    nxt = nxt.groupby([t.facility, t.route]).shift(-1)     # strictly after this row
    nxt = nxt.groupby([t.facility, t.route]).bfill()

    p = t.booking_type.eq("Pickup") & t.day.astype(str).between(*cvw.WINDOW)
    out = pd.DataFrame({
        "facility": t.facility, "route": t.route, "route_status": t["Route Status"],
        "pooled_route": [(f, r) in pooled for f, r in zip(t.facility, t.route)],
        "pickup_day": t.day.astype(str), "pickup_time": t["when"].dt.strftime("%Y-%m-%d %H:%M"),
        "pickup_site": t.site, "pickup_kind": t.kind, "pickup_comment": t["Booking Comments"],
        "delivery_time": pd.to_datetime(nxt["when"]).dt.strftime("%Y-%m-%d %H:%M"),
        "delivery_site": nxt.site, "delivery_kind": nxt.kind,
        "delivery_comment": nxt["Booking Comments"]})[p]
    out["hours_to_delivery"] = ((pd.to_datetime(out.delivery_time)
                                 - pd.to_datetime(out.pickup_time)).dt.total_seconds() / 3600).round(2)
    for c in ["pickup_comment", "delivery_comment"]:
        out[c] = out[c].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    out.to_csv(OUT, index=False)

    # middle mile = network pickup -> network delivery; count each by where it is delivered
    mm = out[out.pickup_kind.eq("network") & out.delivery_kind.eq("network")]
    dest = (mm.groupby(["facility", "pickup_day", "delivery_site"]).size()
              .rename("movements").reset_index()
              .rename(columns={"pickup_day": "day", "delivery_site": "destination"}))
    dest["pct_of_day"] = (100 * dest.movements
                          / dest.groupby(["facility", "day"]).movements.transform("sum")).round(1)
    dest = dest.sort_values(["facility", "day", "movements", "destination"],
                            ascending=[True, True, False, True])
    dest.to_csv(OUT_DEST, index=False)

    # ── report ───────────────────────────────────────────────────────────────────────
    pd.set_option("display.width", 200)
    print(f"{len(out):,} pickups, Mon-Fri {cvw.WINDOW[0]}..{cvw.WINDOW[1]}, "
          f"{int(out.delivery_site.isna().sum())} with no later delivery on the route")
    print(f"pooled routes (flagged): {sorted(r for _, r in pooled)}")
    print(out.fillna({"delivery_kind": "none"})
             .groupby(["facility", "pickup_kind", "delivery_kind"]).size().unstack(fill_value=0)
             .to_string())
    print(f"\nmiddle-mile movements (network -> network) by day:")
    print(dest.pivot_table(index="facility", columns="day", values="movements",
                           aggfunc="sum").to_string())
    print(f"\nwrote {OUT.relative_to(ROOT)}\nwrote {OUT_DEST.relative_to(ROOT)}")
    return out


if __name__ == "__main__":
    main()
