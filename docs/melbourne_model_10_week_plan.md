# Melbourne model — 10 week plan to close the data and logic gaps

Written 2026-07-28. Covers four gaps: first-mile volume, middle-mile lane flows, capacity, business logic.

## Where we are

The model runs and balances, but a lot of it is assumption, not data:

- Pickup volume is a guess: `P = 50% of delivered volume`, split 90/10 PP/EP.
- All 411 pickup catchments are given **identical** volume (`P / 411`). Obviously wrong at the postcode level.
- Interstate is a plug: out = 50% of pickup split across hubs by sorter capacity; in = whatever makes the balance close.
- Middle-mile lanes are solver-chosen with a 500/lane floor. Only Sunshine West has real flow data (from David).
- Hub docks were assumed to be one machine of each type per hub. That assumption is what broke the DLC-as-PDC run.
- We do not actually know what Optilogic counts in `throughputcapacity` — it was inferred from one violation message.

## Blocking items — start these in week 1, they have the longest lead time

- Data request to ops for first-mile lodgement volume by catchment/postcode.
- Data request to David (or equivalent owner) for inbound/outbound flows at the other 10 PDCs and the hubs.
- Data request to ops for dock and machine counts per hub, plus operating hours.
- Confirm with Optilogic support what `Facilities.throughputcapacity` counts: inbound only, inbound + outbound, and whether transit and co-located supply count.

## Week by week

**Week 1 — get the asks out, settle the capacity definition**
- Send all four requests above. Nothing else unblocks until these land.
- Run a small test model to prove what `throughputcapacity` counts, rather than inferring it from violations.
- Write down every placeholder currently in the notebook in one list, with owner and status.

**Weeks 2–3 — first-mile volume (gap 1)**
- Replace `P = 50% of D` with actual daily lodgement volume.
- Replace the flat `P / 411` with real volume per catchment. Keep the pinning approach, just change the numbers.
- Replace the assumed 90/10 PP/EP split with the actual product mix per catchment.
- Get actual interstate in and out per hub. Stop deriving interstate in from the balance.
- Re-check which facilities actually do first-mile pickup. Right now it is 5 PDCs plus 2 transport facilities.

**Weeks 3–4 — middle-mile lane flows (gap 2)**
- Use the Sunshine West data as the template: agree the format once, then apply it to the other 10 PDCs.
- Compare the model's chosen lanes against actual flows for Sunshine West. This tells us how wrong the solver's routing is before we scale up.
- Get hub-to-hub flows. This is the one that tests the two-sort-round assumption.
- Decide whether lanes should be pinned to actuals for the baseline, or left free with the actuals used only to validate.
- Replace the 500/lane floor with real minimum loads once we can see what actually runs.

**Weeks 5–6 — capacity (gap 3)**
- Rebuild hub capacity from real machine counts and hours instead of the assumed one-of-each-type.
- Get a throughput figure for Dandenong Letter as a PDC. There is currently none.
- Resolve the PDCs already over their ops capacity: Bayswater, Oakleigh South, and now Mount Waverley and Sunshine West sitting inside the headroom. Either the ops number is wrong or the volume is.
- Replace the bag-unloading placeholders (rate, unit cost, fixed cost) with real numbers.
- Replace the placeholder driver wage. Confirm driver headcount per PDC against the 1,052 the model implies.
- Replace the placeholder fixed operating costs for every machine type.
- Confirm the 20-hour machine day.

**Weeks 6–7 — business logic (gap 4)**
- Confirm every parcel really is sorted twice, at two different hubs. If some volume is sorted once, the model is overstating hub cost and dock capacity.
- Confirm the 20% local keep at sorting PDCs, and whether it is the same at every site.
- Confirm the steady-state staging assumption: today's staged volume equals yesterday's kept volume.
- Confirm interstate leaves after one sort only.
- Decide whether Dandenong Letter as a PDC gets a delivery area. If yes, we need a re-clustering run to assign it clusters.
- Decide whether 2 products (EP/PP) is enough or whether we need the 5 delivery classes separately.
- Decide whether cut-off times and service windows need to be in the model at all. Right now there is no time dimension — one representative day, no sequencing.
- Confirm the manual sort 10% floor.

**Week 8 — rebuild**
- Put all the real data into the notebook and regenerate.
- Remove the headroom parameters where real capacity data has replaced the guesswork.
- Keep the notebook structure. Only the inputs should change.

**Week 9 — validate**
- Compare model output against actuals: volume per facility, lane flows, machine utilisation, cost.
- Work through any remaining Optilogic constraint violations. Fix the real bottleneck, not the elastic constraint.
- Write down where the model still disagrees with reality and why.

**Week 10 — scenarios and handover**
- Re-run the DLC-as-PDC scenario on real data and give a proper answer on whether it works.
- Run whatever other scenarios the business wants.
- Document the model and hand over.

## Decisions we need from the business, not from data

- Does Dandenong Letter as a PDC get a delivery area?
- Are we optimising cost only, or does service need to be a constraint?
- Do we pin lanes to actuals, or let the solver route and use actuals to validate?
- Who signs off on capacity numbers that go above the ops figures?

## Risk

- If the first-mile and middle-mile data does not land by end of week 4, weeks 8–10 slip. Everything after week 4 assumes real data is in hand.
- The +26% hub dock requirement in the DLC-as-PDC scenario is based on assumed machine counts. Real counts could make it better or considerably worse.
