"""PART 2 of 2 — export the observed chain-2 factors as DataStar tables.

Calls `scan_reduction.main()` — the same entry point the local build runs, in a standalone
copy that imports nothing from pipeline/ — and publishes the CSVs it wrote. Every fold dial,
cohort rule and share denominator is the one the model already reads, so the output can be
diffed against inputs/factors_observed/ row for row.

PART 1 must run first: this reads the reduction table it published, so 3M scan events are
not reduced a second time.

Needs `scan_reduction.py` beside it.

Input  : Wip_C02_ConsignmentPaths  (read from the sandbox)
Outputs: Wip_C02_ObsJoint, ObsDemand, ObsLegs, ObsDelivery, ObsSingleSort,
         ObsSecondSort, ObsRound2Sites, Provenance
"""

import logging
import sys

import pandas as pd

from datastar import Project

import scan_reduction as reduction

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
PATHS_TABLE = "Wip_C02_ConsignmentPaths"

# obs_*.csv -> DataStar table name. Anything the export writes that is not listed is still
# published, under a name derived the same way, so a new factor file cannot go missing.
TABLE_NAME = {
    "obs_joint": "Wip_C02_ObsJoint",
    "obs_demand": "Wip_C02_ObsDemand",
    "obs_legs": "Wip_C02_ObsLegs",
    "obs_delivery": "Wip_C02_ObsDelivery",
    "obs_single_sort": "Wip_C02_ObsSingleSort",
    "obs_second_sort": "Wip_C02_ObsSecondSort",
    "obs_round2_sites": "Wip_C02_ObsRound2Sites",
    # NOT an obs_ file, and published because macro 3 needs it: s2a re-derives a ratio from
    # _provenance.csv, so leaving it behind would change a number in chain 2 rather than fail.
    "obs_path": "Wip_C02_ObsPath",
    "_provenance": "Wip_C02_Provenance",
}


def table_name(stem):
    return TABLE_NAME.get(stem, "Wip_C02_" + "".join(w.title() for w in stem.split("_")[1:]))


def main():
    logger.info("Starting observed-factor export (PART 2)")
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    # Double quotes preserve the table's exact case in the Starburst sandbox.
    paths = sandbox.read_sql(f'SELECT * FROM "{PATHS_TABLE}"')
    logger.info("Read %s consignments from %s", f"{len(paths):,}", PATHS_TABLE)

    reduction.set_paths(paths)

    # The real export, unchanged: main() calls load_paths(), which now returns the table.
    reduction.main([])

    written = sorted(reduction.FOBS.glob("obs_*.csv"))
    if not written:
        raise SystemExit(f"reduction.main() wrote no obs_*.csv into {reduction.FOBS}")
    prov = reduction.FOBS / "_provenance.csv"
    if not prov.exists():
        raise SystemExit(f"the exporter wrote no _provenance.csv into {reduction.FOBS}; "
                         f"chain 2 re-derives a ratio from it")
    written.append(prov)

    logger.info("")
    logger.info("=== PUBLISHING %d FACTOR TABLES ===", len(written))
    for path in written:
        df = pd.read_csv(path)
        name = table_name(path.stem)
        # The old table is cleared by the macro's "run SQL" task, which runs BEFORE this
        # script: the sandbox object has no way to execute SQL (read_table / write_table only).
        sandbox.write_table(df, name)
        logger.info("  %-24s -> %-28s %4d rows  %s",
                    path.name, name, len(df), ", ".join(df.columns))
        if "articles" in df.columns:
            logger.info("  %-24s    %s articles", "", f"{int(df['articles'].sum()):,}")
    logger.info("Done")


if __name__ == "__main__":
    main()
