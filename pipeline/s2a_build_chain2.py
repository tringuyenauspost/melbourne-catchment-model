"""Step 2 of the build — chain 2, the DELIVERY entity, built entirely from observed data.

    IN    inputs/factors_observed/    the measurement (written by step 1, s1a_export_chain2_factors.py)
          inputs/factors_assumed/     the hand-managed assumption tables
          inputs/melbourne/           temp_clustered.csv, cluster_summary.csv, all-data.xlsx,
                                      the first-mile catchment geojson
          inputs/optilogic/           an Anura reference export — COLUMN SCHEMAS, not data
    OUT   outputs/melbourne_optilogic_chain2_observed/    (21 tables)

Sources -> sort -> delivery. The balance is exact and measured:
`stage + interstate + Victoria same-day = D`, per depot x class.

Converted from notebooks/melbourne-optilogic-chain2-observed.ipynb (2026-09-07). The notebook
was executed top-to-bottom in a fresh kernel, so this module builds the same namespace in the
same order; the cell boundaries survive as the `# ---` comment banners.

Run:  uv run python notebooks/build_chain2_observed.py
      (or as step 2 of  uv run python pipeline/run_pipeline.py)
"""

# --------------------------------------------------------------------------------------
# # Melbourne — **chain 2 as its own entity, built from observed data** (Change 28)
#
# **The architecture changed here.** Chain 1 (pickup → Sort0 → export) and chain 2 (delivery) are
# now **two separate entities, built and run separately**, then combined for the final Optilogic
# upload:
#
# | notebook | entity | basis |
# |---|---|---|
# | `melbourne-optilogic-chain1-pickup` | chain 1: pickup → sortation → terminate | **current assumptions**, extracted from the Change 27 build |
# | **this one** | chain 2: sources → sort → delivery | **entirely observed** — the 20 May 2026 scan extract |
# | `melbourne-optilogic-final` | the combined model | union of both entities' tables |
#
# Chain 2 here contains **no pickup machinery at all**. Its balance is exact and fully measured:
# `stage + interstate + Victoria same-day = D`, per depot × class. Three supplier families feed it:
#
# - `SUP_INT_*` — interstate arrivals at **six** measured entry sites (the 3 hubs *and* Sunshine
#   West / Melbourne Nth / Bayswater — 48% of interstate first sorts at a delivery depot);
# - `SUP_MET_*` — lodged in metro Melbourne, delivered in metro (45% of delivery)
# - `SUP_REG_*` — lodged in regional Victoria, railed to a metro depot (5.6%). Change 46 split
#   these two apart; they used to be one `SUP_VIC_*` family (the volume the manager's rule had
#   no lane for), entering at its measured first-sort site;
# - `SUP_STAGE_*` — the measured overnight stage at **all 11** delivering depots (kept-at-depot,
#   ops definition; 35–56% of it interstate-lodged, which is why it owes nothing to local pickup).
#
# The hub cross-dock (12b) is a recipe; sort-round shares are banded to the measurement per
# family × site × class. Every measured number is derived live from
# `sankey_from_scans.load_paths()` in section 0b and checked against frozen literals (±1%).
# `LOCAL_KEEP` no longer appears in this notebook — it is chain 1's dial, and chain 1 is next door.
# --------------------------------------------------------------------------------------

from pathlib import Path
import math, json
import pandas as pd
import numpy as np
# geopandas is NOT imported. The notebook read the first-mile catchment geojson with
# gpd.read_file() and used the result for exactly one thing: printing the number of catchments.
# That made the build's heaviest dependency (geopandas + pyogrio + GDAL) load a 22 MB file to
# produce a len(). `n_catchments()` below counts the same features with the standard library, so
# the printed number is unchanged and the build runs anywhere pandas does.

from _paths import CHAIN2_OUT as OUT, DATA_ROOT as REPO, RAW, REF
OUT.mkdir(parents=True, exist_ok=True)

def write_csv(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, encoding="utf-8-sig")
    print(f"  ✓ {name+'.csv':<34} {len(df):>7,} rows")

print("REPO:", REPO)

# --------------------------------------------------------------------------------------
# ## 0. Configuration — the dials the Week 2 session needs
#
# Every expansion in the proposal is a switch here. The sizing table in section 1 reports what each
# combination costs in products, demand rows and work centres, which is the "test rather than assert"
# question the document raises.
# --------------------------------------------------------------------------------------

# ══ FACTOR INPUTS (Change 28b) — nothing numeric is hardcoded in this notebook ════════
# Two folders, two owners:
#   inputs/factors_assumed/    hand-managed assumption CSVs — edit these to steer the model
#   inputs/factors_observed/   written by s1a_export_chain2_factors.py from the scan reduction —
#                              never hand-edit; re-run the exporter after a re-extract
from _paths import FASS, FOBS
for _p, _fix in ((FASS, "restore inputs/factors_assumed (hand-managed)"),
                 (FOBS, "run: uv run python s1a_export_chain2_factors.py")):
    assert _p.exists(), f"missing {_p} — {_fix}"

_dl = pd.read_csv(FASS / "dials.csv").set_index("parameter")
def dial(name):
    _r = _dl.loc[name]
    return {"int": int, "float": float, "str": str,
            "bool": lambda x: str(x).strip() == "True"}[_r["kind"]](_r["value"])
print(f"  dials.csv: {len(_dl)} managed parameters")

AVAILABLE_HOURS_PER_DAY = dict(zip(
    (_oh := pd.read_csv(FASS / "operating_hours.csv")).kind, _oh.hours_per_day))
PERIOD_SPLIT = {f: dict(zip(g.period, g.share))
                for f, g in pd.read_csv(FASS / "period_split.csv").groupby("flow")}

# ── ORIGIN IDENTITY ────────────────────────────────────────────────────────
ORIGIN_TAG_LEVEL = "cluster"      # none | region | cluster | catchment  (doc recommends cluster = 7)
MERGE_DANDENONG  = False          # doc open question 3: collapse the two Dandenong tags -> 6 clusters

# Carry the origin tag all the way into Delivered and the delivery demand rows.
# ON (Change 12, 2026-07-29). This was off because it "needs origin-destination data we do not have".
# It still does — but that data is now supplied EXPLICITLY as an assumption you can see and edit,
# the per-PDC origin mix in section 4b, instead of being silently absent. The cost is real and is
# reported at build time: demand rows and last-mile lanes both multiply by the number of origin tags.
# Set back to False for the cheaper build in which the tag collapses at the delivery stage.
ORIGIN_TAG_TO_DEMAND = True

# ── ORIGIN MIX (Change 12) — how much of each PDC's delivery volume comes from each origin ──
# Seed for the matrix that the tagged demand rows are built from:
#   gravity      nearer origins supply more of a PDC's volume (distance decay, ORIGIN_MIX_DECAY_KM)
#   proportional every PDC gets the same mix, in proportion to each origin's deliverable volume
# Either way the seed is reconciled by IPF to the row margin (PDC demand), the column margin
# (origin deliverable volume) and the pinned local-stage cells, so it is always feasible.
ORIGIN_MIX_SEED     = "gravity"
ORIGIN_MIX_DECAY_KM = dial("ORIGIN_MIX_DECAY_KM")  # legacy gravity seed (retired branch)
ORIGIN_MIX_OVERRIDE = RAW / "origin_mix_override.csv"   # optional hand-edited % table, same shape
                                                        # as the OriginMix.csv this notebook writes

# DELIVERY SOURCING (Change 18) — where a PDC's delivery volume…  → docs/chain2-observed.md#delivery-sourcing-change-18-where-a
DELIVERY_SOURCING = dial("DELIVERY_SOURCING")  # <-- CHANGE 28: the measured matrix

# PICKUP BALANCE (Change 18) — only consulted when…  → docs/chain2-observed.md#pickup-balance-change-18-only-consulted
PICKUP_BALANCE = "conserve_supply"            # <-- hold P + IN where origin_mix had it

# LODGEMENT TYPE  → docs/chain2-observed.md#lodgement-type
LODGEMENT_SPLIT = False           # IND / RES at the pickup state only; collapses after unload
# We do not have a per-catchment industrial flag. The doc cites 59 industrial catchments network wide.
# Rather than guess WHICH catchments are industrial, every catchment supplies both products split by
# this share. Swap to "catchment_list" once ops give us the list — then the ULD/hand split becomes a
# derived result per the doc, instead of a network-wide average.
LODGEMENT_MODE   = "volume_share"     # volume_share | catchment_list
IND_CATCHMENTS   = dial("IND_CATCHMENTS")     # doc figure (lodgement split is off)
IND_LIST_FILE    = RAW / "industrial_catchments.csv"   # optional: post_code column

# Interstate DESPATCH — the volume that leaves Melbourne. Locally lodged, not re-exported arrivals.
# (Change 12 fixed this: the sink used to demand the INTERSTATE-origin Despatch product, which made
#  half of the inbound interstate volume turn around and go straight back out while every locally
#  lodged parcel stayed in Melbourne. The balance still closed, so it was invisible until the origin
#  tag was carried to the demand rows. See section 4.)
# Share of each origin's pickup that leaves Melbourne = 1 - LOCAL_SHARE, uniform across origins.

# ── INTERSTATE INBOUND UNLOAD ──────────────────────────────────────────────
# One product, two competing recipes (ULD / long reach). Doc FAQ 2.
INTERSTATE_UNLOAD   = dial("INTERSTATE_UNLOAD")     # fixed | bounded | free (Change 23)
INTERSTATE_ULD_SHARE = dial("INTERSTATE_ULD_SHARE") # used by "fixed" — a PLACEHOLDER
INTERSTATE_ULD_BOUNDS = (0.20, 0.80)

# ── Change 33 (2026-08-13): the work-centre mix as a user-defined constraint ─────────
# Q2 from the Optilogic cadence. `bounded` above was documented as NOT EXPRESSIBLE; a
# UserDefinedVariable is scoped by product AND process at once, which is the missing
# capability, so it is expressible now. unload_mix.csv carries the shares; see the cell
# after Groups for the algebra and the n-1 rule.
VIC_UNLOAD  = dial("VIC_UNLOAD")       # free | bounded — mirrors INTERSTATE_UNLOAD
WC_MIX_BAND = dial("WC_MIX_BAND")      # half-width of a bound, in share points

# Change 39 : the MANUAL-SORT FLOOR, measured off the scan…  → docs/chain2-observed.md#change-39-the-manual-sort-floor
MANUAL_SORT_SHARE = dial("MANUAL_SORT_SHARE")   # share of DELIVERED volume; 0 = off
UNLOAD_MIX  = pd.read_csv(FASS / "unload_mix.csv")
UNLOAD_MIX["constrain"] = UNLOAD_MIX["constrain"].astype(str).str.strip().str.lower().eq("yes")
for _f, _g in UNLOAD_MIX.groupby("family"):
    assert abs(_g.target_share.sum() - 1.0) < 1e-6, \
        f"unload_mix.csv: {_f} shares sum to {_g.target_share.sum()}, not 1"
    assert (~_g.constrain).sum() >= 1, \
        f"unload_mix.csv: {_f} constrains every method — the n-1 rule needs one residue"
print(f"  unload_mix.csv: {len(UNLOAD_MIX)} rows over {UNLOAD_MIX.family.nunique()} families, "
      f"band +/-{WC_MIX_BAND:.0%}")

# ── PERIODS AND SERVICE ────────────────────────────────────────────────────
# ONE period (Change 13, 2026-07-29, by request) — a single steady-state day named ALL, as in the
# current single-stream model. Section 10 is dormant while this is 1.
N_PERIODS = dial("N_PERIODS")     # 1 = one steady-state day; 2 = AM / PM
PERIODS   = ["AM", "PM"][:N_PERIODS] if N_PERIODS > 1 else ["ALL"]
# Service classes go with the periods. A service class binds through TWO mechanisms: a transit band
# (geography) and an eligible delivery window (time of day). With one period the window restriction
# has nothing to restrict, so a class would be a comment on the demand row and nothing more — and
# two rows differing only by that comment would collide on (customer, product, period). So it is off
# too; turn it back on with the periods.
SERVICE_CLASSES = False           # split delivery demand by service class + eligible window
SAME_DAY_SHARE  = dial("SAME_DAY_SHARE")  # same-day test share (service classes off)

# Operating hours a resource is genuinely available PER DAY.
# THE POINT OF THE PROPOSAL: capacity = rate x window, never rate x period length.
# These are placeholders — doc open question 4, and the same question as "20 hours or 13.5".
# AVAILABLE_HOURS_PER_DAY: loaded from operating_hours.csv at the top of this cell

# Capacity is split EVENLY across periods, and that is a…  → docs/chain2-observed.md#capacity-is-split-evenly-across-periods
def window(kind):
    return AVAILABLE_HOURS_PER_DAY[kind] / len(PERIODS)
# Share of each flow that sits in each period. Delivery is a morning operation, collection an evening one.
# PERIOD_SPLIT: loaded from period_split.csv at the top of this cell

# SORTATION ROUNDS  → docs/chain2-observed.md#sortation-rounds
HUB_SORT_ROUNDS = dial("HUB_SORT_ROUNDS")    # <-- sort rounds

# Hub docks with two rounds. Each delivery-bound parcel is unloaded twice and loaded twice, at two
# different hubs, so the dock requirement roughly doubles while the machine fleet does not. Rather
# than emit a model that cannot solve, scale the hub dock rate to the touches it must carry and say
# so loudly — a scale above 1.0 is a real ops requirement (more docks, or longer dock hours), not a
# modelling fudge. Set False to leave the fleet as stated and let the shortfall stand.
HUB_DOCK_AUTOSCALE = True
HUB_DOCK_HEADROOM  = dial("HUB_DOCK_HEADROOM")

# MINIMUM VIABLE SHIPMENT  → docs/chain2-observed.md#minimum-viable-shipment
DESPATCH_MIN_SHIPMENT = dial("DESPATCH_MIN_SHIPMENT")   # EA per arc. 0 = OFF.
DESPATCH_MIN_LEGS     = ("7", "7b")       # leg codes from the transport table's `notes` column:
                                          # 6/6b round-2 linehaul, 7/7b despatch to the depots

# INTERSTATE SORT BYPASS  → docs/chain2-observed.md#interstate-sort-bypass
INTERSTATE_BYPASS   = dial("INTERSTATE_BYPASS")   # off | free | min_truckload
BYPASS_MIN_SHIPMENT = dial("BYPASS_MIN_SHIPMENT") # EA per direct arc (min_truckload)
BYPASS_MAX_SHARE    = None        # optional cap on the share of a hub's arrivals that may
                                  # bypass, e.g. 0.30. None = uncapped, let cost decide.

# ── constraint posture (Change 27) — dials in factors_assumed/dials.csv ──────────────
BYPASS_CONSTRAIN = dial("BYPASS_CONSTRAIN")   # band the single-sort shares to the measurement
BYPASS_BAND      = dial("BYPASS_BAND")

# ── CHANGE 28 (2026-08-08): CHAIN 2 FROM OBSERVED DATA, AS ITS OWN ENTITY ─────────────
# Change 37 collapsed OBS_CELL_FLOOR (a share of a row), OBS_MIN_CLASS_SITE (EA per flavour) and
# OBS_LANE_FLOOR/OBS_LANE_BASIS (a share of a column) into ONE threshold in articles, applied by
# the exporter. The notebook consumes what it gets and keeps the number only to print it.
FOLD_MIN_ARTICLES  = dial("FOLD_MIN_ARTICLES")   # EA below which a flavour, cell or lane folds
XDOCK_ENABLED      = dial("XDOCK_ENABLED")       # Change 28 (12b): the hub cross-dock recipe
SORT_BAND          = dial("SORT_BAND")           # half-width of every measured band
ARRIVAL_HEADROOM   = dial("ARRIVAL_HEADROOM")    # chain-2 supplier caps are exact

# ── CHANGE 48: round-2 handling charged for the sorts the SCANS measure ──────────────
# The model routes fewer parcels through a second sort than the network performs — most of the
# gap is staged freight, sorted the day it ARRIVED, which one day of model cannot carry. The WORK
# is real either way, so round-2 unload, sort and load are timed and charged for the measured
# volume instead of the routed one. `off` pays only for what is routed. Both measured volumes come
# from the exporter (`_provenance.csv`), so the ratio re-derives itself whenever a filter dial
# moves — a number typed here would go stale the first time SORT_BAND or the cohort changed.
ROUND2_COST_BASIS = dial("ROUND2_COST_BASIS")    # off | same_day | cohort
assert ROUND2_COST_BASIS in ("off", "same_day", "cohort"), \
    f"ROUND2_COST_BASIS must be off | same_day | cohort, got {ROUND2_COST_BASIS!r}"

WORKING_DAYS = dial("WORKING_DAYS")

# ── 0b. CHANGE 28: the measured factors, read from inputs/factors_observed/ ───────────
# NOTHING here is hardcoded, and NOTHING here filters. `s1a_export_chain2_factors.py` derives AND
# filters the factors (cell floor, class-site minimum, round-2 site threshold — dials it reads
# from factors_assumed/dials.csv); this notebook consumes whatever it got. The drift guard below
# re-runs the exporter's own derive + filter code against the scan cache and asserts the CSVs
# still match, so a stale or hand-edited export fails the build.
_prov = pd.read_csv(FOBS / "_provenance.csv").set_index("key")["value"]
print("  observed factors: " + "  ".join(f"{k}={_prov[k]}" for k in
      ("source_scan_csv", "peak_day", "generated_utc")))
print(f"  filters applied AT EXPORT: one fold at {_prov['filter_fold_min_articles']} EA "
      f"(a flavour, a joint cell or a lane below it folds into what survives), round-2 share >= "
      f"{float(_prov['filter_round2_min_share']):.0%}  "
      f"({_prov['filtered_volume_reassigned_ea']} EA reassigned to surviving cells)")
# Change 44: the parcel's journey is the FACILITY PATH — the buildings it was in, in order — not
# the sequence of roles (first sort, first handler, second sort) it used to be built from.
print(f"  path basis: {_prov['path_basis']} on the {_prov['path_touch_bar'].upper()} bar "
      f"({_prov['path_touch_events']}), capped at {_prov['path_depth']} buildings "
      f"({_prov['path_cap_rule']}); mean {_prov['path_mean_buildings']} of our buildings per "
      f"article before the depot")

# CHANGE 44b — THE MEASUREMENT SPEAKS A WIDER GRAMMAR THAN THIS…  → docs/chain2-observed.md#change-44b-the-measurement-speaks-a
# The exporter is the vocabulary: parse the measurement's tags with ITS rules, never with
# split("_"). It is a sibling in this folder now, so the import needs no sys.path shim.
from s1a_export_chain2_factors import tag_family as _tag_family, tag_site as _tag_site
# CHANGE 46 (2026-08-31) — FOUR FAMILIES, which is what the exporter has always given us.
# METRO and REGION used to fold together into one "VIC" family here, so a model built from a
# four-band measurement carried three. They are different journeys: METRO is lodged inside the
# metro catchment and delivered inside it; REGION is lodged in regional Victoria and railed in.
# The scan page (sankey-facility-path.html) has drawn all four since Change 36 — this is the
# chain-2 half of that, and the two pages now answer with the same vocabulary.
C2_FAMILY = {"INTERSTATE": "INT", "METRO": "MET", "REGION": "REG", "METRO_DEPOT": "STG"}
C2_SITES  = ("MPF", "TPF", "MGF", "SWP", "MNP", "BAY")

_j = pd.read_csv(FOBS / "obs_joint.csv")
_j["fam"] = _j.tag.map(lambda t: C2_FAMILY[_tag_family(t)])
_j["site"] = _j.tag.map(_tag_site)
_off = (_j.share[_j.site.notna() & ~_j.site.isin(C2_SITES)].sum() / _j.share.sum())
OBS_JOINT, _lost = {}, 0.0
for (_p, _c), _g in _j.groupby(["pud", "cls"]):
    _row = {}
    for _fam, _fg in _g.groupby("fam"):
        if _fam == "STG":
            _row["STG"] = float(_fg.share.sum())
            continue
        _keep = _fg[_fg.site.isin(C2_SITES)]
        _spill = float(_fg.share.sum() - _keep.share.sum())
        if _keep.empty:               # nothing of this family survives in this row
            _lost += _spill
            continue
        _w = _keep.groupby("site").share.sum()
        for _s, _v in (_w + _spill * _w / _w.sum()).items():
            _row[f"{_fam}_{_s}"] = _row.get(f"{_fam}_{_s}", 0.0) + float(_v)
    _tot = sum(_row.values())
    OBS_JOINT[(_p, _c)] = {t: v / _tot for t, v in _row.items()}   # rows sum to 1, always
OBS_JOINT_F = OBS_JOINT          # pre-filtered at export; the notebook applies nothing further
print(f"  grammar fold: none (Change 46 keeps all four families); {_off:.1%} of the joint "
      f"(share-weighted) sat at a site "
      f"outside {list(C2_SITES)} and was re-spread within its own family"
      + (f"; {_lost:.2%} had no surviving site in its row and was renormalised away"
         if _lost else ""))

# obs_single_sort is rebuilt from the folded legs below, so the two cannot disagree.
# the measured 2+ sort volume, for Change 48's round-2 handling scale (dial in the cell above)
SORT_2PLUS = {"cohort": int(_prov["sort_2plus_cohort"]),
              "same_day": int(_prov["sort_2plus_same_day"])}
print(f"  measured 2+ sorts: cohort {SORT_2PLUS['cohort']:,} EA, "
      f"same-day {SORT_2PLUS['same_day']:,} EA")

_ss = pd.read_csv(FOBS / "obs_single_sort.csv")
_ss["fam"] = _ss.family.map(C2_FAMILY)
_ss = _ss[_ss.site.isin(C2_SITES)]
OBS_SINGLE = {}
for (_f, _c, _s), _g in _ss.groupby(["fam", "cls", "site"]):
    _a = int(_g.articles.sum())
    OBS_SINGLE[(_f, _c, _s)] = (float((_g.single_share * _g.articles).sum() / _a), _a)
# Change 44: ONE lane matrix, because there is one journey  → docs/chain2-observed.md#change-44-one-lane-matrix-because
OBS_DEMAND = {(_r.pud, _r.cls): int(_r.articles)
              for _r in pd.read_csv(FOBS / "obs_demand.csv").itertuples()}
# the same two folds, applied to the lanes: families merged by ARTICLES (a share cannot be
# averaged), and a leg into a building this notebook has no node for becomes ONE BUILDING —
# the parcel is modelled as finishing where it entered, which is what "no node for it" means.
_lg = pd.read_csv(FOBS / "obs_legs.csv")
_lg["fam"] = _lg.family.map(C2_FAMILY)
_lg["to"] = _lg.dest.where(_lg.dest.isin(C2_SITES), "ONCE")
_lg = _lg[_lg.entry.isin(C2_SITES)]
OBS_LEGS = {}
for (_f, _c, _e), _g in _lg.groupby(["fam", "cls", "entry"]):
    _by = _g.groupby("to").articles.sum()
    for _d, _a in (_by / _by.sum()).items():
        OBS_LEGS[(_f, _c, _e, _d)] = float(_a)
OBS_ROUND2 = OBS_LEGS            # the lane matrix under the name the build already reads
# and obs_single_sort follows the folded lanes rather than the unfolded export
OBS_SINGLE = {(_f, _c, _e): (OBS_LEGS.get((_f, _c, _e, "ONCE"), 0.0), _a)
              for (_f, _c, _e), (_sh, _a) in OBS_SINGLE.items()}
OBS_RECV_ENTRY = {}              # no separately measured cross-dock on this basis — see above
_dlvcsv = pd.read_csv(FOBS / "obs_delivery.csv")     # NOT _dl — that is cell 3's dials frame
_dlvcsv = _dlvcsv[_dlvcsv["exit"].isin(C2_SITES)]
OBS_DELIVERY = {}                                                              # verification only
for (_c, _x), _g in _dlvcsv.groupby(["cls", "exit"]):
    for _p, _a in (_g.groupby("pud").articles.sum() / _g.articles.sum()).items():
        OBS_DELIVERY[(_c, _x, _p)] = float(_a)
print(f"  lanes measured: {len([k for k in OBS_LEGS if k[3] != 'ONCE'])} building-to-building, "
      f"{len(OBS_DELIVERY)} despatch->depot (verified, not pinned); "
      f"measured delivery {sum(OBS_DEMAND.values()):,} EA")
R2_ALLOWED_CODES = sorted(pd.read_csv(FOBS / "obs_round2_sites.csv").site)
ARR_CODES = ["MPF", "TPF", "MGF", "SWP", "MNP", "BAY"]
print(f"  round-2 sites (measured, share >= dial): {R2_ALLOWED_CODES}")

# ── drift guard: the exporter's own derive + filter, run against the live scan cache ──
try:
    from s1a_export_chain2_factors import CACHE as _SCAN_CACHE, load_paths as _scan_paths
    from s1a_export_chain2_factors import (derive as _derive_factors,
                                      filter_factors as _filter_factors,
                                      round2_sites as _round2_sites)
    _have_cache = _SCAN_CACHE.exists()
except Exception as _e:
    _have_cache = False
    print(f"  drift guard skipped — scan pipeline not importable ({_e})")
