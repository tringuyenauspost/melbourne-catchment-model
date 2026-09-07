"""Every distinct itinerary, the loops inside it, and the lanes that run both ways.

    IN   the same reduction the itinerary pages read — either extract
    OUT  a console report, and optionally four CSVs

The Sankey answers "how many buildings, and which" at the altitude of a column. It cannot answer
"how many DIFFERENT journeys are there", because a Sankey folds: two parcels that share a first
and a third building are one ribbon in between whether or not they shared the second. This script
does not fold. It counts whole ordered paths, so a path here is a path — and that is what makes
the wasteful ones findable.

FOUR QUESTIONS, ONE PASS ────────────────────────────────────────────────────────────
  PATHS    how many distinct itineraries the day actually contains, and how few of them carry
           most of it. Concentration is the useful number: if 40 paths carry 80% of the articles,
           the network has 40 real routes and a long tail of exceptions.
  LOOPS    a parcel that comes back to a building it had already left. This is the honest
           definition of a circular path: not a repeat scan (those collapse), but a DEPARTURE and
           a RETURN with at least one other building in between. The segment between the two
           visits is the loop, and it is reported whole, because "MPF came back 900 times" is
           useless next to "MPF -> SWP -> MPF, 900".
  LANES    every directed building-to-building move, and then the pairs that run BOTH ways on the
           same day. A two-way lane is not automatically waste — a hub pair genuinely trades in
           both directions — but the SMALLER of the two directions is the volume that could have
           been avoided if the sortation upstream had known where the parcel was going, so it is
           ranked on that.
  WASTE    the three above are three different definitions, and a slide cannot carry three. The
           ledger puts them on one unit — the TOUCH, one building entered by one article, which is
           an unload, a sort, a load, a dock slot and a person — and then prices every journey
           against THE LEANEST ONE THE SAME DAY PROVED between the same two end buildings. The
           claim is a receipt rather than a standard: not "this should have been two buildings"
           but "34,000 articles that entered and left at the same two buildings did it in two".
           `--norm-min` sets how much volume a journey needs before it may be quoted as one.

WHERE — AND WHY THE CATCHMENT IS A CUT ON BUILDINGS ─────────────────────────────────
`--place catchment` keeps only buildings inside the first-mile catchment polygon, so what a path
shows is the MELBOURNE PART of the journey and nothing else: an interstate parcel's Sydney legs
fall away and what is left is what our own network did with it. Parcels that never entered the
catchment fall out entirely and are counted where they fall. It needs a coordinate per facility,
which only the new extract has. The polygon is the first-mile ROUTE footprint and reaches Geelong,
Bacchus Marsh and Kyneton — it is the catchment we collect from, not the metropolitan area, and
the report says catchment for that reason.

SCOPE — AND WHY IT DEFAULTS TO THE WHOLE CHAIN ──────────────────────────────────────
`--scope full` (the default) is the whole folded chain, end to end: interstate buildings included,
and the buildings a parcel was seen in AFTER it reached its delivering site included too. That is
deliberate. Waste does not respect the page's frame — a parcel that reaches its depot, leaves, and
comes back is exactly the thing this script exists to find, and the Sankey cuts it off by design.

`--scope vic` is the plain answer to "where did it go once it was ours": every Victorian building
the parcel entered, from the first one onward, in order, nothing truncated and nothing else
filtered. The interstate prefix falls away, so the path starts where the parcel crossed the border.

`--scope drawn` reproduces the page's journey instead — Victoria only, truncated at the parcel's
final arrival at its delivering site, and put through `--group`. Use it when a number here has to
reconcile with a number on the diagram.

ONE PATH IS ONE CONSIGNMENT ────────────────────────────────────────────────────────
The chain is recorded per CONSIGNMENT and the volume is its article count, so a consignment
holding several articles that were split up contributes ONE path carrying all of them, and the
path is the union of what its articles did. That is worth knowing before reading a loop as a
mis-sort — but it is a small caveat here: multi-article consignments are 4.2% of the 20 May day,
and the longest paths in it are single-article consignments, so they are real journeys rather than
merged ones. `--singles` drops the doubt entirely by keeping only one-article consignments.

THE VOCABULARY IS THE PAGE'S ───────────────────────────────────────────────────────
Names are alias-folded by `sankey_facility_path.learn_aliases`, so one building is one node here
as it is there — Melbourne North's two names, the van arms, the PARCEL DELIVERY / PDC pairs. The
delivering site is shown in brackets at the end of a path but takes no part in the lane and loop
counts: for the 20 May extract it is a depot LABEL rather than a facility name, and mixing the two
vocabularies into one lane table would invent moves that no scan records.

Run:  uv run python utilities/path_census.py
      uv run python utilities/path_census.py --source new --top 30
      uv run python utilities/path_census.py --source new --scope vic --csv outputs/path_census_vic
      uv run python utilities/path_census.py --scope drawn --group model
      uv run python utilities/path_census.py --singles                      # one-article consignments only
      uv run python utilities/path_census.py --source new --place catchment --csv outputs/path_census_melb
      uv run python utilities/path_census.py --csv outputs/path_census      # paths, loops, lanes, waste
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plotting"))   # the diagrams live there
import argparse
import collections
import csv
import pathlib

import sankey_facility_path as OLD

HERE = pathlib.Path(__file__).resolve().parents[1]   # the repo root, one up from utilities/


# ══ THE SOURCES ══════════════════════════════════════════════════════════════════════
# Each returns the six-column frame the pages already normalise to — articles, cls, band, chain,
# dest, own — plus the facility -> state map and what the fold did. Nothing below this point
# knows which extract it is reading, which is the same bargain `build` makes.
def source_old(args):
    if args.place != "any":
        raise SystemExit("  --place needs a coordinate on every facility, and the 20 May extract\n"
                         "  carries a state name instead. Run --source new for a catchment cut.")
    paths = OLD.load_paths(args.rebuild)
    q = paths[paths.origin != "UNKNOWN"].copy()
    raw = [list(c) for c in q.full_chain]
    folds, state = OLD.learn(zip(raw, q.articles), OLD.facility_state(args.rebuild))
    if args.touch == "physical":
        chains = raw
    else:
        ch = OLD.touch_chain(args.touch, args.rebuild).reindex(q.Consignment_ID)
        chains = [c if isinstance(c, list) else [] for c in ch]
    return OLD.frame(q, chains), state, folds, "depot", None


def source_new(args):
    import sankey_facility_path_new as NEW
    p = NEW.load_paths(args.rebuild)
    p = p[p.site.notna() & p.Product_type.isin(NEW.PRODUCT)].copy()
    ch = NEW.chain_by_time(args.touch, args.rebuild).reindex(p.Consignment_ID)
    chains = [c if isinstance(c, list) else [] for c in ch]
    folds, state = OLD.learn(zip(chains, p.articles), NEW.facility_point(args.rebuild))
    place = NEW.facility_catchment(args.rebuild) if args.place != "any" else None
    return NEW.frame(p, chains), state, folds, "site", place


SOURCES = {"old": source_old, "new": source_new}


# ══ THE JOURNEY ══════════════════════════════════════════════════════════════════════
def collapse(names):
    """Consecutive repeats out. A path lists BUILDINGS, not visits — the pages' own rule."""
    return [x for i, x in enumerate(names) if not i or names[i - 1] != x]


