# Melbourne Catchment Model — Optilogic Modelling Review

*What we built in Cosmic Frog (NEO), reviewed against the kick-off modelling structure: inputs prepared, business logic encoded, baseline outputs, and observations.*
*Model: `create_input_melbourne_catchment_baseline_v2_model.ipynb` → `outputs/mebounre_catchment_baseline_v2/` (16 Anura tables). Analysis: `notebooks/output_analysis/` (9 notebooks).*

---

## 1. Objective & alignment with the kick-off plan

**Kick-off objective (project-plan deck):** *"Build a Melbourne Catchment parcel-network optimisation model in Optilogic Cosmic Frog spanning first-, middle- and last-mile, then test scenarios on cost, service and facility utilisation."*

The kick-off structured each phase into two complementary components — **Transportation modelling** and **Facility modelling** — with volume conservation across phases (*1st-mile output = middle input = last-mile origin volume*). That is exactly how the model is organised:

| Kick-off stage | What we implemented in Optilogic | Status |
|---|---|---|
| First mile — Route Collection | 411 catchment suppliers (`SUP_Pickup_*`, one per postcode) originate `Pickup_EP/PP`; **Red Vans** to van-op PUDs, **Trucks** to transport facilities | ✅ |
| First mile — Collection Facilities | 5 van-op PUDs **+ 2 new Transport Facilities** receive Pickup (`ProcurementPolicies`) | ✅ |
| Middle mile — Point-to-point line-haul | PUD→HUB and HUB→PUD lanes with **Rigid / 12-Tonner / B-Double choice** (volume-driven) | ✅ |
| Middle mile — Originating processing (HUB) | Full **unload → sort → load** recipes at machine-level work centres, per-hub sorter capability | ✅ |
| Middle mile — Terminating processing (HUB) | Last-mile volume enters as pre-sorted supply (`SUP_HUB`, "arrived previous day") and is **re-handled (unload→sort→load)** at the HUB | ✅ (HUB↔HUB leg abstracted) |
| Last mile — Delivery facilities (PDC/PUD) | **Driver sort** (3rd/final sort) + **load-to-vehicle** at each PUD, with real ops throughput caps | ✅ |
| Last mile — Route delivery | Contract **White Vans** PUD→cluster (1,046 clusters) | ✅ |
| Volume conservation | All BOMs 1:1; per-product supply capacities sized to demand | ✅ verified in run |

**Weekly-plan position:** we are at the *Week 6–7 "Baseline solve — run first baseline; debug, resolve infeasibilities and data gaps; review parcel flows"* milestone — a baseline has solved, one infeasibility was diagnosed and fixed (§8), and output review notebooks are built (§9).

### 1.1 Cross-check against the full kick-off deck (`Optilogic_Kick_Off.pptx`)

**Activity taxonomy (kick-off slides 13–14) maps 1:1 to our work centres:**

| Kick-off activity | Our implementation | Match |
|---|---|---|
| [A2.1/A4.1] Forklift in/outbound | ULD unload & load (55k/day, $0.015) | ✅ |
| [A2.2] Loose Hand | Loose unload & load (15k/day, $0.05) | ✅ |
| [A2.3] Loose Longreach | Longreach unload & load (35k/day, $0.02) | ✅ |
| [A3.1/2] SPS Manual | Manual sort (25k/day, $0.06) + 10% floor | ✅ |
| [A3.3] SPS Automated | Small auto sort (90k/day, $0.02) | ✅ |
| [A3.4] LPS Automated | Large auto sort (250k/day, $0.01) | ✅ |
| Activity attributes: capacity · throughput/hr · fixed · variable | WC capacity · rate/day · fixed $/day · unit cost | ✅ (per-day, not per-hour) |
| Vehicles: capacity + fixed + variable (hr **+ km**) | capacity + $/km (driver-hr + per-day fixed pending) | ⚠️ partial |
| Time windows ([A1.0] pickup, [A3.0] sortation, [A5.0/A6.0] despatch) | not modelled — the cut-off gap | ⚠️ gap |

