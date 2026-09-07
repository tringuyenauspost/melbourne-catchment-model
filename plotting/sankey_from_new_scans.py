"""Draw the new extract as a Sankey: lodgement to delivering site, over the reduction.

The sibling of sankey_from_scans.py, for inputs/new_scan_events/. It contains nothing but the
diagram — every rule about what the data MEANS lives in analyse_new_scans.py, which this imports,
so the picture and the analysis can never drift apart. It differs from the analysis on exactly one
thing, deliberately and in one place: SCOPE. The analysis narrows to the Australia Post parcel
stream so its rates describe one operation; the page draws every product, because the buildings
are shared and a network drawn with StarTrack missing has holes in it. The networks stay
separable as buttons — see the note beside PRODUCT_KEY.

    uv run python plotting/sankey_from_new_scans.py                 # path basis, depth 2
    uv run python plotting/sankey_from_new_scans.py --depth 3
    uv run python plotting/sankey_from_new_scans.py --basis role     # the original five columns
    uv run python plotting/sankey_from_new_scans.py --rebuild --top 24

TWO BASES, AND THE DEPTH IS A DIAL (2026-08-31) ──────────────────────────────────────
The columns used to be ROLES — first sort, second sort, last sort — which answers "how often was
this sorted", not "how many buildings was it in". They are different questions and this data says
so loudly: on the 20 May reduction 23,334 EA moved between two buildings on a single sort, and
22,388 EA was sorted twice inside ONE building. `--basis path` (the default now) draws POSITIONS
in the itinerary instead, `--depth N` says how many, and the cap is the exporter's own
`first_last` — keep the first building and the last, fold the middle — so this page, the
itinerary page and `export_chain2_factors` all mean the same thing by "second building".

Each combination writes its OWN file (`…-path-d3-entry.html`, `…-role.html`): the depth is a
different diagram, not a different rendering of one, and a single filename meant a depth-3 run
silently replaced the depth-2 page.

The chain comes from `analyse_new_scans.chain_by_time`, in TIME order. It must: `Event_seq` in
this extract is a per-event-type index, so ordering the concatenated files by it interleaves the
types and invents journeys — 82.4% of articles came out in a different order and the mean
building count read 7.889 instead of 3.648.

WHY THIS IS NOT THE OLD PAGE WITH NEW NUMBERS ────────────────────────────────────────
The old diagram could name its nodes in advance: eight sort sites and eleven depots, typed into a
dictionary, and anything else folded into "sorted elsewhere". This extract has 113 sorting sites
and 1,422 delivering ones, and which of them the model should carry is undecided — so the node set
is DERIVED here, per column, by volume, and everything below the line is folded into one honest
"Other" node that says how many sites it stands for. `--top` moves that line, and the page carries
the same control, because where the line falls is exactly the question the scope decision has to
answer and it should be possible to see what each answer costs.

Colour says two things the old page could not. Every event carries a coordinate, so a site is
drawn as INTERSTATE or Victorian on measurement rather than on its name; and `wcc_list` in the
review workbook types the building itself, so a node is a parcel centre or a depot because the
work centre list says so, not because of how it happened to behave in these two days. The code
rides on the label. Where the list has no record, the old behavioural inference is the fallback
and the label carries no code — see nodes_meta.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))                # the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utilities"))               # ad-hoc tools
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_input_preparation"))  # the reduction lives there
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from analyse_new_scans import (OUT, VIC_BOX, facility_roles, load_paths,
                               site_points)
from analyse_new_scans import chain_by_time as chain_in_time_order
from sankey_facility_path import EVIDENCE      # which events may name a building, said once

TARGET = OUT / "sankey-from-new-scans.html"    # replaced by target_for() — see the note there


# ── THE FILE SAYS WHAT IT IS (2026-08-31) ────────────────────────────────────────────
# The page used to write one filename whatever it drew, so a depth-3 run silently replaced the
# depth-2 page and two files that looked identical in a folder were different diagrams. The basis,
# the depth and the evidence bar are now IN THE NAME, and the default is stamped like any other,
# because a file called "…-new-scans.html" sitting beside "…-new-scans-path-d3-entry.html" reads
# as the old one rather than as depth 2.
def target_for(basis, depth, bar):
    return (OUT / f"sankey-from-new-scans-role.html" if basis == "role"
            else OUT / f"sankey-from-new-scans-path-d{depth}-{bar}.html")


def basis_label(bar):
    return ("role basis · first / second / last machine sort" if BASIS == "role"
            else f"path basis · buildings touched, {bar} bar · depth {DEPTH} (first_last)")


def bucket_label():
    return "Victorian sorts" if BASIS == "role" else "Buildings touched"


def bucket_buttons():
    lab = {"all": "All", "0": "None"}
    return "".join(
        f'<button data-sorts="{b}" aria-pressed="{"true" if b == "all" else "false"}">'
        f'{lab.get(b, b)}</button>' for b in buckets())
TOPN = 18                     # nodes kept per column before the fold
# THE HANDLED COLUMN IS GONE, and the cross-dock it used to show is a COLOUR BAND instead.
# Between "first handled" and "first sorted" the page drew one MPF -> Bayswater truck, and then
# drew it again between "first sorted" and "sorted again" — the same vehicle, two ribbons, two
# columns. Banding stage 0 on `crossdocked` says the same thing once: this much of the freight
# reached its first sort in the building that handled it, this much had to be carried.
# ── TWO BASES, AND DEPTH IS A DIAL ON ONE OF THEM (2026-08-31) ───────────────────────
# The page was five fixed columns on the ROLE basis — first sort, second sort, last sort — which
# answers "how often was it sorted", not "how many buildings was it in". They are different
# questions and the extract says so: on the 20 May reduction 23,334 EA moved between two
# buildings on one sort, and 22,388 EA was sorted twice inside ONE building. The exporter and
# `sankey-facility-path-new.html` both read the PATH; this page now can too, so the three can be
# read against each other.
#
# `--depth N` is the cap, and it is the exporter's cap: `PATH_CAP_RULE="first_last"` — keep the
# FIRST building and the LAST, fold the middle. A parcel in more than N buildings is drawn
# entering where it really entered and despatching from where it really despatched, and what is
# invented is the directness of the middle. See docs/export_chain2_factors.md#the-model-has-two-sort-rounds.
ROLE_COLS = ["Where it came from", "First sorted at", "Sorted again at",
             "Despatched from", "Delivered by"]
ROLE_KEYS = ["org", "entry", "mid", "exit", "dest"]
ROLE_PREFIX = ["ORG", "E", "M", "X", "P"]
DEPTH = 2                     # touch columns between the source band and the delivering site
BASIS = "path"                # path | role
COLS, STAGE_KEYS, PREFIX = ROLE_COLS, ROLE_KEYS, ROLE_PREFIX      # replaced by configure()
ORDINAL = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"]


def ordinal(i):
    return ORDINAL[i] if i < len(ORDINAL) else f"{i}th"


def configure(basis=BASIS, depth=DEPTH):
    """Set the column grammar. Every other function reads STAGE_KEYS / PREFIX / COLS, so this is
    the only place that knows how many columns there are."""
    global BASIS, DEPTH, COLS, STAGE_KEYS, PREFIX
    BASIS, DEPTH = basis, depth
    if basis == "role":
        COLS, STAGE_KEYS, PREFIX = list(ROLE_COLS), list(ROLE_KEYS), list(ROLE_PREFIX)
    else:
        COLS = (["Where it came from"] + [f"{ordinal(i)} building" for i in range(1, depth + 1)]
                + ["Delivered by"])
        STAGE_KEYS = ["org"] + [f"t{i}" for i in range(1, depth + 1)] + ["dest"]
        PREFIX = ["ORG"] + [f"T{i}" for i in range(1, depth + 1)] + ["P"]
    return COLS


def cap_path(sites, depth):
    """The journey the page can draw, and how many buildings that cost — `first_last`."""
    if len(sites) <= depth:
        return list(sites), 0
    return list(sites[:depth - 1]) + [sites[-1]], len(sites) - depth
# WHAT THE PAGE DRAWS — EVERY PRODUCT, WITH THE NETWORKS STILL SEPARABLE ─────────────
# `analyse_new_scans.PRODUCT_SCOPE` narrows the ANALYSIS to the Australia Post parcel stream,
# for a good reason: StarTrack runs different lanes through partly different buildings, so a
# pooled cross-dock rate is the average of two operations and describes neither.
#
# That is an argument about *rates*, not about the picture. The page deliberately ignores the
# scope and draws the whole extract, because the question the diagram answers — which buildings
# does freight move through, and how much — needs every product in it: the StarTrack sites are
# real sites, they share buildings with the parcel stream, and leaving them out drew a network
# with holes in it. The separation the scope was protecting is kept as a CONTROL instead: the
# first three buttons pick a network, so any rate can still be read one operation at a time.
PRODUCT_KEY = {"Parcel Post": "PP", "Express Post": "EP",
               "StarTrack Road Express": "STR", "StarTrack Premium": "STP"}
PRODUCT_BTN = {"PP": "Parcel Post", "EP": "Express Post",
               "STR": "ST Road", "STP": "ST Premium"}
# The network buttons: a key, its label, and the product keys it pools.
NETWORKS = [("ALL", "All", None), ("AP", "AusPost", ("PP", "EP")),
            ("STK", "StarTrack", ("STR", "STP"))]

def buckets():
    """The filter values — machine sorts on the role basis, BUILDINGS TOUCHED on the path basis.
    The top bucket is open-ended at one past the depth, because that is where the cap bites."""
    return (["all", "0", "1", "2", "3+"] if BASIS == "role"
            else ["all"] + [str(i) for i in range(DEPTH + 1)] + [f"{DEPTH + 1}+"])
ORG_ORDER = ["INT", "KEPT_METRO", "METRO", "REGION", "UNPLACED", "UNKNOWN"]
ORG_LABEL = {"INT": "Interstate", "KEPT_METRO": "Kept at the site · metro",
             "METRO": "Metro Victoria", "REGION": "Regional Victoria",
             "UNPLACED": "Lodged, no coordinate", "UNKNOWN": "Origin unknown"}
ORG_SUB = {
    "INT": "lodged outside Victoria — one band, whether or not it slept at the site",
    "KEPT_METRO": "already in the building before its delivery date, lodged inside the catchment",
    "METRO": "lodged inside the first-mile catchment, delivered the same day",
    "REGION": "lodged in Victoria, outside the first-mile catchment",
    "UNPLACED": "a lodgement scan with no coordinate on it",
    "UNKNOWN": "no lodgement scan and no later scan carrying a point",
}
# the three nodes that are not buildings: what happened, rather than where
PSEUDO = {"ONCE": ("Sorted once and sent on", "state"),
          "NONE": ("No machine sort at all", "state"),
          # the path basis needs a fourth: a journey shorter than the depth must NOT be padded by
          # repeating its last building across the remaining columns — drawn that way it is
          # indistinguishable from a parcel that really went back. It rides a grey node instead,
          # which is exactly what sankey-facility-path.html does and for the same reason.
          "ONWARD": ("On to the delivering site", "state"),
          "NOPATH": ("No building on this bar", "state"),
          "OTHER": ("Other sites", "fold")}

# ── LABELS — WHICH BUILDING IS THIS, IN THE WIDTH OF A COLUMN GAP ────────────────────
# The raw names are inconsistent enough that one rule cannot do it, so there are three, in order.
#
#   1. PHRASES, because "PARCEL FACILITY" is one idea and its short form is what operations says
#      out loud. Note what is NOT here: "PARCEL DELIVERY" does not become "PD". SUNSHINE WEST
#      PARCEL DELIVERY and SUNSHINE WEST PDC are two different buildings, both in this extract,
#      and a label that separated them by one letter would be a label that lies quietly.
#   2. WORDS, verbatim — this is where STARTRACK becomes ST. It has to be a token rule and not a
#      text replacement, because ST ALBANS DELIVERY CENTRE is a saint, not a StarTrack site, and
#      a blind replace would have said otherwise. Now that StarTrack is drawn, that matters.
#   3. Everything else is title-cased unless it is an acronym: "SUNSHINE WEST PARCEL DELIVERY"
#      should read "Sunshine West Delivery", not "Sunshine WEST Delivery".
PHRASES = [("MC MAIL PROCESSING", "MC"), ("MAIL PROCESSING CENTRE", "MC"),
           ("PARCEL FACILITY", "PF"), ("PCL FAC", "PF"), ("GATEWAY FACILITY", "GF"),
           ("LETTERS FACILITY", "LF"), ("LETTER CENTER", "LC"), ("LETTER CENTRE", "LC"),
           ("DELIVERY CENTRE", "DC"), ("DELIVERY CENTER", "DC"),
           ("PARCEL DELIVERY", "Delivery"), ("PARCEL OPERATIONS", "Ops"),
           ("PARCEL COLLECTION", "Collection"), ("VAN SERVICES", "Vans"),
           ("BUSINESS CENTRE", "BC"), ("BUSINESS HUB", "BH")]
WORDS = {"STARTRACK": "ST", "ST": "St", "MELBOURNE": "Melb", "NTH": "North", "STH": "Sth",
         "FRGHT": "Freight", "EXP": "Express", "PRM": "Premium", "REG": "Regional"}
# Words that stay shouty because they are acronyms, not words.
ACRONYMS = {"PF", "GF", "LC", "LF", "BC", "BH", "SC", "AP", "DC", "MC", "MSC", "PDC", "MDC",
            "LPO", "DFPC", "MPF", "TPF", "MGF", "MNP", "SWP", "BAY", "AVL", "DLC", "BNPF",
            "PPF", "STC", "II", "III"}
MAXLAB = 28                   # characters; the column gap fits about this many at 12px


def short(name):
    """A label that fits in a column without losing which building it is."""
    s = re.sub(r"\s*\([^)]*\)", "", str(name).upper())     # MGF - AIRPORT FACILITY(HIRCHY)
    s = s.replace(" - ", " ")
    for a, b in PHRASES:
        s = s.replace(a, b)
    out = [WORDS[w] if w in WORDS else w if w in ACRONYMS else w.title() for w in s.split()]
    s = " ".join(out)
    return s if len(s) <= MAXLAB else s[:MAXLAB - 1] + "\u2026"


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE FRAME — one row per consignment, with the six columns the diagram draws
# ══════════════════════════════════════════════════════════════════════════════════════
def frame(p, chains=None):
    """The reduction's columns, renamed onto the stages `configure()` set up.

    ROLE basis — a parcel sorted once must NOT be drawn passing through a second-sort node; that
    is indistinguishable from a genuine second sort there. It gets ONCE instead, so the column
    reads: this much was sorted again here, this much never was.

    PATH basis — the columns are POSITIONS in the itinerary, and the same rule applies one column
    later: a journey shorter than the depth rides ONWARD rather than repeating its last building.
    `chains` is the time-ordered building list per consignment (analyse_new_scans.chain_by_time);
    it is required on this basis and ignored on the other.
    """
    q = p.copy()
    q["org"] = q.source_band.fillna("UNKNOWN")
    # handled at one building, first sorted at another — `sortation` measures it off the
    # Victorian chain, so it is already scoped the way the columns are
    q["xdock"] = q.crossdocked.fillna(False).astype(bool)
    q["dest"] = q.site.fillna("NONE")
    if BASIS == "role":
        q["entry"] = q.first_sort.fillna("NONE")
        q["mid"] = np.where(q.nrounds >= 2, q.second_sort.fillna("NONE"),
                            np.where(q.nrounds == 1, "ONCE", "NONE"))
        q["exit"] = q.last_sort.fillna("NONE")
        q["bucket"] = np.where(q.nrounds >= 3, "3+", q.nrounds.astype(int).astype(str))
        q["touches"] = q.nrounds
        return _finish(q)
    assert chains is not None, "the path basis needs the time-ordered chain"
    # ── THE TOUCH COLUMNS STOP BEFORE THE DELIVERING SITE ───────────────────────────
    # The chain runs all the way to delivery, and the last column IS the delivering site — so
    # left whole it draws that building twice, once as a touch and once as the destination, and
    # the ribbon between them is a journey from a building to itself. It also makes this page's
    # "buildings touched" a different quantity from everybody else's: `export_chain2_factors`
    # counts buildings BEFORE the depot (`path_n`), and comparing 2.91 against its 1.34 would be
    # comparing two different questions. So the journey is cut at the LAST arrival at the
    # delivering site, which is the reduction's own rule in `path_chain`.
    caps, uncapped = [], []
    for ch, d in zip(chains, q.dest):
        ch = ch if isinstance(ch, list) else []
        if d in ch:
            ch = ch[:len(ch) - 1 - ch[::-1].index(d)]
        c, _ = cap_path(ch, DEPTH)
        caps.append(c)
        uncapped.append(len(ch))
    for i in range(1, DEPTH + 1):
        q[f"t{i}"] = [c[i - 1] if len(c) >= i else ("NOPATH" if not c else "ONWARD")
                      for c in caps]
    # the count is the UNCAPPED one — the filter answers "how many buildings was it in", and
    # capping it first would put every long journey in the same bucket as an honest two
    q["touches"] = uncapped
    q["bucket"] = np.where(np.array(uncapped) > DEPTH, f"{DEPTH + 1}+",
                           np.array([str(n) for n in uncapped]))
    return _finish(q)


def _finish(q):
    # No fillna here: an unmapped product would be silently drawn as Parcel Post, and the whole
    # point of widening the page is that each product is drawn as itself.
    q["pk"] = q.Product_type.map(PRODUCT_KEY)
    assert q.pk.notna().all(), f"unmapped products: {sorted(q.Product_type[q.pk.isna()].unique())}"
    return q


def products(q):
    """The product keys present, biggest first, so the buttons follow the data."""
    v = q.groupby("pk").articles.sum().sort_values(ascending=False)
    return list(v.index)


def keep_sets(q, top):
    """Which node names survive in each column — decided ONCE, over everything.

    Per-filter node sets would let a node appear and vanish as the reader changes product, which
    reads as the network changing. The line is drawn on total volume and then applies to every
    view; a folded node still carries its volume, it just carries it under "Other".
    """
    keep = {}
    for k in STAGE_KEYS:
        v = q.groupby(k).articles.sum().sort_values(ascending=False)
        names = [n for n in v.index if n not in PSEUDO]
        keep[k] = set(names[:top]) | set(PSEUDO) | (set(ORG_ORDER) if k == "org" else set())
        keep[k + "_folded"] = len(names) - min(len(names), top)
    return keep


def fold(q, keep):
    for k in STAGE_KEYS:
        q[k] = np.where(q[k].isin(keep[k]), q[k], "OTHER")
    return q


def flows(sub):
    """The four link stages, with the two that feed a sort split by the cross-dock flag.

    Stage 0 (source -> first sort) and stage 1 (first -> second sort) each emit up to two ribbons
    per pair, so the page can colour the cross-docked cohort apart from the rest and follow it
    past its first sort. The other two stages carry no flag: once a parcel is despatched, how it
    reached its first sorter is no longer what the ribbon is about.

    ON THE PATH BASIS ONLY STAGE 0 IS BANDED. `crossdocked` means "handled at one building, first
    sorted at another" — which on an itinerary is not a property of a ribbon at all, it IS the
    second building. Banding stage 1 with it would colour the same fact twice, once as a node and
    once as a ribbon, so the flag stops at the first column where it still says something the
    columns do not.
    """
    out = []
    banded = 2 if BASIS == "role" else 1      # see the docstring
    for i in range(len(STAGE_KEYS) - 1):
        a, b = STAGE_KEYS[i], STAGE_KEYS[i + 1]
        if i < banded:
            g = sub.groupby([a, b, "xdock"]).articles.sum()
            out += [{"s": f"{PREFIX[i]}_{x}", "t": f"{PREFIX[i+1]}_{y}", "v": int(v), "x": int(xd)}
                    for (x, y, xd), v in g.items() if v > 0]
        else:
            g = sub.groupby([a, b]).articles.sum()
            out += [{"s": f"{PREFIX[i]}_{x}", "t": f"{PREFIX[i+1]}_{y}", "v": int(v)}
                    for (x, y), v in g.items() if v > 0]
    return out


def headline(sub):
    tot = max(int(sub.articles.sum()), 1)
    a = lambda m: int(sub.articles[m].sum())
    return dict(
        articles=int(sub.articles.sum()),
        vic=round(100 * a(sub.origin == "VIC") / tot, 1),
        region=round(100 * a(sub.lodge_band == "REGION") / tot, 1),
        kept=round(100 * a(sub.kept_on_site) / tot, 1),
        mean=round(float((sub.nrounds * sub.articles).sum() / tot), 2),
        meantouch=round(float((sub.touches * sub.articles).sum() / tot), 2),
        capped=round(100 * a(sub.touches > DEPTH) / tot, 1),
        xdock=round(100 * a(sub.crossdocked) / tot, 1),
        once=round(100 * a(sub.nrounds == 1) / tot, 1),
        presorted=round(100 * a(sub.sorted_elsewhere) / tot, 1),
    )


def nodes_meta(q, keep, p):
    """Every node the page can draw: what it is called, what it IS, and where it is.

    `kind` used to be inferred from behaviour — a site that delivered was a depot, a site that
    only sorted was a hub. `wcc_list` types the building itself, so the inference is now the
    FALLBACK and the master list is the answer. It disagrees where it matters: Bayswater PDC and
    Avalon Parcel Facility never terminate volume here and were drawn as hubs, but they are parcel
    depots; Sunshine West Parcel Delivery terminates more than any other site and was drawn as a
    depot, but it is a parcel centre — the same class as Melbourne Parcel Facility, and 0.7 km
    from the SUNSHINE WEST PDC node this page draws separately.

    Interstate stays measured, and still wins: a coordinate outside Victoria says more about how
    to read the node than its type does. A node with no code has no master record, which is a
    fact worth showing rather than papering over — the label simply carries no tag.
    """
    pts = site_points(p)
    roles = facility_roles()
    delivers = set(q.groupby("dest").articles.sum()[lambda s: s > 0].index)
    # the FIRST touch column, whatever it is called on this basis — "entry" only exists on the
    # role one, and naming it here is what made the two bases diverge in the first place
    sorts = set(q.groupby(STAGE_KEYS[1]).articles.sum()[lambda s: s > 0].index)
    meta = {}
    for k in ORG_ORDER:
        meta["ORG_" + k] = {"n": ORG_LABEL[k], "k": "org", "s": ORG_SUB[k]}
    names = set()
    for i, k in enumerate(STAGE_KEYS[1:], start=1):
        names |= {(PREFIX[i], n) for n in q[k].unique()}
    for pre, n in names:
        key = f"{pre}_{n}"
        if n in PSEUDO:
            lab, kind = PSEUDO[n]
            sub = (f"folded — {keep_folded_label(keep, pre)}" if n == "OTHER" else "")
            meta[key] = {"n": lab, "k": kind, "s": sub}
            continue
        interstate = False
        if n in pts.index:
            la, lo = pts.loc[n]
            interstate = not (VIC_BOX["lat"][0] <= la <= VIC_BOX["lat"][1]
                              and VIC_BOX["lon"][0] <= lo <= VIC_BOX["lon"][1])
        r = roles.loc[n] if n in roles.index else None
        if interstate:
            kind = "interstate"
        elif r is not None:
            kind = {"sort": "hub", "depot": "pdc", "retail": "pdc"}.get(r.kind, "other")
        else:
            kind = "pdc" if n in delivers else "hub" if n in sorts else "other"
        m = {"n": short(n), "k": kind, "s": str(n)}
        if r is not None:
            m |= {"c": r.code, "r": r.role or r.code, "fid": int(r.id),
                  "term": bool(r.terminates)}
        meta[key] = m
    return meta


def keep_folded_label(keep, pre):
    i = PREFIX.index(pre)
    n = keep[STAGE_KEYS[i] + "_folded"]
    return f"{n:,} site{'s' if n != 1 else ''} below the line"


def typed_share(q):
    """Share of the volume standing at a NAMED site that wcc_list can type, over all five stages.

    ONCE / NONE / OTHER are states and folds, not buildings, so they are out of the denominator —
    counting them would understate the coverage by the size of the sorted-once band.
    """
    roles = facility_roles()
    num = den = 0
    for k in STAGE_KEYS[1:]:
        v = q.groupby(k).articles.sum()
        v = v[[n not in PSEUDO for n in v.index]]
        num += v[[n in roles.index for n in v.index]].sum()
        den += v.sum()
    return 100 * num / max(den, 1)


def selections(q):
    """Every filter the buttons can ask for: the networks, then the products inside them.

    The product buttons follow their NETWORK, not raw volume — ordering them by volume alone
    interleaved the two operations (Parcel Post, ST Road, Express Post, ST Premium) and made the
    grouping the whole control is trying to show unreadable.
    """
    pk = products(q)
    nets = [(k, lab, None if grp is None else [x for x in grp if x in pk])
            for k, lab, grp in NETWORKS]
    nets = [(k, lab, g) for k, lab, g in nets if g is None or g]
    grouped = [x for _, _, g in nets if g for x in g]
    return nets + [(x, PRODUCT_BTN[x], [x]) for x in grouped + [x for x in pk if x not in grouped]]


def build(q, keep, p):
    data = {"flows": {}, "stats": {}, "cols": COLS,
            "nodes": nodes_meta(q, keep, p), "orgOrder": ORG_ORDER,
            "pseudo": list(PSEUDO), "prefix": list(PREFIX),
            "basis": BASIS, "depth": DEPTH,
            # the first building column, and the later ones — see kindOf() in the page
            "hubCol": 1,
            "pdcCols": [2] if BASIS == "role" else list(range(2, len(COLS) - 1))}
    for ck, _, grp in selections(q):
        cs = q if grp is None else q[q.pk.isin(grp)]
        data["flows"][ck], data["stats"][ck] = {}, {}
        for b in buckets():
            sub = cs if b == "all" else cs[cs.bucket == b]
            data["flows"][ck][b] = flows(sub)
            data["stats"][ck][b] = headline(sub)
    return data


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE PAGE
# ══════════════════════════════════════════════════════════════════════════════════════
from sankey_from_scans import CSS as BASE_CSS      # noqa: E402 — same visual language, one source

CSS = (BASE_CSS.replace("<title>Melbourne parcel paths — 20 May 2026</title>",
                        '<meta charset="utf-8">\n'
                        "<title>Parcel paths — new scan extract, 11–12 May 2026</title>")
       .replace("</style>", """
