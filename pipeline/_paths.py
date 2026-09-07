"""The one place that decides where `inputs/` and `outputs/` are.

Every script in this folder asks here instead of counting parent directories, so the folder can be
dragged anywhere — an Optilogic DataStar workspace, a container, a checkout with a different shape
— and still find its data. Nothing else in `pipeline/` touches `__file__`.

THE SEARCH, in order:

    1. $MELB_DATA_ROOT              set it and nothing is guessed
    2. the nearest directory at or above pipeline/'s parent that CONTAINS an `inputs/` folder
    3. pipeline/'s parent, so the failure names a real path instead of a guessed one

Rung 2 is what makes the drag-and-drop work. Drop `pipeline/` beside `inputs/` and `outputs/` and
it resolves on the first try; drop it three levels down inside a workspace and it walks up until
it finds the data. It looks for `inputs/` rather than for a marker file because `inputs/` is the
thing the build actually needs — a marker can be present in a tree that has no data in it.

    from _paths import INPUTS, OUTPUTS, DATA_ROOT

Run this file directly to see what it resolved and why:

    uv run python pipeline/_paths.py
"""
import os
from pathlib import Path

_ENV = "MELB_DATA_ROOT"
PIPELINE = Path(__file__).resolve().parent


def _resolve():
    """Return (root, how it was found). Never raises — a bad root fails at the first read."""
    env = os.environ.get(_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve(), f"${_ENV}"
    for cand in [PIPELINE.parent, *PIPELINE.parent.parents]:
        if (cand / "inputs").is_dir():
            return cand, f"found inputs/ at {cand}"
    return PIPELINE.parent, "fallback: the folder above pipeline/ (no inputs/ found)"


DATA_ROOT, HOW = _resolve()
INPUTS = DATA_ROOT / "inputs"
OUTPUTS = DATA_ROOT / "outputs"

# The named folders, so a rename happens once here rather than in seven scripts.
RAW = INPUTS / "melbourne"                      # the raw extract and the zone table
REF = INPUTS / "optilogic"                      # an Anura reference export — COLUMN SCHEMAS
FASS = INPUTS / "factors_assumed"               # hand-managed assumptions
FOBS = INPUTS / "factors_observed"              # written by s1a — never hand-edit
SCAN_OUT = OUTPUTS / "melbourne_scan_path_analysis"    # the reduction and its cache
CHAIN2_OUT = OUTPUTS / "melbourne_optilogic_chain2_observed"
CHAIN1_OUT = OUTPUTS / "melbourne_optilogic_chain1"
CHAIN1_SRC = OUTPUTS / "melbourne_optilogic_chain2_scan_constrained"   # FROZEN, 11 Aug
FINAL_OUT = OUTPUTS / "melbourne_optilogic_final"      # the folder Cosmic Frog takes
PRESPLIT = OUTPUTS / ".presplit"                # s3a's undo copies


def require(path, what, fix):
    """Assert a path exists, and say what to do about it rather than just naming it."""
    assert Path(path).exists(), (
        f"missing {what}: {path}\n"
        f"  {fix}\n"
        f"  (data root {DATA_ROOT} — {HOW}; set ${_ENV} to point somewhere else)")
    return Path(path)


if __name__ == "__main__":
    print(f"pipeline   {PIPELINE}")
    print(f"data root  {DATA_ROOT}      ({HOW})")
    for name, p in (("inputs", INPUTS), ("outputs", OUTPUTS), ("factors_assumed", FASS),
                    ("factors_observed", FOBS), ("melbourne (raw)", RAW),
                    ("optilogic (schemas)", REF), ("chain-1 source", CHAIN1_SRC)):
        print(f"  {'OK ' if p.exists() else '-- '} {name:<22} {p}")