def canon_place(place):
    """The facility -> inside-the-catchment map, re-keyed on canonical names, by UNION.

    `canon_state` asserts that two names which fold together agree, and is right to: a building
    cannot be in two states. This one cannot assert, because the position it tests is the MEDIAN
    OF THE BUILDING'S OWN EVENTS and a van arm's events are lodgements out on the round — a name
    that folds onto a depot inside the catchment can sit 15 km outside it on its own points. The
    fold has already decided those are one building, so the union is the honest merge: inside if
    ANY of its names is.
    """
    out = {}
    for fac, inside in place.items():
        name = OLD.alias(fac)
        out[name] = out.get(name, False) or bool(inside)
    return out


def journeys(f, state, group, scope, place=None):
    """One (path, dest, articles, cls, band) per parcel, in the scope asked for.

    The fold runs first in every scope, and the collapse runs AFTER every cut — a cut that leaves
    two identical neighbours would otherwise manufacture a move from a building to itself, which
    is the one thing a lane can never be. `place`, when given, is the last cut of all: the
    catchment is a filter on BUILDINGS, so what survives is the part of the journey that happened
    inside it, whatever the parcel did before or after. A path emptied by that cut is yielded as
    the empty tuple and the caller decides — `census` drops it and counts what it dropped, because
    a parcel that never entered the catchment is not a Melbourne journey at all.
    """
    for r in f.itertuples():
        chain = collapse([OLD.alias(x) for x in r.chain])
        if scope == "full":
            path = chain
        elif scope == "vic":
            path = collapse([x for x in chain if state.get(x) == "Victoria"])
        else:
            idx = [i for i, x in enumerate(chain) if x in r.own]
            ch = chain[:idx[-1]] if idx else chain
            vic = [x for x in ch if state.get(x) == "Victoria"]
            if group is not None:
                vic = [x for x in vic if x in group]
            path = collapse(vic)
        if place is not None:
            path = collapse([x for x in path if place.get(x, False)])
        yield tuple(path), r.dest, int(r.articles), r.cls, r.band