if _have_cache:
    _lj, _ls, _l2, _lmeta = _derive_factors(_scan_paths().set_index("Consignment_ID"))
    _fj, _fs, _dead, _moved = _filter_factors(_lj, _ls, dial("FOLD_MIN_ARTICLES"))
    # Change 44b: the notebook folds the export onto its own grammar, so this can no longer be
    # an equality test on the tags. What it CAN still prove is that no volume was invented or
    # lost by the fold — every depot row sums to 1 and every family total survives it.
    _fam_ex, _fam_nb = {}, {}
    for _pud, _cls, _tag, _share, _a in _fj:
        _k = (_pud, _cls, C2_FAMILY[_tag_family(_tag)])
        _fam_ex[_k] = _fam_ex.get(_k, 0.0) + _share
    for (_pud, _cls), _m in OBS_JOINT.items():
        for _t, _v in _m.items():
            _k = (_pud, _cls, _t.split("_")[0])
            _fam_nb[_k] = _fam_nb.get(_k, 0.0) + _v
    assert set(_fam_ex) == set(_fam_nb), "the grammar fold changed which families a depot has"
    for _k, _v in _fam_ex.items():
        assert abs(_fam_nb[_k] - _v) < 0.01, \
            f"the grammar fold moved volume between families at {_k} — {_v:.3f} -> {_fam_nb[_k]:.3f}"
    for (_pud, _cls), _m in OBS_JOINT.items():
        assert abs(sum(_m.values()) - 1.0) < 1e-9, f"{_pud}/{_cls} does not sum to 1"
    # Change 29: obs_single_sort IS the ONCE slice of the round-2 matrix now, so it is checked
    # against that and not against the old standalone derivation — the two differ on purpose
    # (the matrix drops staged freight, which rides no sort lane in this model).
    assert R2_ALLOWED_CODES == sorted(s for s, _, _ in
                                      _round2_sites(_l2, dial("ROUND2_MIN_SHARE"))), \
        "obs_round2_sites.csv disagrees with the ROUND2_MIN_SHARE dial — re-run the exporter"
    # Change 29: the stage matrices go through the same door
    from s1a_export_chain2_factors import (derive_stages as _derive_stages,
                                      filter_stages as _filter_stages)
    _dem, _re, _legs, _dlv, _vicxd = _derive_stages(_scan_paths().set_index("Consignment_ID"))
    _xf, _r2f, _dlf, _lm = _filter_stages(_re, _legs, _dlv,
                                          {s for s, _, _ in
                                           _round2_sites(_l2, dial("ROUND2_MIN_SHARE"))},
                                          _dead, dial("FOLD_MIN_ARTICLES"))
    assert OBS_DEMAND == {(p, c): a for p, c, a in _dem}, \
        "obs_demand.csv is stale — re-run s1a_export_chain2_factors.py"
    assert OBS_RECV_ENTRY == {(c, r, e): a for c, r, e, a, _s in _xf}, \
        "the cross-dock matrix disagrees with the exporter — re-run s1a_export_chain2_factors.py"
    assert set(OBS_LEGS) <= {(C2_FAMILY[f], c, e, d if d in C2_SITES else "ONCE")
                             for f, c, e, d, _a, _s in _r2f}, \
        "obs_legs.csv carries lanes the current FOLD_MIN_ARTICLES no longer allows — re-export"
    assert set(OBS_DELIVERY) == {(c, x, p) for c, x, p, _a, _s in _dlf if x in C2_SITES}, \
        "obs_delivery.csv is stale — re-run s1a_export_chain2_factors.py"
    for (_f, _c, _e), (_sh, _a) in OBS_SINGLE.items():
        assert abs(OBS_LEGS.get((_f, _c, _e, "ONCE"), 0.0) - _sh) < 1e-9, \
            f"the single-sort share disagrees with the folded lanes at {_f}/{_c}/{_e}"
    print("  drift guard: CSV factors re-derived and re-filtered from the scan cache — matched")

# ── Facilities, unchanged from the current model ───────────────────────────
PRODUCT_MAP = {
    "eParcel Express":   "EP", "Metro Next Day":  "PP", "eParcel Standard": "PP",
    "eParcel Returns":   "PP", "rParcel Post Plus": "PP",
}
# Real delivery class -> service class. Uses the 5 classes we already have in the source data
# rather than inventing 2h/4h promises.
SERVICE_OF_CLASS = {
    "eParcel Express":   "ND",   "Metro Next Day":  "ND",
    "eParcel Standard":  "STD",  "eParcel Returns": "STD", "rParcel Post Plus": "STD",
}
SERVICE_DEF = {   # service -> (transit band in days, eligible periods)
    "ND":  (1.0, ["AM"]),            # next day: must go out on the morning wave
    "STD": (1.0, ["AM", "PM"]),
    "SD":  (0.2, ["PM"]),            # same day: afternoon wave only (test class)
}
HUB_NODES = {
    "Melbourne Gateway Facility":  "HUB_Melbourne_Gateway",
    "Tullamarine Parcel Facility": "HUB_Tullamarine_Facility",
    "Melbourne Parcel Facility":   "HUB_Melbourne_Parcel",
    "Dandenong Letter Center":     "HUB_Dandenong_Letter",
}
PUD_NODES = {
    "Sunshine West PDC": "PUD_Sunshine_West", "Melbourne North PDC": "PUD_Melbourne_North",
    "Oakleigh South PDC": "PUD_Oakleigh_South", "Dandenong South PDC": "PUD_Dandenong_South",
    "Bayswater PDC": "PUD_Bayswater", "Tullamarine PDC": "PUD_Tullamarine",
    "Mulgrave PDC": "PUD_Mulgrave", "Darebin PDC": "PUD_Darebin", "Pakenham PDC": "PUD_Pakenham",
    "Mount Waverley PDC": "PUD_Mount_Waverley", "Abbotsford Parcel Delivery": "PUD_Abbotsford",
}
FM_ONLY_PUD_NODES = {
    "Dandenong Transport Facility": "PUD_Dandenong_Transport",
    "Melbourne Transport":          "PUD_Melbourne_Transport",
}
FM_ONLY_COORD_SOURCE = {"PUD_Dandenong_Transport": "HUB_Dandenong_Letter",
                        "PUD_Melbourne_Transport": "HUB_Melbourne_Parcel"}
NODE_TO_CANON = {**HUB_NODES, **PUD_NODES}
HUB_SET  = set(HUB_NODES.values())
PUD_SET  = set(PUD_NODES.values()) | set(FM_ONLY_PUD_NODES.values())
FM_ONLY_PUD_SET = set(FM_ONLY_PUD_NODES.values())
DELIVERY_PUD_SET = set(PUD_NODES.values())

FIRST_MILE_FACILITY = {
    "Oakleigh South Van Operations":  "PUD_Oakleigh_South",
    "Sunshine West Van Services":     "PUD_Sunshine_West",
    "Bayswater Van Operations":       "PUD_Bayswater",
    "Melbourne North Van Operations": "PUD_Melbourne_North",
    "Dandenong South Van Operations": "PUD_Dandenong_South",
    "Dandenong Transport Facility":   "PUD_Dandenong_Transport",
    "Melbourne Transport":            "PUD_Melbourne_Transport",
}
DEPOT_TO_PUD = {
    "Depot_1": "PUD_Pakenham", "Depot_2": "PUD_Dandenong_South", "Depot_3": "PUD_Oakleigh_South",
    "Depot_4": "PUD_Mulgrave", "Depot_5": "PUD_Mount_Waverley", "Depot_6": "PUD_Bayswater",
    "Depot_7": "PUD_Sunshine_West", "Depot_8": "PUD_Abbotsford", "Depot_9": "PUD_Darebin",
    "Depot_10": "PUD_Tullamarine", "Depot_11": "PUD_Melbourne_North",
}
DEPOT_TO_PUD_SHORT = {f"D{k.split('_')[1]}": v for k, v in DEPOT_TO_PUD.items()}

# ── ORIGIN CLUSTERS: one per first-mile site, named for the geography not the building ──
# The doc's Appendix A is the reason: a facility-named tag has no producer once that facility closes,
# and every demand row asking for it becomes unsatisfiable.
CLUSTER_OF_PUD = {
    "PUD_Bayswater":            "OUTER_EAST",
    "PUD_Melbourne_North":      "NORTH",
    "PUD_Sunshine_West":        "WEST",
    "PUD_Oakleigh_South":       "SOUTH_EAST",
    "PUD_Dandenong_South":      "DANDENONG",
    "PUD_Dandenong_Transport":  "DANDENONG_TR",
    "PUD_Melbourne_Transport":  "INNER",
}
if MERGE_DANDENONG:
    CLUSTER_OF_PUD["PUD_Dandenong_Transport"] = "DANDENONG"
REGION_OF_CLUSTER = {"OUTER_EAST": "EAST", "SOUTH_EAST": "EAST", "DANDENONG": "EAST",
                     "DANDENONG_TR": "EAST", "NORTH": "NORTH", "WEST": "WEST", "INNER": "NORTH"}

def origin_tag(pud):
    """The tag a parcel lodged at `pud` carries, at the configured granularity."""
    c = CLUSTER_OF_PUD[pud]
    return {"none": "LOCAL", "region": REGION_OF_CLUSTER[c], "cluster": c}.get(ORIGIN_TAG_LEVEL, c)

SITE_SORTERS = {}
for _r in pd.read_csv(FASS / "site_sorters.csv").itertuples():
    SITE_SORTERS.setdefault(_r.site, {})[_r.machine] = int(_r.rate_hr)
SORT_PUD_SET  = {s for s in SITE_SORTERS if s in PUD_SET}
STAGE_PUD_SET = SORT_PUD_SET & DELIVERY_PUD_SET
PUD_CAPACITY = {_r.pud: int(_r.capacity_ea)
                for _r in pd.read_csv(FASS / "pud_capacity.csv").itertuples()}
# LOCAL_KEEP / LOCAL_SHARE: chain-1 dials, moved to the chain-1 notebook.
FACILITY_HEADROOM = dial("FACILITY_HEADROOM")

# Short codes for the origin-flavoured round-1 despatch products (Change 14).
HUB_CODE = {"HUB_Dandenong_Letter": "DLC", "HUB_Melbourne_Gateway": "MGF",
            "HUB_Melbourne_Parcel": "MPF", "HUB_Tullamarine_Facility": "TPF"}
assert set(HUB_CODE) == HUB_SET, f"HUB_CODE does not cover the hubs: {HUB_SET ^ set(HUB_CODE)}"

ORIGIN_TAGS = sorted({origin_tag(p) for p in CLUSTER_OF_PUD})
# Tags a DELIVERY demand row can carry. Interstate is its own family, not an origin cluster, but it
# is an origin as far as "where did the parcel come from" is concerned — so it is a column of the
# origin-mix matrix and a delivered product like any other.
# Change 18: under `interstate_plus_stage` the only LOCAL tags that ever reach a delivery zone are
# the ones with staged volume sitting on the delivery site itself. Every other origin's volume is
# exported, so its Delivered product, its driver-wave recipe, its hub->PDC lanes and its round-2
# states are all dead weight — this is where the model gets dramatically smaller.
STAGE_TAGS  = sorted({origin_tag(p) for p in STAGE_PUD_SET})
_dtags      = STAGE_TAGS if DELIVERY_SOURCING == "interstate_plus_stage" else ORIGIN_TAGS
DEMAND_TAGS = (_dtags + ["INTERSTATE"]) if ORIGIN_TAG_TO_DEMAND else ["LOCAL"]
print(f"origin tags ({ORIGIN_TAG_LEVEL}, {len(ORIGIN_TAGS)}): {ORIGIN_TAGS}")
print("demand-row tags: see the Change 20 block below — this build redefines them")
print(f"delivery sourcing: {DELIVERY_SOURCING}"
      + (f"   pickup balance: {PICKUP_BALANCE}" if DELIVERY_SOURCING == "interstate_plus_stage" else ""))
print(f"periods: {PERIODS}   hub sort rounds: {HUB_SORT_ROUNDS}"
)

# ══ Change 20 (2026-07-31) — two adjustments to the manager's sourcing variant ═══════════
# 20a  Dandenong Letter Center is a PDC, not a hub.
# 20b  ALL pickup terminates — and it is made STRUCTURAL, not left to demand pinning + cost.

# ── 20a — DLC RECLASSIFIED: HUB -> PDC ──────────────────────────────────────────────────
# It collects no pickup and runs no delivery round. It keeps its 10k/hr small sorter and takes
# HUB OVERFLOW: a capped slice of the volume needing a second sort, which it then despatches
# onward to the delivering PDCs. DLC holds no Despatch1 flavour of its own, so "the two sorts
# happen at two different sites" stays structural — it may consume EVERY hub's flavour.
DLC_NODE, DLC_HUB, DLC_PUD = "Dandenong Letter Center", "HUB_Dandenong_Letter", "PUD_Dandenong_Letter"
DLC_SMALL_SHARE = dial("DLC_SMALL_SHARE")   # Max round-2 divert share; measured 0.4%, see dials.csv
HUB_NODES.pop(DLC_NODE)
PUD_NODES[DLC_NODE] = DLC_PUD
NODE_TO_CANON = {**HUB_NODES, **PUD_NODES}
HUB_SET = set(HUB_NODES.values())
PUD_SET = set(PUD_NODES.values()) | set(FM_ONLY_PUD_NODES.values())
HUB_CODE.pop(DLC_HUB)                                  # no Despatch1 flavour, no interstate sink
# site_sorters.csv now names the building PUD_Dandenong_Letter, as sites.csv and
# sort_only_sites.csv always did, so there is usually nothing left to move. The pop stays
# tolerant so an older CSV still loads.
if DLC_HUB in SITE_SORTERS:
    SITE_SORTERS[DLC_PUD] = SITE_SORTERS.pop(DLC_HUB)  # the sorter follows the building
FM_ONLY_COORD_SOURCE["PUD_Dandenong_Transport"] = DLC_PUD
assert set(HUB_CODE) == HUB_SET, f"HUB_CODE no longer covers the hubs: {HUB_SET ^ set(HUB_CODE)}"

# CHANGE 30 — THE SORT-ONLY SITES: eight sorting buildings, not…  → docs/chain2-observed.md#change-30-the-sort-only-sites
SORT_ONLY = pd.read_csv(FASS / "sort_only_sites.csv")
SORT_ONLY_PUDS = set(SORT_ONLY.facility)
for _r in SORT_ONLY.itertuples():
    PUD_NODES[_r.node_label] = _r.facility
NODE_TO_CANON = {**HUB_NODES, **PUD_NODES}
PUD_SET = set(PUD_NODES.values()) | set(FM_ONLY_PUD_NODES.values())
SITE_SORTERS_MISSING = SORT_ONLY_PUDS - set(SITE_SORTERS)
assert not SITE_SORTERS_MISSING, (
    f"a sort-only site with no sorter: {SITE_SORTERS_MISSING} — add it to site_sorters.csv")

# ══ CHANGE 28 — THE ARRIVAL SET: interstate enters at SIX sites, not three ═════════════
# 48% of interstate first sorts at Sunshine West / Melbourne Nth / Bayswater (section 12a, now
# adopted). Those depots join the arrival set and get their own SITE code, so INTERSTATE_<code>
# and VIC_<code> flavours exist for them.
ARRIVAL_PUD_SET = {"PUD_Sunshine_West", "PUD_Melbourne_North",
                   "PUD_Bayswater"} | SORT_ONLY_PUDS      # Change 30: + Avalon, Dandenong LC
ARRIVAL_SET = HUB_SET | ARRIVAL_PUD_SET
HUB_CODE.update({"PUD_Sunshine_West": "SWP", "PUD_Melbourne_North": "MNP",
                 "PUD_Bayswater": "BAY"})
HUB_CODE.update({_r.facility: _r.code for _r in SORT_ONLY.itertuples()})
assert set(HUB_CODE) == ARRIVAL_SET, "HUB_CODE must cover exactly the arrival set"
# Change 28c: only the sites the scans show running second sorts may run one (obs_round2_sites.csv).
# Everything else still sorts round 1 and despatches its own flavour DIRECT — it just cannot take
# another site's flavour for a second pass.
R2_SORT_SITES = {s for s in ARRIVAL_SET if HUB_CODE[s] in R2_ALLOWED_CODES}
print(f"  round-2 capability restricted to {sorted(HUB_CODE[s] for s in R2_SORT_SITES)} "
      f"(measured); other sites despatch their own flavour direct only")
CODE_SITE = {v: k for k, v in HUB_CODE.items()}

# ══ CHANGE 29 — the observed stage lanes, as sets this notebook can loop over ═══════════
# Stage 2: who hands freight to whom for its FIRST sort. The diagonal (handled and sorted at the
# same site) is the normal case and is not a lane; only the off-diagonals are cross-dock.
XD_PAIRS   = {(c, CODE_SITE[r], CODE_SITE[e]) for (c, r, e) in OBS_RECV_ENTRY
              if r != e and r in CODE_SITE and e in CODE_SITE}
XD_SOURCES = {s for _, s, _ in XD_PAIRS}          # sites that hand freight on unsorted
XD_DESTS   = {d for _, _, d in XD_PAIRS}          # sites that sort someone else's arrivals
# Stage 3: which (family, class, first-sort site) -> (second-sort site) lanes exist at all. This
# REPLACES the "any allowed round-2 site may take any flavour" rule — the scans name the pairs.
R2_PAIRS = {(f, c, CODE_SITE[g], CODE_SITE[h]) for (f, c, g, h) in OBS_ROUND2
            if h != "ONCE" and g in CODE_SITE and h in CODE_SITE}
R2_SORT_SITES = {h for _, _, _, h in R2_PAIRS}    # tightened: a site with no inbound lane is out
print(f"  Change 29 stage lanes: cross-dock "
      + (", ".join(sorted({f"{HUB_CODE[r]}->{HUB_CODE[e]}" for _c, r, e in XD_PAIRS})) or "none")
      + f"; round-2 routing on {len(R2_PAIRS)} family x class x site pairs into "
      + f"{sorted(HUB_CODE[s] for s in R2_SORT_SITES)}")

# Four PDC roles, derived. A site may hold any combination.
_FM_PUDS         = set(FIRST_MILE_FACILITY.values())
SORT_PUD_SET     = {s for s in SITE_SORTERS if s in PUD_SET}       # has a sorter
ROUND0_PUD_SET   = SORT_PUD_SET & _FM_PUDS                         # sorts its OWN pickup
ROUND2_PUD_SET   = ({DLC_PUD} if "DLC" in R2_ALLOWED_CODES else set())  # observed: DLC is ~1% of 2nd sorts
DELIVERY_PUD_SET = set(PUD_NODES.values()) - SORT_ONLY_PUDS        # runs a last-mile round
STAGE_PUD_SET    = ROUND0_PUD_SET & DELIVERY_PUD_SET               # stages only where it delivers
for _s in SORT_ONLY_PUDS:          # PLACEHOLDER — ops gave no PDC figure for either
    PUD_CAPACITY[_s] = 0           # (both are sorting buildings, not delivery depots)
PUD_R2_UNLOAD, PUD_R2_LOAD = "UNLOAD_ULD", "LOAD_ULD"   # hub arrivals come in ULDs/cages

# 20b — ALL PICKUP TERMINATES, STRUCTURALLY  → docs/chain2-observed.md#20b-all-pickup-terminates-structurally
def stage_tag(t):
    return f"STG_{t}"

# CHANGE 28: stage is keyed by DEPOT, not by origin cluster — 8 of the 11 delivering depots have
# no cluster entry (they collect no pickup), and the measured stage does not care: kept freight
# is 35-56% interstate-lodged. STG_PUDS is every delivering depot.
def stage_tag_pud(p):
    return f"STG_{p.replace('PUD_', '')}"
STG_PUDS = sorted(DELIVERY_PUD_SET)

PICKUP_TAGS  = list(ORIGIN_TAGS)                                    # 7 clusters, export or keep
R0_TAGS      = sorted({origin_tag(p) for p in ROUND0_PUD_SET})      # tags with a round-0 sort
STG_TAGS     = [stage_tag_pud(p) for p in STG_PUDS]                 # Change 28: 11 depot tags
DEMAND_TAGS  = STG_TAGS + ["INTERSTATE"]        # redefined below once the site tags exist
DLV_TAGS     = ["INTERSTATE"]

def zone_tags(pud):
    """The origins a delivery zone under `pud` may be served from (Change 20b).

    Its own PDC's staged volume, if that PDC stages, plus interstate. Nothing else can physically
    be in the building: everything else lodged in Melbourne left the state after one sort.
    """
    return [stage_tag_pud(pud), "INTERSTATE"]

print(f"  sorting sites ({len(ARRIVAL_SET)}): {sorted(HUB_CODE.values())}"
      f"   of which hubs: {sorted(HUB_CODE[h] for h in HUB_SET)}"
      f"   sort-only: {sorted(HUB_CODE[s] for s in SORT_ONLY_PUDS)}   PDC buildings: {len(PUD_SET)}")
print(f"    round-0 (own pickup): {sorted(_short_ for _short_ in (p.replace('PUD_','') for p in ROUND0_PUD_SET))}")
print(f"    round-2 (off-hub 2nd sort): {[p.replace('PUD_','') for p in sorted(ROUND2_PUD_SET)]}"
      f"  (cap {DLC_SMALL_SHARE:.0%})")
print(f"    staging: {[p.replace('PUD_','') for p in sorted(STAGE_PUD_SET)]}"
      f"   delivering: {len(DELIVERY_PUD_SET)}")
print(f"  tag families — pickup {PICKUP_TAGS}")
print(f"                 stage  {STG_TAGS}")
print(f"                 demand rows may carry {DEMAND_TAGS}; round 2 is {DLV_TAGS} only")

# ── CHANGE 47 (2026-08-31): class_hub_split.csv is GONE, and so is everything that read
# it. It survived here as CLASS_HUB_SPLIT / CLASS_HUB_SPLIT_PRE26 / CLASS_HUB_SPLIT_BY_ORIGIN
# / pickup_split / pickup_hubs, which between them reached two asserts and two print
# statements and NOT ONE emitted cell — a perturbation test (change every share, rebuild,
# diff all 21 tables) came back byte-identical. Arrivals have been measured since Change 28
# and the pickup side moved to chain 1, which reads no factor CSV at all, so nothing was
# left for it to steer.
# hoisted from the demand cell: the balance cell now needs it too, to split the interstate
# column margin by arrival hub. Redefined identically further down; harmless.
def split_int(total, weights):
    """Integer split of `total` in proportion to `weights`, largest remainder, sums exactly."""
    tw = sum(weights)
    if tw <= 0 or total == 0:
        return [total if i == 0 else 0 for i in range(len(weights))]
    raw = [total * w / tw for w in weights]
    out = [int(math.floor(x)) for x in raw]
    for _, i in sorted(((raw[i] - out[i], i) for i in range(len(raw))), reverse=True)[:total - sum(out)]:
        out[i] += 1
    return out
# CHANGE 28 — the arrival accessors range over the measured entry sites. A (family, class, site)
# gets a flavour only if the floored joint leaves volume there.
def _fam_sites(fam, cls):
    tot = {}
    for (p, c), m in OBS_JOINT_F.items():
        if c != cls:
            continue
        for t, v in m.items():
            if t.startswith(fam + "_"):
                tot[t.split("_", 1)[1]] = tot.get(t.split("_", 1)[1], 0.0) + v
    return sorted(code for code in tot if code in HUB_CODE.values())

# ══ CHANGE 46 — THE FOUR FAMILIES, AND ONE SET OF ACCESSORS FOR ALL OF THEM ════════
# There used to be an `int_*` set and a `vic_*` set of these, which is why adding a fourth
# family meant touching fourteen cells. They are now keyed by family code, and the interstate
# names below are thin aliases so the interstate paths read exactly as they did.
#   INT  interstate arrivals          MET  lodged in metro, delivered in metro
#   REG  regional Victoria -> metro   STG  kept at its own depot (no arrival leg at all)
SD_FAMS   = ("MET", "REG")                 # the two VICTORIAN lodgement bands
ARR_FAMS  = ("INT",) + SD_FAMS             # every family that ARRIVES somewhere
FAM_TAG   = {"INT": "INTERSTATE", "MET": "MET", "REG": "REG"}
FAM_LABEL = {"INT": "Interstate", "MET": "Vic Metro to Metro",
             "REG": "Regional Vic to Metro Vic", "STG": "Kept at depot"}
SITES_FOR = {f: {c: [CODE_SITE[k] for k in _fam_sites(f, c)] for c in ("EP", "PP")}
             for f in ARR_FAMS}

def fam_sites(fam, cls):
    """The arrival sites this family reaches for this class — hubs and sorting depots."""
    return sorted(SITES_FOR[fam][cls])

def fam_classes(fam, site):
    """The classes with measured arrivals of this family at this site."""
    return [c for c in ("EP", "PP") if site in SITES_FOR[fam][c]]

def fam_any_sites(fam):
    """Every site this family reaches, in any class — the supplier and group loops want this."""
    return sorted({s for c in ("EP", "PP") for s in SITES_FOR[fam][c]})

def cls_hubs(cls):
    """Kept name, widened meaning: the ARRIVAL SITES (hubs and depots) for this class."""
    return fam_sites("INT", cls)

def hub_classes(site):
    """Kept name, widened meaning: the classes with measured interstate arrivals at this site."""
    return fam_classes("INT", site)

def arrival_split(cls):
    """Rough per-site arrival shares (equal-weight over depots) — prints only; the real volumes
    come from the joint applied to each depot's demand."""
    agg = {}
    for (p, c), m in OBS_JOINT_F.items():
        if c != cls:
            continue
        for t, v in m.items():
            if t.startswith("INT_"):
                agg[CODE_SITE[t[4:]]] = agg.get(CODE_SITE[t[4:]], 0.0) + v
    tot = sum(agg.values())
    return {s: v / tot for s, v in agg.items()}

# ── 21b — the interstate family is flavoured by ARRIVAL HUB, all the way to Delivered ───
# Round 1 happens where the freight lands, so the arrival hub IS the round-1 sort site and the
# existing `Despatch1_<hub>` flavour already carries it. What changes is that the flavour is no
# longer thrown away at round 2: Unloaded2 / Sorted2 / Despatch2 / Delivered keep it too.
def fam_tag(fam, site):
    """The origin flavour a parcel of this family carries out of this arrival site."""
    return f"{FAM_TAG[fam]}_{HUB_CODE[site]}"

def int_tag(site):
    return fam_tag("INT", site)

TAGS_FOR = {f: {c: [fam_tag(f, s) for s in fam_sites(f, c)] for c in ("EP", "PP")}
            for f in ARR_FAMS}
