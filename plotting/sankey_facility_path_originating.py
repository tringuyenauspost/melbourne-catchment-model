"""The itinerary page, drawn from ORIGINATING volume — inputs/originating_volume/.

    IN   inputs/originating_volume/MELBOUNRE_ORIGINATING_VOL.csv (one file, every event type)
    OUT  outputs/originating_volume_analysis/sankey-facility-path-originating.html

The third sibling. `sankey_facility_path.py` holds every rule about what the diagram MEANS — what
a touch is, how two names for one building fold, how the columns and the fold dial work — and this
module says what the source IS and where it must depart from the other two.

IT IS THE OTHER DIRECTION, AND THAT CHANGES TWO RULES ────────────────────────────────
The two existing pages read TERMINATING volume: freight that arrives in the Melbourne catchment
and is delivered here. Their journey therefore starts at the state border ("only Victorian
buildings are drawn") and ends at the delivering depot. This extract is the mirror image — it is
what Bayswater LODGED, 13,154 consignments over 11-12 May 2026 — and 55% of it is delivered in
another state. So:

  * THE VICTORIA CUT IS OFF (user, 2026-08-31). `export_chain2_factors.path_chain` drops every
    non-Victorian building, which on the terminating pages costs 0.69 touches an article and on
    this one would delete the entire second half of the journey: 8,064 of the 8,333
    interstate-destined consignments are physically scanned outside Victoria, at the Sydney,
    Brisbane, Adelaide and Perth parcel facilities. `origin_chain` below is that function with the
    one line removed, patched over `PAGE.path_chain` — the reduction is NOT edited, because the
    model reads it and the model is still a Victorian model.
  * THE LAST COLUMN IS A DESTINATION, NOT A DEPOT. A Victorian delivery names the site that
    delivered it and the band says whether that was inside metro or out in the regions; anything
    else is one node — Interstate, or International. That is the shape asked for, and it is the
    only shape the data supports evenly: the Victorian tail is 293 named sites, the interstate one
    is a thousand buildings this catchment has no opinion about.

WHAT ELSE IS DIFFERENT ──────────────────────────────────────────────────────────────
  * ONE CSV, not eight. Every event type is in the same file, so there is no per-type
    concatenation and — measured, not assumed — `Event_seq` IS chronological here: all 13,154
    consignments come out in the same order by seq as by timestamp. The chain is still built on
    the TIMESTAMP, because that is what the page means by order and a second extract agreeing
    with itself is not a reason to stop measuring.
  * THE JOURNEY IS THREE TIMES LONGER. 5.46 buildings a consignment before the name fold and the
    cut, 3.41 drawn, against 1.69 on 20 May — so the default depth is 5 rather than 2 and five
    columns hold 89.9% of articles end to end. Interstate freight is most of the volume and it is
    the long half of the distribution.
  * THE EVIDENCE BAR IS `physical`, NOT `entry` (2026-08-31). This is the one dial where matching
    the siblings would have been the wrong answer, and it is worth saying why. The chain is cut at
    the parcel's last arrival at the site that delivered it, so that site has to be ON the chain —
    and at an interstate depot the only scans this extract carries are usually ZPT_DELIVER and
    ZPT_UNLOAD_ITEMS, neither of which the `entry` bar admits. On `entry` the delivering site is
    missing from 62.2% of journeys and the far end of the itinerary simply is not drawn; on
    `physical` that falls to 7.3%. The siblings default to `entry` because the model reads
    entry-type events; this page is not feeding the model, it is asking how far the freight went.
  * A FACILITY GETS A REAL STATE. The other two pages answer Victoria-or-not and badge every
    interstate building "?"; here that would be more than half the nodes. `facility_state` reads
    a state off the building's own median coordinate and the console prints its measured
    disagreement rate against the 2,235 buildings this extract names a delivery state for.

Run:  uv run python plotting/sankey_facility_path_originating.py
      uv run python plotting/sankey_facility_path_originating.py --rebuild
      uv run python plotting/sankey_facility_path_originating.py --depth 6
      uv run python plotting/sankey_facility_path_originating.py --touch entry     # the siblings' bar
      uv run python plotting/sankey_facility_path_originating.py --dest class   # 5 nodes, no sites
      uv run python plotting/sankey_facility_path_originating.py --vic catchment
      uv run python plotting/sankey_facility_path_originating.py --sites redvan
      uv run python plotting/sankey_facility_path_originating.py --sites transport
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))               # ad-hoc tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the reduction lives there
import argparse
import collections
import json

import pandas as pd

from export_chain2_factors import (
    EVIDENCE, VAN_SUFFIX, Z_DEPOT, Z_INTER, Z_NOSCAN, Z_OUTSIDE, alias,
)
import sankey_facility_path as PAGE                # the page module, for the dials it owns
from sankey_facility_path import (
    KEEP, build, learn, report, top_after, write_page,
)

HERE = Path(__file__).resolve().parents[1]
SRC = HERE / "inputs" / "originating_volume" / "MELBOUNRE_ORIGINATING_VOL.csv"
OUT = HERE / "outputs" / "originating_volume_analysis"
TARGET = OUT / "sankey-facility-path-originating.html"
CACHE = OUT / "originating_rows.pkl"
# Bump when `load_rows` changes what it returns — the COLUMNS or the VALUES in them. Not just the
# shape: `dest` carries node LABELS, so renaming OTHER_SITES without bumping this left the page
# drawing the old label out of the pickle while the console printed the new one from the constant.
SCHEMA = 7
STATE_JSON = OUT / "facility_state.json"

DEPTH = 5                     # the drawn journey is 3.41 buildings — see the header note
TOUCH = "physical"            # NOT the siblings' `entry` — see the header note
DEST_KEEP = 40                # destination nodes named before the fold
PRODUCT = {"Parcel Post": "PP", "Express Post": "EP", "Letters": "LT"}
PRODUCT_LABEL = [("PP", "Parcel Post"), ("EP", "Express Post"), ("LT", "Letters")]

# ── the destination bands, which are also the page's filter ──────────────────────────
# On the terminating pages the band is where the freight came FROM, because that is the thing a
# delivery-side reader cannot see in the columns. Here the columns already start at the lodgement
# site — there is only one — so the band that earns its place is where the freight WENT. It is
# deliberately the same vocabulary as the last column, so filtering to INTERSTATE and reading the
# touch columns answers "which buildings does our interstate freight actually run through", which
# is the question this extract exists to answer.
# THE BAND IS READ OFF THE DELIVERY COORDINATE, NOT OFF `Del_state` (2026-09-04).
# `sql/originating_volume_scan_events.sql` takes the destination as
# `MAX(IF(Event_type = 'ZPT_DELIVER', ev.Event_rec_state, NULL))` — off the DELIVER scan's address
# block and nowhere else — and that block is blank on 12,090 EA that WERE delivered, at a real
# handover point, sub-type Success. Banding those "no delivery recorded" was simply wrong. The
# coordinate on the same event survives where the address text does not (the query COALESCEs it to
# the work centre's own geo and then to ADAM), so `au_state` on that point is both a better basis
# and a more honest one: it is measured, and it agrees with `Del_state` on 98.2% of the volume
# where both exist. `Del_state` is kept only as a FALLBACK for the 1,019 EA with no coordinate —
# almost all of them overseas region codes, which no Australian coordinate test could ever find.
#
# AND THE OLD `NODEST` WAS TWO DIFFERENT THINGS, so it is now two bands. The distinction is the
# whole finding of `utilities/analyse_missing_deliver.py`: one half was never scanned as
# delivered, the other half was delivered and simply carries no location. Folding them together
# hid the fact that 91.6% of the first half had gone silent for three days or more before the
# trace window closed — they are not in transit, the scan is missing.
# where the DELIVER event names no building. It is not "unknown" — the parcel was handed over,
# the scan just does not say by whom — so it is a named node in the destination column rather than
# a hole, and both the located and the unlocated version of that land on the same node.
#
# IT MAY NOT BE CALLED "Other sites". The destination column ALREADY has a node by that name: the
# page module folds every delivering site too small to name into one residual labelled
# "Other " + destword + "s", which on this page is "Other sites" and carries 67,933 EA across
# 1,349 buildings. Two nodes, one column, the same words, 29x apart in size and meaning entirely
# different things — one is "we know the site, it is just small", the other is "the scan does not
# say". The residual's label is shared machinery (it reads "Other depots" on the terminating
# pages), so the one to rename is this one.
OTHER_SITES = "Site not recorded"
BAND_ORDER = ["VIC_METRO", "VIC_REGION", "INTERSTATE", "INTERNATIONAL", "NOWHERE", "NOSCAN"]
BAND_LABEL = {"VIC_METRO": "Victoria — metro", "VIC_REGION": "Victoria — regional",
              "INTERSTATE": "Interstate", "INTERNATIONAL": "International",
              "NOWHERE": "Delivered, location not recorded",
              "NOSCAN": "Never scanned as delivered"}
DEST_NODE = {"INTERSTATE": "Interstate", "INTERNATIONAL": "International",
             "NOWHERE": OTHER_SITES, "NOSCAN": "Never scanned as delivered"}
AU_STATES = ["VIC", "NSW", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

# Melbourne metro, as postcodes. THIS IS NOT THE CATCHMENT POLYGON, deliberately. The polygon
# `analyse_new_scans.metro_flag` tests is the first-mile ROUTE footprint — it reaches Geelong,
# Bacchus Marsh, Kyneton and Woodend, and the repo's own note says a page built on it should say
# catchment rather than metro. The question here is about a DELIVERY address, which the collection
# footprint has no authority over, so the default is the postcode definition of Melbourne metro and
# `--vic catchment` is the opt-in for the other one. Ranges are the standard metro allocation.
METRO_PC = [(3000, 3207), (3335, 3341), (3427, 3442), (3750, 3810),
            (3910, 3920), (3926, 3944), (3975, 3978), (3980, 3980), (8000, 8999)]


# ══ STATE FROM A COORDINATE ══════════════════════════════════════════════════════════
# The other two pages need Victoria-or-not and get it from a rectangle. This page draws buildings
# in every state and puts a state code on each node, so it needs the real answer. There is no
# state boundary file in the repo, so the borders are encoded directly — which is less of a
# liberty than it sounds, because Australia's are mostly meridians and parallels: 129°E splits WA
# from NT and SA, 26°S splits NT from SA, 141°E is SA's whole eastern edge, and 29°S is most of
# the Queensland–NSW line. The one border that is a river is the Murray, and it is interpolated
# through eight points, good to about 30 km — which matters only for a building standing on the
# bank, and there are none.
#
# IT IS CHECKED RATHER THAN ASSERTED. 2,235 of the buildings in this extract are the terminating
# facility for some consignment, and those consignments carry a delivery state — so the classifier
# has ground truth for most of the node set and `facility_state` prints how often it disagrees.
MURRAY = [(141.00, -34.05), (142.00, -34.20), (143.00, -35.35), (144.00, -35.90),
          (145.00, -35.95), (146.00, -36.05), (147.00, -36.10), (148.19, -36.13),
          (149.98, -37.50)]     # the last leg is the straight run from the source to Cape Howe
ACT_BOX = dict(lon=(148.76, 149.40), lat=(-35.92, -35.12))
CODE_NAME = {"VIC": "Victoria", "NSW": "New South Wales", "QLD": "Queensland",
             "SA": "South Australia", "WA": "Western Australia", "TAS": "Tasmania",
             "ACT": "Australian Capital Territory", "NT": "Northern Territory"}


def _murray_lat(lon):
    """The VIC/NSW border latitude at this longitude — north of it is NSW, south is Victoria."""
    if lon <= MURRAY[0][0]:
        return MURRAY[0][1]
    for (x0, y0), (x1, y1) in zip(MURRAY, MURRAY[1:]):
        if lon <= x1:
            return y0 + (y1 - y0) * (lon - x0) / (x1 - x0)
    return MURRAY[-1][1]


def au_state(lat, lon):
    """State code for a point, or None where it is outside Australia or has no coordinate."""
    if pd.isna(lat) or pd.isna(lon):
        return None
    if not (-44.0 <= lat <= -9.0 and 112.0 <= lon <= 154.5):
        return None                                   # not in the country at all
    if lat < -39.2:
        return "TAS" if 143.5 < lon < 149.0 else None
    if lon < 129.0:
        return "WA"
    if lon < 141.0:
        if lat > -26.0:
            return "NT" if lon < 138.0 else "QLD"
        return "SA"
    if lat > -29.0:
        return "QLD"
    if ACT_BOX["lon"][0] <= lon <= ACT_BOX["lon"][1] and ACT_BOX["lat"][0] <= lat <= ACT_BOX["lat"][1]:
        return "ACT"
    return "NSW" if lat > _murray_lat(lon) else "VIC"


def facility_state(d, rebuild=False):
    """Facility name -> full state name, from its own median event coordinate, cached and checked.

    Full names because `sankey_facility_path.STATE_CODE` keys on them; a building with no usable
    coordinate is left out of the map entirely and the page badges it "?", which is the honest
    answer rather than a guessed one.
    """
    if STATE_JSON.exists() and not rebuild:
        return json.loads(STATE_JSON.read_text())
    g = (d.dropna(subset=["Event_facility_name"])
          .groupby("Event_facility_name")[["Event_Lat", "Event_Long"]].median())
    code = {n: au_state(r.Event_Lat, r.Event_Long) for n, r in g.iterrows()}

    # the check — every building this extract also names as a terminating facility carries a
    # delivery state, so the classifier can be scored instead of trusted
    truth = (d.dropna(subset=["Terminating_facility_name", "Del_state"])
              .groupby("Consignment_ID")
              .agg(f=("Terminating_facility_name", "first"), s=("Del_state", "first")))
    truth = truth[truth.s.isin(AU_STATES)].groupby("f").s.agg(lambda s: s.value_counts().idxmax())
    both = [(code[n], truth[n]) for n in truth.index if code.get(n)]
    agree = sum(a == b for a, b in both)
    print(f"  state classifier checked on {len(both):,} buildings the extract names a delivery "
          f"state for: {agree / len(both):.1%} agree")
    if agree < len(both):
        bad = collections.Counter(f"{a}→{b}" for a, b in both if a != b)
        print("    disagreements: " + ", ".join(f"{k} ×{v}" for k, v in bad.most_common(6)))

    out = {n: CODE_NAME[c] for n, c in code.items() if c}
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(out, sort_keys=True))
    print(f"  ✓ {len(code):,} facilities, {len(out):,} placed in an Australian state")
    return out


# ══ THE JOURNEY IS NOT CUT AT THE BORDER ═════════════════════════════════════════════
# `export_chain2_factors.path_chain` with one line removed — the `state.get(x) == "Victoria"`
# filter. Everything else is identical and deliberately so: the name fold runs first, the chain is
# cut at the LAST arrival at the delivering site, repeats collapse after the fold, and the same
# seven diagnostics come back so the page's footer counts the same things. `before` (buildings
# dropped for being interstate) is 0 by construction now, which is what "nothing was cut" should
# read as, and the footer's "arrived through another state" tile correctly goes to zero.
#
# It is PATCHED OVER the reduction's copy rather than edited into it. The model reads `path_chain`
# and the model is a Victorian model; an originating page is not a reason to change what the
# exporter means by an itinerary.
def origin_chain(raw, own, state, group=None):
    """One parcel's itinerary: every building it was in before it reached its delivering site."""
    chain = []
    for name in raw:
        name = alias(name)
        if not chain or chain[-1] != name:
            chain.append(name)
    idx = [i for i, f in enumerate(chain) if f in own]
    ch = chain[:idx[-1]] if idx else list(chain)      # cut at the LAST arrival at its own site
    seen = len(ch)
    keep = list(ch)
    if group is not None:
        keep = [x for x in keep if x in group]
    fell = seen - len(keep)
    cut = len(keep)
    keep = [x for i, x in enumerate(keep) if not i or keep[i - 1] != x]
    revisit = cut - len(keep)
    oidx = [i for i, x in enumerate(raw) if alias(x) in own]
    old = list(raw[:oidx[-1]] if oidx else raw)
    why = (0 if keep else
           Z_OUTSIDE if seen else
           Z_INTER if ch else
           Z_DEPOT if idx else
           Z_NOSCAN)
    return (keep, 0, (len(chain) - 1 - idx[-1] if idx else 0),
            len(old) - seen, bool(old) and old[0].endswith(VAN_SUFFIX), fell, why, revisit)


