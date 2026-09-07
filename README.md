# Melbourne Optilogic input generator

Turns Australia Post scan data and a set of hand-managed assumption tables into the CSV
folder Cosmic Frog / Optilogic uploads as a model.

The build has **two owners of numbers**, and the split is the point of the whole folder:

| | folder | who writes it |
|---|---|---|
| **Measured** | `inputs/factors_observed/` | `pipeline/s1a_export_chain2_factors.py` only — never hand-edit |
| **Assumed** | `inputs/factors_assumed/` | you, by hand — this is where you steer the model |

Nothing numeric is hardcoded in the build scripts. They read those two folders and emit tables.

## Run order

**The whole build is one command:**

```bash
uv run python pipeline/run_pipeline.py              # steps 1-7, from the cached reduction
uv run python pipeline/run_pipeline.py --rebuild    # step 1 re-reads the raw scan CSV first
```

It runs the seven steps below in order, each in its own process, and **stops dead at the first
failure** — steps 2-4 write their folder one table at a time, so a step that dies halfway leaves a
folder that looks like a model and is half of two different builds. Every step is still runnable on
its own; the runner only sequences them and holds no build logic.

```
0.  inputs/melbourne/                     the raw extract — 375 MB of scans + the zone table
      |
s1 — EXPORT THE FACTORS
1.  uv run python pipeline/s1a_s1a_export_chain2_factors.py --rebuild
      |   reads  the scan CSV
      |   writes outputs/melbourne_scan_path_analysis/consignment_paths.pkl   (the reduction)
      |          inputs/factors_observed/*.csv                                (7 measured tables)
      v
s2 — GENERATE THE TABLES
2.  uv run python pipeline/s2a_build_chain2.py                  -> outputs/…_chain2_observed/
      |   reads  factors_observed + factors_assumed + temp_clustered.csv + the catchment geojson
      |          (and the reduction cache, for the drift guard)
      v
3.  uv run python pipeline/s2b_build_chain1.py                  -> outputs/…_chain1/
      |   reads  outputs/melbourne_optilogic_chain2_scan_constrained/   <-- FROZEN, 11 Aug
      v
4.  uv run python pipeline/s2c_build_final.py                   -> outputs/…_final/
      |   reads 2 + 3
      v
s3 — POST-PROCESS — in this order, EVERY time s2c runs
      |   uv run python pipeline/s3a_split_despatch2.py       splits Despatch2 by who made it
      |   uv run python pipeline/s3b_no_relay.py              a site may only despatch what it makes
      |   uv run python pipeline/s3c_narrow_sort_band.py --band 0   pins round-2 on the measured shares
      |   (round-2 handling is no longer patched — Change 48 moved it into s2a)
      v
    upload outputs/melbourne_optilogic_final/   <- this is the folder Cosmic Frog takes
      |
8.  drop the solved CSVs into outputs/run_outputs/, then read them back:
        uv run python utilities/verify_touch_and_sort.py            every cell of the two tables, with its working
        uv run python plotting/sankey_from_optilogic.py   the same run as a picture
```

Partial runs, for when you changed one thing:

```bash
uv run python pipeline/run_pipeline.py --from s2c     # a combiner change: rebuild + re-patch
uv run python pipeline/run_pipeline.py --from s2a     # a dial change: everything but the measurement
uv run python pipeline/run_pipeline.py --band 0.05    # s3c with the original band
uv run python pipeline/run_pipeline.py --dry-run      # print the commands and stop
```

`--only s3a s3b s3c` re-applies the patches, but **only onto a folder s2c has just rebuilt.** s3a
refuses to split an already-split model rather than corrupting it, so on a patched folder that plan
stops at s3a and tells you to `--restore` first or to use `--from s2c`. Prefer `--from s2c`: it is
one command and it cannot be applied to the wrong state.

### The whole build is one folder (2026-09-07)

`pipeline/` holds **every script the build runs and nothing else**, named for when it fires. Drag
that one folder into an Optilogic DataStar workspace and the build goes with it.

