"""Why 22,813 articles on the originating page say "No delivery recorded".

    IN   inputs/originating_volume/originating_volume_scan_events.csv
         outputs/originating_volume_analysis/facility_state.json   (optional, for the state test)
    OUT  console

`sankey_facility_path_originating.py` bands a consignment by `Del_state`, and 11.1% of the
five-site extract has none. The band's name is WRONG for most of them, and this script is the
receipt: the population splits almost exactly in half, and only one half is actually undelivered.

  B — DELIVERED, BUT THE SCAN NAMES NO STATE. A ZPT_DELIVER event exists, at a real delivery
      point, sub-type Success. `sql/originating_volume_scan_events.sql` reads the destination as
      `MAX(IF(Event_type = 'ZPT_DELIVER', ev.Event_rec_state, NULL))` — off the DELIVER scan and
      nowhere else — so a blank REC_STATE on that one event erases the destination even though
      `Del_postcode` and `Terminating_facility_name` both survived. It is an address-field gap,
      not a delivery gap, and it is ~98% recoverable.

  A — NO ZPT_DELIVER EVENT AT ALL. The interesting half, and it is NOT mostly "still in transit":
      the great majority went silent DAYS before the trace window closed, and a large share had
      already reached a delivery point when they did. A parcel that reaches a PDEP and is never
      heard of again over the following week was delivered; what is missing is the scan, or the
      scan's join back to the consignment.

WHAT THE QUERY DOES THAT MAKES THIS WORSE ──────────────────────────────────────────
  * THE CONSIGNMENT KEY IS RECOMPUTED ON EVERY EVENT —
    `IF(REGEXP_CONTAINS(EH_TRACKID_2, r'\\d+'), EH_TRACKID_2, EH_TRACKID_1)` — while the seed set
    is keyed off the LODGE scans only. An event whose EH_TRACKID_2 is blank where the lodge scan's
    was populated keys to the ARTICLE instead of the consignment and is dropped by the INNER JOIN.
    That is the most likely mechanism behind the articles that reach a delivering depot and then
    go quiet, and it CANNOT BE TESTED FROM THIS EXPORT because the final SELECT carries neither
    `Article_ID` nor the raw trackids. Add them if this matters.
  * EVERY `Del_*` COLUMN IS AN INDEPENDENT `MAX()`. On a multi-article consignment with more than
    one delivery, `Del_date` may come from one article and `Del_postcode` from another; the block
    is not guaranteed to describe a single delivery. Small here (1.8% of consignments carry more
    than one article) but it is a correctness trap at any larger scale.
  * THE REPO SQL IS NOT THE QUERY THAT BUILT THESE FILES. The extract contains ZPT_REMOVE_AGGR,
    which is not in `trace_event_code_str`; and `work_centre_str` is committed EMPTY, so the file
    as it stands returns nothing. It is a per-site template, and a stale one.

Run:  uv run python utilities/analyse_missing_deliver.py
"""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "inputs" / "originating_volume" / "originating_volume_scan_events.csv"
STATE = HERE / "outputs" / "originating_volume_analysis" / "facility_state.json"

# the event types the committed SQL asks for — the file is checked against this
SQL_EVENTS = {"ZPT_LODGE", "ZPT_ACCEPT_FACILITY", "ZPT_UNLOAD_ITEMS", "ZPT_MACHINE_SORT",
              "ZPT_TRANSFER", "ZPT_LOAD_ITEM", "ZPT_DEPART_CONTAINER", "ZPT_DELIVER"}

# a building where a parcel is handed over, as against one where it is sorted. The split matters:
# a trace that stops at a PDEP has arrived, a trace that stops at a PC has not.
HANDOVER = {"PDEP", "DC", "LPO", "CPA", "POBA", "RP", "TDEP", "BC", "CDC"}

