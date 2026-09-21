"""The one place that decides where `inputs/` and `outputs/` are — the DataStar edition.

FORK of pipeline/_paths.py, and the ONLY file of the seven build steps that is not a verbatim
copy. Two deliberate differences, both because the platform's folders are not the repo's:

  1. THE INPUT FOLDER IS CALLED `Raw Inputs` HERE, not `inputs`. The repo's rung 2 walks up
     looking for a directory named `inputs/` and would walk past the platform's data. This
     version looks for either, and `$MELB_INPUTS` names it outright.
  2. OUTPUTS ARE SEPARATE FROM INPUTS. In the repo both hang off one root, which assumes the
     root is writable. `Raw Inputs` may not be, so `$MELB_OUTPUTS` puts the build's own
     folders somewhere that is -- a scratch directory inside the task, by default, since the
     wrappers publish TABLES and treat the CSV folders as working files.

Everything else -- the names of the folders, the search order, the module's interface -- is the
original, so the six build steps that import it are unmodified copies.

    from _paths import INPUTS, OUTPUTS, DATA_ROOT
"""
import os
import tempfile
from pathlib import Path

from _log import get_logger      # every message in the build goes through here

log = get_logger(__file__)
_ENV = "MELB_DATA_ROOT"
PIPELINE = Path(__file__).resolve().parent


def _resolve():
    """Return (root, how it was found). Never raises -- a bad root fails at the first read."""
    env = os.environ.get(_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve(), f"${_ENV}"
    for cand in [PIPELINE.parent, *PIPELINE.parent.parents]:
        if (cand / "Raw Inputs").is_dir() or (cand / "inputs").is_dir():
            return cand, f"found the input folder at {cand}"
    return PIPELINE.parent, "fallback: the folder above this one (no input folder found)"


def _inputs_dir(root):
    """DataStar's upload folder is `Raw Inputs`; a repo checkout calls it `inputs`.

    Whichever exists wins, so the same file runs on the platform and against a local checkout
    without editing a path. `$MELB_INPUTS` overrides both. Same rule, same env var, as
    scan_reduction.py -- the two must agree about where the data is.
    """
    env = os.environ.get("MELB_INPUTS", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    for name in ("Raw Inputs", "inputs"):
        if (root / name).is_dir():
            return root / name
    return root / "Raw Inputs"


def _outputs_dir(root):
    """Where the build writes. `$MELB_OUTPUTS`, else `outputs/` beside the inputs if that root
    is writable, else a scratch directory -- the tables are the deliverable, not these files."""
    env = os.environ.get("MELB_OUTPUTS", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if os.access(root, os.W_OK):
        return root / "outputs"
    return Path(tempfile.gettempdir()) / "melb_build_outputs"


DATA_ROOT, HOW = _resolve()
INPUTS = _inputs_dir(DATA_ROOT)
OUTPUTS = _outputs_dir(DATA_ROOT)

# The named folders, so a rename happens once here rather than in seven scripts.
RAW = INPUTS / "melbourne"                      # the raw extract and the zone table
REF = INPUTS / "optilogic"                      # OPTIONAL Anura reference export — schema check only
FASS = INPUTS / "factors_assumed"               # hand-managed assumptions
FOBS = Path(os.environ.get("MELB_FOBS") or (INPUTS / "factors_observed"))
SCAN_OUT = OUTPUTS / "melbourne_scan_path_analysis"    # the reduction and its cache
CHAIN2_OUT = OUTPUTS / "melbourne_optilogic_chain2_observed"
CHAIN1_OUT = OUTPUTS / "melbourne_optilogic_chain1"
FINAL_OUT = OUTPUTS / "melbourne_optilogic_final"      # the folder Cosmic Frog takes
PRESPLIT = OUTPUTS / ".presplit"                # s3a's undo copies


if __name__ == "__main__":
    log.info(f"pipeline   {PIPELINE}")
    log.info(f"data root  {DATA_ROOT}      ({HOW})")
    for name, p in (("inputs", INPUTS), ("outputs", OUTPUTS), ("factors_assumed", FASS),
                    ("factors_observed", FOBS), ("melbourne (raw)", RAW),
                    ("optilogic (schemas)", REF)):
        log.info(f"  {'OK ' if p.exists() else '-- '} {name:<22} {p}")
