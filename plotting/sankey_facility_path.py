"""Sankey of HOW MANY BUILDINGS a parcel touches, and which ones.  ── the path, not the process ──

    IN   the reduction in export_chain2_factors.py (cached per-parcel rows)
    OUT  outputs/melbourne_scan_path_analysis/sankey-facility-path.html

The sibling of `sankey_from_scans.py`, and deliberately a different question. That page draws the
PROCESS — handled here, first sorted there, sorted again, despatched — because that is the shape
the model is built in. This one drops the process entirely and draws the ITINERARY: the 1st
building the parcel was seen in, the 2nd, the 3rd, the 4th, the 5th, and then the depot that
delivered it. A column is a POSITION IN THE JOURNEY, not a role, so a hub can appear in any of
them and the reader can count buildings straight off the picture.

Both pages read the same reduction, so they cannot disagree about volume: 165,567 articles, every
delivery date the extract covers, articles rather than consignments.

THE JOURNEY STARTS WHEN THE PARCEL ENTERS VICTORIA ───────────────────────────────────
Only Victorian buildings are drawn. A parcel lodged in Sydney is on the page from the building it
first appears in HERE — the gateway it lands at — and the Sydney end of its life is not a column.
The state comes off the scan itself (`STE_NAME21`, cached per facility), not off the name, and
every building in the extract is placed, so nothing falls through the test. It costs 0.69 touches
per article on average, and where the freight came from is kept as a FILTER rather than a column,
because that question is answered better by a button than by three columns of interstate hops.

WHAT COUNTS AS A TOUCH ──────────────────────────────────────────────────────────────
`full_chain` is every building a PHYSICAL scan puts the parcel in, in order, consecutive repeats
collapsed — so it lists buildings, not scans. The journey drawn here is that chain up to the
parcel's LAST arrival at one of the delivering depot's own buildings (a depot appears under up to
three names; LODGE_PUD maps them), with the interstate part of it removed. Two consequences, both
deliberate:

  * a depot the parcel visited, LEFT, and came back to is a touch like any other, and shows up in
    the columns under its own name — the same "final stay" rule the overnight stage is measured
    with (Change 31);
  * scans AFTER that final arrival are not drawn. They are 26.8% of articles, and they are the
    last-mile end of the journey rather than a step towards it: a post shop or an LPO where the
    parcel was collected, or an admin scan back at a hub. The footer says how much and the console
    prints the biggest of them, so the cut is visible rather than silent.

FIVE COLUMNS, AND WHAT HAPPENS EITHER SIDE OF THAT ───────────────────────────────────
Five positions cover 98.9% of articles end to end once the interstate leg is out. Shorter journeys are not padded with a fake
building: they flow into a grey "on to the depot" node and ride it to the right, so the width of
that node in column N is exactly the volume that was finished in under N buildings. Longer
journeys keep their first five buildings and are coloured as 6+, which is the one cohort the
diagram cannot draw whole; the ribbon into the depot is where their extra sites are hiding.

Run:  uv run python plotting/sankey_facility_path.py
      uv run python plotting/sankey_facility_path.py --rebuild        # re-read the scan CSV
      uv run python plotting/sankey_facility_path.py --keep 24        # more sites per column
      uv run python plotting/sankey_facility_path.py --group model    # outside buildings fall out
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

from export_chain2_factors import (  # the reduction — one source of truth for what a scan MEANS
    ALIASED_PUD, ALIASED_SORT, CLASSES, EVIDENCE, HERE, LODGE_PUD, OUT, SCAN, SORT_SITE,
    TERM_PDC, VAN_SUFFIX, Z_DEPOT, Z_INTER, Z_NOSCAN, Z_OUTSIDE, alias, canon_state,
    collapse_chain, depot_buildings, depot_label, facility_state, learn_aliases, load_paths,
    model_names, model_node, path_chain,
)

TARGET = OUT / "sankey-facility-path.html"
DEPTH = 4   # touch columns drawn before the delivering depot
KEEP = 20        # facilities kept per column; the page folds further, never less
BANDS = ["INT", "METRO", "KEPT_METRO", "REGION"]     # source_band, as the page's filter


# ── naming ───────────────────────────────────────────────────────────────────────────
# The scans name a building in full caps and at full length. A Sankey has one line of label per
# node, so the long form goes to the tooltip and a short form goes on the page. Abbreviations are
# applied as PHRASES first, so "MELBOURNE NTH PARCEL FACILITY" shortens the same way every time.
# THE LABEL IS THE SCAN NAME (user, 2026-08-24). An earlier draft abbreviated ("MELBOURNE PARCEL
# FACILITY" -> "Melbourne PF") and renamed a delivery centre to PDC on the node itself, which put a
# second vocabulary on the page: the reader could not take a node label back to the extract without
# a translation table, and a renamed node claims something the scan file does not say. So the node
# says exactly what the scan says, and everything the mapping KNOWS — that a van site is the red van
# pickup fleet, that a delivery centre does a PDC's job — is told in the tooltip, where it is an
# explanation rather than a substitution.
# ══ ONE BUILDING, SEVERAL NAMES ══════════════════════════════════════════════════════
# The fold that decides which scan names are one building — a van arm is its depot, a PARCEL
# DELIVERY is its PDC, two names with one SORT_SITE code are one site — MOVED INTO
# `export_chain2_factors.py` (2026-08-28), because the model reads the itinerary now and the two
# must not disagree about which buildings exist. The reasoning, the ops evidence and the one
# told-not-derived merge are all in that file's step 0d; this page imports the result.

# ══ THE SCOPE WORD IS A DIAL ═════════════════════════════════════════════════════════
# Two of the three sources are TERMINATING volume: freight arrives in the catchment and is
# delivered here, so the journey is confined to Victoria and a dozen sentences on this page say
# so. `sankey_facility_path_originating.py` follows freight the other way — out of the state — and
# on that page every one of those sentences is false. So the word is a dial with the Victorian
# answer as its default, and the two terminating pages are byte-identical with it in place.
#   adj       qualifies "buildings" wherever the page counts them; "" on an unbounded page
#   place     the region the journey is confined to
#   bounded   is there a border at all? An unbounded page DROPS the sentences about crossing one
#             rather than rewording them — there is no interstate leg to declare as cut.
SCOPE = {
    "adj": "Victorian",
    "place": "Victoria",
    "bounded": True,
    "eyebrow": "Victoria only",
    "question": " in Victoria",
    "clock": "The clock starts <strong>when the parcel enters the state</strong>,\n  so a Sydney "
             "parcel is on the page from the gateway it lands at.",
    "zeroklab": "none \u2014 first seen in Victoria at the depot itself",
    "tile": "in Victoria, before the delivering ",
    "title": "Parcel Paths by Melbourne handling facilities",
    # the grey ride-along node and the "finished early" tile both name what comes NEXT, which on a
    # terminating page is always a depot and on an outbound one may be a whole other state
    "onward": "on to the depot",
    "onwardsub": "the journey ended before this step \u2014 the next building is the delivering "
                 "depot",
    "fewtile": "entry to depot",
    # what a node in the last column IS. On a terminating page every one of them ran a delivery
    # round; on an outbound page the biggest of them is a whole other state. {d} is `destword`.
    "destsub": "the {d} that ran the delivery round",
}


def _adj(noun):
    """"Victorian buildings" on a bounded page, plain "buildings" where there is no border."""
    return f"{SCOPE['adj']} {noun}" if SCOPE["adj"] else noun


STATE_CODE = {"Victoria": "Vic", "New South Wales": "NSW", "Queensland": "Qld",
              "South Australia": "SA", "Western Australia": "WA", "Tasmania": "Tas",
              "Australian Capital Territory": "ACT", "Northern Territory": "NT"}


def short(name):
    """The label, which is the scan name — see the note above. Kept as a seam, not a transform."""
    return name


def role(name):
    """What kind of building this is — for the tooltip, so a node says more than a number.

    The eight modelled sort sites and the eleven delivering depots are named in the reduction, so
    those two answers are read rather than guessed. Everything else is typed off the name, which
    is weaker and says so: an LPO is a retail counter, a DC is a delivery centre, the rest is
    "another building on the network".

    THIS IS WHERE THE MAPPING LIVES NOW. The node keeps the scan's own name, so what ops know
    about a name — a van site is the red van pickup fleet, a delivery centre is a PDC by another
    name — is said here instead of being written over the label.
    """
    if name.endswith((" VAN OPERATIONS", " VAN SERVICES")):
        return "red van pickup — the depot's street and business collection fleet"
    if name in ALIASED_SORT:
        code = ALIASED_SORT[name]
        return ("hub — a modelled sorting hub" if code in ("MPF", "TPF", "MGF")
                else f"modelled sort site ({code})")
    if name in ALIASED_PUD:
        return f"delivering depot — {depot_label(ALIASED_PUD[name])}"
    for key, what in (("LPO", "retail counter (LPO)"), ("POST SHOP", "retail counter (post shop)"),
                      ("POST OFFICE", "retail counter"), ("PARCEL COLLECTION", "collection point"),
                      ("AIRPORT", "air gateway"), ("QANTAS", "air linehaul"),
                      ("VIRGIN", "air linehaul"), ("PARCEL FACILITY", "parcel facility"),
                      ("GATEWAY", "gateway facility"), (" MC", "mail centre"),
                      ("PDC", "parcel delivery centre"),
                      (" DC", "delivery centre — a PDC by another name")):
        if key in name:
            return what
    return "another building on the network"


# The evidence bars — which events may name a building — are the reduction's; see EVIDENCE
# in export_chain2_factors.py for what each one includes and why.


def touch_chain(bar, rebuild=False):
    """The 20 May extract's chain on one evidence bar, cached beside the reduction.

    The `physical` bar is the reduction's own `full_chain` and is not rebuilt here; every other
    bar is one pass over the scan file. Names are left RAW: `touches` folds them itself, and it
    needs the raw form to measure what the fold was worth and to spot a van arm.
    """
    cache = OUT / f"chain_bar_{bar}.pkl"
    if cache.exists() and not rebuild:
        return pd.read_pickle(cache)
    print(f"  building the {bar} chain (once; cached to {cache.name})")
    d = pd.read_csv(SCAN, usecols=["Consignment_ID", "Event_seq", "Event_type",
                                   "Event_facility_name"],
                    dtype={"Consignment_ID": str}, low_memory=False)
    d = d[d.Event_type.isin(EVIDENCE[bar]) & d.Event_facility_name.notna()]
    ch = collapse_chain(d, "Event_seq")
    ch.to_pickle(cache)
    return ch


# ══ OUR FACILITY GROUP ═══════════════════════════════════════════════════════════════
# The eight sort sites and the eleven delivering depots — the buildings the model has a node for —
# read from the reduction's own dictionaries and put through the name fold, so HOLLOWAY DR is
# BAYSWATER PDC here as it is everywhere else. `alias` must already be learnt.
#
# THREE BASES, AND THE MIDDLE ONE IS THE DEFAULT (user, 2026-08-26).
#   scan   every building a scan names, under its own name — LPOs and customers' sheds included.
#          Honest and unreadable: a column has hundreds of nodes and the tail is most of them.
#   model  a building that is not one of ours FALLS OUT and the parcel keeps going until it
#          reaches one that is — the same rule the delivering site is resolved with, applied to
#          every column. The count then answers "how many of OURS did it pass through", which is
#          the one chain 2 is built on. But it SHORTENS the journey, and a reader looking at the
#          picture cannot tell a direct move from one with a building deleted out of the middle.
#   keep   the default. Every stop stays a stop and the journey is never cut short; only the
#          NAMING is folded, so our facilities keep their names and the rest share one node per
#          column. The count is the true number of Victorian buildings — identical to `scan`, by
#          construction — and the page stays readable. Use `model` when a number has to line up
#          with chain 2, and `scan` when the outside buildings are the subject.
def touches(f, state, group=None):
    """The journey drawn, per parcel — `path_chain` for every row of the frame.

    The rule itself lives in the reduction (`export_chain2_factors.path_chain`), because the model
    reads the same itinerary; this is the loop around it and the seven lists the page needs. The
    page draws the first; the rest are what it cut, and the footer declares them rather than
    letting a shorter journey look like a simpler one.
    """
    pre, before, after, merged, vanfirst, fell, why, revisit = [], [], [], [], [], [], [], []
    for r in f.itertuples():
        vic, b, a, m, v, fe, w, rv = path_chain(r.chain, r.own, state, group)
        pre.append(vic); before.append(b); after.append(a); merged.append(m)
        vanfirst.append(v); fell.append(fe); why.append(w); revisit.append(rv)
    return pre, before, after, merged, vanfirst, fell, why, revisit


# ── the payload ──────────────────────────────────────────────────────────────────────
# The page cross-filters on product class, source band and journey length, and it folds sites per
# column. Precomputing every combination would be 100-odd copies of the same diagram, so what is
# shipped instead are ATOMS: one row per (class, band, length, five folded site ids, depot), with
# an article count. There are ~11k of them and the browser re-aggregates on every control change,
# which is what lets the fold dial fold and the totals stay exact.
NOSITE, OTHER_RETAIL, OTHER_NET = -1, -2, -3
OTHER_DEST = -1                 # the folded tail of the delivering-site column
# ══ THE ZERO BUCKET IS THREE POPULATIONS, NOT ONE (user, 2026-08-26) ══════════════════
# "0 buildings" was a single grey node, and it answered a question nobody asked. A parcel whose
# first scan IS its delivering depot is a parcel the network handled once; a parcel that flew in
# from another state and went straight to the depot is a different animal; and on the model basis
# a third kind appears — a parcel that DID pass through Victorian buildings, none of which we
# carry. They arrive at the same place on the page and are not the same fact, so the first column
# names them apart instead of pooling them. They are ids rather than sites because there is no
# building to point at: that is precisely what each one is saying.
# Z_DEPOT / Z_INTER / Z_OUTSIDE / Z_NOSCAN are the reduction's, so the page and the
# provenance count the same four empty populations.
# ══ A BUILDING WE DO NOT CARRY IS STILL A STOP (user, 2026-08-26) ═════════════════════
# The third basis, and the one that answers the question the other two each answer half of.
# `--group scan` names every building and the picture fills with a thousand of them; `--group
# model` names only ours but DELETES the rest, so a parcel that went out to somewhere we do not
# carry and came back reads as though it went straight there. Neither shows what the reader
# actually asked for: our buildings by name, and the outside hops still on the page, in place,
# counted. So this basis keeps every stop and folds only the NAMING — a building that is not ours
# is drawn as one node per column instead of falling out of the journey.
OUT_OTHER = -8
ZERO_LABEL = {Z_DEPOT: "Started at its depot", Z_INTER: "Straight in from interstate",
              Z_OUTSIDE: "Only buildings we do not model", Z_NOSCAN: "No qualifying scan"}
ZERO_PROSE = {Z_DEPOT: "were first scanned AT the depot that delivered them",
              Z_INTER: "came in from another state and were not seen in Victoria before it",
              Z_OUTSIDE: "did pass through Victorian buildings, none of them ours",
              Z_NOSCAN: "have no scan of this evidence bar naming any building"}
RETAIL = ("retail counter", "collection point")


def is_retail(name):
    """A counter the public hands a parcel over at, rather than a building of the network."""
    return role(name).startswith(RETAIL)


def build(fr, state, keep_n, cfg):
    """The payload. `fr` is the normalised frame — articles, cls, band, chain, dest, own — and
    `cfg` is everything about the SOURCE the page has to be told rather than measure: which
    product classes and source bands its filters offer, how many delivering sites to name, and
    what to call that last column. Both extracts reach here; nothing below knows which is which.
    """
    # In `keep` mode nothing is cut from the journey — the group is a NAMING rule below, not a
    # membership test here — so `touches` is told there is no group and every stop survives.
    grp, outside = cfg.get("group"), cfg.get("outside")
    pre, before, after, merged, vanfirst, fell, why, revisit = touches(
        fr, state, None if outside else grp)
    arts = fr.articles.tolist()
    ks = [len(c) for c in pre]

    vol = [collections.Counter() for _ in range(DEPTH)]
    for ch, a in zip(pre, arts):
        for i, f in enumerate(ch[:DEPTH]):
            vol[i][f] += a
    # only OUR buildings compete for a name in `keep` mode; everything else has one node waiting
    ours = (lambda f: f in grp) if outside else (lambda f: True)
    keep = [[f for f, _ in vol[i].most_common() if ours(f)][:keep_n] for i in range(DEPTH)]
    outn = [sum(1 for f in vol[i] if not ours(f)) for i in range(DEPTH)]
    outall = len({f for i in range(DEPTH) for f in vol[i] if not ours(f)})

    fac, fid = [], {}
    for i in range(DEPTH):
        for f in keep[i]:
            if f not in fid:
                fid[f] = len(fac)
                fac.append({"n": f, "l": short(f), "s": STATE_CODE.get(state.get(f), "?"),
                            "t": int(is_retail(f)), "r": role(f)})
    rank = [[fid[f] for f in keep[i]] for i in range(DEPTH)]
    # how many sites the page never names, per column — the residual nodes carry this, so
    # "Other retail counters" can say how many buildings it is standing for. Everything on the
    # page is Victorian now, so the split that means something is the KIND of building: a counter
    # the public lodges at, or a building of the network.
    tail = [[sum(1 for f in vol[i] if ours(f) and f not in set(keep[i]) and is_retail(f)),
             sum(1 for f in vol[i] if ours(f) and f not in set(keep[i]) and not is_retail(f))]
            for i in range(DEPTH)]

    def fold(f, i):
        if outside and not ours(f):
            return OUT_OTHER          # a stop, drawn, but not one we have a node for
        if f in fid and f in keep[i]:
            return fid[f]
        return OTHER_RETAIL if is_retail(f) else OTHER_NET

    # THE LAST COLUMN FOLDS TOO, where the source needs it to. The old extract delivers from 11
    # depots and names them all; the new one delivers from 1,422 sites, and a column of 1,422 nodes
    # is not a picture. `dest_keep` names the biggest and folds the rest into one residual that
    # says how many sites it stands for — the same bargain the touch columns make.
    dep_vol = fr.groupby("dest").articles.sum().sort_values(ascending=False)
    keep_dest = cfg.get("dest_keep")
    dep = list(dep_vol.index[:keep_dest] if keep_dest else dep_vol.index)
    did = {p: i for i, p in enumerate(dep)}
    dest_tail = len(dep_vol) - len(dep)
    cls_i = {c: i for i, (c, _) in enumerate(cfg["classes"])}
    band_i = {b: i for i, (b, _) in enumerate(cfg["bands"])}
    # AN OPTIONAL THIRD FILTER, and it is appended to the END of each atom rather than slotted in
    # beside cls and band. Every index on the page is positional — `a[2 + DEPTH]`, `a[4 + DEPTH]`
    # — so inserting a field would move the ids, the destination and the volume, in the Python and
    # in the JS, on pages that do not have the field at all. Appending costs one `undefined` read
    # on a page without groups, which the filter never looks at because its state stays "ALL".
    grp_i = {g: i for i, (g, _) in enumerate(cfg.get("groups") or [])}

    atoms, khist, zero = collections.Counter(), collections.Counter(), collections.Counter()
    for r, ch, k, a, w in zip(fr.itertuples(), pre, ks, arts, why):
        ids = [fold(x, i) for i, x in enumerate(ch[:DEPTH])]
        ids += [NOSITE] * (DEPTH - len(ids))
        if w:
            ids[0] = w                   # nothing to draw — say WHY in the first column
            zero[w] += a
        c, b = cls_i[r.cls], band_i[r.band]
        g = grp_i[r.grp] if grp_i else None
        atoms[(c, b, min(k, DEPTH + 1), tuple(ids), did.get(r.dest, OTHER_DEST), g)] += a
        khist[(c, b, k, g)] += a             # the TRUE length, uncapped, so the mean is exact

    rows = [[c, b, k, *ids, d, int(v)] + ([g] if g is not None else [])
            for (c, b, k, ids, d, g), v in atoms.items()]
    day = int(fr.articles.sum())
    # the volume is at a FIXED offset, not at the end — an optional group index may follow it
    assert sum(r[4 + DEPTH] for r in rows) == day, "atoms do not carry the day"

    over = sum(a for k, a in zip(ks, arts) if k > DEPTH)
    trail = sum(a for n, a in zip(after, arts) if n > 0)
    came = sum(a for n, a in zip(before, arts) if n > 0)      # arrived through another state
    dbl = sum(a for n, a in zip(merged, arts) if n > 0)       # scanned under two names of one site
    van = sum(a for ch, a in zip(vanfirst, arts) if ch)
    revisited = sum(a for n, a in zip(revisit, arts) if n > 0)
    fellout = sum(a for n, a in zip(fell, arts) if n > 0)
    data = {
        "fac": fac, "dep": list(dep), "rank": rank, "tail": tail, "desttail": dest_tail,
        "atoms": rows,
        "khist": [[c, b, k, int(v)] + ([g] if g is not None else [])
                  for (c, b, k, g), v in khist.items()],
        "groups": cfg.get("groups") or [],
        "cls": [list(c) for c in cfg["classes"]], "bands": [list(b) for b in cfg["bands"]],
        "destcol": cfg["destcol"], "destword": cfg["destword"],
        # in `keep` mode every stop is on the page, so a column is a BUILDING again — it is the
        # naming that is folded, not the journey
        "colnoun": "modelled site" if grp and not outside else "building",
        "outn": outn,
        "basis": "keep" if outside else "model" if grp else "scan", "outall": outall,
        # day-level, because the browser cannot recount buildings it was never sent: on the keep
        # basis these are the ones folded into one node, on the model basis the ones DELETED
        "outart": int(sum(a for ch, a in zip(pre, arts) if any(not ours(x) for x in ch[:DEPTH]))),
        "outstep": int(sum(a * sum(1 for x in ch[:DEPTH] if not ours(x))
                           for ch, a in zip(pre, arts))),
        "fellart": int(sum(a for x, a in zip(fell, arts) if x > 0)),
        "fellstep": int(sum(x * a for x, a in zip(fell, arts))),
        "zero": {str(z): int(v) for z, v in sorted(zero.items(), reverse=True)},
        "zerolab": {str(z): l for z, l in ZERO_LABEL.items()},
        "bar": cfg.get("bar", "physical"),
        # how many to name before the page folds further. On the model basis there are nineteen
        # buildings in the whole world and folding five of them into "other" helps nobody; on the
        # scan basis a column has hundreds and twelve is where it stops being a wall of hairlines.
        "topdefault": keep_n if cfg.get("group") else min(12, keep_n),
        "scope": {"adjb": _adj("buildings"), "zeroklab": SCOPE["zeroklab"],
                  "tile": SCOPE["tile"], "onward": SCOPE["onward"],
                  "onwardsub": SCOPE["onwardsub"], "fewtile": SCOPE["fewtile"],
                  "destsub": SCOPE["destsub"].format(d=cfg["destword"])},
        "depth": DEPTH, "keep": keep_n, "day": day,
    }
    summary = {
        "day": day, "over": int(over), "trail": int(trail), "came": int(came),
        "mean": sum(k * a for k, a in zip(ks, arts)) / day,
        "intmean": sum(n * a for n, a in zip(before, arts)) / day,
        "dbl": int(dbl), "van": int(van), "fellout": int(fellout),
        "fellmean": sum(n * a for n, a in zip(fell, arts)) / day,
        "mergemean": sum(n * a for n, a in zip(merged, arts)) / day,
        "dist": collections.Counter({k: 0 for k in range(DEPTH + 2)}),
        "sites": len(fac), "folded": [sum(t) for t in tail], "after": after, "pre": pre,
        "dests": len(dep_vol), "dest_named": len(dep), "zero": zero, "outall": outall,
        "outart": int(sum(a for ch, a in zip(pre, arts)
                          if any(not ours(x) for x in ch[:DEPTH]))),
        "revisit": int(revisited),
        # never seen at its own depot on this bar — a cross-cut of the zero bucket, not a
        # fifth category, so it is reported as a sub-count rather than drawn as a node
        "nodepot": int(sum(a for r, a in zip(fr.itertuples(), arts)
                           if not r.own & {alias(x) for x in r.chain})),
    }
    for k, a in zip(ks, arts):
        summary["dist"][min(k, DEPTH + 1)] += a
    return data, summary


def prose(summary, data, extra):
    """Every number the page says in words, computed here so nothing is typed twice.

    `extra` is what only the SOURCE can answer — which extract, which dates, which state test —
    and it is passed in rather than branched on, so this function never learns which page it is
    writing.
    """
    n = lambda v: f"{int(v):,}"
    pct = lambda v, d=None: f"{100 * v / (d or summary['day']):.1f}%"
    d = summary["dist"]
    within = sum(v for k, v in d.items() if k <= DEPTH)
    word = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
    ordinal = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"]
    return {
        "{{GROUPFILTER}}": "",       # …and a third filter control, if it declares `groups`
        "{{SECTIONS}}": "",          # a source may add a section of its own here
        "{{LANEDIAL}}": LANE_DIAL,   # …and decline this one by passing ""
        "{{TRACED}}": n(summary["day"]),
        "{{SCOPEEYEBROW}}": SCOPE["eyebrow"],
        "{{SCOPETITLE}}": SCOPE["title"],
        "{{SCOPEQ}}": SCOPE["question"],
        "{{SCOPEADJ}}": f"{SCOPE['adj']} " if SCOPE["adj"] else "",
        "{{SCOPECLOCK}}": SCOPE["clock"],
        "{{DEPTH}}": str(DEPTH),
        "{{DEPTHWORD}}": word[DEPTH] if DEPTH < len(word) else str(DEPTH),
        "{{OVERBAND}}": f"{DEPTH + 1}+",
        # "the 1st building ... , the 2nd, the 3rd" — as long as the build asked for
        "{{COLLIST}}": "".join(f", the {ordinal[i]}" for i in range(1, DEPTH)),
        "{{MEAN}}": f"{summary['mean']:.2f}",
        "{{WITHIN}}": pct(within), "{{WITHINEA}}": n(within),
        "{{OVER}}": n(summary["over"]), "{{OVERPCT}}": pct(summary["over"]),
        "{{TRAIL}}": n(summary["trail"]), "{{TRAILPCT}}": pct(summary["trail"]),
        "{{CAME}}": n(summary["came"]), "{{CAMEPCT}}": pct(summary["came"]),
        "{{INTMEAN}}": f"{summary['intmean']:.2f}",
        "{{DBL}}": n(summary["dbl"]), "{{DBLPCT}}": pct(summary["dbl"]),
        "{{MERGEMEAN}}": f"{summary['mergemean']:.2f}",
        "{{VAN}}": n(summary["van"]), "{{VANPCT}}": pct(summary["van"]),
        "{{FELLOUT}}": n(summary["fellout"]), "{{FELLOUTPCT}}": pct(summary["fellout"]),
        "{{FELLMEAN}}": f"{summary['fellmean']:.2f}",
        "{{COLNOUN}}": data["colnoun"],
        # the zero bucket, told apart in words. Only the categories that actually occur are
        # listed, so the sentence never claims a population the day does not have.
        "{{ZEROEA}}": n(d[0]), "{{ZEROPCT}}": pct(d[0]),
        "{{ZEROSPLIT}}": "; ".join(
            f"<b>{n(v)}</b> ({pct(v)}) {ZERO_PROSE[z]}"
            for z, v in sorted(summary["zero"].items(), reverse=True)),
        "{{NODEPOT}}": n(summary["nodepot"]), "{{NODEPOTPCT}}": pct(summary["nodepot"]),
        "{{REVISIT}}": n(summary["revisit"]), "{{REVISITPCT}}": pct(summary["revisit"]),
        "{{TOUCHBAR}}": ", ".join(e.replace("ZPT_", "") for e in EVIDENCE[data["bar"]]),
        "{{BARNAME}}": data["bar"],
        "{{BASISNOTE}}": {
            "model":
                "<p><b>Only buildings the model carries.</b> A column is one of OUR facilities "
                "&mdash; the eight sort sites and the eleven delivering depots &mdash; and a "
                "building we do not carry is not a stop but a gap: the parcel keeps going until "
                "it reaches one that is ours, exactly as the delivering site is resolved. "
                "{{FELLOUT}} articles ({{FELLOUTPCT}}) passed through at least one such building "
                "and {{FELLMEAN}} of them fall out of the average journey. The count therefore "
                "answers &lsquo;how many of OURS did it touch&rsquo;, which is the question "
                "chain 2 is built on. <code>--group keep</code> puts those buildings back on the "
                "page as stops without naming them.</p>",
            "keep":
                "<p><b>Every stop is on the page; only the naming is folded.</b> A building the "
                "model does not carry is still somewhere the parcel WAS, so it is drawn in place "
                "as <b>Not one of ours</b> rather than deleted &mdash; the journey is never cut "
                "short to reach the next facility we happen to have a node for, and the count is "
                "the true number of " + _adj("buildings") + ". What the fold buys is a "
                "readable page: "
                "our own facilities keep their names, and the {{TAILNET}}-odd others share one "
                "node per column instead of {{OUTN}} hairlines. <code>--group scan</code> names "
                "them individually; <code>--group model</code> drops them from the journey.</p>",
            "scan":
                "<p><b>Every building a scan names</b> is a column here, ours or not &mdash; an "
                "LPO, a bulk customer's shed, another carrier's depot. <code>--group keep</code> "
                "keeps every stop but gives the ones we do not carry a single node per column; "
                "<code>--group model</code> drops them from the journey altogether.</p>",
        }[data.get("basis", "scan")],
        "{{FOLDPAIRS}}": ", ".join(
            f"<code>{' = '.join(v)}</code>" for _, v in sorted(summary["folds"].items())),
        "{{SITES}}": n(summary["sites"]), "{{KEEP}}": str(data["keep"]),
        "{{OUTN}}": n(data.get("outall", 0)),
        "{{TAILRETAIL}}": n(sum(t[0] for t in data["tail"])),
        "{{TAILNET}}": n(sum(t[1] for t in data["tail"])),
        "{{DESTS}}": n(summary["dests"]),
        "{{DESTNAMED}}": n(summary["dest_named"]),
        "{{DESTWORD}}": data["destword"],
        "{{DESTFOLD}}": ("" if not data["desttail"] else
                         f", of which the {n(summary['dest_named'])} biggest are named and "
                         f"{n(data['desttail'])} fold into one residual"),
        "{{TOPTRAIL}}": ", ".join(f"<strong>{short(f)}</strong> {n(v)}"
                                  for f, v in summary["top_after"]),
        "{{THREE}}": pct(sum(v for k, v in d.items() if k <= 3)),
        **extra,
    }


def report(summary, data, folds, dest_word):
    """The console block. Both pages print the same shape, off the same numbers."""
    day = summary["day"]
    print(f"\n  === {_adj('buildings').upper()} TOUCHED BEFORE THE {dest_word.upper()} ===")
    print(f"    {'buildings':<12}{'articles':>10}{'share':>8}{'cumulative':>12}")
    cum = 0
    for k in sorted(summary["dist"]):
        v = summary["dist"][k]
        cum += v
        lab = f"{k}+" if k == DEPTH + 1 else str(k)
        print(f"    {lab:<12}{v:>10,}{100 * v / day:>7.1f}%{100 * cum / day:>11.1f}%")
        if k == 0:      # the empty journeys are not one population — say which
            for z, zv in sorted(summary["zero"].items(), reverse=True):
                print(f"{'':<6}{zv:>20,}{100 * zv / day:>7.1f}%   {ZERO_LABEL[z].lower()}")
    print(f"    {summary['nodepot']:,} of all {day:,} articles "
          f"({100 * summary['nodepot'] / day:.1f}%) are never scanned at their own depot on this "
          f"bar — the depot column comes from the reduction, not from a scan, so their whole "
          f"chain is drawn and nothing is truncated")
    if SCOPE["bounded"]:      # nothing was cut at a border, so there is no cut to declare
        print(f"    {summary['came']:,} articles ({100 * summary['came'] / day:.1f}%) reached "
              f"{SCOPE['place']} through another state; the {summary['intmean']:.2f} interstate "
              f"buildings an average article touches are NOT drawn — the journey starts here")
    if summary.get("revisit"):
        print(f"    {summary['revisit']:,} articles ({100 * summary['revisit'] / day:.1f}%) came "
              f"back to a building they had already left; the cuts above left that as a lane from "
              f"a building to ITSELF, and it is collapsed — a lane is two different buildings")
    if summary.get("fellout"):
        print(f"    {summary['fellout']:,} articles ({100 * summary['fellout'] / day:.1f}%) "
              f"passed through a building the model does NOT carry; {summary['fellmean']:.2f} such "
              f"buildings per article FALL OUT and the journey continues to the next one we do")
    print(f"    mean {summary['mean']:.2f} {_adj('buildings')}; {DEPTH} columns hold "
          f"{100 * (day - summary['over']) / day:.1f}% of articles end to end")
    print(f"    {summary['trail']:,} articles ({100 * summary['trail'] / day:.1f}%) "
          f"were also seen somewhere AFTER their {dest_word} — not drawn: "
          + ", ".join(f"{f} {v:,}" for f, v in summary["top_after"]))
    if folds:
        print(f"    name folds: {len(folds)} buildings carried "
              f"{sum(len(v) for v in folds.values())} names between them, and keep the busiest — "
              + "; ".join(f"{c} <- {' + '.join(x for x in v if x != c)}"
                          for c, v in sorted(folds.items())))
    print(f"                every node is labelled exactly as the scans write it; nothing renamed")
    print(f"                {summary['dbl']:,} articles "
          f"({100 * summary['dbl'] / day:.1f}%) were scanned under two names of ONE building "
          f"— {summary['mergemean']:.2f} phantom touches per article removed; "
          f"{summary['van']:,} "
          + (f"enter {SCOPE['place']}" if SCOPE["bounded"] else "start")
          + " at a red van pickup site")
    print(f"    {len(data['fac'])} sites named across {DEPTH} columns (top {data['keep']} each); "
          f"the tail folds to {sum(t[0] for t in data['tail'])} retail counters and "
          f"{sum(t[1] for t in data['tail'])} other {_adj('buildings')}")
    if data.get("basis") == "keep":
        print(f"    {summary['outart']:,} articles ({100 * summary['outart'] / day:.1f}%) stop at "
              f"a building the model does not carry; those {summary['outall']:,} buildings are "
              f"DRAWN, one node per column — the journey is not cut short to reach ours")
    print(f"    {summary['dest_named']:,} of {summary['dests']:,} {dest_word}s named in the last "
          f"column" + (f", {data['desttail']:,} folded" if data["desttail"] else ""))
    print(f"    {len(data['atoms']):,} atoms carry {data['day']:,} articles to the browser")


def top_after(f, summary):
    """The busiest buildings the page cuts — what a parcel was seen in AFTER its delivering site."""
    vol = collections.Counter()
    for r, nafter in zip(f.itertuples(), summary["after"]):
        if not nafter:
            continue
        chain = []
        for name in r.chain:
            name = alias(name)
            if not chain or chain[-1] != name:
                chain.append(name)
        idx = [i for i, x in enumerate(chain) if x in r.own]
        for x in chain[idx[-1] + 1:]:
            vol[x] += r.articles
    return vol.most_common(3)


def write_page(data, summary, extra, target):
    # the whole page, title included — the <title> carries the extract's dates, so the two
    # siblings do not open as two identical tabs
    body = CSS + BODY
    filled = prose(summary, data, extra)
    for _ in range(3):          # {{LANEDIAL}}/{{BASISNOTE}} are prose that contain tokens
        for token, value in filled.items():
            body = body.replace(token, value)
    assert "{{" not in body, f"unfilled prose token: {body[body.index('{{'):][:40]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body
                      + "<script>const DATA=" + json.dumps(data, separators=(",", ":"))
                      + ";</script>\n<script>" + JS + "</script>\n")
    print(f"\n  wrote {target.relative_to(HERE)}  ({target.stat().st_size / 1024:.0f} KB)")


def learn(chains, state_map):
    """The name fold, learnt off the chains that will be drawn. Returns (folds, canonical state)."""
    vol = collections.Counter()
    for chain, art in chains:
        for name in set(chain):
            vol[name] += art
    for name in state_map:
        vol.setdefault(name, 0)
    folds = learn_aliases(vol)
    return folds, canon_state(state_map)


# ── THE OLD EXTRACT, AS A SOURCE ─────────────────────────────────────────────────────
# Everything above is source-agnostic; this is where the 20 May extract says what it is. The
# sibling `sankey_facility_path_new.py` says the same six things about `inputs/new_scan_events/`
# and draws the identical page.
CONFIG = {
    "classes": [("PP", "Parcel Post"), ("EP", "Express Post")],
    "bands": [("INT", "Interstate"), ("METRO", "Metro Vic"),
              ("KEPT_METRO", "Kept at depot"), ("REGION", "Regional Vic")],
    "dest_keep": None,                  # eleven depots — name them all
    "destcol": "Delivered by", "destword": "depot",
}
TOKENS = {
    "{{DATELINE}}": "20 May 2026",
    "{{PROVENANCE}}": "Built from <code>inputs/melbourne/all_scan_for_melbourne_pdc_20052026.csv"
                      "</code> by <code>sankey_facility_path.py</code>, over the same reduction as "
                      "<code>sankey_from_scans.py</code> &mdash; so the two pages draw one day two "
                      "ways and cannot disagree about volume",
    "{{VICRULE}}": "read off <code>STE_NAME21</code> on the event, not off the building's name, "
                   "and every facility in the extract is placed, so nothing falls through the test",
}


def frame(q, chains):
    """The old extract, normalised to the six columns `build` reads.

    Called AFTER the name fold is learnt, never before: `own` is the set of names that end a
    journey, and it has to be in the same vocabulary as the chain it will be compared against.
    """
    own = depot_buildings()
    return pd.DataFrame({
        "articles": q.articles.values,
        "cls": q.cls.values,
        "band": q.source_band.values,
        "chain": chains,
        "dest": [depot_label(p) for p in q.pdc],
        "own": [own.get(p, frozenset()) for p in q.pdc],
    })


def main():
    global DEPTH
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true",
                    help="ignore the cached reduction and re-read the scan CSV")
    ap.add_argument("--keep", type=int, default=KEEP, metavar="N",
                    help=f"facilities named per column before the fold (default {KEEP}); "
                         "the page can fold further, never wider")
    ap.add_argument("--depth", type=int, default=DEPTH, metavar="N",
                    help=f"touch columns before the delivering site (default {DEPTH}); the whole "
                         "page follows it — headings, filter, tiles and copy")
    ap.add_argument("--touch", choices=tuple(EVIDENCE), default="entry",
                    help="which events may name a building (default entry: LODGE / MACHINE_SORT / "
                         "TRANSFER / LOAD_ITEM / ACCEPT_FACILITY, in time order). physical is the "
                         "widest; dock is what sankey_from_scans.py counts a handled touch on")
    ap.add_argument("--group", choices=("model", "keep", "scan"), default="keep",
                    help="model (default): only the eight sort sites and eleven depots — anything "
                         "else falls out and the journey continues to the next building we carry. "
                         "keep: the same buildings are NAMED, but one we do not carry is still a "
                         "stop, drawn in place as 'Not one of ours' rather than deleted. "
                         "scan: every building a scan names, under its own name")
    args = ap.parse_args()
    DEPTH = args.depth

    paths = load_paths(args.rebuild)
    q = paths[paths.origin != "UNKNOWN"].copy()
    # the fold has to be learnt before the bar chain is read, because that chain is folded on the
    # way in — so the physical chain, which needs no reading, teaches it
    raw = [list(c) for c in q.full_chain]
    folds, state = learn(zip(raw, q.articles), facility_state(rebuild=args.rebuild))
    if args.touch == "physical":
        chains = raw
    else:
        ch = touch_chain(args.touch, args.rebuild).reindex(q.Consignment_ID)
        chains = [c if isinstance(c, list) else [] for c in ch]
    f = frame(q, chains)
    cfg = dict(CONFIG, group=None if args.group == "scan" else model_names(),
               outside=args.group == "keep", bar=args.touch)
    data, summary = build(f, state, args.keep, cfg)
    summary["folds"] = folds
    summary["top_after"] = top_after(f, summary)
    report(summary, data, folds, CONFIG["destword"])
    print(f"    evidence bar: {args.touch.upper()} — "
          + ", ".join(e.replace("ZPT_", "") for e in EVIDENCE[args.touch]))
    write_page(data, summary, TOKENS, TARGET)


CSS = """<title>{{SCOPETITLE}} · {{DATELINE}}</title>
<style>
:root{
  color-scheme:light;
  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --line:#dde2e9; --line-soft:#e8ecf1;
  --ink:#0e1116; --ink-2:#4d5766; --ink-3:#7b8593;
  --accent:#2a78d6; --shadow:0 1px 2px rgba(14,17,22,.06),0 8px 24px -12px rgba(14,17,22,.14);
  --k0:#b0b9c4; --k1:#a8cdec; --k2:#5b9fdf; --k3:#2a78d6; --k4:#f0a95d; --k5:#eb6834; --k6:#a83a1c;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --bg:#14171c; --panel:#191d24; --panel-2:#202631; --line:#2b323d; --line-soft:#232935;
    --ink:#eef1f5; --ink-2:#9aa5b4; --ink-3:#6f7988;
    --accent:#3987e5; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
    --k0:#5c6675; --k1:#7fb0da; --k2:#4a90d9; --k3:#3987e5; --k4:#dc9a52; --k5:#d95926; --k6:#9c391d;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#14171c; --panel:#191d24; --panel-2:#202631; --line:#2b323d; --line-soft:#232935;
  --ink:#eef1f5; --ink-2:#9aa5b4; --ink-3:#6f7988;
  --accent:#3987e5; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -12px rgba(0,0,0,.6);
  --k0:#5c6675; --k1:#7fb0da; --k2:#4a90d9; --k3:#3987e5; --k4:#dc9a52; --k5:#d95926; --k6:#9c391d;
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#f6f7f9; --panel:#ffffff; --panel-2:#eef1f5; --line:#dde2e9; --line-soft:#e8ecf1;
  --ink:#0e1116; --ink-2:#4d5766; --ink-3:#7b8593;
  --accent:#2a78d6; --shadow:0 1px 2px rgba(14,17,22,.06),0 8px 24px -12px rgba(14,17,22,.14);
  --k0:#b0b9c4; --k1:#a8cdec; --k2:#5b9fdf; --k3:#2a78d6; --k4:#f0a95d; --k5:#eb6834; --k6:#a83a1c;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Segoe UI",Roboto,-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
  font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.wrap{max-width:1780px;margin:0 auto;padding:40px 24px 72px}
header{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:28px}
.eyebrow{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 12px}
h1{font-size:clamp(26px,3.6vw,38px);line-height:1.15;margin:0 0 14px;letter-spacing:-.02em;
  text-wrap:balance;font-weight:650}
.lede{margin:0;max-width:70ch;color:var(--ink-2);font-size:16.5px}
.lede strong{color:var(--ink);font-weight:620}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:26px 0 0}
.stat{background:var(--panel);padding:14px 16px}
.stat .k{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);margin-bottom:6px}
.stat .v{font-size:25px;font-weight:640;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.1}
.stat .v em{font-style:normal;font-size:15px;color:var(--ink-2);font-weight:500}
.stat .s{font-size:12.5px;color:var(--ink-3);margin-top:3px}
.controls{display:flex;flex-wrap:wrap;gap:10px 22px;align-items:center;margin:30px 0 6px}
.grp{display:flex;align-items:center;gap:9px}
.grp>span{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3)}
.seg{display:inline-flex;background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:2px;gap:2px}
.seg button{font:inherit;font-size:13px;padding:5px 13px;border:0;border-radius:6px;background:transparent;
  color:var(--ink-2);cursor:pointer;transition:background .13s,color .13s}