ALL_TAGS = {f: sorted({t for v in TAGS_FOR[f].values() for t in v}) for f in ARR_FAMS}
INT_TAGS_FOR, ALL_INT_TAGS = TAGS_FOR["INT"], ALL_TAGS["INT"]

def zone_tags_cls(pud, cls):
    """Origins a delivery zone under `pud` may be served from, FOR THIS CLASS (21b).

    Its own PDC's overnight stage, if it stages, plus one interstate flavour per hub that class
    arrives at. Everything else lodged in Melbourne left the state after one sort.
    """
    return [stage_tag_pud(pud)] + [t for f in ARR_FAMS for t in TAGS_FOR[f][cls]]

DEMAND_TAGS = STG_TAGS + [t for f in ARR_FAMS for t in ALL_TAGS[f]]
print("  Change 28 — arrival sites per class (measured; equal-weight shares for orientation):")
for _c in ("PP", "EP"):
    print(f"    INT {_c}: " + ", ".join(f"{HUB_CODE[s]} {v:.0%}"
          for s, v in sorted(arrival_split(_c).items(), key=lambda kv: -kv[1])))
    for _f in SD_FAMS:
        print(f"    {_f} {_c}: " + ", ".join(HUB_CODE[s] for s in fam_sites(_f, _c)))
print("  delivered flavours: "
      + " + ".join(f"{len(ALL_TAGS[f])} {FAM_LABEL[f]}" for f in ARR_FAMS)
      + f" + {len(STG_TAGS)} kept at depot")

# Change 23 — THE INTERSTATE UNLOAD MIX, MADE TO ACTUALLY BIND  → docs/chain2-observed.md#change-23-the-interstate-unload-mix
PRES_EQ   = {"ULD": "ULD", "LR": "LONGREACH"}          # presentation -> unload machine
INT_PRES  = {"ULD": INTERSTATE_ULD_SHARE, "LR": round(1 - INTERSTATE_ULD_SHARE, 6)}
FIXED_PRES = (INTERSTATE_UNLOAD == "fixed")
assert INTERSTATE_UNLOAD in ("fixed", "free", "bounded")
assert VIC_UNLOAD in ("free", "bounded"), \
    f"VIC_UNLOAD is free | bounded (there is no split VIC arrival product), got {VIC_UNLOAD!r}"
# Change 33: `bounded` is now real  → docs/chain2-observed.md#change-33-bounded-is-now-real
BOUNDED_PRES = (INTERSTATE_UNLOAD == "bounded")
VIC_BOUNDED  = (VIC_UNLOAD == "bounded")
if BOUNDED_PRES:
    _t = UNLOAD_MIX.loc[(UNLOAD_MIX.family == "INTERSTATE")
                        & (UNLOAD_MIX.method == "UNLOAD_ULD"), "target_share"]
    assert len(_t) == 1 and abs(float(_t.iloc[0]) - INTERSTATE_ULD_SHARE) < 1e-9, \
        (f"unload_mix.csv INTERSTATE/UNLOAD_ULD is {list(_t)} but dial INTERSTATE_ULD_SHARE is "
         f"{INTERSTATE_ULD_SHARE} — `fixed` and `bounded` must describe the same mix, or the two "
         f"modes silently model different networks")

INT_PRES_LIST = list(INT_PRES) if FIXED_PRES else [None]

def int_pk(cls, pres=None):
    """The interstate arrival product — presentation-flavoured only when the ratio is pinned."""
    return f"{cls}_INTERSTATE_{pres}_Pickup" if (FIXED_PRES and pres) else f"{cls}_INTERSTATE_Pickup"

print(f"  Change 23 — interstate unload mix: {INTERSTATE_UNLOAD.upper()}"
      + (f"  ({', '.join(f'{k} {v:.0%}' for k, v in INT_PRES.items())}, pinned by supplier "
         f"capacity — exact, no constraint)" if FIXED_PRES
         else "  (two competing recipes, solver picks on cost)"))

# ── CHANGE 26: the calibration, stated as a table ─────────────────────────────────────
# Every number this notebook changed, what it was, and where the new value comes from. Printed at
# build time so a run is self-documenting and nobody has to trust the markdown.
_CAL = [
    ("arrival routing", "CLASS_HUB_SPLIT", "measured per site",
     "the assumed hub split was retired with class_hub_split.csv (Change 47)"),
    ("INTERSTATE_BYPASS", "off", INTERSTATE_BYPASS,
     "interstate averages 1.11 Melbourne sorts; 90% sorted once"),
]
_CAL += [
    ("ENTITY SPLIT (28)", "one model, two chains", "chain 2 alone",
     "chain 1 runs separately under current assumptions; combined downstream"),
    ("ARRIVAL topology (28)", "3 hubs", "6 sites incl SWP/MNP/BAY",
     "48% of interstate first sorts at a delivery depot"),
    ("DELIVERY_SOURCING (28)", "interstate_plus_stage", "observed_mix",
     "measured per-depot mix: kept + interstate + metro + regional"),
    ("stage (28)", "3 depots, from local keep", "11 depots, measured EA",
     "kept-at-depot is 35-56% interstate-lodged"),
    ("cross-dock (28)", "not modelled", "XDock recipe + band" if XDOCK_ENABLED else "off",
     "19% of interstate is hub-handled, depot-sorted"),
    ("bypass share band  (C2)", "free", f"measured +/- {BYPASS_BAND:.0%}",
     "single-sort share per hub x class, FlowConstraints Min+Max"),
    ("DLC_SMALL_SHARE  (C3)", "0.15", f"{DLC_SMALL_SHARE}",
     "off-hub round-2 measured at 0.37% of delivered volume"),
]
print("Change 26 + 27 — chain-2 calibrated, then constrained, from the 20 May 2026 scan extract")
print(f"  {'parameter':<30}{'was':<20}{'now':<22}measured from")
for k, was, now, why in _CAL:
    print(f"  {k:<30}{was:<20}{now:<22}{why}")

# Things the scans measured that this notebook deliberately does NOT adopt, so the gap is visible
# in the build log rather than only in the markdown.
_NOT_ADOPTED = [
    ("the PICKUP side", "chain 1, separate notebook", "despatch split, LOCAL_SHARE and the pickup "
     "total are unmeasurable from a delivery-side extract; chain 1 keeps today's assumptions"),
]
print("\n  measured but NOT adopted here (see section 12):")
for k, cur, why in _NOT_ADOPTED:
    print(f"    {k:<30}{str(cur):<24}{why}")

# The three asserts that used to sit here all checked class_hub_split.csv, which no longer
# exists: two that its shares summed to 1 and one that the pickup pin covered every origin
# cluster. Arrivals are measured per site now, and the pickup side is chain 1's.

# --------------------------------------------------------------------------------------
# ## 1. Sizing — the Week 2 question
#
# The document's open question 5 asks for the combined size of both expansions before either is built.
# This computes it for every combination, so the trade-off is a number rather than an argument.
# --------------------------------------------------------------------------------------

print("sizing analysis superseded by Change 28 — the entity split changes every row count;")
print("see the per-table prints as the build runs, and the summary in section 11")

print("(second-round economics: see the solved run — the bands price it now)")

# --------------------------------------------------------------------------------------
# ## 2. Source data
# --------------------------------------------------------------------------------------

parcels = pd.read_csv(RAW / "temp_clustered.csv",
                      usecols=["cluster_id", "facility_id", "Product_type", "parcel_count"])
parcels["product"] = parcels["Product_type"].map(PRODUCT_MAP)
parcels["service"] = parcels["Product_type"].map(SERVICE_OF_CLASS)
assert parcels["product"].notna().all()

clusters = pd.read_csv(RAW / "cluster_summary.csv",
                       usecols=["cluster_id", "centroid_lat", "centroid_lon"])
clusters["customername"] = "CZ_" + clusters["cluster_id"].apply(
    lambda x: f"{DEPOT_TO_PUD_SHORT[x.split('_')[0]]}_{x.split('_')[1]}")
clusters["pud"] = ("Depot_" + clusters["cluster_id"].str.split("_").str[0].str[1:]).map(DEPOT_TO_PUD)
assert clusters["pud"].notna().all()

def n_catchments(path):
    """Feature count of a GeoJSON FeatureCollection — what len(gpd.read_file(path)) returned."""
    with open(path, encoding="utf-8") as fh:
        return len(json.load(fh).get("features", []))
N_CATCHMENTS = n_catchments(
    RAW / "first_mile_manifest_catchment_include_transport_facility.geojson")
nodes = pd.ExcelFile(RAW / "all-data.xlsx").parse("nodes")
# Change 30: sites the source workbook has no row for (Avalon) carry their coordinates in
# inputs/factors_assumed/sort_only_sites.csv, flagged there as an assumption.
_extra = (SORT_ONLY[SORT_ONLY.lat.notna()][["node_label", "lat", "long"]]
          .rename(columns={"node_label": "Facility"}))
nodes = pd.concat([nodes[~nodes.Facility.isin(_extra.Facility)], _extra], ignore_index=True)
print(f"  Change 30 sort-only sites: {sorted(SORT_ONLY_PUDS)}"
      f"  ({len(_extra)} with assumed coordinates)")
print(f"  {len(parcels):,} parcel rows | {len(clusters):,} delivery zones | {N_CATCHMENTS} catchments")
print("  service classes in the source data:",
      parcels.groupby("service")["parcel_count"].sum().to_dict())

# ── Facilities ─────────────────────────────────────────────────────────────
fac = nodes.copy()
fac["facilityname"] = fac["Facility"].map(NODE_TO_CANON)
assert fac["facilityname"].notna().all()
fac["role"] = np.where(fac["facilityname"].isin(HUB_SET), "HUB", "PUD")
facilities = pd.DataFrame({
    "facilityname": fac["facilityname"], "status": "Include", "facilitystatus": "Open",
    "initialstate": "Existing", "address": "", "city": "", "region": "", "postalcode": "",
    "country": "Australia", "latitude": fac["lat"].round(6), "longitude": fac["long"].round(6),
    "fixedstartupcost": "",
    "fixedoperatingcost": np.where(fac["role"].eq("HUB"), "Fixed_Cost_HUB", "Fixed_Cost_PUD"),
    "fixedclosingcost": "", "storagecapacity": "", "storagecapacityuom": "",
    "throughputcapacity": "", "throughputcapacityuom": "",
    "geographicriskscore": "", "userdefinedriskscore": "", "notes": fac["role"],
    "fixedco2emissions": "",
})
_src = facilities.set_index("facilityname")[["latitude", "longitude"]]
facilities = pd.concat([facilities, pd.DataFrame([{
    "facilityname": canon, "status": "Include", "facilitystatus": "Open", "initialstate": "Existing",
    "address": "", "city": "", "region": "", "postalcode": "", "country": "Australia",
    "latitude": _src.loc[FM_ONLY_COORD_SOURCE[canon], "latitude"],
    "longitude": _src.loc[FM_ONLY_COORD_SOURCE[canon], "longitude"],
    "fixedstartupcost": "", "fixedoperatingcost": "Fixed_Cost_PUD", "fixedclosingcost": "",
    "storagecapacity": "", "storagecapacityuom": "", "throughputcapacity": "",
    "throughputcapacityuom": "", "geographicriskscore": "", "userdefinedriskscore": "",
    "notes": f"PUD (first-mile transport facility; coords from {FM_ONLY_COORD_SOURCE[canon]})",
    "fixedco2emissions": "",
} for canon in FM_ONLY_PUD_NODES.values()])], ignore_index=True)

# Change 16 (2026-07-29): this table was BUILT AND NEVER WRITTEN — the model shipped without
# Facilities.csv, which every other table references. Written here so it always exists, then
# rewritten below once the throughput caps can be computed.
write_csv(facilities, "Facilities")

# Doc section 1.2 raises a reconciliation point on 17 facilities vs 16 buildings. Check it directly.
_co = []
for i, a in facilities.iterrows():
    for j, b in facilities.iterrows():
        if i < j and abs(a.latitude - b.latitude) < 0.005 and abs(a.longitude - b.longitude) < 0.005:
            _co.append((a.facilityname, b.facilityname,
                        round(111 * math.hypot(a.latitude - b.latitude, a.longitude - b.longitude), 2)))
print(f"  {len(facilities)} facility records. Co-located pairs (km apart):")
for a, b, d in _co:
    print(f"    {a:<26} {b:<26} {d:>5} km")
print("  The document is right that Tullamarine hub and Tullamarine PDC are the same site (60 m).")
print("  The two transport facilities sit on borrowed coordinates, so those pairs are our own artefact.")

# --------------------------------------------------------------------------------------
# ## 3. Products
#
# Five states with one hub sort round; eight with two, because the round-1 despatch state is
# **flavoured by the hub that produced it** and the second round adds its own unload/sort/despatch.
# The pickup state carries the origin tag — **and** lodgement type when `LODGEMENT_SPLIT`
# is on, in which case it collapses at unload. It is currently off, so pickup carries origin only. Interstate is its own family carrying the 27-hour linehaul floor. The origin PDC's round-0
# states stay numbered so local-keep volume cannot be confused with hub-sorted volume. With
# `ORIGIN_TAG_TO_DEMAND` on, `Delivered` carries the tag too, which is what closes the path at the sink.
# --------------------------------------------------------------------------------------

PRODUCT_COLS = ["productname", "status", "unitvolume", "unitweight", "notes"]
CLASSES = ["EP", "PP"]
LODGE = ["IND", "RES"] if LODGEMENT_SPLIT else ["ALL"]
TWO_ROUNDS = HUB_SORT_ROUNDS == 2

# Change 25: the bypass is a second RECIPE for a product that already exists
# (`<cls>_INTERSTATE_<hub>_Despatch2`), so it adds no products at all — only a BOM, a
# production policy at the arrival hub, and the lanes out of it.
assert INTERSTATE_BYPASS in ("off", "free", "min_truckload"), \
    f"INTERSTATE_BYPASS must be off | free | min_truckload, got {INTERSTATE_BYPASS!r}"
BYPASS = INTERSTATE_BYPASS != "off"
assert not (BYPASS and not TWO_ROUNDS), \
    "INTERSTATE_BYPASS needs HUB_SORT_ROUNDS = 2 — with one round there is no second sort to skip"

PICKUP_PREFIXES = tuple(f"{c}_{t}_" for c in CLASSES for t in PICKUP_TAGS)

def pk(cls, tag, lod):      return f"{cls}_{tag}_{lod}_Pickup" if LODGEMENT_SPLIT else f"{cls}_{tag}_Pickup"
def st(cls, tag, state):    return f"{cls}_{tag}_{state}"
def delivered(cls, tag):    return st(cls, tag, "Delivered")
def dsp1(cls, tag, code):   return f"{cls}_{tag}_Despatch1_{code}"
def dsp_final(cls, tag):    return st(cls, tag, "Despatch2" if TWO_ROUNDS else "Despatch")
def dsp_out(cls, tag, hub): return dsp1(cls, tag, HUB_CODE[hub]) if TWO_ROUNDS else st(cls, tag, "Despatch")

ROUND2_STATES = ["Unloaded2", "Sorted2", "Despatch2"] if TWO_ROUNDS else ["Despatch"]

# ── THREE FAMILIES THAT SHARE NO STATE PAST THE PICKUP DOCK ───────────────────────────
prod_rows = []
for cls in CLASSES:
    # 1. PICKUP — moved to its own entity (chain-1 notebook). No pickup product exists here.
    # 2. INTERSTATE — arrivals. Change 21b: the ARRIVAL HUB is carried to the delivery zone.
    #    Upstream states need no hub tag: the freight is physically at its arrival hub, and
    #    round 1 happens there, so `Despatch1_<hub>` is where the flavour is first named.
    for _pres in INT_PRES_LIST:
        prod_rows.append([int_pk(cls, _pres), "Include", "", "",
                          f"interstate arrival ({'/'.join(HUB_CODE[h] for h in cls_hubs(cls))})"
                          + (f", presented on {_pres} — one unload recipe, so the mix is exact"
                             if _pres else ", presentation is a solver choice")])
    for s in ["Unloaded", "Sorted"]:
        prod_rows.append([st(cls, "INTERSTATE", s), "Include", "", "", f"interstate {s} (round 1)"])
    for h in cls_hubs(cls):
        prod_rows.append([dsp1(cls, "INTERSTATE", HUB_CODE[h]), "Include", "", "",
                          f"round-1 despatch, arrived and sorted at {HUB_CODE[h]}"])
        it = int_tag(h)
        for s in ROUND2_STATES:
            prod_rows.append([st(cls, it, s), "Include", "", "",
                              f"{s} — still carrying its {HUB_CODE[h]} arrival origin"])
        prod_rows.append([delivered(cls, it), "Include", "", "",
                          f"delivered, arrived interstate at {HUB_CODE[h]}"])
    # 2b. THE TWO VICTORIAN BANDS (Change 46) — one chain each, and they are separate
    #     journeys, not one "same-day" family: METRO is lodged and delivered inside the metro
    #     catchment; REGION is lodged in regional Victoria and railed in. Both mirror the
    #     interstate chain — enter at the measured first-sort site, sorted there, carry that
    #     site as the flavour to the zone.
    for _f in SD_FAMS:
        _fl = FAM_LABEL[_f]
        prod_rows.append([f"{cls}_{FAM_TAG[_f]}_Pickup", "Include", "", "",
                          f"{_fl} lodgement, supplied at its measured first-sort site"])
        for s in ["Unloaded", "Sorted"]:
            prod_rows.append([st(cls, FAM_TAG[_f], s), "Include", "", "",
                              f"{_fl} {s} (round 1)"])
        for h in fam_sites(_f, cls):
            prod_rows.append([dsp1(cls, FAM_TAG[_f], HUB_CODE[h]), "Include", "", "",
                              f"round-1 despatch, {_fl}, sorted at {HUB_CODE[h]}"])
            vt = fam_tag(_f, h)
            for s in ROUND2_STATES:
                prod_rows.append([st(cls, vt, s), "Include", "", "",
                                  f"{s} — {_fl}, first sorted at {HUB_CODE[h]}"])
            prod_rows.append([delivered(cls, vt), "Include", "", "",
                              f"delivered, {_fl} via {HUB_CODE[h]}"])
    # 2c. CROSS-DOCK (Change 28, 12b) — a hub worked it but did not sort it. A despatch-class
    #     state, flavoured by the HANDLING hub; the flavour dissolves at the depot sort.
    if XDOCK_ENABLED:
        for h in sorted(HUB_SET):
            prod_rows.append([f"{cls}_INTERSTATE_XDock_{HUB_CODE[h]}", "Include", "", "",
                              f"cross-dock: handled at {HUB_CODE[h]}, unsorted, bound for a "
                              f"depot sorter"])
    # 3. STAGE — yesterday's keep, delivery-ready. Change 28: one per DELIVERING DEPOT (11),
    #    measured EA, decoupled from local pickup.
    for t in STG_TAGS:
        prod_rows.append([dsp_final(cls, t), "Include", "", "",
                          f"local stage at the {t[4:]} PDC — supplied delivery-ready"])
        prod_rows.append([delivered(cls, t), "Include", "", "", f"delivered from the {t[4:]} local stage"])

products = pd.DataFrame(prod_rows, columns=PRODUCT_COLS).drop_duplicates("productname")
write_csv(products, "Products")

_bad = [p for p in products.productname if p.startswith(PICKUP_PREFIXES)
        and any(p.endswith(s) for s in ("Delivered", "Despatch2", "Unloaded2", "Sorted2"))]
assert not _bad, f"a pickup-origin product owns a delivery-side state: {sorted(set(_bad))[:6]}"
print(f"  {len(products)} products — "
      + ", ".join(f"{FAM_LABEL[f]} {len(ALL_TAGS[f])}" for f in ARR_FAMS)
      + f" site flavours, kept at depot {len(STG_TAGS)}, pickup 0 (chain 1)")

# --------------------------------------------------------------------------------------
# ## 4. Customers and demand
#
# Delivery zones split **by origin**, which is what makes the source-to-sink path binding. The origin
# split comes from the matrix built in 4b.
#
# Service classes and the eligible-window mechanism are off with the periods (Change 13): a promise
# binds through a transit band *and* a delivery window, and with one period there is no window to
# restrict. Turn `N_PERIODS` and `SERVICE_CLASSES` back on together to restore it.
#
# Three sinks, all origin-tagged: delivery zones (`*_Delivered`), interstate despatch at the hubs
# (`*_Despatch`, locally lodged volume leaving Melbourne) and the local-terminate sinks at the sorting
# PDCs (`*_Sort0`, kept on site and staged for tomorrow).
# --------------------------------------------------------------------------------------

# ── Customers ──────────────────────────────────────────────────────────────
CUST_COLS = ["customername", "address", "status", "city", "region", "postalcode", "country",
             "latitude", "longitude", "notes", "singlesource", "geographicriskscore",
             "userdefinedriskscore"]

def _cust(name, lat, lon, note):
    return [name, "", "Include", "", "", "", "Australia", round(lat, 6), round(lon, 6), note, "", "", ""]

cust_rows = [_cust(r.customername, r.centroid_lat, r.centroid_lon, "delivery zone")
             for r in clusters.itertuples()]
_geo = facilities.set_index("facilityname")
# Change 28: the CZ_Interstate_* and CZ_LocalTerm_* sinks belong to chain 1's entity.
customers = pd.DataFrame(cust_rows, columns=CUST_COLS)
write_csv(customers, "Customers")

# ── Volume balance, same identity as the current model ─────────────────────
# Change 13: group by service only when service classes are on — otherwise the ND and STD rows for
# one zone stay separate and collide on (customer, product, period) once the origin split is applied.
_grp = ["cluster_id", "product"] + (["service"] if SERVICE_CLASSES else [])
base = parcels.groupby(_grp, as_index=False)["parcel_count"].sum()
if not SERVICE_CLASSES:
    base["service"] = "STD"
base["customername"] = "CZ_" + base["cluster_id"].apply(
    lambda x: f"{DEPOT_TO_PUD_SHORT[x.split('_')[0]]}_{x.split('_')[1]}")
base = base.merge(clusters[["customername", "pud"]], on="customername", how="left")
assert base["pud"].notna().all()

# CHANGE 29 — the model carries the MEASURED day, not a blend of…  → docs/chain2-observed.md#change-29-the-model-carries-the
if dial("DEMAND_BASIS") == "observed_peak_day":
    _before, _blocks = int(base["parcel_count"].sum()), []
    for (_p, _c), _blk in base.groupby(["pud", "product"], sort=False):
        _want, _have = OBS_DEMAND.get((_p, _c)), int(_blk["parcel_count"].sum())
        if _want is None or _have == 0:
            _blocks.append(_blk)
            continue
        _raw = _blk["parcel_count"] * _want / _have
        _fl = np.floor(_raw).astype(int)
        for _i in (_raw - _fl).sort_values(ascending=False).index[:_want - int(_fl.sum())]:
            _fl[_i] += 1
        _blk = _blk.copy()
        _blk["parcel_count"] = _fl
        _blocks.append(_blk)
    base = pd.concat(_blocks).loc[base.index]
    base = base[base["parcel_count"] > 0].reset_index(drop=True)
    assert int(base["parcel_count"].sum()) == sum(OBS_DEMAND.values()), \
        "the rescale did not land on the measured total"
    print(f"  Change 29: demand rebased on the measured day — {_before:,} EA (zone table, every "
          f"delivery date in the extract) -> {int(base['parcel_count'].sum()):,} EA (peak day, "
          f"the cohort every observed factor is measured on)")

D_TOTAL = int(base["parcel_count"].sum())

# ══ CHANGE 28 — chain 2's volumes are MEASURED; there is no pickup in this entity ══════
# Per (depot, class): the floored scan joint applied to the model's own demand, integerised by
# largest remainder so every row sums exactly to that depot's demand. The columns are the three
# supplier families: STG_<depot>, INT_<site>, VIC_<site>. The balance is exact by construction:
#     stage + interstate + Victoria same-day = D
D_pud_class = (base.groupby(["pud", "product"])["parcel_count"].sum()
                   .astype(int).unstack(fill_value=0))

def _row_alloc(total, shares):
    keys = sorted(shares)
    raw = [total * shares[k] for k in keys]
    out = [int(math.floor(x)) for x in raw]
    for _, i in sorted(((raw[i] - out[i], i) for i in range(len(keys))), reverse=True)[:total - sum(out)]:
        out[i] += 1
    return dict(zip(keys, out))

MIX = {}                     # (pud, tag, cls) -> EA, the origin-mix matrix, fully measured
for p in sorted(DELIVERY_PUD_SET):
    for c in CLASSES:
        row = _row_alloc(int(D_pud_class.loc[p, c]), OBS_JOINT_F[(p, c)])
        for t, v in row.items():
            tag = stage_tag_pud(p) if t == "STG" else \
                  (("INTERSTATE_" + t[4:]) if t.startswith("INT_") else t)
            MIX[(p, tag, c)] = MIX.get((p, tag, c), 0) + v

STAGE_BY_PUD = {(p, c): MIX.get((p, stage_tag_pud(p), c), 0)
                for p in sorted(DELIVERY_PUD_SET) for c in CLASSES}
STAGE_TOT = sum(STAGE_BY_PUD.values())
IN_by_site_class = {(s, c): sum(MIX.get((p, int_tag(s), c), 0) for p in sorted(DELIVERY_PUD_SET))
                    for c in CLASSES for s in cls_hubs(c)}
# Change 46: one volume table per arriving family. VIC_TOT survives as the two Victorian
# bands added together — the balance identity and the sizing prints are about "not
# interstate and not kept", which is still one quantity even though it is now two families.
VOL = {"INT": IN_by_site_class}
for _f in SD_FAMS:
    VOL[_f] = {(s, c): sum(MIX.get((p, fam_tag(_f, s), c), 0)
                           for p in sorted(DELIVERY_PUD_SET))
               for c in CLASSES for s in fam_sites(_f, c)}
