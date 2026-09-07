"""How many buildings did a site's freight touch before it got there?

    IN   a paths.csv written by path_census.py
    OUT  four CSVs beside it, and two top-N tables on the console

One row of paths.csv is one distinct journey and the count of articles that rode it. Grouping
that by delivering site and by how many buildings the journey held turns it into a profile: for
each site, how much of its freight arrived after 0, 1, 2, 3 or 4+ building touches.

WHAT ZERO MEANS. It is not missing data. paths.csv is written at the Sankey's own scope, where
the journey is cut off at the parcel's final arrival at the site that delivered it — so a parcel
whose only building WAS that site has nothing left to draw and lands at 0. Read it as "took it in
and delivered it itself", and it is the biggest single bucket for the StarTrack sites.

Run:  uv run python utilities/site_touch_profile.py
      uv run python utilities/site_touch_profile.py --top 20
      uv run python utilities/site_touch_profile.py --paths outputs/path_census_vic/paths.csv
"""
import argparse
import pathlib

import pandas as pd

DEFAULT = "outputs/path_census_sankey/paths.csv"
BUCKETS = ["0", "1", "2", "3", "4+"]


def bucket(n):
    """0, 1, 2, 3 or 4+ — the tail is one bucket because past three touches the volume is thin
    and the interesting question stops being 'how many' and starts being 'why'."""
    return str(n) if n < 4 else "4+"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paths", default=DEFAULT, help=f"paths.csv to read (default {DEFAULT})")
    ap.add_argument("--top", type=int, default=10, metavar="N",
                    help="how many delivering sites to profile (default 10)")
    ap.add_argument("--out", help="where to write the CSVs (default: beside paths.csv)")
    args = ap.parse_args()

    src = pathlib.Path(args.paths)
    out = pathlib.Path(args.out) if args.out else src.parent
    out.mkdir(parents=True, exist_ok=True)
    paths = pd.read_csv(src)
    site = paths.columns[-1]            # 'site' on the new extract, 'depot' on the old one
    day = paths.articles.sum()

    # ── 1. the sites, biggest first — this is what "top 10" is chosen on ──────────────
    total = (paths.groupby(site)
             .agg(total_volume=("articles", "sum"), number_of_paths=("path", "count"))
             .sort_values("total_volume", ascending=False))
    total["share_of_day"] = (100 * total.total_volume / day).round(2)
    total.to_csv(out / "total_volume.csv")

    # ── 2. every site x every building count ─────────────────────────────────────────
    by = (paths.groupby([site, "buildings"])
          .agg(volume_by_num_buildings_touch=("articles", "sum"),
               number_of_path=("path", "count"))
          .reset_index()
          .sort_values(["volume_by_num_buildings_touch", site, "buildings"], ascending=False))
    by.to_csv(out / "volume_by_site_buildings.csv", index=False)

    # ── 3. the top N, bucketed ───────────────────────────────────────────────────────
    top = total.head(args.top).index
    q = paths[paths[site].isin(top)].copy()
    q["touches"] = q.buildings.map(bucket)
    vol = (q.pivot_table(index=site, columns="touches", values="articles",
                         aggfunc="sum", fill_value=0)
           .reindex(columns=BUCKETS, fill_value=0)
           .reindex(top))
    vol.insert(0, "total_volume", total.total_volume.reindex(top))
    pct = vol[BUCKETS].div(vol.total_volume, axis=0).mul(100).round(1)
    pct.columns = [f"{c}_pct" for c in BUCKETS]
    prof = pd.concat([vol, pct], axis=1)
    prof.index.name = site
    prof.to_csv(out / f"top{args.top}_touch_profile.csv")

    # ── 4. the sites whose freight goes round in a circle ────────────────────────────
    # `circular` is path_census's own test: the parcel reached the building that would DELIVER it,
    # left, and came back. Ranked on volume rather than on share, because
    # a small site where three parcels looped is a curiosity and a big one where thousands did is
    # a problem — the share column is right beside it for reading the other way.
    circ = pd.DataFrame({
        "circular_volume": (paths[paths.circular == "yes"].groupby(site).articles.sum()),
        "circular_paths": (paths[paths.circular == "yes"].groupby(site).path.count()),
    }).fillna(0).astype(int)
    circ["total_volume"] = total.total_volume.reindex(circ.index)
    circ["pct_of_site"] = (100 * circ.circular_volume / circ.total_volume).round(1)
    circ = circ.sort_values("circular_volume", ascending=False)
    circ.to_csv(out / "circular_by_site.csv")

    # ── the console table ────────────────────────────────────────────────────────────
    w = max(len(str(x)) for x in top)
    print(f"\n  TOP {args.top} DELIVERING SITES — {day:,} articles in {src}")
    print(f"  buildings touched before the site that delivered it; 0 = it delivered its own\n")
    print(f"  {site.upper():<{w}}{'total':>10}" + "".join(f"{b:>10}" for b in BUCKETS)
          + "   " + "".join(f"{b + '%':>7}" for b in BUCKETS))
    for s in top:
        r = prof.loc[s]
        print(f"  {s:<{w}}{int(r.total_volume):>10,}"
              + "".join(f"{int(r[b]):>10,}" for b in BUCKETS)
              + "   " + "".join(f"{r[f'{b}_pct']:>6.1f}%" for b in BUCKETS))
    rest = day - int(prof.total_volume.sum())
    print(f"\n  these {args.top} sites carry {int(prof.total_volume.sum()):,} of {day:,} articles "
          f"({100 * prof.total_volume.sum() / day:.1f}%); {rest:,} ride the other "
          f"{len(total) - args.top:,} sites")
    cday = int(paths[paths.circular == "yes"].articles.sum())
    ct = circ.head(args.top)
    cw = max([len(str(x)) for x in ct.index] + [len(site)])
    print(f"\n  TOP {args.top} SITES BY CIRCULAR VOLUME — {cday:,} articles ({100 * cday / day:.1f}%) "
          f"went to the building\n  that would deliver them, LEFT, and came back, on "
          f"{int((paths.circular == 'yes').sum()):,} distinct paths\n")
    print(f"  {site.upper():<{cw}}{'circular':>12}{'site total':>12}{'% of site':>11}"
          f"{'paths':>8}{'% of all':>10}")
    for s_, r in ct.iterrows():
        print(f"  {s_:<{cw}}{int(r.circular_volume):>12,}{int(r.total_volume):>12,}"
              f"{r.pct_of_site:>10.1f}%{int(r.circular_paths):>8,}"
              f"{100 * r.circular_volume / cday:>9.1f}%")
    print(f"\n  these {args.top} sites hold {int(ct.circular_volume.sum()):,} of the "
          f"{cday:,} circular articles ({100 * ct.circular_volume.sum() / cday:.1f}%); "
          f"{len(circ):,} sites have any")
    print(f"\n  wrote total_volume.csv, volume_by_site_buildings.csv, "
          f"top{args.top}_touch_profile.csv, circular_by_site.csv to {out}")


if __name__ == "__main__":
    main()
