# Melbourne model — DataStar macros: logic, inputs, outputs

Project `Temp_FY26_Melbourne`. Five macros run in order: **2 → 3 → 4 → 5 → 6**. Each one reads the previous macro's tables and publishes its own under a `Wip_C0<n>_` prefix.

**Rules for every macro**
- Each script task has a **`Wip_C0<n>_Drop<step>`** SQL task directly before it, created by `setup_macro_drop_task.py <macro_n>`. To re-run a step, start at its Drop task, or the old rows stay.
- The build scripts (`s2a`, `s2b`, `s2c`, `s3a–c`, `s4a`) are **verbatim copies** of `pipeline/`. Edit `pipeline/`, then re-copy. `_paths.py` is the only fork.
- Every macro needs these files in the same folder: `_paths.py`, `_log.py`, `_report.py`, `table_bridge.py`. Macros 3–5 also need `_facilities.py` (reads `sites.csv` + `site_machines.csv`).
- Tables move as **text**, so each published table is byte-identical to the local build's CSV.

---

## Macro 2 — Scan measurement

**Purpose**
- Turn the raw delivery scan extract into the measured factors that chain 2 is built from.

**Run elements**
1. `Wip_C02_DropScanClean` → `Wip_C02_ScanClean`
2. `Wip_C02_DropScanAnalysis` → `Wip_C02_ScanAnalysis`
3. `Wip_C02_DropObsFactors` → `Wip_C02_ObservedFactors`
4. `Wip_C02_DropDeliveryCatchment` → `Wip_C02_DeliveryCatchment` (independent of 1–3; can run on its own)
- Scripts: `wip_c02_scanclean.py`, `wip_c02_scan_analysis.py`, `wip_c02_obs_factors.py`, `wip_c02_delivery_catchment.py`. Steps 2 and 3 also need `scan_reduction.py`.
- Create the `Wip_C02_DeliveryCatchment` script task before re-running `setup_macro_drop_task.py macro_2`, or the setup stops (it resolves every task before changing anything).
- Leave `Wip_C02_ScanClean_AI` alone. It belongs to the AI agent, not to this build.

**Logic**
- **Clean**
  - Drop consignments that have no delivery coordinate.
  - Drop Western DC, Mulgrave PDC and StarTrack Mulgrave. This must happen **before** the remaps, or all genuine Mulgrave volume is deleted.
  - Keep in-scope products: eParcel Express / Standard / Returns, Parcel Post Plus, Metro Next Day.
  - Remap two names: Holloway Dr → Bayswater PDC, and V03974 → Mulgrave PDC.
  - Keep only consignments that terminate at the 11 modelled PDCs. Tag each event with its state by point-in-polygon.
- **Analyse**
  - Reduce each consignment to the buildings it passed through.
  - Fold alias names into one building, and apply the stage and sort rules.
  - Assign an origin band: Interstate, Metro, Region or Kept at depot.
- **Observed factors**
  - Derive the measured shares chain 2 reads: demand, legs, delivery, single and second sort, round-2 sites, and the full path.
  - The dials are the same as the local build, so the output diffs row for row against `inputs/factors_observed/`.
- **Delivery catchment** (port of `inputs/deilvery_events/generate_melbourne_scenario.ipynb`)
  - Same rules 1a–1c and the same two remaps as Clean, on the last-mile extract (one row per consignment).
  - Tag each delivery point with its POSTCODE by point-in-polygon; overwrite the depot coordinate with the surveyed one.
  - Byte-identical to the notebook's output on the 20 May extract (163,250 rows, 167,445 articles).

**Inputs**
- `Raw Inputs/melbourne/melbourne-delivery-volume-all-scan-events.csv`
- `Raw Inputs/melbourne/aus_state_boundaries.csv` (state polygons, WKT)
- `Raw Inputs/deilvery_events/melbourne_pdc_last_mile_catchment.csv`
- `Raw Inputs/deilvery_events/vic_postcode_boundaries.csv` (Victorian postcode polygons, WKT, unsimplified; made by `utilities/convert_postcode_boundaries.py`)

**Outputs**
- `Wip_C02_ScanClean`
- `Wip_C02_ConsignmentPaths` (the reduction), plus 15 diagnostic tables (`ReductionLedger`, `FacilityByBuilding`, `SortationCounts`, `KeptByPdc`, …)
- `Wip_C02_ObsJoint`, `ObsDemand`, `ObsLegs`, `ObsDelivery`, `ObsSingleSort`, `ObsSecondSort`, `ObsRound2Sites`, `ObsPath`, `Provenance`
- `Wip_C02_DeliveryCatchment`, also written as `Raw Inputs/deilvery_events/melbourne_catchment.csv`