FAM_TOT = {f: sum(VOL[f].values()) for f in ARR_FAMS}
IN_TOT  = FAM_TOT["INT"]
VIC_TOT = sum(FAM_TOT[f] for f in SD_FAMS)
IN_by_hub_class = IN_by_site_class            # legacy name, kept for downstream cells
V_by_origin = {}
for c in CLASSES:
    for p in sorted(DELIVERY_PUD_SET):
        V_by_origin[(stage_tag_pud(p), c)] = STAGE_BY_PUD[(p, c)]
    for s in cls_hubs(c):
        V_by_origin[(int_tag(s), c)] = IN_by_site_class[(s, c)]
    for _f in SD_FAMS:
        for s in fam_sites(_f, c):
            V_by_origin[(fam_tag(_f, s), c)] = VOL[_f][(s, c)]

# ── the CROSS-DOCK split of the interstate supply — Change 29: the measured matrix ────
# A cross-docked parcel is counted in the joint under the site that SORTED it. Physically it was
# handled somewhere else first, so its supply moves to the handling site and the XDock recipe
# carries it back. Change 28 attributed that handling with a 3:1 weight toward MPF; the scans
# give the pair directly (obs_recv_entry.csv), and they say the handling is almost entirely MPF's
# — half of everything MPF touches is sorted at another site.
XDOCK_PAIR = {}      # (handling site, sorting site, cls) -> EA on that lane
XDOCK_AT   = {}      # (sorting site, cls)  -> EA sorted there that was handled elsewhere
XDOCK_HAND = {}      # (handling site, cls) -> EA handled there and forwarded unsorted
if XDOCK_ENABLED:
    for c in CLASSES:
        for e in cls_hubs(c):
            _cells = {r: a for (_c2, r, _e2), a in OBS_RECV_ENTRY.items()
                      if _c2 == c and _e2 == HUB_CODE[e] and r in CODE_SITE}
            if not _cells or sum(_cells.values()) == 0:
                continue
            # measured shares of "who handled the freight this site sorts", applied to this
            # site's own measured arrivals so the two always add up
            _keys = sorted(_cells)
            for _r, _v in zip(_keys, split_int(IN_by_site_class[(e, c)],
                                               [_cells[k] for k in _keys])):
                if _r == HUB_CODE[e] or _v == 0:
                    continue
                XDOCK_PAIR[(CODE_SITE[_r], e, c)] = _v
                XDOCK_AT[(e, c)] = XDOCK_AT.get((e, c), 0) + _v
                XDOCK_HAND[(CODE_SITE[_r], c)] = XDOCK_HAND.get((CODE_SITE[_r], c), 0) + _v
    assert sum(XDOCK_AT.values()) == sum(XDOCK_HAND.values()), "cross-dock in != out"
    assert all(XDOCK_AT[(s, c)] <= IN_by_site_class[(s, c)] for (s, c) in XDOCK_AT), \
        "a site would cross-dock in more than it sorts"
XD_TOT = sum(XDOCK_AT.values())

print("  interstate arrivals by site x class (measured):  "
      + ", ".join(f"{HUB_CODE[s]}/{c} {v:,}" for (s, c), v in sorted(
          IN_by_site_class.items(), key=lambda kv: (HUB_CODE[kv[0][0]], kv[0][1]))))
for _f in SD_FAMS:
    print(f"  {FAM_LABEL[_f]} by site x class (measured):  "
          + ", ".join(f"{HUB_CODE[s]}/{c} {v:,}" for (s, c), v in sorted(
              VOL[_f].items(), key=lambda kv: (HUB_CODE[kv[0][0]], kv[0][1]))))
if XDOCK_ENABLED:
    _hand = {}
    for (h, _c2), v in XDOCK_HAND.items():
        _hand[h] = _hand.get(h, 0) + v
    _at = {}
    for (s2, _c2), v in XDOCK_AT.items():
        _at[s2] = _at.get(s2, 0) + v
    print(f"  cross-dock: {XD_TOT:,} EA — handled at "
          + ", ".join(f"{HUB_CODE[h]} {v:,}" for h, v in sorted(_hand.items()) if v)
          + "; sorted at "
          + ", ".join(f"{HUB_CODE[s]} {v:,}" for s, v in sorted(_at.items()) if v))

assert STAGE_TOT + IN_TOT + VIC_TOT == D_TOTAL, "chain-2 balance broken"
print(f"  D {D_TOTAL:,} = " + " + ".join(
    [f"{FAM_LABEL['STG']} {STAGE_TOT:,} ({100*STAGE_TOT/D_TOTAL:.1f}%)"]
    + [f"{FAM_LABEL[f]} {FAM_TOT[f]:,} ({100*FAM_TOT[f]/D_TOTAL:.1f}%)" for f in ARR_FAMS])
      + "   — exact, measured, no free parameter")

# ── PDC throughput capacity (Change 16, chain-2 load only) ────────────────────────────
_dem_pud = base.groupby("pud")["parcel_count"].sum()
_arr_pud = {p: sum(sum(VOL[f].get((p, c), 0) for f in ARR_FAMS)
                   + XDOCK_AT.get((p, c), 0) for c in CLASSES)
            for p in sorted(DELIVERY_PUD_SET)}
# ── Change 45 (2026-08-28): two flows the load was blind to ──────────────────────────
# A NEO infeasibility run bit here — PUD_Melbourne_North wanted 34,198 against a cap of 31,677,
# PUD_Bayswater 38,988 against 38,950 — and the cap is supposed to lift itself to whatever the
# model needs. It was lifting to the wrong number because this sum hand-lists the flows through a
# building and the list was short by two:
#
#   ROUND-2 INBOUND   three of these depots (SWP, MNP, BAY) are also SORT SITES, so another site
#                     can send them freight for its second sort. That volume is not the depot's
#                     own demand and not an arrival of its own — it is a third thing, and at
#                     Melbourne North it is 6,076 EA, which is the whole of that violation. It
#                     grew with Change 44: the facility-path basis measures more building-to-
#                     building lanes than the role basis did, and FOLD_MIN_ARTICLES at 200
#                     rather than 1,300 keeps far more of them.
#   STAGE             the overnight pile is injected AT the depot by SUP_STAGE_<depot> and then
#                     despatched from it. It is inside `demand` as an origin tag, but the
#                     facility sees it as its own flow.
#
# Bayswater keeps a 937 EA residual that neither term explains and that this notebook's own
# quantities cannot source — NEO counts something here we are not reproducing exactly. The
# headroom absorbs it (the cap lands 966 EA above the activity), so if a later run bites at
# Bayswater again the dial to move is FACILITY_HEADROOM, not this formula.
_stg_pud = {p: sum(STAGE_BY_PUD.get((p, c), 0) for c in CLASSES)
            for p in sorted(DELIVERY_PUD_SET)}
def _round2_inbound(depot):
    """What other sites may send this depot for its SECOND sort. 0 unless it is a sort site."""
    code = HUB_CODE.get(depot)
    if code is None:
        return 0.0
    t = 0.0
    for (_fam, _cls, _e, _dest), _sh in OBS_LEGS.items():
        if _dest != code or _e == code:
            continue
        _src = CODE_SITE[_e]
        t += _sh * VOL.get(_fam, {}).get((_src, _cls), 0)
    return t
_r2_pud = {p: _round2_inbound(p) for p in sorted(DELIVERY_PUD_SET)}
_lifted, _reason = {}, {}
print(f"  facility load check (delivery + arrivals + stage + round-2 inbound vs PUD_CAPACITY, "
      f"+{FACILITY_HEADROOM:.0%} headroom):")
for d in sorted(DELIVERY_PUD_SET):
    load = (float(_dem_pud.get(d, 0)) + float(_arr_pud.get(d, 0))
            + float(_stg_pud.get(d, 0)) + float(_r2_pud.get(d, 0)))
    cap  = PUD_CAPACITY.get(d, 0)
    want = math.ceil(load * (1 + FACILITY_HEADROOM))
    if want > cap:
        _lifted[d], _reason[d] = want, ("over ops cap" if load > cap else "headroom only")
        _extra = ("" if not _r2_pud.get(d) else
                  f", of which {_r2_pud[d]:,.0f} is another site's second sort")
        print(f"    {d:<26} {load:>9,.0f}  vs cap {cap:>7,}  -> LIFTED to {want:,}  "
              f"({_reason[d]}{_extra})")
    else:
        _lifted[d] = cap
        print(f"    {d:<26} {load:>9,.0f}  vs cap {cap:>7,}  ({100 * load / cap:.0f}% util)")
for _c in ("throughputcapacity", "throughputcapacityuom"):
    facilities[_c] = facilities[_c].astype(object)
_m = facilities["facilityname"].isin(_lifted)
facilities.loc[_m, "throughputcapacity"]    = facilities.loc[_m, "facilityname"].map(_lifted)
facilities.loc[_m, "throughputcapacityuom"] = "EA"
write_csv(facilities, "Facilities")     # rewrite, now with the caps

# --------------------------------------------------------------------------------------
# ## 4b. The origin mix — which origin serves which PDC
#
# Carrying the origin tag into the demand row asserts **which origin serves which delivery zone**. That
# is genuine origin-destination information and we do not have it, so it is supplied here as an explicit,
# editable assumption rather than left absent.
#
# The matrix is **not free-form**. It has to reconcile on three counts at once or the model is infeasible:
#
# | | constraint | why |
# |---|---|---|
# | row | `sum over origins = that PDC's delivery demand` | every parcel delivered came from somewhere |
# | column | `sum over PDCs = V_origin = P − OUT − L + stage` | an origin cannot deliver more than it lodged, net of what left interstate and what it kept |
# | pinned cell | `M[p][own origin] >= that PDC's staged volume` | **local stage**: yesterday's own kept pickup is delivery-ready on site this morning, so Bayswater must deliver at least its own staged volume of Bayswater-origin parcels |
#
# The default seed is a **gravity prior** — nearer origins supply more of a PDC's volume, decaying with
# `ORIGIN_MIX_DECAY_KM` — reconciled to all three by iterative proportional fitting. The result is written
# to `OriginMix.csv` as a percentage table.
#
# **To replace it with real percentages:** edit that file, save it as `inputs/melbourne/origin_mix_override.csv`,
# re-run. Your percentages are treated as a *target*: they are reconciled to the same three margins and the
# largest adjustment is reported, so an edit that cannot be supplied is visible rather than silently wrong.
# --------------------------------------------------------------------------------------

# ── ORIGIN MIX: how much of each PDC's delivery volume comes from each origin ──────────
# The matrix behind every tagged demand row. Row margin = PDC delivery demand, column margin =
# that origin's deliverable volume V, and the local-stage cells are pinned. See the note above.

def _ipf(seed, row_tot, col_tot, iters=200, tol=1e-9):
    """Scale `seed` to hit both margins (iterative proportional fitting)."""
    R, C = len(row_tot), len(col_tot)
    M = [[max(float(seed[i][j]), 0.0) for j in range(C)] for i in range(R)]
    for _ in range(iters):
        worst = 0.0
        for i in range(R):
            s = sum(M[i])
            if s > 0:
                f = row_tot[i] / s
                worst = max(worst, abs(f - 1))
                for j in range(C):
                    M[i][j] *= f
        for j in range(C):
            s = sum(M[i][j] for i in range(R))
            if s > 0:
                f = col_tot[j] / s
                worst = max(worst, abs(f - 1))
                for i in range(R):
                    M[i][j] *= f
        if worst < tol:
            break
    return M

def alloc2d(row_tot, col_tot, seed):
    """Integer matrix hitting BOTH margins exactly: IPF, floor, then hand out the remainders
    largest-fraction-first to cells whose row and column both still owe units."""
    assert sum(row_tot) == sum(col_tot), f"margins differ: {sum(row_tot)} vs {sum(col_tot)}"
    R, C = len(row_tot), len(col_tot)
    M = _ipf(seed, row_tot, col_tot)
    A = [[int(math.floor(M[i][j])) for j in range(C)] for i in range(R)]
    rrem = [row_tot[i] - sum(A[i]) for i in range(R)]
    crem = [col_tot[j] - sum(A[i][j] for i in range(R)) for j in range(C)]
    for _, i, j in sorted(((M[i][j] - A[i][j], i, j) for i in range(R) for j in range(C)),
                          reverse=True):
        if rrem[i] > 0 and crem[j] > 0:
            A[i][j] += 1; rrem[i] -= 1; crem[j] -= 1
    while any(r > 0 for r in rrem):          # degenerate leftovers (zero-seed cells)
        i = next(i for i, r in enumerate(rrem) if r > 0)
        j = next(j for j, c in enumerate(crem) if c > 0)
        A[i][j] += 1; rrem[i] -= 1; crem[j] -= 1
    return A

MIX_PUDS = sorted(DELIVERY_PUD_SET)
# CHANGE 28: nothing to fit and nothing to derive — the matrix WAS BUILT from the measured joint
# in the balance cell. This cell verifies its margins and writes it. The gravity seed, the
# override file and the interstate_plus_stage derivation are retired with the rule they served.
MIX_ORIGINS_FOR = {c: STG_TAGS + [t for f in ARR_FAMS for t in TAGS_FOR[f][c]]
                   for c in CLASSES}
MIX_ORIGINS = STG_TAGS + [t for f in ARR_FAMS for t in ALL_TAGS[f]]
for p in MIX_PUDS:
    for t in MIX_ORIGINS:
        for c in CLASSES:
            MIX.setdefault((p, t, c), 0)

for c in CLASSES:
    for p in MIX_PUDS:
        assert sum(MIX[(p, t, c)] for t in MIX_ORIGINS) == int(D_pud_class.loc[p, c]), \
            f"row margin broken at {p} {c}"
        assert MIX[(p, stage_tag_pud(p), c)] == STAGE_BY_PUD[(p, c)], f"stage cell moved at {p} {c}"
        for p2 in MIX_PUDS:
            if p2 != p:
                assert MIX[(p2, stage_tag_pud(p), c)] == 0, f"{p}'s stage appears at {p2}"
    for t in [x for x in MIX_ORIGINS if x not in MIX_ORIGINS_FOR[c]]:
        assert all(MIX[(p, t, c)] == 0 for p in MIX_PUDS), \
            f"{c} volume landed in {t}, a column its class cannot use"

mix_tot = {(p, t): sum(MIX[(p, t, c)] for c in CLASSES) for p in MIX_PUDS for t in MIX_ORIGINS}
origin_mix = pd.DataFrame(
    [[p] + [round(100 * mix_tot[(p, t)] / max(sum(mix_tot[(p, u)] for u in MIX_ORIGINS), 1), 2)
            for t in MIX_ORIGINS] for p in MIX_PUDS],
    columns=["pud"] + MIX_ORIGINS)
write_csv(origin_mix, "OriginMix")

print("  origin mix, MEASURED (% of each PDC's delivery volume) — three families summarised:")
print("    " + "PDC".ljust(24) + "stage".rjust(8) + "interstate".rjust(12) + "victoria".rjust(10))
for p in MIX_PUDS:
    tot = max(sum(mix_tot[(p, u)] for u in MIX_ORIGINS), 1)
    stg = 100 * mix_tot[(p, stage_tag_pud(p))] / tot
    intl = 100 * sum(mix_tot[(p, t)] for t in ALL_INT_TAGS) / tot
    vic = 100 * sum(mix_tot[(p, t)] for f in SD_FAMS for t in ALL_TAGS[f]) / tot
    print(f"    {p.replace('PUD_', ''):<24}{stg:>7.1f}%{intl:>11.1f}%{vic:>9.1f}%")

# ── CustomerDemand: zone x class x service x period x ORIGIN ──────────────
# Change 12: each delivery row is split by origin using its PDC's row of the origin mix. That split
# is what forces the source-to-sink path — a zone asking for 400 EA of PP_OUTER_EAST_Delivered can
# only be satisfied by parcels that entered the network as PP_OUTER_EAST_*_Pickup. No routing
# constraint is needed; flow conservation does the rest.
DEM_COLS = ["customername", "productname", "periodname", "quantity", "status", "notes",
            "seasontype", "zonetype"]
dem_rows = []

def _svc_periods(svc):
    return [p for p in SERVICE_DEF[svc][1] if p in PERIODS] or [PERIODS[0]]

def split_int(total, weights):
    """Integer split of `total` in proportion to `weights`, largest remainder, sums exactly."""
    tw = sum(weights)
    if tw <= 0 or total == 0:
        return [total if i == 0 else 0 for i in range(len(weights))]
    raw = [total * w / tw for w in weights]
    out = [int(math.floor(x)) for x in raw]
    for _, i in sorted(((raw[i] - out[i], i) for i in range(len(raw))), reverse=True)[:total - sum(out)]:
        out[i] += 1
    return out

# Step 1: zone x class x service -> periods, preserving the zone total exactly (the old
# int(round(...)) per period did not, which would have left the origin margins unsatisfiable).
zone_rows = []          # (pud, class, customername, service, period, qty)
for r in base.itertuples():
    svc = r.service if SERVICE_CLASSES else "STD"
    elig = _svc_periods(svc)
    qs = split_int(int(r.parcel_count), [PERIOD_SPLIT["delivery"][p] for p in elig])
    for p, q in zip(elig, qs):
        if q:
            zone_rows.append((r.pud, r.product, r.customername, svc, p, q))

# Step 2: within each PDC and class, allocate those rows across origins so that BOTH margins hold —
# every zone row keeps its own total, and every origin column sums to what the mix gave that PDC.
for pud in sorted(DELIVERY_PUD_SET):
    for c in CLASSES:
        rows = [z for z in zone_rows if z[0] == pud and z[1] == c]
        if not rows:
            continue
        row_tot = [z[5] for z in rows]
        col_tot = [MIX[(pud, t, c)] for t in MIX_ORIGINS]
        if sum(row_tot) != sum(col_tot):        # period rounding can move a parcel or two
            col_tot = split_int(sum(row_tot), [max(v, 1e-9) for v in col_tot])
        A = alloc2d(row_tot, col_tot, [[max(v, 1e-9) for v in col_tot] for _ in rows])
        for k, z in enumerate(rows):
            for j, tag in enumerate(MIX_ORIGINS):
                if A[k][j]:
                    dem_rows.append([z[2], delivered(c, tag), z[4], A[k][j], "Include",
                                     f"delivery demand, {z[3]}, {tag} origin", "Standard", ""])

# Change 28: the despatch and local-terminate sinks belong to chain 1's entity.
demand = pd.DataFrame(dem_rows, columns=DEM_COLS)
write_csv(demand, "CustomerDemand")

# ── the demand side must reproduce the volumes the balance was built on ────────────────
_dlv = demand[demand["productname"].str.endswith("_Delivered")]
assert int(_dlv["quantity"].sum()) == D_TOTAL, "delivery demand no longer equals D"
for c in CLASSES:
    for tag in MIX_ORIGINS:
        got = int(_dlv.loc[_dlv["productname"] == delivered(c, tag), "quantity"].sum())
        want = sum(MIX[(p, tag, c)] for p in sorted(DELIVERY_PUD_SET))
        assert abs(got - want) <= len(DELIVERY_PUD_SET), (
            f"{tag} {c}: delivered {got:,} vs mix {want:,}")
print("  delivered by family (Change 28):")
for c in CLASSES:
    for _fam, _tags in [(f, TAGS_FOR[f][c]) for f in ARR_FAMS]:
        _v = int(sum(_dlv.loc[_dlv.productname == delivered(c, t), "quantity"].sum() for t in _tags))
        print(f"    {c} {_fam}: {_v:>8,} over {len(_tags)} site flavours")
print(f"  demand rows {len(demand):,}  (untagged build: ~{len(zone_rows) + 2 * len(HUB_SET) * len(CLASSES):,};"
      f" document estimate ~2,400)")
print(f"  delivered {int(_dlv['quantity'].sum()):,} = D — chain 2 has no other sink")

# --------------------------------------------------------------------------------------
# ## 5. Suppliers
#
# Catchment lodgement, interstate arrivals at the hubs, and the local stage at the sorting PDCs.
# Lodgement type (IND/RES) would split each catchment's supply in two at the pickup state; it is off,
# so each catchment supplies one pickup product per class.
# --------------------------------------------------------------------------------------

# ── Suppliers: the three measured chain-2 families (Change 28) ────────────────────────
# No catchment suppliers here — pickup is chain 1's entity.
SUP_COLS = ["suppliername", "status", "city", "region", "postalcode", "country",
            "latitude", "longitude", "suppliercapacity", "notes"]
def _sup_short(s):
    return s.replace("HUB_", "").replace("PUD_", "")
sup_rows = []
for s in sorted(ARRIVAL_SET):
    sup_rows.append(["SUP_INT_" + _sup_short(s), "Include", "", "", "", "Australia",
                     _geo.loc[s, "latitude"], _geo.loc[s, "longitude"], "", "interstate arrivals"])
for _f in SD_FAMS:                                   # Change 46: one supplier set per band
    for s in fam_any_sites(_f):
        sup_rows.append([f"SUP_{_f}_" + _sup_short(s), "Include", "", "", "", "Australia",
                         _geo.loc[s, "latitude"], _geo.loc[s, "longitude"], "",
                         f"{FAM_LABEL[_f]} lodgement, measured at its first-sort site"])
for p in STG_PUDS:
    sup_rows.append(["SUP_STAGE_" + p.replace("PUD_", ""), "Include", "", "", "", "Australia",
                     _geo.loc[p, "latitude"], _geo.loc[p, "longitude"], "", "overnight stage, measured"])
suppliers = pd.DataFrame(sup_rows, columns=SUP_COLS).drop_duplicates("suppliername")
write_csv(suppliers, "Suppliers")

# ── SupplierCapabilities: three measured families, exact caps (Change 28) ─────────────
# Interstate: per arrival site. A depot's direct arrivals EXCLUDE its cross-dock intake (that
# volume physically enters at a hub and comes over on the XDock lane); the handling hub's cap
# INCLUDES it. Victoria same-day and stage are exact per site / depot.
SC_COLS = ["suppliername", "productname", "status", "supplycapacity", "supplycapacityuom",
           "notes", "unitcost", "unitcostuom", "co2emissionrate", "co2emissionrateuom"]
def _sup_short(s):
    return s.replace("HUB_", "").replace("PUD_", "")
sc_rows = []
for s in sorted(ARRIVAL_SET):
    for c in hub_classes(s):
        _need = IN_by_site_class[(s, c)]
        if s in ARRIVAL_PUD_SET:
            _need -= XDOCK_AT.get((s, c), 0)
        _need += XDOCK_HAND.get((s, c), 0)
        if _need <= 0:
            continue
        for _pres in INT_PRES_LIST:
            _sh = INT_PRES[_pres] if _pres else 1.0
            sc_rows.append(["SUP_INT_" + _sup_short(s), int_pk(c, _pres), "Include",
                            math.ceil(_need * _sh), "EA",
                            f"interstate arrivals, {c} at {HUB_CODE[s]}"
                            + (f", {_pres} {_sh:.0%} (pinned)" if _pres else "")
                            + (", incl. cross-dock intake" if XDOCK_HAND.get((s, c), 0) else ""),
                            "", "", "", ""])
for _f in SD_FAMS:
    for s in fam_any_sites(_f):
        for c in fam_classes(_f, s):
            _need = VOL[_f][(s, c)]
            if _need:
                sc_rows.append([f"SUP_{_f}_" + _sup_short(s),
                                f"{c}_{FAM_TAG[_f]}_Pickup", "Include", _need, "EA",
                                f"{FAM_LABEL[_f]}, {c}, first sorted at {HUB_CODE[s]} "
                                f"(measured)", "", "", "", ""])
for p in STG_PUDS:
    for c in CLASSES:
        _need = STAGE_BY_PUD[(p, c)]
        if _need:
            sc_rows.append(["SUP_STAGE_" + p.replace("PUD_", ""), dsp_final(c, stage_tag_pud(p)),
                            "Include", _need, "EA",
                            "overnight stage, measured kept-at-depot EA", "", "", "", ""])
supplier_capabilities = pd.DataFrame(sc_rows, columns=SC_COLS)
write_csv(supplier_capabilities, "SupplierCapabilities")

# --------------------------------------------------------------------------------------
# ## 6. Bills of material — the recipe selects the equipment, and the hub
#
# With two rounds there is one round-2 unload recipe **per origin flavour**
# (`BOM_UNLOAD2_<code>_<class>_<tag>`). A hub is granted the foreign flavours and never its own, which
# is what makes "the two sorts happen at two different hubs" structural rather than a constraint —
# the same device the baseline notebooks use.
#
# With lodgement type on, the unload recipes are specific to it and the product selects the equipment,
# which is what removes the need for a ratio constraint. **With it off (current setting), all three
# unload machines are competing recipes on the one pickup product and the solver picks on cost and
# capacity** — so the equipment split becomes a model output again, or something you force with a ratio
# constraint. Interstate inbound has competing recipes either way, because the presentation there is a
# decision Australia Post makes rather than an input it receives.
# --------------------------------------------------------------------------------------

BOM_COLS = ["bomname", "productname", "quantity", "status", "notes"]
bom_rows = []