# ── the source ───────────────────────────────────────────────────────────────────────
def metro_pc(pc):
    """Is this a Melbourne metro postcode? See METRO_PC — a delivery-address question."""
    if pd.isna(pc):
        return False
    try:
        n = int(float(pc))
    except (TypeError, ValueError):
        return False
    return any(lo <= n <= hi for lo, hi in METRO_PC)


def load_rows(vic_basis="postcode", bar="entry", rebuild=False, noscan="last"):
    """One row per consignment: articles, product, destination band and node, chain, own.

    The whole reduction, and it is small enough to be honest in one function: this extract is one
    file, 125,303 events, and every question the page asks of it is a groupby. The chain is built
    on the TIMESTAMP (see the header) over the chosen evidence bar.
    """
    if CACHE.exists() and not rebuild:
        cached = pd.read_pickle(CACHE)
        # SCHEMA as well as basis. A cache written before a column existed is not "wrong basis",
        # it is a KeyError three functions downstream, and the run that produced it looked clean.
        if (cached.attrs.get("vic") == vic_basis and cached.attrs.get("bar") == bar
                and cached.attrs.get("noscan") == noscan
                and cached.attrs.get("schema") == SCHEMA):
            return cached
        print("  the cache was built on another basis or schema — rebuilding it")
    print(f"  reading {SRC.name} ({SRC.stat().st_size / 1e6:.0f} MB)")
    d = pd.read_csv(SRC, dtype={"Consignment_ID": str}, low_memory=False)
    d["t"] = pd.to_datetime(d.Event_date.astype(str) + " " + d.Event_local_time.astype(str),
                            errors="coerce")

    # the chain — evidence bar, time order, consecutive repeats collapsed. Names stay RAW:
    # `origin_chain` folds them itself and needs the raw form to spot a van arm.
    e = d[d.Event_type.isin(EVIDENCE[bar]) & d.Event_facility_name.notna()].sort_values(
        ["Consignment_ID", "t"], kind="stable")
    keep = ((e.Consignment_ID != e.Consignment_ID.shift())
            | (e.Event_facility_name != e.Event_facility_name.shift()))
    chain = e[keep].groupby("Consignment_ID").Event_facility_name.apply(list)

    agg = dict(
        articles=("Article_count", "first"), Product_type=("Product_type", "first"),
        term=("Terminating_facility_name", "first"), st=("Del_state", "first"),
        pc=("Del_postcode", "first"), lat=("Del_Lat", "first"), lon=("Del_Long", "first"))
    # `Origin_site` exists only on the five-site merge that
    # `utilities/extract_originating_event.py` writes; the single-site file has no such column and
    # the page simply does not print the per-site section for it.
    if "Origin_site" in d.columns:
        agg["site"] = ("Origin_site", "first")
    c = d.groupby("Consignment_ID").agg(**agg)

    # ── THE DELIVERY EVENT ITSELF ────────────────────────────────────────────────────
    # Not the `Del_*` block. Every one of those columns is an independent `MAX()` over the
    # consignment's DELIVER events, so on a multi-article consignment the date can come from one
    # article and the postcode from another; taking the LAST delivery scan by timestamp gives one
    # event whose coordinate and building name describe the same handover. It also settles the
    # 346 EA where `Terminating_facility_name` and the last DELIVER's own building disagree.
    dl = (d[d.Event_type == "ZPT_DELIVER"].sort_values(["Consignment_ID", "t"], kind="stable")
           .groupby("Consignment_ID").last())
    c["dfac"] = dl.Event_facility_name.reindex(c.index)
    c["dlat"] = dl.Event_Lat.reindex(c.index)
    c["dlon"] = dl.Event_Long.reindex(c.index)
    delivered = c.index.isin(dl.index)

    # ── AND, FOR THE ONES THAT NEVER GOT ONE, THE LAST PLACE ANYONE SAW IT ───────────
    # `--noscan last` (the default) gives the 17,477 articles with no ZPT_DELIVER a position
    # instead of a dead-end node: the last building on the evidence bar, with its own coordinate.
    # It is the best-known position, and the page must not pretend it is more than that —
    # THE BAND STAYS `NOSCAN`. Band and destination are independent dimensions here, so the
    # column can say where the parcel got to while the band keeps saying nobody scanned it as
    # delivered, and the reader can still filter the whole population in or out. Only 38.9% of it
    # stops at a building that delivers anything at all; the other 61.1% stops in the sort
    # network, and drawing a hub as somebody's "destination" without that caveat would be a lie.
    lastseen = e[keep].groupby("Consignment_ID").last() if noscan == "last" else None
    if lastseen is not None:
        gap = ~delivered
        c.loc[gap, "dfac"] = lastseen.Event_facility_name.reindex(c.index)[gap]
        c.loc[gap, "dlat"] = lastseen.Event_Lat.reindex(c.index)[gap]
        c.loc[gap, "dlon"] = lastseen.Event_Long.reindex(c.index)[gap]

    # ── STATE, FROM THE COORDINATE ───────────────────────────────────────────────────
    cst = pd.Series([au_state(a, b) for a, b in zip(c.dlat, c.dlon)], index=c.index)
    by_coord = cst.notna()                         # the coordinate answered — the normal case
    fell_back = cst.isna() & c.st.notna()          # `Del_state` picks up what no coordinate can
    off_shore = cst.isna() & c.st.isna() & c.dlat.notna()   # a point, and it is not in Australia
    cst[fell_back & c.st.isin(AU_STATES)] = c.st[fell_back & c.st.isin(AU_STATES)]
    overseas = (fell_back & ~c.st.isin(AU_STATES)) | off_shore

    # metro/region: the postcode where the extract still has one, the catchment polygon on the
    # delivery point where it does not — the same fallback logic as the state, one level down
    if vic_basis == "catchment":
        from analyse_new_scans import metro_flag
        inside = metro_flag(c.dlon, c.dlat).fillna(False).astype(bool)
    else:
        from analyse_new_scans import metro_flag
        poly = metro_flag(c.dlon, c.dlat).fillna(False).astype(bool)
        inside = c.pc.map(metro_pc).where(c.pc.notna(), poly)

    # the geography bands describe a DELIVERY, so they are computed on delivered rows only —
    # a borrowed last-seen coordinate must never promote a parcel into VIC_METRO or INTERSTATE
    band = pd.Series("NOSCAN", index=c.index)
    band[delivered] = "NOWHERE"                    # delivered, but the scan places it nowhere
    band[delivered & overseas] = "INTERNATIONAL"
    band[delivered & cst.isin(AU_STATES) & (cst != "VIC")] = "INTERSTATE"
    band[delivered & (cst == "VIC") & inside] = "VIC_METRO"
    band[delivered & (cst == "VIC") & ~inside] = "VIC_REGION"

    vic = band.isin(["VIC_METRO", "VIC_REGION"])
    dest = band.map(DEST_NODE)
    dest[vic] = c.dfac[vic].fillna(OTHER_SITES)    # the DELIVER event's own building
    # …and the never-delivered keep their band while the column names where they got to
    if lastseen is not None:
        gap = ~delivered
        dest[gap] = c.dfac[gap].fillna(DEST_NODE["NOSCAN"])
    c = c.assign(band=band, dest=dest, chain=chain.reindex(c.index))
    # HOW each row's destination was decided, kept per row rather than totalled here — the page
    # may draw a subset (`--sites`) and a summary of the whole file printed over a filtered
    # diagram is just a wrong number in a confident font.
    # every one of these describes how a DELIVERY was placed, so each is gated on `delivered`.
    # Without that gate a coordinate borrowed from the last-seen building under `--noscan last`
    # reads back as "placed by the ZPT_DELIVER event's coordinate", and the summary claims a
    # delivery scan the parcel never got.
    how = pd.Series("noscan", index=c.index)
    how[delivered] = "nowhere"
    how[delivered & off_shore] = "offshore"
    how[delivered & fell_back] = "fallback"
    how[delivered & by_coord] = "coord"
    c["how"] = how
    c["unnamed"] = delivered & c.dfac.isna()
    # the whole point of the change: delivered, `Del_state` blank, and the coordinate
    # nevertheless says where — volume the old basis banded "no delivery recorded"
    c["rescued"] = delivered & c.st.isna() & cst.notna()
    c["chain"] = [x if isinstance(x, list) else [] for x in c.chain]
    c.attrs["vic"], c.attrs["bar"], c.attrs["schema"] = vic_basis, bar, SCHEMA
    c.attrs["noscan"] = noscan
    c.attrs["raw"] = d
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    c.to_pickle(CACHE)
    return c