:root{ --ist:#8b5cf6; --halo:#ffffff }
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){ --ist:#a78bfa; --halo:#191d24 } }
:root[data-theme="dark"]{ --ist:#a78bfa; --halo:#191d24 }
.legend i.ist{background:var(--ist)}
/* A label sits over ribbons, so it is painted with a halo of the panel colour behind it —
   stroke first, fill second — rather than being made pale enough to lose. */
.nodelab,.nodeval{paint-order:stroke fill;stroke:var(--halo);stroke-width:3.5px;
  stroke-linejoin:round}
.nodelab{font-size:12px;fill:var(--ink-2)}
.nodeval{font-size:10.5px;fill:var(--ink-3)}
.leader{stroke:var(--line);fill:none;stroke-width:1}
.wcc{font-size:9.5px;font-family:ui-monospace,Menlo,Consolas,monospace;fill:var(--ink-3);
  letter-spacing:.04em}
.coltot{font-size:10.5px;font-family:ui-monospace,Menlo,Consolas,monospace;fill:var(--ink-3);
  font-variant-numeric:tabular-nums}
.coltot.bad{fill:var(--pdc);font-weight:700}
.colhead{font-size:11px;letter-spacing:.1em;text-transform:uppercase;fill:var(--ink-3)}
.headrule{stroke:var(--line);stroke-width:1}
.seg .div{width:1px;background:var(--line);margin:4px 5px;align-self:stretch}
.rib{mix-blend-mode:normal}
.rib.dim{opacity:.10}
.nd.dim{opacity:.25}
</style>"""))

BODY = """<div class="wrap">
<header>
  <p class="eyebrow">New scan extract &middot; 11&ndash;12 May 2026 &middot; {{TRACED}} articles
  &middot; {{BASISLAB}}</p>
  <h1>Parcel paths, lodgement to delivering site</h1>
  <p class="lede">Every product in the second extract &mdash; Australia Post parcels
  <em>and</em> StarTrack &mdash; drawn from the moment it reaches <strong>Victoria</strong>.
  The first three buttons pick a <strong>network</strong>, because these are two operations
  sharing buildings rather than one with a product mix.
  The node set is derived by volume rather than typed &mdash; <strong>{{SITES}} facilities</strong>
  appear across the roles &mdash; and the slider decides where the line falls. Each label carries
  the site&rsquo;s <strong>work centre code</strong> from the review workbook, so colour says what
  a building <em>is</em> rather than what it happened to do here; <strong>interstate</strong>
  stays measured, from the coordinate. Every column shows its own total: the same articles,
  counted at six different moments.</p>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <div class="grp"><span>Network &amp; product</span>
    <div class="seg" role="group" aria-label="Network and product">{{PRODBTNS}}</div>
  </div>
  <div class="grp"><span>{{BUCKETLAB}}</span>
    <div class="seg" role="group" aria-label="{{BUCKETLAB}}">{{BUCKETBTNS}}</div>
  </div>
  <div class="grp"><span>Sites per column</span>
    <div class="thr">
      <input id="thr" type="range" min="4" max="{{MAXTOP}}" step="1" value="14"
             aria-label="How many sites to draw in each column before folding">
      <input id="thrn" type="number" min="4" max="{{MAXTOP}}" step="1" value="14"
             aria-label="Sites per column">
      <span class="unit">then fold</span>
    </div>
  </div>
  <div class="grp"><span>View</span>
    <div class="seg" role="group" aria-label="View">
      <button data-view="flow" aria-pressed="true">Diagram</button>
      <button data-view="table" aria-pressed="false">Table</button>
    </div>
  </div>
</div>
<p class="sub" id="foldnote" style="margin:8px 0 0"></p>

<div class="legend" id="legend">
</div>

<div class="figure" id="fig">
  <div class="figscroll"><svg id="sankey" role="img" aria-label="Sankey diagram of parcel flow
    from four lodgement source bands through the first Victorian handling, first Victorian sort,
    second sort and despatch to the delivering site"></svg></div>
  <div class="figfoot"><span class="mono" id="figtot"></span></div>
</div>

<div id="tablewrap" hidden>
  <h2>Every flow, as numbers</h2>
  <p>The same data the diagram draws, for the current filters. Sorted by volume.</p>
  <div class="tablewrap"><table id="flowtable"><thead><tr>
    <th>Stage</th><th>From</th><th>To</th><th style="text-align:right">Articles</th>
    <th style="text-align:right">Share of stage</th>
  </tr></thead><tbody></tbody></table></div>
</div>

<footer>
  <p>Built from <code>inputs/new_scan_events/</code> by <code>sankey_from_new_scans.py</code>,
  over the reduction in <code>analyse_new_scans.py</code>. Volumes are <b>articles</b>.
  {{TRACED}} articles across {{CONS}} consignments, delivered 11&ndash;12 May 2026.
  <b>Cohort:</b> {{SCOPE}} &mdash; the page draws the whole extract, unlike
  <code>analyse_new_scans.PRODUCT_SCOPE</code>, which narrows the <i>analysis</i> to the Australia
  Post parcel stream. Both are right about different things: the buildings and lanes are real
  whichever product moves through them, but a <b>pooled rate is the average of two operations and
  describes neither</b> &mdash; on this extract {{CONTRAST}}. So every rate above should be read
  with a network selected, and &ldquo;All&rdquo; is a picture of the freight, not a description of
  an operation. StarTrack sites are drawn from their own coordinates like any other.
  <b>Scope:</b> the four middle columns count <b>Victorian events only</b>, decided on each
  event&rsquo;s own coordinate. An interstate parcel is sorted in Brisbane or Sydney before it
  ever reaches Victoria &mdash; {{PRESORTED}} of everything here arrives already sorted
  &mdash; and counting
  those would make &ldquo;first sorted at&rdquo; read <code>BRISBANE PARCEL FACILITY</code>, which
  is true about the parcel and useless about the network being modelled. That history is not
  discarded: it is the <i>Arrived pre-sorted</i> stat, and the source band still says the freight
  came from interstate.
  <b>Source bands:</b> interstate is decided on the lodgement coordinate; Victorian freight is
  then split on whether that point falls inside the dissolved first-mile catchment, and only the
  metro side carries a kept-at-site band. <b>What each site is:</b> the work centre code beside each
  label is read from <code>wcc_list</code> in the review workbook, joined on the facility name
  &mdash; a safe key here, since every terminating row in the extract matches one and its ID and
  type agree exactly. It types {{TYPED}} of the volume standing at a named site, and it corrects
  the old rule, which inferred a role from behaviour: <b>Bayswater PDC</b> and <b>Avalon Parcel
  Facility</b> never terminate volume in this extract and so were drawn as hubs, but both are
  <code>PDEP</code> parcel depots, while <b>Sunshine West Parcel Delivery</b> terminates more than
  any other site and was drawn as a depot &mdash; it is a <code>PC</code>, a parcel centre, the
  same class as Melbourne Parcel Facility. A node with no code has no record in the list and keeps
  the inferred role; the tooltip says which of the two you are looking at. The sheet&rsquo;s
  <code>Available_in_terminating_data</code> flag is <i>not</i> used to filter that list &mdash;
  it marks which sites TERMINATE volume, so every hub is a &ldquo;No&rdquo;, and filtering on it
  would take the share of first-sort volume that can be typed from 91% to 25%. It is shown in the
  tooltip instead. <b>No name dictionary:</b> node names are still whatever the scan called them
  and nothing is aliased, so two names for one building are drawn as two nodes &mdash;
  <code>SUNSHINE WEST PDC</code> has no record in the list and sits 0.7 km from
  <code>SUNSHINE WEST PARCEL DELIVERY</code>, which is the same building twice;
  <code>report_alias_suspects</code> in <code>analyse_new_scans.py</code> lists the candidates
  with their codes. The delivering site is the last <code>ZPT_DELIVER</code> scan, falling back to
  <code>Terminating_facility_name</code>.</p>
</footer>
</div>
<div id="tip" role="status" aria-live="polite"></div>"""

JS = r"""
const $ = s => document.querySelector(s);
const fmt = n => n.toLocaleString("en-AU");
// FROM PYTHON, not typed: the prefixes are the column ids and the path basis invents one per
// depth (T1, T2, …). Hardcoded here, colOf() returned -1 for every touch node and foldLinks
// dereferenced tot[-1] — the page rendered blank with a console error and nothing Python-side
// could see it.
const PRE = DATA.prefix;
const KCOL = {hub:"var(--hub)", xd:"var(--xd)", pdc:"var(--pdc)", interstate:"var(--ist)",
              org:"var(--other)", state:"var(--other)", fold:"var(--other)", other:"var(--other)"};
let CLS = "ALL", SORTS = "all", TOP = 14, VIEW = "flow", CAT = null;
// ── WHAT A RIBBON IS, in four categories ────────────────────────────────────────────
// Colour used to repeat the node typing, which the labels and the tooltip already carry. It
// says something they cannot instead: which round of sorting a ribbon feeds, and whether the
// freight had to change buildings to get there.
//   hub    first sorted in the building that handled it
//   xd     CROSS-DOCK — handled at one building, first sorted at another. Kept green where that
//          same freight comes back for a second sort, so the cohort can be followed across.
//   pdc    a second sort that was NOT cross-docked
//   other  not a sort: sorted once and sent on, never sorted, or the run to the depot
// Only columns 1 and 2 are targets of a sort, and a source band is never a target, so testing
// the pseudo-nodes is enough to know a real building.
const PSEUDO_K = new Set(DATA.pseudo);   // from Python, so a new state cannot be missed here
// DATA.hubCol / DATA.pdcCols come from Python because "which column is a building" is a fact
// about the BASIS, not about the drawing: on the role basis column 3 is "Despatched from" and is
// deliberately not coloured as a second sort, while on the path basis every touch column is a
// building and columns 2..DEPTH all colour the same way.
function kindOf(l){
  const tc = colOf(l.t), tk = keyOf(l.t);
  if (PSEUDO_K.has(tk)) return "other";
  if (tc === DATA.hubCol) return l.x ? "xd" : "hub";
  return DATA.pdcCols.includes(tc) ? (l.x ? "xd" : "pdc") : "other";
}

const meta = id => DATA.nodes[id] || {n:id.slice(id.indexOf("_")+1), k:"other", s:""};
const colOf = id => PRE.indexOf(id.slice(0, id.indexOf("_")));
const keyOf = id => id.slice(id.indexOf("_") + 1);

// ── the fold, done here so the slider is live ────────────────────────────────────────
// The build already dropped the long tail; this narrows it further. A folded node keeps its
// volume, it just carries it under "Other" — the diagram never quietly loses articles.
function foldLinks(links, top){
  const tot = DATA.cols.map(() => ({}));      // one per column, however many there are
  links.forEach(l => {
    tot[colOf(l.s)][l.s] = (tot[colOf(l.s)][l.s] || 0) + l.v;
    tot[colOf(l.t)][l.t] = (tot[colOf(l.t)][l.t] || 0) + l.v;
  });
  const keep = tot.map((m, i) => {
    const names = Object.keys(m).filter(k => !PSEUDO_K.has(keyOf(k)));
    if (i === 0) return new Set(Object.keys(m));
    names.sort((a,b) => m[b] - m[a]);
    return new Set(names.slice(0, top).concat(
      Object.keys(m).filter(k => PSEUDO_K.has(keyOf(k)))));
  });
  const folded = keep.map((s, i) => Object.keys(tot[i]).length - s.size);
  const map = id => keep[colOf(id)].has(id) ? id : PRE[colOf(id)] + "_OTHER";
  // The cross-dock flag is part of the KEY, not just a passenger. Aggregating on the pair alone
  // merged the flagged and unflagged rows of the same pair and dropped `x` from the result, so
  // every ribbon reached kindOf() with l.x undefined and the green band could never be drawn.
  const agg = new Map();
  links.forEach(l => {
    const k = map(l.s) + "|" + map(l.t) + "|" + (l.x ? 1 : 0);
    agg.set(k, (agg.get(k) || 0) + l.v);
  });
  return {links:[...agg].map(([k,v]) => {const [s,t,x] = k.split("|");
                                         return {s,t,v,x:+x};}), folded};
}

// ── layout ──────────────────────────────────────────────────────────────────────────
// L is the strip the FIRST column's labels hang back into and R the strip the LAST column's hang
// forward into; every other column labels rightwards into the gap. The old code reserved 178px on
// the left and then labelled column 0 rightwards anyway, so that strip was empty and the last
// column's labels were laid back over its own inbound ribbons — which is most of why the right
// hand side looked like a smudge.
function layout(links, W, H){
  const NW = 13, PAD = 4, L = 186, R = 200;
  const nodes = new Map();
  const touch = (id, side, v) => {
    if (!nodes.has(id)) nodes.set(id, {id, col:colOf(id), in:0, out:0, ...meta(id)});
    nodes.get(id)[side] += v;
  };
  links.forEach(l => { touch(l.s,"out",l.v); touch(l.t,"in",l.v); });
  nodes.forEach(n => n.v = Math.max(n.in, n.out));

  const cols = DATA.cols.map(() => []);
  nodes.forEach(n => cols[n.col].push(n));
  const tail = k => DATA.pseudo.indexOf(keyOf(k));
  cols.forEach((c, i) => c.sort((a,b) => {
    if (i === 0) return DATA.orgOrder.indexOf(keyOf(a.id)) - DATA.orgOrder.indexOf(keyOf(b.id));
    const ta = tail(a.id), tb = tail(b.id);
    if ((ta >= 0) !== (tb >= 0)) return ta >= 0 ? 1 : -1;
    if (ta >= 0 && tb >= 0) return ta - tb;
    return b.v - a.v;
  }));
  const colSum = cols.map(c => c.reduce((a,n) => a + n.v, 0));
  const maxN = Math.max(...cols.map(c => c.length));
  const unit = (H - (maxN - 1) * PAD) / Math.max(...colSum, 1);
  // (cols.length - 1), not 4: the page had five columns when this was written and now the depth
  // decides how many, so a hardcoded divisor drew a depth-3 page off the right edge.
  const xs = cols.map((_, i) => L + i * ((W - L - R - NW) / Math.max(cols.length - 1, 1)));
  cols.forEach((c, i) => {
    const h = c.reduce((a,n) => a + Math.max(n.v * unit, 1.5), 0) + (c.length - 1) * PAD;
    let y = (H - h) / 2;
    c.forEach(n => { n.h = Math.max(n.v * unit, 1.5); n.x = xs[i]; n.y = y; y += n.h + PAD; });
  });

  const N = id => nodes.get(id);
  links.forEach(l => { l.sn = N(l.s); l.tn = N(l.t); l.h = l.v * unit; });
  const bySrc = new Map(), byTgt = new Map();
  links.forEach(l => {
    (bySrc.get(l.s) || bySrc.set(l.s, []).get(l.s)).push(l);
    (byTgt.get(l.t) || byTgt.set(l.t, []).get(l.t)).push(l);
  });
  bySrc.forEach(ls => { ls.sort((a,b) => a.tn.y - b.tn.y); let o = 0;
                        ls.forEach(l => { l.sy = l.sn.y + o; o += l.h; }); });
  byTgt.forEach(ls => { ls.sort((a,b) => a.sn.y - b.sn.y); let o = 0;
                        ls.forEach(l => { l.ty = l.tn.y + o; o += l.h; }); });
  return {nodes:[...nodes.values()], links, cols, xs, NW, unit};
}

// ── labels ──────────────────────────────────────────────────────────────────────────
// Node rectangles are stacked by volume, so in a busy column a dozen thin nodes sit inside 40px
// and their labels land on top of each other. Two lines of text need PITCH px whatever the node
// is worth, so the labels are laid out as their OWN stack: each one starts at its node's centre,
// is pushed down until it clears the label above, then the stack is pulled up off the bottom
// edge. Where that moves a label away from its node, a leader line says which node it belongs to.
//
// A column can only hold H/PITCH labels, so when there are more nodes than that the smallest are
// left unlabelled rather than allowed to overlap — they keep their rectangle and their tooltip.
const PITCH = 28;

function place(cols, H){
  cols.forEach(c => {
    const cap = Math.max(1, Math.floor((H - 12) / PITCH));
    const big = new Set([...c].sort((a, b) => b.v - a.v).slice(0, cap).map(n => n.id));
    const lab = c.filter(n => big.has(n.id));
    c.forEach(n => n.lab = false);
    let y = 10;                                   // down, clearing the one above
    lab.forEach(n => { n.ly = Math.max(n.y + n.h / 2, y); y = n.ly + PITCH; });
    let z = H - 6;                                // up, off the bottom edge
    for (let i = lab.length - 1; i >= 0; i--) { lab[i].ly = Math.min(lab[i].ly, z); z = lab[i].ly - PITCH; }
    lab.forEach(n => n.lab = true);
  });
}

function ribbon(l, NW){
  const x0 = l.sn.x + NW, x1 = l.tn.x, mx = (x0 + x1) / 2;
  const a = l.sy, b = l.sy + l.h, c = l.ty, d = l.ty + l.h;
  return `M${x0},${a} C${mx},${a} ${mx},${c} ${x1},${c} L${x1},${d} `
       + `C${mx},${d} ${mx},${b} ${x0},${b} Z`;
}

// The legend doubles as the filter: click a category to isolate it, click again for all four.
// It is a HIGHLIGHT, never a re-measurement — widths and column totals do not move — so the
// counts beside each swatch stay true whichever category is selected.
const LG = [["hub", "First sort", "first sorted in the building that handled it"],
            ["xd", "Cross-dock", "handled at one building, first sorted at another — kept green where that same freight returns for a second sort"],
            ["pdc", "Second sort", "round-2 work that was NOT cross-docked"],
            ["other", "Not a sort", "sorted once and sent on, never sorted, or the run to the depot"]];
function legend(links){
  const vol = {hub:0, xd:0, pdc:0, other:0};
  links.forEach(l => { vol[kindOf(l)] += l.v; });
  // The denominator is THE DAY, not the ribbon total: there are four stages, so summing them
  // counts the day four times and grey collects every ribbon past the second sort. Against the
  // day each share is a fact about parcels, and they deliberately do not sum to 100 — a parcel
  // can be first sorted in place AND come back for a second round.
  const day = Math.max(links.filter(l => colOf(l.s) === 0).reduce((a, l) => a + l.v, 0), 1);
  // Green is a COHORT, not a stage, so it is the one category that would double-count. Count it
  // once, in the stage that defines it, and name the onward volume separately.
  const xd0 = links.filter(l => colOf(l.s) === 0 && kindOf(l) === "xd")
                   .reduce((a, l) => a + l.v, 0);
  const xdOn = vol.xd - xd0;
  vol.xd = xd0;
  $("#legend").innerHTML = LG.map(([k, name, note]) =>
    `<button class="lg" data-cat="${k}" aria-pressed="${CAT === k}" title="${note}">
       <i style="background:${KCOL[k]}"></i><b>${name}</b>
       <span class="lgv">${fmt(vol[k])}</span>
       <span class="lgp">${k === "other" ? "every stage"
         : (100 * vol[k] / day).toFixed(1) + "% of the day"}</span>
       ${k === "xd" && xdOn ? `<span class="lgp">+${fmt(xdOn)} again at the 2nd sort</span>` : ""}
       </button>`).join("")
    + `<span class="lgnote"><i style="background:var(--ist)"></i>violet node = outside Victoria,
       by coordinate &middot; building type is on the label and the tooltip</span>`;
  $("#legend").querySelectorAll("[data-cat]").forEach(b =>
    b.addEventListener("click", () => { CAT = CAT === b.dataset.cat ? null : b.dataset.cat;
                                        draw(); }));
}

function draw(){
  const raw = DATA.flows[CLS][SORTS];
  const {links, folded} = foldLinks(raw, TOP);
  const svg = $("#sankey");
  legend(links);
  const per = [0,1,2,3,4].map(i => {
    const s = new Set();
    links.forEach(l => { if (colOf(l.s) === i) s.add(l.s); if (colOf(l.t) === i) s.add(l.t); });
    return s.size;
  });
  const W = 1720, HEAD = 54, H = Math.max(620, 32 * Math.max(...per));
  svg.setAttribute("viewBox", `0 0 ${W} ${H + HEAD + 14}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H + HEAD + 14);
  const G = layout(links, W, H, TOP);
  place(G.cols, H);
  const total = G.cols[0].reduce((a, n) => a + n.v, 0);

  let out = `<g transform="translate(0,${HEAD})">`;
  G.links.forEach((l, i) => {
    const kind = kindOf(l);
    out += `<path class="rib" data-i="${i}" d="${ribbon(l, G.NW)}" fill="${KCOL[kind]}"
             opacity="${CAT && kind !== CAT ? .04 : .26}"></path>`;
  });
  G.nodes.forEach(n => {
    // Nodes go NEUTRAL so the four ribbon colours mean one thing each. The work-centre type
    // the wcc_list gives every building is not lost — it is on the label and in the tooltip.
    // INTERSTATE keeps its violet: it is measured off a coordinate, it is the most distinctive
    // thing this extract can say, and violet is outside the ribbon palette so it cannot collide.
    out += `<rect class="nd" data-id="${n.id}" x="${n.x}" y="${n.y}" width="${G.NW}"
             height="${n.h}" rx="2.5"
             fill="${n.k === "interstate" ? "var(--ist)" : "var(--ink-3)"}"></rect>`;
    if (!n.lab) return;
    const back = n.col === 0;                       // the only column that labels leftwards
    const tx = back ? n.x - 10 : n.x + G.NW + 10;
    const cy = n.y + n.h / 2;
    if (Math.abs(n.ly - cy) > 2.5){                 // say which node this label is for
      const a = back ? n.x - 2 : n.x + G.NW + 2, b = back ? tx + 3 : tx - 3;
      out += `<path class="leader" d="M${a},${cy} L${(a + b) / 2},${cy} `
           + `L${(a + b) / 2},${n.ly} L${b},${n.ly}"></path>`;
    }
    const anc = back ? "end" : "start";
    // The code rides on the name, because "Sunshine West Delivery · PC" answers a question the
    // name alone gets wrong — that building is a parcel centre, not a delivery depot.
    const tag = n.c ? `<tspan class="wcc"> ${n.c}</tspan>` : "";
    out += `<text class="nodelab" x="${tx}" y="${n.ly - 6}" text-anchor="${anc}"
             dominant-baseline="middle">${anc === "end" ? tag + " " : ""}${n.n}`
         + `${anc === "end" ? "" : tag}</text>`
         + `<text class="nodeval mono" x="${tx}" y="${n.ly + 7}" text-anchor="${anc}"
             dominant-baseline="middle">${fmt(n.v)} · ${(100 * n.v / total).toFixed(1)}%</text>`;
  });
  out += "</g>";
  // Headers last and in their own band: drawn first, the ribbons painted straight over them.
  out += `<path class="headrule" d="M0,${HEAD - 6} L${W},${HEAD - 6}"></path>`;
  // Every column carries its own total. They are all the same number by construction — each
  // stage is the same articles counted at a different moment — but "by construction" is exactly
  // the kind of claim that stops being true without anyone noticing, so the page states it and
  // marks any column that disagrees with the first one instead of quietly balancing.
  const tots = G.cols.map(c => c.reduce((a, n) => a + n.v, 0));
  DATA.cols.forEach((c, i) => {
    const back = i === 0;
    const x = G.xs[i] + (back ? G.NW : 0), anc = back ? "end" : "start";
    const bad = tots[i] !== tots[0];
    out += `<text class="colhead" x="${x}" y="${HEAD - 32}" text-anchor="${anc}">`
         + `${i + 1}. ${c}</text>`
         + `<text class="coltot${bad ? " bad" : ""}" x="${x}" y="${HEAD - 16}" `
         + `text-anchor="${anc}">${fmt(tots[i])} articles`
         + `${bad ? " ✗ does not balance" : ""}</text>`;
  });
  svg.innerHTML = out;
  $("#figtot").textContent = `${fmt(total)} articles · ${G.links.length} lanes · `
    + `${G.nodes.length} nodes drawn`;
  const nf = folded.reduce((a,b) => a + b, 0);
  $("#foldnote").innerHTML = nf
    ? `Drawing the top <b>${TOP}</b> sites per column; <b>${nf}</b> smaller node${nf===1?"":"s"} `
      + `folded into &ldquo;Other&rdquo;, which keeps their volume.`
    : "Every node in the build is drawn.";
  wire(G, total);
  stats();
  table(G, total);
}