```
pipeline/
  _paths.py                 where inputs/ and outputs/ are — the only file that touches __file__
  run_pipeline.py           runs s1 -> s2 -> s3
  s1a_export_chain2_factors.py   s1  the measurement
  s2a_build_chain2.py       s2  chain 2, the delivery entity
  s2b_build_chain1.py       s2  chain 1, the collection entity
  s2c_build_final.py        s2  the combiner
  s3a_split_despatch2.py    s3  patch 1
  s3b_no_relay.py           s3  patch 2
  s3c_narrow_sort_band.py   s3  patch 3
```

**Where the data is.** Nothing in `pipeline/` counts parent directories. `_paths.py` resolves the
data root once — `$MELB_DATA_ROOT` if set, otherwise the nearest directory at or above the folder
that contains an `inputs/` — and everything else asks it. So `pipeline/` beside `inputs/` and
`outputs/` just works, and `pipeline/` three levels down in a workspace works too. `uv run python
pipeline/_paths.py` prints what it resolved, how, and which folders it can actually see.

Both shapes were run end to end and both reproduce the model byte for byte: the folder copied to
an unrelated directory with `MELB_DATA_ROOT` pointing back at the data, and the folder dropped
beside `inputs/`/`outputs/` in a workspace with no env var at all.

**Where the code came from.** Steps s2a-s2c used to be Jupyter notebooks, and the three `.ipynb`
files are still in [notebooks/](notebooks/) as the reference copy. **The build runs the `.py`.**
Each was converted mechanically — code cells concatenated in execution order, markdown cells kept
as comment banners — because all three had been executed top-to-bottom in a fresh kernel, so a
module that runs those cells in that order builds exactly the namespace the kernel had. That was
verified rather than assumed: the full pipeline reproduces every table in all three output folders
**byte for byte**, including the three post-build patches, from the cache and from the raw 393 MB
scan CSV. Only `_provenance.csv` changes, and only in its `generated_utc` stamp.

| was | is |
|---|---|
| `notebooks/melbourne-optilogic-chain2-observed.ipynb` | [pipeline/s2a_build_chain2.py](pipeline/s2a_build_chain2.py) |
| `notebooks/melbourne-optilogic-chain1-pickup.ipynb` | [pipeline/s2b_build_chain1.py](pipeline/s2b_build_chain1.py) |
| `notebooks/melbourne-optilogic-final.ipynb` | [pipeline/s2c_build_final.py](pipeline/s2c_build_final.py) |
| `model_input_preparation/export_chain2_factors.py` | [pipeline/s1a_export_chain2_factors.py](pipeline/s1a_export_chain2_factors.py) — a redirect, not a copy |
| `post_process/split_despatch2_by_route.py` | [pipeline/s3a_split_despatch2.py](pipeline/s3a_split_despatch2.py) |
| `post_process/add_no_relay_constraints.py` | [pipeline/s3b_no_relay.py](pipeline/s3b_no_relay.py) |
| `post_process/narrow_sort_band.py` | [pipeline/s3c_narrow_sort_band.py](pipeline/s3c_narrow_sort_band.py) |

**The measurement moved, and nine scripts import it.** Six diagrams in `plotting/`, three checks in
`utilities/`, plus `model_common.py` and `classify_entry.py`, all say
`from export_chain2_factors import …`. That kept working through a redirect shim while the real
file was called `s1a_export_chain2_factors.py`. **On 2026-09-07 the pipeline file took the name
`export_chain2_factors.py` itself, which collided with the shim, so the shim was deleted**: each of
those callers now puts `pipeline/` on `sys.path` and imports the real module directly. There is
still exactly one module object — verified by importing all thirteen callers and asserting
`sys.modules["export_chain2_factors"] is` the pipeline file.

**Nothing keeps the `.ipynb` and the `.py` in step.** The notebooks are kept so the conversion can
be audited against its source, and for nothing else — editing one changes no build. If you are
done with them, move them to [notebooks/archive_notebook/](notebooks/archive_notebook/) where the
other retired notebooks live; if you are not, make the change in the `.py` and port it back by
hand. There is deliberately no converter in the repo: re-running one would silently undo the three
fixes below.

Three things changed in the conversion, and nothing else did:

- **`Path.cwd()` is gone.** The notebooks found the repo root by looking at the kernel's working
  directory. The scripts ask `_paths.py`, so they run from anywhere and the folder can be moved.