# ══ LOOPS ════════════════════════════════════════════════════════════════════════════
# A loop is a building seen, LEFT, and reached again. Consecutive repeats are already collapsed,
# so every repeat that survives has at least one other building between the two visits and is a
# real return. Overlapping returns are reported as the successive pairs rather than as one span:
# A -> B -> A -> C -> A is two loops (A>B>A and A>C>A), which is what an operator would count.
def loops_in(path):
    """Every (loop tuple, building) the path contains, in order."""
    pos = collections.defaultdict(list)
    for i, x in enumerate(path):
        pos[x].append(i)
    out = []
    for name, ix in pos.items():
        for a, b in zip(ix, ix[1:]):
            out.append((tuple(path[a:b + 1]), name))
    return out


# ══ CIRCULAR — BACK TO THE BUILDING THAT DELIVERED IT ════════════════════════════════
# The user's definition (2026-08-27), and a narrower one than `loops_in`: a parcel that bounces
# between two hubs and is then delivered normally is not the thing being looked for. What is, is
# freight that REACHED THE BUILDING THAT WOULD DELIVER IT, LEFT, AND CAME BACK — the depot had it,
# sent it away, and got it again, which is the one loop that is always a mistake.
#
# WHY THE TEST DEPENDS ON THE SCOPE. `drawn` truncates the journey at the parcel's FINAL arrival
# at its delivering site, so that arrival is not in the path at all: seeing the site anywhere in a
# drawn path already means there was an earlier visit, and one occurrence is proof. Every other
# scope keeps the final arrival, so it takes two.
def returns_to_site(path, dest, scope):
    """Did the parcel leave the building that delivered it and come back to it?"""
    return path.count(dest) >= (1 if scope == "drawn" else 2)


# ══ THE REPORT ═══════════════════════════════════════════════════════════════════════
def pct(v, d):
    return f"{100 * v / d:.1f}%" if d else "—"


def arrow(seq, cap=0):
    """The path as text. A pathological path is 100 buildings long and unreadable in a terminal,
    so the console elides the middle and says how much it dropped; the CSVs never elide."""
    if cap and len(seq) > cap:
        head, tail = cap - 3, 2
        return (" > ".join(seq[:head]) + f" > … {len(seq) - head - tail} more … > "
                + " > ".join(seq[-tail:]))
    return " > ".join(seq)