def frame(c, dest_basis):
    """The originating extract, normalised to the six columns `build` reads.

    Runs AFTER the name fold is learnt: `own` and `dest` are compared against the chain, so they
    have to be in the same vocabulary as it.
    """
    own = [frozenset([alias(t)]) if isinstance(t, str) else frozenset() for t in c.dfac]
    dest = ([BAND_LABEL[b] for b in c.band] if dest_basis == "class"
            else [alias(x) for x in c.dest])
    out = pd.DataFrame({
        "articles": c.articles.values,
        "cls": c.Product_type.map(PRODUCT).values,
        "band": c.band.values,
        "chain": list(c.chain),
        "dest": dest,
        "own": own,
    })
    # the third filter's key, when the extract names an origin site — see SITE_GROUP
    if "site" in c.columns:
        out["grp"] = ["TRANSPORT" if x in TRANSPORT else "REDVAN" for x in c.site]
    return out


# ══ THE RED VAN TABLE ════════════════════════════════════════════════════════════════
# The volume on this page is RED VAN LODGEMENT: what a driver collected on a round and brought
# back to the site. It is a KNOWN FRACTION of what the site originates — 40% of its Parcel Post
# and 60% of its Express Post — the rest being lodged over a retail counter, dropped in a street
# posting box or picked up on a contract run that this extract does not follow. So the measured
# figure is a floor, and the grossed-up one is what the site actually sends:
#
#     total PP = red van PP / 0.40        total EP = red van EP / 0.60
#
# The two shares are DIFFERENT and that is the whole reason the table is split by product: an EP
# parcel is far more likely to be collected by a van than a PP one, so a single blended factor
# applied to the combined figure would understate a Parcel Post site and overstate an Express
# Post one. Grossing up per product and adding is the only version of this that is right.
#
# PEAK_2025 is the operational planning figure the notebook compares against
# (`notebooks/analyse-pickup-volume.ipynb`). It is printed against BOTH columns without a verdict,
# because which of the two it is meant to be compared with is the site planners' question, not
# this page's: the red van number is what these scans measure, the inferred number is what the
# site originates, and the peak sits somewhere in that argument.
RED_VAN_SHARE = {"PP": 0.40, "EP": 0.60}
PEAK_2025 = {
    "MELBOUNRE_NORTH": 43_515, "BAYSWATER": 37_721, "OAKLEIGH_SOUTH": 57_493,
    "DANDENONG_SOUTH": 35_427, "SUNSHINE_WEST": 96_849,
    "MELBOURNE_TRANSPORT": 351_535, "DANDENONG_TRANSPORT": 24_081,
}
SITE_LABEL = {"MELBOUNRE_NORTH": "Melbourne North", "BAYSWATER": "Bayswater",
              "SUNSHINE_WEST": "Sunshine West", "OAKLEIGH_SOUTH": "Oakleigh South",
              "DANDENONG_SOUTH": "Dandenong South",
              "MELBOURNE_TRANSPORT": "Melbourne PF (transport)",
              "DANDENONG_TRANSPORT": "Dandenong LC (transport)"}
