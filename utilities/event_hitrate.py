"""Hit rate and trustworthiness of a scan event type. ACCEPT_FACILITY vs DELIVER.

    uv run python utilities/event_hitrate.py

Three questions, because "hit rate" alone does not decide whether an event is usable:
  1. COVERAGE     what share of articles carry at least one such scan
  2. CORROBORATION when the scan names a building, was the parcel physically worked there
  3. POSITION      does the event sit where its name implies, in the physical chain

Part 1 reads only the raw scan file. Parts 2-3 are the same file; the per-band breakdown
additionally needs the cached reduction from sankey_from_scans.py.
"""
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]          # the repo root, one up from utilities/
SCAN = REPO / "inputs" / "melbourne" / "all_scan_for_melbourne_pdc_20052026.csv"
CACHE = REPO / "outputs" / "melbourne_scan_path_analysis" / "consignment_paths.pkl"

EVENTS = ["ZPT_ACCEPT_FACILITY", "ZPT_DELIVER"]
# events that show the parcel was physically WORKED ON — acceptance is deliberately not one
HANDLED = ["ZPT_UNLOAD_ITEMS", "ZPT_DEPART_CONTAINER", "ZPT_LOAD_ITEM", "ZPT_TRANSFER",
           "ZPT_MACHINE_SORT"]
# the eight events ops supplied as evidence of presence, used for the chain and for POSITION
PHYSICAL = ["ZPT_LOAD_ITEM", "ZPT_LODGE", "ZPT_DEPART_CONTAINER", "ZPT_ACCEPT_FACILITY",
            "ZPT_UNLOAD_ITEMS", "ZPT_MACHINE_SORT", "ZPT_TRANSFER", "ZPT_DELIVER"]

df = pd.read_csv(
    SCAN,
    usecols=["Consignment_ID", "Event_type", "Event_facility_name", "Event_seq", "Article_count"],
    dtype={"Consignment_ID": str, "Event_type": "category", "Event_facility_name": "category"},
)
# one article count per consignment — the scan rows repeat it, so take the first
art = df.groupby("Consignment_ID", observed=True).Article_count.first()
total = art.sum()
print(f"{len(df):,} scans · {len(art):,} consignments · {total:,} articles\n")

# ── 1. COVERAGE ────────────────────────────────────────────────────────────────────────
cov = []
for ev in EVENTS:
    d = df[df.Event_type == ev]
    cons = d.Consignment_ID.unique()
    ea = art.reindex(cons).sum()
    cov.append({
        "event": ev.replace("ZPT_", ""),
        "scans": len(d),
        "consignments": len(cons),
        "articles": int(ea),
        "hit_rate": ea / total,
        "scans_per_cons": len(d) / len(cons),
        "buildings_per_cons": (d.groupby("Consignment_ID", observed=True)
                                .Event_facility_name.nunique().mean()),
    })
cov = pd.DataFrame(cov).set_index("event")
print("1. COVERAGE — what share of articles carry at least one such scan")
print(cov.assign(hit_rate=lambda x: (100 * x.hit_rate).round(2)).to_string(
    formatters={"scans_per_cons": "{:.2f}".format, "buildings_per_cons": "{:.2f}".format}))

# ── 2. CORROBORATION — is the named building one the parcel was actually worked in? ────
print("\n2. CORROBORATION — of the (consignment, building) pairs each event names,")
print("   how many also carry a physical handling scan at that same building?")
print("   NOTE this test only means something for an event claiming TRANSIT. DELIVER's low score")
print("   is not a warning: a van round is not an unload-container or a machine sort, so the")
print("   delivering depot is expected to carry no HANDLED scan. Read the ACCEPT row.")
worked = set(map(tuple, df[df.Event_type.isin(HANDLED)]
                 [["Consignment_ID", "Event_facility_name"]]
                 .drop_duplicates().itertuples(index=False, name=None)))
for ev in EVENTS:
    pairs = (df[df.Event_type == ev][["Consignment_ID", "Event_facility_name"]]
             .drop_duplicates())
    ok = np.fromiter((t in worked for t in pairs.itertuples(index=False, name=None)),
                     bool, len(pairs))
    pairs = pairs.assign(corroborated=ok, ea=pairs.Consignment_ID.map(art))
    print(f"   {ev.replace('ZPT_', ''):18s} {len(pairs):8,} pairs · "
          f"corroborated {pairs.corroborated.mean():6.1%} of pairs, "
          f"{pairs.loc[pairs.corroborated, 'ea'].sum() / pairs.ea.sum():.1%} by articles "
          f"· bare {(~pairs.corroborated).sum():,} pairs")

# ── 3. POSITION — does the event open or close the physical chain? ─────────────────────
ph = df[df.Event_type.isin(PHYSICAL)].sort_values(["Consignment_ID", "Event_seq"])
g = ph.groupby("Consignment_ID", observed=True).Event_type
first, last = g.first(), g.last()
print("\n3. POSITION — share of articles whose physical chain the event opens / closes")
for ev in EVENTS:
    o = art.reindex(first[first == ev].index).sum() / total
    c = art.reindex(last[last == ev].index).sum() / total
    print(f"   {ev.replace('ZPT_', ''):18s} opens {o:6.2%}   closes {c:6.2%}")

# ── per-band breakdown (needs the cached reduction) ────────────────────────────────────
if CACHE.exists():
    p = pd.read_pickle(CACHE).set_index("Consignment_ID")
    band = np.where(p.kept_on_site, "kept overnight",
                    np.where(p.origin == "INTERSTATE", "interstate", "Victoria same-day"))
    for ev in EVENTS:
        p[ev] = p.index.isin(df.loc[df.Event_type == ev, "Consignment_ID"].unique())
    rate = lambda g, ev: g.loc[g[ev], "articles"].sum() / g.articles.sum()
    print("\n4. WHERE THE MISSES ARE — hit rate by measured band, and by Melbourne sorts")
    for key, name in ((band, "band"), (p.nrounds.clip(upper=3), "sorts")):
        print(f"   by {name}:")
        for k, gg in p.groupby(key, observed=True):
            print(f"     {str(k):20s} " + "   ".join(
                f"{ev.replace('ZPT_', '')} {rate(gg, ev):6.1%}" for ev in EVENTS)
                + f"   ({gg.articles.sum():,} EA)")
else:
    print(f"\n(skipped the per-band breakdown — no {CACHE.name}; "
          f"run: uv run python plotting/sankey_from_scans.py --rebuild)")
