"""MACRO 6 — cut the Network Plan scenarios (S0-S3) out of the finished model.

    Wip_C05_*  ->  s4a_build_scenarios.py  ->  Wip_C06_<S>_*      (Wip_C06_<S>C1_* with chain 1)

s4a READS the combined model and never edits it, so this macro only needs macro 5's tables on
disk, runs the step, and publishes one table set per scenario. Which scenarios exist is
scenarios.csv; whether chain 1 rides along is the CHAIN1 dial in dials_scenario.csv — the same
two files the local build reads, so the platform and the repo cannot disagree.

TABLE NAMES ARE SHORT AND FIXED. A scenario's folder is melbourne_scenario_S1_closure[_chain1];
its tables are Wip_C06_S1_* (or Wip_C06_S1C1_*), the code before the first underscore. The drop
task has to name its tables in advance, and a short fixed code keeps that list stable when a
scenario's description changes.

NEEDS BESIDE IT: s4a_build_scenarios.py, _paths.py, _log.py, _routes.py, table_bridge.py.
With a `route` basis it also reads the two routing runs' cluster_summary.csv +
temp_clustered.csv from Raw Inputs (melbourne/ and PICKUP_CLUSTERS), as macros 3 and 4 do.
"""

import logging
import runpy
import sys

import pandas as pd
from datastar import Project

import table_bridge
from _paths import FASS, FINAL_OUT, INPUTS, OUTPUTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
FINAL_PREFIX = "Wip_C05_"
PREFIX = "Wip_C06_"
STEP = "s4a_build_scenarios"

# what s4a reads out of the combined model (its TABLES, plus the modes it copies across)
FINAL_TABLES = [
    "Products", "Facilities", "Suppliers", "SupplierCapabilities", "Customers",
    "CustomerDemand", "CustomerFulfillmentPolicies", "ProcurementPolicies",
    "TransportationPolicies", "Groups", "FlowConstraints", "TransportationModes",
]


def main():
    logger.info("=== MACRO 6 — the Network Plan scenarios ===")
    logger.info("  inputs   %s", INPUTS)
    logger.info("  outputs  %s", OUTPUTS)
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    logger.info("  the combined model, from macro 5's tables:")
    table_bridge.materialise(sandbox, FINAL_OUT,
                             table_bridge.prefix_map(FINAL_PREFIX, FINAL_TABLES), logger)
    for f in ("scenarios.csv", "dials_scenario.csv", "dials_chain1.csv"):
        assert (FASS / f).exists(), f"no {f} at {FASS} — check MELB_INPUTS"

    logger.info("")
    logger.info("  running %s.py", STEP)
    # run_name is NOT "__main__": main() is called with an empty argv so the platform's own
    # sys.argv never reaches argparse — every setting comes from the two CSVs
    runpy.run_module(STEP, run_name="not_main")["main"]([])

    dials = pd.read_csv(FASS / "dials_scenario.csv").set_index("parameter").value
    chain1 = bool(int(dials["CHAIN1"]))
    total = 0
    for name in pd.read_csv(FASS / "scenarios.csv").scenario:
        folder = OUTPUTS / f"melbourne_scenario_{name}{'_chain1' if chain1 else ''}"
        prefix = f"{PREFIX}{name.split('_')[0]}{'C1' if chain1 else ''}_"
        logger.info("")
        logger.info("  publishing %s as %s*", folder.name, prefix)
        total += len(table_bridge.publish_folder(sandbox, folder, prefix, logger))
    logger.info("Done — %d tables. Each scenario is a model Cosmic Frog takes on its own.", total)


if __name__ == "__main__":
    main()