SITE_ORDER = ["MELBOUNRE_NORTH", "BAYSWATER", "SUNSHINE_WEST",
              "OAKLEIGH_SOUTH", "DANDENONG_SOUTH",
              "MELBOURNE_TRANSPORT", "DANDENONG_TRANSPORT"]

# THE GROSS-UP DOES NOT APPLY TO A HUB. 40% and 60% are the red van fractions of a DELIVERY
# SITE's originating volume — the rest of that site's freight is lodged over a counter, in a
# street box or on a contract run. A transport facility has no red van fleet at all: what
# originates at MPF or Dandenong LC is freight injected into the network, and dividing it by 0.40
# would invent volume out of a share that never described it. So the two hubs are printed with
# their measured volume and their peak, and the last three columns are blank for them.
TRANSPORT = {"MELBOURNE_TRANSPORT", "DANDENONG_TRANSPORT"}
RED_VAN = [s for s in SITE_ORDER if s not in TRANSPORT]

# `--sites`: which population the page draws. The filter runs on `c` AFTER `load_rows`, so the
# reduction and its cache stay whole and switching sets costs a re-render, not a re-read. The
# name fold and every summary number are learnt from the filtered volume, which is the point —
# a fold learnt on MPF's traffic is not the fold the red van page should be drawn with.
SITE_GROUP_LABEL = {"REDVAN": "Red van sites", "TRANSPORT": "Transport facilities"}
SITE_SETS = {
    "all": (None, "seven sites", "five red van sites and two transport facilities"),
    "redvan": (set(RED_VAN), "five red van sites",
               "the five delivery sites, whose freight a driver collected on a round"),
    "transport": (set(TRANSPORT), "two transport facilities",
                  "Melbourne Parcel Facility and Dandenong LC, whose freight is injected into "
                  "the network rather than collected by a van"),
}