# ── 1. PICKUP FAMILY: chain 1's entity — no pickup recipe exists here. ─────────────────
for c in CLASSES:
# ── 2. INTERSTATE FAMILY: the only one that takes round 2 and reaches a delivery zone ──
    # Change 23: with the mix pinned, each presentation has ONE recipe and the ratio is
    # structural. Left free, both recipes consume the same product and compete on cost.
    for _pres, _eq in PRES_EQ.items():
        bom_rows.append([f"BOM_UNLOAD_{_eq}_INT_{c}", int_pk(c, _pres if FIXED_PRES else None),
                         1, "Include",
                         f"interstate inbound on {_eq}" +
                         (f" — the ONLY recipe for {_pres}-presented freight, so the mix is exact"
                          if FIXED_PRES else " — competing recipe, cost decides")])
    bom_rows.append([f"BOM_SORT_INT_{c}", f"{c}_INTERSTATE_Unloaded", 1, "Include", "interstate sort"])
    bom_rows.append([f"BOM_LOAD_INT_{c}", f"{c}_INTERSTATE_Sorted", 1, "Include", "interstate round-1 load"])
    for h in cls_hubs(c):
        bom_rows.append([f"BOM_DRIVER_INT_{HUB_CODE[h]}_{c}", dsp_final(c, int_tag(h)), 1, "Include",
                         f"driver wave -> delivered, arrived interstate at {HUB_CODE[h]}"])
    # ── Change 46: BOTH VICTORIAN BANDS mirror interstate, one recipe set each ────────
    for _f in SD_FAMS:
        _T, _fl = FAM_TAG[_f], FAM_LABEL[_f]
        for _eq in ("ULD", "HAND"):
            bom_rows.append([f"BOM_UNLOAD_{_eq}_{_T}_{c}", f"{c}_{_T}_Pickup", 1, "Include",
                             f"{_fl} inbound on {_eq} — competing recipe"])
        bom_rows.append([f"BOM_SORT_{_T}_{c}", f"{c}_{_T}_Unloaded", 1, "Include", f"{_fl} sort"])
        bom_rows.append([f"BOM_LOAD_{_T}_{c}", f"{c}_{_T}_Sorted", 1, "Include",
                         f"{_fl} round-1 load"])
        if BYPASS:
            bom_rows.append([f"BOM_LOAD_DIRECT_{_T}_{c}", f"{c}_{_T}_Sorted", 1, "Include",
                             f"round-1 load -> Despatch2 DIRECT ({_fl})"])
        for h in fam_sites(_f, c):
            bom_rows.append([f"BOM_DRIVER_{_T}_{HUB_CODE[h]}_{c}", dsp_final(c, fam_tag(_f, h)),
                             1, "Include",
                             f"driver wave -> delivered, {_fl} via {HUB_CODE[h]}"])
    # ── Change 28 (12b): the CROSS-DOCK — load UNSORTED, a depot sorts it ─────────────
    # Deliberately consumes the state BEFORE the sort: the mirror image of the bypass (which
    # skips the SECOND sort, where this skips the hub's FIRST — the depot still sorts once).
    if XDOCK_ENABLED:
        for h in sorted(HUB_SET):
            bom_rows.append([f"BOM_LOAD_XDOCK_{HUB_CODE[h]}_{c}", f"{c}_INTERSTATE_Unloaded", 1,
                             "Include", f"cross-dock load at {HUB_CODE[h]}: unsorted, to a depot sorter"])
            bom_rows.append([f"BOM_SORT_XD_{HUB_CODE[h]}_{c}", f"{c}_INTERSTATE_XDock_{HUB_CODE[h]}",
                             1, "Include",
                             "depot sorts the cross-docked freight — its FIRST sort"])

# ── 3. STAGE FAMILY: one recipe only — the driver wave. No unload, no sort, no hub. ────
    for t in STG_TAGS:
        bom_rows.append([f"BOM_DRIVER_{c}_{t}", dsp_final(c, t), 1, "Include",
                         f"driver wave -> delivered, kept at the {t[4:]} PDC"])

# ── Round 2 (interstate only), at a site that is NOT the one that did the first sort ───
# Change 21b: round 2 no longer collapses the arrival flavour. `Despatch1_<h>` becomes
# `INTERSTATE_<h>_Unloaded2` and stays that way through Despatch2 and Delivered, which is what
# gives interstate volume a source-to-sink path.
if TWO_ROUNDS:
    for c in CLASSES:
        for h in cls_hubs(c):
            it = int_tag(h)
            bom_rows.append([f"BOM_UNLOAD2_{HUB_CODE[h]}_{c}", dsp1(c, "INTERSTATE", HUB_CODE[h]), 1,
                             "Include", f"round-2 unload of {HUB_CODE[h]}-arrived volume "
                                        f"(never granted at {HUB_CODE[h]} itself)"])
            bom_rows.append([f"BOM_SORT2_{c}_{it}", st(c, it, "Unloaded2"), 1, "Include",
                             f"round-2 sort, keeps its {HUB_CODE[h]} arrival origin"])
            bom_rows.append([f"BOM_LOAD2_{c}_{it}", st(c, it, "Sorted2"), 1, "Include",
                             f"round-2 load -> Despatch2, {HUB_CODE[h]} arrival origin"])
        for _f in SD_FAMS:                         # Change 46: both bands take round 2 too
            _T, _fl = FAM_TAG[_f], FAM_LABEL[_f]
            for h in fam_sites(_f, c):
                vt = fam_tag(_f, h)
                bom_rows.append([f"BOM_UNLOAD2_{_T}{HUB_CODE[h]}_{c}",
                                 dsp1(c, _T, HUB_CODE[h]), 1, "Include",
                                 f"round-2 unload of {_fl} volume first sorted at {HUB_CODE[h]}"])
                bom_rows.append([f"BOM_SORT2_{c}_{vt}", st(c, vt, "Unloaded2"), 1, "Include",
                                 f"round-2 sort, keeps its {HUB_CODE[h]} first-sort origin"])
                bom_rows.append([f"BOM_LOAD2_{c}_{vt}", st(c, vt, "Sorted2"), 1, "Include",
                                 f"round-2 load -> Despatch2, {HUB_CODE[h]} first-sort origin"])

# ── Change 25: the sort BYPASS — a SECOND round-1 load recipe at the arrival hub ───────
# The normal round-1 load makes `Despatch1_<hub>`, which nothing but a round-2 unload at
# ANOTHER site can consume. This one takes the same round-1 sorted parcel and loads it straight
# to `Despatch2` — the state a driver wave consumes — so it leaves the arrival hub already
# delivery-ready and skips the second site entirely. Both recipes consume
# `<cls>_INTERSTATE_Sorted`; with the bypass on they compete on cost.
if BYPASS:
    for c in CLASSES:
        bom_rows.append([f"BOM_LOAD_DIRECT_{c}", f"{c}_INTERSTATE_Sorted", 1, "Include",
                         "round-1 load -> Despatch2 DIRECT (Change 25): skips the second sort"])

bill_of_materials = pd.DataFrame(bom_rows, columns=BOM_COLS)
write_csv(bill_of_materials, "BillOfMaterials")

# ══ THE GUARANTEE, ASSERTED (Change 20b) ══════════════════════════════════════════════
# No round-2 or driver recipe anywhere consumes a PICKUP-family product, so locally lodged volume
# has no path to Despatch2 and therefore none to a delivery zone. This is a property of the recipe
# table — no FlowConstraint is involved.
_downstream = bill_of_materials.bomname.str.startswith(("BOM_UNLOAD2_", "BOM_SORT2_", "BOM_LOAD2_",
                                                        "BOM_DRIVER_"))
_leak = bill_of_materials.loc[_downstream
                              & bill_of_materials.productname.str.startswith(PICKUP_PREFIXES)]
assert _leak.empty, ("a delivery-side recipe consumes a pickup-origin product — pickup could be "
                     f"delivered in Melbourne:\n{_leak[['bomname', 'productname']].to_string()}")
# Change 28: only the cross-dock may consume an _Unloaded state — the whole point is that the
# hub did not sort it. Nothing else may.
_unl = bill_of_materials[bill_of_materials.productname.str.endswith("_Unloaded")]
_bad_unl = _unl[~_unl.bomname.str.startswith(("BOM_SORT", "BOM_LOAD_XDOCK_"))]
assert _bad_unl.empty, f"a non-sort, non-crossdock recipe consumes an unsorted state:\n{_bad_unl.head()}"
# and symmetrically: the STG family has exactly one recipe each, the driver wave
for c in CLASSES:
    for t in STG_TAGS:
        _b = bill_of_materials.loc[bill_of_materials.productname == dsp_final(c, t)]
        assert len(_b) == 1 and _b.iloc[0].bomname == f"BOM_DRIVER_{c}_{t}", (
            f"{dsp_final(c, t)} is consumed by something other than its driver wave")
# Change 21b: every interstate arrival flavour has an unbroken chain to its own Delivered product.
for c in CLASSES:
    for h in cls_hubs(c):
        it = int_tag(h)
        for want, bom in [(dsp1(c, "INTERSTATE", HUB_CODE[h]), f"BOM_UNLOAD2_{HUB_CODE[h]}_{c}"),
                          (st(c, it, "Unloaded2"), f"BOM_SORT2_{c}_{it}"),
                          (st(c, it, "Sorted2"), f"BOM_LOAD2_{c}_{it}"),
                          (dsp_final(c, it), f"BOM_DRIVER_INT_{HUB_CODE[h]}_{c}")]:
            _b = bill_of_materials.loc[bill_of_materials.bomname == bom]
            assert len(_b) == 1 and _b.iloc[0].productname == want, \
                f"{bom} should consume {want}, chain for {it} is broken"
# Change 25: the bypass must consume the ROUND-1 SORTED state, not the despatch state — if it
# consumed Despatch1 it would be a second sort by another name, and if it consumed Unloaded it
# would skip sorting altogether.
if BYPASS:
    for c in CLASSES:
        _b = bill_of_materials.loc[bill_of_materials.bomname == f"BOM_LOAD_DIRECT_{c}"]
        assert len(_b) == 1 and _b.iloc[0].productname == f"{c}_INTERSTATE_Sorted", \
            f"the {c} bypass load must consume the round-1 sorted state, not {_b.productname.tolist()}"
    print(f"  Change 25: bypass ON ({INTERSTATE_BYPASS}) — {len(CLASSES)} direct load recipes, "
          f"0 new products")
print(f"  {len(bill_of_materials)} BOM rows — no delivery-side recipe consumes a pickup-origin "
      f"product, so ALL pickup terminates after one sort (structural)")

# --------------------------------------------------------------------------------------
# ## 7. Work centres — capacity from the operating window
#
# The proposal's most important capacity point, and it survives the periods being switched off:
# capacity is rate × the window a resource is genuinely available, never rate × the elapsed day. With
# one period that window is the full day's available hours; with two it is split across them, because
# the Anura `WorkCenters` table has no period column.
# --------------------------------------------------------------------------------------

UNLOADS = ["UNLOAD_HAND", "UNLOAD_ULD", "UNLOAD_LONGREACH"]
LOADS   = ["LOAD_HAND", "LOAD_ULD", "LOAD_LONGREACH"]
_mr = pd.read_csv(FASS / "machine_rates.csv")
RATE_HR   = {_r.machine: int(_r.rate_hr) for _r in _mr.itertuples() if pd.notna(_r.rate_hr)}
UNIT_COST = dict(zip(_mr.machine, _mr.unit_cost))
FIXED_YR  = {_r.machine: int(_r.fixed_yr) for _r in _mr.itertuples()}

SORT_SITES = {s: dict(v) for s, v in SITE_SORTERS.items()}
for h in sorted(HUB_SET):
    SORT_SITES[h]["SORT_MANUAL"] = RATE_HR["SORT_MANUAL"]

def _kind(a):
    if a.startswith("UNLOAD") or a == "BAG_UNLOAD": return "UNLOAD"
    if a.startswith("LOAD"):   return "LOAD"
    if a == "DRIVER_WAVE":     return "DRIVER_WAVE"
    return "SORT"

def _short(s): return s.replace("HUB_", "").replace("PUD_", "")
def _machines(site):
    """Change 20a: a PDC can now hold TWO inbound docks — BAG_UNLOAD for its own first-mile bags
    (round 0) and a linehaul dock for Despatch1 arriving from a hub (round 2, DLC only)."""
    if site in HUB_SET:
        return UNLOADS + sorted(SORT_SITES[site]) + LOADS
    ms = []
    # Change 28: no BAG_UNLOAD here — round 0 is chain 1's entity, in its own notebook.
    if site in ROUND2_PUD_SET:
        ms += [PUD_R2_UNLOAD, PUD_R2_LOAD]
    if site in ARRIVAL_PUD_SET:      # Change 28: an arrival depot gets the linehaul docks
        ms += ["UNLOAD_ULD", "UNLOAD_LONGREACH", "UNLOAD_HAND", "LOAD_ULD", "LOAD_HAND"]
    if site in SORT_PUD_SET:
        ms += sorted(SORT_SITES.get(site, {}))
    return list(dict.fromkeys(ms))

# ── Round-2 sort PDC: how much may divert off the hubs (Change 20a) ────────────────────
# Round-2 volume is everything still needing a second sort = D - stage. DLC_SMALL_SHARE caps the
# slice that may take that sort at a PDC instead of a hub — the stand-in for "the small-parcel
# share" until products carry a size attribute. A Max, not a pin.
_R2_VOL    = D_TOTAL - STAGE_TOT
r2_pud_vol = {p: int(round(DLC_SMALL_SHARE * _R2_VOL)) for p in sorted(ROUND2_PUD_SET)}
R2_PUD_TOT = sum(r2_pud_vol.values())
# its docks are sized to that slice (plus the same headroom the hubs get), expressed as a rate/hr
# because capacity here is rate x operating window, not a flat daily figure.
PUD_R2_DOCK_HR = {p: v * (1 + HUB_DOCK_HEADROOM) / AVAILABLE_HOURS_PER_DAY["UNLOAD"]
                  for p, v in r2_pud_vol.items()}
print(f"  round-2 sort PDC: {_R2_VOL:,} EA/day need a 2nd sort; cap {DLC_SMALL_SHARE:.0%} -> "
      + ", ".join(f"{_short(k)} {v:,}" for k, v in r2_pud_vol.items()) + " (rest stays at the hubs)")

# ── Hub dock sizing (Change 14) ────────────────────────────────────────────────────────
# Round 1 = exported pickup + interstate arrivals. Round 2 = every delivery-bound parcel takes a
# SECOND unload and load, at a different hub. Unload touches == load touches.
# Change 22 — DOCKS ARE SIZED PER HUB, NOT BY A NETWORK AVERAGE  → docs/chain2-observed.md#change-22-docks-are-sized-per
_r1_hub = {h: 0.0 for h in sorted(ARRIVAL_SET)}
for _c in CLASSES:
    for _h in cls_hubs(_c):                                  # interstate arrivals landed here
        _r1_hub[_h] += IN_by_hub_class[(_h, _c)]
    for _f in SD_FAMS:                                       # both Victorian bands land here too
        for _h in fam_sites(_f, _c):
            _r1_hub[_h] += VOL[_f][(_h, _c)]
    for _h in sorted(HUB_SET):                               # cross-dock touches the hub's docks
        _r1_hub[_h] += XDOCK_HAND.get((_h, _c), 0)
_r1_touch = IN_TOT + VIC_TOT + XD_TOT
_r2_touch = (D_TOTAL - STAGE_TOT) if HUB_SORT_ROUNDS == 2 else 0
_touch    = _r1_touch + _r2_touch
_r2_pool  = {h: (sum(IN_by_hub_class[(g, c)] for c in CLASSES for g in cls_hubs(c) if g != h)
                 if h in R2_SORT_SITES else 0)
             for h in sorted(ARRIVAL_SET)} if HUB_SORT_ROUNDS == 2 else {h: 0 for h in ARRIVAL_SET}
_r2_even  = _r2_touch / max(len(R2_SORT_SITES), 1)
_dock_base = sum(RATE_HR[a] for a in UNLOADS) * AVAILABLE_HOURS_PER_DAY["UNLOAD"]
_dock_day  = len(HUB_SET) * _dock_base
_need = {h: (_r1_hub[h] + min(_r2_even, _r2_pool[h])) * (1 + HUB_DOCK_HEADROOM)
         for h in sorted(ARRIVAL_SET)}
HUB_DOCK_SCALE = ({h: max(1.0, _need[h] / _dock_base) for h in sorted(ARRIVAL_SET)}
                  if HUB_DOCK_AUTOSCALE else {h: 1.0 for h in sorted(ARRIVAL_SET)})
print(f"  hub docks: {_touch:,} touches/side network-wide (R1 {_r1_touch:,} + R2 {_r2_touch:,}) "
      f"vs a stated fleet of {int(_dock_day):,} EA/day. Sized PER HUB (Change 22):")
for h in sorted(ARRIVAL_SET, key=lambda x: -_need[x]):
    _sc = HUB_DOCK_SCALE[h]
    print(f"    {HUB_CODE[h]:<4} R1 {_r1_hub[h]:>9,.0f} (pinned) + R2 {min(_r2_even, _r2_pool[h]):>9,.0f} "
          f"(cap: pool {_r2_pool[h]:,.0f}) = {_need[h]:>9,.0f} needed vs {int(_dock_base):,} base "
          f"-> x{_sc:.3f}" + (f"  ** {math.ceil(_dock_base * _sc):,} EA/side **" if _sc > 1.0 else "  (fits)"))
_short_hubs = {h: math.ceil(_dock_base * (s - 1)) for h, s in HUB_DOCK_SCALE.items() if s > 1.0}
if _short_hubs:
    print(f"  NOTE: the stated dock fleet is SHORT at "
          + ", ".join(f"{HUB_CODE[h]} (+{v:,} EA/day/side)" for h, v in sorted(_short_hubs.items()))
          + ". A scale above 1.0 is a REAL OPS REQUIREMENT — more dock machines or longer dock "
            "hours — not a modelling fudge. It used to be blamed on the assumed PP hub split; "
            "that split is gone (Change 47) and the arrivals driving these docks are MEASURED, "
            "so the requirement is a finding about the network rather than about a dial. "
            "Confirm with ops.")

WC_COLS = ["workcentername", "facilityname", "status", "workcenterstatus", "initialstate",
           "throughputcapacity", "throughputcapacityuom", "fixedoperatingcost", "fixedstartupcost",
           "fixedclosingcost", "changeovername", "notes", "minimumthroughput", "minimumthroughputuom"]
wc_rows, site_machines = [], {}

def _rate(site, a):
    if site in ROUND2_PUD_SET and a in (PUD_R2_UNLOAD, PUD_R2_LOAD):
        return PUD_R2_DOCK_HR[site]     # dock sized to the volume capped onto this PDC
    r = SORT_SITES.get(site, {}).get(a, RATE_HR.get(a))
    if site in ARRIVAL_SET and a in (UNLOADS + LOADS):
        r = r * HUB_DOCK_SCALE[site]    # Change 22/28: docks sized to THIS site's own touches
    return r

for site in sorted(set(list(SORT_SITES) + list(DELIVERY_PUD_SET) + list(ROUND2_PUD_SET))):
    ms = _machines(site)
    site_machines[site] = ms
    for a in ms:
        win = window(_kind(a))     # hours available PER PERIOD; the cap applies in every period
        wc_rows.append([f"WC_{a}_{_short(site)}", site, "Include", "Open", "Existing",
                        int(_rate(site, a) * win), "EA", round(FIXED_YR[a] / WORKING_DAYS),
                        "", "", "", f"{a} at {_short(site)}: {_rate(site,a):,}/hr x {win}h per period "
                        f"({AVAILABLE_HOURS_PER_DAY[_kind(a)]}h/day over {len(PERIODS)} period(s))",
                        "", ""])
# driver waves at the delivery PDCs
lm_by_pud = base.groupby("pud")["parcel_count"].sum()   # base carries `pud` since Change 12
WAVE_RATE, WAVE_VAN = dial("WAVE_RATE"), dial("WAVE_VAN")
for site in sorted(DELIVERY_PUD_SET):
    win = window("DRIVER_WAVE")
    peak = max(PERIOD_SPLIT["delivery"][p] for p in PERIODS)      # size for the busiest period
    vol = lm_by_pud.get(site, 0) * peak
    drivers = math.ceil(vol / min(WAVE_VAN, WAVE_RATE * win)) if vol else 0
    wc_rows.append([f"WC_DRIVER_WAVE_{_short(site)}", site, "Include", "Open", "Existing",
                    int(drivers * WAVE_RATE * win), "EA", round(FIXED_YR["DRIVER_WAVE"] / WORKING_DAYS),
                    "", "", "", f"{drivers} drivers x {WAVE_RATE}/hr x {win}h per period "
                    f"(sized on the busiest period, {peak:.0%} of the day)", "", ""])
    site_machines.setdefault(site, [])
    site_machines[site] = site_machines[site] + ["DRIVER_WAVE"]

work_centers = pd.DataFrame(wc_rows, columns=WC_COLS)
write_csv(work_centers, "WorkCenters")

_hub_dock = work_centers[work_centers.workcentername.str.startswith("WC_UNLOAD")]
_hub_dock = _hub_dock[_hub_dock.facilityname.isin(HUB_SET)]
_per_period = int(_hub_dock.throughputcapacity.sum())          # the cap applies in EVERY period
_per_day    = _per_period * len(PERIODS)
print(f"  hub unload capacity {_per_period:,}/period x {len(PERIODS)} = {_per_day:,} EA/day "
      f"vs {_touch:,} touches needed ({100*_touch/_per_day:.0f}% used) "
      f"[{HUB_SORT_ROUNDS} sort round(s): R1 {_r1_touch:,} + R2 {_r2_touch:,}]")
print(f"  for comparison, the current model's 20 h day would give "
      f"{int(4 * (750+2750+1750) * 20):,} EA/day across 4 hubs — the operating-window correction "
      f"removes about a third of the stated dock capacity.")
if _per_day < _touch:
    print(f"  SHORT by {_touch - _per_day:,} EA/day.")

# ── Processes: one per machine per period ──────────────────────────────────
PROC_COLS = ["processname", "stepname", "stepnumber", "status", "workcentername", "processingrate",
             "ratequantityuom", "ratetimeuom", "unitcost", "unitcostuom", "fixedtime", "fixedtimeuom",
             "fixedcost", "lotsize", "lotsizeuom", "yieldpercentage", "notes", "fixedcostrule"]
wc_cap = dict(zip(work_centers.workcentername, work_centers.throughputcapacity))
proc_rows, procs_at = [], {}
for site, ms in site_machines.items():
    procs_at[site] = {}
    for a in ms:
        wc = f"WC_{a}_{_short(site)}"
        if wc not in wc_cap:
            continue
        pname = f"{_short(site)}_{a}"
        proc_rows.append([pname, a, 1, "Include", wc, wc_cap[wc], "EA", "DAY",
                          UNIT_COST[a], "EA", "", "", "", "", "", "",
                          f"{a} at {_short(site)}", ""])
        procs_at[site].setdefault(a, []).append(pname)
# ── CHANGE 48: A SECOND, SCALED COPY OF EVERY ROUND-2 PROCESS ────────────────────────
# Round-2 handling is charged and timed for the second sorts the SCANS measure, not the smaller
# number the model routes. A clone rather than a dial on the process itself, because all of these
# processes are SHARED with round 1 — `Sunshine_West_SORT_AUTO_LGE` does both rounds — so editing
# the original in place would scale round-1 volume too. Only the round-2 production policies point
# at the clone (see the TWO_ROUNDS block below); round 1 keeps the original untouched.
#
#     processingrate  / ratio     so the routed parcels take the measured volume's machine hours
#     unitcost        x ratio     so they cost the measured volume's handling
#
# The denominator is the round-2 volume the LANE BANDS are about to pin, computed here with the
# same `share x VOL` arithmetic the constraint cell uses, so the two cannot disagree. It is NOT
# obs_legs' own article count: the bands scale measured shares by the MODEL's arrival volume, and
# the two bases differ by 6%.
#
# NOTE what this does not do — work-centre capacity in this model is expressed in EA, not hours
# (`throughputcapacityuom` is EA everywhere), so utilisation is quantity/capacity and a rate change
# does not move it. Cost and machine HOURS scale; the utilisation percentage does not.
R2_MODELLED = sum(OBS_ROUND2.get((_f, _c, HUB_CODE[_g], HUB_CODE[_h]), 0.0) * VOL[_f].get((_g, _c), 0)
                  for (_f, _c, _g, _h) in R2_PAIRS)
R2_PROCS = set()          # the cloned process names, for the Change 33 work-centre guard
if ROUND2_COST_BASIS == "off" or not R2_MODELLED:
    R2_SCALE, procs_r2 = 1.0, procs_at
    print("  Change 48: round-2 handling NOT scaled (ROUND2_COST_BASIS=off)")
else:
    R2_SCALE = SORT_2PLUS[ROUND2_COST_BASIS] / R2_MODELLED
    assert R2_SCALE >= 1, f"round-2 scale {R2_SCALE:.3f} below 1 — the model routes MORE second " \
                          f"sorts ({R2_MODELLED:,.0f}) than the scans measure"
    _base = {r[0]: r for r in proc_rows}
    # only the sites that take a second sort, and only the activities round 2 uses. Cloning every
    # process at every site left 23 copies nothing pointed at, which NEO reports as unused model
    # elements and a reader has to decide are harmless.
    _r2_sites = R2_SORT_SITES | set(ROUND2_PUD_SET)
    _r2_act = lambda a: (a.startswith(("UNLOAD_", "SORT_", "LOAD_"))
                         or a in (PUD_R2_UNLOAD, PUD_R2_LOAD))
    procs_r2 = {}
    for _site, _byact in procs_at.items():
        if _site not in _r2_sites:
            continue
        procs_r2[_site] = {}
        for _a, _pnames in _byact.items():
            if not _r2_act(_a):
                continue
            for _pn in _pnames:
                _b = _base[_pn]
                _clone = list(_b)
                _clone[0] = _pn + "_R2"
                _clone[5] = _b[5] / R2_SCALE                       # processingrate
                _clone[8] = round(_b[8] * R2_SCALE, 6)             # unitcost
                _clone[16] = (f"{_b[16]} [Change 48: round-2 only, rate /{R2_SCALE:.4f}, "
                              f"cost x{R2_SCALE:.4f}]")
                proc_rows.append(_clone)
                procs_r2[_site].setdefault(_a, []).append(_clone[0])
                R2_PROCS.add(_clone[0])
    print(f"  Change 48: round-2 handling charged for {SORT_2PLUS[ROUND2_COST_BASIS]:,} measured "
          f"2+ sorts against {R2_MODELLED:,.0f} modelled — x{R2_SCALE:.4f} on "
          f"{sum(len(v) for v in procs_r2.values())} cloned processes")

processes = pd.DataFrame(proc_rows, columns=PROC_COLS)
write_csv(processes, "Processes")