**Demand calibration (kick-off slide 12 — the biggest cross-check finding).** AP's terminating-volume table uses **exactly our capacity figures** (same source ✅), but its volumes are much heavier: across our 11 modelled sites AP shows **271,949 parcels/day vs our modelled 167,431 (we are at ~62%)**. On AP's volumes **5 of our 11 sites breach capacity** (Sunshine West −4,434, Mt Waverley −2,187, Abbotsford −2,078, Bayswater −894, Pakenham −500); on our lighter clustered baseline none breach — though our Mt Waverley watch-out (99.7% full) flags the same site AP flags. → **Action: calibrate the demand baseline before capacity conclusions are quoted.**

**Scope note:** the kick-off covers 17 metro sites (adds Mornington Peninsula: Rosebud/Mornington/Hastings, and Geelong: Avalon/Ocean Grove/Torquay); we model the 11 metro PDCs.

**Engine enablers the kick-off names for our open gaps:** SLA (KPI 4) → `Customer Transit Constraints` (Transit Band, Band %) + `TransportationPolicies.TransportTime`; facility footprint → `InitialState='Potential'` / `FacilityStatus='Consider'` + `FacilityCountConstraints`; automation strategy → sortation variants + `FacilityCountConstraints` (our WC-variant design already follows this pattern ✅); intra-day peaks (morning last-mile vs evening first-mile sharing capacity) → flagged NEO limitation in the kick-off, THROG for time-sequenced review.

---

## 2. Network — nodes & facilities (17 facilities, 1,061 customers, 426 suppliers)

### 2.1 HUBs (4) — middle-mile sort centres
| HUB | Auto sortation (real ops input) | Manual sort |
|---|---|---|
| HUB_Melbourne_Gateway | **Small** items only | ✅ |
| HUB_Dandenong_Letter | **Small** items only | ✅ |
| HUB_Tullamarine_Facility | **Large** parcels only | ✅ |
| HUB_Melbourne_Parcel | **Large + Small** | ✅ |

### 2.2 Parcel Centres / PUDs (11) — with real ops daily capacities
| PUD | Cap (EA/day) | PUD | Cap (EA/day) |
|---|--:|---|--:|
| Sunshine West* | 55,767 | Dandenong South* | 29,754 |
| Darebin | 38,018 | Tullamarine | 29,160 |
| Bayswater* | 35,520 | Pakenham | 18,444 |
| Oakleigh South* | 32,375 | Mulgrave | 11,100 |
| Melbourne North* | 30,770 | Abbotsford | 5,828 |
| | | Mount Waverley | 3,885 |

*\* = the 5 first-mile van-operation collection depots.* Caps sit on `Facilities.throughputcapacity`. **Watch-out:** Mount Waverley runs at 99.7% of cap on last-mile demand alone (3,874 / 3,885).

### 2.3 First-mile-only Transport Facilities (2 — added this build)
| Facility | Catchment | Coordinates |
|---|--:|---|
| PUD_Dandenong_Transport | 49 postcodes | = Dandenong Letter Center (per ops direction) |
| PUD_Melbourne_Transport | 40 postcodes | = Melbourne Parcel Facility (per ops direction) |

They **collect and line-haul only** — deliberately excluded from all last-mile tables (no CZ node, no HUB→PUD lanes, no load processes; verified in the outputs).

### 2.4 Demand nodes
- **1,046 last-mile clusters** (`CZ_D*`), each pinned to its serving PUD from the clustering run.
- **15 first-mile CZ nodes** (one per HUB/PUD — *not* for the 2 transport facilities), receiving `Sorted_Pickup` demand.

---

## 3. How the flows are modelled and kept separate (staged products)

Cosmic Frog pools any product that shares a name, so the two streams are separated by a **12-product staged chain** — first-mile and last-mile volume can never mix even though they share facilities and (first-mile) machines:

```
FIRST MILE   Pickup ──(HUB: BOM_SORT, unload→sort→load)──▶ Sorted_Pickup ──▶ CZ demand

LAST MILE    Sorted_Lastmile (SUP_HUB, 80%) ──(HUB: BOM_LMSORT, unload→sort→load)──▶ Prestage_Lastmile ──▶ HUB→PUD
             Prestage_Lastmile (SUP_PUD, 20% local staging) ────────────────────────────────────┘
             ──(PUD: BOM_RESORT, driver sort)──▶ Final_Lastmile ──(PUD: BOM_LOAD)──▶ Delivered ──▶ clusters
```

### 3.1 End-to-end diagram