---

## Macro 3 — Chain 2, the delivery entity

**Purpose**
- Build the delivery model from observed data only: sources → sort → delivery.
- Balance per depot × class: **Stage + Interstate + Victoria same-day = Delivered demand**.

**Run elements**
1. `Wip_C03_DropChain2` → `wip_c03_build_chain2.py`
- Macro 2 must have run first.
- Also needs `s2a_build_chain2.py`, `scan_reduction.py` and `s1a_export_chain2_factors.py` in the folder.

**Logic**
- Write Macro 2's `Obs*` tables back out as `factors_observed/*.csv`, unchanged, so `s2a` runs as-is.
- **Drift guard:** pass `ConsignmentPaths` to `s2a`. It re-derives the factors and stops the build if a dial such as `FOLD_MIN_ARTICLES` changed after Macro 2 ran.
- **Supplier families feeding delivery**
  - `SUP_INT_*`: interstate arrivals at 6 measured entry sites
  - `SUP_MET_*`: metro-lodged volume
  - `SUP_REG_*`: regional Victoria volume
  - `SUP_STAGE_*`: the overnight stage, kept at all 11 delivering depots
- **Sort rounds:** `HUB_SORT_ROUNDS` = 2. The share of each lane that is sorted once or twice is banded to the measurement by `SORT_BAND`.
- **Capacity:** `machine_rates` × `operating_hours` for each work centre. Docks are sized to their workload.

**Inputs**
- From Macro 2: `Wip_C02_Obs*`, `Wip_C02_Provenance`, `Wip_C02_ConsignmentPaths`
- `factors_assumed/`: `dials.csv`, `sites.csv`, `site_machines`, `machine_rates`, `operating_hours`, `period_split`, `unload_mix`, `origin_clusters`, `transport_modes`
- `melbourne/`: `temp_clustered.csv`, `cluster_summary.csv`, `first_mile_catchment_polygons.csv`

**Outputs** (20 tables, `Wip_C03_*`)
- The Anura model tables, including `OriginMix`, `UserDefinedVariables` and `UserDefinedConstraints`.

---

## Macro 4 — Chain 1, the collection entity

**Purpose**
- Build the first-mile model: pickup → round-0 sort → round-1 sort → terminate.
- Balance: **Pickup = Kept at depot + Vic Metro to Metro + PDO terminate + Interstate + Regional**.

**Run elements**
1. `Wip_C04_DropChain1` → `wip_c04_build_chain1.py`
- Macro 3 must have run first, because chain 1 reads chain 2's tables.
- Also needs `s2b_build_chain1.py` in the folder.

**Logic**
- **Pickup per site** = `PEAK_2025_<site>` × `PEAK_FACTOR` (0.70). The sites are 5 red-van depots and 2 transport facilities.
- **Collection cells:** each site's volume is split **equally** across its first-mile route clusters (`PICKUP_CLUSTERS` = `pick_up_cluster`). This is the only basis: the postcode polygons and weights were removed 2026-09-29.
- **Leg 1 vehicle** comes from `first_mile_pickup.csv`.
  - Transports use Truck and run as a **service**: they collect and drive straight to their despatch destinations, with no sort at the transport site.
  - Red-van depots use Red_Van.
- **Round-1 destination** comes from `first_mile_despatch.csv`.
  - Melbourne Transport: MPF 50 / Melbourne North 30 / Bayswater 20.
  - Dandenong Transport: MPF 80 / Bayswater 20.
  - Every other site uses the class dials.
- **Terminate (sinks):** read from chain 2's `SupplierCapabilities`, so chain 1 never hands volume to a building where chain 2 has no supplier.
  - PDO terminate is carved out of the interstate sink at the same hub (`PDO_TERMINATE_FACTOR` = 1.10).
- **Lanes** use the haversine distance between cell and facility.
- **Capacity:** `machine_rates` × `operating_hours`, with any rate or cost `site_machines.csv` localises at one site. Sort load is published so Macro 5 sizes shared sorters once.

**Inputs**
- From Macro 3: all 20 `Wip_C03_*` tables. `Facilities`, `SupplierCapabilities` and `TransportationModes` are the ones read.
- `factors_assumed/`: `dials_chain1.csv`, `dials.csv`, `sites.csv`, `machine_rates`, `site_machines`, `operating_hours`, `transport_modes`, `first_mile_pickup.csv`, `first_mile_despatch.csv`
- `pick_up_cluster/`: `cluster_summary.csv`, `temp_clustered.csv`

**Outputs** (17 tables, `Wip_C04_*`)
- The same Anura tables as chain 2, minus `OriginMix`, `UserDefinedVariables` and `UserDefinedConstraints`.

