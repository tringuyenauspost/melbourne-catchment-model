"""MACRO 3 — build chain 2, the delivery entity, and publish its 20 model tables.

    Wip_C02_Obs* (macro 2)  ->  obs_*.csv  ->  s2a_build_chain2.py  ->  Wip_C03_*

WHAT RUNS. `s2a_build_chain2.py` in this folder is a VERBATIM COPY of pipeline/s2a. It is not
imported for its functions -- it is a script, and running it IS the build. This file only puts
the files it reads where it expects them and publishes what it leaves behind.

WHY THE MEASUREMENT COMES BACK OUT AS CSV. s2a reads inputs/factors_observed/*.csv. On the
platform that measurement lives in macro 2's tables, so it is written back to disk here, as
text, byte-for-byte as the exporter wrote it. The alternative -- teaching s2a to read a
database -- would turn a copy into a fork.

NEEDS BESIDE IT: s2a_build_chain2.py, scan_reduction.py, s1a_export_chain2_factors.py,
_paths.py, _log.py, _report.py, table_bridge.py.
"""

import logging
import runpy
import sys

from datastar import Project

import scan_reduction as reduction
import table_bridge
from _paths import CHAIN2_OUT, FASS, FOBS, INPUTS, OUTPUTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
PREFIX = "Wip_C03_"
STEP = "s2a_build_chain2"
PATHS_TABLE = "Wip_C02_ConsignmentPaths"

# the exporter's file name -> the table macro 2 published it as
FACTOR_TABLES = {
    "obs_joint": "Wip_C02_ObsJoint",
    "obs_demand": "Wip_C02_ObsDemand",
    "obs_legs": "Wip_C02_ObsLegs",
    "obs_delivery": "Wip_C02_ObsDelivery",
    "obs_single_sort": "Wip_C02_ObsSingleSort",
    "obs_second_sort": "Wip_C02_ObsSecondSort",
    "obs_round2_sites": "Wip_C02_ObsRound2Sites",
    # not an obs_ file and easy to forget: s2a re-derives a ratio from it, so a missing
    # provenance table stops the build rather than changing a number quietly
    "_provenance": "Wip_C02_Provenance",
}


def main():
    logger.info("=== MACRO 3 — chain 2, the delivery entity ===")
    logger.info("  inputs   %s", INPUTS)
    logger.info("  outputs  %s", OUTPUTS)
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    logger.info("  the measurement, from macro 2's tables:")
    table_bridge.materialise(sandbox, FOBS, FACTOR_TABLES, logger)
    assert (FASS / "dials.csv").exists(), f"no assumed factors at {FASS} — check MELB_INPUTS"

    # THE DRIFT GUARD NEEDS THE REDUCTION, not just the factors. s2a re-derives the factor
    # tables from it and checks them against the CSVs above, which is the only thing that
    # catches a dial changed since the factors were exported -- FOLD_MIN_ARTICLES above all.
    # Locally it reads a pickle; here the reduction is a table, so it is handed over directly.
    paths = sandbox.read_sql(f'SELECT * FROM "{PATHS_TABLE}"')
    reduction.set_paths(paths)
    logger.info("  reduction handed to the drift guard: %s consignments from %s",
                f"{len(paths):,}", PATHS_TABLE)

    logger.info("")
    logger.info("  running %s.py (verbatim copy of the pipeline step)", STEP)
    runpy.run_module(STEP, run_name="__main__")

    logger.info("")
    logger.info("  publishing %s as %s*", CHAIN2_OUT.name, PREFIX)
    written = table_bridge.publish_folder(sandbox, CHAIN2_OUT, PREFIX, logger)
    logger.info("Done — %d tables.", len(written))


if __name__ == "__main__":
    main()
