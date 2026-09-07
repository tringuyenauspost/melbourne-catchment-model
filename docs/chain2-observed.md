# melbourne-optilogic-chain2-observed — the notes

The long notes from `melbourne-optilogic-chain2-observed.ipynb`, lifted out so the cells read as code. The prose is the original, verbatim. Each heading is the anchor the notebook points at.

## Contents

- [DELIVERY SOURCING (Change 18) — where a PDC's delivery volume…](#delivery-sourcing-change-18-where-a) — cell 3
- [PICKUP BALANCE (Change 18) — only consulted when…](#pickup-balance-change-18-only-consulted) — cell 3
- [LODGEMENT TYPE](#lodgement-type) — cell 3
- [Change 39 : the MANUAL-SORT FLOOR, measured off the scan…](#change-39-the-manual-sort-floor) — cell 3
- [Capacity is split EVENLY across periods, and that is a…](#capacity-is-split-evenly-across-periods) — cell 3
- [SORTATION ROUNDS](#sortation-rounds) — cell 3
- [MINIMUM VIABLE SHIPMENT](#minimum-viable-shipment) — cell 3
- [INTERSTATE SORT BYPASS](#interstate-sort-bypass) — cell 3
- [CHANGE 44b — THE MEASUREMENT SPEAKS A WIDER GRAMMAR THAN THIS…](#change-44b-the-measurement-speaks-a) — cell 4
- [Change 44: ONE lane matrix, because there is one journey](#change-44-one-lane-matrix-because) — cell 4
- [CHANGE 30 — THE SORT-ONLY SITES: eight sorting buildings, not…](#change-30-the-sort-only-sites) — cell 5
- [20b — ALL PICKUP TERMINATES, STRUCTURALLY](#20b-all-pickup-terminates-structurally) — cell 5
- [Change 21 — CLASS-ROUTED HUBS, AND AN INTERSTATE SOURCE->SINK…](#change-21-class-routed-hubs-and) — cell 5
- [CHANGE 26 — MEASURED, not assumed](#change-26-measured-not-assumed) — cell 5
- [CHANGE 26 — THE PICKUP SIDE IS PINNED TO WHAT IT WAS](#change-26-the-pickup-side-is) — cell 5
- [Change 23 — THE INTERSTATE UNLOAD MIX, MADE TO ACTUALLY BIND](#change-23-the-interstate-unload-mix) — cell 5
- [Change 33: `bounded` is now real](#change-33-bounded-is-now-real) — cell 5
- [CHANGE 29 — the model carries the MEASURED day, not a blend of…](#change-29-the-model-carries-the) — cell 17
- [Change 22 — DOCKS ARE SIZED PER HUB, NOT BY A NETWORK AVERAGE](#change-22-docks-are-sized-per) — cell 27
- [TransportationPolicies](#transportationpolicies) — cell 33
- [Change 24: minimum viable despatch shipment](#change-24-minimum-viable-despatch-shipment) — cell 35
- [Change 33 — THE WORK-CENTRE MIX AS A USER-DEFINED CONSTRAINT](#change-33-the-work-centre-mix) — cell 37
- [CHANGE 39 — THE MANUAL-SORT FLOOR](#change-39-the-manual-sort-floor-2) — cell 37

---

## DELIVERY SOURCING (Change 18) — where a PDC's delivery volume…
<a id="delivery-sourcing-change-18-where-a"></a>

*cell 3*

```text
  origin_mix              the matrix above. Local lodgement is spread across all 12 PDCs by a
                          gravity prior — genuine origin-destination data we DO NOT HAVE.
  interstate_plus_stage   a PDC delivers only (a) its own local stage and (b) interstate
                          arrivals. Everything collected in Melbourne and not kept on site
                          leaves the state. The matrix becomes DERIVED, not assumed: a pinned
                          diagonal at the staging PDCs, one INTERSTATE column, zeros elsewhere.
The second option deletes the model's most fabricated number. What it costs: a parcel lodged at
Bayswater for a Sunshine West address cannot be represented at all, so the middle mile is
100% interstate-fed and the first mile is 100% export.
```

---

## PICKUP BALANCE (Change 18) — only consulted when…
<a id="pickup-balance-change-18-only-consulted"></a>

*cell 3*

```text
Under that rule the identity P + IN + stage = D + OUT + L closes for ANY pickup total, so P
becomes a free dial and something has to pick it.
  hold_pickup       keep P at PICKUP_TOTAL (= D // 2). Interstate inbound rises to D - stage
                    and the hubs absorb the difference (~+17% touches).
  conserve_supply   choose P so total supply P + IN lands exactly where the origin_mix build
                    had it. Algebraically this also holds interstate OUTBOUND constant, since
                    OUT = P + IN - D. Total network workload barely moves; the first-mile
                    collection operation shrinks instead.
```

---

## LODGEMENT TYPE
<a id="lodgement-type"></a>

*cell 3*

```text
OFF (Change 13, 2026-07-29, by request). With no IND/RES tag on the pickup product there is
nothing in the product to select the unloading equipment, so all three unload machines become
COMPETING RECIPES at the hubs and the solver picks on cost and capacity — the same treatment
interstate inbound already gets. That is the honest fallback: the document's argument for
lodgement type is precisely that it removes the need for a ratio, so removing it puts the
equipment split back in the solver's hands (or a ratio constraint's, if you want to force it).
Everything below stays wired; set the switch back to True to restore it.
```

---

## Change 39 : the MANUAL-SORT FLOOR, measured off the scan…
<a id="change-39-the-manual-sort-floor"></a>

*cell 3*

```text
The model had no manual sort at all. Every hub owns a SORT_MANUAL work centre but its
`minimumthroughput` was blank, and at 0.06/EA against SORT_AUTO_SML 0.02 it is the
dearest sorter — so the solver booked ZERO hand sort. (The 10% floor some notes refer to
was MANUAL_SHARE in melbourne-single-stream-source-sink.ipynb, now archived; it did not
survive into this notebook.) So this REINSTATES a floor, and measures it rather than
assuming it.

THE MEASUREMENT. 9,869 of 165,567 EA (5.96%) reach a depot with no ZPT_MACHINE_SORT at any
of the eight modelled sites — analyse_sort_residual.py. That is the hand-sorted share.
It needs DROP_UNPLACED_ENTRY=False in export_chain2_factors.py: the 4,665 EA that dial used
to drop sit ENTIRELY inside this residual and are exactly the half with no handling scan
either, so dropping them and then measuring the residual reads 3.23% for a mechanism that
is 6.0% of the day. The exporter asserts nothing about this — check _provenance.csv.

WHAT IT POOLS, deliberately and with the caveat recorded. The residual is TWO mechanisms of
about 3% each: NONE (4,923 EA, no machine sort anywhere — genuinely hand-sorted) and ELSE
(4,946 EA, machine-sorted INTERSTATE then railed in — which is the Change 24 sort-bypass,
not Melbourne labour). This dial books both as manual. Set it to 0.03 to price only the
half that is really hand work.
```

---

## Capacity is split EVENLY across periods, and that is a…
<a id="capacity-is-split-evenly-across-periods"></a>

*cell 3*

```text
Of the 17 Anura tables we hold, only CustomerDemand and FlowConstraints carry a period column.
WorkCenters does not, so one throughput number applies in EVERY period. An asymmetric window
(say a 4 h morning delivery wave against a 5 h evening collection peak) therefore CANNOT be
expressed here. Splitting a machine into a _AM and a _PM work centre does not work either:
nothing binds either one to a period, so both are simply two machines at the site, usable in
both periods — which double-counts the capacity. Ask Optilogic whether Anura has a
period-specific capacity table; if it does, this becomes asymmetric and the split goes away.
```

---

## SORTATION ROUNDS
<a id="sortation-rounds"></a>

*cell 3*

```text
1 = the proposal's five-state chain. 2 = our current model: sorted at hub A, linehauled to a
DIFFERENT hub B, sorted again, then delivered. The document describes a five-state chain and does
not mention the second round; section 1 quantifies what the difference is worth.

Change 14 (2026-07-29) — THIS IS NOW WIRED INTO THE BUILD. Until now it only fed the sizing
formula and the analysis print, so setting it to 2 reported a bigger model and produced the same
small one. The second round works exactly as it does in the baseline notebooks: round-1 despatch
is ORIGIN-FLAVOURED by the hub that produced it (`Despatch1_<code>`), and no hub is given the BOM
for its own flavour — so "the two sorts happen at two different hubs" is STRUCTURAL and needs no
constraint. Interstate despatch leaves after round 1 (sorted once locally, the rest happens
interstate). Cost: every delivery-bound parcel takes two more machine touches, at a second hub.
```

---

## MINIMUM VIABLE SHIPMENT
<a id="minimum-viable-shipment"></a>

*cell 3*

```text
A CONDITIONAL Min on a lane reads "flow = 0, OR flow >= this many EA". It is not a floor —
zero always satisfies it — so it can never force volume anywhere. What it buys is that the
solver may not open a despatch arc for a handful of parcels. Worth having: in the 2026-08-02
run, 19 of the 79 used linehaul lanes carried less than half a truckload, and every linehaul
mode is `Treat As Full`, so a 9%-full Rigid_Truck costs exactly what a full one costs.
Pattern taken from Optilogic's sort-bypass demo, inputs/sort-bypass-example.

COST: a conditional min is semi-continuous — one binary per arc, so the LP becomes a MIP.
Keep the scope to the despatch arcs. Do NOT blanket all 5,342 lanes.

FEASIBILITY: this can make the model INFEASIBLE if set too high. A depot must be able to
split its intake across arcs that each clear the threshold, and no hub may despatch the
flavour it sorted in round 1, so in the worst case a depot needs two arcs and the smaller of
the two must still clear it. The build computes that ceiling per depot and asserts against
the tightest one rather than letting NEO discover it. As at 2026-08-04 the ceiling is
1,323 EA (Mount Waverley), just under a Rigid_Truck.
```

---

## INTERSTATE SORT BYPASS
<a id="interstate-sort-bypass"></a>

*cell 3*

```text
Today EVERY interstate parcel is sorted twice — round 1 at its arrival hub, round 2 at a
different site, then despatch. That is structural (the only recipe consuming a
`Despatch1_<hub>` product is the round-2 unload), which means the model cannot even PRICE the
alternative, let alone choose it.

This switch adds the alternative as a SECOND recipe at the arrival hub: sort once, load
straight to Despatch2, and go direct to the delivering depot. Same downstream product, two
production points, different leg counts — the structure Optilogic's demo is built to show.
  off            no bypass. Byte-identical to the pre-Change-25 build.
  free           the direct path exists and cost decides how much of the volume uses it.
  min_truckload  as `free`, plus a Conditional Min on the direct flow, so a bypass service
                 either runs at a sensible scale or does not run at all.

The arrival hub's own workload is UNCHANGED either way — unload, sort and load happen there
regardless. What the bypass saves is the three touches at the second site plus one linehaul
leg. Hub docks are sized for the no-bypass case, so turning this on leaves capacity
oversized, never short.
CHANGE 26 (2026-08-06): ON. The scans say interstate averages 1.11 Melbourne sorts and 90% of it
is sorted exactly once, so the no-bypass model was giving every interstate parcel roughly 0.9 sort
rounds — three machine touches and a linehaul leg — that the network does not perform. `free`
does not force the direct path; it lets cost choose, which makes the result falsifiable: compare
the solved bypass share against the observed 90%.
```

---

## CHANGE 44b — THE MEASUREMENT SPEAKS A WIDER GRAMMAR THAN THIS…
<a id="change-44b-the-measurement-speaks-a"></a>

*cell 4*

```text
Two exporter changes widened the vocabulary and chain 2 has not been refactored to meet them,
so the fold happens HERE, once, loudly, and nowhere else:

  FAMILIES  Change 36/37 split the old three (STG / INT / VIC) into four, by splitting Victorian
            freight into METRO and REGION on the lodgement point. REGION folds back into VIC.
            That is not a shrug: the exporter's own note says REGION is the UNPLACEABLE RESIDUAL
            — every geocoder layer it tests against is metro-only — so the band is an upper
            bound on regional lodgement, not a measurement of it. Merging it into VIC restores
            exactly the three-family model this notebook is built in and loses no volume. The
            split stays measured in obs_joint.csv for the day chain 2 grows a REGION supply.
  SITES     Change 30 added AVL and DLC and Change 44 admits the eight non-sorting DEPOTS as
            entry sites. This notebook has arrival nodes for six buildings. Volume measured at a
            seventh is re-spread over its own family's surviving sites IN THE SAME DEPOT ROW,
            in proportion to them — the exporter's own fold rule, applied one level further in.
            It may say where freight was sorted; it may never move it between families.
```

---

## Change 44: ONE lane matrix, because there is one journey
<a id="change-44-one-lane-matrix-because"></a>

*cell 4*

```text
obs_demand is the MAGNITUDE (what the measured day actually delivered); obs_legs is every
movement between two of our buildings, and obs_delivery is the despatch into the depot. Both
were pruned at FOLD_MIN_ARTICLES before they were written, so a lane that survives here is a
lane the diagram shows.

THE CROSS-DOCK MATRIX IS GONE, and its absence is the change rather than a gap in it. A
cross-dock leg (handled at A, sorted at B) and a round-2 leg (sorted at A, sorted again at B)
are one truck between two buildings; the role basis asked about the same movement under two
names and Change 42 then had to pool the answers back together before the fold would behave.
The path basis never splits them, so obs_legs carries both and OBS_RECV_ENTRY is empty. The
consequence for THIS notebook: the 12b hub cross-dock recipe has no measured lane to build, so
it is inert while PATH_BASIS is facility_path, and every measured movement is modelled as a
round-2 sort lane — the more expensive of the two representations, which charges a sort at each
end. Flip PATH_BASIS in export_chain2_factors.py to get the split measurement back.
```

---

## CHANGE 30 — THE SORT-ONLY SITES: eight sorting buildings, not…
<a id="change-30-the-sort-only-sites"></a>

*cell 5*

```text
The scans show EIGHT buildings running a first machine sort. Avalon Parcel Facility
(2,107 EA) and Dandenong Letter Center (1,183 EA) are small — 2.0% of the day between
them — but they are real, they are in the scan Sankey, and while they were absent every
parcel they sorted was handed to Melbourne Parcel or Tullamarine by the entry fallback in
export_chain2_factors.cohort(). Avalon is 50 km from either.

Both are SORT-ONLY: a sorter and linehaul docks, no delivery round — so they join PUD_SET
(they are buildings) and ARRIVAL_SET (freight lands and is sorted there) but never
DELIVERY_PUD_SET, exactly as DLC already did. Avalon has no row in all-data.xlsx, so its
coordinates come from the hand-managed CSV and are an ASSUMPTION.
```

---

## 20b — ALL PICKUP TERMINATES, STRUCTURALLY
<a id="20b-all-pickup-terminates-structurally"></a>

*cell 5*

```text
`interstate_plus_stage` already makes the QUANTITIES right: every local origin's deliverable
volume equals its stage, and cell 15 asserts it. But the STRUCTURE still allowed a swap. In the
unadjusted notebook `EP_OUTER_EAST_Despatch` is produced BOTH by SUP_STAGE_Bayswater and by
Bayswater's own pickup coming back from a hub, and it is demanded BOTH by CZ_Interstate_* and by
Bayswater's delivery zones. Cost favours the stage (co-located, 0 km) so the solver lands in the
right place — but nothing FORBIDS the round trip.

The fix is to stop the two being the same product. Volume is now split into THREE FAMILIES that
share no state past the pickup dock:

  PICKUP   the 7 origin clusters. Chain ends at CZ_LocalTerm (round 0, the ~20% keep) or at
           CZ_Interstate_<hub> after ONE hub sort. NO round-2 states, NO Delivered product.
  INTERSTATE  arrivals. The ONLY family that takes round 2 and reaches a delivery zone.
  STG_<tag>   yesterday's keep, delivery-ready. Supplied by SUP_STAGE_* straight into the PDC as
              Despatch2. It has NO pickup state and NO hub chain — it never touches a hub.

So "no locally lodged parcel is delivered in Melbourne today" is now a property of the recipe
table: there is no BOM anywhere that carries a PICKUP tag into Despatch2 or Delivered. Delete
every FlowConstraint and it still holds. Asserted in the BOM and ProductionPolicies cells.
```

---

## Change 21 — CLASS-ROUTED HUBS, AND AN INTERSTATE SOURCE->SINK…
<a id="change-21-class-routed-hubs-and"></a>

*cell 5*

```text
21a  A hub handles ONE class. PP is a Melbourne-Parcel / Tullamarine product at 80:20;
     EP is a Melbourne-Gateway product. This applies to BOTH directions:
       * pickup despatched interstate ORIGINATES at that class's hub(s)
       * interstate freight ARRIVES at that class's hub(s)
21b  The arrival hub is carried all the way to the delivery zone, so an interstate parcel has a
     traceable source-to-sink path. Because EP only ever lands at MGF, the EP flavour is a
     rename; PP genuinely splits into an MPF-origin and a TPF-origin product.
```

---

## CHANGE 26 — MEASURED, not assumed
<a id="change-26-measured-not-assumed"></a>

*cell 5*

```text
From the 20 May 2026 scan extract: the first Melbourne machine-sort site of every interstate
consignment, by class, renormalised over the three hubs the model allows.

  EP:  TPF 92.6% of interstate EP   MGF 1.2%    MPF 0.6%   (rest arrives at a delivery depot)
  PP:  MPF 23.4%  TPF 18.1%  MGF 0.6%           (rest arrives at a delivery depot)

EP was the big error and it is not a ratio error — it is the wrong building. The model sent 100%
of EP through Melbourne Gateway; Gateway takes 0.7% of ALL interstate arrivals, and only a third
of that small volume is EP. Tullamarine is the express gateway.

MGF keeps a small explicit share rather than being dropped. If its EP share went to zero,
`hub_classes(MGF)` would be empty, MGF would silently fall out of the interstate chain, and its
work centres would sit unused — a facility disappearing as a side effect of a ratio edit. 2% is
close to the measured 1.2% and keeps the decision visible.
```

---

## CHANGE 26 — THE PICKUP SIDE IS PINNED TO WHAT IT WAS
<a id="change-26-the-pickup-side-is"></a>

*cell 5*

```text
This is the subtle half of Change 26 and the reason it is not a one-line edit.

`CLASS_HUB_SPLIT` is consulted in BOTH directions: `arrival_split()` asks it where interstate
freight LANDS, and `pickup_split()` asks it which hub locally lodged volume is DESPATCHED from.
The scan extract is selected on "delivered by a Melbourne PDC", so a parcel lodged here and
delivered in Sydney has no row in it. It measures ARRIVALS. It says nothing whatsoever about
despatch.

So moving EP to Tullamarine on the strength of arrival data would ALSO have moved every
EP export off Melbourne Gateway — dressing an assumption up as a measurement. Pinning every
origin cluster to the pre-Change-26 split keeps the pickup leg exactly as it was.

When the lodgement-side extract lands (same query, selected on lodgement facility instead of
delivery facility), THIS is the dict to replace — and then the pin can come out.
```

---

## Change 23 — THE INTERSTATE UNLOAD MIX, MADE TO ACTUALLY BIND
<a id="change-23-the-interstate-unload-mix"></a>

*cell 5*

```text
The architecture document (FAQ 2) promises the ULD / long-reach split for interstate inbound
can be fixed, bounded, or left free. It was NOT delivered. The `bounded` setting emitted 12
FlowConstraint rows that did nothing, and the solved run came out at 96-100% ULD against a
supposed 20-80% band. Three reasons, all fatal:
  1. the rows named `<cls>_INTERSTATE_Unloaded` — the OUTPUT, which BOTH recipes produce
     identically, so nothing in the row distinguishes ULD from long reach;
  2. originname = destinationname = the hub, i.e. a self-loop; FlowConstraints act on LANES
     and an intermediate state rides no lane, so the row matched nothing;
  3. the values were IN_by_class / n_hubs, an even split that Change 21a made obsolete.

THE FIX, and it is the same trick that made the class split and the terminating rule work:
stop trying to constrain a choice, and make the choice STRUCTURAL by splitting the product.
Interstate freight now ARRIVES already presented, as it does in reality — a container is a
container before anyone decides what to do with it:

  fixed    `<cls>_INTERSTATE_ULD_Pickup` and `<cls>_INTERSTATE_LR_Pickup`, supplied in the
           target proportions by SupplierCapabilities. Each has exactly ONE unload recipe, so
           the ratio is exact and needs no constraint at all. This is the doc's
           "baseline calibration" case, and it is what "force the solver to use X" means.
  free     one arrival product, two competing recipes, solver picks on cost. Today's actual
           behaviour — now honestly labelled, with no dead constraint rows.
  bounded  NOT expressible here, and the notebook says so rather than pretending. The ULD dock
           at a hub also carries pickup round-1 AND round-2 volume, so a WorkCenter min/max
           bounds the machine's total, not the interstate portion. Real bounds need DEDICATED
           interstate docks (own work centres, dock capacity split explicitly) — a bigger
           modelling decision that should be taken deliberately, not defaulted into.
```

---

## Change 33: `bounded` is now real
<a id="change-33-bounded-is-now-real"></a>

*cell 5*

```text
The objection above was right about WorkCenters and wrong about the model as a whole: a
WorkCenter min/max does bound the MACHINE's total, which is shared with pickup and round-2
volume. But a UserDefinedVariable is scoped by PRODUCT and PROCESS together, so it can bound
the interstate portion of a shared dock without dedicating work centres to interstate. That
is the capability Optilogic's Sort ByPass v2 model demonstrates, and it is what makes this
mode expressible. Structurally `bounded` is `free` — ONE arrival product, both recipes
competing — plus the constraint rows built after Groups. INTERSTATE_ULD_BOUNDS is superseded
by unload_mix.csv + WC_MIX_BAND and is kept only so old runs stay readable.
```

---

## CHANGE 29 — the model carries the MEASURED day, not a blend of…
<a id="change-29-the-model-carries-the"></a>

*cell 17*

```text
temp_clustered.csv counts every delivery date the scan extract touches; every observed factor
is measured on the peak day alone. Multiplying one by the other gave the model an interstate
total of 55,594 EA against the 49,276 EA the scans show — the share was right and the base was
not. Each (depot, class) block is scaled to its measured volume and integerised by largest
remainder, so the zone geography is untouched and the totals are the diagram's.
The tail this removes is not a random 11%: a parcel delivered after the peak day is only in the
extract because it was already in the network on it, so 85% of that tail reads as staged
freight. Keeping it would have inflated the stage share the whole model turns on.
```

---

## Change 22 — DOCKS ARE SIZED PER HUB, NOT BY A NETWORK AVERAGE
<a id="change-22-docks-are-sized-per"></a>

*cell 27*

```text
NEO reported Workcenter Capacity violations on WC_UNLOAD_ULD / WC_LOAD_ULD at Melbourne Parcel
(+5,866 EA/day each). Root cause: this cell inherited a UNIFORM dock scale — total touches
divided by (hubs x base fleet) — which was fine while every hub sorted the same mix. Change 21a
pins 80% of the PP stream to MPF, so round 1 is now violently lopsided:
    MGF 28,906   MPF 140,597   TPF 35,149
A network average handed all three the same 134,733 EA/side, which is LESS than MPF's own PINNED
round-1 volume — infeasible before a single round-2 parcel arrives. Each hub is now sized to the
work IT has to do.
  round 1  PINNED by the class split — a hard lower bound on that hub's dock.
  round 2  not pinned (the solver picks the second sort site), but Change 21b restricts it: a hub
           may only take flavours it did NOT originate. So its round-2 exposure is capped by the
           volume that arrived at the OTHER hubs. Sized at the smaller of an even share and that
           pool. GROSS of the DLC divert, since a dock rate is a Max and must cover the case where
           the solver sends nothing to DLC.
Change 28: chain-2 touches only — chain 1 sizes its own docks in its own notebook, and the
combiner merges the two workloads.
```

---

## TransportationPolicies
<a id="transportationpolicies"></a>

*cell 33*

```text
Read this cell as the two SOURCING CHAINS, laid out in order. Nothing joins them.
  CHAIN 1  pickup -> sortation -> TERMINATE   (legs 1, 2, 3a, 3b)
  CHAIN 2  interstate + stage -> delivery      (legs 4, 5, 6, 7, 8)
Column list for the transport table. Normally the Anura reference export in inputs/optilogic;
if that folder is missing, fall back to the HEADER of our own last generated table so a missing
reference cannot leave the output folder half-written (it did on 2026-08-02 — the run died here
and left 6 tables stale while 12 were new, which would have uploaded as a broken model).
```

---

## Change 24: minimum viable despatch shipment
<a id="change-24-minimum-viable-despatch-shipment"></a>

*cell 35*

```text
CONDITIONAL Min — "flow = 0, or flow >= N". Blank product and blank mode, so it binds the
arc's TOTAL: the operational question is "do we run a despatch service on this arc at all",
not "how much of each parcel state travels on it".

THE FEASIBILITY CEILING, computed rather than guessed. A depot cannot take all its volume on
one arc, because no hub may despatch the flavour it sorted in round 1 — so at least two arcs
are needed and BOTH must clear the threshold. The best split isolates the depot's largest
flavour on its own arc and puts the rest on a second, giving a per-depot ceiling of
max_f min(v_f, total - v_f). The network ceiling is the smallest of those.
```

---

## Change 33 — THE WORK-CENTRE MIX AS A USER-DEFINED CONSTRAINT
<a id="change-33-the-work-centre-mix"></a>

*cell 37*

```text
WHY THIS EXISTS. Until now the notebook had two ways to set an unload mix and neither was a
statement of policy you could vary:
  fixed  split the ARRIVAL PRODUCT in two (Change 23) and give each half exactly one recipe.
         Exact — but it clones a product to express a ratio, only works at the point of supply,
         and every downstream table has to carry the extra flavour.
  free   two competing recipes, cost decides. ULD is $0.015/EA and long reach $0.020, so ULD
         fills first and the realised mix is a by-product of how the docks were sized. In the
         last recorded WC run this gave 88.7% ULD / 11.3% LR / 0.0% HAND at Melbourne Parcel —
         the model books NO hand-unload labour at all, which is not what the operation does.
  bounded was documented as not expressible, because a WorkCenter min/max bounds the MACHINE's
         total and a hub's ULD dock is shared with pickup and round-2 volume.

A UserDefinedVariable is scoped by PRODUCT and PROCESS at the same time, which is exactly the
missing capability — it can bound the interstate portion of a shared dock without dedicating
work centres to interstate. So `bounded` is real now.

THE ALGEBRA. For family F at site S, method m, target share s and band b:
    V_lo = production(F at S via m) - (s - b) * production(F at S via any method)  >= 0
    V_hi = production(F at S via m) - (s + b) * production(F at S via any method)  <= 0
Both compare against a variable total, so the share lives in the COEFFICIENT and the constraint
VALUE is 0. That is why a band needs two variables rather than one with a tolerance: `s * Total`
is not a constant the solver can be handed. Optilogic's own example pins with
`Fixed With Tolerance = 0`, which is the b = 0 case of the same construction.

THE n-1 RULE. Never constrain every method of a family. m shares that must sum to 1, plus the
flow balance, is one equation too many: any rounding makes the model infeasible with no
diagnostic. unload_mix.csv marks exactly one method per family as the unconstrained residue,
and the loader in the config cell asserts it.

DENOMINATOR SCOPE. The total term filters on facility + the family's Unloaded product group and
nothing else. Only unload recipes produce `<cls>_<FAM>_Unloaded`, so that IS the family's total
unload at that site — no process filter needed, and one less thing to get wrong.
```

---

## CHANGE 39 — THE MANUAL-SORT FLOOR
<a id="change-39-the-manual-sort-floor-2"></a>

*cell 37*

```text
"At least MANUAL_SORT_SHARE of delivered volume is sorted by hand."

WHY A UDC AND NOT A WORKCENTER MINIMUM. `minimumthroughput` on WC_SORT_MANUAL takes a flat
EA figure, which fixes the hand-sort volume no matter what the solver routes through the
hub — the floor would keep its size while its denominator moved. The measured quantity is
a SHARE, so it belongs in the object that can express one. That is the same argument that
earned Change 33, applied to a second question.

THE DENOMINATOR CONVERSION, and why it is not an equal EA split. The dial is a share of
DELIVERED volume, because that is the basis it was measured on. A UDC cannot be a share of
a constant, so the target is converted once, here:

    hub_share = MANUAL_SORT_SHARE * D_TOTAL / (round-1 sort at the hubs)

and every hub carries that SAME converted share. MGF sorts about a third of what MPF does;
an equal EA split would have MGF hand-sorting 32% of its round-1 volume while MPF did 9%.
A common share puts the work where the freight is and still totals the dial x D.

WHY ROUND 1 ONLY. The residual is a first-sort measurement — 5.96% of the day reached its
depot with no machine sort behind it AT ALL. Round-2 products (`..._Sorted2`) are kept out
of the denominator group, so changing HUB_SORT_ROUNDS cannot silently rescale the floor.
The cross-dock is subtracted too: BOM_LOAD_XDOCK consumes `_Unloaded`, so that freight
leaves the hub UNSORTED and sizing the floor against it would charge for work never done.

THE GAP THIS LEAVES, and it is real. Only the three HUBS own a SORT_MANUAL work centre, but
the hubs run just 56% of round-1 sort — SWP, MNP and BAY run the rest. And the evidence
points the other way still: the residual is thickest at DEPOTS (Sunshine West Parcel
Delivery, Oakleigh South, Darebin), and eight of the eleven delivering depots carry no
ZPT_MACHINE_SORT precisely because they sort by hand. So this books the right VOLUME of
hand labour in buildings that are probably not the ones doing it. Granting SORT_MANUAL to
the depot sort sites is the fix; that is a topology change and is NOT made here.
```