# the standard postcode allocation — the ACT ranges are carved out of NSW's, so they come first
PC_STATE = [(200, 299, "Australian Capital Territory"), (2600, 2618, "Australian Capital Territory"),
            (2900, 2920, "Australian Capital Territory"), (800, 999, "Northern Territory"),
            (1000, 2599, "New South Wales"), (2619, 2899, "New South Wales"),
            (2921, 2999, "New South Wales"), (3000, 3999, "Victoria"), (8000, 8999, "Victoria"),
            (4000, 4999, "Queensland"), (9000, 9999, "Queensland"),
            (5000, 5999, "South Australia"), (6000, 6999, "Western Australia"),
            (7000, 7999, "Tasmania")]

COLS = ["Consignment_ID", "Events_traced", "Event_type", "Event_date", "Event_local_time",
        "Event_facility_name", "Event_work_centre_type", "Del_state", "Del_postcode",
        "Del_suburb", "Terminating_facility_name", "Product_type", "Article_count", "Origin_site"]


def pc_state(p):
    try:
        n = int(p)
    except (TypeError, ValueError):
        return None
    return next((s for lo, hi, s in PC_STATE if lo <= n <= hi), None)


def ea(v):
    return f"{int(round(v)):,}"


def head(t):
    print(f"\n══ {t} " + "═" * max(0, 74 - len(t)))


def split(d):
    """(all consignments, never-delivered, delivered-but-stateless) — one row per consignment."""
    c = d.groupby("Consignment_ID").agg(
        art=("Article_count", "first"), st=("Del_state", "first"),
        pc=("Del_postcode", "first"), sb=("Del_suburb", "first"),
        term=("Terminating_facility_name", "first"), site=("Origin_site", "first"),
        prod=("Product_type", "first"), n=("Events_traced", "first"))
    c["hasdel"] = c.index.isin(set(d.loc[d.Event_type == "ZPT_DELIVER", "Consignment_ID"]))
    return c, c[~c.hasdel].copy(), c[c.hasdel & c.st.isna()].copy()


