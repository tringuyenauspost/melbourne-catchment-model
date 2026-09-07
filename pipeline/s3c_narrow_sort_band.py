"""Narrow the ±5-point round-2 band, so the solver has to sort twice instead of choosing to.

    IN / OUT   outputs/melbourne_optilogic_final/FlowConstraints.csv   (rewritten in place)

WHY THE BAND AND NOT THE RELAY. `add_no_relay_constraints.py` deletes a free arc; it does not
create a sort. Blocked from passing finished freight through a second building, the solver sends
it DIRECT from the first one — still one sort — because nothing requires otherwise. What actually
decides the round-2 volume is the band on the `Despatch1_<SITE>` lanes, and on the 31-Aug run it
is barely used:

    44 banded lanes, 33,210 EA flowing.  Sum of the Mins 22,169, sum of the Maxes 61,436.
    26 of the 44 sit EXACTLY on their Min — 28,226 EA of headroom nobody spends.

Round-2 is a COST CHOICE inside that band. `BOM_LOAD_DIRECT_*` skips UNLOAD2 / SORT2 / LOAD2, so
the cheap corner of every band is its floor and the solver sits there. Narrow the band and the
floor rises to the measured share; the band centres sum to 41,802 EA against 33,210 today.

THE TWO PATCHES ARE COMPLEMENTARY, NOT ALTERNATIVES, and that is the whole point of running them
together. Sunshine West, Melbourne North and Bayswater are the three sites that hold the relay AND
the three sitting at 100% of `Facilities.throughputcapacity`. Tightening the band alone asks them
for round-2 inbound they have no room for; the no-relay rows free 19,444 EA of exactly that room,
at exactly those three sites. Either alone fails — one changes nothing, the other has nowhere to
put the freight.

HOW A ROW IS REBUILT. The note carries the measured share (`9.1% +/- 5%`) and the pair carries the
band, so the arrival base the percentages were taken on can be recovered and a new band written on
it. Unclamped pairs use `base = (Max - Min) / 0.10` and `centre = (Min + Max) / 2`, both exact.
Where the Min was clamped at 0 (a measured share below the half-width) the note's rounded share is
the only route: `base = Max / (c + 0.05)`. Those are the small lanes — every one of them is under
150 EA — so the rounding costs nothing that matters.

IDEMPOTENT, and it has to be: re-deriving the base from an ALREADY-NARROWED pair would read the
new half-width as if it were the old one and collapse the band a second time. The centre and base
are stamped into the note the first time and read back on every run after, so running this twice
with the same `--band` is the same as running it once, and re-running with a different one widens
or narrows from the ORIGINAL band rather than from the last result.

NOT IN THE NOTEBOOK — same standing as add_no_relay_constraints.py. `SORT_BAND` is a build dial
(`factors.py`), and rebuilding through the chain-2 notebook is what you do when the whole model is
being regenerated. This is the post-build patch for testing a band without that, and a rebuild
drops it. Run the two patches in either order; this one checks the interaction whichever way.

Run:  uv run python post_process/narrow_sort_band.py --band 0.01
"""
import argparse
import csv
import os
import re
from collections import defaultdict

from _paths import DATA_ROOT, FINAL_OUT

HERE = str(DATA_ROOT)
MODEL = str(FINAL_OUT)
FC = os.path.join(MODEL, "FlowConstraints.csv")
OLD_BAND = 0.05                        # what the notebook wrote, and what the notes say
TAG = "sort-band"                      # our stamp in `notes`, carrying centre and base
SHARE = re.compile(r"([\d.]+)% \+/- ([\d.]+)%")
STAMP = re.compile(r" \[%s[^]]*?c=([\d.]+) b=([\d.]+) w=[\d.]+\]" % TAG)