# --------------------------------------------------------------------------------------
# ## 8. Production policies — where each recipe is allowed to fire
#
# This is where the different-hub rule is actually enforced: round-1 load produces the loading hub's
# **own** flavour, and round-2 unload is granted only for the **other** hubs' flavours. Both are
# asserted after the table is written.
#
# `Despatch` is produced only at hubs, which is what forces the hub leg structurally. No routing
# constraint is written.
# --------------------------------------------------------------------------------------

PP_COLS = ["facilityname", "productname", "status", "productionrate", "ratequantityuom",
           "ratetimeuom", "workcentername", "unitcost", "unitcostuom", "bomname",
           "processname", "notes", "co2emissionrate", "co2emissionrateuom"]

def _pp(fac, prod, bom, proc, note):
    return [fac, prod, "Include", "", "", "", "", "", "", bom, proc, note, "", ""]

pp_rows = []
for site, byact in procs_at.items():
    is_hub = site in HUB_SET
    for c in CLASSES:
        # ── PICKUP FAMILY: chain 1's entity — nothing here. ───────────────────────────
        # ── INTERSTATE round 1, at every ARRIVAL SITE this class enters (Change 28) ───
        if site in ARRIVAL_SET and c in hub_classes(site):
            for a, pnames in byact.items():
                for pn in pnames:
                    if a in ("UNLOAD_ULD", "UNLOAD_LONGREACH"):
                        eq = "ULD" if a == "UNLOAD_ULD" else "LONGREACH"
                        pp_rows.append(_pp(site, f"{c}_INTERSTATE_Unloaded", f"BOM_UNLOAD_{eq}_INT_{c}",
                                           pn, f"interstate inbound, {eq} (competing recipe)"))
                    elif a.startswith("SORT_"):
                        pp_rows.append(_pp(site, f"{c}_INTERSTATE_Sorted", f"BOM_SORT_INT_{c}", pn,
                                           "interstate round-1 sort"))
                        # Change 28 (12b): a depot sorter also takes the cross-docked freight —
                        # the flavour dissolves into the shared round-1 sorted state here.
                        if XDOCK_ENABLED and site in XD_DESTS:
                            for g in sorted(h for (c2, h, d) in XD_PAIRS
                                            if c2 == c and d == site):
                                pp_rows.append(_pp(site, f"{c}_INTERSTATE_Sorted",
                                                   f"BOM_SORT_XD_{HUB_CODE[g]}_{c}", pn,
                                                   f"first sort of {HUB_CODE[g]} cross-dock freight"))
                    elif a.startswith("LOAD_"):
                        pp_rows.append(_pp(site, dsp_out(c, "INTERSTATE", site), f"BOM_LOAD_INT_{c}",
                                           pn, "interstate round-1 load -> own Despatch1 flavour"))
                        if BYPASS:
                            pp_rows.append(_pp(site, st(c, int_tag(site), "Despatch2"),
                                               f"BOM_LOAD_DIRECT_{c}", pn,
                                               "round-1 load -> Despatch2 DIRECT (sort bypass)"))
                        # Change 28 (12b): the hub may load UNSORTED freight for a depot sorter.
                        if XDOCK_ENABLED and site in XD_SOURCES:
                            pp_rows.append(_pp(site, f"{c}_INTERSTATE_XDock_{HUB_CODE[site]}",
                                               f"BOM_LOAD_XDOCK_{HUB_CODE[site]}_{c}", pn,
                                               "cross-dock load: handled here, sorted at a depot"))
        # ── THE TWO VICTORIAN BANDS, round 1 (Change 46) — each mirrors interstate ────
        for _f in SD_FAMS:
            _T, _fl = FAM_TAG[_f], FAM_LABEL[_f]
            if not (site in ARRIVAL_SET and c in fam_classes(_f, site)):
                continue
            for a, pnames in byact.items():
                for pn in pnames:
                    if a in ("UNLOAD_ULD", "UNLOAD_HAND"):
                        eq = "ULD" if a == "UNLOAD_ULD" else "HAND"
                        pp_rows.append(_pp(site, f"{c}_{_T}_Unloaded",
                                           f"BOM_UNLOAD_{eq}_{_T}_{c}", pn,
                                           f"{_fl} inbound, {eq}"))
                    elif a.startswith("SORT_"):
                        pp_rows.append(_pp(site, f"{c}_{_T}_Sorted", f"BOM_SORT_{_T}_{c}", pn,
                                           f"{_fl} round-1 sort"))
                    elif a.startswith("LOAD_"):
                        pp_rows.append(_pp(site, dsp_out(c, _T, site), f"BOM_LOAD_{_T}_{c}",
                                           pn, f"{_fl} round-1 load -> own flavour"))
                        if BYPASS:
                            pp_rows.append(_pp(site, st(c, fam_tag(_f, site), "Despatch2"),
                                               f"BOM_LOAD_DIRECT_{_T}_{c}", pn,
                                               f"round-1 load -> Despatch2 DIRECT ({_fl})"))

# ── Round 2, at a site that is NOT the arrival hub. Change 21b: the flavour SURVIVES. ──
if TWO_ROUNDS:
    for site in sorted(R2_SORT_SITES | set(ROUND2_PUD_SET)):   # Change 28c: observed sites only
        # Change 48: the SCALED copy of each process, so round-2 handling is timed and charged for
        # the measured second-sort volume. `procs_r2` is `procs_at` itself when the dial is off.
        for a, pnames in procs_r2.get(site, {}).items():
            for pn in pnames:
                for c in CLASSES:
                    for g in cls_hubs(c):
                        if ("INT", c, g, site) not in R2_PAIRS:
                            continue          # Change 29: only the routing the scans show
                        it = int_tag(g)
                        if a.startswith("UNLOAD_") or a == PUD_R2_UNLOAD:
                            pp_rows.append(_pp(site, st(c, it, "Unloaded2"),
                                               f"BOM_UNLOAD2_{HUB_CODE[g]}_{c}", pn,
                                               f"round-2 unload of {HUB_CODE[g]}-arrived volume"))
                        elif a.startswith("SORT_"):
                            pp_rows.append(_pp(site, st(c, it, "Sorted2"), f"BOM_SORT2_{c}_{it}",
                                               pn, f"round-2 sort, keeps {HUB_CODE[g]} origin"))
                        elif a.startswith("LOAD_") or a == PUD_R2_LOAD:
                            pp_rows.append(_pp(site, st(c, it, "Despatch2"), f"BOM_LOAD2_{c}_{it}",
                                               pn, f"round-2 load, keeps {HUB_CODE[g]} origin"))
                    for _f in SD_FAMS:                        # Change 46: both bands take round 2
                      _T, _fl = FAM_TAG[_f], FAM_LABEL[_f]
                      for g in fam_sites(_f, c):
                        if (_f, c, g, site) not in R2_PAIRS:
                            continue          # Change 29: only the routing the scans show
                        vt = fam_tag(_f, g)
                        if a.startswith("UNLOAD_") or a == PUD_R2_UNLOAD:
                            pp_rows.append(_pp(site, st(c, vt, "Unloaded2"),
                                               f"BOM_UNLOAD2_{_T}{HUB_CODE[g]}_{c}", pn,
                                               f"round-2 unload, {_fl} via {HUB_CODE[g]}"))
                        elif a.startswith("SORT_"):
                            pp_rows.append(_pp(site, st(c, vt, "Sorted2"), f"BOM_SORT2_{c}_{vt}",
                                               pn, f"round-2 sort, {_fl} via {HUB_CODE[g]}"))
                        elif a.startswith("LOAD_") or a == PUD_R2_LOAD:
                            pp_rows.append(_pp(site, st(c, vt, "Despatch2"), f"BOM_LOAD2_{c}_{vt}",
                                               pn, f"round-2 load, {_fl} via {HUB_CODE[g]}"))

# ── driver waves: one per origin a zone under this PDC may be served from ─────────────
for site in sorted(DELIVERY_PUD_SET):
    for pn in procs_at.get(site, {}).get("DRIVER_WAVE", []):
        for c in CLASSES:
            for tag in zone_tags_cls(site, c):
                if tag in STG_TAGS:
                    pp_rows.append(_pp(site, delivered(c, tag), f"BOM_DRIVER_{c}_{tag}", pn,
                                       "driver wave, own overnight stage"))
                elif any(tag.startswith(FAM_TAG[_f] + "_") for _f in SD_FAMS):
                    pp_rows.append(_pp(site, delivered(c, tag),
                                       f"BOM_DRIVER_{tag.rsplit('_', 1)[0]}"
                                       f"_{tag.rsplit('_', 1)[1]}_{c}", pn,
                                       f"driver wave, Victoria same-day via {tag.split('_')[-1]}"))
                else:
                    code = tag.split("_")[-1]
                    pp_rows.append(_pp(site, delivered(c, tag), f"BOM_DRIVER_INT_{code}_{c}", pn,
                                       f"driver wave, arrived interstate at {code}"))
production_policies = pd.DataFrame(pp_rows, columns=PP_COLS).drop_duplicates()
write_csv(production_policies, "ProductionPolicies")

# ── the different-site rule, structural ───────────────────────────────────────────────
if TWO_ROUNDS:
    for h in sorted(ARRIVAL_SET):
        at_h = production_policies[production_policies.facilityname == h]
        for _c in CLASSES:
            assert not at_h.bomname.eq(f"BOM_UNLOAD2_{HUB_CODE[h]}_{_c}").any() and \
                   not at_h.bomname.eq(f"BOM_UNLOAD2_V{HUB_CODE[h]}_{_c}").any(), (
                f"{h} consumes its own Despatch1 flavour — a same-site double sort would be possible")
    for d in sorted(ROUND2_PUD_SET):
        at_d = production_policies[production_policies.facilityname == d]
        for c in CLASSES:
            for g in cls_hubs(c):
                if ("INT", c, g, d) not in R2_PAIRS:
                    continue                   # Change 29: unmeasured routing is not wired
                assert at_d.bomname.eq(f"BOM_UNLOAD2_{HUB_CODE[g]}_{c}").any(), (
                    f"{d} cannot unload {HUB_CODE[g]}-arrived {c} — the divert is unreachable")
        assert not at_d.bomname.str.startswith(tuple(["BOM_LOAD_INT", "BOM_LOAD_DIRECT"]
                                              + [f"BOM_LOAD_{FAM_TAG[_f]}" for _f in SD_FAMS] +
                                                "BOM_LOAD_XDOCK")).any(), (
            f"{d} must not run a round-1 load — it holds no Despatch1 flavour")
    print(f"  round 2 wired: no site unloads its own flavour; "
          f"{sorted(_short(p) for p in ROUND2_PUD_SET)} takes every flavour (it has none)")

# ── Change 25: the bypass is reachable, and did not open a same-site double sort ───────
if BYPASS:
    for h in sorted(ARRIVAL_SET):
        at_h = production_policies[production_policies.facilityname == h]
        for c in hub_classes(h):
            assert (at_h.productname == st(c, int_tag(h), "Despatch2")).any(), (
                f"{HUB_CODE[h]} has no direct load for its own {c} arrivals — bypass unreachable")
    print(f"  bypass wired: every arrival site can load its own arrivals straight to Despatch2")

# ══ GUARANTEES ════════════════════════════════════════════════════════════════════════
_out_side = production_policies.productname.str.contains("Despatch2|Delivered", regex=True)
_leak = production_policies.loc[_out_side
                                & production_policies.productname.str.startswith(PICKUP_PREFIXES)]
assert _leak.empty, f"a pickup-origin product reaches Despatch2/Delivered:\n{_leak.head().to_string()}"
# Change 21a/28: a site runs interstate round 1 only for the classes that measurably enter it
for h in sorted(ARRIVAL_SET):
    at_h = production_policies[production_policies.facilityname == h]
    made = sorted({p[:2] for p in at_h.productname
                   if f"_INTERSTATE_Despatch1_{HUB_CODE[h]}" in p})
    assert made == sorted(hub_classes(h)), \
        f"{HUB_CODE[h]} INTERSTATE Despatch1 for {made}, measured arrivals say {sorted(hub_classes(h))}"
print("  source->sink: pickup terminates at a hub/local sink (Change 20b); every interstate "
      "parcel carries its ARRIVAL HUB to the zone (Change 21b)  OK")
print("  class routing: " + " | ".join(f"{HUB_CODE[h]} {hub_classes(h)}" for h in sorted(HUB_SET)))

# --------------------------------------------------------------------------------------
# ## 9. Sourcing, lanes and constraints
# --------------------------------------------------------------------------------------

# ── ProcurementPolicies: the three measured families (Change 28) ──────────────────────
PROC_COLS2 = ["facilityname", "productname", "sourcename", "optimizationpolicy",
              "optimizationpolicyvalue", "status", "unitcost", "unitcostuom",
              "maxsourcingrange", "maxsourcingrangeuom", "notes"]
def _sup_short(s):
    return s.replace("HUB_", "").replace("PUD_", "")
pc_rows = []
for s in sorted(ARRIVAL_SET):
    for c in hub_classes(s):
        for _pres in INT_PRES_LIST:
            pc_rows.append([s, int_pk(c, _pres), "SUP_INT_" + _sup_short(s), "", "",
                            "Include", "", "", "", "",
                            f"interstate arrivals, {c}" + (f", {_pres}" if _pres else "")])
for _f in SD_FAMS:
    for s in fam_any_sites(_f):
        for c in fam_classes(_f, s):
            pc_rows.append([s, f"{c}_{FAM_TAG[_f]}_Pickup", f"SUP_{_f}_" + _sup_short(s),
                            "", "", "Include", "", "", "", "",
                            f"{FAM_LABEL[_f]}, at its first-sort site"])
for p in STG_PUDS:
    for c in CLASSES:
        pc_rows.append([p, dsp_final(c, stage_tag_pud(p)), "SUP_STAGE_" + p.replace("PUD_", ""),
                        "", "", "Include", "", "", "", "", "overnight stage, measured"])
write_csv(pd.DataFrame(pc_rows, columns=PROC_COLS2), "ProcurementPolicies")

# TransportationPolicies  → docs/chain2-observed.md#transportationpolicies
_ref_tp = REF / "TransportationPolicies.csv"
if _ref_tp.exists():
    TP_COLS = list(pd.read_csv(_ref_tp, nrows=0).columns)
else:
    _prev = OUT / "TransportationPolicies.csv"
    assert _prev.exists(), (
        f"no Anura reference at {REF} and no previous output to take the schema from. "
        f"Restore inputs/optilogic (the reference model export) before rebuilding.")
    TP_COLS = list(pd.read_csv(_prev, nrows=0).columns)
    print(f"  WARNING: {REF} is missing — taking the TransportationPolicies column list from the "
          f"previous output instead. That is schema only, not data, but RESTORE THE REFERENCE "
          f"FOLDER: static pass-through tables cannot be regenerated without it.")
_tm = pd.read_csv(FASS / "transport_modes.csv")
MODE_CAP  = {_r.mode: int(_r.capacity_ea) for _r in _tm.itertuples()}
MODE_RATE = dict(zip(_tm["mode"], _tm.rate_per_km))
LINEHAUL_MODES = [_r.mode for _r in _tm.itertuples() if int(_r.linehaul) == 1]

coords = {}
for df, key in [(facilities, "facilityname"), (suppliers, "suppliername"), (customers, "customername")]:
    for n, la, lo in zip(df[key], df["latitude"], df["longitude"]):
        coords[n] = (float(la), float(lo))

def _km(a, b):
    (la1, lo1), (la2, lo2) = a, b
    dphi, dlmb = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dphi/2)**2 + math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(dlmb/2)**2
    return 2 * 6371.0 * math.asin(math.sqrt(h))

tp_rows = []
def lane(o, d, prod, note, mode=None, rule="Prorate"):
    if o not in coords or d not in coords:
        return
    dist = round(_km(coords[o], coords[d]), 2)
    row = {c: "" for c in TP_COLS}
    row.update(originname=o, destinationname=d, productname=prod, status="Include",
               transportdistance=dist, transportdistanceuom="KM", notes=note)
    if mode:
        row.update(modename=mode, averageshipmentsize=MODE_CAP[mode], averageshipmentsizeuom="EA",
                   fixedcost=round(MODE_RATE[mode] * dist, 2), fixedcostrule=rule)
    else:
        row.update(unitcost=0.8, unitcostuom="KM")
    tp_rows.append(row)

hubs, dpuds = sorted(HUB_SET), sorted(DELIVERY_PUD_SET)
r2puds = sorted(ROUND2_PUD_SET)

# ══ CHAIN 1 lives in its own notebook now (Change 28). ═══════════════════════════════
# ══ CHAIN 2 — INTERSTATE + STAGE -> DELIVERY ══════════════════════════════════════════
# 4) SOURCE: interstate arrivals land at their MEASURED entry site (Change 28: six of them)
for s in sorted(ARRIVAL_SET):
    for c in hub_classes(s):
        for _pres in INT_PRES_LIST:             # Change 23: split by presentation when pinned
            lane("SUP_INT_" + _short(s), s, int_pk(c, _pres),
                 "4 SOURCE arrivals: supplier->site")
# 4v) SOURCE: each Victorian band lands at its measured first-sort site (Change 46)
for _f in SD_FAMS:
    for s in fam_any_sites(_f):
        for c in fam_classes(_f, s):
            lane(f"SUP_{_f}_" + _short(s), s, f"{c}_{FAM_TAG[_f]}_Pickup",
                 f"4v SOURCE {FAM_LABEL[_f]}: supplier->site")
# 5) SOURCE: overnight stage lands at its own PDC already delivery-ready (Change 28: all 11)
for p in STG_PUDS:
    for c in CLASSES:
        lane("SUP_STAGE_" + p.replace("PUD_", ""), p, dsp_final(c, stage_tag_pud(p)),
             "5 SOURCE local stage: supplier->PDC")
# 6) round 2: first-sort site -> a DIFFERENT round-2 site (Change 28: both families, six sites)
_r2_sites = sorted(R2_SORT_SITES | set(ROUND2_PUD_SET))   # Change 28c: observed round-2 sites
if TWO_ROUNDS:
    for c in CLASSES:
        for _fk in ARR_FAMS:
            fam = FAM_TAG[_fk]
            for g in fam_sites(_fk, c):
                for h in _r2_sites:
                    if (_fk, c, g, h) not in R2_PAIRS:
                        continue          # Change 29: the scans name the round-2 lanes
                    _leg = "6b" if h in ROUND2_PUD_SET else "6"
                    for m in LINEHAUL_MODES:
                        lane(g, h, dsp1(c, fam, HUB_CODE[g]),
                             f"{_leg} linehaul: site->site (Despatch1, 2nd sort)",
                             mode=m, rule="Treat As Full")
# 6x) the CROSS-DOCK lane: hub -> arrival depot, UNSORTED (Change 28, 12b)
if XDOCK_ENABLED:
    for (g, d, c) in sorted(XDOCK_PAIR):          # Change 29: the measured handing-on lanes
        for m in LINEHAUL_MODES:
            lane(g, d, f"{c}_INTERSTATE_XDock_{HUB_CODE[g]}",
                 "6x linehaul: handling site->sorting site (cross-dock, unsorted)",
                 mode=m, rule="Treat As Full")
# 7) despatch: hub -> delivering PDC. INTERSTATE only — a PDC's own stage is already on site.
# Change 21b: the despatch product now names the ARRIVAL hub, so the whole leg is traceable.
for h in sorted(set(_r2_sites) | ARRIVAL_SET):     # non-R2 sites still despatch their OWN flavour
    for d in dpuds:
        if h == d:
            continue                             # a depot's own despatch to itself is not a lane
        for c in CLASSES:
            for g, _fam_tag in [(x, fam_tag(_f2, x))
                                for _f2 in ARR_FAMS for x in fam_sites(_f2, c)]:
                # g != h is the normal despatch: h forwards volume that ARRIVED at g and was
                # sorted a second time here. g == h would be h despatching what it sorted in
                # round 1 — forbidden, unless Change 25's bypass is on, in which case that is
                # precisely the direct path and it gets its own leg code so the constraints and
                # the reporting can find it.
                if g == h and not BYPASS:
                    continue
                if h in ROUND2_PUD_SET and g == h:
                    continue
                _fk = next(f for f in ARR_FAMS if _fam_tag.startswith(FAM_TAG[f] + "_"))
                if g != h and (_fk, c, g, h) not in R2_PAIRS:
                    continue                     # Change 29: h never sorts g's flavour
                _note = ("7 linehaul: site->PDC (despatch)" if g != h else
                         "7c linehaul: site->PDC (DIRECT, sort bypass)")
                for m in LINEHAUL_MODES:
                    lane(h, d, dsp_final(c, _fam_tag), _note, mode=m, rule="Treat As Full")
# 8) SINK: delivery. Each zone from its own PDC, for the origins that PDC can actually hold.
for r in clusters.itertuples():
    for c in CLASSES:
        for tag in zone_tags_cls(r.pud, c):
            lane(r.pud, r.customername, delivered(c, tag), "8 SINK delivery: PDC->zone", mode="White_Van")

transportation_policies = pd.DataFrame(tp_rows).reindex(columns=TP_COLS)
write_csv(transportation_policies, "TransportationPolicies")

# Change 28: every INT/VIC flavour with delivery demand must have a despatch lane — a flavour
# whose Despatch2 rides no lane is unsatisfiable demand, and the solver would only tell us later.
for c in CLASSES:
    for t in [x for f in ARR_FAMS for x in TAGS_FOR[f][c]]:
        assert (transportation_policies.productname == dsp_final(c, t)).any(), \
            f"no lane carries {dsp_final(c, t)} — its delivery demand cannot be met"
print(f"  lane coverage: every "
      f"{sum(len(TAGS_FOR[f][c]) for f in ARR_FAMS for c in CLASSES)} "
      f"family x flavour despatch product rides at least one lane")

# no intermediate state may ride a lane
assert not transportation_policies.productname.str.contains(
    r"_(?:Unload0|Unloaded|Sorted|Unloaded2|Sorted2)$", regex=True).any(), \
    "an intermediate state has a lane — volume could skip a machine"
# No pickup-origin product may ride a DELIVERY-SIDE lane. Note this is not the same as "no lane
# into a delivering PDC": Bayswater is both a first-mile and a delivering PDC, so its own collection
# leg legitimately carries pickup into it. What must never happen is a pickup product being
# despatched from a hub (or the off-hub sort PDC) to a delivering PDC, or loaded onto a van.
_tp_pick = transportation_policies.productname.str.startswith(PICKUP_PREFIXES)
_despatch_leg = (transportation_policies.originname.isin(set(_r2_sites))
                 & transportation_policies.destinationname.isin(set(dpuds)))
_van_leg = transportation_policies.destinationname.isin(set(clusters.customername))
_bad = transportation_policies[_tp_pick & (_despatch_leg | _van_leg)]
assert _bad.empty, ("a pickup-origin product rides a delivery-side lane:\n"
                    f"{_bad[['originname','destinationname','productname']].head().to_string()}")
TM_COLS = ["modename", "status", "unitcost", "unitcostuom", "fixedcost", "fixedcostrule",
           "averageshipmentsize", "averageshipmentsizeuom", "notes", "co2emissionrate",
           "co2emissionrateuom"]
write_csv(pd.DataFrame([[m, "Include", "", "", "",
                         ("Treat As Full" if m in LINEHAUL_MODES else "Prorate"),
                         MODE_CAP[m], "EA", f"{MODE_RATE[m]} $/km per trip", "", ""]
                        for m in MODE_CAP], columns=TM_COLS), "TransportationModes")

print("  lanes by leg:")
print(transportation_policies.notes.value_counts().sort_index().to_string())

# ── CustomerFulfillmentPolicies / ReplenishmentPolicies ────────────────────
CF_COLS = ["customername", "productname", "sourcename", "status", "unitcost", "unitcostuom",
           "maxsourcingrange", "maxsourcingrangeuom", "notes"]
cf_rows = []
# SINK 1 — delivery zones, from their own PDC, for the origins that PDC can hold
for r in clusters.itertuples():
    for c in CLASSES:
        for tag in zone_tags_cls(r.pud, c):
            cf_rows.append([r.customername, delivered(c, tag), r.pud, "Include", "", "", "", "",
                            "delivered from its PDC"])
# SINKS 2 and 3 (interstate despatch, local terminate) are chain 1's entity.
write_csv(pd.DataFrame(cf_rows, columns=CF_COLS).drop_duplicates(), "CustomerFulfillmentPolicies")

RP_COLS = ["facilityname", "productname", "sourcename", "optimizationpolicy", "optimizationpolicyvalue",
           "status", "unitcost", "unitcostuom", "maxsourcingrange", "maxsourcingrangeuom", "notes"]
rp_rows = []
# hub -> delivering PDC, and off-hub sort PDC -> delivering PDC. INTERSTATE only: a PDC's own
# stage is supplied on site and never replenished from anywhere.
_r2_sites = sorted(R2_SORT_SITES | set(ROUND2_PUD_SET))   # Change 28c
for d in dpuds:
    for c in CLASSES:
        for g, fam in ([(g, "INTERSTATE") for g in cls_hubs(c)]
                       + [(g, _f) for _f in SD_FAMS for g in fam_sites(_f, c)]):
            _tag = int_tag(g) if fam == "INTERSTATE" else fam_tag(fam, g)
            for h in _r2_sites:
                if h != g and h != d:
                    rp_rows.append([d, dsp_final(c, _tag), h, "", "", "Include", "", "", "", "",
                                    "site->PDC despatch"])
            if BYPASS and g != d:
                rp_rows.append([d, dsp_final(c, _tag), g, "", "", "Include", "", "", "", "",
                                "site->PDC despatch (direct)"])
# round 2: each site from the first-sort site; both families (Change 28)
if TWO_ROUNDS:
    for c in CLASSES:
        for g, fam in ([(g, "INTERSTATE") for g in cls_hubs(c)]
                       + [(g, _f) for _f in SD_FAMS for g in fam_sites(_f, c)]):
            for h in _r2_sites:
                if h != g:
                    rp_rows.append([h, dsp1(c, FAM_TAG.get(fam, fam), HUB_CODE[g]), g,
                                    "", "", "Include",
                                    "", "", "", "", "site->site (Despatch1, 2nd sort)"])