def main():
    if not SRC.exists():
        sys.exit(f"missing {SRC} — run utilities/extract_originating_event.py first")
    print(f"  reading {SRC.name} ({SRC.stat().st_size / 1e6:.0f} MB)")
    d = pd.read_csv(SRC, usecols=COLS, dtype={"Consignment_ID": str}, low_memory=False)
    d["t"] = pd.to_datetime(d.Event_date + " " + d.Event_local_time.astype(str), errors="coerce")
    state = json.loads(STATE.read_text()) if STATE.exists() else {}

    c, A, B = split(d)
    day, nodest = c.art.sum(), c.loc[c.st.isna(), "art"].sum()

    head("TWO POPULATIONS, AND ONLY ONE OF THEM IS STILL A BAND")
    print(f"  {ea(day)} articles, of which {ea(nodest)} ({100 * nodest / day:.1f}%) carry no "
          "Del_state.\n  Since the page was rebased on the DELIVER event's own coordinate "
          "(2026-09-04) that is NO LONGER\n  one band — B is placed by its coordinate and only A "
          "is drawn as unknown:\n")
    print(f"    A  no ZPT_DELIVER event at all     {ea(A.art.sum()):>9}  "
          f"({len(A):,} consignments)  <- the page's NEVER SCANNED AS DELIVERED band")
    print(f"    B  delivered, but Del_state null   {ea(B.art.sum()):>9}  "
          f"({len(B):,} consignments)  <- banded by coordinate, NOT lost")
    print(f"\n  window observed: {d.Event_date.min()} → {d.Event_date.max()}"
          f"  ({pd.to_datetime(d.Event_date).nunique()} days)")

    # ── B ────────────────────────────────────────────────────────────────────────────
    head("B — DELIVERED, BUT THE DELIVER SCAN NAMES NO STATE")
    wc = d[d.Event_type == "ZPT_DELIVER"].groupby("Consignment_ID").Event_work_centre_type.first()
    print("  where they were delivered (work-centre type of the terminating site):")
    for k, v in B.assign(w=wc.reindex(B.index)).groupby("w", dropna=False).art.sum() \
                 .sort_values(ascending=False).head(8).items():
        print(f"    {str(k):<8} {ea(v):>8}")
    print(f"\n  the address fields fail INDEPENDENTLY — state is null on all "
          f"{len(B):,} consignments, but")
    print(f"    postcode survives on {(B.pc.notna()).sum():,}   "
          f"suburb survives on {(B.sb.notna()).sum():,}   "
          f"terminating facility survives on {(B.term.notna()).sum():,}")

    B["pcst"] = B.pc.map(pc_state)
    B["termst"] = B.term.map(state)
    both = B[B.pcst.notna() & B.termst.notna()]
    agree = both.loc[both.pcst == both.termst, "art"].sum()
    best = B.pcst.fillna(B.termst)
    rec = B.loc[best.notna(), "art"].sum()
    print(f"\n  RECOVERABLE. Postcode first, then the terminating building's own state:")
    print(f"    from Del_postcode          {ea(B.loc[B.pcst.notna(), 'art'].sum()):>9}")
    print(f"    from the terminating site  {ea(B.loc[B.termst.notna(), 'art'].sum()):>9}")
    print(f"    from either                {ea(rec):>9}  ({100 * rec / B.art.sum():.1f}% of B)")
    print(f"    from neither               {ea(B.art.sum() - rec):>9}")
    if len(both):
        print(f"    where both are known they agree on {ea(agree)} of {ea(both.art.sum())} "
              f"({100 * agree / both.art.sum():.1f}%)")
    print(f"\n  recovered destination:")
    for k, v in B.assign(b=best).groupby("b").art.sum().sort_values(ascending=False).items():
        print(f"    {k:<30} {ea(v):>8}")

    # ── A ────────────────────────────────────────────────────────────────────────────
    head("A — NO DELIVER EVENT: WHERE THE TRACE STOPS, AND HOW LONG AGO")
    last = d[d.Consignment_ID.isin(A.index)].sort_values("t").groupby("Consignment_ID").last()
    A["lastev"], A["lastfac"] = last.Event_type, last.Event_facility_name
    A["lastd"], A["lastwc"] = last.Event_date, last.Event_work_centre_type
    edge = pd.to_datetime(d.Event_date).max()
    A["silence"] = (edge - pd.to_datetime(A.lastd)).dt.days

    print(f"  days between the LAST scan and the window edge ({edge:%-d %b}):")
    run = 0
    for k, v in A.groupby("silence").art.sum().sort_index(ascending=False).items():
        run += v
        print(f"    {k:>2}d  {ea(v):>7}   cumulative {ea(run):>7}  ({100 * run / A.art.sum():4.1f}%)")
    quiet = A.loc[A.silence > 2, "art"].sum()
    print(f"\n  {ea(quiet)} ({100 * quiet / A.art.sum():.1f}%) had been silent for MORE THAN TWO "
          "DAYS when the window closed.\n  These were not in transit; the trace simply ends.")

    arrived = A.lastwc.isin(HANDOVER)
    print(f"\n  and where it ends:")
    print(f"    already at a delivery point  {ea(A.loc[arrived, 'art'].sum()):>8}  "
          f"({100 * A.loc[arrived, 'art'].sum() / A.art.sum():.1f}%) — "
          f"{ea(A.loc[arrived & (A.silence > 2), 'art'].sum())} of them silent 3+ days, so "
          "delivered with the scan missing")
    print(f"    still in the sort network    {ea(A.loc[~arrived, 'art'].sum()):>8}  "
          f"({100 * A.loc[~arrived, 'art'].sum() / A.art.sum():.1f}%)")
    for k, v in A.groupby(A.lastwc.fillna("?")).art.sum().sort_values(ascending=False).head(8).items():
        print(f"      {k:<7} {ea(v):>7}  {'handover' if k in HANDOVER else 'sort'}")

    gw = A.lastfac.fillna("").str.contains("GATEWAY")
    print(f"\n  left the country: {ea(A.loc[gw, 'art'].sum())} last seen at a gateway facility "
          "— there is no domestic delivery scan to find")

    print(f"  last event type: " + ", ".join(
        f"{k.replace('ZPT_', '')} {ea(v)}"
        for k, v in A.groupby("lastev").art.sum().sort_values(ascending=False).head(5).items()))
    print(f"\n  it is a PARCEL POST phenomenon: " + ", ".join(
        f"{k} {ea(v)} ({100 * v / c.groupby('prod').art.sum()[k]:.1f}% of that product)"
        for k, v in A.groupby("prod").art.sum().items()))
    print(f"\n  NOT a multi-article effect: {A.art.mean():.3f} articles per consignment here "
          f"against {c.art.mean():.3f} overall")

    # ── BY SITE — which depot is losing the delivery scan ────────────────────────────
    # `gateway` and `arrived` are the SAME definitions as above (last seen at), not "ever touched":
    # a parcel that passes through a gateway mid-journey has not left the country.
    if A.site.notna().any():
        head("A — NEVER SCANNED AS DELIVERED, BY ORIGIN SITE")
        tot = c.groupby("site").art.sum()
        sub = A.groupby("site").art.sum()
        g = A[gw].groupby("site").art.sum()
        dep = A[arrived].groupby("site").art.sum()
        print(f"  {'site':<22}{'never delivered':>16}{'of':>10}{'rate':>8}"
              f"{'at a depot':>12}{'gateway':>9}")
        for k in tot.sort_values(ascending=False).index:
            print(f"  {k:<22}{sub.get(k, 0):>16,}{tot[k]:>10,}"
                  f"{100 * sub.get(k, 0) / tot[k]:>7.1f}%{dep.get(k, 0):>12,}{g.get(k, 0):>9,}")
        print(f"  {'TOTAL':<22}{A.art.sum():>16,}{tot.sum():>10,}"
              f"{100 * A.art.sum() / tot.sum():>7.1f}%"
              f"{A.loc[arrived, 'art'].sum():>12,}{A.loc[gw, 'art'].sum():>9,}")

    # ── the query itself ─────────────────────────────────────────────────────────────
    head("THE EXTRACT AGAINST THE COMMITTED SQL")
    extra = sorted(set(d.Event_type.unique()) - SQL_EVENTS)
    missing = sorted(SQL_EVENTS - set(d.Event_type.unique()))
    print(f"  event types in the file but NOT in trace_event_code_str: "
          f"{', '.join(extra) if extra else 'none'}")
    print(f"  asked for but absent: {', '.join(missing) if missing else 'none'}")
    if extra:
        print("  → sql/originating_volume_scan_events.sql is NOT the revision that built "
              "these files")

    head("WHAT WOULD FIX IT")
    print(f"  1. IN THE QUERY, coalesce the destination instead of reading it off the DELIVER")
    print(f"     scan alone. The PAGE already works around this by reading that event's own")
    print(f"     coordinate, so B is not lost there — but every other consumer of this extract")
    print(f"     still sees {ea(B.art.sum())} EA with a null Del_state, and {ea(rec)} of those")
    print(f"     are recoverable from the postcode or the terminating building alone.")
    print(f"  2. Carry Article_ID / the raw trackids into the SELECT, so the "
          f"{ea(A.loc[arrived & (A.silence > 2), 'art'].sum())} EA that reach a depot and go")
    print(f"     quiet can be tested against the consignment-key hypothesis.")
    print(f"  3. Extending trace_lookahead_days buys little: only "
          f"{ea(A.loc[A.silence <= 2, 'art'].sum())} EA were still moving at the edge.")


if __name__ == "__main__":
    main()