def census(f, state, group, scope, destword, top, long_at, place=None):
    day = 0
    away = 0                               # articles the catchment cut left with no journey
    touches = 0                            # article x buildings — the day's handling, in touches
    paths = collections.Counter()          # path (+ dest) -> articles
    plain = collections.Counter()          # path alone, no dest
    lens = collections.Counter()
    lanes = collections.Counter()
    loopvol = collections.Counter()
    loopby = collections.Counter()
    loop_extra = collections.Counter()     # building -> touches that exist only because of a loop
    looped_art = 0
    dest_of = collections.defaultdict(collections.Counter)
    od = collections.defaultdict(collections.Counter)   # (first, last) -> path -> articles
    for path, dest, art, _cls, _band in journeys(f, state, group, scope, place):
        if place is not None and not path:
            away += art
            continue
        day += art
        touches += art * len(path)
        paths[(path, dest)] += art
        plain[path] += art
        lens[len(path)] += art
        dest_of[path][dest] += art
        if path:
            od[(path[0], path[-1])][path] += art
        for a, b in zip(path, path[1:]):
            lanes[(a, b)] += art
        ls = loops_in(path)
        if ls:
            looped_art += art
            for loop, name in ls:
                loopvol[loop] += art
                loopby[name] += art
                # the return itself is the extra touch: a loop of n buildings adds n-1 of them
                for x in loop[1:]:
                    loop_extra[x] += art
    return {"day": day, "away": away, "touches": touches, "paths": paths, "plain": plain,
            "lens": lens, "lanes": lanes, "loopvol": loopvol, "loopby": loopby,
            "loop_extra": loop_extra, "looped": looped_art, "dest_of": dest_of, "od": od,
            "destword": destword, "top": top, "scope": scope, "long": long_at,
            "place": place is not None}