.seg button:hover{color:var(--ink)}
.seg button[aria-pressed="true"]{background:var(--panel);color:var(--ink);font-weight:600;
  box-shadow:0 1px 2px rgba(0,0,0,.09)}
.seg button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.legend{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:16px 0 0;font-size:13px;color:var(--ink-2)}
button.lg{display:inline-flex;align-items:center;gap:7px;cursor:pointer;font:inherit;font-size:12.5px;
  color:var(--ink-2);background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:5px 12px 5px 8px}
button.lg:hover{border-color:var(--ink-3)}
button.lg[aria-pressed="true"]{border-color:var(--accent);color:var(--ink);box-shadow:inset 0 0 0 1px var(--accent)}
button.lg i{display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:-1px}
button.lg .lgv{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink)}
button.lg .lgp{color:var(--ink-3)}
.lgnote{font-size:12px;color:var(--ink-3);align-self:center;max-width:62ch}
.figure{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);
  margin:18px 0 0;overflow:hidden}
.figscroll{overflow-x:auto;padding:10px 6px 4px}
svg{display:block}
.colhead{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;fill:var(--ink-3)}
.coltot{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;
  font-variant-numeric:tabular-nums;fill:var(--ink);font-weight:600}
.nlabel,.nval{paint-order:stroke fill;stroke:var(--panel);stroke-width:3.5px;stroke-linejoin:round}
.nlabel{font-size:12px;fill:var(--ink);letter-spacing:.005em}
.nval{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:11px;fill:var(--ink-3);
  font-variant-numeric:tabular-nums}
