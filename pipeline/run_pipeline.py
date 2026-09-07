"""Run the whole build, in order, in one command. Everything it runs is in this folder.

    s1  export the factors      the measurement
    s2  generate the tables     the two entities, then the combiner
    s3  post-process            the three patches that finish the model

Seven scripts, each its own process, each runnable on its own. This file only sequences them and
stops at the first failure — there is no build logic here, and nothing reads or writes a model
table.

    s1a  s1a_export_chain2_factors.py      2.96 M scan events -> inputs/factors_observed/  (7 tables)
    s2a  s2a_build_chain2.py        chain 2, delivery, observed   -> outputs/…_chain2_observed/
    s2b  s2b_build_chain1.py        chain 1, collection, assumed  -> outputs/…_chain1/
    s2c  s2c_build_final.py         the combiner                  -> outputs/…_final/
    s3a  s3a_split_despatch2.py     a Despatch2 per route
    s3b  s3b_no_relay.py            a site despatches only what it makes
    s3c  s3c_narrow_sort_band.py    round-2 pinned on the measured share

WHERE THE DATA IS. Nothing here counts parent directories: `_paths.py` resolves `inputs/` and
`outputs/` once, and every script asks it. Drop this folder beside them and it just works; set
`$MELB_DATA_ROOT` to put them anywhere else. `uv run python pipeline/_paths.py` prints what it
resolved and why.

WHY IT STOPS DEAD. s2a-s2c own their output folder and write it one table at a time, so a step
that dies halfway leaves a folder that LOOKS like a model and is half of two different builds. The
runner aborts on the first non-zero exit and says what that step left behind, rather than handing
the next step a folder it cannot tell is stale.

WHY s3 IS NOT OPTIONAL. The three patches edit outputs/melbourne_optilogic_final/ in place and s2c
overwrites them every time it runs, so a build that stops after s2c is not the model — it is the
model minus three rules the build has no way to write. The order matters: the split changes what
each site can produce and the no-relay rule is written from exactly that, so a no-relay run before
the split writes the wrong rows.

Run:  uv run python pipeline/run_pipeline.py                 the whole build
      uv run python pipeline/run_pipeline.py --rebuild       s1a re-reads the raw scan CSV first
      uv run python pipeline/run_pipeline.py --from s2c      the combiner onward
      uv run python pipeline/run_pipeline.py --only s3a s3b s3c   just re-apply the patches
      uv run python pipeline/run_pipeline.py --band 0.05     restore the original sort band
      uv run python pipeline/run_pipeline.py --dry-run       print the commands and stop
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

from _paths import DATA_ROOT, HOW, PIPELINE

# (id, script, what it does, the folder it owns)
STEPS = [
    ("s1a", "s1a_export_chain2_factors.py",
     "export the factors — 2.96 M scan events to the measurement", "inputs/factors_observed"),
    ("s2a", "s2a_build_chain2.py",
     "generate tables — chain 2, the delivery entity, observed",
     "outputs/melbourne_optilogic_chain2_observed"),
    ("s2b", "s2b_build_chain1.py",
     "generate tables — chain 1, the collection entity, assumed",
     "outputs/melbourne_optilogic_chain1"),
    ("s2c", "s2c_build_final.py",
     "generate tables — the combiner, the folder Cosmic Frog takes",
     "outputs/melbourne_optilogic_final"),
    ("s3a", "s3a_split_despatch2.py",
     "post-process — a Despatch2 per route", "outputs/melbourne_optilogic_final"),
    ("s3b", "s3b_no_relay.py",
     "post-process — a site despatches only what it makes", "outputs/melbourne_optilogic_final"),
    ("s3c", "s3c_narrow_sort_band.py",
     "post-process — round-2 pinned on the measured share", "outputs/melbourne_optilogic_final"),
]
IDS = [i for i, *_ in STEPS]
PATCHES = ("s3a", "s3b", "s3c")     # these EDIT the model folder; the others WRITE one


def argv_for(step_id, args):
    """The per-step flags. Only two steps take any, and both come off this runner's own args."""
    if step_id == "s1a" and args.rebuild:
        return ["--rebuild"]
    if step_id == "s3c":
        return ["--band", str(args.band)]
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog="steps: " + "  ".join(IDS),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true",
                    help="s1a re-reads the raw scan CSV instead of the cached reduction. Needed "
                         "when the extract changes; also needs geopandas.")
    ap.add_argument("--band", default="0",
                    help="s3c's round-2 band half-width in share points (default 0 = pinned on "
                         "the measured share; 0.05 restores the original band)")
    ap.add_argument("--from", dest="start", metavar="STEP",
                    help=f"start at this step and run to the end. One of: {' '.join(IDS)}")
    ap.add_argument("--only", nargs="+", metavar="STEP",
                    help="run just these steps, in the order given")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands and exit without running anything")
    args = ap.parse_args(argv)

    if args.only:
        bad = [s for s in args.only if s not in IDS]
        assert not bad, f"no such step: {bad} — the steps are {' '.join(IDS)}"
        plan = [s for want in args.only for s in STEPS if s[0] == want]
    elif args.start:
        assert args.start in IDS, f"no such step: {args.start} — the steps are {' '.join(IDS)}"
        plan = STEPS[IDS.index(args.start):]
    else:
        plan = list(STEPS)

    print(f"pipeline   {PIPELINE}")
    print(f"data root  {DATA_ROOT}      ({HOW})")
    print(f"plan       {' -> '.join(i for i, *_ in plan)}"
          + ("   (--rebuild: s1a re-reads the raw scans)" if args.rebuild else ""))
    planned = {i for i, *_ in plan}
    if (planned - set(PATCHES)) and not set(PATCHES) <= planned:
        print("  NOTE: this plan rebuilds the model folder without re-applying every patch. "
              "s2c drops all three, so finish with --only s3a s3b s3c.")

    t0 = time.time()
    for step_id, script, label, owns in plan:
        path = PIPELINE / script
        assert path.exists(), f"{step_id}: missing {script} — the pipeline folder is incomplete"
        cmd = [sys.executable, str(path), *argv_for(step_id, args)]
        print(f"\n{'=' * 92}\n  {step_id.upper()}  {label}"
              f"\n  $ python pipeline/{script} {' '.join(cmd[2:])}\n{'=' * 92}")
        if args.dry_run:
            continue
        t = time.time()
        rc = subprocess.call(cmd, cwd=DATA_ROOT)
        if rc != 0:
            print(f"\n  {step_id.upper()} FAILED (exit {rc}) — the build is STOPPED here.")
            # What a failure leaves behind depends on which half of the build it was in, and
            # saying "half-written" for a patch that refused to run is worse than saying nothing:
            # it sends you to re-run a rebuild you do not need.
            if step_id in PATCHES:
                prev = plan[plan.index((step_id, script, label, owns)) - 1][0]
                print(f"  The patches rewrite whole tables and refuse rather than half-apply, so")
                print(f"  {owns}/ is as {prev} left it. Read the step's own message above — the")
                print(f"  usual cause is running a patch onto an ALREADY-PATCHED folder, which is")
                print(f"  what `--only s3a s3b s3c` does unless s2c has just rebuilt the folder.")
                print(f"  To redo the patches from scratch on the model you have:")
                print(f"      uv run python pipeline/s3a_split_despatch2.py --restore")
                print(f"      uv run python pipeline/run_pipeline.py --only s3a s3b s3c")
                print(f"  Or rebuild and patch in one go:")
                print(f"      uv run python pipeline/run_pipeline.py --from s2c")
            else:
                print(f"  {owns}/ is now HALF-WRITTEN: it holds some tables from this run and the")
                print(f"  rest from the last one, and nothing downstream can tell the difference.")
                print(f"  Fix the failure and re-run from this step:")
                print(f"      uv run python pipeline/run_pipeline.py --from {step_id}"
                      + ("  --rebuild" if args.rebuild and step_id == "s1a" else ""))
            return rc
        print(f"  {step_id} ok ({time.time() - t:.1f}s)")

    if not args.dry_run:
        print(f"\n  all {len(plan)} step(s) ok in {time.time() - t0:.1f}s")
        if plan[-1][0] == "s3c":
            print(f"  upload  {DATA_ROOT / 'outputs' / 'melbourne_optilogic_final'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