def site_table(c):
    """The per-site red van section, or "" when the source does not name an origin site."""
    if "site" not in c.columns or c.site.isna().all():
        return ""
    n = lambda v: f"{round(v):,}"
    cls = c.Product_type.map(PRODUCT)
    vol = c.groupby([c.site, cls]).articles.sum()
    sites = [s for s in SITE_ORDER if s in set(c.site)] + \
            sorted(set(c.site.dropna()) - set(SITE_ORDER))

    def group(names):
        """One block of rows plus its subtotal, or None when the extract has none of them."""
        names = [x for x in sites if x in names]
        if not names:
            return None
        out, t = [], [0.0, 0.0, 0.0, 0.0, 0]
        for site in names:
            pp, ep = (float(vol.get((site, k), 0)) for k in ("PP", "EP"))
            # a hub is not grossed up — see the TRANSPORT note above
            tpp, tep = ((0.0, 0.0) if site in TRANSPORT
                        else (pp / RED_VAN_SHARE["PP"], ep / RED_VAN_SHARE["EP"]))
            peak = PEAK_2025.get(site)
            out.append((site, pp, ep, tpp, tep, peak))
            for i, v in enumerate((pp, ep, tpp, tep, peak or 0)):
                t[i] += v
        return out, t

    red = group(set(SITE_ORDER) - TRANSPORT)
    hub = group(TRANSPORT)
    # the bar scales to the biggest MEASURED site, so the two groups stay comparable on it
    widest = max((r[1] + r[2] for blk in (red, hub) if blk for r in blk[0]), default=1)

    def render(site, pp, ep, tpp, tep, peak, kind=""):
        redv, inf = pp + ep, tpp + tep
        label = SITE_LABEL.get(site, str(site).title()) if kind != "sub" else site
        share = f"{100 * redv / peak:.0f}%" if peak else "&mdash;"
        bar = ("" if kind == "sub" else
               f'<div class="minibar"><span style="width:{100 * redv / max(widest, 1):.0f}%;'
               'background:var(--accent)"></span></div>')
        cells = ("<td class=\"n\">&mdash;</td>" * 3 if not inf else
                 f'<td class="n">{n(tpp)}</td><td class="n">{n(tep)}</td>'
                 f'<td class="n"><b>{n(inf)}</b></td>')
        return (f'<tr{" class=\"hit\"" if kind == "sub" else ""}>'
                f'<td>{"<b>" + label + "</b>" if kind == "sub" else label}</td>'
                f'<td class="n">{n(pp)}</td><td class="n">{n(ep)}</td>'
                f'<td class="n"><b>{n(redv)}</b></td>'
                f'<td class="n">{n(peak) if peak else "&mdash;"}</td>'
                f'<td class="n">{share}</td>{cells}<td>{bar}</td></tr>')

    body = []
    if red:
        body += [render(*r) for r in red[0]]
        body.append(render(f"All {len(red[0])} red van sites", *red[1], "sub"))
    if hub:
        body.append('<tr><td colspan="10" class="sub" style="padding-top:14px">'
                    '<b>Transport facilities</b> &mdash; freight injected into the network rather '
                    'than collected by a van, so the red van shares do not apply and the last '
                    'three columns are left blank.</td></tr>')
        body += [render(*r) for r in hub[0]]
        if len(hub[0]) > 1:
            body.append(render("Both transport facilities", *hub[1], "sub"))

    return f"""<h2>Red van lodgements by site, against peak 2025</h2>
<p>Everything on this page is <b>red van lodgement</b> &mdash; a parcel a driver collected on a
round and brought back to the site &mdash; matched on the condition
<code>notebooks/analyse-pickup-volume.ipynb</code> uses: the parcel&rsquo;s first traced event of
11 May 2026 happened in one of that site&rsquo;s own buildings. That is a KNOWN FRACTION of what
the site originates, and a different fraction per product:
<b>{RED_VAN_SHARE['PP']:.0%} of its Parcel Post</b> and
<b>{RED_VAN_SHARE['EP']:.0%} of its Express Post</b> arrive this way, the rest being lodged over a
counter, in a street box, or on a contract run these scans do not follow. So the last three
columns gross each product up on its OWN share and add them &mdash; a single blended factor would
understate a Parcel Post site and overstate an Express Post one. The peak 2025 figure is printed
beside both and judged against neither: the measured column is a floor, the inferred column is the
estimate, and which one the planning number is meant to answer is the site&rsquo;s question rather
than this page&rsquo;s.</p>
<p><b>The transport facilities are a different animal</b> and are kept in their own block.
Melbourne Parcel Facility and Dandenong LC run no red van fleet: what originates there is freight
injected into the network, not freight a driver collected on a round. The 40/60 shares describe a
delivery site&rsquo;s collection mix and say nothing about a hub, so grossing a hub up on them
would invent volume out of a fraction that never described it. They are printed measured, against
their peak, and nothing more.</p>
<div class="tablewrap"><table><thead><tr>
  <th>Site</th>
  <th style="text-align:right">Red van PP</th><th style="text-align:right">Red van EP</th>
  <th style="text-align:right">Red van total</th>
  <th style="text-align:right">Peak 2025</th><th style="text-align:right">Red van / peak</th>
  <th style="text-align:right">PP &divide; {RED_VAN_SHARE['PP']:.0%}</th>
  <th style="text-align:right">EP &divide; {RED_VAN_SHARE['EP']:.0%}</th>
  <th style="text-align:right">Originating total</th>
  <th></th>
</tr></thead><tbody>{"".join(body)}</tbody></table></div>
"""