.node{cursor:pointer}
.node rect{stroke:var(--panel);stroke-width:1}
.node.sel rect{fill:var(--accent);stroke-width:2}
.node.pale{opacity:.28}
.link{fill:none;transition:stroke-opacity .13s;pointer-events:none}
.hitline{fill:none;stroke:transparent;cursor:pointer}
#pop[hidden]{display:none}
#pop{position:fixed;inset:0;z-index:20;display:flex;align-items:center;justify-content:center;
  padding:24px;background:rgba(14,17,22,.5);backdrop-filter:blur(2px)}
.popcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);
  width:min(1020px,100%);max-height:86vh;overflow:auto;position:relative}
.pophead{position:sticky;top:0;z-index:1;background:var(--panel);border-bottom:1px solid var(--line);
  padding:16px 20px;display:flex;gap:18px;justify-content:space-between;align-items:flex-start}
.pophead h3{margin:0 0 4px;font-size:19px;font-weight:650;letter-spacing:-.01em;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.pophead .psub{color:var(--ink-2);font-size:13px;max-width:70ch}
.pophead .pnum{color:var(--ink-3);font-size:12.5px;margin-top:5px;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.popclose{border:1px solid var(--line);background:var(--panel-2);color:var(--ink-2);border-radius:8px;
  font:inherit;font-size:13px;padding:5px 11px;cursor:pointer;flex:none}
.popclose:hover{color:var(--ink);border-color:var(--ink-3)}
.popbody{padding:14px 20px 20px;display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:900px){.popbody{grid-template-columns:1fr}}
.popbody h4{margin:0 0 8px;font-size:12px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink-3);font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-weight:500}
.popbody td,.popbody th{padding:6px 10px}
.popbody td:first-child{white-space:normal;max-width:290px;
  font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px}
.popnote{padding:0 20px 18px;color:var(--ink-3);font-size:12.5px;max-width:88ch}
tr.go{cursor:pointer}
tr.go:hover td:first-child{color:var(--accent)}
.stack{display:flex;height:9px;width:104px;border-radius:5px;overflow:hidden;background:var(--panel-2)}
.stack i{display:block;height:100%}
.dim .link{stroke-opacity:.06!important}
.dim .link.on{stroke-opacity:.68!important}
.dim .node{opacity:.34}
.dim .node.on{opacity:1}
.figfoot{border-top:1px solid var(--line-soft);padding:11px 18px;font-size:12.5px;color:var(--ink-3);
  display:flex;flex-wrap:wrap;gap:6px 18px;justify-content:space-between}
#tip{position:fixed;pointer-events:none;z-index:9;opacity:0;transition:opacity .1s;background:var(--panel);
  border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow);padding:9px 12px;font-size:13px;
  max-width:300px;line-height:1.45}