def report(c):
    day, top = c["day"], c["top"]
    print(f"\n  === PATH CENSUS · {c['scope']} scope · {day:,} articles ===")
    print(f"    {len(c['plain']):,} distinct building paths "
          f"({len(c['paths']):,} counting the {c['destword']} they ended at)")
    mean = sum(k * v for k, v in c["lens"].items()) / day
    print(f"    {mean:.2f} buildings per article; longest path drawn {max(c['lens'])} buildings")
    # CONCENTRATION. The number that says whether the network has routes or exceptions.
    cum, marks, need = 0, [0.5, 0.8, 0.95, 0.99], []
    for i, (_, v) in enumerate(c["plain"].most_common(), 1):
        cum += v
        while marks and cum >= marks[0] * day:
            need.append((marks.pop(0), i))
    print("    " + "; ".join(f"{int(m * 100)}% of articles ride {n:,} paths" for m, n in need))
    # THE LONG TAIL, WHICH IS WHERE THE WASTE IS. A path with more buildings than the network has
    # roles is not a route, it is an exception someone had to handle: mis-sorts, re-inductions,
    # freight that went to a building and had to be moved again. Ranked by LENGTH, not volume,
    # because the point is to find the worst journey rather than the commonest one.
    over = sum(v for k, v in c["lens"].items() if k > c["long"])
    print(f"\n  === THE LONGEST PATHS — {over:,} articles ({pct(over, day)}) ride a path of more "
          f"than {c['long']} buildings ===")
    print(f"    {'articles':>10}{'n':>4}   path")
    for path, v in sorted(c["plain"].items(), key=lambda kv: (-len(kv[0]), -kv[1]))[:top]:
        print(f"    {v:>10,}{len(path):>4}   {arrow(path, cap=12)}")

    print(f"\n    {'articles':>10}{'share':>8}{'cum':>8}   path")
    cum = 0
    for path, v in c["plain"].most_common(top):
        cum += v
        d = c["dest_of"][path].most_common(1)[0]
        tail = f"  [{d[0]}]" if len(c["dest_of"][path]) == 1 else \
               f"  [{d[0]} +{len(c['dest_of'][path]) - 1} more]"
        print(f"    {v:>10,}{pct(v, day):>8}{pct(cum, day):>8}   "
              f"{arrow(path) if path else '(no building)'}{tail}")

    print(f"\n  === CIRCULAR PATHS — a building left and reached again ===")
    if not c["loopvol"]:
        print("    none: no parcel returns to a building it had already left")
    else:
        print(f"    {c['looped']:,} articles ({pct(c['looped'], day)}) contain at least one loop; "
              f"{len(c['loopvol']):,} distinct loops")
        print(f"\n    {'articles':>10}{'share':>8}   loop")
        for loop, v in c["loopvol"].most_common(top):
            print(f"    {v:>10,}{pct(v, day):>8}   {arrow(loop)}")
        print(f"\n    {'articles':>10}{'share':>8}   building returned to")
        for name, v in c["loopby"].most_common(min(top, 15)):
            print(f"    {v:>10,}{pct(v, day):>8}   {name}")

    lanes = c["lanes"]
    lane_art = sum(lanes.values())
    print(f"\n  === LANES — {len(lanes):,} distinct moves carrying {lane_art:,} article-legs ===")
    print(f"    {'articles':>10}{'share':>8}   lane")
    for (a, b), v in lanes.most_common(top):
        print(f"    {v:>10,}{pct(v, lane_art):>8}   {a} > {b}")

    # TWO-WAY LANES. Ranked on the SMALLER direction, which is the volume that would disappear if
    # the freight had been sorted for its destination the first time. The larger direction is the
    # lane doing its job; the smaller one is the correction.
    two = {}
    for (a, b), v in lanes.items():
        if (b, a) in lanes and a < b:
            two[(a, b)] = (v, lanes[(b, a)])
    print(f"\n  === LANES THAT RUN BOTH WAYS — {len(two):,} pairs ===")
    if not two:
        print("    none")
    else:
        back = sum(min(x, y) for x, y in two.values())
        print(f"    {back:,} article-legs ({pct(back, lane_art)} of all legs) ride the SMALLER "
              f"direction of a pair — the correction, not the route")
        print(f"    {'forward':>10}{'back':>10}{'min':>10}   pair")
        for (a, b), (v, w) in sorted(two.items(), key=lambda kv: -min(kv[1]))[:top]:
            f_, b_ = (a, b) if v >= w else (b, a)
            print(f"    {max(v, w):>10,}{min(v, w):>10,}{min(v, w):>10,}   {f_} <> {b_}")