# cross-dock: the arrival depots take unsorted freight from every hub (Change 28)
if XDOCK_ENABLED:
    for c in CLASSES:
        for g in sorted(HUB_SET):
            for d in sorted(ARRIVAL_PUD_SET):
                rp_rows.append([d, f"{c}_INTERSTATE_XDock_{HUB_CODE[g]}", g, "", "", "Include",
                                "", "", "", "", "hub->depot cross-dock (unsorted)"])
write_csv(pd.DataFrame(rp_rows, columns=RP_COLS).drop_duplicates(), "ReplenishmentPolicies")

# ── Groups, FlowConstraints and the interstate unload ratio dial ───────────
GROUP_COLS = ["groupname", "grouptype", "membername", "status", "notes"]
grp = []
def add_group(name, gtype, members, note=""):
    grp.extend([[name, gtype, m, "Include", note] for m in members])
add_group("Interstate_Suppliers", "Suppliers",
          ["SUP_INT_" + s.replace("HUB_", "").replace("PUD_", "") for s in sorted(ARRIVAL_SET)])
for _f in SD_FAMS:                       # Change 46: one supplier group per Victorian band
    add_group(f"{_f}_Suppliers", "Suppliers",
              [f"SUP_{_f}_" + s.replace("HUB_", "").replace("PUD_", "")
               for s in fam_any_sites(_f)])
add_group("Stage_Suppliers", "Suppliers",
          ["SUP_STAGE_" + p.replace("PUD_", "") for p in STG_PUDS])
add_group("HUB_Facilities", "Facilities", sorted(HUB_SET))
add_group("PDC_Facilities", "Facilities", sorted(PUD_SET))
write_csv(pd.DataFrame(grp, columns=GROUP_COLS), "Groups")

FC_COLS = ["originname", "originnamegroupbehavior", "destinationname", "destinationnamegroupbehavior",
           "productname", "productnamegroupbehavior", "modename", "modenamegroupbehavior",
           "periodname", "periodnamegroupbehavior", "constrainttype", "constraintvalue",
           "constraintvalueuom", "status", "notes"]
fc_rows = []
def fc(**kw):
    row = {c: "" for c in FC_COLS}; row.update(kw); fc_rows.append(row)

# Change 28: the pickup pins are chain 1's entity.
# Change 23: the interstate unload-ratio FlowConstraints are GONE. They named the shared output
# product on a self-loop lane and bound nothing — the run came out 96-100% ULD against a supposed
# 20-80% band. The ratio is now set structurally in SupplierCapabilities (one presentation, one
# recipe), so there is nothing for this table to do.
# ── Round-2 sort PDC cap (Change 20a) — the DLC hub-overflow divert ───────────────────
# At most DLC_SMALL_SHARE of the delivery-bound volume may take its SECOND sort off-hub. A Max,
# not a Min: the solver only uses DLC if it beats sorting at a hub, so this answers "should volume
# come here?" rather than assuming it does. This is also where the small-parcel share lives until
# products carry a size attribute.
for d in sorted(ROUND2_PUD_SET):
    fc(originname="HUB_Facilities", originnamegroupbehavior="Aggregate",
       destinationname=d, destinationnamegroupbehavior="Aggregate",
       periodname="ALL", periodnamegroupbehavior="Aggregate",
       constrainttype="Max", constraintvalue=r2_pud_vol[d], status="Include",
       notes=f"round-2 sort PDC cap ({DLC_SMALL_SHARE:.0%} of delivery-bound volume)")
# Change 24: minimum viable despatch shipment  → docs/chain2-observed.md#change-24-minimum-viable-despatch-shipment
if DESPATCH_MIN_SHIPMENT > 0:
    _z2p = clusters.set_index("customername")["pud"]
    _int = demand.loc[demand.productname.str.contains("_INTERSTATE_")].copy()
    _int["pud"] = _int.customername.map(_z2p)
    _cell = _int.groupby(["pud", "productname"])["quantity"].sum().unstack(fill_value=0)
    _ceiling = {p: max(min(v, r.sum() - v) for v in r if v > 0) for p, r in _cell.iterrows()}
    _tight = min(_ceiling, key=_ceiling.get)
    assert DESPATCH_MIN_SHIPMENT <= _ceiling[_tight], (
        f"DESPATCH_MIN_SHIPMENT = {DESPATCH_MIN_SHIPMENT:,} EA is above the feasibility ceiling "
        f"of {_ceiling[_tight]:,} EA set by {_short(_tight)} — that depot could not be served by "
        f"any two arcs that both clear the threshold, and NEO would return infeasible")
    _legcode = transportation_policies.notes.str.split(n=1).str[0]
    _arcs = (transportation_policies.loc[_legcode.isin(DESPATCH_MIN_LEGS),
                                         ["originname", "destinationname"]]
             .drop_duplicates().sort_values(["originname", "destinationname"]))
    for _o, _d in _arcs.itertuples(index=False):
        fc(originname=_o, destinationname=_d, periodname="ALL",
           periodnamegroupbehavior="Aggregate", constrainttype="Conditional Min",
           constraintvalue=DESPATCH_MIN_SHIPMENT, constraintvalueuom="EA", status="Include",
           notes="minimum viable despatch shipment — run nothing, or run a real load")
    print(f"  Change 24: Conditional Min {DESPATCH_MIN_SHIPMENT:,} EA on {len(_arcs)} arcs "
          f"(legs {'/'.join(DESPATCH_MIN_LEGS)}); ceiling {_ceiling[_tight]:,} EA at "
          f"{_short(_tight)}; +{len(_arcs)} binaries, the model is now a MIP")

# ── Change 25: the bypass controls ────────────────────────────────────────────────────
# Both rows here NAME THE PRODUCT, which Change 24's rows deliberately do not. The direct arcs
# share their (origin, destination) pairs with the normal despatch arcs, so an unnamed
# constraint would bind the two together and control neither.
if BYPASS:
    _byp = transportation_policies.notes.str.startswith("7c ")
    _byp_arcs = (transportation_policies.loc[_byp, ["originname", "destinationname", "productname"]]
                 .drop_duplicates().sort_values(["originname", "destinationname", "productname"]))
    if INTERSTATE_BYPASS == "min_truckload":
        for _o, _d, _p in _byp_arcs.itertuples(index=False):
            fc(originname=_o, destinationname=_d, productname=_p,
               periodname="ALL", periodnamegroupbehavior="Aggregate",
               constrainttype="Conditional Min", constraintvalue=BYPASS_MIN_SHIPMENT,
               constraintvalueuom="EA", status="Include",
               notes="bypass runs at truckload scale or not at all")
    if BYPASS_MAX_SHARE is not None:
        for _h in sorted(HUB_SET):
            for _c in hub_classes(_h):
                fc(originname=_h, destinationname="PDC_Facilities",
                   destinationnamegroupbehavior="Aggregate",
                   productname=dsp_final(_c, int_tag(_h)), productnamegroupbehavior="Aggregate",
                   periodname="ALL", periodnamegroupbehavior="Aggregate", constrainttype="Max",
                   constraintvalue=int(round(BYPASS_MAX_SHARE * IN_by_hub_class[(_h, _c)])),
                   constraintvalueuom="EA", status="Include",
                   notes=f"at most {BYPASS_MAX_SHARE:.0%} of {HUB_CODE[_h]} {_c} arrivals may "
                         f"skip the second sort")
    # ── Change 28: sort-round bands + cross-dock bands, all measured ──────────────────
    # The direct-despatch product from a site is the only place it despatches its OWN flavour,
    # so a product-named Min/Max from the site to the PDC group binds the single-sort share and
    # nothing else. Bands are OBS_SINGLE ± SORT_BAND. For arrival DEPOTS the Min uses direct
    # arrivals only (the pinned part) and the Max adds the cross-dock intake, since the depot
    # also single-sorts that volume — conservative on both sides.
    if BYPASS_CONSTRAIN:
        _n_band = 0
        for _fam, _sites_of, _tag_of, _vol in (
                [(f, (lambda ff: lambda c: fam_sites(ff, c))(f),
                  (lambda ff: lambda s: fam_tag(ff, s))(f), VOL[f]) for f in ARR_FAMS]):
            for _c in CLASSES:
                for _s in _sites_of(_c):
                    _meas = OBS_SINGLE.get((_fam, _c, HUB_CODE[_s]))
                    if _meas is None or _meas[1] < dial("BAND_MIN_ARTICLES"):
                        continue
                    _share = _meas[0]
                    # ── Change 29b: two things this band cannot assume ────────────────
                    # (1) If the site has no round-2 lane out for this family and class,
                    # there is nowhere else its freight COULD go — single-sort is
                    # structural, and a Min here constrains nothing while risking a
                    # violation. Skip it.
                    if not any((_fam, _c, _s, _h2) in R2_PAIRS for _h2 in ARRIVAL_SET):
                        continue
                    # (2) The constraint names an arc OUT of the site, so it can only bind
                    # freight that rides one. A site that also DELIVERS keeps its own
                    # zones' parcels on site with no lane at all — at Bayswater that was
                    # 100% of its interstate parcel post, which made the Min unsatisfiable
                    # by construction (NEO reported it, violation 727 of 727). Base the
                    # band on what can actually travel.
                    _tot = _vol[(_s, _c)]
                    _ride = sum(MIX.get((_p2, _tag_of(_s), _c), 0)
                                for _p2 in sorted(DELIVERY_PUD_SET) if _p2 != _s)
                    if _ride <= 0:
                        continue
                    _xd = XDOCK_AT.get((_s, _c), 0) if _fam == "INT" else 0
                    _lo = int(math.floor(max(_share - SORT_BAND, 0.0) * _ride
                                         * max(_tot - _xd, 0) / max(_tot, 1)))
                    _hi = int(math.ceil(min(_share + SORT_BAND, 1.0) * _ride))
                    for _t, _v in (("Min", _lo), ("Max", _hi)):
                        fc(originname=_s, destinationname="PDC_Facilities",
                           destinationnamegroupbehavior="Aggregate",
                           productname=dsp_final(_c, _tag_of(_s)), productnamegroupbehavior="Aggregate",
                           periodname="ALL", periodnamegroupbehavior="Aggregate",
                           constrainttype=_t, constraintvalue=_v, constraintvalueuom="EA",
                           status="Include",
                           notes=f"Change 29b: {_fam} single-sort share at {HUB_CODE[_s]} {_c} "
                                 f"held to measured {_share:.1%} +/- {SORT_BAND:.0%}, on the "
                                 f"{_ride:,} EA that leaves the site on a lane")
                    _n_band += 1
        print(f"  Change 29b: single-sort share banded on {_n_band} family x site x class cells "
              f"(measured +/- {SORT_BAND:.0%}) — only where a round-2 lane offers an "
              f"alternative, and only on volume that rides a lane out")
    # ── Change 29: round-2 ROUTING banded to the measured pairs ───────────────────────
    # The single-sort bands above say HOW MUCH leaves a site sorted once. These say where the
    # rest goes. The despatch product names the origin flavour, and only `_h` sorts `_g`'s
    # flavour a second time, so an (origin, destination, product) Min/Max binds exactly this lane.
    if BYPASS_CONSTRAIN:
        _n_r2 = 0
        for (_fam, _c, _g, _h) in sorted(R2_PAIRS, key=lambda k: (k[0], k[1], str(k[2]), str(k[3]))):
            _share = OBS_ROUND2.get((_fam, _c, HUB_CODE[_g], HUB_CODE[_h]))
            _vol = VOL[_fam].get((_g, _c), 0)
            if not _share or not _vol:
                continue
            _tag = fam_tag(_fam, _g)
            for _t, _v in (("Min", int(math.floor(max(_share - SORT_BAND, 0.0) * _vol))),
                           ("Max", int(math.ceil(min(_share + SORT_BAND, 1.0) * _vol)))):
                fc(originname=_g, destinationname=_h,
                   productname=dsp1(_c, FAM_TAG[_fam], HUB_CODE[_g]),
                   productnamegroupbehavior="Aggregate",
                   periodname="ALL", periodnamegroupbehavior="Aggregate",
                   constrainttype=_t, constraintvalue=_v, constraintvalueuom="EA",
                   status="Include",
                   notes=f"Change 29: {_share:.1%} +/- {SORT_BAND:.0%} of {_fam} {_c} arriving at "
                         f"{HUB_CODE[_g]} takes its second sort at {HUB_CODE[_h]} (measured)")
            _n_r2 += 1
        print(f"  Change 29: round-2 routing banded on {_n_r2} measured lanes")
    if XDOCK_ENABLED:
        _n_xd = 0
        for (_g, _d, _c) in sorted(XDOCK_PAIR):
                _v = XDOCK_PAIR[(_g, _d, _c)]
                if not _v:
                    continue
                for _t, _val in (("Min", int(math.floor(_v * (1 - SORT_BAND)))),
                                 ("Max", int(math.ceil(_v * (1 + SORT_BAND))))):
                    fc(originname=_g, destinationname=_d,
                       productname=f"{_c}_INTERSTATE_XDock_{HUB_CODE[_g]}",
                       productnamegroupbehavior="Aggregate",
                       periodname="ALL", periodnamegroupbehavior="Aggregate",
                       constrainttype=_t, constraintvalue=_val, constraintvalueuom="EA",
                       status="Include",
                       notes=f"Change 29: cross-dock {HUB_CODE[_g]}->{HUB_CODE[_d]} {_c} held to "
                             f"measured {_v:,} EA +/- {SORT_BAND:.0%}")
                _n_xd += 1
        print(f"  Change 29: cross-dock banded on {_n_xd} measured lanes")
    print(f"  Change 25: {len(_byp_arcs)} direct arcs"
          + (f", Conditional Min {BYPASS_MIN_SHIPMENT:,} EA each"
             if INTERSTATE_BYPASS == "min_truckload" else ", cost decides the volume")
          + (f", capped at {BYPASS_MAX_SHARE:.0%} of arrivals" if BYPASS_MAX_SHARE else ""))

# ── Change 29b: a Min may not exceed what its own arcs can physically carry ───────────
# NEO reported three violated Mins (Bayswater 727 of 727, Melbourne North, Sunshine West) and
# nothing here had checked for them: a band named an arc OUT of a site, but that site also
# DELIVERS, and its own zones' freight never rides an arc. Bayswater's whole interstate parcel
# post is delivered by Bayswater, so the arc could carry nothing at all. This asserts every
# product-named Min against the demand reachable on the lanes that actually exist.
_z2p = clusters.set_index("customername")["pud"]
_dm = demand.copy()
_dm["pud"] = _dm.customername.map(_z2p)
_reachable = _dm.groupby(["pud", "productname"])["quantity"].sum()
_n_chk = 0
for _r in fc_rows:
    if _r["constrainttype"] != "Min" or "_Despatch2" not in str(_r["productname"]):
        continue
    _pn, _on = _r["productname"], _r["originname"]
    _dst = set(transportation_policies.loc[
        (transportation_policies.originname == _on)
        & (transportation_policies.productname == _pn), "destinationname"])
    _del = _pn.replace("_Despatch2", "_Delivered")
    _have = int(sum(v for (_p3, _pn3), v in _reachable.items()
                    if _pn3 == _del and _p3 in _dst))
    assert _r["constraintvalue"] <= _have, (
        f"FlowConstraint Min {_r['constraintvalue']:,} EA on {_short(_on)} -> {_pn} exceeds the "
        f"{_have:,} EA of that flavour demanded at the {len(_dst)} depots its lanes reach. NEO "
        f"would report this as a violation. A site that also delivers keeps its own zones' "
        f"freight on site with no lane, so the band must be based on what travels.")
    _n_chk += 1
print(f"  Change 29b: {_n_chk} product-named Min constraints checked against reachable demand "
      f"— all satisfiable on the lanes that exist")

write_csv(pd.DataFrame(fc_rows).reindex(columns=FC_COLS), "FlowConstraints")

# --------------------------------------------------------------------------------------
# ## 9c. Change 33 — the work-centre mix as a user-defined constraint
#
# Optilogic cadence Q2: *"split work-centre flow using a defined variable and defined constraint,
# rather than the current hard-coded product splits."* This is the pattern from their
# `AusPost - Sort ByPass v2` model (`inputs/optilogic-model-example/UserDefined*.csv`), generalised
# and driven from `inputs/factors_assumed/unload_mix.csv`.
#
# Default is **off** (`INTERSTATE_UNLOAD=fixed`, `VIC_UNLOAD=free`) — the build is byte-identical
# until a dial is moved, the same way Changes 24 and 25 were introduced.
# --------------------------------------------------------------------------------------

# Change 33 — THE WORK-CENTRE MIX AS A USER-DEFINED CONSTRAINT  → docs/chain2-observed.md#change-33-the-work-centre-mix
UDV_COLS = ["variablename", "termname", "status", "coefficient", "type", "uom",
            "facilityname", "facilitynamegroupbehavior", "originname", "originnamegroupbehavior",
            "destinationname", "destinationnamegroupbehavior", "productname",
            "productnamegroupbehavior", "modename", "modenamegroupbehavior", "workcentername",
            "workcenternamegroupbehavior", "bomname", "bomnamegroupbehavior", "processname",
            "processnamegroupbehavior", "periodname", "periodnamegroupbehavior",
            "consideredinventory", "countingrule", "notes"]
UDC_COLS = ["constraintname", "variablename", "constrainttype", "constraintvalue", "status",
            "notes", "conditionvariablename", "conditionminvalue", "conditionmaxvalue"]
# The two Anura UDC type strings. CONFIRMED working in the 2026-08-13 solve.
UDC_MIN, UDC_MAX = "Min", "Max"
# A CONSTRAINT NAME MAY NOT REUSE A VARIABLE NAME. Anura holds both in one master-entity
# namespace, and NEO's "Unique Master Entity" rule DROPS the duplicate row — silently, during
# preprocessing. The first build named each constraint after the variable it bounds; all 26 rows
# were dropped, the variables still evaluated, and the solve came back 100% ULD with every upper
# bound violated and no error on the face of it. Prefix, and assert the namespaces stay disjoint.
UDC_PREFIX = "CONSTR_"

MIX_FAMILIES = {}                       # family -> the predicate saying which classes a site takes
if BOUNDED_PRES: MIX_FAMILIES["INTERSTATE"] = hub_classes
# Change 46: ONE Victorian mix bound spanning both bands. Metro and regional freight come off
# the same docks, so splitting the constraint would double the rows for no measured reason —
# unload_mix.csv keeps its single VIC family and the product group carries both.
if VIC_BOUNDED:  MIX_FAMILIES["VIC"]        = lambda s: sorted(
    {c for f in SD_FAMS for c in fam_classes(f, s)})
# The denominator measures UNLOAD at a site, so the volume to size it against is what the site
# is SUPPLIED. Do NOT use IN_by_hub_class here: that is the round-1 SORT workload, which credits
# cross-docked freight to the site that sorts it rather than the site that unloads it. The
# 2026-08-13 solve made the gap visible — this check said 12,603 EA at MPF where the variable
# actually measured 22,597, and said 4,518 at Bayswater where it measured 820.
_SUP_PREFIX = {"INTERSTATE": ["SUP_INT_"], "VIC": [f"SUP_{f}_" for f in SD_FAMS]}
_supcap = supplier_capabilities.groupby("suppliername").supplycapacity.sum()

udv_rows, udc_rows, _mix_report = [], [], []

def _udv(var, term, coeff, fac, prodgrp, proc, note):
    r = {c: "" for c in UDV_COLS}
    r.update(variablename=var, termname=term, status="Include", coefficient=round(coeff, 6),
             type="Production", uom="EA", facilityname=fac,
             productname=prodgrp, productnamegroupbehavior="Aggregate", notes=note)
    if proc:
        r.update(processname=proc, processnamegroupbehavior="Enumerate")
    udv_rows.append(r)

def _udc(name, var, ctype, note):
    udc_rows.append({"constraintname": name, "variablename": var, "constrainttype": ctype,
                     "constraintvalue": 0, "status": "Include", "notes": note,
                     "conditionvariablename": "", "conditionminvalue": "", "conditionmaxvalue": ""})

_cap = dict(zip(work_centers.workcentername, work_centers.throughputcapacity))

for fam, classes_at in MIX_FAMILIES.items():
    _mix = UNLOAD_MIX[UNLOAD_MIX.family == fam]
    assert len(_mix), f"unload_mix.csv has no rows for family {fam}"
    # Change 46: "VIC" is a family of the CONSTRAINT, not of the model — it spans both
    # Victorian bands, so the denominator lists the products of each.
    _tags = ["INTERSTATE"] if fam == "INTERSTATE" else [FAM_TAG[f] for f in SD_FAMS]
    grp_prod = f"grp_MIX_{fam}_Unloaded"
    add_group(grp_prod, "Products",
              [f"{c}_{t}_Unloaded" for c in CLASSES for t in _tags],
              f"Change 33: denominator for the {fam} unload mix bound")
    sites = [s for s in sorted(ARRIVAL_SET) if classes_at(s)]
    for site in sites:
        code = HUB_CODE[site]
        vol  = float(sum(_supcap.get(p + _short(site), 0) for p in _SUP_PREFIX[fam]))
        if vol <= 0:
            continue
        for _r in _mix[_mix.constrain].itertuples():
            procs = procs_at.get(site, {}).get(_r.method, [])
            assert procs, (f"Change 33: {code} has no {_r.method} process, so the {fam} mix cannot "
                           f"be bounded there. Either the site has no such dock or unload_mix.csv "
                           f"names a method this model does not build.")
            lo = max(0.0, _r.target_share - WC_MIX_BAND)
            hi = min(1.0, _r.target_share + WC_MIX_BAND)
            for proc in procs:
                base = f"{code}_{fam}_MIX_{_r.method}"
                tag  = f"{_r.target_share:.0%} +/-{WC_MIX_BAND:.0%} of {fam} unload at {code}"
                _udv(f"{base}_LO", "Total",  -lo, site, grp_prod, "",   f"{tag}: lower bound total")
                _udv(f"{base}_LO", "ThisWC",  1.0, site, grp_prod, proc, f"{tag}: lower bound method")
                _udv(f"{base}_HI", "Total",  -hi, site, grp_prod, "",   f"{tag}: upper bound total")
                _udv(f"{base}_HI", "ThisWC",  1.0, site, grp_prod, proc, f"{tag}: upper bound method")
                _udc(f"{UDC_PREFIX}{base}_LO", f"{base}_LO", UDC_MIN,
                     f"{_r.method} >= {lo:.0%} of {fam} unload")
                _udc(f"{UDC_PREFIX}{base}_HI", f"{base}_HI", UDC_MAX,
                     f"{_r.method} <= {hi:.0%} of {fam} unload")
            # NECESSARY feasibility check. The dock is shared with other families, so clearing
            # this does not guarantee feasibility — but failing it guarantees infeasibility, and
            # it is far cheaper to find out here than in a NEO run.
            need = lo * vol
            have = _cap.get(f"WC_{_r.method}_{_short(site)}", 0)
            assert need <= have, (
                f"Change 33: bounding {fam} {_r.method} at {code} to >= {lo:.0%} needs "
                f"{need:,.0f} EA/day but that work centre holds {have:,.0f} — infeasible before "
                f"any other family competes for it. Lower the share, widen the band, or resize "
                f"the dock.")
            _mix_report.append((fam, code, _r.method, lo, hi, need, have, vol))

# CHANGE 39 — THE MANUAL-SORT FLOOR  → docs/chain2-observed.md#change-39-the-manual-sort-floor-2
MANUAL_FLOOR = {}
if MANUAL_SORT_SHARE > 0:
    _grp_sort = "grp_MIX_SORT_R1"
    add_group(_grp_sort, "Products",
              [f"{c}_{t}_Sorted" for c in CLASSES
               for t in ["INTERSTATE"] + [FAM_TAG[f] for f in SD_FAMS]],
              "Change 39: denominator for the manual-sort floor — ROUND-1 sort only")
    # *_by_site_class, NOT the IN_by_hub_class alias: that alias exists only for interstate
    # (it is a legacy name kept for downstream cells) and there is no VIC_by_hub_class at all.
    # Reaching for the symmetric name that looks like it should exist is a NameError.
    _r1_by_hub = {h: sum(sum(VOL[f].get((h, c), 0) for f in ARR_FAMS)
                         - XDOCK_HAND.get((h, c), 0) for c in CLASSES)
                  for h in sorted(HUB_SET)}
    _r1_hub_tot = sum(_r1_by_hub.values())
    assert _r1_hub_tot > 0, "Change 39: the hubs sort nothing in round 1 — cannot site a floor"
    _hub_share = MANUAL_SORT_SHARE * D_TOTAL / _r1_hub_tot
    assert _hub_share < 1.0, (
        f"Change 39: MANUAL_SORT_SHARE={MANUAL_SORT_SHARE:.0%} of {D_TOTAL:,} EA needs "
        f"{MANUAL_SORT_SHARE * D_TOTAL:,.0f} EA of hand sort, more than the {_r1_hub_tot:,.0f} EA "
        f"the hubs sort in round 1. The floor cannot be met at the hubs alone.")
    for site in sorted(HUB_SET):
        procs = procs_at.get(site, {}).get("SORT_MANUAL", [])
        assert procs, (
            f"Change 39: {HUB_CODE[site]} has no SORT_MANUAL process, so the manual floor cannot "
            f"be applied there. Every hub is granted one where SORT_SITES is built — this means "
            f"the topology changed under this cell.")
        code = HUB_CODE[site]
        need = _hub_share * _r1_by_hub[site]
        MANUAL_FLOOR[code] = need
        for proc in procs:
            base = f"{code}_SORT_MIX_MANUAL"
            tag  = f"{_hub_share:.1%} of round-1 sort at {code} is manual"
            _udv(f"{base}_LO", "Total",  -_hub_share, site, _grp_sort, "",
                 f"{tag}: floor, total round-1 sort")
            _udv(f"{base}_LO", "ThisWC",  1.0, site, _grp_sort, proc,
                 f"{tag}: floor, the manual share of it")
            _udc(f"{UDC_PREFIX}{base}_LO", f"{base}_LO", UDC_MIN,
                 f"SORT_MANUAL >= {_hub_share:.1%} of round-1 sort at {code} "
                 f"(= {MANUAL_SORT_SHARE:.0%} of delivered volume, network-wide)")
        have = _cap.get(f"WC_SORT_MANUAL_{_short(site)}", 0)
        assert need <= have, (
            f"Change 39: a {_hub_share:.1%} manual floor at {code} needs {need:,.0f} EA/day of "
            f"hand sort but WC_SORT_MANUAL holds {have:,.0f}. Lower MANUAL_SORT_SHARE or raise "
            f"the SORT_MANUAL rate in machine_rates.csv.")
    _mtot = sum(MANUAL_FLOOR.values())
    print(f"  Change 39: manual-sort floor {MANUAL_SORT_SHARE:.0%} of DELIVERED volume "
          f"= {_hub_share:.1%} of round-1 hub sort -> {_mtot:,.0f} EA/day by hand ("
          + ", ".join(f"{k} {v:,.0f}" for k, v in sorted(MANUAL_FLOOR.items()))
          + f") = {_mtot / D_TOTAL:.2%} of the {D_TOTAL:,} EA delivered")

