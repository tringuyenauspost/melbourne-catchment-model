# Optilogic Engagement — Adhoc and Scheduled Cadence

**Budget:** 24 hours total = **20 h scheduled** (10 weeks × 2 h) + **4 h ad-hoc reserve**.
**Model in scope:** Melbourne catchment, merged single-stream baseline with two hub sortation rounds (`melbourne-baseline-single-stream-flow.ipynb`) — 17 input tables, 26 products, 24 BOMs, 51 work centres, 85 processes, 218 production policies, ~3,450 lanes, 86 flow constraints. Comparison scenario: dual-stream (`baseline_with_wc`).

---

## Operating rules

**Scheduled sessions (2 h, weekly)**
- Pre-read sent to Optilogic **48 h before**: 1-page brief + the relevant CSVs / run outputs / diagram.
- Shape: 10 min decisions recap → 90 min topic → 20 min actions + triage of the parking lot.
- Every session ends with: decisions logged (what we'll change in the model), owner, and what "done" looks like before the next session.

**Ad-hoc pool (4 h, use sparingly)**
- Burn only when **blocked**: a NEO run fails/won't converge, an infeasibility we can't diagnose within a day, or a schema question that stops CSV generation.
- Book in 30–60 min bursts; log each burn against the pool in this file.
- Checkpoints at **Week 5** and **Week 8**: re-plan remaining sessions against what the pool and backlog say.

**Session ledger**

| Week | Hours | Theme | Status |
|---|---|---|---|
| 1 | 2 | Architecture validation | |
| 2 | 2 | Schema & solver semantics | |
| 3 | 2 | Transport & consolidation | |
| 4 | 2 | Business rules & infeasibility | |
| 5 | 2 | Time: multi-period & windows *(checkpoint)* | |
| 6 | 2 | Costing & financial basis | |
| 7 | 2 | Outputs, KPIs & scenario compare | |
| 8 | 2 | Scenario design & sensitivities *(checkpoint)* | |
| 9 | 2 | Automation, API & scale-up | |
| 10 | 2 | Wrap: roadmap & self-sufficiency | |
| Ad-hoc | 4 | Blockers only | 0 h used |

---

## Week 1 — Architecture validation (get their blessing or their objections early)

**Objective:** Optilogic confirms — or corrects — the modelling architecture before we build more on it.
**Bring:** the flow diagram, the balance equation, model-overview notes.

Key questions:
1. **Single-stream merge + steady-state closure** — we run one representative day where sources = sinks (`P + IN + stage = D + OUT + L`); today's local despatch stands in for tomorrow's delivery. Is this a pattern they've seen work at scale? Known pitfalls?
2. **Stage-as-product chain** (each processing stage is its own product — `Pickup → Unload1 → Sort1 → Despatch1_<hub> → Unload2 → Sort2 → Despatch2 → Delivered`, 1:1 BOMs, no lanes for intermediates; origin-flavoured Despatch1 forces the two hub sorts onto different hubs) — is this the idiomatic Cosmic Frog way to force stage order and conservation, or is there a native feature we're re-implementing?
3. **Boundary sinks/sources** (interstate arrivals as co-located suppliers, interstate/local-terminate as co-located customers) — better native constructs?

**Exit:** a written yes/no per pattern + list of anything to restructure before Week 4.

## Week 2 — Schema & solver semantics (WorkCenters / Processes / BOMs)

**Objective:** stop guessing at Anura semantics we currently infer from behaviour.
**Bring:** WorkCenters.csv, Processes.csv, ProductionPolicies.csv + the WC summary from the last run.

Key questions:
1. Shared work centres: multiple processes referencing one WC — is `throughputcapacity` the **sum across processes**, as we assume? EA vs HR capacity basis — when to use which; how `processingrate` interacts.
2. `MinimumThroughput` (our 10% manual-sort floor, 5,023 EA/hub): does it force flow even if the solver routes volume elsewhere? Can it create infeasibility, and how does NEO report it?
3. Per-day fixed-cost basis (annual ÷ 252) on WCs + `Fixed_Cost_*` StepCosts curves on Facilities — are both charged? Double-count risk?
4. Facility `throughputcapacity` (PUD_CAPACITY): which flows count against it — inbound, outbound, production, transit?

**Exit:** documented semantics for each field we use; corrections queued for the notebook.

## Week 3 — Transport & consolidation modelling

**Objective:** validate the trip-cost math and improve the consolidation patterns.
**Bring:** TransportationPolicies/Modes CSVs, lane-cost examples, the wave-load finding.

Key questions:
1. **Treat-As-Full** linehaul: is cost = per-trip `fixedcost` × ceil(flow / averageshipmentsize)? Prorate = linear? Confirm exact formulas.
2. **Wave-load coupling** — driver loads 80/hr in a 2 h wave < 180 van capacity, so we set White_Van trip size to 160. Is `averageshipmentsize` the right lever, or is there proper **fleet/trip modelling** (vehicle counts, rounds, driver shifts) in Cosmic Frog or an adjacent Optilogic product?
3. **Min 500/lane flow floors** as consolidation proxies — we watched them bind exactly (500.0 × 4 out of Bayswater). Is there a native minimum-shipment / frequency construct that avoids hard Min floors?
4. Distance basis: we use haversine; do they recommend road-distance factors or an internal distance engine?

**Exit:** confirmed cost math; a recommended consolidation pattern; decision on fleet modelling scope.

## Week 4 — Business rules & infeasibility debugging

**Objective:** learn to express our rules natively and to self-diagnose failed runs.
**Bring:** FlowConstraints.csv; the history of the "Site Production Capacity" violation (fixed with the 2× pickup buffer); the emergent 80/20 story.

Key questions:
1. **Ratio rules** — "80% of a sorting PUD's pickup must linehaul" is currently *emergent* (pinned sink demand + scarcity). Can Cosmic Frog express ratio/percentage constraints directly? Recommended pattern?
2. **Artificial caps as boundary conditions** — our pickup caps needed a 2× buffer to avoid false capacity violations. What's the recommended slack/soft-constraint approach (penalties vs hard caps)?
3. **Infeasibility workflow** — when NEO reports a violation, what's the triage sequence (which outputs, which logs)? Can we get shadow prices / binding-constraint reports?
4. Where do they recommend Groups + Aggregate behaviour vs per-row constraints (we use both)?

**Exit:** a written infeasibility playbook; native patterns for ratio + soft constraints.

## Week 5 — Time: multi-period & windows *(checkpoint)*

**Objective:** decide whether/how to add the time dimension the ops slide wants (A1.0 pickup windows, A3.0 sortation windows, A5.0 despatch windows).
**Bring:** the ops architecture slide; `SORT_HOURS_PER_DAY = 20` and 2 h wave assumptions.

Key questions:
1. Multi-period in Cosmic Frog: model a day as **time slices** (e.g. arrival wave / sort window / despatch window) — supported patterns, table changes needed (Periods, period-specific capacities), solve-time impact?
2. Can service commitments (next-day) be expressed as period-lagged demand satisfaction?
3. Peak seasonality: same network, scaled demand periods — one model or scenario set?
4. If multi-period is heavy: what's the lightest way to respect windows (e.g. capacity = rate × window per stage, which we already do for sorters)?

**Checkpoint:** review ledger + parking lot; re-scope Weeks 6–10 if needed.
**Exit:** go/no-go + effort estimate for a multi-period v3.

## Week 6 — Costing & financial basis

**Objective:** make the cost stack defensible for exec review.
**Bring:** cost inventory: per-parcel step costs, per-day fixed (annual ÷ 252), wage-based driver load ($40/h ÷ 80), $/km trip rates, StepCosts curves.

Key questions:
1. Mixed bases sanity check: variable $/EA + per-day fixed + per-trip transport — is the objective composition what we think? Any double counting with facility StepCosts?
2. Handling costs quoted **$/hr** (ops slide) — converting via `$/EA = ($/hr)/(EA/hr)`: fine, or is there native time-based costing?
3. Utilisation-dependent costs (idle machines still charge fixed): how do others report "cost per parcel at utilisation"?
4. CO₂ fields — worth populating now for later reporting?

**Exit:** validated cost basis note (goes into the review deck); list of cost fields to correct.

## Week 7 — Outputs, KPIs & scenario comparison

**Objective:** a repeatable read-out: which output tables answer which questions.
**Bring:** last run's 4 output CSVs; the scenario-mixing gotcha (always filter `scenarioname`); analyze_run_outputs.ipynb.

Key questions:
1. Canonical output tables for: network cost bridge, WC utilisation, lane utilisation/consolidation, sourcing mix per PUD (own-sort vs stage vs hub-fed — our Bayswater 9,288.8 / 4,750 / 9,705.2 split)?
2. Side-by-side scenario compare inside the platform (single- vs dual-stream) — native tooling vs exporting?
3. Solver diagnostics: gap, solve time, binding constraints — where to read them?
4. Recommended KPI set for a parcel network baseline review?

**Exit:** an output-reading guide; fix list for our analysis notebook (group renames, scenario filters).

## Week 8 — Scenario design & sensitivities *(checkpoint)*

**Objective:** turn the baseline into a scenario machine for the real questions.
**Bring:** the placeholder register (below) + candidate scenario list.

Key questions:
1. Scenario mechanics: cleanest way to run structured variants (parameter sweeps on `LOCAL_SHARE` 0.3–0.7, `LOCAL_KEEP_FRAC` 0.1–0.3, sorter capacities, floors) — scenario tables vs separate models vs API?
2. Facility scenarios: close/open a PUD, add a sorter (a 4th sorting PUD), automation upgrades — how to model open/close decisions (binary) vs fixed status; solve-time implications?
3. How to keep scenario inputs version-controlled and reproducible (they see our CSV-from-notebook pipeline)?

**Checkpoint:** review ledger; protect Week 10.
**Exit:** prioritised scenario backlog with modelling approach per scenario.

## Week 9 — Automation, API & scale-up

**Objective:** remove manual steps; assess the path from Melbourne to national.
**Bring:** the notebook pipeline; upload/run/download steps we do by hand today.

Key questions:
1. Optilogic Python API / SDK: push tables, trigger NEO, pull outputs from CI — auth, quotas, gotchas?
2. Model-size guidance: national scale (~10× facilities, ~100× lanes) — solve times, decomposition strategies, when to aggregate?
3. Multi-model management (dual-stream vs single-stream vs future v3) — workspace hygiene they recommend (we hit the multi-scenario export confusion).

**Exit:** an automation plan (what we script next); scale feasibility note.

## Week 10 — Wrap: roadmap & self-sufficiency

**Objective:** leave with everything needed to run without them.
- Walk the decisions log end-to-end; confirm nothing contradicts.
- Open-issue register: what remains, who owns it, what future support (if any) costs.
- Self-sufficiency checklist: can we diagnose infeasibility, add a scenario, read outputs, and defend the cost stack without Optilogic on the call?
- Agree the v3 roadmap (multi-period? national? fleet modelling?) with effort estimates.

**Exit:** signed-off engagement summary + v3 roadmap.

---

## Placeholder register (feed Weeks 5–8; update as ops data lands)

| Parameter | Current | Needs |
|---|---|---|
| `LOCAL_SHARE` | 0.50 | real local vs interstate OD split |
| `LOCAL_KEEP_FRAC` anchor | 20% × catchment share of P | ops rule for kept share |
| `SORT_HOURS_PER_DAY` | 20 h | real sort windows per site |
| Driver wage / headcounts | $40/h; drivers = ceil(demand/160) | payroll + rostered drivers per PDC |
| `BAG_UNLOAD_RATE` / cost | 25k/day; $0.03 | ops figures |
| Vehicle $/km + capacities | placeholders | fleet rates |
| WC fixed costs (annual) | placeholders | equipment quotes |
| Pickup volume | 50% of D, 90/10 PP/EP | lodgement data |

## Parking lot (triage each session)

- Mulgrave still gets the full 500/lane floor (10% of demand = 517) — relax further?
- Vans depart ≤160/180 full on a 2 h wave — pre-staging question for ops, then decouple `WAVE_LOAD` if confirmed.
- Slide A3.1/A3.2 both say "SPS Manual" — duplicate or a semi-auto tier?
- PUD inbound handling for *transiting* pickup (A2 says "PUD/Hub") — currently free.
- CZ_Interstate at Dandenong Letter — letters site despatching parcels interstate: keep?