function wire(G, total){
  const svg = $("#sankey"), tip = $("#tip");
  const show = (html, e) => {
    tip.innerHTML = html; tip.style.display = "block";
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(e.clientX + 14, innerWidth - r.width - 12) + "px";
    tip.style.top = Math.min(e.clientY + 14, innerHeight - r.height - 12) + "px";
  };
  const hide = () => { tip.style.display = "none";
    svg.querySelectorAll(".rib,.nd").forEach(el => el.classList.remove("dim")); };
  svg.querySelectorAll(".rib").forEach(el => {
    el.addEventListener("mousemove", e => {
      const l = G.links[+el.dataset.i];
      svg.querySelectorAll(".rib").forEach(o => o.classList.toggle("dim", o !== el));
      show(`<b>${l.sn.n}</b> &rarr; <b>${l.tn.n}</b><br>${fmt(l.v)} articles · `
         + `${(100*l.v/total).toFixed(2)}% of the day`
         + (l.sn.s ? `<br><span class="sub">${l.sn.s}</span>` : ""), e);
    });
    el.addEventListener("mouseleave", hide);
  });
  svg.querySelectorAll(".nd").forEach(el => {
    el.addEventListener("mousemove", e => {
      const n = G.nodes.find(x => x.id === el.dataset.id);
      svg.querySelectorAll(".rib").forEach(o => {
        const l = G.links[+o.dataset.i];
        o.classList.toggle("dim", l.s !== n.id && l.t !== n.id);
      });
      const rec = n.c
        ? `<br><span class="sub">${n.c}${n.r && n.r !== n.c ? " — " + n.r : ""} · #${n.fid}`
          + ` · ${n.term ? "terminates volume here" : "never terminates here"}</span>`
        : (n.k === "org" || n.k === "state" || n.k === "fold" ? ""
           : `<br><span class="sub">no record in wcc_list — role inferred from behaviour`
             + `</span>`);
      show(`<b>${n.n}</b><br>${fmt(n.v)} articles · ${(100*n.v/total).toFixed(1)}%`
         + `<br>in ${fmt(n.in)} · out ${fmt(n.out)}`
         + (n.s ? `<br><span class="sub">${n.s}</span>` : "") + rec, e);
    });
    el.addEventListener("mouseleave", hide);
  });
}

