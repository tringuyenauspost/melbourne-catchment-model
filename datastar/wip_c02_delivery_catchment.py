"""Clean the last-mile delivery extract into the Melbourne delivery catchment.

DataStar port of `inputs/deilvery_events/generate_melbourne_scenario.ipynb`. Reads
`melbourne_pdc_last_mile_catchment.csv` (one row per delivered consignment) and produces
`melbourne_catchment.csv`: the same rows, cleaned, with the delivery point's POSTCODE and the
terminating depot's surveyed coordinate. Written to the table OUTPUT_TABLE and to OUTPUT_CSV.

The rules are the notebook's, in the notebook's order -- the same ones Wip_C02_ScanClean
applies to the scan events (1a-1c, 2), so the two tables agree on which volume is in scope:

  1a  drop rows with no delivery coordinate
  1b  drop rows terminating at an excluded facility (BEFORE the remaps -- see ScanClean: the
      Mulgrave volume that belongs arrives via the V03974 remap; remap first and 1b deletes it)
  1c  keep only in-scope products
  2   remap facility aliases (Holloway Dr -> Bayswater, V03974 @ Oakleigh South -> Mulgrave)
  3   tag each delivery point with the postcode polygon it falls in (a LEFT join: a point in
      no polygon keeps its row with a blank POSTCODE)
  4   overwrite Terminating_Lat/Long with the surveyed depot coordinate

The notebook's geopandas `sjoin` is replaced by a shapely STRtree query on the DISTINCT
delivery coordinates against POSTCODE_BOUNDARY, a WKT CSV of the Victorian postcode polygons
(DataStar rejects .shp; see utilities/convert_postcode_boundaries.py, which refuses to write
unless every delivery coordinate gets the same postcode as the full-resolution national
shapefile). `sjoin` would DUPLICATE a row whose point touches two polygons; here the lowest
polygon index wins, as in ScanClean, so a row is never double-counted. On the 20 May extract
no point touches two polygons, so the two agree row for row.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
OUTPUT_TABLE = "Wip_C02_DeliveryCatchment"

RAW_DIR = "/projects/My Files/DataStar/Raw Inputs/deilvery_events"
RAW_PATH = f"{RAW_DIR}/melbourne_pdc_last_mile_catchment.csv"
POSTCODE_BOUNDARY = f"{RAW_DIR}/vic_postcode_boundaries.csv"
OUTPUT_CSV = f"{RAW_DIR}/melbourne_catchment.csv"

READ_COLS = [
    "Del_Long", "Del_Lat", "Terminating_facility_name", "Terminating_Lat", "Terminating_Long",
    "Product_type", "Contract_ID", "Contract_type", "User_role", "Article_count",
]
EXCLUDE_FACILITY_SET = {"WESTERN DC", "MULGRAVE PDC", "STARTRACK MULGRAVE"}
CLEAN_PRODUCT_SET = {
    "eParcel Express", "eParcel Standard", "eParcel Returns", "rParcel Post Plus", "Metro Next Day",
}
# surveyed depot coordinates; the extract's Terminating_Lat/Long are rounded to 3 dp and are
# sometimes another depot's
FACILITY_COORDS = {
    "ABBOTSFORD PARCEL DELIVERY": (-37.807663, 145.006708),
    "BAYSWATER PDC": (-37.850121, 145.282369),
    "DANDENONG SOUTH PDC": (-38.013138, 145.200767),
    "DAREBIN PDC": (-37.742981, 145.028548),
    "MELBOURNE NORTH PDC": (-37.627467, 144.941256),
    "MOUNT WAVERLEY PARCEL DELIVERY": (-37.875511, 145.128504),
    "MULGRAVE PDC": (-37.907287, 145.156119),
    "OAKLEIGH SOUTH PDC": (-37.925748, 145.097855),
    "PAKENHAM PARCEL DELIVERY": (-38.094414, 145.482365),
    "SUNSHINE WEST PARCEL DELIVERY": (-37.810432, 144.814491),
    "TULLAMARINE PDC": (-37.702444, 144.854473),
}
OUTPUT_COLS = [
    "Del_Lat", "Del_Long", "Terminating_facility_name", "Terminating_Lat", "Terminating_Long",
    "POSTCODE", "Product_type", "Contract_ID", "Contract_type", "User_role", "parcel_count",
    "delivery_lat", "delivery_lon", "wh_lat", "wh_lon",
]


def volumes(df):
    """(rows, articles). One row per consignment here, so rows ARE consignments."""
    return len(df), int(df["Article_count"].sum())


def stage(stages, label, df):
    stages.append((label,) + volumes(df))
    return df


def log_ledger(stages, notes=()):
    """Running volume after each rule, and what that rule removed on its own."""
    logger.info("")
    logger.info("=== STAGE LEDGER ===")
    logger.info("  %-44s %10s %10s   %9s %9s %8s", "", "rows", "articles", "-rows", "-articles",
                "of raw")
    logger.info("  %s", "-" * 96)
    raw_art, prev = stages[0][2], None
    for label, rows, art in stages:
        d = ("", "") if prev is None else tuple(
            f"-{p - c:,}" if p != c else "0" for p, c in zip(prev, (rows, art)))
        logger.info("  %-44s %10s %10s   %9s %9s %7.1f%%", label, f"{rows:,}", f"{art:,}",
                    d[0], d[1], 100 * art / raw_art if raw_art else 0)
        prev = (rows, art)
    for note in notes:
        logger.info("  %s", note)


def log_by_facility(before, after):
    """Rows and articles per terminating facility, raw label vs cleaned label."""
    b = before.groupby("Terminating_facility_name", dropna=False)["Article_count"].agg(["size", "sum"])
    a = after.groupby("Terminating_facility_name", dropna=False)["parcel_count"].agg(["size", "sum"])
    t = b.join(a, how="outer", lsuffix="_raw", rsuffix="_clean").fillna(0).astype("int64")
    t = t.sort_values(["sum_clean", "sum_raw"], ascending=False)
    logger.info("")
    logger.info("=== BY TERMINATING FACILITY (raw label -> cleaned label) ===")
    logger.info("  %-32s %9s %9s   %9s %9s", "", "raw rows", "raw art", "rows", "articles")
    for k, r in t.iterrows():
        logger.info("  %-32s %9s %9s   %9s %9s", str(k)[:32], f"{r.size_raw:,}", f"{r.sum_raw:,}",
                    f"{r.size_clean:,}", f"{r.sum_clean:,}")
    logger.info("  %-32s %9s %9s   %9s %9s", "TOTAL", f"{t.size_raw.sum():,}",
                f"{t.sum_raw.sum():,}", f"{t.size_clean.sum():,}", f"{t.sum_clean.sum():,}")


def tag_postcode(lat, lon, postcodes):
    """POSTCODE per point (pd.NA when in none), queried once per DISTINCT coordinate.

    Returns (postcodes, n_distinct, n_outside, n_on_boundary).
    """
    codes, uniq = pd.factorize(pd.MultiIndex.from_arrays([lat, lon]))
    u_lat = np.fromiter((u[0] for u in uniq), float, len(uniq))
    u_lon = np.fromiter((u[1] for u in uniq), float, len(uniq))

    names = postcodes["POSTCODE"].to_numpy()
    tree = shapely.STRtree(postcodes["geometry"].to_numpy())
    hit_pt, hit_poly = tree.query(shapely.points(u_lon, u_lat), predicate="intersects")
    # a point on a boundary matches two polygons -- lowest index wins (assign in reverse)
    order = np.argsort(hit_poly, kind="mergesort")[::-1]
    u_pc = pd.array(np.full(len(uniq), pd.NA, dtype=object), dtype="Int64")
    u_pc[hit_pt[order]] = names[hit_poly[order]]
    n_multi = int((np.bincount(hit_pt, minlength=len(uniq)) > 1).sum())
    return u_pc[codes], len(uniq), int(u_pc.isna().sum()), n_multi


def build(raw_df, postcodes):
    """The cleaned catchment. Pure: no I/O, so it runs the same locally and on DataStar."""
    stages = []
    stage(stages, "0.  raw extract", raw_df)

    df = raw_df[raw_df["Del_Lat"].notna() & raw_df["Del_Long"].notna()]
    stage(stages, "1a. + has a delivery coordinate", df)
    df = df[~df["Terminating_facility_name"].isin(EXCLUDE_FACILITY_SET)]
    stage(stages, "1b. + not an excluded facility", df)
    df = df[df["Product_type"].isin(CLEAN_PRODUCT_SET)].reset_index(drop=True)
    stage(stages, "1c. + in-scope product", df)

    name = df["Terminating_facility_name"]
    holloway = name == "HOLLOWAY DR PARCEL OPERATIONS"
    name = name.mask(holloway, "BAYSWATER PDC")
    v03974 = (df["Contract_ID"] == "V03974") & (name == "OAKLEIGH SOUTH PDC")
    df["Terminating_facility_name"] = name.mask(v03974, "MULGRAVE PDC")
    stage(stages, "2.  remap facility aliases (renames only)", df)

    pc, n_uniq, n_out, n_multi = tag_postcode(
        df["Del_Lat"].to_numpy(float), df["Del_Long"].to_numpy(float), postcodes)
    df["POSTCODE"] = pc
    stage(stages, "3.  postcode tagged (left join, FINAL)", df)

    for fac, (lat, lon) in FACILITY_COORDS.items():
        at = df["Terminating_facility_name"] == fac
        df.loc[at, "Terminating_Lat"] = lat
        df.loc[at, "Terminating_Long"] = lon

    df = df.rename(columns={"Article_count": "parcel_count"})
    df["delivery_lat"] = df["Del_Lat"]
    df["delivery_lon"] = df["Del_Long"]
    df["wh_lat"] = df["Terminating_Lat"]
    df["wh_lon"] = df["Terminating_Long"]

    notes = [
        f"rule 2 renamed Holloway Dr -> Bayswater on {int(holloway.sum()):,} rows and "
        f"V03974 @ Oakleigh South -> Mulgrave on {int(v03974.sum()):,} rows",
        f"rule 3 tested {n_uniq:,} distinct coordinates; {n_out:,} in no postcode "
        f"({int(df['POSTCODE'].isna().sum()):,} rows kept with a blank POSTCODE), "
        f"{n_multi:,} on a boundary between two",
    ]
    unplaced = sorted(set(df["Terminating_facility_name"].dropna()) - set(FACILITY_COORDS))
    if unplaced:
        notes.append(f"rule 4: NO surveyed coordinate for {unplaced} -- extract's value kept")
    return df[OUTPUT_COLS], stages, notes


def main():
    from datastar import Project  # platform-only; build() above runs without it

    logger.info("Starting delivery catchment clean")
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()
    logger.info("Connected to sandbox for %s", PROJECT_NAME)

    raw_df = pd.read_csv(RAW_PATH, low_memory=False, usecols=READ_COLS)
    postcodes = pd.read_csv(POSTCODE_BOUNDARY)
    postcodes["POSTCODE"] = pd.to_numeric(postcodes["POSTCODE"], errors="coerce").astype("Int64")
    postcodes["geometry"] = shapely.from_wkt(postcodes["geometry"])
    logger.info("Read %s delivery rows and %d postcode polygons", f"{len(raw_df):,}", len(postcodes))

    out, stages, notes = build(raw_df, postcodes)
    log_ledger(stages, notes)
    log_by_facility(raw_df, out)

    logger.info("")
    logger.info("Writing %s rows to table %s", f"{len(out):,}", OUTPUT_TABLE)
    # the old table is dropped by the macro's Wip_C02_DropDeliveryCatchment task, which runs
    # BEFORE this script (the sandbox cannot execute DDL)
    sandbox.write_table(out, OUTPUT_TABLE)
    Path(OUTPUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_CSV, index=False)
    logger.info("Wrote %s", OUTPUT_CSV)
    logger.info("Done")


if __name__ == "__main__":
    main()