# ══ WASTE — MEASURED AGAINST WHAT THE DAY ITSELF PROVED POSSIBLE ═════════════════════
# The three tables above each show a KIND of waste, and a slide cannot carry three definitions.
# This is the one number that puts them on a common footing: the TOUCH. Every building a parcel
# enters is a touch — an unload, a sort, a load, a dock slot, a person — so touches are what the
# network actually spends, and a journey that costs more touches than another journey between the
# same two points spent more than it had to.
#
# THE BENCHMARK IS NOT A TARGET, IT IS A RECEIPT. For every (first building, last building) pair
# the day contains, the leanest journey is the SHORTEST path that actually ran between them, and
# it is quoted with its volume so nobody has to take it on faith. That is a much harder claim to
# argue with than a design standard: not "this should have been three buildings" but "4,000 other
# articles that entered and left the network at the same two buildings did it in three".
#
# BOTH ENDS ARE PINNED, AND THAT IS THE WHOLE DESIGN. Pinning only the first building rewards a
# MISSING SCAN: a parcel seen at MPF and nowhere else looks like the leanest way to reach Sunshine
# West, and every parcel that was honestly scanned into the depot is charged an extra touch for
# it. Pinning the last building too makes the comparison a real one — same entry, same exit, and
# the only question left is HOW MANY BUILDINGS IN BETWEEN. A parcel that went MPF > MGF > TPF >
# SWP is measured against MPF > SWP, a lane that demonstrably runs, and against nothing else.
#
# The delivering site rides along as a label but is deliberately NOT in the key. With both ends
# pinned it is very nearly determined anyway, and putting 1,400 sites into the key would split
# every OD into slivers too small to have a proven benchmark at all.
#
# `--norm-min` guards the way this can still lie. A single article with a scan gap in the MIDDLE
# looks like a direct journey and would set an impossible bar for its whole OD, so a journey has
# to have carried at least N articles to be quoted as the benchmark (default 25). Where no path
# at an OD clears the bar, the OD's own busiest path is the benchmark instead — which can only
# UNDERSTATE the excess, never invent it.
def leanest(c, norm_min):
    """(first building, last building) -> the shortest journey the day proved between them."""
    out = {}
    for key, ps in c["od"].items():
        proven = [p for p, v in ps.items() if v >= norm_min] or list(ps)
        out[key] = min(proven, key=lambda p: (len(p), -ps[p], p))
    return out


def excess(c, norm):
    """Every path that cost more touches than its OD's leanest, dearest first.

    Rows are (extra touches, articles, path, dest, benchmark, buildings over) — one per path AND
    delivering site, so a row lines up with a row of paths.csv. The benchmark is the OD's, and the
    OD does not carry the site, so two rows sharing a path share a benchmark.
    """
    rows = []
    for (path, dest), art in c["paths"].items():
        if not path:
            continue
        n = norm[(path[0], path[-1])]
        over = len(path) - len(n)
        if over > 0:
            rows.append((art * over, art, path, dest, n, over))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    return rows


# THE SHAPE OF THE EXCESS. Both ends of the comparison are pinned, so every wasteful journey is
# one of exactly three things, and naming them is what makes the number actionable: a round trip
# is a building that took freight it could not finish, one extra building is a sortation decision
# made at the wrong site, and two or more is a journey that lost its way. They are a partition —
# these three do sum, unlike the ledger above.
SHAPES = ("ROUND TRIP — left a building and came back to it",
          "ONE EXTRA BUILDING between the same two ends",
          "TWO OR MORE EXTRA BUILDINGS between the same two ends")


def shape_of(path, norm):
    if len(norm) == 1:
        return SHAPES[0]
    return SHAPES[1] if len(path) == len(norm) + 1 else SHAPES[2]


def excess_by_building(rows):
    """Where the extra touches LAND — the buildings a wasteful path enters and its benchmark does
    not, charged the article count each time. This is the table that turns a list of odd journeys
    into a place to go and look."""
    out = collections.Counter()
    for _extra, art, path, _dest, norm, _over in rows:
        base = collections.Counter(norm)
        for x in path:
            if base[x]:
                base[x] -= 1              # a touch the benchmark also pays for
            else:
                out[x] += art
    return out


