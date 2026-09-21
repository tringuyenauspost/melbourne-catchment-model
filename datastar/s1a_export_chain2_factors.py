"""The pipeline's name for the reduction, so the copied build steps import unmodified.

`s2a_build_chain2.py` does

    from s1a_export_chain2_factors import tag_family, tag_site

and this folder calls that file `scan_reduction.py`. Rather than edit the copy -- which would
make it a fork to re-take by hand every time s2a changes -- the NAME is provided here and
points at the same code. Same trick, same reason, as
`model_input_preparation/export_chain2_factors.py` in the repo.

Nothing new is defined here. If s2a starts importing something else from s1a, it arrives
through this module untouched, because the whole module is re-exported.
"""
import sys

import scan_reduction

# `from s1a_export_chain2_factors import X` then resolves X on the reduction itself, for every
# X it has -- not just the two names s2a asks for today.
sys.modules[__name__] = scan_reduction