function stats(){
  const s = DATA.stats[CLS][SORTS], whole = DATA.stats[CLS].all;
  const filt = SORTS !== "all";
  const row = (k, v, sub) => `<div class="stat"><div class="k">${k}</div>`
    + `<div class="v">${v}</div><div class="s">${sub}</div></div>`;
  $("#stats").innerHTML = [
    row("Articles", fmt(s.articles), filt
        ? (100 * s.articles / whole.articles).toFixed(1) + "% of the " + fmt(whole.articles)
        : "both delivery dates in the extract"),
    row("Sorts per parcel", s.mean.toFixed(2), "machine sorts in Victoria only"),
    row("Arrived pre-sorted", s.presorted.toFixed(1) + "%", "already sorted interstate before it got here"),
    row("Lodged in Victoria", s.vic.toFixed(1) + "%", "by lodgement coordinate"),
    row("Outside the catchment", s.region.toFixed(1) + "%", "regional Vic — measured, not assumed"),
    row("Kept at the site", s.kept.toFixed(1) + "%", "understated until aliases are merged"),
    row("Cross-docked", s.xdock.toFixed(1) + "%", "handled at one site, first sorted at another"),
  ].join("");
}

function table(G, total){
  const rows = G.links.slice().sort((a,b) => b.v - a.v).map(l =>
    `<tr><td>${DATA.cols[l.sn.col]} &rarr; ${DATA.cols[l.tn.col]}</td><td>${l.sn.n}</td>`
    + `<td>${l.tn.n}</td><td style="text-align:right" class="mono">${fmt(l.v)}</td>`
    + `<td style="text-align:right" class="mono">${(100*l.v/total).toFixed(2)}%</td></tr>`);
  $("#flowtable").querySelector("tbody").innerHTML = rows.join("");
}

