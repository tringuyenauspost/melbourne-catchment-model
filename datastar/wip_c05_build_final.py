"""MACRO 5 — combine the two entities, apply the three patches, publish the model.

    Wip_C03_* + Wip_C04_*  ->  s2c_build_final.py  ->  s3a -> s3b -> s3c  ->  Wip_C05_*

THE PATCHES ARE PART OF THE BUILD, NOT AN EXTRA. s2c writes the combined folder and the three
s3 steps edit it in place; s2c overwrites their work every time it runs, so a build that stops
after s2c is the model minus three rules the combiner has no way to write. The order is fixed:
the despatch split changes what each site can produce, and the no-relay rule is written from
exactly that, so running no-relay first writes the wrong rows.

NEEDS BESIDE IT: s2c_build_final.py, s3a_split_despatch2.py, s3b_no_relay.py,
s3c_narrow_sort_band.py, _paths.py, _log.py, _report.py, table_bridge.py.
"""

import logging
import runpy
import sys

from datastar import Project

import table_bridge
from _paths import CHAIN1_OUT, CHAIN2_OUT, FASS, FINAL_OUT, INPUTS, OUTPUTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"
CHAIN2_PREFIX, CHAIN1_PREFIX = "Wip_C03_", "Wip_C04_"
PREFIX = "Wip_C05_"
COMBINER = "s2c_build_final"

# THE BAND IS NOT s3c's OWN DEFAULT. Run on its own, s3c bands round-2 at +/-1%; the pipeline
# runner passes --band 0, which PINS it on the measured share, and that is the build. Taking
# the script's default instead moves 88 FlowConstraints rows and nothing says so, which is
# precisely what this constant is here to prevent.
SORT_BAND = "0"                   # 0 = pinned on the measured share; 0.05 = the original band

# (module, argv) -- s3b takes no arguments; the other two are argparse scripts
PATCHES = [("s3a_split_despatch2", []),
           ("s3b_no_relay", None),
           ("s3c_narrow_sort_band", ["--band", SORT_BAND])]

CHAIN2_TABLES = [
    "BillOfMaterials", "CustomerDemand", "CustomerFulfillmentPolicies", "Customers",
    "Facilities", "FlowConstraints", "Groups", "OriginMix", "Processes",
    "ProcurementPolicies", "ProductionPolicies", "Products", "ReplenishmentPolicies",
    "SupplierCapabilities", "Suppliers", "TransportationModes", "TransportationPolicies",
    "UserDefinedConstraints", "UserDefinedVariables", "WorkCenters",
]
# chain 1 has no OriginMix, UserDefinedConstraints or UserDefinedVariables of its own
CHAIN1_TABLES = [t for t in CHAIN2_TABLES
                 if t not in ("OriginMix", "UserDefinedConstraints", "UserDefinedVariables")]


def main():
    logger.info("=== MACRO 5 — the combined model ===")
    logger.info("  inputs   %s", INPUTS)
    logger.info("  outputs  %s", OUTPUTS)
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()

    logger.info("  chain 2, from macro 3's tables:")
    table_bridge.materialise(sandbox, CHAIN2_OUT,
                             table_bridge.prefix_map(CHAIN2_PREFIX, CHAIN2_TABLES), logger)
    logger.info("  chain 1, from macro 4's tables:")
    table_bridge.materialise(sandbox, CHAIN1_OUT,
                             table_bridge.prefix_map(CHAIN1_PREFIX, CHAIN1_TABLES), logger)
    assert (FASS / "dials.csv").exists(), f"no assumed factors at {FASS} — check MELB_INPUTS"

    logger.info("")
    logger.info("  running %s.py (the combiner)", COMBINER)
    runpy.run_module(COMBINER, run_name="__main__")

    for patch, argv in PATCHES:
        logger.info("")
        logger.info("  running %s.py%s (patch, edits the combined folder in place)",
                    patch, f" {' '.join(argv)}" if argv else "")
        # run_name is NOT "__main__": these three are argparse scripts, and their main() is
        # called here with the arguments the pipeline runner passes, not sys.argv
        mod = runpy.run_module(patch, run_name="not_main")
        mod["main"]() if argv is None else mod["main"](argv)

    logger.info("")
    logger.info("  publishing %s as %s*", FINAL_OUT.name, PREFIX)
    written = table_bridge.publish_folder(sandbox, FINAL_OUT, PREFIX, logger)
    logger.info("Done — %d tables. This is the model Cosmic Frog takes.", len(written))


if __name__ == "__main__":
    main()