def rows(name):
    with open(os.path.join(MODEL, f"{name}.csv"), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--band", type=float, default=0.01,
                    help="new half-width, in SHARE POINTS of the arrival base (default 0.01 = "
                         "+/-1 point; 0 pins every lane on its measured share)")
    args = ap.parse_args(argv)
    assert 0 <= args.band <= OLD_BAND, f"--band must be within [0, {OLD_BAND}]"

    fc = rows("FlowConstraints")
    cols = list(fc[0].keys())
    pairs = defaultdict(dict)
    for r in fc:
        if "_Despatch1_" in r["productname"] and r["status"] == "Include":
            pairs[(r["originname"], r["destinationname"], r["productname"])][r["constrainttype"]] = r
    assert pairs, "no Despatch1 band rows in FlowConstraints.csv — has the model been rebuilt?"

    # ── THE INTERACTION CHECK ────────────────────────────────────────────────────────
    # A tighter band pushes freight down a Despatch1 lane, and the receiving site then has to
    # round-2 sort it and despatch the result. If add_no_relay_constraints.py has written a Max 0
    # against that very despatch the model is infeasible the moment the Min bites, and it would
    # read as "the band is too tight" rather than as two patches disagreeing. The round-2 output
    # is flavoured by the FIRST-sort site, not the sorting one — `Despatch1_MPF` sorted at SWP
    # becomes `..._MPF_Despatch2` — so the product to test is the lane's own flavour.
    blocked = {(r["originname"], r["productname"]) for r in fc
               if r["constrainttype"] == "Max" and float(r["constraintvalue"] or 0) == 0
               and "no-relay" in (r["notes"] or "")}
    # WHICH Despatch2 the destination will actually make. `split_despatch2_by_route.py` gives the
    # round-2 route its own `..._Despatch2R` product, so after that patch a round-2 site produces
    # the R half and the base name names the DIRECT half — which the destination cannot make and
    # is rightly blocked from despatching. Testing the base name here reported 13 false clashes
    # and refused to write; the product to test is the one the round-2 recipe yields.
    _prods = {r["productname"] for r in rows("Products")}
    clash = []
    for o, d, p in pairs:
        cls_fam, site = p.split("_Despatch1_")
        _made = f"{cls_fam}_{site}_Despatch2R"
        if _made not in _prods:
            _made = f"{cls_fam}_{site}_Despatch2"
        if (d, _made) in blocked:
            clash.append((o, d, p))
    if clash:
        raise SystemExit(
            f"  !! {len(clash)} banded lanes send freight to a site the no-relay rows forbid from "
            f"despatching the result:\n     "
            + "\n     ".join(f"{o} -> {d}  {p}" for o, d, p in clash)
            + "\n     Those two patches disagree — fix before solving.")
    print(f"  interaction check: {len(pairs)} banded lanes, none blocked by the "
          f"{len(blocked)} no-relay Max-0 rows")

    # ── rewrite each pair on its recovered centre and base ───────────────────────────
    changed, floor_old, floor_new, ceil_new = 0, 0.0, 0.0, 0.0
    report = []
    for (o, d, p), b in sorted(pairs.items()):
        if "Min" not in b or "Max" not in b:
            continue                                # not a two-sided band; leave it alone
        lo, hi = b["Min"], b["Max"]
        m = STAMP.search(lo["notes"] or "")
        if m:                                       # a previous run recorded the truth
            centre, base = float(m.group(1)), float(m.group(2))
        else:
            v_lo, v_hi = float(lo["constraintvalue"]), float(hi["constraintvalue"])
            if v_lo > 0:                            # exact: neither bound was clamped
                base, centre = (v_hi - v_lo) / (2 * OLD_BAND), (v_lo + v_hi) / 2
            else:                                   # Min clamped at 0 — the note is all there is
                c = float(SHARE.search(lo["notes"]).group(1)) / 100
                base, centre = v_hi / (c + OLD_BAND), v_hi * c / (c + OLD_BAND)
        assert 0 <= centre <= base, (
            f"recovered centre {centre:,.0f} outside its base {base:,.0f} on {o} -> {d} {p} — the "
            f"stamp did not parse and the band was re-derived from an already-narrowed pair")
        new_lo = max(0.0, centre - args.band * base)
        new_hi = min(base, centre + args.band * base)
        assert new_lo <= new_hi, f"inverted band on {o} -> {d} {p}"
        floor_old += float(lo["constraintvalue"])
        floor_new += round(new_lo)
        ceil_new += round(new_hi)
        for r, v in ((lo, new_lo), (hi, new_hi)):
            r["constraintvalue"] = str(int(round(v)))
            r["notes"] = STAMP.sub("", r["notes"] or "") + (
                f" [{TAG} band now +/-{args.band:.1%} (the % in the note above is the ORIGINAL);"
                f" c={centre:.6f} b={base:.6f} w={args.band:g}]")
        changed += 1
        report.append((o, d, p, float(lo["constraintvalue"]), float(hi["constraintvalue"]), centre))

    assert floor_new <= ceil_new, (
        f"floor {floor_new:,.0f} above ceiling {ceil_new:,.0f} — refusing to write")
    with open(FC, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(fc)

    nice = lambda f: f.replace("HUB_", "").replace("PUD_", "").replace("_", " ")   # noqa: E731
    print(f"  band +/-{OLD_BAND:.1%} -> +/-{args.band:.1%} on {changed} lanes")
    print(f"    round-2 FLOOR  {floor_old:,.0f} -> {floor_new:,.0f} EA   "
          f"(the run solved 33,210 with the old floor)")
    print(f"    round-2 CEILING          -> {ceil_new:,.0f} EA")
    by = defaultdict(lambda: [0.0, 0.0])
    for _o, d, _p, mn, mx, c in report:
        by[nice(d)][0] += mn
        by[nice(d)][1] += c
    print(f"    {'second-sort site':<22}{'new floor':>11}{'band centre':>13}")
    for s in sorted(by, key=lambda x: -by[x][1]):
        print(f"      {s:<20}{by[s][0]:>11,.0f}{by[s][1]:>13,.0f}")
    print(f"  wrote {FC}")


if __name__ == "__main__":
    main()
