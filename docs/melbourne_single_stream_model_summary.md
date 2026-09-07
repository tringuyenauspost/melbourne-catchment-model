# Melbourne Parcel Network — Single-Stream Baseline Model

**Management summary · 15 July 2026**
Platform: Optilogic Cosmic Frog (Anura schema, NEO solver) · Scenario: `Baseline_single_stream` · Source: `notebooks/melbourne-baseline-single-stream-flow.ipynb` → 17 input tables · Interactive flow diagram: <https://claude.ai/code/artifact/1379b6c2-c823-4698-ba23-90eaad522231>

---

## 1. Executive summary

We have built a **daily steady-state cost-optimisation model of the Melbourne metro parcel network** that connects first-mile pickup, facility sortation, middle-mile linehaul and last-mile delivery as **one continuous flow** — mirroring the operations architecture (A1 first mile → A2 inbound → A3 sortation → A4 outbound → A5 middle/last mile). The model is generated entirely from a version-controlled Python notebook, so every input is reproducible, auditable and cheap to re-run as a scenario.

**Scale:** 17 facilities (4 hubs, 11 delivery PUDs, 2 first-mile transport sites), 411 pickup catchments, 1,046 delivery zones, ~218k parcels/day through the network, ~3,400 transport lanes, 51 work centres.

**Status:** the build passes all internal conservation checks. The last NEO run surfaced two facility-capacity infeasibilities (Bayswater, Oakleigh South) — a genuine finding about how pickup transit consumes facility throughput — which we have adopted into the inputs; a confirmation re-run is the next action.

**Three insights the model has already produced:**

1. **Vans leave 89% full.** A 2-hour loading wave at 80 parcels/hour caps a driver's load at 160 parcels against a 180-parcel van — a structural 11% capacity giveaway unless loading is pre-staged.
2. **Pickup transit consumes PUD capacity.** With pickup pinned to catchments, Bayswater and Oakleigh South exceed their stated facility throughput once pickup transit is counted on top of delivery — the ops capacity figures need a definition check (delivery-only vs total throughput).
3. **Consolidation floors bind exactly.** The solver ships the bare minimum (500/lane) on thin PUD→hub lanes, telling us where volume does not naturally justify a service and a frequency/consolidation rule is doing the work.

---

## 2. Network scope

| Layer | Count | Detail |
|---|---|---|
| Hubs (full sortation) | 4 | Melbourne Parcel, Tullamarine, Melbourne Gateway, Dandenong Letter |
| Delivery PUDs | 11 | Sunshine West, Bayswater, Oakleigh South, Darebin, Melbourne North, Dandenong South, Tullamarine, Pakenham, Mulgrave, Abbotsford, Mount Waverley |
| First-mile sites | 7 | 5 pickup PUDs (Bayswater, Melbourne North, Sunshine West, Oakleigh South, Dandenong South) + 2 transport facilities (Dandenong Transport, Melbourne Transport) |
| Sort sites | 7 | 4 hubs + 3 sorting PUDs (Bayswater, Melbourne North, Sunshine West) |
| Pickup catchments | 411 | postcode-level suppliers, each assigned to exactly one first-mile site |
| Delivery zones | 1,046 | postcode clusters, each served by its PUD |
| Products | 2 classes | PP (standard parcel, ~86% of volume) and EP (express, ~14%) |

---

## 3. Flow architecture

### 3.1 One stream, closed on a representative day

Earlier versions modelled first mile and last mile as two disconnected streams, which double-counted facilities and let each stream ignore the other's capacity use. The merged model runs **one average day in steady state**: everything that enters the network equals everything that leaves it.

| Sources (into the network) | /day | Sinks (out of the network) | /day |
|---|--:|---|--:|
| **P** — first-mile pickup (pinned) | 83,715 | **D** — last-mile delivery demand | 167,431 |
| **IN** — interstate arrivals at hubs (cap) | 125,572 | **OUT** — interstate despatch from hubs | 41,852 |
| **stage** — yesterday's locally-kept volume | 8,348 | **L** — today's locally-kept volume | 8,348 |
| **Total** | **217,635** | **Total** | **217,631** |

Balance: `P + IN + stage = D + OUT + L` (slack +4/day is deliberate rounding headroom on the arrivals cap). OUT is set to 50% of pickup (`LOCAL_SHARE = 0.50` placeholder), and IN is derived so delivery demand is exactly met.

