"""Move model tables between the sandbox and the CSV folders the build steps read and write.

THE BUILD STEPS ARE VERBATIM COPIES of pipeline/s2a, s2b, s2c and s3a-c. They read CSVs out of
`outputs/…` folders and write CSVs into them, and nothing here changes that: the wrappers
materialise a folder from tables before a step runs, and publish the folder as tables after.
That is what keeps the copies copies -- a step that knew about a database would be a fork.

EVERYTHING MOVES AS TEXT, in both directions. A model table is Anura input: every column is
text as far as Cosmic Frog is concerned, and a float column that round-trips through the
database comes back rendered by whatever wrote it -- `1` becoming `1.0`, `0.1` becoming
`0.10000000000000001`. Reading the CSV with `dtype=str` and publishing THAT means the CSV the
next step reads is byte-for-byte the CSV the local build produced. It also means SQL over these
tables needs a cast to add anything up, which is the price of the guarantee.

A NULL IS AN EMPTY CELL. `keep_default_na=False` on the way out and `fillna("")` on the way
back keep an empty CSV cell an empty string in both directions, rather than letting it become
NaN, then NULL, then the four characters `None` in somebody's CSV.
"""

import pandas as pd


def read_csv_text(path):
    """A model CSV, exactly as it sits on disk. utf-8-sig strips the BOM the build writes."""
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def write_csv_text(df, path):
    """Back to disk the way every build step writes: BOM, no index."""
    df.to_csv(path, index=False, encoding="utf-8-sig")


def prefix_map(prefix, stems):
    """{"Facilities": "Wip_C03_Facilities", …} for the steps whose tables share one prefix."""
    return {stem: f"{prefix}{stem}" for stem in stems}


def publish_folder(sandbox, folder, prefix, log=None, skip_underscore=True):
    """Every CSV in `folder` -> a table called <prefix><stem>. Returns what it wrote.

    Files starting with an underscore are the build's own working files -- `_sort_load.csv` is
    a diagnostic, not a model table -- so they stay on disk.
    """
    written = []
    for path in sorted(folder.glob("*.csv")):
        if skip_underscore and path.name.startswith("_"):
            continue
        df = read_csv_text(path)
        name = f"{prefix}{path.stem}"
        sandbox.write_table(df, name)
        written.append((name, len(df), len(df.columns)))
        if log:
            log.info("    %-38s %7s rows  %2d cols", name, f"{len(df):,}", len(df.columns))
    if not written:
        raise SystemExit(f"nothing to publish: {folder} holds no model CSV")
    return written


def materialise(sandbox, folder, mapping, log=None):
    """<table> -> <folder>/<stem>.csv, so a step that wants files finds the files it wants.

    The mapping is stem -> table name rather than a prefix, because the measurement tables are
    not named after their files (obs_joint.csv is Wip_C02_ObsJoint).
    """
    folder.mkdir(parents=True, exist_ok=True)
    got = []
    for stem, table in mapping.items():
        try:
            df = sandbox.read_sql(f'SELECT * FROM "{table}"')
        except Exception as exc:
            raise SystemExit(
                f"cannot read {table} ({type(exc).__name__}: {exc}). It is built by an earlier "
                f"macro — run that one first, or check the table name.")
        df = df.fillna("")                      # a NULL is an empty cell, not the word None
        write_csv_text(df, folder / f"{stem}.csv")
        got.append((table, len(df)))
        if log:
            log.info("    %-38s %7s rows  -> %s.csv", table, f"{len(df):,}", stem)
    return got