*See the flow-diagram slide in `optilogic_modelling_review.pptx` — activities, method options, vehicles and business rules per step, drawn per phase and per stream.*

Design points a reviewer should note:
1. **Sequencing is enforced by product state, not by convention** — `Delivered` can only come from `Final_Lastmile`, which only comes from `Prestage_Lastmile`; no stage can be skipped.
2. **Sort count is explicit:** HUB-fed last-mile parcels are sorted **3 times** (pre-sorted arrival → HUB re-sort → PUD driver sort); locally-staged parcels once. The PUD **driver sort** is a single deliberate step (drivers' final sequence sort before the customer), not a multi-level machine sort.
3. **First- vs last-mile HUB handling runs on separate machine sets** (`WC_*` vs `WC_LM_*`) so throughput and cost are distinguishable per stream in the outputs — added specifically for reporting clarity.
4. **All 4 BOM transforms are 1:1** (no loss/consolidation) — the kick-off volume-conservation rule, confirmed in the run (§9.4).

---

## 4. Phase detail — activities, options and parameters

### 4.1 First mile (collection)
- **Demand assumption:** first-mile volume = **50% of last-mile** = 83,715/day; delivered 40% to HUB-CZ nodes / 60% to PUD-CZ nodes; 90% PP / 10% EP.
- **Catchments:** the new geojson (`first_mile_manifest_catchment_include_transport_facility.geojson`) → 411 postcode catchments across 7 collection sites.
- **Supply capacity:** per-postcode `Pickup` capacity **EP 41 / PP 367** — sized at **2× demand** (`PICKUP_CAP_BUFFER = 2.0`) after the baseline exposed a binding artificial cap (§8).
- **Vehicles:** Red Van (cap 200, $1.20/km/trip, Prorate) to van-op PUDs; **Truck** (cap 1,500, $0.90/km, Prorate) to the transport facilities — the kick-off's mixed collection fleet.
- **First-mile delivery (HUB→CZ):** Truck / Rigid / B-Double, Treat As Full — contract white vans are reserved for **real last-mile customers only**.
- **Business rule encoded:** each PUD must receive at least `min(40% of its last-mile demand, pickup supply)` of Pickup (FlowConstraints, Min, Aggregate).

### 4.2 Middle mile (HUB processing + line-haul)
**HUB handling — enumerated recipes.** Every HUB process is a 3-step `UNLOAD → SORT → LOAD` recipe; the optimiser picks the cheapest feasible combination. Methods and per-step economics:

| Activity | Method | Rate (EA/day) | Unit cost ($/EA) | WC fixed ($/day) |
|---|---|--:|--:|--:|
| Unload / Load | Loose | 15,000 | 0.050 | 198 |
| Unload / Load | Longreach | 35,000 | 0.020 | 595 |
| Unload / Load | ULD | 55,000 | 0.015 | 992 |
| Sort | Manual | 25,000 | 0.060 | 397 |
| Sort | Small auto | 90,000 | 0.020 | 3,175 |
| Sort | Large auto | 250,000 | 0.010 | 9,921 |

Recipe counts respect each hub's sorter capability: Melbourne Parcel 27 recipes/stream, the other three 18 each → 81 recipes per stream, ×2 streams on separate machines.

**Forced manual sortation (real ops behaviour: ~10% is always hand-sorted).** Anura has no native ratio constraint, so it is enforced as a `MinimumThroughput` floor on every hub's manual-sort machines: **2,092 EA/day first-mile + 3,349 EA/day last-mile per hub** (= 10% of each hub's expected sort volume). The optimiser fills exactly the floor with manual (dearest method) and the rest with auto.

**Line-haul — volume-driven vehicle choice.** Every PUD↔HUB lane is offered in all three trucks with `fixedcostrule = Treat As Full` (a partial load pays a full trip):

| Vehicle | Capacity | $/km/trip | Wins when lane volume is… |
|---|--:|--:|---|
| Rigid Truck | 1,500 | 0.70 | ≤ ~1,500 |
| 12-Tonner | 2,500 | 0.95 | ~1,500–2,500 |
| B-Double | 4,000 | 1.30 | ≥ ~4,000 (multiple trips) |