### 3.2 Conservation is guaranteed, not hoped for

Each parcel is tracked through **processing stages**: `Pickup → Unloaded → Sorted → Despatch → Delivered` (× 2 product classes = 10 products). Every activity converts exactly one state into the next (8 bills of material, all 1:1), and the intermediate states have **no transport lanes** — so volume physically cannot skip a stage, leak, or be double-counted. Build-time assertions verify total unload = total sort = total load before the CSVs are written.

### 3.3 The stream, stage by stage

1. **A1 First mile (pinned).** 411 catchment suppliers feed their assigned site; pickup is **fixed data, not a solver choice** — per-catchment supply = P/411 (PP 183.32 + EP 20.37/day) with a matching minimum at each site, so every PUD collects exactly its catchment volume: Sunshine West 16,702 · Bayswater 13,443 · Oakleigh South 12,221 · Melbourne North 11,610 · Dandenong South 11,610 · Dandenong Transport 9,981 · Melbourne Transport 8,147.
2. **A2 Inbound.** The 3 sorting PUDs bag-unload only what they keep; hubs unload everything arriving (three machine types: loose, ULD, long-reach). Interstate arrivals (125,572/day capacity, 31,393 per hub) enter at hubs as raw pickup and get full treatment.
3. **A3 Sortation — the 80/20 rule.** Sorting PUDs sort and keep **exactly 20%** of their own pickup, which terminates locally with no further network cost (Bayswater 2,687 · Melbourne North 2,321 · Sunshine West 3,340 = 8,348/day); the other 80% linehauls raw to hubs. Non-sorting first-mile sites export 100%. Hubs sort everything else, with a **manual-sort floor of 5,023/day per hub** (10% of expected hub sort volume is always non-machinable).
4. **A4 Outbound.** Hubs load via three machine types; at delivery PUDs, **drivers self-load**: 80 parcels/hour in a 2-hour wave, one wave/day, van capacity 180 → effective 160/van. Network requirement: **1,052 driver waves/day** (e.g. Sunshine West 232, Bayswater 149, Mount Waverley 25).
5. **A5 Middle & last mile.** PUD→hub and hub→PUD linehaul choose between Rigid Truck / 12-Tonner / B-Double (treat-as-full trip costing, i.e. you pay per truck, not per parcel); last mile is White Van at 160/trip. Interstate despatch terminates at the 4 hubs, split by their sorter capacity: Melbourne Parcel 16,223 · Tullamarine 10,057 · Melbourne Gateway 9,084 · Dandenong Letter 6,488.

---

## 4. Activities, resources and capacities

### 4.1 Work centres (51 across the network)

| Activity | Where | Daily capacity | Basis |
|---|---|--:|---|
| Unload — loose / ULD / long-reach | each hub | 15,000 / 55,000 / 35,000 | ops daily rates |
| Bag unloading | 3 sorting PUDs | 25,000 | placeholder rate, $0.03/parcel + $50k p.a. fixed |
| Large auto sort | Melb Parcel 300k · Sunshine W 200k · Bayswater 140k · Melb North 134k · Tullamarine 110k | rate × 20 h | ops sorter rates (15,000 / 10,000 / 7,000 / 6,700 / 5,500 per hr) |
| Small auto sort | Melb Gateway 280k · DLC, Melb Parcel, Tullamarine 200k | rate × 20 h | ops sorter rates (14,000 / 10,000 per hr) |
| Manual sort | each hub, cap 25,000 | **min 5,023 forced** | 10% non-machinable design rule |
| Load — loose / ULD / long-reach | each hub | 15,000 / 55,000 / 35,000 | ops daily rates |
| Driver wave load | 11 delivery PUDs | drivers × 160 | ceil(demand/160) drivers; $0.50/parcel (=$40/h ÷ 80/h) |

Note: sorter capacity (rate × 20 h) is far above daily volume — **sortation machinery is not the binding constraint**; facility throughput caps, the manual floor and driver capacity are. This means the sort **cost rates**, currently placeholders, will decide the machine mix — a key data ask.

### 4.2 Transport

