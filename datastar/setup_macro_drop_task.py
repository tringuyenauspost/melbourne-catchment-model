"""ONE-OFF SETUP — give each script in macro_2 its own "drop my tables first" SQL task.

Run this once from DataStar. It changes the macro, not the data: it inserts three Run SQL
tasks, each immediately BEFORE the script that rebuilds the tables it drops.

    Start -> [Drop ScanClean tables]    -> 1_Wip_C02_ScanClean
          -> [Drop ScanAnalysis tables] -> 2_Wip_C02_ScanAnalysis
          -> [Drop ObsFactors tables]   -> 3_Wip_C02_ObsFactors

ONE TASK PER SCRIPT, not one for the macro, so a partial re-run stays honest: re-running the
analysis step drops the sixteen tables that step rebuilds and leaves ScanClean and the factor
tables alone.

HOW TO RE-RUN ONE STEP. Start the run at that step's DROP task, not at the script -- the drop
is upstream of the script, so starting at the script skips it and you are back to writing into
a table that still holds the old rows.

WHY A TASK AND NOT A LINE OF PYTHON. The sandbox object the scripts hold can read and write
tables (`read_table` / `write_table`, plus an undocumented `read_sql`) but cannot execute SQL.
DDL on this platform is a macro task, so the DROP cannot live inside 1_/2_/3_*.py at all.

HOW A TASK IS INSERTED IN THE MIDDLE. `macro.get_tasks()` returns NAMES (strings), and
`macro.get_task(name)` returns the object, which carries `get_dependencies()`,
`add_dependency(name)` and `remove_dependency(name)`. So inserting D between P and S is:
give D the dependencies S had, then swap S's dependency from P to D.

RE-RUNNABLE. If a drop task is already in place it is unwound first -- the script it feeds is
reconnected to what came before it -- and then rebuilt. Running this twice leaves three tasks,
not six.

IT DOES NOT RUN THE MACRO. Nothing is dropped until macro_2 (or one of its steps) next runs.
"""

import logging
import sys

from datastar import Project

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

PROJECT_NAME = "Temp_FY26_Melbourne"

# which macro to wire, as the first argument: `python setup_macro_drop_task.py macro_3`.
# Macros 3-5 hold one build step each, so they get one drop task each.
MACRO_NAME = sys.argv[1] if len(sys.argv) > 1 else "macro_2"