#tip .tt{font-weight:640;margin-bottom:3px;display:block}
#tip .tv{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums;color:var(--ink-2)}
#tip .tn{color:var(--ink-3);font-size:12px;margin-top:4px;display:block}
h2{font-size:19px;margin:44px 0 10px;letter-spacing:-.015em;font-weight:640;max-width:70ch}
h2+p{margin:0 0 16px;color:var(--ink-2);max-width:76ch;font-size:14.5px}
table{border-collapse:collapse;width:100%;font-size:13px}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
th,td{padding:8px 13px;text-align:left;border-bottom:1px solid var(--line-soft);white-space:nowrap}
th{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink-3);font-weight:500;background:var(--panel-2);position:sticky;top:0}
td.n{text-align:right;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
tbody tr:hover{background:var(--panel-2)}
tbody tr:last-child td{border-bottom:0}
tbody tr.hit{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.minibar{width:150px;height:9px;border-radius:5px;background:var(--panel-2);overflow:hidden}
.minibar span{display:block;height:100%;border-radius:5px}
.tag{display:inline-block;font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:10px;
  letter-spacing:.06em;padding:1px 6px;border-radius:4px;background:var(--panel-2);color:var(--ink-2);
  border:1px solid var(--line)}
.chain{white-space:normal;line-height:1.5}
.chain .arr{color:var(--ink-3);padding:0 4px}
.sub{color:var(--ink-3);font-size:12px;line-height:1.4}
td.sub{max-width:52ch}
.thr{display:flex;align-items:center;gap:10px;background:var(--panel-2);border:1px solid var(--line);
  border-radius:8px;padding:3px 10px}
.thr input[type=range]{width:170px;accent-color:var(--accent);cursor:pointer}
.thr input[type=number]{width:72px;font:inherit;font-size:13px;font-variant-numeric:tabular-nums;
  background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
.thr .unit{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--ink-3)}
footer{margin-top:52px;padding-top:20px;border-top:1px solid var(--line);color:var(--ink-3);font-size:12.5px}
footer code{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--ink-2)}
footer b{color:var(--ink-2)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
"""

# ── THE LANE-DIAL RECEIPT, AS A SECTION THE PAGE MAY DECLINE ─────────────────────────
# It is the fold's receipt and every page that folds lanes owes the reader one, so the DEFAULT is
# that it is printed — the two terminating pages are untouched by this being a token. A page whose
# question is not "what did the picture cost" can put something else in its place: `{{SECTIONS}}`
# is where an extra section goes and `{{LANEDIAL}}` set to "" is how this one is declined. The JS
# that fills the table checks the table exists before writing to it.
LANE_DIAL = """<h2>What the lane dial costs</h2>
<p>The diagram folds a lane too thin to see onto a thicker one out of the same node. That keeps
the picture readable and it MOVES VOLUME the measurement never moved, so this is the receipt: every
threshold the dial can be set to, how many lanes survive it, how many article-steps it re-routed
to get there, and what the average parcel then appears to touch. That last column is the one to
watch: the fold is supposed to move ink, not journeys, so a mean that slides away from the top row
is the fold rewriting the day rather than tidying it. It counts the <b>{{OVERBAND}}</b> band at
{{DEPTH}}, so the rows are exactly comparable to each other and read a little under the measured
{{MEAN}} above. Click a row to set the dial to it.</p>
<div class="tablewrap"><table id="thrtable"><thead><tr>
  <th style="text-align:right">Fold under</th><th style="text-align:right">Lanes drawn</th>
  <th style="text-align:right">Lanes folded</th><th style="text-align:right">Volume re-routed</th>
  <th style="text-align:right">Avg {{COLNOUN}}s touched</th>
  <th></th>
</tr></thead><tbody></tbody></table></div>
"""


BODY = """<div class="wrap">
<header>
  <p class="eyebrow">Scan-event itineraries &middot; {{DATELINE}} &middot; {{TRACED}} articles
  &middot; {{SCOPEEYEBROW}}</p>
  <h1>{{SCOPETITLE}}</h1>
  <p class="lede"><strong>How many buildings does a parcel go through{{SCOPEQ}}?</strong>
  Every column is a <strong>step in the journey</strong>, not a job: the 1st
  {{SCOPEADJ}}building the parcel was seen in{{COLLIST}} &mdash; and then the {{DESTWORD}} that
  delivered it. {{SCOPECLOCK}} Colour is the <strong>number of
  buildings</strong> it touched here, so the picture answers the count and the route at the same
  time. The average is <strong>{{MEAN}}</strong>, and {{THREE}} of articles are finished in three
  or fewer.</p>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <div class="grp"><span>Product</span>
    <div class="seg" role="group" aria-label="Product class" id="clsseg"></div>
  </div>
  <div class="grp"><span>Came from</span>
    <div class="seg" role="group" aria-label="Source band" id="bandseg"></div>
  </div>{{GROUPFILTER}}
  <div class="grp"><span>Buildings in Vic</span>
    <div class="seg" role="group" aria-label="Buildings touched before delivery" id="kseg"></div>
  </div>
  <div class="grp"><span>Sites named per column</span>
    <div class="seg" role="group" aria-label="Sites named per column" id="topseg"></div>
  </div>
  <div class="grp"><span>Fold lanes under</span>
    <div class="thr">
      <input id="thr" type="range" min="1" max="4000" step="1" value="50"
             aria-label="Fold every lane carrying fewer articles than this">
      <input id="thrn" type="number" min="1" max="4000" step="1" value="50"
             aria-label="Fold threshold, articles">
      <span class="unit">articles</span>
    </div>
  </div>
  <span class="sub" id="filternote"></span>
</div>

<div class="legend" id="legend"></div>

<div class="figure" id="fig">
  <div class="figscroll"><svg id="sankey" role="img" aria-label="Sankey diagram of the buildings a parcel is seen in, first to fifth, and then the depot that delivered it"></svg></div>
  <div class="figfoot"><span class="mono" id="figtot"></span><span id="foldnote"></span></div>
</div>

{{SECTIONS}}{{LANEDIAL}}
</div>
<div id="tip" role="status" aria-live="polite"></div>
<div id="pop" hidden role="dialog" aria-modal="true" aria-label="Everything in and out of this building"></div>
"""

JS = """
const F = DATA.fac, DEP = DATA.dep, TAIL = DATA.tail, DEPTH = DATA.depth;
const NOSITE = -1, ORETAIL = -2, ONET = -3, OTHER_DEST = -1;
// the empty journeys, told apart: each is a first-column node that names why there is nothing to
// draw, and the sub-line says what the reader should take from it
const ZSUB = {
  "-4": "the first scan on this bar is the depot that delivered it \u2014 one building, no journey",
  "-5": "every building before the depot was in another state; the journey here is one stop",
  "-6": "it did pass through " + DATA.scope.adjb + ", but the model carries none of them",
  "-7": "no scan of this evidence bar names a building for this parcel"};
const ZORD = ["-4", "-5", "-6", "-7"];
const OUT_OTHER = -8;
// DEPTH IS A DIAL, NOT A CONSTANT — the build decides how many touch columns to draw and
// everything on the page follows it: the column headings, the length filter, the copy in the
// tiles and the tooltips. Hard-coding five here meant setting DEPTH=3 in the build produced a
// diagram whose last column was labelled "4th building" and two filter buttons that matched
// nothing at all.
const ORD = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th"];
const ordinal = i => ORD[i] || (i + 1) + "th";
const COLS = [...Array(DEPTH)].map((_, i) => ordinal(i) + " " + DATA.colnoun + (i ? "" : " in Vic"))
                              .concat([DATA.destcol]);
// the filter vocabularies come from the payload, because the two extracts do not share them:
// one has two product classes and four source bands, the other four and five
const CLSL = new Map(DATA.cls), BANDL = new Map(DATA.bands);
const CKEY = DATA.cls.map(x => x[0]), BKEY = DATA.bands.map(x => x[0]);
// the optional third filter. On a page that declares no groups this is an empty list, GRP never
// leaves "ALL", and every test below is a no-op — which is why the atoms need not carry the field.
const GRPS = DATA.groups || [];
const GRPL = new Map(GRPS), GKEY = GRPS.map(x => x[0]);
const GPOS = 5 + DEPTH;                       // appended after the volume — see build()
// where each named site sits in its column's volume order — the fold reads this, and so does the
// node order, so a column never reshuffles when a filter changes
const RPOS = DATA.rank.map(list => new Map(list.map((f, i) => [f, i])));
// the six-step ramp stretched over however many buckets DEPTH gives, so the longest journeys are
// always the darkest — at DEPTH=5 this is the identity it has always been
const KCOL = k => "var(--k" + Math.round(Math.min(k, DEPTH + 1) * 6 / (DEPTH + 1)) + ")";
const WORD = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
const FEW = Math.min(3, Math.max(1, DEPTH - 1));   // the "finished early" tile, at any depth
const KLAB = k => k === 0 ? DATA.scope.zeroklab
               : k > DEPTH ? (DEPTH + 1) + " or more" : k + (k === 1 ? " building" : " buildings");

// 12 named buildings per column is where the picture stops being a wall of hairlines and
// still names everything that carries real volume; the control goes wider and narrower.
let CLS = "ALL", BAND = "ALL", GRP = "ALL", KF = "all", CAT = null, SEL = null, POP = null;
let TOPN = DATA.topdefault;
const $ = s => document.querySelector(s);
const svg = $("#sankey"), tip = $("#tip");
const fmt = n => Math.round(n).toLocaleString("en-AU");
const pc = (v, d) => (100 * v / Math.max(d, 1)).toFixed(1) + "%";

// ── the fold ─────────────────────────────────────────────────────────────────────────
// A site below the line does not disappear; it joins the residual for its KIND. Every building on
// this page is Victorian, so the distinction worth keeping at this altitude is the one the page
// cannot show any other way: a counter the public hands a parcel over at, or a building of the
// network the parcel was moved through.
function fold(f, col){
  if (f < 0) return f;
  const r = RPOS[col].get(f);
  return (r !== undefined && r < TOPN) ? f : (F[f].t ? ORETAIL : ONET);
}
let RESN = [];      // per column, how many buildings each residual is standing for
function nodeMeta(id){
  const i = id.indexOf(":"), col = +id.slice(0, i), f = +id.slice(i + 1);
  if (col === DEPTH) return f === OTHER_DEST
    ? {col, f, kind: "other", label: "Other " + DATA.destword + "s",
       sub: DATA.desttail + " " + DATA.destword + "s below the line, none big enough to name"}
    : {col, f, label: DEP[f], kind: "depot",
       sub: DATA.scope.destsub};
  if (f === NOSITE) return {col, f, label: DATA.scope.onward, kind: "none",
    sub: DATA.scope.onwardsub};
  if (f === OUT_OTHER) return {col, f, kind: "other", label: "Not one of ours",
    sub: (DATA.outn || [])[col] + " " + DATA.scope.adjb + " the model does not carry \u2014 the parcel "
       + "really was in one of them at this step, so it is a stop like any other; only the NAME "
       + "is folded away"};
  if (ZSUB[f]) return {col, f, label: DATA.zerolab[f], kind: "zero", sub: ZSUB[f]
    + " \u00b7 " + fmt(DATA.zero[f] || 0) + " articles across the day"};
  if (f === ORETAIL || f === ONET) {
    const n = (RESN[col] || [0, 0])[f === ORETAIL ? 0 : 1];
    return {col, f, kind: "other",
      label: (f === ORETAIL ? "Other retail counters" : "Other network buildings"),
      sub: n + (f === ORETAIL ? " post offices, LPOs and collection points"
                              : " " + DATA.scope.adjb) + ", none big enough to name at this step"};
  }
  const x = F[f];
  return {col, f, label: x.l, kind: "site", sub: x.n + " \\u00b7 " + x.s + " \\u00b7 " + x.r};
}

// ── aggregation ──────────────────────────────────────────────────────────────────────
// Every control change re-reads the atoms. There are ~11k of them, so this is a few milliseconds,
// and it is what keeps the fold honest: nothing is precomputed per filter, so the totals cannot
// drift away from the day.
function agg(){
  const links = new Map(), paths = new Map();
  const res = [...Array(DEPTH)].map(() => [new Set(), new Set()]);
  // the SAME journeys read without the naming fold, so the fold's cost is measured rather than
  // asserted: how many distinct paths and how many distinct buildings there were before it
  const rawP = new Set(), rawF = new Set();
  let day = 0, residv = 0;
  for (const a of DATA.atoms){
    const c = a[0], b = a[1], k = a[2], d = a[3 + DEPTH], v = a[4 + DEPTH];
    if (CLS !== "ALL" && CKEY[c] !== CLS) continue;
    if (BAND !== "ALL" && BKEY[b] !== BAND) continue;
    if (GRP !== "ALL" && GKEY[a[GPOS]] !== GRP) continue;
    if (KF !== "all" && k !== +KF) continue;
    day += v;
    const n = [];
    for (let i = 0; i < DEPTH; i++){
      const raw = a[3 + i], got = fold(raw, i);
      if (raw >= 0 && got < 0){ res[i][got === ORETAIL ? 0 : 1].add(raw); residv += v; }
      if (raw >= 0) rawF.add(raw);
      n.push(i + ":" + got);
    }
    const dk = DEPTH + ":" + d;
    rawP.add(a.slice(3, 3 + DEPTH).join("|") + "|" + d);
    for (let i = 0; i < DEPTH; i++){
      const s = n[i], t = i + 1 < DEPTH ? n[i + 1] : dk, key = s + ">" + t + ">" + k;
      const L = links.get(key);
      if (L) L.v += v; else links.set(key, {s, t, k, v});
    }
    const pk = n.join("|") + "|" + dk;
    const P = paths.get(pk);
    if (P) P.v += v; else paths.set(pk, {nodes: n.concat(dk), k, v});
  }
  RESN = res.map((r, i) => [r[0].size + TAIL[i][0], r[1].size + TAIL[i][1]]);
  return {links: [...links.values()], paths: [...paths.values()], day,
          rawpaths: rawP.size, rawfacs: rawF.size, residv};
}

// the length distribution, off the TRUE count rather than off the picture, so the 6+ bucket can
// still report what it actually is
function hist(useKF = true){
  const h = new Map();
  let day = 0, sum = 0;
  for (const [c, b, k, v, g] of DATA.khist){
    if (CLS !== "ALL" && CKEY[c] !== CLS) continue;
    if (BAND !== "ALL" && BKEY[b] !== BAND) continue;
    if (GRP !== "ALL" && GKEY[g] !== GRP) continue;
    if (useKF && KF !== "all" && Math.min(k, DEPTH + 1) !== +KF) continue;
    h.set(k, (h.get(k) || 0) + v); day += v; sum += k * v;
  }
  return {h, day, mean: sum / Math.max(day, 1)};
}

// ── layout ───────────────────────────────────────────────────────────────────────────
// PADL fits the longest scan name (30 characters, upper case, ~230px) to the LEFT of the first
// column's nodes, and the column pitch has to clear the same label between columns — the page
// stopped abbreviating, so the layout pays for it in width rather than the reader paying for it
// in a translation table. The figure scrolls sideways if the window is narrower.
const W = 1860, PADL = 250, PADR = 150, TOP = 62, BOT = 18, NW = 13, GAP = 9, H = 780;
const HIT = 11;        // the invisible lane a thin ribbon is hovered by

function build(links){
  const cols = [...Array(DEPTH + 1)].map(() => []), nodes = new Map();
  const get = id => {
    if (!nodes.has(id)){
      const m = nodeMeta(id), n = {id, ...m, in:0, out:0, inL:[], outL:[]};
      nodes.set(id, n); cols[m.col].push(n);
    }
    return nodes.get(id);
  };
  links.forEach(l => {
    const s = get(l.s), t = get(l.t);
    s.out += l.v; t.in += l.v;
    const L = {...l, sn:s, tn:t};
    s.outL.push(L); t.inL.push(L);
  });
  cols.forEach(c => c.forEach(n => n.val = Math.max(n.in, n.out)));
  // fixed order: the column's own volume ranking, then the two residuals, then the journeys
  // that are already finished. Depots keep their whole-day order. Nothing moves under a filter.
  const rank = n => n.col === DEPTH ? (n.f === OTHER_DEST ? 1e6 : n.f)
              : n.f === NOSITE ? 1002 : n.f === ONET ? 1001 : n.f === ORETAIL ? 1000
              : n.f === OUT_OTHER ? 999.5
              : ZSUB[n.f] ? 1003 + ZORD.indexOf(String(n.f))
              : (RPOS[n.col].get(n.f) ?? 999);
  cols.forEach(c => c.sort((a, b) => rank(a) - rank(b)));

  const inner = H - TOP - BOT;
  let scale = Infinity;
  cols.forEach(c => {
    const tot = c.reduce((a, n) => a + n.val, 0);
    if (tot > 0) scale = Math.min(scale, (inner - GAP * (c.length - 1)) / tot);
  });
  const xOf = i => PADL + i * ((W - PADL - PADR) / DEPTH);
  cols.forEach((c, i) => {
    let y = TOP;
    c.forEach(n => { n.x = xOf(i); n.y = y; n.h = Math.max(n.val * scale, 2.5); y += n.h + GAP; });
  });
  cols.forEach(c => c.forEach(n => {
    let o = 0;
    n.outL.sort((a, b) => a.tn.y - b.tn.y || a.k - b.k)
          .forEach(l => { l.sy = n.y + o + l.v * scale / 2; l.w = l.v * scale; o += l.v * scale; });
    o = 0;
    n.inL.sort((a, b) => a.sn.y - b.sn.y || a.k - b.k)
         .forEach(l => { l.ty = n.y + o + l.v * scale / 2; o += l.v * scale; });
  }));
  return {cols, nodes};
}

function ribbon(l){
  const x0 = l.sn.x + NW, x1 = l.tn.x, cx = (x0 + x1) / 2;
  return `M${x0},${l.sy}C${cx},${l.sy} ${cx},${l.ty} ${x1},${l.ty}`;
}
"""

JS += """
// ── the legend, which is also the highlight ──────────────────────────────────────────
// Volumes here are counted ONCE PER PARCEL, off the length distribution, not off the ribbons:
// a parcel that touches four buildings is drawn on five ribbons, so summing ink would count it
// five times and every share would be nonsense. Clicking a band isolates it in the picture and
// changes nothing about the numbers.
// THE BANDS FOLLOW THE DIAL (2026-08-31, user). They were read off `hst` — the measurement —
// while the ribbons beside them were the FOLDED picture, so a reader moving the fold dial watched
// every ribbon move and every band stand still, and the caption told them totals do not move.
// They now count the drawn ribbons (`drawnHist`, the same function the threshold table's mean
// comes from), so a band is what the colour beside it is actually worth on screen. The MEASURED
// count moves into the tooltip rather than being dropped: where the two differ, the fold has
// recoloured a journey, and that is the one step the fold is allowed to take and worth seeing.
function legend(A, hst, drawn){
  const by = new Map(), meas = new Map();
  const dh = drawnHist(drawn);
  dh.h.forEach((v, k) => { const b = Math.min(k, DEPTH + 1); by.set(b, (by.get(b) || 0) + v); });
  hst.h.forEach((v, k) => { const b = Math.min(k, DEPTH + 1); meas.set(b, (meas.get(b) || 0) + v); });
  // a band the fold emptied still belongs on the legend — it is a real cohort with no ribbon
  // left, which is exactly what the reader needs told
  meas.forEach((_, k) => { if (!by.has(k)) by.set(k, 0); });
  const keys = [...by.keys()].sort((a, b) => a - b);
  const day = dh.day || hst.day;
  $("#legend").innerHTML = keys.map(k =>
    `<button class="lg" data-cat="${k}" aria-pressed="${CAT === k}"
       title="parcels seen in ${KLAB(k)} before the depot that delivered them&#10;drawn ${
         fmt(by.get(k))}, measured ${fmt(meas.get(k) || 0)}${
         by.get(k) === (meas.get(k) || 0) ? "" : " \u2014 the difference is the fold recolouring"}">
       <i style="background:${KCOL(k)}"></i><b>${k > DEPTH ? (DEPTH + 1) + "+" : k}</b>
       <span class="lgv">${fmt(by.get(k))}</span>
       <span class="lgp">${pc(by.get(k), day)}</span></button>`).join("")
    + `<span class="lgnote">${CAT !== null
        ? "Showing journeys of <b>" + KLAB(CAT) + "</b> only \\u2014 click again for all."
        : "Colour is how many buildings the parcel touched. Click a band to isolate it."}${
        SEL ? " Pinned to <b>" + nodeMeta(SEL).label + "</b> in <b>" + COLS[nodeMeta(SEL).col]
              + "</b> \\u2014 click it again to close."
            : " Click a cell for every lane in and out of it, as a table."}
        Clicking moves nothing \u2014 but the counts are what is DRAWN, so they follow the fold
        dial; hover a band for the measured figure.</span>`;
  $("#legend").querySelectorAll("[data-cat]").forEach(b =>
    b.addEventListener("click", () => { CAT = CAT === +b.dataset.cat ? null : +b.dataset.cat;
                                        render(); }));
}

// ── the fold dial ────────────────────────────────────────────────────────────────────
// A WHAT-IF on the picture, never a re-measurement: the atoms are untouched and every column
// still totals the day. A lane is a MOVEMENT between two buildings — one source node, one
// destination — and it is judged ONCE, on its whole volume with the colour bands merged, because
// a truck is not two trucks because it carried parcels with different itineraries. A lane under
// the threshold is deleted and its volume divided equally between the lanes still leaving that
// node, BAND BY BAND: a parcel that touched four buildings is still drawn in the four-building
// colour wherever it lands. Volume moves; the cohort never does.
//
// Then the cascade, left to right and per band: a node that lost inbound volume has its outbound
// rescaled to what actually reached it, so a building that goes dark goes dark all the way across
// instead of despatching freight nobody sent it. Rescaling per band rather than in bulk is what
// keeps a parcel its own colour through the cascade — scaling a node's whole outbound would hand
// arriving three-building volume to whatever mix that node happens to send on.
//
// A source always keeps its largest lane: a threshold above a whole node has nowhere to put the
// volume, and emptying a node is a different decision from folding a lane.
// 50 articles is where the picture stops being a haze of hairlines while no depot's total moves
// by more than about 2% — see the drift the fold note reports. The dial goes to 1 for the
// measured picture, and up for a much simpler one.
// TWO THINGS FOLD, and they are the same question asked of different objects:
//   the LANE  — is this MOVEMENT worth drawing? Asked of the lane's whole volume, bands merged.
//   the BAND  — is this SPLIT of a surviving lane worth drawing? Asked of the band alone.
// Only folding lanes left the picture full of hairlines the dial had been told to remove: a lane
// clears 250 on the strength of its 2-building band and still draws a 4-article 5-building band
// beside it. A thin band moves SIDEWAYS — onto the same band on another lane out of that node —
// which keeps the parcel its own colour, and only merges into its lane-mate when no same-coloured
// lane is left anywhere at that node. That last resort is the one step in the whole dial that
// changes a parcel's colour, so it is counted separately and the caption says how much.
let THRESH = 50, FOLDSTAT = {};
const lanesOf = ls => new Set(ls.filter(l => l.v > 0).map(l => l.s + ">" + l.t)).size;
const colOf = id => +id.slice(0, id.indexOf(":"));

function foldLanes(links){
  FOLDSTAT = {of: lanesOf(links), left: lanesOf(links), cut: 0, dark: 0, moved: 0, added: 0,
              bands: 0, merged: 0, shifted: 0, shiftv: 0, thin: 0};
  if (THRESH <= 1) return links;
  const out = links.map(l => ({...l}));
  const stages = [...Array(DEPTH)].map(() => []);
  out.forEach(l => stages[colOf(l.s)].push(l));
  let arrived = new Map();                    // "node|band" -> volume that actually got there
  stages.forEach((stage, col) => {
    if (col > 0){
      const had = new Map();
      stage.forEach(l => had.set(l.s + "|" + l.k, (had.get(l.s + "|" + l.k) || 0) + l.v));
      stage.forEach(l => { const h = had.get(l.s + "|" + l.k) || 0;
                           l.v = h > 0 ? l.v * (arrived.get(l.s + "|" + l.k) || 0) / h : 0; });
      // a band that reached a node it never used to leave by needs somewhere to go, and the
      // rescale above cannot give it one — it lands on that node's destinations, equally
      const dests = new Map();
      stage.forEach(l => { if (!dests.has(l.s)) dests.set(l.s, new Set()); dests.get(l.s).add(l.t); });
      arrived.forEach((v, key) => {
        const i = key.lastIndexOf("|"), s = key.slice(0, i), k = +key.slice(i + 1);
        if (v <= 0 || (had.get(key) || 0) > 0 || !dests.has(s)) return;
        [...dests.get(s)].forEach(t => {
          const L = {s, t, k, v: v / dests.get(s).size};
          out.push(L); stage.push(L); FOLDSTAT.added++;
        });
      });
    }
    const bySrc = new Map();
    stage.forEach(l => { if (!bySrc.has(l.s)) bySrc.set(l.s, []); bySrc.get(l.s).push(l); });
    bySrc.forEach(ls => {
      const lane = new Map();
      ls.forEach(l => { if (!lane.has(l.t)) lane.set(l.t, []); lane.get(l.t).push(l); });
      const tot = t => lane.get(t).reduce((a, l) => a + l.v, 0);
      let keep = [...lane.keys()].filter(t => tot(t) >= THRESH);
      if (!keep.length) keep = [[...lane.keys()].reduce((a, b) => tot(b) > tot(a) ? b : a)];
      const drop = [...lane.keys()].filter(t => !keep.includes(t) && tot(t) > 0);
      // NO EARLY RETURN HERE. A node whose lanes are ALL fat still has thin bands inside them —
      // "on to the depot" sends thousands to every depot and a handful of five-building parcels
      // to each — and skipping the band fold when no lane was dropped is exactly the case where
      // the dial looked broken: the threshold was set to 250 and 34-article ribbons stayed on the
      // page. The lane fold below is conditional; the band fold after it is not.
      const pot = new Map();
      drop.forEach(t => lane.get(t).forEach(l => {
        if (l.v <= 0) return;
        pot.set(l.k, (pot.get(l.k) || 0) + l.v); FOLDSTAT.moved += l.v; l.v = 0;
      }));
      FOLDSTAT.cut += drop.length;
      pot.forEach((vol, k) => {
        const share = vol / keep.length;
        keep.forEach(t => {
          const rs = lane.get(t);
          let host = rs.find(l => l.k === k);
          if (!host){ host = {s: rs[0].s, t, k, v: 0}; rs.push(host); out.push(host); stage.push(host); }
          host.v += share;
        });
      });
      // ── the BAND fold, on what each surviving lane ACTUALLY ended up with ─────────
      // After the lane fold, never before: a band judged on its resting volume can be folded away
      // and then handed the volume of the lane that just folded into it.
      keep.forEach(t => {
        const rs = lane.get(t).filter(l => l.v > 0);
        if (rs.length < 2) return;
        const fat = rs.filter(l => l.v >= THRESH);
        if (!fat.length){
          // the lane clears but no band does — they collapse onto the biggest, because sending
          // them all away would empty a lane the threshold just said to keep
          const into = rs.reduce((a, b) => b.v > a.v ? b : a);
          rs.forEach(l => { if (l !== into){ into.v += l.v; FOLDSTAT.bands++;
                                             FOLDSTAT.merged += l.v; l.v = 0; } });
          return;
        }
        // thinnest first, and a host is any ribbon of the SAME COLOUR leaving this node by
        // another lane — a fat one for preference, otherwise the biggest thin one, which then
        // grows and is usually fat by the time the loop reaches it. Only when the colour exists
        // nowhere else at this node does the volume have to give up its band.
        rs.filter(l => l.v < THRESH).sort((a, b) => a.v - b.v).forEach(l => {
          if (l.v <= 0 || l.v >= THRESH) return;
          const others = keep.filter(t2 => t2 !== t)
                             .flatMap(t2 => lane.get(t2).filter(x => x.k === l.k && x.v > 0));
          const host = others.filter(x => x.v >= THRESH).sort((a, b) => b.v - a.v)[0]
                    || others.sort((a, b) => b.v - a.v)[0];
          const vol = l.v;
          l.v = 0;
          if (host){ host.v += vol; FOLDSTAT.shifted++; FOLDSTAT.shiftv += vol; }
          else { fat.reduce((a, b) => b.v > a.v ? b : a).v += vol;
                 FOLDSTAT.bands++; FOLDSTAT.merged += vol; }
        });
      });
    });
    stage.forEach(l => { if (l.v > 0) arrived.set(l.t + "|" + l.k,
                                                  (arrived.get(l.t + "|" + l.k) || 0) + l.v); });
  });
  const live = out.filter(l => l.v > 0.0001);
  FOLDSTAT.left = lanesOf(live);
  FOLDSTAT.dark = FOLDSTAT.of - FOLDSTAT.left - FOLDSTAT.cut;
  // what is STILL under the threshold, and it is one case only: a node whose entire outbound is
  // smaller than the threshold keeps its biggest lane, because emptying a node is a decision this
  // dial is not allowed to make. Counted so the caption can say it rather than let the reader
  // find a hairline and conclude the dial is broken.
  FOLDSTAT.thin = live.filter(l => l.v < THRESH).length;
  return live;
}

// The delivering-depot column is a MEASURED fact — the scan says which depot ran the round — and
// the fold moves volume between lanes, so a parcel folded onto another building then follows that
// building's depot mix and the column drifts. It is never large at a sane threshold, but it is
// not zero either, so the page measures it against the unfolded picture and says so out loud
// rather than letting the reader take a folded depot total as a count.
function depotDrift(before, after){
  const t = new Map(), f = new Map();
  before.forEach(l => { if (colOf(l.t) === DEPTH) t.set(l.t, (t.get(l.t) || 0) + l.v); });
  after.forEach(l => { if (colOf(l.t) === DEPTH) f.set(l.t, (f.get(l.t) || 0) + l.v); });
  let pct = 0, ea = 0;
  t.forEach((v, k) => {
    const d = Math.abs((f.get(k) || 0) - v);
    if (v > 0 && d / v > pct) pct = d / v;
    ea = Math.max(ea, d);
  });
  return {pct: 100 * pct, ea};
}

// ── THE CELL SHEET ───────────────────────────────────────────────────────────────────
// Hovering a ribbon answers one question about one lane, and on a page with hundreds of lanes
// that is a poor way to read a building: you cannot compare, you cannot copy a number down, and
// the thin ones are hard to catch. Clicking a cell opens everything that reaches it and everything
// that leaves it as two tables, biggest first, with the journey-length bands as a bar so the mix
// is visible without a second hover. Every row is itself a link — click one and the sheet moves to
// that building, so a route can be walked end to end without ever going back to the picture.
//
// The numbers are AS DRAWN, folded lanes included, because a sheet that disagreed with the ribbon
// beside it would be worse than no sheet. The footnote says so and names the threshold.
function open_cell(id){
  POP = id; SEL = id;
  render();
  const btn = id && document.querySelector(".popclose");
  if (btn && btn.focus) btn.focus();
}
function bandbar(map, tot){
  return `<div class="stack" title="${[...map.entries()].sort((a, b) => a[0] - b[0])
      .map(([k, v]) => KLAB(k) + ": " + fmt(v)).join(", ")}">`
    + [...map.entries()].sort((a, b) => a[0] - b[0])
        .map(([k, v]) => `<i style="width:${100 * v / Math.max(tot, 1)}%;background:${KCOL(k)}"></i>`)
        .join("") + `</div>`;
}
function drawPop(all, colTot){
  const box = $("#pop");
  if (!POP){ box.hidden = true; box.innerHTML = ""; return; }
  const m = nodeMeta(POP);
  const side = (ls, key) => {
    const g = new Map();
    ls.forEach(l => {
      const o = l[key].id, row = g.get(o) || {id: o, v: 0, k: new Map()};
      row.v += l.v; row.k.set(l.k, (row.k.get(l.k) || 0) + l.v); g.set(o, row);
    });
    return [...g.values()].sort((a, b) => b.v - a.v);
  };
  const ins = side(all.filter(l => l.tn.id === POP), "sn");
  const outs = side(all.filter(l => l.sn.id === POP), "tn");
  const sum = rows => rows.reduce((a, r) => a + r.v, 0);
  const tot = Math.max(sum(ins), sum(outs));
  const table = (rows, head, total) => rows.length ? `<h4>${head}</h4>
    <div class="tablewrap"><table><thead><tr><th>Building</th>
      <th style="text-align:right">Articles</th><th style="text-align:right">Share</th>
      <th>Buildings touched</th></tr></thead><tbody>`
    + rows.map(r => `<tr class="go" data-go="${r.id}"><td>${nodeMeta(r.id).label}</td>
        <td class="n">${fmt(r.v)}</td><td class="n">${pc(r.v, total)}</td>
        <td>${bandbar(r.k, r.v)}</td></tr>`).join("")
    + `</tbody></table></div>`
    : `<h4>${head}</h4><p class="sub">Nothing &mdash; this is where the column starts.</p>`;
  box.innerHTML = `<div class="popcard">
    <div class="pophead"><div>
      <h3>${m.label}</h3>
      <div class="psub">${m.sub}</div>
      <div class="pnum">${COLS[m.col]} &middot; ${fmt(tot)} articles &middot; ${pc(tot, colTot[m.col])}
        of this column &middot; ${ins.length} in, ${outs.length} out</div>
    </div><button class="popclose">Close &times;</button></div>
    <div class="popbody">
      <div>${table(ins, "Arriving from", sum(ins))}</div>
      <div>${table(outs, "Leaving for", sum(outs))}</div>
    </div>
    <p class="popnote">Click any row to open that building. The bar is the mix of journey lengths on
      that lane, in the diagram's colours. Figures are as drawn${THRESH > 1
        ? ", with lanes under " + fmt(THRESH) + " articles folded into the ones that survive \\u2014"
          + " set the dial to 1 for the measured lanes" : ", nothing folded"}.</p>
  </div>`;
  box.hidden = false;
  // assigned, not added: #pop outlives every redraw, and addEventListener here would stack one
  // more backdrop handler on it every time the sheet is rebuilt
  box.onclick = e => { if (e.target === box) open_cell(null); };
  box.querySelectorAll(".popclose").forEach(b =>
    b.addEventListener("click", () => open_cell(null)));
  box.querySelectorAll("[data-go]").forEach(tr =>
    tr.addEventListener("click", () => open_cell(tr.dataset.go)));
}
addEventListener("keydown", e => { if (e.key === "Escape" && POP) open_cell(null); });

function render(){
  const A = agg(), hst = hist();
  const raw = A.links;
  A.links = foldLanes(raw);
  const DRIFT = depotDrift(raw, A.links);
  // a filter can empty the cell the sheet is open on; it closes rather than showing a blank
  if (SEL && !A.links.some(l => l.s === SEL || l.t === SEL)){ SEL = null; POP = null; }
  legend(A, hst, A.links);
  const {cols} = build(A.links);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);

  const colTot = cols.map(c => c.reduce((a, n) => a + n.val, 0));
  let s = "";
  COLS.forEach((c, i) => {
    const x = PADL + i * ((W - PADL - PADR) / DEPTH), tx = i === DEPTH ? x + NW + 9 : x;
    s += `<text class="colhead" x="${tx}" y="18">${c}</text>`
       + `<text class="coltot" x="${tx}" y="36">${fmt(colTot[i])} articles</text>`;
  });

  const all = [];
  cols.forEach(c => c.forEach(n => n.outL.forEach(l => all.push(l))));
  all.sort((a, b) => b.v - a.v);
  const touches = l => !SEL || l.s === SEL || l.t === SEL;
  const lit = new Set(SEL ? all.filter(touches).flatMap(l => [l.s, l.t]) : []);
  // TWO PATHS PER RIBBON. The visible one is as thin as the volume says — a 40-article lane is a
  // hairline and should look like one — and takes no pointer events at all. The pointer is served
  // by an invisible lane over the top, never narrower than HIT px, so a hairline is as easy to hit
  // as a trunk. Before this, reading a thin ribbon meant hunting for a sub-pixel target, and a fat
  // ribbon crossing it stole the hover on the way.
  const off = l => (CAT !== null && Math.min(l.k, DEPTH + 1) !== CAT) || !touches(l);
  s += `<g id="links">` + all.map((l, i) =>
    `<path class="link" data-i="${i}" data-k="${Math.min(l.k, DEPTH + 1)}"
      data-s="${l.sn.id}" data-t="${l.tn.id}" d="${ribbon(l)}" stroke="${KCOL(l.k)}"
      stroke-width="${Math.max(l.w, 1.2)}" stroke-opacity="${off(l) ? .04 : .46}"></path>`
  ).join("") + `</g>`;
  // isolation takes a ribbon out of the POINTER as well as out of the ink: a hidden band that
  // still answers the hover hands you a tooltip for the layer you just asked to put away, so the
  // hit lane is simply not drawn for one
  s += `<g id="hits">` + all.map((l, i) => off(l) ? "" :
    `<path class="hitline" data-i="${i}" d="${ribbon(l)}"
       stroke-width="${Math.max(l.w, HIT)}" pointer-events="stroke"></path>`).join("") + `</g>`;

  s += `<g id="nodes">` + cols.flat().map(n => {
    const right = n.col === DEPTH, lx = right ? n.x + NW + 9 : n.x - 9;
    const mid = n.y + n.h / 2, two = n.h >= 22;
    return `<g class="node${n.id === SEL ? " sel" : ""}${SEL && !lit.has(n.id) ? " pale" : ""}"
      data-id="${n.id}">
      <rect x="${n.x}" y="${n.y}" width="${NW}" height="${n.h}" rx="2.5" fill="var(--ink-3)"></rect>
      <text class="nlabel" x="${lx}" y="${mid + (two ? -2 : 4)}"
        text-anchor="${right ? "start" : "end"}">${n.label}</text>
      ${two ? `<text class="nval" x="${lx}" y="${mid + 12}"
        text-anchor="${right ? "start" : "end"}">${fmt(n.val)}</text>` : ""}
    </g>`;
  }).join("") + `</g>`;
  svg.innerHTML = s;

  // conservation: a parcel is seen in a first building once and delivered once, so every column
  // carries the same total. If that ever fails, the picture is lying and says so out loud.
  const bad = colTot.some(t => Math.abs(t - A.day) > 0.5);
  $("#figtot").textContent = bad ? "COLUMNS DO NOT BALANCE: " + colTot.map(fmt).join(" / ")
                                 : fmt(A.day) + " articles in every column";
  $("#foldnote").innerHTML = "Naming the <b>" + TOPN + "</b> biggest buildings per step ("
    + RESN.reduce((a, r) => a + r[0] + r[1], 0) + " smaller ones fold into the two residuals). "
    + (THRESH <= 1
       ? "<b>Every measured lane drawn</b> \\u2014 " + FOLDSTAT.of + " of them."
       : "Lanes under <b>" + fmt(THRESH) + "</b> articles folded: " + FOLDSTAT.of + " down to <b>"
         + FOLDSTAT.left + "</b> (" + FOLDSTAT.cut + " too thin"
         + (FOLDSTAT.dark ? ", " + FOLDSTAT.dark + " emptied behind them" : "") + "), "
         + fmt(FOLDSTAT.moved) + " article-steps re-routed \\u2014 "
         + (100 * FOLDSTAT.moved / Math.max(A.day * DEPTH, 1)).toFixed(2) + "% of the ink"
         + (FOLDSTAT.shifted ? "; " + FOLDSTAT.shifted + " thin bands (" + fmt(FOLDSTAT.shiftv)
            + " articles) moved onto the same colour on a lane that survived" : "")
         + (FOLDSTAT.bands ? "; " + FOLDSTAT.bands + " (" + fmt(FOLDSTAT.merged) + ") had no "
            + "same-coloured lane left and merged into their own, the one step that recolours" : "")
         + (FOLDSTAT.thin ? "; " + FOLDSTAT.thin + " ribbons stay under the threshold because "
            + "their whole source is" : "")
         + ". No " + DATA.destword + "'s total moves by more than <b>" + DRIFT.pct.toFixed(1)
         + "%</b> (" + fmt(DRIFT.ea) + " articles). Set it to 1 for the measured picture.");
  STAGE_TOTAL = colTot;
  stats(A, hst, cols);
  wire(all, colTot);
  drawPop(all, colTot);
  thrTable(raw);
  $("#filternote").textContent = [CLS !== "ALL" ? CLSL.get(CLS) : null,
    BAND !== "ALL" ? BANDL.get(BAND) : null,
    GRP !== "ALL" ? GRPL.get(GRP) : null,
    KF !== "all" ? KLAB(+KF) : null].filter(Boolean).join(" \\u00b7 ");
}

let STAGE_TOTAL = [];
function stats(A, hst, cols){
  const filt = CLS !== "ALL" || BAND !== "ALL" || GRP !== "ALL" || KF !== "all";
  const le = k => [...hst.h].filter(([n]) => n <= k).reduce((a, [, v]) => a + v, 0);
  const named = new Set();
  cols.slice(0, DEPTH).forEach(c => c.forEach(n => { if (n.f >= 0) named.add(n.f); }));
  // THE TILES STAY ON THE MEASUREMENT. They answer "what was the day", not "what is drawn" —
  // the legend below the controls is the thing that follows the dial (see `legend`).
  $("#stats").innerHTML = [
    ["Articles", fmt(A.day), filt ? pc(A.day, DATA.day) + " of the " + fmt(DATA.day) + " traced"
                                  : "every delivery date in the extract"],
    [DATA.colnoun[0].toUpperCase() + DATA.colnoun.slice(1) + "s per parcel", hst.mean.toFixed(2),
      DATA.scope.tile + DATA.destword],
    [WORD[FEW] + " or fewer", pc(le(FEW), hst.day).replace("%", "<em>%</em>"), DATA.scope.fewtile],
    ["More than " + WORD[DEPTH].toLowerCase(), pc(hst.day - le(DEPTH), hst.day)
      .replace("%", "<em>%</em>"), "first " + DEPTH + " drawn, the rest inside the last ribbon"],
    [DATA.colnoun[0].toUpperCase() + DATA.colnoun.slice(1) + "s named", fmt(named.size),
      RESN.some(r => r[0] + r[1]) ? "plus the two residuals per step"
                                  : "every one the model carries — nothing folds"],
    [DATA.destword + "s", fmt(cols[DEPTH].length),
      DATA.desttail ? "biggest named, " + fmt(DATA.desttail) + " folded" : "all of them, named"],
  ].map(([k, v, sub]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div>
     <div class="s">${sub}</div></div>`).join("");
}

function wire(all, colTot){
  const fig = $("#fig");
  svg.querySelectorAll(".hitline").forEach(h => {
    const l = all[+h.dataset.i], p = svg.querySelector(`.link[data-i="${h.dataset.i}"]`);
    h.addEventListener("pointerenter", e => {
      fig.classList.add("dim"); p.classList.add("on");
      svg.querySelector(`.node[data-id="${l.sn.id}"]`).classList.add("on");
      svg.querySelector(`.node[data-id="${l.tn.id}"]`).classList.add("on");
      const step = l.sn.col + 1 === DEPTH ? "the run into the delivering depot"
                 : ORD[l.sn.col] + " building to the " + ORD[l.sn.col + 1];
      const note = ZSUB[l.sn.f] ? "these parcels never reach a second building \u2014 "
                                  + "the node says why"
        : l.tn.f === NOSITE && l.sn.f === NOSITE ? "already finished before this step"
        : l.tn.f === NOSITE ? "these parcels went straight to their depot from here"
        : l.k > DEPTH ? "these parcels touch " + KLAB(l.k) + " \\u2014 the picture draws their "
                        + "first " + DEPTH + ", so more buildings sit inside this ribbon"
        : "parcels seen in " + KLAB(l.k) + " in all";
      show(e, l.sn.label + " \\u2192 " + l.tn.label,
        fmt(l.v) + " articles \\u00b7 " + pc(l.v, colTot[l.sn.col]) + " of this column \\u00b7 "
        + step, note);
    });
    h.addEventListener("pointermove", pos);
    h.addEventListener("pointerleave", clear);
    // a ribbon is a way INTO a cell too: clicking one opens the building it lands in, which is
    // almost always what you were about to do next
    h.addEventListener("click", () => { clear(); open_cell(l.tn.id); });
  });
  svg.querySelectorAll(".node").forEach(g => {
    const id = g.dataset.id;
    g.addEventListener("pointerenter", e => {
      fig.classList.add("dim"); g.classList.add("on");
      const only = CAT !== null ? `[data-k="${CAT}"]` : "";
      svg.querySelectorAll(`.link[data-s="${id}"]${only},.link[data-t="${id}"]${only}`)
         .forEach(p => {
        p.classList.add("on");
        svg.querySelector(`.node[data-id="${p.dataset.s}"]`).classList.add("on");
        svg.querySelector(`.node[data-id="${p.dataset.t}"]`).classList.add("on");
      });
      const m = nodeMeta(id);
      const v = all.filter(l => l.sn.id === id).reduce((a, b) => a + b.v, 0)
             || all.filter(l => l.tn.id === id).reduce((a, b) => a + b.v, 0);
      show(e, m.label, fmt(v) + " articles \\u00b7 " + pc(v, colTot[m.col]) + " of "
           + COLS[m.col].toLowerCase(), m.sub);
    });
    g.addEventListener("pointermove", pos);
    g.addEventListener("pointerleave", clear);
    g.addEventListener("click", () => { clear(); open_cell(SEL === id && POP ? null : id); });
  });
  function clear(){
    fig.classList.remove("dim");
    svg.querySelectorAll(".on").forEach(x => x.classList.remove("on"));
    tip.style.opacity = 0;
  }
}
function show(e, t, v, n){
  tip.innerHTML = `<span class="tt">${t}</span><span class="tv">${v}</span>`
                + (n ? `<span class="tn">${n}</span>` : "");
  tip.style.opacity = 1; pos(e);
}
function pos(e){
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX + 14, innerWidth - r.width - 10) + "px";
  tip.style.top  = Math.min(e.clientY + 14, innerHeight - r.height - 10) + "px";
}
"""

JS += """
// ── the table ────────────────────────────────────────────────────────────────────────
// THE RECEIPT FOR THE DIAL, AT EVERY SETTING IT HAS. Every other number on this page describes
// the day; this one describes what the PICTURE did to it. The fold is re-run once per threshold
// against the same unfolded links the diagram was built from — never interpolated, never cached —
// so a row says exactly what the reader would see if they moved the dial to it.
//
// The steps are the ones asked for: tens to a hundred, hundreds to five hundred, then five
// hundred at a time. Fine where the picture actually changes, coarse where it has stopped
// changing.
const THRSTEPS = (() => {
  const t = [1];
  for (let v = 10; v <= 100; v += 10) t.push(v);
  for (let v = 200; v <= 500; v += 100) t.push(v);
  for (let v = 1000; v <= 2000; v += 500) t.push(v);
  return t;
})();

// what the PICTURE says a parcel touches, off the first column's ribbons rather than off the
// measurement — which is the whole point of putting it in this table. The fold keeps a lane's
// colour when it moves it, except at the one last-resort step that recolours, so this only moves
// when the dial has started rewriting journeys instead of tidying ink.
const meanTouched = ls => drawnHist(ls).mean;

// AS DRAWN, not as measured — the same column-0 ribbons meanTouched() has always read, returned
// in hist()'s shape so the tiles and the threshold table cannot disagree about what the picture
// says. `l.k` is the parcel's journey-length band and the fold carries a lane's colour with it,
// so this only moves when the dial has started rewriting journeys instead of tidying ink. That
// is exactly the thing worth showing on a tile: the measured value sits beside it, and the gap
// between the two IS the fold's distortion.
function drawnHist(ls){
  const h = new Map();
  let day = 0, sum = 0;
  ls.forEach(l => {
    if (colOf(l.s) !== 0) return;
    h.set(l.k, (h.get(l.k) || 0) + l.v); day += l.v; sum += l.k * l.v;
  });
  return {h, day, mean: sum / Math.max(day, 1)};
}

function thrTable(raw){
  if (!$("#thrtable")) return;      // the page declined the section — see LANE_DIAL
  const held = THRESH;
  const rows = THRSTEPS.map(t => {
    THRESH = t;
    const folded = foldLanes(raw);
    // the cascade rescales in fractions of an article; the reader is owed whole ones
    return {t, of: FOLDSTAT.of, left: FOLDSTAT.left, cut: FOLDSTAT.of - FOLDSTAT.left,
            moved: Math.round(FOLDSTAT.moved), mean: meanTouched(folded)};
  });
  THRESH = held;
  foldLanes(raw);            // put FOLDSTAT back the way the caption above found it
  const max = Math.max(...rows.map(r => r.moved), 1);
  $("#thrtable tbody").innerHTML = rows.map(r =>
    `<tr class="${r.t === THRESH ? "hit" : ""}" data-thr="${r.t}" style="cursor:pointer">
      <td class="n">${r.t === 1 ? "1 (none)" : fmt(r.t)}</td>
      <td class="n"><b>${fmt(r.left)}</b></td>
      <td class="n">${r.cut ? fmt(r.cut) : "\u2014"}</td>
      <td class="n">${r.moved ? fmt(r.moved) : "\u2014"}</td>
      <td class="n">${r.mean.toFixed(2)}</td>
      <td><div class="minibar"><span style="width:${100 * r.moved / max}%;
        background:var(--accent)"></span></div></td></tr>`).join("");
  $("#thrtable tbody").querySelectorAll("tr[data-thr]").forEach(tr =>
    tr.addEventListener("click", () => setThresh(+tr.dataset.thr)));
}

// ── controls ─────────────────────────────────────────────────────────────────────────
function press(sel, val, attr){
  document.querySelectorAll(sel).forEach(x =>
    x.setAttribute("aria-pressed", String(x.dataset[attr] === val)));
}
function setK(v){ KF = v; press("#kseg [data-k]", v, "k"); render(); }
function seg(host, attr, pairs, set){
  $(host).innerHTML = [["ALL", "All"], ...pairs].map(([k, l]) =>
    `<button data-${attr}="${k}" aria-pressed="${k === "ALL"}">${l}</button>`).join("");
  $(host).querySelectorAll(`[data-${attr}]`).forEach(b => b.addEventListener("click", () => {
    set(b.dataset[attr]); press(`${host} [data-${attr}]`, b.dataset[attr], attr); render(); }));
}
seg("#clsseg", "cls", DATA.cls, v => CLS = v);
seg("#bandseg", "band", DATA.bands, v => BAND = v);
if (GRPS.length) seg("#grpseg", "grp", GRPS, v => GRP = v);
seg("#kseg", "k", [...Array(DEPTH + 1)].map((_, i) => [String(i + 1),
    i + 1 > DEPTH ? (DEPTH + 1) + "+" : String(i + 1)]), v => KF = v);

// how many buildings each step names before the fold. The widest stop is whatever the build
// shipped, so the control can never claim more sites than the page actually carries.
$("#topseg").innerHTML = [...new Set([8, 12, 16, DATA.topdefault, DATA.keep])]
  .filter(n => n <= DATA.keep).sort((a, b) => a - b)
  .map(n => `<button data-top="${n}" aria-pressed="${n === TOPN}">${n}</button>`).join("");
$("#topseg").querySelectorAll("[data-top]").forEach(b => b.addEventListener("click", () => {
  TOPN = +b.dataset.top; press("[data-top]", b.dataset.top, "top"); SEL = null; render(); }));
const thrRange = $("#thr"), thrNum = $("#thrn");
function setThresh(v){
  const n = Math.round(Number(v));
  THRESH = Math.max(1, Math.min(4000, Number.isFinite(n) ? n : 1));
  thrRange.value = THRESH; thrNum.value = THRESH;
  render();
}
thrRange.addEventListener("input", e => setThresh(e.target.value));
thrNum.addEventListener("change", e => setThresh(e.target.value));
render();
"""


if __name__ == "__main__":
    main()