- **geopandas is gone.** The chain-2 notebook loaded a 22 MB catchment geojson through
  `gpd.read_file()` and used the result for one thing: printing the number of catchments. The
  script counts the same 411 features with the standard library. That removes the build's heaviest
  dependency (geopandas + pyogrio + GDAL) and changes no emitted number.
- **The combiner's balance line was wrong and is fixed.** It read the Victorian volume off
  `SUP_VIC_`, a supplier family Change 46 split into `SUP_MET_` and `SUP_REG_`, so it printed
  `VIC 0 + interstate 152,613` against a real `metro 75,420 + regional 9,328 + interstate 67,865`.
  Print only — no emitted table read it — but the build log is what gets read back.

**s3 is not optional and a rebuild drops it.** The three patches edit the built model in place,
so s2c overwrites them every time. `run_pipeline.py` runs them by default and warns when a partial
plan would leave them off. Run them in the order above: the split changes what
each site can produce, and the no-relay rule reads exactly that, so a no-relay run before the
split writes the wrong rows. See [The post-build patches](#the-post-build-patches) below.

Drop `--rebuild` on s1a to reuse the cached reduction — about a minute saved, and it
auto-rebuilds anyway if the cache is missing a column it needs. Step 1 only needs the raw scans
when the extract changes; a filter-dial change re-runs it against the cache.

**s2b does not start from raw, and that is the one real gap.** Chain 1 is not a generator: it
filters a build dated 11 August, produced by an archived notebook, so no chain-1 parameter can be
changed from `factors_assumed/`. s1a, s2a and s2c are reproducible from the raw extract; s2b is
not. See the chain-1 caveat below.

The diagrams are optional and read the same reduction — `uv run python plotting/sankey_facility_path.py`
(the measurement) and `uv run python plotting/sankey_from_optilogic.py` (a solved run, from
`outputs/run_outputs/`).


---

## s1 — `pipeline/s1a_export_chain2_factors.py`, the measurement

Reduces **2.96 M scan events to one row per parcel**, then turns those rows into the measured
factors the model is built from. It is the *single source of the observed side*: it derives the
factors **and** applies every filter, so the build scripts consume whatever they are given and
cannot quietly re-filter.

```bash
uv run python pipeline/s1a_s1a_export_chain2_factors.py  # uses the cached reduction (seconds)
uv run python pipeline/s1a_s1a_export_chain2_factors.py --rebuild  # re-reads the raw scan CSV first
```

**Inputs**

| path | what |
|---|---|
| [inputs/melbourne/all_scan_for_melbourne_pdc_20052026.csv](inputs/melbourne/) | the raw scan extract |
| `outputs/melbourne_scan_path_analysis/consignment_paths.pkl` | the cached reduction (rebuilt by `--rebuild`) |
| [inputs/factors_assumed/dials.csv](inputs/factors_assumed/dials.csv) | the filter dials it reads (below) |
| [inputs/melbourne/temp_clustered.csv](inputs/melbourne/) | the model's demand table, for the reconciliation check |

**Outputs** — all into [inputs/factors_observed/](inputs/factors_observed/):

| file | what it measures |
|---|---|
| `obs_joint.csv` | pud × class × source tag share of deliveries — the product families |
| `obs_demand.csv` | pud × class measured delivery volume — the model's demand magnitude |
| `obs_legs.csv` | the lane matrix: 1st building → 2nd building (`ONCE` = one building and no more) |
| `obs_delivery.csv` | last building → delivering depot (verification only) |
| `obs_single_sort.csv` | the `ONCE` slice of `obs_legs`, under its old name |
| `obs_round2_sites.csv` | the sites *allowed* to be a second building |
| `_provenance.csv` | source file, cohort, thresholds applied, how much volume they moved, and the measured 2+ sort volume (`sort_2plus_cohort` 51,496 / `sort_2plus_same_day` 43,883) that Change 48 charges round-2 handling for |

**Seven files, and every one of them is read.** `obs_recv_entry.csv` and `obs_round2.csv` merged
into `obs_legs.csv` at Change 44, because a cross-dock leg and a round-2 leg are one truck between
two buildings. `obs_xdock.csv`, `obs_second_sort.csv` and `obs_stage_origin.csv` were deleted on
2026-08-28: the first two were reference cuts nothing consumed, and the third was measured ahead
of a modelling decision that was never taken. Deleting all three left every emitted table
byte-identical.

Those `obs_*` matrices **are** the link stages of the scan Sankey on the same cohort, so a number
read off the diagram is the number the model carries.

**The dials it reads** (from `factors_assumed/dials.csv`):

- `FOLD_MIN_ARTICLES` (200) — the *only* size question. A product flavour, a joint cell or a
  stage lane carrying fewer articles than this folds into what survives.
- `ROUND2_MIN_SHARE` (0.02) — a site must carry this share of observed second sorts to be
  offered as a round-2 destination at all. A different question, so it keeps its own dial.
- `OBS_COHORT` — `all_dates` (every parcel in the extract) or `peak_day`.

`ROUND2_COST_BASIS` (`off | same_day | cohort`) is read by the NOTEBOOK, not the exporter, and
decides which of the two measured 2+ sort volumes the round-2 handling is charged and timed for.
The model routes fewer parcels through a second sort than the scans record — most of the gap is
staged freight, sorted the day it ARRIVED, which one day of model cannot carry — so the round-2
unload, sort and load processes get a scaled `*_R2` clone, and only the round-2 production
policies point at it. The ratio is derived at build time from `_provenance.csv` and the lane bands,
so it cannot go stale the way a typed constant would. **It scales cost and machine HOURS, not
utilisation:** work-centre capacity in this model is in EA, so utilisation is quantity/capacity and
a rate change does not move it.

Chain 2 re-runs this file's own derive-and-filter code against the scan cache and asserts the
CSVs still match — **a stale export fails the build**.

---

## s2 — the three table generators

Chain 1 (collection) and chain 2 (delivery) are **separate entities, built and run separately**,
then combined. They share no products, suppliers, customers or lanes.

### `pipeline/s2a_build_chain2.py` — the delivery entity

Sources → sort → delivery, **entirely observed**. Its balance is exact and measured:
`stage + interstate + Victoria same-day = D`, per depot × class.

Reads:

- `inputs/factors_observed/` — every measured factor, plus `_provenance.csv` for the run header
- `inputs/factors_assumed/` — `dials.csv`, `machine_rates.csv`, `operating_hours.csv`,
  `pud_capacity.csv`, `site_sorters.csv`, `sort_only_sites.csv`,
  `unload_mix.csv`, `transport_modes.csv`, `period_split.csv`
- `inputs/melbourne/` — `temp_clustered.csv`, `cluster_summary.csv`, `all-data.xlsx` (nodes),
  the first-mile catchment geojson
- `inputs/optilogic/TransportationPolicies.csv` — **schema only**, an Anura reference export that
  supplies a column list that cannot be regenerated. If it goes missing the build warns and falls
  back to the previous output's columns — and on a clean workspace there is no previous output, so
  the build stops. Ship this folder with the repo.

Writes `outputs/melbourne_optilogic_chain2_observed/` (21 tables — 19 required plus the two
optional Change-33 mix tables, and `_ElapsedTimeReference`).

### `pipeline/s2b_build_chain1.py` — the collection entity

Pickup → round-0 sort → terminate, under **today's assumptions**. Every row is *selected by
family* out of an earlier build rather than re-derived, so "current assumptions" means exactly
the assumptions of that build. Balance: `P = OUT + L`.

- Reads `outputs/melbourne_optilogic_chain2_scan_constrained/` — the last model in which both
  chains were built together (the Change 27 build; its notebook now lives in
  [notebooks/archive_notebook/](notebooks/archive_notebook/)).
- Reads no `inputs/` folder at all.
- Writes `outputs/melbourne_optilogic_chain1/` (17 tables).

**Caveat:** this entity is only as current as that source build. If the source build is
re-run with different dials, re-run this one after it.

### `pipeline/s2c_build_final.py` — the combiner

Reads both entity folders **back off disk** and writes `outputs/melbourne_optilogic_final/` —
the folder you upload. Four merge rules:

- products / suppliers / customers / lanes — concatenated, with a disjointness assert
- `Facilities` — containment assert (chain 2 may know buildings chain 1 does not), chain 2's copy taken
- `TransportationModes` — identical by construction, asserted, taken once
- `WorkCenters` — the one real merge: identical rows collapse (a machine is not doubled by being
  used twice), rows that differ — the workload-scaled docks — have their capacities **summed**

---

## s3 — the post-build patches

Three scripts in [pipeline/](pipeline/) that edit `outputs/melbourne_optilogic_final/` after
the notebooks have written it.
They exist because each expresses a rule the chain-2 build has no way to write, and each is
idempotent and reversible. **A rebuild drops all three — re-apply in the order below.**

| # | script | what it does |
|---|---|---|
| s3a | [s3a_split_despatch2.py](pipeline/s3a_split_despatch2.py) | splits 16 `Despatch2` flavours into a direct-made half and a `…Despatch2R` round-2-made half, and points the sourcing at the half that can actually make each one |
| s3b | [s3b_no_relay.py](pipeline/s3b_no_relay.py) | 889 `Max 0` rows: a building may only despatch a `Despatch2` it has a ProductionPolicy for |
| s3c | [s3c_narrow_sort_band.py](pipeline/s3c_narrow_sort_band.py) | rewrites the 44 `Despatch1_<SITE>` lane bands from ±5 points to `--band W`; `--band 0` pins each on its measured share |

**Why the order.** The split changes what each site can produce; the no-relay rule is written from
exactly that, so running it first writes the wrong rows. The band is independent but is listed
last because it is the dial you will actually change between runs.

**What they are for.** A parcel is in a *second building* whenever a truck carries it from one of
our buildings to another, and it is *sorted twice* only when that second building opens it. Those
were different numbers — the model relayed finished freight through a building that never touched
it, 8,449 EA on the 2 Sep run — because one product could be made two ways and a site that could
make it could also receive it and pass it on. Patch 1 gives each route its own product, so a site
holding freight it did not make has no lane to pass it on: the relay is not forbidden, it is
unrepresentable. Patch 2 is the flat rule that covers the 13 flavours patch 1 leaves alone. Patch
3 sets how much freight takes a second sort at all.

**One rule makes patch 1 work:** the **origin** of a sourcing row must be able to make what it
ships. That is enough — every unit of second-building relay in the runs so far arrived from the
flavour's own site, so it is direct-half freight, and a site that is not that site can neither
produce the direct product nor ship it.

A second rule — *the destination must not be able to make it* — was tried on 2 Sep and is **wrong;
do not reintroduce it.** Production is capped by the pinned `Despatch1` band, so a round-2 site can
make *some* of a flavour and still need the rest shipped in already sorted. Dropping those lanes
cost 7,486 EA of routing that the previous solve used and returned **infeasible with no constraint
violated** — the break was structural, so nothing in the validation report pointed at it. Patch 1
now ends with a guard that replays the last solve's Despatch2 movements against the trimmed
sourcing tables and says loudly if any of them has lost its lane.

Undo: `pipeline/s3a_split_despatch2.py --restore` (pre-split tables are kept in `outputs/.presplit/`),
then re-run s3b. `pipeline/s3c_narrow_sort_band.py --band 0.05` restores the original band exactly.

## What the build needs installed

The build is **pandas, numpy and openpyxl**, and nothing else — verified by running every step
with `geopandas`, `shapely`, `pyogrio`, `matplotlib` and `seaborn` blocked at import.

| step | needs |
|---|---|
| s1a **without** `--rebuild` | pandas, numpy, openpyxl |
| s1a `--rebuild` | **+ geopandas, shapely, pyogrio** |
| s2a / s2b / s2c | pandas, numpy, openpyxl |
| s3a / s3b / s3c | the standard library only (`csv`, `os`, `re`) |

Only `--rebuild` needs the geo stack, and it needs it for real work, not for a print:
`lodgement_geography()` puts every lodgement facility on the map and asks the metro boundary which
side it is on — that point-in-polygon **is** the METRO / REGION band split (Change 36). It is a
lazy import inside that one function, so the cached path never touches it.

Everything else the geo stack was doing has been removed. The chain-2 notebook used to load a
22 MB catchment geojson through `gpd.read_file()` to print one number; the script counts the same
411 features with `json`. `jupyter`, `notebook` and `seaborn` are in `pyproject.toml` for the
reference notebooks and the `plotting/` scripts — the build does not import them.

**Running it somewhere else** (Optilogic DataStar, a container, CI): every script resolves
`inputs/` and `outputs/` from its own location, not the working directory, so the only requirement
is that the repo tree stays intact. Ship `inputs/` with it — including
[inputs/optilogic/](inputs/optilogic/), which is column schemas rather than data, and which
`build_chain2_observed.py` asserts on: without it the build can only fall back to the previous
output's column list, and on a clean workspace there is no previous output.


## The input folders

| folder | contents |
|---|---|
| [inputs/factors_assumed/](inputs/factors_assumed/) | hand-managed CSVs: `dials.csv` plus the capacity, rate, hours, split and mode tables |
| [inputs/factors_observed/](inputs/factors_observed/) | generated by step 1 — the measurement. Never hand-edit |
| [inputs/melbourne/](inputs/melbourne/) | the raw source data: the scan extract, `temp_clustered.csv`, cluster/facility summaries, first-mile catchment geojsons |
| [inputs/new_scan_events/](inputs/new_scan_events/) | a newer extract split by event type, with coordinates on every event — analysis only (`model_input_preparation/analyse_new_scans.py`), not yet wired into the build |
| [inputs/optilogic/](inputs/optilogic/) | an Anura reference export — **column schemas**, not data |
| [inputs/optilogic-model-example/](inputs/optilogic-model-example/) | a worked Optilogic model, kept as a reference for table shapes |

## Repo layout

Every script lives in a folder named for **when in the run order it fires** (2026-09-04). The
repo root holds no loose `.py`.

```
pipeline/                   THE BUILD — everything the build runs, and nothing else. This is the
                            folder you drag into Optilogic DataStar.
      _paths.py                  where inputs/ and outputs/ are; the only file touching __file__
      run_pipeline.py            s1 -> s2 -> s3, stops dead at the first failure
      s1a_export_chain2_factors.py    s1  the only writer of inputs/factors_observed/
      s2a_build_chain2.py        s2  the delivery entity, from the measurement
      s2b_build_chain1.py        s2  the collection entity, from the frozen 11-Aug build
      s2c_build_final.py         s2  the combiner
      s3a_split_despatch2.py     s3  a Despatch2 per route
      s3b_no_relay.py            s3  a site despatches only what it makes
      s3c_narrow_sort_band.py    s3  round-2 pinned on the measured share

model_input_preparation/    BESIDE the build — the vocabulary, and analysis of the second extract
      export_chain2_factors.py   a REDIRECT to pipeline/s1a_export_chain2_factors.py, for the nine
                                 non-build scripts that import the reduction by that name
      analyse_new_scans.py       the same reduction for the second extract
      classify_entry.py          which of our buildings the parcel entered at
      model_common.py            the network's identity — buildings, roles, codes, scan names
      factors.py                 one door to inputs/factors_assumed/, with the cross-file asserts

notebooks/                  REFERENCE ONLY — the .ipynb the build scripts were converted from
      melbourne-optilogic-*.ipynb   editing one changes no build

utilities/                  BESIDE the build — ad-hoc only: verify_touch_and_sort.py,
                            path_census.py, circular_scan_events.py, site_touch_profile.py,
                            analyse_sort_residual.py, event_hitrate.py

plotting/                   every script whose output is a PICTURE (2026-08-28)
docs/                       the long notes, lifted out of the code so the code reads as code
inputs/ outputs/            data in, tables out
```

**Run every script from the repo root**, e.g. `uv run python pipeline/s3c_narrow_sort_band.py`.
Nothing resolves anything from the working directory: the build asks `pipeline/_paths.py`, and the
scripts outside it anchor at their own location. The ones that import a sibling folder put it on
`sys.path` themselves.

## Where a number lives

Two owners, and the split is the point:

| | folder | who writes it | who reads it |
|---|---|---|---|
| **Measured** | `inputs/factors_observed/` | `pipeline/s1a_export_chain2_factors.py` only — never hand-edit | chain 2, via a live drift guard |
| **Assumed** | `inputs/factors_assumed/` | you, by hand | `factors.py`, which validates before anyone sees it |

[inputs/factors_assumed/_index.csv](inputs/factors_assumed/_index.csv) says, for each file, what it
owns and who reads it. `factors.py` asserts that index stays in step with what is on disk, that
every split sums to 1, that no work-centre family constrains all its methods, and that the one
value living in two files (`INTERSTATE_ULD_SHARE`) agrees with itself.

The **identity** — which buildings exist, what they are called, and what each may do — was declared
twice, in two casings: scan names in CAPS in the exporter, display names in Title Case in the
chain-2 notebook. `model_common.py` joins them and asserts they agree.
[sites.csv](inputs/factors_assumed/sites.csv) holds the modelling half (node, code, display, role,
delivers / sorts / first_mile); the exporter keeps the measurement half (which scan name is which
building) beside its evidence.

**`plotting/` scripts are run from the repo root**, e.g. `uv run python plotting/sankey_facility_path.py`.
Each begins with a shim putting `model_input_preparation/` and `utilities/` on `sys.path`, because
the reduction they draw lives in `pipeline/s1a_export_chain2_factors.py` (reachable under its
library name through the redirect); a script in another folder that imports a diagram module does
the reverse.

**`docs/` holds the prose that used to sit inside the code.** The exporter went from
2,634 lines (45% comment and docstring) to 1,850 (21%), and the chain-2 notebook lost 329 lines
the same way. Nothing was rewritten or dropped — every lifted note is in the doc verbatim, and the
code keeps the note's title plus a `→ docs/…#anchor` pointer, so a block still says what it is
without the doc open.

| doc | what it holds |
|---|---|
| [docs/export_chain2_factors.md](docs/export_chain2_factors.md) | the 40 long notes from the exporter — the evidence bars, the depot ladder, the fold rules, why the path basis replaced the role basis |
| [docs/chain2-observed.md](docs/chain2-observed.md) | the 23 long notes from the chain-2 notebook — the dials, the sourcing rules, the grammar fold |

## The other scripts

| script | what |
|---|---|
| [plotting/sankey_from_scans.py](plotting/sankey_from_scans.py) | the scan-path Sankey — the diagram only; it imports the reduction from `pipeline/s1a_export_chain2_factors.py` |
| [plotting/sankey_facility_path.py](plotting/sankey_facility_path.py) | the same day as an ITINERARY, **Victoria only** — 1st building in the state, 2nd, 3rd, 4th, 5th, then the delivering depot; colour is how many buildings the parcel touched here |
| [classify_entry.py](model_input_preparation/classify_entry.py) | resolves the FIRST touch onto a modelled facility for chain 2 — a ladder of evidence (`--evidence dock|physical|sort`, `--compare`), writing `entry_classification.csv` + `entry_coverage.json`; no assumed rung |
| [plotting/sankey_facility_path_new.py](plotting/sankey_facility_path_new.py) | the same itinerary page over `inputs/new_scan_events/`; `--depth N`. The TIME-order chain it used to build itself now lives in `analyse_new_scans.chain_by_time`, shared with `sankey_from_new_scans.py` |
| [plotting/sankey_from_optilogic.py](plotting/sankey_from_optilogic.py) | the same picture drawn from a solved model run |
| [analyse_new_scans.py](model_input_preparation/analyse_new_scans.py) | the `new_scan_events/` reduction. `chain_by_time()` is the shared TIME-ordered itinerary; `full_chain` was ordered by `Event_seq` until 2026-08-31, which is a per-event-type index and mis-ordered 82.4% of articles |
| [plotting/sankey_from_new_scans.py](plotting/sankey_from_new_scans.py) | that extract as a Sankey. `--basis path` (default) draws BUILDINGS TOUCHED with `--depth N` and the exporter's `first_last` cap; `--basis role` is the original first/second/last-sort columns. Each combination writes its own file (`…-path-d3-entry.html`) |
| [event_hitrate.py](utilities/event_hitrate.py) | scan event coverage |
| [verify_touch_and_sort.py](utilities/verify_touch_and_sort.py) | recomputes every cell of the facility-touch and sortation tables from source, printing the filter at each step — nothing copied from the Sankey page, so agreeing with it is evidence rather than restatement |
| [path_census.py](utilities/path_census.py) → [site_touch_profile.py](utilities/site_touch_profile.py) → [plotting/plot_touch_violin.py](plotting/plot_touch_violin.py) | a standalone chain: journeys per site, then how many buildings each site's freight touched, then the violin plot. Writes `outputs/path_census_sankey/` |
| [circular_scan_events.py](utilities/circular_scan_events.py) | every scan of every parcel whose journey returns to a building it had already reached |