# key -> the tables that script rebuilds. The key also MATCHES THE SCRIPT'S TASK NAME
# (case-insensitive substring), which is how a drop is paired with the script it belongs to.
# If a key matches no task, or more than one, the script stops and prints the task list rather
# than guessing -- put the exact name in TASK_FOR below and run it again.
# Wip_C02_ScanClean_AI is deliberately absent: the AI agent's table is not ours to drop.
TABLES_BY_MACRO = {
  "macro_2": {
    "ScanClean": [
        "Wip_C02_ScanClean",
    ],
    "ScanAnalysis": [
        "Wip_C02_ConsignmentPaths",
        "Wip_C02_ReductionLedger",
        "Wip_C02_EventDistribution",
        "Wip_C02_ProductByOrigin",
        "Wip_C02_ProductByType",
        "Wip_C02_ProductByPdc",
        "Wip_C02_FacilityTouches",
        "Wip_C02_FacilityTouchesByProduct",
        "Wip_C02_FacilityTouchesByProductPct",
        "Wip_C02_FacilityCensus",
        "Wip_C02_FacilityAliases",
        "Wip_C02_FacilityByBuilding",
        "Wip_C02_SortationCounts",
        "Wip_C02_KeptByProduct",
        "Wip_C02_KeptByPdc",
        "Wip_C02_LaneThresholds",
    ],
    "ObsFactors": [
        "Wip_C02_ObsJoint",
        "Wip_C02_ObsDemand",
        "Wip_C02_ObsLegs",
        "Wip_C02_ObsDelivery",
        "Wip_C02_ObsSingleSort",
        "Wip_C02_ObsSecondSort",
        "Wip_C02_ObsRound2Sites",
        "Wip_C02_ObsPath",
        "Wip_C02_Provenance",
    ],
  },
  # One build step per macro, so one drop task each. The table names are the Anura table
  # names prefixed per entity -- all three steps write a Facilities table, and they are three
  # different tables.
  "macro_3": {
    "Chain2": [
        "Wip_C03_BillOfMaterials",
        "Wip_C03_CustomerDemand",
        "Wip_C03_CustomerFulfillmentPolicies",
        "Wip_C03_Customers",
        "Wip_C03_Facilities",
        "Wip_C03_FlowConstraints",
        "Wip_C03_Groups",
        "Wip_C03_OriginMix",
        "Wip_C03_Processes",
        "Wip_C03_ProcurementPolicies",
        "Wip_C03_ProductionPolicies",
        "Wip_C03_Products",
        "Wip_C03_ReplenishmentPolicies",
        "Wip_C03_SupplierCapabilities",
        "Wip_C03_Suppliers",
        "Wip_C03_TransportationModes",
        "Wip_C03_TransportationPolicies",
        "Wip_C03_UserDefinedConstraints",
        "Wip_C03_UserDefinedVariables",
        "Wip_C03_WorkCenters",
    ],
  },
  "macro_4": {
    "Chain1": [
        "Wip_C04_BillOfMaterials",
        "Wip_C04_CustomerDemand",
        "Wip_C04_CustomerFulfillmentPolicies",
        "Wip_C04_Customers",
        "Wip_C04_Facilities",
        "Wip_C04_FlowConstraints",
        "Wip_C04_Groups",
        "Wip_C04_Processes",
        "Wip_C04_ProcurementPolicies",
        "Wip_C04_ProductionPolicies",
        "Wip_C04_Products",
        "Wip_C04_ReplenishmentPolicies",
        "Wip_C04_SupplierCapabilities",
        "Wip_C04_Suppliers",
        "Wip_C04_TransportationModes",
        "Wip_C04_TransportationPolicies",
        "Wip_C04_WorkCenters",
    ],
  },
  "macro_5": {
    "Final": [
        "Wip_C05_BillOfMaterials",
        "Wip_C05_CustomerDemand",
        "Wip_C05_CustomerFulfillmentPolicies",
        "Wip_C05_Customers",
        "Wip_C05_Facilities",
        "Wip_C05_FlowConstraints",
        "Wip_C05_Groups",
        "Wip_C05_OriginMix",
        "Wip_C05_Processes",
        "Wip_C05_ProcurementPolicies",
        "Wip_C05_ProductionPolicies",
        "Wip_C05_Products",
        "Wip_C05_ReplenishmentPolicies",
        "Wip_C05_SupplierCapabilities",
        "Wip_C05_Suppliers",
        "Wip_C05_TransportationModes",
        "Wip_C05_TransportationPolicies",
        "Wip_C05_UserDefinedConstraints",
        "Wip_C05_UserDefinedVariables",
        "Wip_C05_WorkCenters",
    ],
  },
}

if MACRO_NAME not in TABLES_BY_MACRO:
    raise SystemExit(f"no table list for {MACRO_NAME!r}; known: {list(TABLES_BY_MACRO)}")
TABLES_BY_SCRIPT = TABLES_BY_MACRO[MACRO_NAME]

# key -> the spellings to look for in a task name, tried in order until exactly one task
# matches. "ObservedFactors" is here because a task may be named Wip_C02_ObservedFactors,
# which does not contain "ObsFactors" at all.
MATCH_KEYS = {
    "ScanClean": ("ScanClean",),
    "ScanAnalysis": ("ScanAnalysis", "Analysis"),
    "ObsFactors": ("ObsFactors", "ObservedFactors", "Factors"),
    # macros 3-5 do not exist yet, so their task names are not known. These are the spellings
    # worth trying; if none matches exactly one task the script stops and prints the macro's
    # task list, and the answer goes in TASK_FOR.
    "Chain2": ("C03", "Chain2", "chain2"),
    "Chain1": ("C04", "Chain1", "chain1"),
    "Final": ("C05", "Final", "final"),
}

# key -> the script task's EXACT name. Taken from the macro itself (2026-09-21), which holds:
#   Start, Wip_C02_ScanClean, Wip_C02_ScanClean_AI, Wip_C02_ScanAnalysis,
#   Wip_C02_ObservedFactors, Wip_C02_DropExistTables
# ScanClean HAS to be pinned: "ScanClean" matches the AI task too, and picking between them
# is not something a substring rule should decide.
TASK_FOR = {
    "macro_2": {
        "ScanClean": "Wip_C02_ScanClean",
        "ScanAnalysis": "Wip_C02_ScanAnalysis",
        "ObsFactors": "Wip_C02_ObservedFactors",
    },
}.get(MACRO_NAME, {})