if MIX_FAMILIES or MANUAL_FLOOR:
    # ── the two checks the 2026-08-13 run earned ─────────────────────────────────────
    _vn = {r["variablename"] for r in udv_rows}
    _cn = {r["constraintname"] for r in udc_rows}
    _clash = sorted(_vn & _cn)
    assert not _clash, (
        f"Change 33: {len(_clash)} constraint name(s) reuse a variable name, e.g. {_clash[:2]}. "
        f"Anura keeps both in ONE master-entity namespace and NEO drops the duplicate row during "
        f"preprocessing — the variables still evaluate, so the solve looks fine while nothing is "
        f"bounded. Keep the {UDC_PREFIX!r} prefix.")
    assert {r["variablename"] for r in udc_rows} <= _vn, \
        "Change 33: a constraint points at a variable that was never defined"
    # Terms filter by PROCESS, not by work centre, so a term only measures the whole machine if
    # no OTHER process on that machine can produce what the term counts. This used to be checked
    # with a one-process-per-work-centre proxy, which Change 48 broke: the scaled round-2 clones
    # share a machine with their base process, so 44 work centres run two. The proxy said the
    # bounds were unsafe; they are not, because both bounded groups are ROUND-1 products
    # (`grp_MIX_*_Unloaded` is `_Unloaded`, `grp_MIX_SORT_R1` is `_Sorted`) and a clone only ever
    # makes `_Unloaded2` / `_Sorted2` / `_Despatch2`. So test the thing the guard is FOR — two
    # processes on one machine both producing into a bounded group — rather than its proxy, and
    # the check keeps working if someone later points a clone at a round-1 product.
    _bounded = {r[2] for r in grp if r[0].startswith("grp_MIX_") and r[1] == "Products"}
    _wc_of = dict(zip(processes.processname, processes.workcentername))
    _hits = {}
    for _r in production_policies.itertuples():
        if _r.productname in _bounded and _r.processname in _wc_of:
            _hits.setdefault(_wc_of[_r.processname], set()).add(_r.processname)
    _multi = sorted(w for w, ps in _hits.items() if len(ps) > 1)
    assert not _multi, (
        f"Change 33: {len(_multi)} work centre(s) run more than one process producing a BOUNDED "
        f"product, e.g. {_multi[:2]}. The mix terms filter on ONE process name, so they no longer "
        f"measure the whole machine. Widen the term to a process GROUP per work centre.")
    if R2_PROCS:
        _leak = sorted({_r.processname for _r in production_policies.itertuples()
                        if _r.processname in R2_PROCS and _r.productname in _bounded})
        assert not _leak, (f"Change 48: {len(_leak)} scaled round-2 process(es) produce a product "
                           f"the mix bounds count, e.g. {_leak[:2]} — the bound would now be "
                           f"measuring round-2 work it was never sized for")
    write_csv(pd.DataFrame(udv_rows).reindex(columns=UDV_COLS), "UserDefinedVariables")
    write_csv(pd.DataFrame(udc_rows).reindex(columns=UDC_COLS), "UserDefinedConstraints")
    write_csv(pd.DataFrame(grp, columns=GROUP_COLS), "Groups")     # rewritten with the mix groups
    print(f"  Change 33: mix bounded for {', '.join(sorted(MIX_FAMILIES)) or 'nothing'} — "
          f"{len(udv_rows)} variable rows, {len(udc_rows)} constraints, "
          f"{len(MIX_FAMILIES)} product groups")
    print(f"    {'family':<11}{'site':<6}{'method':<19}{'band':<16}{'needs':>10}{'dock':>12}"
          f"{'family vol':>12}")
    for fam, code, m, lo, hi, need, have, vol in _mix_report:
        print(f"    {fam:<11}{code:<6}{m:<19}{lo:>5.0%}-{hi:<10.0%}{need:>10,.0f}{have:>12,.0f}"
              f"{vol:>12,.0f}")
    print(f"    constraint names carry the {UDC_PREFIX!r} prefix — a constraint may not reuse a "
          f"variable name (NEO drops the row, silently)")
else:
    # The notebook OWNS its output folder. Leaving a previous run's mix tables behind would put
    # constraints into Cosmic Frog that this build no longer intends — the half-written-folder
    # hazard, one table at a time. Remove them rather than warning about them.
    _stale = [n for n in ("UserDefinedVariables", "UserDefinedConstraints")
              if (OUT / f"{n}.csv").exists()]
    for _n in _stale:
        (OUT / f"{_n}.csv").unlink()
    print("  Change 33/39: work-centre mix bounds and manual floor OFF "
          f"(INTERSTATE_UNLOAD={INTERSTATE_UNLOAD}, VIC_UNLOAD={VIC_UNLOAD}, "
          f"MANUAL_SORT_SHARE={MANUAL_SORT_SHARE}) — "
          "no UserDefined* tables written, build is byte-identical"
          + (f"; removed {len(_stale)} stale table(s) from a previous bounded run" if _stale else ""))

# --------------------------------------------------------------------------------------
# ## 9b. Change 28 — chain 2 IS the measurement; verify it
#
# Under Change 27 this section quantified the stage the model could NOT represent (16%). This
# entity represents all of it, so the cell flips to **verification**: the emitted tables must
# reproduce the measured three-way split exactly. (The frozen OBS tables were already checked
# against the scan cache in section 0b.)
# --------------------------------------------------------------------------------------

# ── Change 28 verification: the emitted model equals the measurement ──────────────────
_sc = pd.read_csv(OUT / "SupplierCapabilities.csv")
_stage_out = int(_sc.loc[_sc.suppliername.str.startswith("SUP_STAGE_"), "supplycapacity"].sum())
assert _stage_out == STAGE_TOT, f"stage caps {_stage_out:,} != measured {STAGE_TOT:,}"
_vic_out = int(_sc.loc[_sc.suppliername.str.startswith(
    tuple(f"SUP_{f}_" for f in SD_FAMS)), "supplycapacity"].sum())
assert _vic_out == VIC_TOT, f"Victorian caps {_vic_out:,} != measured {VIC_TOT:,}"
for _f in SD_FAMS:                       # Change 46: and each band on its own
    _fo = int(_sc.loc[_sc.suppliername.str.startswith(f"SUP_{_f}_"), "supplycapacity"].sum())
    assert _fo == FAM_TOT[_f], f"{FAM_LABEL[_f]} caps {_fo:,} != measured {FAM_TOT[_f]:,}"
_int_out = int(_sc.loc[_sc.suppliername.str.startswith("SUP_INT_"), "supplycapacity"].sum())
assert _int_out >= IN_TOT, "interstate caps below the measured arrivals"
print(f"  supplier caps: kept at depot {_stage_out:,} = measured | "
      + " | ".join(f"{FAM_LABEL[f]} {FAM_TOT[f]:,} = measured" for f in SD_FAMS)
      + f" | interstate {_int_out:,} (vs flows {IN_TOT:,}; ceil per presentation + "
        f"cross-dock shift)")
# The measurement, off the factors, so this line cannot go stale when OBS_COHORT moves.
# Weight the SHARES by measured demand — not by obs_joint's `articles`, which counts only the
# cells that survived the flavour filters and so understates whichever family lost most cells
# (interstate here, which reads 26.9% instead of 30.1% if you sum it that way).
_mj = pd.read_csv(FOBS / "obs_joint.csv")
# NOT tag.split("_"): METRO_DEPOT contains the separator, so a naive split reads it as family
# METRO and folds the overnight stage into the same-day metro family — and the line balances
# anyway, which is how it went unnoticed. `tag_family` is the exporter's own parser, and
# C2_FAMILY then puts the four families back into the three this notebook builds.
_mj["fam"] = _mj.tag.map(lambda t: C2_FAMILY[_tag_family(t)])
_mdem = pd.read_csv(FOBS / "obs_demand.csv").set_index(["pud", "cls"]).articles
_mj["ea"] = _mj.share * _mj.set_index(["pud", "cls"]).index.map(_mdem)
_mtot = _mj.ea.sum()
_MEAS = {f: _mj.ea[_mj.fam == f].sum() / _mtot for f in ("STG",) + ARR_FAMS}
_om = pd.read_csv(OUT / "OriginMix.csv").set_index("pud")
for p in sorted(DELIVERY_PUD_SET):
    _stg = _om.loc[p, stage_tag_pud(p)]
    _meas = 100 * sum(STAGE_BY_PUD[(p, c)] for c in CLASSES) / max(
        sum(int(D_pud_class.loc[p, c]) for c in CLASSES), 1)
    assert abs(_stg - _meas) < 0.5, f"{p}: OriginMix stage {_stg} vs measured {_meas:.2f}"
_MODEL = dict({"STG": STAGE_TOT}, **{f: FAM_TOT[f] for f in ARR_FAMS})
print("  OriginMix: every depot's stage share equals the measurement; four-way split "
      + " / ".join(f"{100*_MODEL[f]/D_TOTAL:.1f}% {FAM_LABEL[f]}" for f in ("STG",) + ARR_FAMS))
print("    scans, same cohort:                                        "
      + " / ".join(f"{100*_MEAS[f]:.1f}% {FAM_LABEL[f]}" for f in ("STG",) + ARR_FAMS))
# ── Change 29: STAGE 4 — where the model despatches from, against where the scans do ──
# This matrix is deliberately NOT pinned. Stages 1-3 (arrivals, cross-dock, round-2 routing) fix
# where a parcel is sorted last; the depot split is then set by the origin mix, which is itself
# measured. So stage 4 is a place the two independent measurements have to agree, and reporting
# the gap is worth more than constraining it away — a Min here on top of the origin-mix pins is
# how a model goes infeasible for a reason nobody can find.
_exp = {}
for (_c, _x, _p), _sh in OBS_DELIVERY.items():
    _exp.setdefault((_c, _x), {})[_p] = _sh
# The model's product flavour names the ARRIVAL site; the scans' stage 4 is keyed on the site
# that sorted it LAST. For single-sort freight those are the same site, for the rest they are
# not — so the modelled matrix has to be pushed through the round-2 routing before the two can
# be compared at all. (Comparing them unpushed reads a 44% error at Bayswater that is really
# just BAY-arrived freight leaving from wherever it took its second sort.)
_mod = {}
for _p in sorted(DELIVERY_PUD_SET):
    for _c in CLASSES:
        for _t in zone_tags_cls(_p, _c):
            if _t in STG_TAGS:
                continue
            _v = MIX.get((_p, _t, _c), 0)
            if not _v:
                continue
            _fam = next(f for f in ARR_FAMS if _t.startswith(FAM_TAG[f] + "_"))
            _g = _t.split("_")[-1]
            for _h in [_g] + [HUB_CODE[h] for (f, cc, gg, h) in R2_PAIRS
                              if (f, cc, HUB_CODE[gg]) == (_fam, _c, _g)]:
                _sh = (OBS_ROUND2.get((_fam, _c, _g, "ONCE"), 0.0) if _h == _g
                       else OBS_ROUND2.get((_fam, _c, _g, _h), 0.0))
                if _sh:
                    _mod.setdefault((_c, _h), {})[_p] = _mod.setdefault((_c, _h), {}).get(_p, 0) + _v * _sh
_dev = []
for (_c, _h), _row in sorted(_mod.items()):
    _tot = sum(_row.values())
    if _tot < dial("BAND_MIN_ARTICLES") or (_c, _h) not in _exp:
        continue
    for _p in set(_row) | set(_exp[(_c, _h)]):
        _dev.append((abs(_row.get(_p, 0) / _tot - _exp[(_c, _h)].get(_p, 0.0)), _c, _h, _p, _tot))
_dev.sort(reverse=True)
# Volume-weighted, because the unweighted mean is dominated by thin EP cells: EP interstate and
# EP Victoria collapsed to TPF/MGF under the 2,000-EA class-site floor, so every other site's EP
# row is modelled as arriving from elsewhere while the scans show it arriving locally. That is
# the floor's cost showing up where it should — in a check, by name.
_wv = sum(_d * _t for _d, _c, _h, _p, _t in _dev) / max(sum(_t for *_r, _t in _dev), 1)
print(f"  Change 29 stage-4 check (NOT pinned — two independent measurements meeting): modelled "
      f"last-sort-site -> depot split vs measured over {len(_dev)} cells — volume-weighted mean "
      f"absolute deviation {_wv:.1%} (unweighted {sum(d for d, *_ in _dev)/max(len(_dev),1):.1%}), "
      f"worst " + ", ".join(f"{_c}/{_h}->{_short(_p)} {_d:.0%}"
                            for _d, _c, _h, _p, _t in _dev[:3]))
print("  Change 28 verification passed — chain 2 is the measurement")

# --------------------------------------------------------------------------------------
# ## 10. Periods and elapsed time
#
# > **Dormant.** `N_PERIODS = 1`, so no `Periods.csv` is written and section 10b does not apply. The
# > elapsed-time reference below is independent of the period split and is still produced.
#
# `Periods` is not in our reference model, so the schema below is assumed and needs confirming against
# Anura. Cumulative elapsed time needs a Cosmic Frog user-defined variable, which is not expressible as
# an input CSV — the leg times are exported as a reference table for whoever configures the UDV.
# --------------------------------------------------------------------------------------

if N_PERIODS > 1:
    periods = pd.DataFrame([
        ["AM", "Include", 1, "12am to 12pm. Morning delivery wave, midday sortation stretch"],
        ["PM", "Include", 2, "12pm to 12am. Evening collection peak, hub processing from 9pm"],
    ], columns=["periodname", "status", "periodsequence", "notes"])
    write_csv(periods, "Periods")
    print("  NOTE: Periods schema is assumed — confirm the column names against Anura before loading.")
else:
    (OUT / "Periods.csv").unlink(missing_ok=True)     # do not leave a stale table behind
    print(f"  single period '{PERIODS[0]}' — no Periods.csv written (periods off, Change 13)")

# ── Elapsed time reference for the UDV (doc section 5.2) ───────────────────
LEG_HOURS = {
    "collection dwell": 2.0, "first mile transit": 1.0,
    "unload ULD": 0.25, "unload hand": 0.75, "unload longreach": 0.5,
    "sort dwell": 2.0, "linehaul transit (metro)": 1.5,
    "linehaul transit (interstate)": 27.0, "terminating sort": 2.0, "delivery run": 3.0,
}
elapsed = pd.DataFrame([{"leg": k, "hours": v, "notes": "PLACEHOLDER — needs ops timings"}
                        for k, v in LEG_HOURS.items()])
write_csv(elapsed, "_ElapsedTimeReference")
_local = sum(LEG_HOURS[k] for k in ["collection dwell", "first mile transit", "unload hand",
                                    "sort dwell", "linehaul transit (metro)", "terminating sort",
                                    "delivery run"])
if HUB_SORT_ROUNDS == 2:      # a second sort at a second hub, and the hop to reach it
    _local += LEG_HOURS["sort dwell"] + LEG_HOURS["linehaul transit (metro)"]
    print(f"  (+{LEG_HOURS['sort dwell'] + LEG_HOURS['linehaul transit (metro)']:.1f} h for the "
          f"second hub sort round and the hop between the two hubs)")
_inter = _local - LEG_HOURS["linehaul transit (metro)"] + LEG_HOURS["linehaul transit (interstate)"]
print(f"  locally lodged, end to end   {_local:>5.1f} h")
print(f"  interstate, end to end       {_inter:>5.1f} h")
print(f"  -> interstate cannot satisfy a same-day promise. The model derives it rather than "
      f"being told, exactly as the document argues (FAQ 5).")

# --------------------------------------------------------------------------------------
# ## 10b. What the two periods do and do not do — read before using this
#
# > **Dormant while `N_PERIODS = 1`.** Kept because it is the analysis to re-read before turning the
# > periods back on, and because its conclusion is why they were not worth keeping switched on yet.
#
# Period is a dimension of the **variables**, not of the definitions. A lane defined once in
# `TransportationPolicies` gets one flow variable per period, which is why that table needs no period
# column. Only the tables carrying **quantities** need one, and of the 17 Anura tables we hold, only
# `CustomerDemand` and `FlowConstraints` have it.
#
# Two consequences, both of which limit what the period split is currently worth.
# --------------------------------------------------------------------------------------

# ── Period mechanics: what is and is not enforced ──────────────────────────
if N_PERIODS == 1:
    print(f"  ONE period ('{PERIODS[0]}') — the AM/PM split is off, so none of the period mechanics")
    print("  below apply. Capacity is rate x the operating window across the whole day, and nothing")
    print("  sequences the day: this is the same single-day steady state the current model runs.")
    print("  Set N_PERIODS = 2 to bring the two-period analysis (and this diagnostic) back.")
else:
    _period_aware = []
    for t in ["CustomerDemand", "FlowConstraints", "SupplierCapabilities", "WorkCenters",
              "TransportationPolicies", "ProductionPolicies", "ProcurementPolicies",
              "ReplenishmentPolicies", "Facilities"]:
        cols = pd.read_csv(REF / f"{t}.csv", nrows=0).columns if (REF / f"{t}.csv").exists() else []
        _period_aware.append((t, any("period" in c.lower() for c in cols)))
    print("  period column present?")
    for t, ok in _period_aware:
        print(f"    {t:<26} {'YES' if ok else 'no'}")

    print()
    print("  1. CAPACITY IS PER PERIOD, NOT PER DAY.")
    print(f"     WorkCenters has no period column, so one throughput number applies in every period.")
    print(f"     Capacity here is rate x {AVAILABLE_HOURS_PER_DAY['SORT']}h / {len(PERIODS)} periods = "
          f"rate x {window('SORT')}h per period. An ASYMMETRIC window (4 h morning wave vs 5 h evening")
    print("     collection) cannot be expressed. Splitting a machine into _AM and _PM work centres does")
    print("     NOT fix it — nothing binds either to a period, so both become usable in both periods and")
    print("     the capacity doubles. Question for Optilogic: does Anura have a period-specific")
    print("     capacity table? If yes, this becomes asymmetric and the even split goes away.")
    print()
    print("  2. NOTHING SEQUENCES THE DAY.")
    print("     Flow conservation holds WITHIN a period. Lanes carry no transit time, so volume that")
    print("     leaves in PM arrives in PM, and there is no carryover variable between periods")
    print("     (InventoryPolicies has no period column either). So today a parcel can be collected,")
    print("     linehauled, sorted and delivered inside the SAME period.")
    print("     Demand is the only period-stamped quantity, so it is the only thing pulling flow into a")
    print("     period. Collection is not gated at all:")
    _dem = pd.read_csv(OUT / "CustomerDemand.csv")
    _by = _dem.groupby("periodname")["quantity"].sum()
    for per in PERIODS:
        print(f"       {per}: demand {_by.get(per, 0):>9,}  <- pulls the whole chain, collection included")
    print()
    print("     That is backwards from the real operation (collect PM, linehaul overnight, deliver AM next")
    print("     day) and it means the two periods do not separate the two peaks. They split one day in two.")
    print()
    print("  TO MAKE THE PERIODS BITE, two things are needed together:")
    print("    a) Gate collection and delivery by period using FlowConstraints (the only period-aware")
    print("       lever we have): Max 0 on pickup in AM, Max 0 on next-day delivery in PM.")
    print("    b) An EXPLICIT overnight handover. With no carryover variable, PM sortation output is not")
    print("       available in AM — so the solver would just sort in AM instead, leaving the PM window")
    print("       idle. The handover has to be modelled the way we already model local staging: a")
    print("       supplier that provides AM-ready Despatch with capacity equal to the previous PM's")
    print("       sorted output (steady state: what crosses midnight tonight = what crossed last night).")
    print()
    print("  NOTE ON THE DOCUMENT: FAQ 3 says hub processing crossing midnight 'nets out and the model")
    print("  does not need to track the handover'. That holds for volume BALANCE but not for CAPACITY")
    print("  TIMING. Without (b), the periods constrain nothing. (a) without (b) is infeasible.")
    print("  Neither is implemented here — it changes the model's economics and needs a decision.")

# --------------------------------------------------------------------------------------
# ## 11. Summary
# --------------------------------------------------------------------------------------

# Every Anura table this model must emit. Change 16: Facilities was missing for weeks because
# nothing checked — a build that cannot be loaded should fail here, not in Cosmic Frog.
REQUIRED_TABLES = ["BillOfMaterials", "CustomerDemand", "CustomerFulfillmentPolicies", "Customers",
                   "Facilities", "FlowConstraints", "Groups", "Processes", "ProcurementPolicies",
                   "ProductionPolicies", "Products", "ReplenishmentPolicies", "SupplierCapabilities",
                   "Suppliers", "TransportationModes", "TransportationPolicies", "WorkCenters"]
tables = sorted(p.stem for p in OUT.glob("*.csv"))
_missing = [t for t in REQUIRED_TABLES if t not in tables]
assert not _missing, f"these Anura tables were never written: {_missing}"
_empty = [t for t in REQUIRED_TABLES if sum(1 for _ in open(OUT / f"{t}.csv", encoding="utf-8-sig")) <= 1]
assert not _empty, f"these Anura tables are header-only: {_empty}"
print(f"  all {len(REQUIRED_TABLES)} required Anura tables present and non-empty")
# Change 33: the mix tables are OPTIONAL — present only when a family is bounded. Assert the
# pair moves together, so a half-written mix can never reach Cosmic Frog.
OPTIONAL_TABLES = ["UserDefinedVariables", "UserDefinedConstraints"]
_opt = [t for t in OPTIONAL_TABLES if t in tables]
assert len(_opt) in (0, len(OPTIONAL_TABLES)), \
    f"Change 33: {_opt} written without its pair — a variable with no constraint binds nothing"
assert bool(_opt) == bool(MIX_FAMILIES), \
    f"Change 33: mix tables {'missing' if MIX_FAMILIES else 'left over from an earlier run'}"
if _opt:
    print(f"  + {len(OPTIONAL_TABLES)} optional Change-33 mix tables")
sizes = {t: sum(1 for _ in open(OUT / f"{t}.csv", encoding="utf-8-sig")) - 1 for t in tables}
print(f"{len(tables)} tables written to {OUT.name}/")
for t in tables:
    print(f"    {t:<32} {sizes[t]:>8,}")
print()
print("Against the current single-stream model:")
for label, now, then in [("products", 26, len(products)),
                         ("demand rows", 2102, len(demand)),
                         ("work centres", 51, len(work_centers)),
                         ("BOM rows", 24, len(bill_of_materials)),
                         ("lanes", 3446, len(transportation_policies))]:
    print(f"    {label:<16} {now:>8,}  ->  {then:>8,}   ({then/now:.1f}x)")

# --------------------------------------------------------------------------------------
# ## 12. The old open decisions — where they went
#
# Sections 12a (interstate arriving at delivery depots), 12b (the hub cross-dock) and 12c (the
# sourcing rule) from the Change 26/27 notebooks are **adopted in this entity**. What remains
# open lives in chain 1's notebook: the pickup despatch split, `LOCAL_SHARE` and the pickup total
# are unmeasurable from a delivery-side extract, and stay assumptions there until a
# lodgement-side extract lands (same query, selected on lodgement facility).
# --------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------
# ## 13. What this entity does and does not claim
#
# **Does.** Chain 2 is the measurement: six arrival sites in the measured class mix; a Victoria
# same-day family; measured stage at all 11 depots; a cross-dock recipe with the hub touch it
# implies; sort-round shares banded per family × site × class. The balance
# `stage + interstate + VIC = D` is exact with no free parameter.
#
# **Does not.** (1) Nothing here covers pickup — chain 1 is a separate entity built under current
# assumptions. The two chains are **independent daily activities** (collection and delivery), so
# this entity's 49% Victoria same-day share does not contradict chain 1 — it is delivery-side
# evidence that can *inform* chain 1's assumed collection volume. (2) The VIC/INT split of the cross-dock's handling hub is directional (weighted to MPF).
# (3) Dock capacities here are sized to chain-2 touches only; the combiner merges chain 1's.
#
# **Dials.** `XDOCK_ENABLED = False` removes the cross-dock recipe and lanes;
# `BYPASS_CONSTRAIN = False` removes the sort-round bands; `OBS_CELL_FLOOR` trims the joint.
# --------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------
# ## 14. Reading a solved run
#
# (1) The solved single-sort share per site should sit inside its band — a hard-binding Min or Max
# means the cost model disagrees with the scans at that site, which is now a finding about *costs*.
# (2) The cross-dock lanes should run near their measured volume — a binding Max means depot
# sorting is priced too cheap. (3) `HUB_DOCK_SCALE` at SWP/MNP/BAY is the ops number to watch:
# those buildings now carry measured linehaul intake at docks sized for vans.
# --------------------------------------------------------------------------------------