def waste_report(c, rows, by_bldg, norm_min):
    day, top, tou = c["day"], c["top"], c["touches"]
    lane_art = sum(c["lanes"].values())
    two = sum(min(v, c["lanes"][(b, a)]) for (a, b), v in c["lanes"].items()
              if (b, a) in c["lanes"] and a < b)
    art = sum(r[1] for r in rows)
    extra = sum(r[0] for r in rows)
    loop_extra = sum(c["loop_extra"].values())

    print(f"\n  === THE WASTE LEDGER — {tou:,} building touches bought this day's "
          f"{day:,} articles ({tou / day:.2f} each) ===")
    print("    three counts of the same day. THEY OVERLAP — one journey can be circular, ride a "
          "two-way\n    lane and beat its benchmark all at once — so read them as three views, "
          "never as a sum. Every\n    row is priced in TOUCHES: a backhaul leg counts as the one "
          "touch it lands at the far end.\n")
    print(f"    {'articles':>10}{'share':>8}{'touches':>10}{'of all':>8}   ")
    print(f"    {c['looped']:>10,}{pct(c['looped'], day):>8}{loop_extra:>10,}"
          f"{pct(loop_extra, tou):>8}   CIRCULAR — came back to a building they had left")
    print(f"    {'':>10}{'':>8}{two:>10,}{pct(two, tou):>8}   BACKHAUL — the smaller "
          f"direction of a two-way lane ({pct(two, lane_art)} of legs)")
    print(f"    {art:>10,}{pct(art, day):>8}{extra:>10,}{pct(extra, tou):>8}   OVER THE "
          f"BENCHMARK — dearer than the leanest journey proven on the same OD")

    shape_t, shape_a = collections.Counter(), collections.Counter()
    for ex, a, path, _dest, n, _over in rows:
        shape_t[shape_of(path, n)] += ex
        shape_a[shape_of(path, n)] += a
    print(f"\n  === THE THREE SHAPES OF EXCESS — and these DO sum ===")
    print(f"    {'articles':>10}{'share':>8}{'touches':>10}{'of all':>8}   ")
    for k in SHAPES:
        print(f"    {shape_a[k]:>10,}{pct(shape_a[k], art):>8}{shape_t[k]:>10,}"
              f"{pct(shape_t[k], extra):>8}   {k}")

    print(f"\n  === THE DEAREST JOURNEYS — ranked on touches spent above the benchmark ===")
    print(f"    the benchmark is the shortest path that carried {norm_min:,}+ articles between "
          f"the SAME FIRST and\n    SAME LAST building; where no path did, that OD's busiest path "
          f"stands in. Both ends are pinned,\n    so what is being priced is purely the buildings "
          f"IN BETWEEN.\n")
    for ex, a, path, dest, norm, over in rows[:top]:
        print(f"    {ex:>10,} extra touches = {a:,} articles x +{over}   [{dest}]")
        print(f"    {'':>10}   rode  {arrow(path, cap=10)}")
        print(f"    {'':>10}   not   {arrow(norm, cap=10)}")

    print(f"\n  === WHERE THE EXTRA TOUCHES LAND — buildings the benchmark never enters ===")
    print(f"    {'touches':>10}{'share':>8}   building")
    for name, v in by_bldg.most_common(min(top, 20)):
        print(f"    {v:>10,}{pct(v, extra):>8}   {name}")