# Not ours, and left strictly alone: Wip_C02_ScanClean_AI is the AI agent's build of the
# clean table, and Wip_C02_DropExistTables is a drop task someone else made. The second one
# is reported below, because two things dropping the same tables is worth seeing.
NOT_OURS = ("Wip_C02_ScanClean_AI", "Wip_C02_DropExistTables")

# Tasks an EARLIER version of this script created, removed on sight. The first version made
# one drop task for the whole macro and could not chain it, so it is sitting in macro_2
# unconnected, doing nothing.
LEGACY_TASKS = ["Drop Wip_C02 tables", "Drop ScanClean tables",
                "Drop ScanAnalysis tables", "Drop ObsFactors tables"]

# The drop task is named for the macro it lives in, not for macro_2: a drop task in macro_4
# called Wip_C02_DropChain1 would read as somebody else's.
TASK_PREFIX = {"macro_2": "Wip_C02_", "macro_3": "Wip_C03_",
               "macro_4": "Wip_C04_", "macro_5": "Wip_C05_"}.get(MACRO_NAME, "Wip_")


def DROP_TASK(key):
    """The name this script gives its own tasks -- the macro's prefix, then Drop<key>."""
    return f"{TASK_PREFIX}Drop{key}"

# One statement per table. If a task refuses more than one statement, switch to
# "one_statement": Postgres takes a comma-separated list in a single DROP. Both quote the
# names -- unquoted, the mixed case folds to lowercase and the drop misses the table.
QUERY_STYLE = "statements"        # "statements" | "one_statement"

SHOW_API = True                   # print one task object's real API, so nothing is guessed


def build_query(key):
    tables = TABLES_BY_SCRIPT[key]
    if QUERY_STYLE == "one_statement":
        return "DROP TABLE IF EXISTS " + ", ".join(f'"{t}"' for t in tables) + ";"
    return "\n".join(f'DROP TABLE IF EXISTS "{t}";' for t in tables)


def dep_name(d):
    """get_dependencies() may hand back names or objects; both reduce to a name."""
    return getattr(d, "name", d)


def deps_of(task):
    try:
        return [dep_name(d) for d in (task.get_dependencies() or [])]
    except Exception as exc:
        logger.warning("    get_dependencies() failed (%s: %s)", type(exc).__name__, exc)
        return []


def task_names(macro):
    """macro.get_tasks() returns NAMES, not objects -- print them as the strings they are."""
    return [dep_name(t) for t in (macro.get_tasks() or [])]


def show(macro, when):
    names = task_names(macro)
    logger.info("  tasks in %s %s (%d):", MACRO_NAME, when, len(names))
    for i, n in enumerate(names, 1):
        try:
            d = deps_of(macro.get_task(n))
        except Exception:
            d = ["?"]
        logger.info("    %d. %-28s after: %-28s %s", i, n, ", ".join(d) or "(nothing)",
                    "" if n not in NOT_OURS else "<- not ours, untouched")
        if n in NOT_OURS:
            q = getattr(macro.get_task(n), "query", None)
            for line in str(q).splitlines()[:6] if q else []:
                logger.info("         | %s", line)
    return names


def match_script(key, names):
    """The task that runs this script. Never a drop task, and never a guess between two."""
    if key in TASK_FOR:
        want = TASK_FOR[key]
        if want not in names:
            raise SystemExit(f"TASK_FOR[{key!r}] = {want!r} is not a task in {MACRO_NAME}: {names}")
        return want
    ours = {DROP_TASK(key=k) for k in TABLES_BY_SCRIPT}
    tried = {}
    for spelling in MATCH_KEYS.get(key, (key,)):
        hits = [n for n in names if spelling.lower() in n.lower() and n not in ours]
        tried[spelling] = hits
        if len(hits) == 1:
            if spelling != key:
                logger.info("    matched %r on %r", hits[0], spelling)
            return hits[0]
    raise SystemExit(
        f"cannot tell which task runs {key}. Tried " +
        "; ".join(f"{k!r} -> {v or 'nothing'}" for k, v in tried.items()) +
        f". The macro holds: {names}. Put the exact name in TASK_FOR[{key!r}] and run again.")


def unwind(macro, drop_name, script_name):
    """Take an existing drop task back out, reconnecting the script to what fed the drop."""
    drop, script = macro.get_task(drop_name), macro.get_task(script_name)
    before = deps_of(drop)
    if drop_name in deps_of(script):
        script.remove_dependency(drop_name)
        for p in before:
            script.add_dependency(p)
        script.save()
    try:
        macro.delete_task(drop_name)
    except Exception:
        drop.delete()
    logger.info("    removed the previous %r (%s was fed by %s)", drop_name, script_name,
                ", ".join(before) or "nothing")
    return before