| Leg | Lanes | Modes (capacity, $/km placeholder) | Costing |
|---|--:|---|---|
| Catchment → first-mile site | 822 | Red Van (200, 1.2) / Truck (1,500, 0.9) | prorated |
| PUD → hub (raw pickup) | 168 | Rigid (1,500, 0.7) / 12T (2,500, 0.95) / B-Double (4,000, 1.3) | **treat-as-full**, min 500/lane |
| Hub → PUD (despatch) | 264 | same trio | treat-as-full, min = lesser of 500 or 10% of PUD demand |
| PUD → delivery zone | 2,092 | White Van (160, 1.1) | prorated |
| Boundary handovers | 28 | co-located (arrivals, stage, interstate, local-terminate) | zero-distance |

### 4.3 Facility throughput caps (parcels/day)

Sunshine West 55,767 · Darebin 38,018 · **Bayswater 37,193\*** · **Oakleigh South 32,844\*** · Melbourne North 30,770 · Dandenong South 29,754 · Tullamarine 29,160 · Pakenham 18,444 · Mulgrave 11,100 · Abbotsford 5,828 · Mount Waverley 3,885.
\* Lifted from the ops figures (35,520 / 32,375) after NEO proved that pinned pickup transit + delivery exceeds them — flagged in the inputs and queued for ops validation. Mount Waverley runs near-tight (3,874 delivery vs 3,885 cap).

---

## 5. Assumptions and parameters

**Ops-sourced (firm):** sorter rates per site; driver load rate 80/hr; van capacity 180; 2-hour loading window; one wave per driver per day; hub unload/load daily rates; facility throughput figures (definition under review); catchment-to-site assignments.

**Design rules (ours, agreed):** 20% local keep at sorting PUDs, terminating free of further cost; pickup pinned exactly to catchments; 10% manual-sort floor; interstate demand only at the 4 hubs, weighted by sorter capacity; consolidation minimums (500/lane, scaled down for small PUDs); 252 working days for annualised costs.

**Placeholders (the data asks):**

| Parameter | Current value | Needs |
|---|---|---|
| `LOCAL_SHARE` (pickup staying in Melbourne) | 0.50 | real local vs interstate OD split |
| Pickup volume | 50% of delivery demand, 90/10 PP/EP | lodgement data |
| Local keep share | 20% | ops rule / destination data |
| Sort window | 20 h/day | real windows per site |
| Driver wage | $40/h | payroll |
| Bag unload rate & cost | 25k/day, $0.03 | ops figures |
| Vehicle $/km & capacities | 0.7–1.3 $/km | fleet rates |
| Machine fixed & variable costs | placeholders | equipment/finance data |

---

## 6. What the model captures well (strengths)

- **End-to-end network in one optimisation** — pickup, sortation, linehaul and delivery compete for the same facilities and budgets, so trade-offs (sort locally vs linehaul raw) are priced, not assumed.
- **Volume conservation by construction** — the processing-state design makes leaks impossible; totals are asserted at build time and have reconciled exactly against solver outputs.
- **Real sortation footprint** — site-specific machine types and rates, the 3 sorting PUDs, the manual-sort floor, and machine-level utilisation reporting.
- **Driver-loading physics** — wage, load rate, wave window and van size interact; the model surfaced the 160/180 van-fill issue by itself.
- **First mile as data** — pickup is pinned to specified catchments, so results reflect the network we operate, and the 80/20 split is exact rather than emergent.
- **Consolidation behaviour** — treat-as-full trip costing plus lane minimums reproduce real linehaul economics (paying for trucks, not parcels).
- **Infeasibility as insight** — capacity conflicts (Bayswater, Oakleigh South) were detected by a build-time load check and confirmed by the solver, turning a model error into an ops question.
- **Reproducible pipeline** — notebook → 17 CSVs → upload; every scenario is a parameter change and a re-run, with the full input set diff-able in git.

## 7. What it can capture next (headroom already designed in)

- **Network design questions:** close/open a PUD, add a 4th sorting PUD, upgrade a sorter — the scenario mechanics exist; each is a small input change.
- **Parameter sensitivities:** sweep `LOCAL_SHARE` (0.3–0.7), keep-share (10–30%), floors and rates to find which assumptions actually move cost.
- **Time within the day:** waves, sort windows and next-day service via multi-period modelling (feasibility to be scoped with Optilogic — Week 5).
- **Fleet realism:** vehicle counts, rounds and shifts instead of the current trip-size approximation (Week 3).
- **Peak seasonality:** same network under scaled demand periods.
- **True cost stack and CO₂** once placeholder rates are replaced (cost fields and emission fields are already in the schema).
- **Scale-up path:** the generator is city-agnostic — a national model is a data exercise, with solve-time guidance to be confirmed (Week 9).