Verified crossover on a 31 km lane: 500→Rigid, 2,500→12-Tonner, 4,000+→B-Double. This directly serves the kick-off's *"most volume carried by semi-trailers and B-doubles"* logic while letting thin lanes right-size down.

**Abstraction (declared):** the HUB↔HUB interstate leg (rail/air in the kick-off) is not linked through; last-mile volume enters as prev-day supply. First- and last-mile are two streams sharing facilities.

### 4.3 Last mile (delivery)
- **Demand:** 167,431 parcels/day (EP 25,182 / PP 142,249) across 1,046 clusters, each served by its designated PUD.
- **Supply split — 80/20 business rule:** 80% arrives via HUB (`SUP_HUB`, capacity EP 5,037 / PP 28,450 per hub); **20% is staged locally at each PUD** (`SUP_PUD`, capacity = 20% of that PUD's own last-mile demand) representing prev-day first-mile terminating volume. Enforced by supplier capacities + cost (local skips the line-haul and HUB re-handle).
- **PUD activities:** **driver sort** (40,000 EA/day, $0.05/EA, $198/day fixed — the kick-off's contractor-driver final sequencing) then **load-to-vehicle** (loose/longreach/ULD options as at the HUB).
- **Delivery vehicle:** contract **White Van** (cap 180, $1.10/km, Prorate) — kick-off: 1,800 contracted vans, ~150 parcels/route.

---

## 5. Business-logic register (rule → encoding → value)

| # | Business rule | Anura encoding | Value |
|---|---|---|---|
| 1 | Two transport facilities do first-mile catchment only | Facilities + exclusion from all last-mile tables | Dandenong Transport, Melbourne Transport |
| 2 | Prev-day first-mile terminating volume staged at PUD | `SUP_PUD_*` suppliers + capacity | 20% of PUD last-mile demand |
| 3 | Last-mile is sorted at HUB **and** again at PUD | `BOM_LMSORT` (HUB) + `BOM_RESORT` (PUD driver sort) | 3rd sort by drivers |
| 4 | ~10% of sortation is always manual | `WorkCenters.MinimumThroughput` on manual-sort WCs | 2,092 FM + 3,349 LM per hub |
| 5 | Hub sorter capability differs by site | Per-hub recipe enumeration | small/small/large/both |
| 6 | PUD daily capacity from operations | `Facilities.throughputcapacity` | 3,885–55,767 EA |
| 7 | Red vans collect for PUDs; trucks for transport facilities | `modename` per pickup lane | Red_Van / Truck |
| 8 | Line-haul by rigid / 12t / B-double, right-sized to volume | 3 modes per lane, `Treat As Full` | see §4.2 |
| 9 | Contract white vans serve **real last-mile customers only**; FM HUB→CZ runs on trucks | `modename` per delivery lane | White_Van (LM) / Truck·Rigid·B-Dbl (FM) |
| 10 | Consolidation economics (fuller vehicle = cheaper/parcel) | mode `fixedcost` = $/km×dist ÷ `averageshipmentsize`, Prorate/TAF | 6-vehicle fleet |
| 11 | First-mile pickup floor per depot | FlowConstraints Min | min(40% demand, supply) |
| 12 | Minimum line-haul service per lane | FlowConstraints Min | 500 EA/lane |
| 13 | Costs charged where NEO reads them | `Processes.unitcost` (not ProductionPolicies — ignored when a process is named) | all 530 steps costed |

---

## 6. Inputs prepared (16 Anura tables)

| Table | Rows | What it encodes |
|---|--:|---|
| Facilities | 17 | 4 HUBs + 11 PUDs (ops caps) + 2 transport facilities |
| Products | 12 | 6 stages × EP/PP staged-product chain |
| BillOfMaterials | 8 | 4 transforms × 2 products, all 1:1 |
| WorkCenters | 110 | per-machine capacity, annual fixed cost, manual floors |
| Processes | 530 | every activity step with rate + unit cost |
| ProductionPolicies | 412 | recipe alternatives bound to BOMs per facility |
| Suppliers / SupplierCapabilities | 426 / 852 | 3 supply streams + the 80/20 and 2×-buffer capacities |
| ProcurementPolicies | 852 | supplier→facility sourcing edges |
| TransportationPolicies | 3,736 | all lanes with mode, distance, per-trip cost |
| TransportationModes | 6 | vehicle fleet (capacity + cost rule) |
| ReplenishmentPolicies | 144 | HUB↔PUD middle-mile edges |
| CustomerFulfillmentPolicies | 2,208 | who serves which demand node |
| CustomerDemand | 2,118 | 167,431 LM + 83,699 FM parcels/day |
| FlowConstraints / Groups | 86 / 1,504 | floors + stream-distinct aggregation groups |

---

## 7. Key assumptions (explicit, for review)

1. First-mile volume = **50%** of last-mile (no first-mile demand data yet); split 40/60 HUB/PUD, 90/10 PP/EP.
2. Local staging fraction = **20%** (`PUD_LOCAL_STAGE_FRAC`) — flat, not derived from catchment terminating data.
3. **All vehicle rates/capacities are placeholders** pending translation of the kick-off cost build-ups (e.g. red van $48.11/day fixed + $0.28/km fuel + $34.06/hr driver over 10-hr day) into $/km-per-trip equivalents.
4. Handling unit costs and WC fixed costs are **realistic-order placeholders**, not finance-validated.
5. **All costs are per-day** — WC fixed = annual quote ÷ 252 working days (total $112,283/day); the Facilities `Fixed_Cost_*` curves must match this basis.
6. Single representative day — **no periods/seasonality**, no induction/dispatch **cut-offs** yet (kick-off Week-5 item).
7. Parcel-count (EA) only — no weight/cube; motorbike posties not modelled.
8. Network is fixed (all facilities/machines Open & Existing) — no siting or investment decisions in the baseline.

---

## 8. Debugging log — the baseline infeasibility we found and fixed

**Symptom:** one violated constraint in `OptimizationConstraintSummary` — *Site Production Capacity* at `SUP_Pickup_Melbourne_North_3062` (`Pickup_PP`): capacity 184 vs required 2,113.8.

**Diagnosis:** pickup supplier capacity had been sized to *exactly equal* demand (`ceil(demand/411)`), leaving 0.4% global slack; 5 of 7 first-mile depots were pinned at 100% of pickup capacity. Adding the PUD ops caps and manual-sort floors reshuffled hub allocation and consumed the last slack, so NEO relaxed the cheapest catchment's cap (1.15 km from its PUD).

**Judgement call:** the pickup cap is a *modelling construct*, not a real limit — the real constraints are demand and PUD capacity. **Fix:** `PICKUP_CAP_BUFFER = 2.0` (per-postcode caps EP 41 / PP 367; ~100% headroom). Verified the first-mile pickup Min constraints were unchanged (still demand-bound at 38,034 PP).

---

## 9. Baseline outputs & observations (analysis notebooks `00–08`)

### 9.1 KPI coverage vs kick-off objectives
| # | Objective | KPI | Baseline status |
|---|---|---|---|
| 1 | Minimise total network cost | Total daily OPEX | ✅ **all per-day and booking** (12-Jul run): transport $14,853 + WC fixed $112,283 + handling $21,537 ≈ **$148,673/day** |
| 2 | Minimise contractor fleet | Peak vehicles | ✅ trips-as-proxy (below) |
| 3 | Minimise travel time | Avg route duration | ✅ 27,298 lane-km, 496 lane-hours |
| 4 | Maximise catchment | % volume within SLA | ⚠️ **gap** — needs cut-offs + service times |
| 5 | Balance facility utilisation | — | ✅ (below) |
| 6 | Minimise empty kilometres | — | ⚠️ **gap** — needs backhaul modelling |

### 9.2 Fleet (KPI 2) — trips/day by mode (12-Jul run)
| Mode | Trips | Cost/day | vs available fleet |
|---|--:|--:|---|
| White_Van | 930 | $9,887 | within 1,800 contracted; **origins are PUD-only** — white vans now serve real last-mile customers exclusively ✅ |
| Red_Van | 249 | $2,208 | within 500 |
| Rigid_Truck | 69 | $1,393 | — |
| B_Double | 51 | $857 | **borderline vs the kick-off fleet of 50** (was 92 — monitor) |
| Truck | 23 | $399 | within 50 |
| Twelve_Tonner | 7 | $109 | — |

The FM HUB→CZ correction is visible in the outputs: that leg now runs on **Rigid (22 trips) + B-Double (13)** — no white vans. White-van trips fell 1,209 → 930 (≈ the predicted last-mile-only level) and total transport cost fell **$19,529 → $14,853/day (−24%)**, because trucks carry the first-mile delivery volume far cheaper than vans. The line-haul mix (Rigid 69 / B-Dbl 51 / 12T 7) confirms Treat-As-Full right-sizes vehicles to lane volume.

### 9.3 Utilisation (KPI 5) & cost booking
54 of 110 work centres used; active machines average ~32% utilisation, max 92%. Expected shape for enumerated recipes — prune to real machine counts for a genuine balance study. **Cost booking verified:** WC fixed charges **$112,283/day** (per-day basis lands exactly) and process/handling books **$21,537/day** (was $0 in the earlier export). **Manual-sort share = 10.0% exactly** — the MinimumThroughput floors deliver the ops rule to the decimal.

### 9.4 Flow & conservation (verified end-to-end)
BOM throughput confirms the chain and the 80/20 rule: `BOM_SORT` 83,699 (first mile) · `BOM_LMSORT` 133,938 (= 80% of last-mile via HUB) · `BOM_RESORT` = `BOM_LOAD` = **167,431** (every parcel driver-sorted then loaded) — so 33,493 (20%) entered as local PUD staging, exactly as designed. Last-mile delivery dominates cost and distance; line-haul is cheap per parcel — consolidation working as intended.

### 9.5 Constraint & process confirmations (files received 12-Jul 09:21)
- **ConstraintSummary: 0 violations across all 86 constraints** — the §8 pickup-capacity violation is formally cleared; no constraint is binding (all pickup mins and 500-EA lane floors carry slack).
- **ProcessSummary method mix:** unload/load runs **100% ULD** (385,068 load + 217,637 unload; loose/longreach never chosen — flag: add product-method compatibility floors if a loose/longreach share is realistic, as was done for manual sort); hub sort splits large-auto 109,806 / small-auto 86,067 / **manual 21,764 = 10.0% exactly**; driver sort covers all **167,431** last-mile parcels.

### 9.6 Data-quality observations (12-Jul export)
- **Resolved:** Warehousing summary is now from the current solve (includes the transport facilities); handling cost books; fixed costs land on the per-day basis.
- **Moderate validation findings are all legacy v1 residue still in the Cosmic Frog database**, not our inputs: 32 orphaned old-style work centres (`WC_IN_MANUAL_*`), the old 12-month Periods (outside the single-day horizon), ~80k old flow-count keys, one stale scenario item, and 656 "no outgoing arc" records tied to the old periods. **Action: purge the legacy rows or rebuild in a clean model from the 16 CSVs.**
- ~~ProcessSummary / ConstraintSummary missing~~ — **received 12-Jul 09:21 and analysed (§9.5)**.
- CO₂ = 0 everywhere (fields exist, not populated — future lever).

---

## 10. Gaps vs kick-off, and next steps (Week 8–9: scenarios + demo)

**Kick-off parameters not yet encoded:** driver hourly costs & shift lengths (van $34.06, HR/semi $35.89, B-double $40.50/hr; 10-hr days; 252 days), vehicle fixed+fuel build-ups, loading/unloading *times* (25–75 min), induction/dispatch cut-offs, posties, backhaul, SLA service levels, mandatory trunk routes / minimum frequencies / curfews (all flagged TBD in the kick-off too).

**Recommended order:**
1. **Calibrate demand to kick-off slide-12 terminating volumes** (ours 167,431/day vs AP's 271,949 across the same 11 sites) — then re-test capacity: on AP's volumes, 5 sites breach.
2. Replace placeholder vehicle economics with the kick-off cost build-ups ($/trip = fixed/day + fuel×km + driver×hrs).
3. Purge the legacy v1 rows in the Cosmic Frog model (old work centres, 12-month Periods, flow-count keys — the source of all moderate validation findings).
4. Add cut-offs via `Customer Transit Constraints` (the kick-off's named mechanism) to unlock **KPI 4 (SLA)**; add backhaul for **KPI 6 (empty km)**.
5. Scenario set: B-Double fleet cap (50), PUD capacity stress on calibrated volumes, staging fraction sensitivity (20%→30%), automation investment (WC fixed vs manual trade-off).