def insert_before(macro, sandbox, key, script_name):
    """Put a fresh drop task between `script_name` and whatever currently feeds it."""
    drop_name = DROP_TASK(key=key)
    script = macro.get_task(script_name)
    feeds = deps_of(script)                       # what the script runs after, today

    drop = macro.add_run_sql_task(name=drop_name, connection=sandbox,
                                  query=build_query(key), auto_join=False)
    for p in feeds:
        drop.add_dependency(p)
    drop.save()

    for p in feeds:
        script.remove_dependency(p)
    script.add_dependency(drop_name)
    script.save()

    # sit it just left of the script it feeds, so the canvas reads the way it runs
    try:
        drop.x = (getattr(script, "x", 250) or 250) - 150
        drop.y = getattr(script, "y", 150) or 150
        drop.save()
    except Exception as exc:
        logger.warning("    could not position it (%s: %s)", type(exc).__name__, exc)
    return drop_name, feeds


def drop_legacy(macro):
    """Delete the tasks an earlier version of this script left behind, links and all."""
    names = task_names(macro)
    for stale in LEGACY_TASKS:
        if stale not in names:
            continue
        for n in names:                      # unwire anything pointing at it first
            if n == stale:
                continue
            t = macro.get_task(n)
            if stale in deps_of(t):
                for p in deps_of(macro.get_task(stale)):
                    t.add_dependency(p)
                t.remove_dependency(stale)
                t.save()
                logger.info("    %r no longer waits on the old %r", n, stale)
        try:
            macro.delete_task(stale)
        except Exception:
            macro.get_task(stale).delete()
        logger.info("    deleted the old %r left by the first version of this script", stale)


def probe(task):
    """One line of ground truth about what a task object actually offers."""
    api = sorted(a for a in dir(task) if not a.startswith("_"))
    logger.info("  task object is %s; public API: %s", type(task).__name__, ", ".join(api))


def main():
    logger.info("=== SETUP: one drop task per script in %s ===", MACRO_NAME)
    project = Project.connect_to(PROJECT_NAME)
    sandbox = project.get_sandbox()
    macro = project.get_macro(MACRO_NAME)
    logger.info("Connected to %s / %s", PROJECT_NAME, MACRO_NAME)

    names = show(macro, "before")
    if SHOW_API and names:
        probe(macro.get_task(names[0]))
    drop_legacy(macro)
    names = task_names(macro)
    clash = [n for n in names if "drop" in n.lower() and n not in NOT_OURS
             and n not in {DROP_TASK(key=k) for k in TABLES_BY_SCRIPT}]
    if "Wip_C02_DropExistTables" in names:
        logger.warning("")
        logger.warning("  NOTE: Wip_C02_DropExistTables already exists and is left alone. If it")
        logger.warning("  drops the same tables, you now have two things doing it — keep whichever")
        logger.warning("  you prefer and delete the other; they do not conflict, they just repeat.")
    for n in clash:
        logger.warning("  NOTE: %r also looks like a drop task; left alone.", n)

    # resolve every script task BEFORE changing anything, so an unmatched key stops the run
    # while the macro is still untouched
    pairs = [(key, match_script(key, names)) for key in TABLES_BY_SCRIPT]
    logger.info("")
    for key, script_name in pairs:
        logger.info("  %-14s -> %-42s %2d tables", key, script_name, len(TABLES_BY_SCRIPT[key]))

    for key, script_name in pairs:
        drop_name = DROP_TASK(key=key)
        logger.info("")
        logger.info("  %s", drop_name)
        if drop_name in task_names(macro):
            unwind(macro, drop_name, script_name)
        created, feeds = insert_before(macro, sandbox, key, script_name)
        logger.info("    inserted %r between [%s] and %r", created,
                    ", ".join(feeds) or "nothing", script_name)
        for line in build_query(key).splitlines()[:2]:
            logger.info("      %s", line)
        if len(TABLES_BY_SCRIPT[key]) > 2:
            logger.info("      ... %d more", len(TABLES_BY_SCRIPT[key]) - 2)

    logger.info("")
    show(macro, "after")
    logger.info("")
    logger.info("Done. Nothing dropped yet. To rebuild one step, START THE RUN AT ITS DROP")
    logger.info("TASK — starting at the script itself skips the drop.")


if __name__ == "__main__":
    main()