def dateline(raw):
    """"lodged 11 May 2026" — read off the extract, not written into the page.

    The dial exists because there is now more than one source. The single-site file this page was
    built on lodged over two days; `originating_volume_scan_events.csv` is one day across five
    sites, and a hardcoded caption would have gone quietly wrong the moment the source changed.
    """
    d = pd.to_datetime(raw.Lodge_date, errors="coerce").dropna()
    if d.empty:
        return "lodgement date not recorded"
    lo, hi = d.min(), d.max()
    if lo == hi:
        return f"lodged {lo:%-d %B %Y}"
    if (lo.year, lo.month) == (hi.year, hi.month):
        return f"lodged {lo:%-d}-{hi:%-d %B %Y}"
    return f"lodged {lo:%-d %B} \u2013 {hi:%-d %B %Y}"


def main():
    global SRC
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=SRC, metavar="CSV",
                    help="the originating event file (default inputs/originating_volume/…)")
    ap.add_argument("--rebuild", action="store_true", help="ignore the caches and re-read the CSV")
    ap.add_argument("--keep", type=int, default=KEEP, metavar="N",
                    help=f"facilities named per touch column before the fold (default {KEEP})")
    ap.add_argument("--dest-keep", type=int, default=DEST_KEEP, metavar="N",
                    help=f"destination nodes named in the last column (default {DEST_KEEP})")
    ap.add_argument("--depth", type=int, default=DEPTH, metavar="N",
                    help=f"touch columns before the destination (default {DEPTH}; the journey "
                         "averages 5.46 buildings, so this is not the other pages' 2)")
    ap.add_argument("--touch", choices=tuple(PAGE.EVIDENCE), default=TOUCH,
                    help=f"which events may name a building (default {TOUCH}, unlike the "
                         "siblings' entry: the delivering site has to be on the chain for the "
                         "journey to be cut there, and interstate depots only DELIVER and UNLOAD)")
    ap.add_argument("--dest", choices=("named", "class"), default="named",
                    help="named (default): Victorian deliveries name their site, everything else "
                         "is one Interstate or International node. class: the last column is the "
                         "five destination bands and no site is named")
    ap.add_argument("--vic", choices=("postcode", "catchment"), default="postcode",
                    help="what splits metro from regional Victoria: postcode (default, the "
                         "delivery address) or catchment (the first-mile route polygon, which "
                         "reaches Geelong and Kyneton and is not the same question)")
    # MPF alone outweighs all five red van sites (201,792 EA against 204,647) and the two groups
    # answer different questions — a van site's freight is collected on a round and mostly
    # delivered locally, a hub's is injected into the network and mostly leaves the state. Drawn
    # together the page is largely a picture of MPF, so which population it draws is a dial.
    ap.add_argument("--noscan", choices=("last", "node"), default="last",
                    help="the articles with no ZPT_DELIVER: draw them at the LAST building that "
                         "scanned them (default), or as one 'never scanned as delivered' node")
    ap.add_argument("--sites", choices=tuple(SITE_SETS), default="all",
                    help="which origin sites to draw: all (default), redvan (the five delivery "
                         "sites), transport (Melbourne PF and Dandenong LC)")
    ap.add_argument("--group", choices=("scan", "keep", "model"), default="scan",
                    help="scan (default): every building under its own name. keep: only modelled "
                         "buildings are NAMED, the rest share a node per column. model: only "
                         "modelled buildings are stops at all")
    args = ap.parse_args()
    SRC = args.src
    PAGE.DEPTH = args.depth
    PAGE.path_chain = origin_chain          # the border cut is off — see the note above
    # ── and the copy has to stop saying Victoria ─────────────────────────────────────
    # The page module's prose is written for freight that ARRIVES here: "Victorian buildings",
    # "the clock starts when the parcel enters the state", "none — first seen in Victoria at the
    # depot itself". Every one of those is false on an outbound page, and leaving them would put
    # a Victorian caption on a column of Brisbane and Perth facilities. `PAGE.SCOPE` is the dial
    # that lets the same template say it the other way round; its default IS the Victorian
    # wording, so the two terminating pages are untouched by this.
    PAGE.SCOPE = {
        "adj": "",                      # a building here is just a building — it has no border
        "place": "Australia",
        "bounded": False,
        "eyebrow": "lodged in Melbourne \u00b7 followed to the door",
        "question": "",
        "clock": "The clock starts <strong>at the site that lodged it</strong>,\n  and the parcel "
                 "is followed out of the state \u2014 a Sydney or Perth facility is a step like "
                 "any other.",
        "zeroklab": "none \u2014 first seen at the site that delivered it",
        "tile": "before the delivering ",
        "title": "Parcel Paths out of Melbourne",
        "onward": "straight on to the destination",
        "onwardsub": "the journey ended before this step \u2014 the next thing on the page is "
                     "where the parcel was delivered",
        "fewtile": "lodgement to destination",
        "destsub": "where the parcel was delivered",
    }

    # ── the zero bucket stops being only about deliveries ────────────────────────────
    # Under `--noscan last` a parcel with no delivery scan is drawn at the last building that saw
    # it, so the zero bucket now holds two things: a local parcel delivered from the building it
    # was first scanned in, AND a parcel whose trail went cold in that same building. "Started at
    # its depot" describes the first and lies about the second, which is 5,454 of 8,288 articles.
    # Patched the same way `path_chain` is — the terminating pages never run this line.
    if args.noscan == "last":
        PAGE.ZERO_LABEL[Z_DEPOT] = "Never left its first building"
        PAGE.ZERO_PROSE[Z_DEPOT] = ("never left the building they were first scanned in — "
                                    "delivered from it, or last seen there and not seen again")
        PAGE.SCOPE["zeroklab"] = "none — it never left the building it was first seen in"

    c = load_rows(args.vic, args.touch, args.rebuild, args.noscan)
    c = c[c.Product_type.isin(PRODUCT)].copy()
    raw = c.attrs.get("raw")
    if raw is None:
        raw = pd.read_csv(SRC, dtype={"Consignment_ID": str}, low_memory=False)

    keep_sites, sitesword, siteslong = SITE_SETS[args.sites]
    if keep_sites is not None:
        if "site" not in c.columns:
            sys.exit(f"--sites {args.sites} needs an Origin_site column; {SRC.name} has none "
                     "(rebuild it with utilities/extract_originating_event.py)")
        before = c.articles.sum()
        c = c[c.site.isin(keep_sites)].copy()
        if c.empty:
            sys.exit(f"--sites {args.sites} matched nothing in {SRC.name}")
        print(f"\n  --sites {args.sites}: {c.articles.sum():,} of {before:,} articles "
              f"({', '.join(SITE_LABEL.get(x, x) for x in SITE_ORDER if x in set(c.site))})")
    # a variant is its own page, not an overwrite of the default one — otherwise the last run
    # silently wins and two questions share one file
    target = (TARGET if args.sites == "all"
              else TARGET.with_name(TARGET.stem + f"-{args.sites}" + TARGET.suffix))
    PAGE.SCOPE["eyebrow"] = f"lodged in Melbourne \u00b7 {sitesword} \u00b7 followed to the door"
    folds, state = learn(zip(c.chain, c.articles), facility_state(raw, args.rebuild))
    f = frame(c, args.dest)
    present = set(f.band)
    cfg = {
        "classes": [x for x in PRODUCT_LABEL if x[0] in set(f.cls)],
        "bands": [(b, BAND_LABEL[b]) for b in BAND_ORDER if b in present],
        "dest_keep": args.dest_keep,
        # destcol heads the column, destword is the noun the copy uses for ONE of them —
        # "the site that delivered it" reads; "the destination that delivered it" does not
        "destcol": "Destination", "destword": "site",
        "group": None if args.group == "scan" else PAGE.model_names(),
        "outside": args.group == "keep",
        "bar": args.touch,
        # THE ON-PAGE SITE FILTER. `--sites` decides what goes INTO the payload; this decides what
        # the reader is looking at once it is there, without a rebuild. Both exist because they
        # answer different needs: a transport-only page is a smaller file worth publishing on its
        # own, and a reader with the whole page open still wants to take MPF out and see what the
        # five red van sites do on their own. Declared only when both groups are actually present,
        # so a page drawn from one of them does not offer a control with one live button.
        "groups": [(g, l) for g, l in SITE_GROUP_LABEL.items() if g in set(f.grp)]
                  if "grp" in f and f.grp.nunique() > 1 else [],
    }
    data, summary = build(f, state, args.keep, cfg)
    summary["folds"] = folds
    summary["top_after"] = top_after(f, summary)
    report(summary, data, folds, cfg["destword"])
    print(f"    evidence bar: {args.touch.upper()} — "
          + ", ".join(e.replace("ZPT_", "") for e in PAGE.EVIDENCE[args.touch]))
    h = c.groupby("how").articles.sum()
    b = dict(coord=int(h.get("coord", 0)), fallback=int(h.get("fallback", 0)),
             nowhere=int(h.get("nowhere", 0)), noscan=int(h.get("noscan", 0)),
             unnamed=int(c.articles[c.unnamed].sum()),
             rescued=int(c.articles[c.rescued].sum()))
    print(f"\n  === DESTINATION, FROM THE DELIVERY SCAN'S OWN COORDINATE ===")
    print(f"    {b['coord']:,} articles placed by the ZPT_DELIVER event's coordinate")
    print(f"    {b['fallback']:,} more placed by Del_state, which no coordinate test could reach "
          "(mostly overseas)")
    if h.get("offshore", 0):
        print(f"    {int(h['offshore']):,} carry a point that is not in Australia at all")
    print(f"    {b['nowhere']:,} delivered but placed nowhere at all; "
          f"{b['noscan']:,} never scanned as delivered")
    print(f"    {b['rescued']:,} of those were delivered with a BLANK Del_state \u2014 volume "
          "the old basis banded \u2018no delivery recorded\u2019")
    print(f"    {b['unnamed']:,} were delivered by a building the scan does not name "
          f"\u2014 drawn as \u201c{OTHER_SITES}\u201d")
    if args.noscan == "last":
        ns = c[c.band == "NOSCAN"]
        hand = ns.dfac.map(lambda x: PAGE.role(alias(x)) if isinstance(x, str) else "")
        real = ns.articles[~hand.str.startswith("hub")].sum()
        print(f"    --noscan last: the {ns.articles.sum():,} never scanned as delivered are drawn "
              "at the LAST building that saw them,")
        print(f"      their band still NOSCAN because it is a position and not a delivery; "
              f"{ns.dfac.nunique():,} distinct buildings")
    vicword = ("the delivery postcode against the standard Melbourne metro ranges, falling back "
               "to the delivery point against the catchment polygon where the extract carries no "
               "postcode" if args.vic == "postcode" else
               "the delivery point against the first-mile catchment polygon")
    write_page(data, summary, {
        # the per-site table takes the lane dial's place — the fold receipt is a question about
        # the DIAGRAM, and on this page the question worth the space below it is the volume
        "{{GROUPFILTER}}": (
            '\n  <div class="grp"><span>Origin</span>\n'
            '    <div class="seg" role="group" aria-label="Origin site group" id="grpseg">'
            '</div>\n  </div>' if cfg["groups"] else ""),
        "{{SECTIONS}}": site_table(c),
        "{{LANEDIAL}}": "",
        "{{DATELINE}}": dateline(raw),
        "{{PROVENANCE}}": f"Built from <code>{SRC.name}</code> by "
                          "<code>sankey_facility_path_originating.py</code> &mdash; the "
                          "ORIGINATING mirror of the two terminating pages, so it reads what this "
                          f"catchment sent rather than what it received. This page draws {siteslong}",
        "{{VICRULE}}": "NOT APPLIED. The terminating pages draw Victorian buildings only, because "
                       "their freight arrives here; this one follows the parcel out, so every "
                       "building is on the page whatever state it is in, and the journey ends at "
                       "the delivering site rather than at the border. <b>Which state a parcel "
                       "was delivered in is read off the ZPT_DELIVER event\u2019s own "
                       "coordinate</b>, not off the address text on that scan: the address block "
                       f"is blank on {b['rescued']:,} articles that were delivered and that the "
                       "coordinate nevertheless places, and where both exist they agree on 98.2% "
                       "of the volume. <code>Del_state</code> is used "
                       f"only where there is no coordinate at all ({b['fallback']:,} articles, "
                       "almost all overseas). Metro and regional Victoria "
                       f"are split on {vicword}; a state code on a node is read off the building\u2019s "
                       "own median coordinate and the console reports how often that disagrees "
                       "with the delivery states the extract carries",
    }, target)


if __name__ == "__main__":
    main()
