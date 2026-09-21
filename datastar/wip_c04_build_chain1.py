"""MACRO 4 — build chain 1, the collection entity, and publish its 17 model tables.

    Wip_C03_* (macro 3)  ->  chain2 CSVs  ->  s2b_build_chain1.py  ->  Wip_C04_*

CHAIN 1 IS NOT INDEPENDENT OF CHAIN 2. It reads chain 2's Facilities, SupplierCapabilities and
TransportationModes -- its sinks are read off chain 2's own supplier table rather than off the
raw measurement, because chain 2 folds small entry sites together and mirroring the measurement
would hand volume to buildings chain 2 has no supplier at. So macro 3 must have run.

Every chain-2 table is brought back, not just the three read today: which ones s2b reads is
s2b's business, and a copy that silently needed a fourth file would fail here instead.

NEEDS BESIDE IT: s2b_build_chain1.py, _paths.py, _log.py, _report.py, table_bridge.py.
"""

import logging
import runpy
import sys

from datastar import Project

import table_bridge
from _paths import CHAIN1_OUT, CHAIN2_OUT, FASS, INPUTS, OUTPUTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
CHAIN2_PREFIX = "Wip_C03_"
PREFIX = "Wip_C04_"
STEP = "s2b_build_chain1"

CHAIN2_TABLES = [
    "BillOfMaterials", "CustomerDemand", "CustomerFulfillmentPolicies", "Customers",
    "Facilities", "FlowConstraints", "Groups", "OriginMix", "Processes",
    "ProcurementPolicies", "ProductionPolicies", "Products", "ReplenishmentPolicies",
    "SupplierCapabilities", "Suppliers", "TransportationModes", "TransportationPolicies",
    "UserDefinedConstraints", "UserDefinedVariables", "WorkCenters",
]


def main():
    logger.info("=== MACRO 4 — chain 1, the collection entity ===")
    logger.info("  inputs   %s", INPUTS)
    logger.info("  outputs  %s", OUTPUTS)
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    logger.info("  chain 2, from macro 3's tables:")
    table_bridge.materialise(sandbox, CHAIN2_OUT,
                             table_bridge.prefix_map(CHAIN2_PREFIX, CHAIN2_TABLES), logger)
    assert (FASS / "dials_chain1.csv").exists(), f"no chain-1 dials at {FASS} — check MELB_INPUTS"

    logger.info("")
    logger.info("  running %s.py (verbatim copy of the pipeline step)", STEP)
    runpy.run_module(STEP, run_name="__main__")

    logger.info("")
    logger.info("  publishing %s as %s*", CHAIN1_OUT.name, PREFIX)
    written = table_bridge.publish_folder(sandbox, CHAIN1_OUT, PREFIX, logger)
    logger.info("Done — %d tables.", len(written))


if __name__ == "__main__":
    main()