def write_csv(c, out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "paths.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["articles", "share", "buildings", "circular", "revisits_any_building",
                    "path", c["destword"]])
        for (path, dest), v in c["paths"].most_common():
            # an empty path is not a blank cell, it is the page's zero bucket: the parcel had
            # nothing left to draw once the delivering site was taken off the end
            w.writerow([v, round(100 * v / c["day"], 4), len(path),
                        "yes" if returns_to_site(path, dest, c["scope"]) else "no",
                        "yes" if loops_in(path) else "no",
                        arrow(path) or "(nothing drawn — delivered by the first building "
                                       "it was seen in)", dest])
    with (out / "loops.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["articles", "share", "buildings_in_loop", "returned_to", "loop"])
        for loop, v in c["loopvol"].most_common():
            w.writerow([v, round(100 * v / c["day"], 4), len(loop) - 1, loop[0], arrow(loop)])
    with (out / "lanes.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["articles", "from", "to", "reverse_articles", "two_way_min"])
        for (a, b), v in c["lanes"].most_common():
            rev = c["lanes"].get((b, a), 0)
            w.writerow([v, a, b, rev, min(v, rev)])
    with (out / "waste.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["extra_touches", "articles", "buildings_over", "buildings", "path",
                    c["destword"], "benchmark_buildings", "benchmark_path", "benchmark_articles"])
        for ex, a, path, dest, norm, over in c["waste"]:
            w.writerow([ex, a, over, len(path), arrow(path), dest, len(norm), arrow(norm),
                        c["od"][(path[0], path[-1])][norm]])
    print(f"\n  wrote paths.csv, loops.csv, lanes.csv, waste.csv to {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=tuple(SOURCES), default="old",
                    help="old: the 20 May extract. new: inputs/new_scan_events/ (default old)")
    ap.add_argument("--scope", choices=("full", "vic", "drawn"), default="full",
                    help="full (default): the whole chain, interstate and after-delivery included "
                         "— where the waste is. vic: every Victorian building, from the first one "
                         "onward, nothing truncated. drawn: the page's journey, so numbers "
                         "reconcile with the Sankey")
    ap.add_argument("--group", choices=("model", "scan"), default="scan",
                    help="drawn scope only: which buildings survive (default scan — every one)")
    ap.add_argument("--touch", choices=tuple(OLD.EVIDENCE), default="entry",
                    help="which events may name a building (default entry)")
    ap.add_argument("--top", type=int, default=20, metavar="N",
                    help="rows per table (default 20); the CSVs always carry everything")
    ap.add_argument("--singles", action="store_true",
                    help="keep only one-article consignments, where the path is unambiguously "
                         "ONE article's journey")
    ap.add_argument("--place", choices=("any", "catchment"), default="any",
                    help="any (default): every building. catchment: keep ONLY buildings inside "
                         "the first-mile catchment, so a path is the Melbourne part of the "
                         "journey and parcels that never entered it fall out (--source new only)")
    ap.add_argument("--norm-min", type=int, default=25, metavar="N",
                    help="a journey must have carried N articles to be quoted as its OD's "
                         "benchmark (default 25) — see the note above `leanest`")
    ap.add_argument("--long", type=int, default=6, metavar="N",
                    help="a path with more than N buildings is an exception, not a route "
                         "(default 6) — only changes what the longest-paths headline counts")
    ap.add_argument("--csv", metavar="DIR", help="also write paths.csv, loops.csv, lanes.csv here")
    ap.add_argument("--rebuild", action="store_true", help="re-read the scan files")
    args = ap.parse_args()

    f, state, folds, destword, place = SOURCES[args.source](args)
    place = canon_place(place) if place else None
    whole = int(f.articles.sum())
    multi = int(f.articles[f.articles > 1].sum())
    if args.singles:
        f = f[f.articles == 1]
    group = OLD.model_names() if args.group == "model" else None
    if args.scope == "full" and args.group == "model":
        print("  note: --group applies to the drawn scope only; the full chain keeps every "
              "building by definition")
        group = None
    c = census(f, state, group, args.scope, destword, args.top, args.long, place)
    norm = leanest(c, args.norm_min)
    c["waste"] = excess(c, norm)
    by_bldg = excess_by_building(c["waste"])
    print(f"\n  source: {args.source} · bar {args.touch.upper()} · "
          f"{sum(len(v) for v in folds.values())} scan names folded onto {len(folds)} buildings")
    print(f"    {multi:,} of {whole:,} articles ({100 * multi / whole:.1f}%) ride in a "
          f"multi-article consignment, whose path is the union of what its articles did"
          + (" — DROPPED, --singles is on" if args.singles else "; --singles drops them"))
    if place is not None:
        print(f"    the catchment keeps {sum(place.values()):,} of {len(place):,} buildings; "
              f"{c['away']:,} articles ({100 * c['away'] / (c['away'] + c['day']):.1f}%) never "
              f"entered one and fall out — what is left is the MELBOURNE part of each journey")
    report(c)
    waste_report(c, c["waste"], by_bldg, args.norm_min)
    if args.csv:
        write_csv(c, args.csv)


if __name__ == "__main__":
    main()