## 8. Limitations and open questions

| # | Limitation | Consequence | Resolution path |
|---|---|---|---|
| 1 | Single average day, no time dimension | can't yet model waves, cut-offs, next-day service | Optilogic Week 5 (multi-period go/no-go) |
| 2 | Cost values largely placeholders | absolute $ not yet decision-grade; **structure and relative comparisons are** | data asks (§5) + Week 6 cost validation |
| 3 | 80/20 keep rule imposed, not derived | local-keep economics untested | destination data; native ratio constraints (Week 4) |
| 4 | Facility capacity definition unresolved | Bayswater/Oakleigh caps lifted on our interpretation | ops confirmation + Week 2 (schema semantics) |
| 5 | Interstate split by sorter capacity (assumption) | hub loading may be misallocated | OD data; revisit with lodgement volumes |
| 6 | Van fill capped at 160/180 by the 2-h wave | 11% van capacity unused in the model | ops: is loading pre-staged? then decouple (Week 3) |
| 7 | Transiting pickup handled free at PUDs | first-mile handling cost understated | add a transit-handling step once rates known |
| 8 | Haversine distances, not road km | transport cost slightly understated | road-factor or distance engine (Week 3) |
| 9 | Sorter capacity generous (20 h × rate) | machine mix will be decided by cost rates, currently placeholders | real windows + rates (§5) |
| 10 | Solver outputs are multi-scenario | misreading risk — always filter `scenarioname` | read-out guide (Week 7) |

## 9. Status and next steps

**Now (this week):**
1. Re-upload the updated inputs (facility caps lifted, pickup pinned) and re-run NEO `Baseline_single_stream`; confirm feasibility and reconcile flows against the balance table in §3.1.
2. Put the two capacity questions to ops: does PUD throughput include pickup transit, and is driver loading pre-staged?
3. Issue the data asks in §5 (owners + dates).

**Next 10 weeks — Optilogic coaching cadence** (24 h budget: 10 × 2 h scheduled + 4 h ad-hoc reserve; full agenda in `optilogic_adhoc_scheduled_cadence.md`). Each session is pointed at a specific model gap:

| Wk | Theme | What it resolves for this model |
|---|---|---|
| 1 | Architecture validation | expert blessing (or correction) of the single-stream merge and state-chain design before we build further |
| 2 | Schema & solver semantics | facility-capacity definition (limitation 4), shared work-centre capacity, fixed-cost double-count risk |
| 3 | Transport & consolidation | trip-cost math, proper fleet modelling vs our wave-load approximation (6), floors vs native frequency rules, road distances (8) |
| 4 | Business rules & infeasibility | native ratio constraints for 80/20 (3), soft-constraint patterns, a self-serve infeasibility playbook |
| 5 | Multi-period *(checkpoint)* | go/no-go and effort estimate for waves/windows/service levels (1) |
| 6 | Costing & financial basis | validated cost stack for exec-grade numbers (2) |
| 7 | Outputs & KPIs | canonical read-outs, scenario comparison, the `scenarioname` discipline (10) |
| 8 | Scenario design *(checkpoint)* | prioritised scenario backlog (close/open PUD, 4th sorter, sweeps) |
| 9 | Automation & scale | API-driven runs from the notebook; national-scale feasibility |
| 10 | Wrap & roadmap | self-sufficiency checklist and the v3 roadmap |

**Decision we are working towards:** a cost-validated baseline (post Week 6) that can price network scenarios — PUD closures, sorter investment, local-keep policy — with defensible numbers by end of the engagement.

---

## Appendix — artefacts

| Artefact | Location |
|---|---|
| Model generator (single source of truth) | `notebooks/melbourne-baseline-single-stream-flow.ipynb` |
| Generated inputs (17 tables) | `outputs/mebounre_catchment_baseline_v2/` — 3,374 lanes, 836 supplier capabilities, 2,102 demand rows, 86 flow constraints, 51 work centres, 10 products, 8 BOMs |
| Interactive flow diagram | <https://claude.ai/code/artifact/1379b6c2-c823-4698-ba23-90eaad522231> (editable copy: `merged_stream_diagram.html`) |
| Coaching plan | `optilogic_adhoc_scheduled_cadence.md` |
| Comparison scenario | `baseline_with_wc` (previous dual-stream model, kept for benchmarking) |