document.querySelectorAll("[data-cls]").forEach(b => b.addEventListener("click", () => {
  CLS = b.dataset.cls;
  document.querySelectorAll("[data-cls]").forEach(o => o.setAttribute("aria-pressed", o === b));
  draw();
}));
document.querySelectorAll("[data-sorts]").forEach(b => b.addEventListener("click", () => {
  SORTS = b.dataset.sorts;
  document.querySelectorAll("[data-sorts]").forEach(o => o.setAttribute("aria-pressed", o === b));
  draw();
}));
document.querySelectorAll("[data-view]").forEach(b => b.addEventListener("click", () => {
  VIEW = b.dataset.view;
  document.querySelectorAll("[data-view]").forEach(o => o.setAttribute("aria-pressed", o === b));
  $("#fig").hidden = VIEW !== "flow";
  $("#tablewrap").hidden = VIEW !== "table";
}));
const thr = $("#thr"), thrn = $("#thrn");
const setTop = v => { TOP = Math.max(4, Math.min(+thr.max, +v));
                      thr.value = TOP; thrn.value = TOP; draw(); };
thr.addEventListener("input", e => setTop(e.target.value));
thrn.addEventListener("change", e => setTop(e.target.value));
draw();
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true", help="re-read the eight CSVs first")
    ap.add_argument("--top", type=int, default=40,
                    help="sites kept per column in the BUILD; the page folds further, live")
    ap.add_argument("--basis", choices=("path", "role"), default=BASIS,
                    help="path (default): the columns are BUILDINGS TOUCHED, in time order, "
                         "capped at --depth. role: the original five columns — first sort, "
                         "second sort, last sort")
    ap.add_argument("--depth", type=int, default=DEPTH, metavar="N",
                    help=f"path basis only: touch columns between the source band and the "
                         f"delivering site (default {DEPTH}). A journey longer than N keeps its "
                         f"FIRST and LAST building and folds the middle, which is the "
                         f"exporter's PATH_CAP_RULE='first_last'")
    ap.add_argument("--touch", choices=tuple(EVIDENCE), default="entry",
                    help="path basis only: which events may name a building (default entry)")
    args = ap.parse_args(argv)
    if args.depth < 1:
        ap.error("--depth must be at least 1")
    configure(args.basis, args.depth)
    global TARGET
    TARGET = target_for(args.basis, args.depth, args.touch)

    p = load_paths(rebuild=args.rebuild)
    chains = None
    if args.basis == "path":
        # NOT `full_chain`: that is every event, on whichever bar the reduction happened to use.
        # This is one evidence bar, in time order — the same chain the itinerary pages read, from
        # the same cached implementation, so the three pages cannot disagree about the journey.
        ch = chain_in_time_order(EVIDENCE[args.touch], args.touch,
                                 rebuild=args.rebuild).reindex(p.Consignment_ID)
        chains = [c if isinstance(c, list) else [] for c in ch]
    q = frame(p, chains)
    keep = keep_sets(q, args.top)
    q = fold(q, keep)
    data = build(q, keep, p)

    payload = json.dumps(data, separators=(",", ":"))
    sel = selections(q)
    btns = "".join(
        ('<span class="div"></span>' if i == len(NETWORKS) else "")
        + f'<button data-cls="{k}" aria-pressed="{"true" if k == "ALL" else "false"}">{lab}</button>'
        for i, (k, lab, _) in enumerate(sel))
    # The pooling warning quotes THIS extract rather than a remembered number, so it cannot go
    # stale: the two rates it contrasts are the ones the buttons will show when clicked.
    ap = data["stats"].get("AP", {}).get("all")
    st = data["stats"].get("STK", {}).get("all")
    contrast = (f"AusPost cross-docks {ap['xdock']:.1f}% of its volume and holds "
                f"{ap['kept']:.1f}% on site, StarTrack {st['xdock']:.1f}% and {st['kept']:.1f}%"
                if ap and st else "the products differ")
    body = (BODY.replace("{{TRACED}}", f"{int(p.articles.sum()):,}")
                .replace("{{CONS}}", f"{len(p):,}")
                .replace("{{SITES}}", f"{len(site_points(p)):,}")
                .replace("{{SCOPE}}", ", ".join(PRODUCT_BTN[k]
                                               for k, _, g in sel if g and len(g) == 1))
                .replace("{{CONTRAST}}", contrast)
                .replace("{{PRESORTED}}", f"{data['stats']['ALL']['all']['presorted']:.1f}%")
                .replace("{{TYPED}}", f"{typed_share(q):.0f}%")
                .replace("{{PRODBTNS}}", btns)
                .replace("{{BUCKETLAB}}", bucket_label())
                .replace("{{BUCKETBTNS}}", bucket_buttons())
                .replace("{{BASISLAB}}", basis_label(args.touch))
                .replace("{{MAXTOP}}", str(args.top)))
    assert "{{" not in body, f"unfilled token: {body[body.index('{{'):][:40]}"
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(CSS + body + "<script>const DATA=" + payload + ";</script>\n"
                      + "<script>" + JS + "</script>\n")
    tot = int(p.articles.sum())
    for k, lab, _ in sel:
        s = data["stats"][k]["all"]
        print(f"    {lab:<13} {s['articles']:>9,} articles | {s['vic']:>5.1f}% Vic "
              f"| region {s['region']:>4.1f}% | kept {s['kept']:>4.1f}% "
              f"| sorts {s['mean']:.2f} | buildings {s['meantouch']:.2f} "
              f"| x-dock {s['xdock']:>5.1f}%")
    roles = facility_roles()
    for i, k in enumerate(STAGE_KEYS[1:], start=1):
        # over REAL buildings only: ONCE/NONE/OTHER are states and folds, not sites to type.
        v = q.groupby(k).articles.sum()
        v = v[[n not in PSEUDO for n in v.index]]
        typed = v[[n in roles.index for n in v.index]].sum()
        print(f"    {COLS[i]:<22} {q[k].nunique():>4} nodes drawn, "
              f"{keep[k + '_folded']:>5,} folded in the build, "
              f"{100 * typed / max(v.sum(), 1):>5.1f}% of the volume at a named site is typed")
    print(f"  every stage balances at {tot:,} articles")
    if BASIS == "path":
        _c = data["stats"]["ALL"]["all"]
        print(f"  basis {BASIS} / {args.touch} bar / depth {DEPTH} (first_last) — "
              f"mean {_c['meantouch']:.2f} buildings, {_c['capped']:.1f}% of articles are in "
              f"more than {DEPTH} and had their middle folded")
    # parents[1] is the repo root; `parent` is plotting/, which TARGET has never been under —
    # the line raised ValueError after the file was already written, so the page looked like a
    # crash that had in fact succeeded.
    print(f"  wrote {TARGET.relative_to(Path(__file__).resolve().parents[1])} "
          f"({TARGET.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