**Pending (measured from CCP bookings, not yet in the build)**
- Pickup stops: 42,022 a week (`utilities/pickup_points.py`). The route clusters were re-run 2026-09-29 (`pickup_new_version_2`: 663 rounds, 9,378 stops).
- Transport despatch from next-drop stop counts disagrees with `first_mile_despatch.csv`.
  - Dandenong Transport sends ~0% to MPF against the assumed 80%.
  - This needs ops confirmation before the CSV changes.

---

## Macro 5 — Final combined model

**Purpose**
- Merge chain 1 and chain 2 into the one model Cosmic Frog takes, then apply the three routing rules.

**Run elements**
1. `Wip_C05_DropFinal` → `wip_c05_build_final.py`, which runs, in this fixed order:
   1. `s2c_build_final.py` (combiner)
   2. `s3a_split_despatch2.py`
   3. `s3b_no_relay.py`
   4. `s3c_narrow_sort_band.py --band 0`
- Macros 3 and 4 must have run first.
- The s3 patches are **part of the build**. `s2c` overwrites them on every run, so never stop after `s2c`.

**Logic**
- **Combine**
  - Products, suppliers, customers and lanes concatenate, with a check that the two chains share none.
  - `Facilities` and `TransportationModes` must be identical in both chains, and are taken once.
  - `WorkCenters`: identical machines collapse to one row; docks sized by workload have their capacities **summed**.
- **s3a:** splits each `Despatch2` product into one-sort (`Despatch2`) and two-sort (`Despatch2R`). A relay through a second building becomes impossible to represent.
- **s3b:** a site may only send out a `Despatch2` product it made (Max-0 flow constraints). Two Sunshine West pairs are exempt (164 EA).
- **s3c:** pins each round-2 lane at its measured share (`--band 0`).
  - Run standalone, the script defaults to ±1%, which moves 88 rows. The wrapper passes `--band 0`, so don't rely on the default.
- The order matters: `s3b` is written from what `s3a` lets each site produce.

**Inputs**
- `Wip_C03_*` (20 tables) and `Wip_C04_*` (17 tables)
- `factors_assumed/dials.csv`

**Outputs** (20 tables, `Wip_C05_*`)
- The complete model for Cosmic Frog.

---

## Macro 6 — Network Plan scenarios

**Purpose**
- Cut the manager's scenarios (S0–S3, `docs/Melbourne_Network_Plan_Scenarios.pptx`) out of the finished model. Each scenario is a separate, smaller model covering the 5 in-scope PDCs (Sunshine West, Tullamarine, Darebin, Abbotsford, Oakleigh South) plus a Port Melbourne candidate.

**Run elements**
1. `Wip_C06_DropScenarios` → `wip_c06_build_scenarios.py`, which runs `s4a_build_scenarios.py`.
- Macro 5 must have run first. s4a only **reads** `Wip_C05_*` and never changes it.
- Needs beside it: `s4a_build_scenarios.py`, `_paths.py`, `_log.py`, `table_bridge.py`.

**Logic**
- One row of `scenarios.csv` per scenario:

  | Scenario | Zones | Abbotsford | Port Melbourne | Capacity | Growth |
  |---|---|---|---|---|---|
  | S0 | pinned to current PDC | open | not included | none | no |
  | S1 | solver chooses | closed | not included | none | no |
  | S2 | solver chooses | closed | optional | Port Melbourne only | no |
  | S3 | solver chooses | closed | open | all PDCs | yes |

- Products keep their **origin**, e.g. `PP_MPF` or `EP_STG_Darebin`. When a zone moves to another PDC, its freight still comes from the same sort site.
- S0 is checked against the baseline: same demand total, and every last-mile lane has the same distance and cost.
- Capacity is a Max on each PDC's **delivery** outflow, so chain 1 pickup doesn't use it up.
- Chain 1 is on when `CHAIN1=1` in `dials_scenario.csv`. Only Sunshine West and Oakleigh South collect.
- Port Melbourne capacity and growth are made up, every $ is a placeholder, and distances are straight line.

**Inputs**
- `Wip_C05_*`: 12 tables
- `factors_assumed/scenarios.csv`, `dials_scenario.csv`, `dials_chain1.csv`, `sites.csv`, `transport_modes.csv`

**Outputs** (12 tables per scenario, 48 per run)
- `Wip_C06_<S0..S3>_*` when chain 1 is off, `Wip_C06_<S0..S3>C1_*` when it's on.
- The drop task covers both variants (96 names). If you add a scenario to `scenarios.csv`, add its code to the macro_6 list in `setup_macro_drop_task.py`.
- One-off setup: run `setup_macro_drop_task.py macro_6` after creating the script task.
