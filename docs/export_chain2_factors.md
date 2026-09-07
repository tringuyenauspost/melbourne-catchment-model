# export_chain2_factors.py — the notes

The long notes from `export_chain2_factors.py`, lifted out so the module reads as code. The prose is the original, verbatim — nothing was rewritten or summarised. Each heading is the anchor the code points at.

## Contents

- [`export_chain2_factors.py` — module overview](#module-overview)
- [`lodgement_geography()`](#lodgement-geography)
- [`consignment_identity()`](#consignment-identity)
- [`path_chain()`](#path-chain)
- [`source()`](#source)
- [`sortation()`](#sortation)
- [`delivery()`](#delivery)
- [`compute_kept_on_site()`](#compute-kept-on-site)
- [`print_kept_duration()`](#print-kept-duration)
- [`print_state_summary()`](#print-state-summary)
- [`cohort()`](#cohort)
- [`filter_factors()`](#filter-factors)
- [`lane_ledger()`](#lane-ledger)
- [`filter_stages()`](#filter-stages)
- [PART 1 — THE REDUCTION. 2.96 M scan events down to one row per…](#part-1-the-reduction-2-96)
- [the facility dictionary, identical to…](#the-facility-dictionary-identical-to)
- [Deliberately NOT aliases, and each one was checked](#deliberately-not-aliases-and-each-one)
- [THE SOURCE TAG GRAMMAR (Change 37)](#the-source-tag-grammar-change-37)
- [A DEPOT WHOSE ROUNDS ARE RUN FROM ANOTHER DEPOT'S ADDRESS](#a-depot-whose-rounds-are-run)
- [WHERE IN VICTORIA WAS IT LODGED — METRO OR REGIONAL](#where-in-victoria-was-it-lodged)
- [WHY REGION IS AN UPPER BOUND](#why-region-is-an-upper-bound)
- [WHICH DEPOT DELIVERED IT — THE PLAN, OR THE VAN?](#which-depot-delivered-it-the-plan)
- [CHANGE 39 — DROP THE PARCELS NO DEPOT SCAN CAN PLACE](#change-39-drop-the-parcels-no)
- [THE REDUCTION — 2.96 M scan events down to one row per parcel](#the-reduction-2-96-m-scan)
- [THREE EVIDENCE BARS, NOT ONE](#three-evidence-bars-not-one)
- [WHICH OF THOSE EVENTS MAY ANSWER "WHERE DID IT COME FROM"](#which-of-those-events-may-answer)
- [STEP 0d — THE ITINERARY. Which buildings the parcel was IN, in…](#step-0d-the-itinerary-which-buildings)
- [WHY THE MODEL READS A PATH AND NOT A ROLE](#why-the-model-reads-a-path)
- [ONE BUILDING, SEVERAL NAMES](#one-building-several-names)
- [WHICH EVENTS MAY NAME A BUILDING ON THE PATH](#which-events-may-name-a-building)
- [THE MODEL HAS TWO SORT ROUNDS, SO THE PATH IS CAPPED AT TWO](#the-model-has-two-sort-rounds)
- [The events that mean "this depot took the parcel in". The third…](#the-events-that-mean-this-depot)
- [AND THE DEPOT MUST HAVE WORKED IT BEFORE THE DELIVERY DATE](#and-the-depot-must-have-worked)
- [CHANGE 41 — DROP THE VOLUME NO SCAN CAN PLACE AT A SORT SITE](#change-41-drop-the-volume-no)
- [CHANGE 39 : REVERSED. The assumed rung is now KEPT](#change-39-reversed-the-assumed-rung)
- [Change 30, and it survives the dial collapse unchanged in…](#change-30-and-it-survives-the)
- [WHERE A FAMILY GOES WHEN IT LOSES EVERY CELL IN A ROW](#where-a-family-goes-when-it)
- [STAGES 2 AND 3 ARE ONE TABLE NOW](#stages-2-and-3-are-one)
- [Stage 4 despatch runs are the same kind of movement as stages 2…](#stage-4-despatch-runs-are-the)

---

## `export_chain2_factors.py` — module overview
<a id="module-overview"></a>

**Export the measured model factors from the scan-path reduction to CSV.**

```text
The single source of the OBSERVED side of the model. Run after any re-extract, and after
changing the filter dials in inputs/factors_assumed/dials.csv:

    uv run python model_input_preparation/export_chain2_factors.py            # uses the cached reduction
    uv run python model_input_preparation/export_chain2_factors.py --rebuild  # re-reads the raw scan CSV first

Writes to inputs/factors_observed/ :

    obs_joint.csv        pud x class x tag share of peak-day deliveries — FILTERED and
                         renormalised (see below); tag = STG | INT_<site> | VIC_<site>
    obs_demand.csv       pud x class MEASURED delivery volume — the model's demand magnitude
    obs_legs.csv         THE LANE MATRIX: 1st building -> 2nd building, dest ONCE = the parcel
                         was in one of our buildings and no more. One row per (family, class,
                         from, to)
    obs_delivery.csv     last building -> delivering depot (verification only)
    obs_single_sort.csv  the dest == ONCE slice of obs_legs, kept under its old name
    obs_round2_sites.csv the sites ALLOWED to be a second building — observed share >= the
                         ROUND2_MIN_SHARE dial
    _provenance.csv      source, cohort, the thresholds applied, and how much volume they moved

THE PATH, NOT THE PROCESS (Change 44, user, 2026-08-28) ──────────────────────────────
`PATH_BASIS` decides what a parcel's journey IS, and the default changed:

  facility_path   the buildings the parcel was IN, in order, on the PATH_TOUCH_BAR evidence bar,
                  cut at its final arrival at its own depot and capped at PATH_DEPTH buildings.
                  This is `sankey_facility_path.py`'s itinerary, and that page and this file now
                  share one implementation (step 0d) rather than two.
  machine_sort    the role basis this file carried until Change 43: first machine sort, first
                  HANDLED site, second machine sort. Restores obs_recv_entry.csv and
                  obs_round2.csv in place of obs_legs.csv, and nothing else moves.

The visible consequence is that STAGES 2 AND 3 BECAME ONE TABLE. A cross-dock leg and a round-2
leg are one truck between two buildings; the role basis asked about it under two names, and
Change 42 then had to pool the two answers back onto the physical (from, to) pair before the fold
would behave. obs_legs is that pooling as the measurement rather than as a repair. See the note
above `PATH_BASIS` for what the cap costs and the note above `lane_nodes` for why a depot may now
be a lane endpoint.

The obs_* stage matrices ARE the Sankey's link stages, on the same cohort, so a number read off
the diagram is the number the model carries.

SOURCE BANDS (Change 36) — `source_band` is INT | METRO | KEPT_METRO | REGION. Interstate is one
band whether or not it slept at the depot; Victorian freight is split METRO/REGION by testing the
LODGEMENT POINT against the dissolved first-mile catchment, and only the metro side carries a
kept-at-depot band. See VIC_UNPLACED_BAND for what an unplaceable lodgement is taken to mean.
This is the MEASUREMENT's banding and the Sankey's source column. It is deliberately NOT
obs_joint's tag set: obs_joint still carries STG | INT_<site> | VIC_<site>, because those are
product families the notebook chain builds lanes from, and renaming them is a model change rather
than a measurement one.

FILTERS — applied HERE, not in the notebook, so the notebook consumes whatever it gets:
  1. FOLD_MIN_ARTICLES   ONE number, in articles, and the only size question this file asks.
                         Anything carrying fewer than this folds into what survives: a product
                         flavour (family x class x site), a joint cell, and a lane in any of the
                         three stage matrices. Change 37 collapsed OBS_MIN_CLASS_SITE (750 EA),
                         OBS_CELL_FLOOR (5% of a row) and OBS_LANE_FLOOR (0.5% of a column, with
                         OBS_LANE_BASIS deciding of WHAT) into it — three thresholds in two units
                         whose combined effect could only be discovered by rebuilding.
                         Change 42: for a STAGE LANE the number is asked of the physical movement
                         and not of one label on it. `lane_ledger` pools every stage and every
                         family/class onto the (from, to) pair, and a lane lives or dies on that
                         total; the cells then keep their own labels. Cell-by-cell testing was
                         booking 60% of the measured second sorts as single sorts — eight
                         flavours came out of the filter at "100% sorted once" that the scans
                         measure at 50-90%.
  2. ROUND2_MIN_SHARE    a site must carry this share of observed second sorts to be offered
                         as a round-2 destination at all. A different question — WHICH SITES MAY
                         SORT TWICE, not how big a lane must be — so it keeps its own dial.
METRO_DEPOT cells are exempt from the fold — the stage diagonal is pinned and cheap.

The chain-2 notebook re-derives all of this (same functions, same dials) against the scan cache
and asserts the CSVs still match, so a stale export fails the model build.
```

---

## `lodgement_geography()`
<a id="lodgement-geography"></a>

**Put every lodgement facility on the map, then ask the boundary which side it is on.**

```text
    Three layers, in order, most authoritative first. Only the FIRST that answers is used, and
    which one answered is kept in `lodge_geo_from` so the coverage can never hide inside the
    banding:

      node    the model's own node table — the parcel facilities, the letter centre, Avalon and
              the eleven depots, reached through LODGE_PUD/SORT_SITE so all three of a depot's
              names land on one point. 84% of Victorian volume, because bulk lodgement happens
              at the hub.
      stop    the 10,606 first-mile pickup points, matched on the facility name. This is the
              retail network the vans actually call at.
      suburb  the suburb read off a route stop's ADDRESS, matched against the suburb token in
              the facility name — `KERRIMUIR LPO` is placed at the median of the route stops in
              Kerrimuir. The weakest layer and the last one asked.

    Every layer is metro-only by construction — they are built from a Melbourne route file and a
    Melbourne node table — so a positive answer is a real point-in-polygon test but an ABSENCE is
    not evidence of anything. See VIC_UNPLACED_BAND for what absence is taken to mean.

    Returns lodge_lon / lodge_lat / lodge_geo_from / lodge_metro, indexed like the input.
    `lodge_metro` is NA where nothing could be placed — a third state, kept distinct from False.
    
```

---

## `consignment_identity()`
<a id="consignment-identity"></a>

**STEP 0b — what the parcel IS, and which depot delivered it. One value per consignment.**

```text
    The delivering depot is computed BOTH ways and `PDC_BASIS` picks which one `pdc` is:

      pdc_term     Terminating_facility_name, the destination the network planned. Constant
                   across the consignment's scans (asserted) and never missing.
      pdc_deliver  the facility on the LAST ZPT_DELIVER scan, mapped through LODGE_PUD so a
                   depot's delivery arm and sorting hall fold onto the depot. Missing wherever
                   the parcel went out from a post office, a locker or an unmodelled site.
      pdc_accept   the LAST ZPT_ACCEPT naming one of the depots, same mapping — which of our
                   depots last took custody. Change 38's middle rung, used only where the
                   delivery scan names no depot. NOT ZPT_ACCEPT_FACILITY — see PDC_BASIS.

    `pdc` is a depot for every row that survives; `build_paths` drops the rest and says how much
    that cost. See the note beside PDC_BASIS for the full measurement.
    
```

---

## `path_chain()`
<a id="path-chain"></a>

**One parcel's itinerary: the VICTORIAN buildings it was in before it reached its depot.**

```text
    `raw` is the chain on the chosen evidence bar, under the scans' own names; `own` is the set of
    canonical names that ARE the delivering depot; `group`, when given, is the set of buildings
    that may appear (a building outside it is not a stop, it is a gap the journey continues past).

    Returns the drawn path and, beside it, everything the cuts removed — so a shorter journey can
    never look like a simpler one:

      before   buildings ahead of it in another state (the interstate leg)
      after    buildings it was seen in AFTER it reached its depot — the last-mile end of the
               journey rather than a step towards it, and 26.8% of articles carry one
      merged    touches the NAME FOLD removed: one building scanned under two names
      fell      buildings dropped by the group test
      revisit   a repeat re-opened by the cuts. The collapse above runs on the WHOLE chain, so a
                parcel that went MPF -> somewhere we dropped -> MPF comes out of the cuts as
                MPF, MPF and would draw a lane from a building to itself. That is a hole where
                the middle used to be, not a journey, so it is collapsed again — after every cut,
                so nothing can re-open it.
      why       0 if there is a path, else which of the four empty populations it is
    
```

---

## `source()`
<a id="source"></a>

**STEP 1 — SOURCE. Where was this parcel lodged?  VIC / INTERSTATE / UNKNOWN.**

```text
    Rule 1, for 94% of articles: the STATE on the lodgement scan. Not the facility — the state.

    Rule 2, the fallback: 5.8% of articles have no lodgement scan, mostly interstate freight that
    entered on a manifest. Rather than drop them, read the state off the first scan that could
    plausibly have happened at the lodgement end — ORIGIN_FALLBACK_EVENTS, four of the eight
    PHYSICAL types. LODGE is not among them, on purpose: it cannot fire in production (the
    fallback only runs when there is no lodge scan) but leaving it in makes the calibration
    dishonest, because the rule would be scored on parcels where it is allowed to read the very
    answer it is predicting. That inflated an earlier version of this number from 98.2% to 99.9%.

    Scored against the 153,608 consignments where the lodge scan IS present, with the lodge scans
    removed BEFORE the consecutive-repeat collapse so the rule sees what a real gap parcel
    presents: it has evidence for 99.12% of those articles and, where it answers, scores 98.21%
    accuracy by article, 99.78% precision, 95.82% recall — 137 articles called interstate that
    were not, and 2,659 true interstate missed because they first surface at a Melbourne site.
    Counting every abstention as a miss, 97.34%.

    Change 35 narrowed the candidate set from all seven non-lodge PHYSICAL types to these four.
    On the day that moves 638 articles (0.38%): 594 that a Melbourne DELIVER scan had called VIC
    become UNKNOWN and leave the cohort, and 44 flip, 43 of them INTERSTATE -> VIC where a
    DEPART_CONTAINER naming another state used to outrank the local sort that follows it. It buys
    a fractionally better answer where it answers, and honesty about the rest — see the note
    beside ORIGIN_FALLBACK_EVENTS for the per-event scores that chose the four.

    Three alternatives were tested and rejected. LOAD_ITEM, the ops brief's own origin event,
    exists for only 34% of the gap and is right 95.5% of the time against this rule's 99.0% on
    the same subset; where the two disagree this rule is correct on 98.1% of articles and
    LOAD_ITEM on 1.5%, because a LOAD_ITEM is usually a linehaul load rather than the pickup (it
    sits at the lodgement facility only 48.8% of the time). "First facility is the gateway"
    misfires on 3,276 locally-lodged articles, and "has a customs scan" on 2,166.

    `origin_from` records which rule fired, so the fallback can never hide inside the answer.

    Pass `articles` (step 0b's series) to weight the printed breakdown by article rather than by
    consignment; the returned columns are the same either way.
    
```

---

## `sortation()`
<a id="sortation"></a>

**STEP 2 — SORT. What did Melbourne do to this parcel, and where?**

```text
    Three questions, in order:

    2a  WHICH BUILDINGS SORTED IT, in sequence. Machine sorts only, at the eight sites the
        dictionary knows, with consecutive repeats collapsed — a recirculation on the same
        machine is not a second round. A parcel with no sort at those eight has not necessarily
        gone unsorted: `sorted_elsewhere` marks the half of that residual that was machine-sorted
        somewhere else, overwhelmingly interstate. The two cases answer different questions, so
        they are tracked apart rather than pooled into one "none".

    2b  WHICH BUILDING RECEIVED IT — the first Melbourne site to physically HANDLE it, which is
        not restricted to sorts: a building that unloads a container and sends it on has handled
        the freight without sorting it. Comparing 2b against 2a is what makes a cross-dock
        visible. A building only counts as a separate receipt if it worked the freight STRICTLY
        EARLIER than the sort; otherwise receipt and sort sit flat against each other.

        This step reads HANDLED and nothing wider, and ZPT_ACCEPT_FACILITY is the event that
        decision is really about. `received` is not a description here — it is a CLAIM that the
        parcel physically passed through building A and was sorted in building B, and the model
        turns that claim into a truck leg with a dock touch at each end. An acceptance scan does
        not support it: 12,351 consignments are accepted at one site before being first sorted at
        another with no unload, depart, load or transfer anywhere at the accepting site. Counting
        those would take the cross-dock from 10.4% of the day to 18.6%. See the note beside
        HANDLED for the full measurement.

    2c  WAS A HUB INVOLVED FIRST — `hub_first`, the hub-handled-then-depot-sorted pattern.

    Pass `articles` (step 0b's series) to weight the printed breakdowns by article rather than by
    consignment; the returned columns are the same either way.
    
```

---

## `delivery()`
<a id="delivery"></a>

**STEP 3 — DELIVERY. Which depot delivered it, and had it slept there first?**

```text
    The depot itself is `identity.pdc` from step 0b. What is left is the third source band: a
    parcel that reached its delivering depot on an EARLIER DATE was not sorted for today's round,
    it was already standing in the building. Detail in `compute_kept_on_site`.

    `same_depot_end_to_end` is a different question, kept alongside because the sourcing rule
    turns on it: lodged in the same depot's catchment as it was delivered from. It needs step 1's
    origin, which is this step's only dependency on another.

    `source_band` is the answer to the question the model actually asks — where did a day's
    delivery volume come from? — and it is the one column that combines steps 1 and 3. Change 36
    made it FOUR bands, and the asymmetry between them is deliberate:

        INT          everything lodged outside Victoria, whether it slept at the depot or not.
                     One band. Interstate freight reaches Melbourne one way — a linehaul into a
                     gateway — and whether it then waited a night is a fact about the depot's
                     timetable, not about where the volume came from. Splitting it bought a
                     distinction the model has no lane to carry.
        KEPT_METRO   lodged inside the first-mile catchment, and already standing in the
                     delivering depot before its delivery date.
        METRO        lodged inside the catchment, delivered the same day.
        REGION       lodged in Victoria but not inside the catchment. One band, for the same
                     reason INT is: it arrives by line-haul from a country depot, and the stage
                     slice of it is too thin to steer anything.

    So the overnight stage appears as a source band ONLY for metro-lodged freight. `kept_on_site`
    is untouched and still marks EVERY parcel that slept at its depot — the two stopped being the
    same question here. (`stage_origin` was the function that read the whole pile by
lodgement; it and obs_stage_origin.csv were deleted 2026-08-28 as unread.)
    
```

---

## `compute_kept_on_site()`
<a id="compute-kept-on-site"></a>

**Ops' definition: of the parcels delivered on a day, which SAT at the delivering depot?**

```text
    TWO conditions since Change 34, and both are dated against the delivery date:
      1. the parcel was **standing in the facility that delivered it** before the delivery date;
      2. that facility **sorted it** before the delivery date as well — see DEPOT_SORT_EVENTS
         for what counts as a sort and for the depots where nothing can.
    Origin is irrelevant — interstate and Victorian volume qualify the same way. A depot appears
    under several names (sorting hall, delivery arm), so both sides are mapped to a depot rather
    than string-matched, and "the facility that delivered it" is `PDC_BASIS`.

    THE FINAL STAY, not the first touch (revised 2026-08-10). The obvious implementation asks
    whether the parcel was EVER at its delivering depot before the delivery date. That is too
    loose, and expensively so: 43% of what it counted was freight that touched its own depot,
    LEFT for a hub or interstate, and came back on delivery day — 18,435 EA, over half of it via
    Melbourne Parcel. Those parcels were moving, not standing. So the clock starts at the last
    moment the parcel was anywhere ELSE: the stay that ends in delivery is the only one that can
    be an overnight hold. It cut the measured stage from 25.6% to 14.6% of the day.

    Two further things this deliberately does NOT do, both of which inflated an earlier version:
      * it does not count *any* event at the depot — a LODGE scan there is a customer dropping
        freight off, not the depot holding stock (worth ~2.8 points);
      * dates, never clock times. Event timestamps are database write times, so an
        hours-elapsed threshold would be measuring the wrong thing; a date boundary survives it.
    
```

---

## `print_kept_duration()`
<a id="print-kept-duration"></a>

**How long the stage actually stood there, split by where it was lodged.**

```text
    The kept BAND is decided by the calendar date and nothing else — see `compute_kept_on_site`
    for why an elapsed-hours threshold would be measuring the database rather than the depot.
    These durations are therefore a DESCRIPTION of the band, not its definition, and they are read
    off the same two timestamps (last arrival scan, delivery scan) that could each be written late.
    Quote the shape of the distribution, not any single parcel's hours.

    Split by lodgement because the parts of the stage are staged for different reasons: an
    interstate parcel is waiting for the next morning's round because the linehaul landed after
    cut-off, a metro one because it was collected during the day it was meant to go out, a
    regional one because a country line-haul runs to its own timetable. The split is `lodge_band`
    rather than `source_band` — the stage as a SOURCE band is metro-only now, but the pile
    standing in the building is not, and this describes the pile.
    
```

---

## `print_state_summary()`
<a id="print-state-summary"></a>

**The day, by the STATE it was lodged in — one row per state, then the two model bands.**

```text
    `origin` only ever says VIC or INTERSTATE because that is all the model can carry, but the
    scans know the actual state and the states do not behave alike: NSW is a quarter of the day
    and cross-docks at 24%, Queensland and WA arrive pre-sorted and are barely touched, Victoria
    is sorted half again as often as anything from outside. Anyone asked "where does the freight
    come from" wants this table, not the two-way split.

    `state` is the lodgement state from step 1 — the LODGE scan where there is one, the first
    physical scan otherwise — so it is a fact about the parcel's origin, never about its band.
    
```

---

## `cohort()`
<a id="cohort"></a>

**The measurement cohort, with every Sankey stage column mapped onto the model's six sites.**

```text
    `OBS_COHORT` picks which parcels are measured:

      all_dates  every parcel in the extract (167,443). Ops' position is that these were all
                 delivered on 20 May and that the spread of Event_date across the following
                 weeks is when the records reached the database, not when the parcel was
                 delivered. On that reading the whole file IS the day, and it lines up with the
                 zone table's 167,431 almost exactly.
      peak_day   only parcels whose delivery scan is dated 20 May (148,716).

    The choice moves one number a long way. `kept_on_site` means "reached the delivering depot
    BEFORE the delivery date" — so if a record's delivery date is later than the parcel's real
    delivery, the test passes for free. The later-dated parcels read 85% kept against 18% on
    20 May, which is the signature of exactly that. Under all_dates the overnight stage share
    is therefore 25.6% rather than 18.1%, and it carries that caveat with it.

    The diagram can draw NONE / ELSE / DLC / AVL as their own nodes; the model has no facility
    for them, so each stage column is folded onto the six arrival sites here, once, and every
    factor below reads the same columns. `site` is the Sankey's `entry`, `recv_site` its `recv`,
    `dest2` its `mid`, `exit_site` its `exit`.
    
```

---

## `filter_factors()`
<a id="filter-factors"></a>

**Fold everything smaller than FOLD_MIN_ARTICLES, renormalise, and report what moved.**

```text
    Shared with the notebook's drift guard, so exporter and model can never disagree.

    Change 37 collapsed three dials into this one number. OBS_MIN_CLASS_SITE asked "is this
    flavour big enough" in articles, OBS_CELL_FLOOR asked "is this cell big enough" as a share of
    its row, and OBS_LANE_FLOOR asked the same of a stage lane as a share of a column — three
    thresholds in two different units, and the only way to know what any of them cost was to move
    one and rebuild. Now there is one question, asked in the unit the model is denominated in:
    does this thing carry FOLD_MIN_ARTICLES? If not, it folds into what survives.

    The fold is PROPORTIONAL within the origin family, not winner-takes-all. Dropping a thin site
    is a statement about where freight was sorted, never about where it came from, so a family's
    total is preserved and its surviving sites share the folded volume in proportion to their own
    — which is "the small lane folds into the larger lanes", weighted by how much larger.
    
```

---

## `lane_ledger()`
<a id="lane-ledger"></a>

**(family, from, to) -> the articles OF THAT ORIGIN measured moving between two buildings.**

```text
    Change 42. The fold used to ask its question of a CELL — one origin family, one class, one
    stage — and a physical movement is none of those things. A truck running MPF -> TPF carries
    interstate freight being cross-docked (stage 2) and Victorian freight going for its second
    sort (stage 3), in two classes each: six cells, six separate 1,300 EA tests, and the lane
    survived only if one cell passed on its own. At stage 3 that booked 60% of the measured
    second sorts as single sorts, because the volume was real and the labels were thin.

    So the fold is asked ONCE PER LANE now, of the pooled total. The dial's meaning is unchanged
    and it is still one number — it is the DENOMINATOR that was wrong. What a lane test says in
    words: a movement between two buildings is worth modelling if it clears 1,300 articles a day;
    and once the truck is running, everything measured on it rides it, because rerouting 200
    express parcels around a lane that already exists invents a routing to save a row.

    THE POOL STOPS AT THE ORIGIN FAMILY. The first cut of this pooled every label, and that let
    one origin decide another's fate: 19 of the 37 surviving round-2 cells lived only because a
    DIFFERENT family's freight on the same OD cleared the threshold — REGION/PP MPF -> MNP carries
    138 articles of its own and was kept alive by METRO's 5,158. That is the same error
    `filter_factors` guards against at the joint, one step downstream: a fold may say where
    freight was SORTED, never where it came from, and a lane a family cannot fill on its own is
    not a lane that family runs. So the ledger is keyed by family, and pooling now reaches across
    CLASS (EP beside PP, which is one truck and one origin) and across the two site-to-site stages
    for interstate freight (a cross-dock leg and a round-2 leg are the same movement), and no
    further.

    Pooling decides EXISTENCE only. A surviving lane keeps every cell under its own family and
    class — merging REGION into METRO because they share a truck would invent an ORIGIN, which
    `filter_factors` explains at length is the expensive error and the one obs_joint exists to
    get right.

    Change 44 left this function alone and took away half its work. On the facility-path basis
    `recv_entry` arrives empty and every physical movement is already one row of `legs`, so the
    pooling across the two site-to-site stages that the paragraph above argues for has nothing
    left to pool: the measurement stopped splitting the truck in the first place. The ledger
    still earns its keep across CLASS, and the code is unchanged so that flipping PATH_BASIS back
    restores the old behaviour exactly.
    
```

---

## `filter_stages()`
<a id="filter-stages"></a>

**Fold every lane under FOLD_MIN_ARTICLES into a larger one, per stage.**

```text
    Change 37 retired OBS_LANE_FLOOR and OBS_LANE_BASIS together. The floor was a SHARE, and a
    share of what was a second dial: of the whole stage column, or of what leaves the source
    node. The two answered differently enough to change the model's shape — 4 cross-dock lanes
    against 20 — which meant the pair had to be reasoned about together every time either moved.
    An articles test needs no basis: 1,300 parcels is 1,300 parcels wherever it sits, and it is
    the same number the joint cells are folded on, so "small" means one thing in this file now.

    Change 42 kept the number and fixed what it is asked OF: `lane_ledger` pools every stage and
    every label onto the physical (from, to) movement, and the fold tests THAT. Read that
    function for why. The three stages then fold their losers exactly as they always did — the
    test changed, the destination of folded volume did not:

    Stage 2  a neglected cross-dock lane folds onto the DIAGONAL: the parcel is modelled as
             sorted at the site that handled it, which is the no-cross-dock case.
    Stage 3  destinations outside `r2_allowed` fold first, then the lane test — and what it
             drops folds INTO ONCE. A site sending 12 EA to a building nothing else goes to is
             not running a round-2 lane there; it is sorting that freight once. What the cell
             test got wrong was the other case: six thin labels sharing one fat truck, all six
             folded to ONCE, and 60% of the measured second sorts modelled as single sorts.
             Pooling recovers 11,457 EA of that, so the single-sort share moves by MORE than
             SORT_BAND here — the band is set around the exported share, so it follows, but a
             rebuild will not be byte-identical and is not meant to be.
    Stage 4  plain prune; this matrix only ever verifies, so nothing downstream shifts.

    Change 44: on the facility-path basis stage 2 is empty and the stage-3 loop is doing the
    whole job under a new meaning — a dropped LEG folds into ONE BUILDING, which is the same
    sentence as "sorted once where it landed" now that a building is a stop rather than a sort.
    
```

---

## PART 1 — THE REDUCTION. 2.96 M scan events down to one row per…
<a id="part-1-the-reduction-2-96"></a>

```text
══════════════════════════════════════════════════════════════════════════════════════

 Moved here from sankey_from_scans.py (2026-08-10): drawing a picture and deciding what the
 data MEANS are two jobs, and only one of them feeds the model. sankey_from_scans.py now
 imports what it needs from here and contains nothing but the diagram.

 Part 2, below, turns these per-parcel rows into the measured model factors.
```

---

## the facility dictionary, identical to…
<a id="the-facility-dictionary-identical-to"></a>

```text
One BUILDING can appear under several names, and a name we do not map is silently read as "not
one of our sites" — the sort disappears into `sorted_elsewhere` and the receipt disappears from
the cross-dock. Every Victorian facility that runs a machine sort or a HANDLED event has been
swept against this dictionary; the aliases below are the ones that turned out to be the same
building under a second name. The rest of the unmapped Victorian names are genuinely other
places (post offices, parcel lockers, business centres, small delivery centres, the Whittlesea
and Golden Square PDCs) or carry no physical event at all.
```

---

## Deliberately NOT aliases, and each one was checked
<a id="deliberately-not-aliases-and-each-one"></a>

```text
  STARTRACK MELBOURNE DFPC   589 sorts, but StarTrack is a separate network, not one of the
                             eight. It stays in `sorted_elsewhere`, where it is the largest
                             Victorian entry.
  MPF BULK PARCELS           700 lodgements and 11 handled events — a bulk induction point, not
                             a receiving dock. Counting it would turn lodgements into receipts.
  *_DWS, *_BUSN HUB DWS      155,600 scans and not one physical event between them: every row is
                             an ADMIN-ER39/40/12 code. Mapping them would change nothing, which
                             is the same reason `away` is restricted to PHYSICAL events.
  WHITTLESEA / GOLDEN SQUARE PDC, and the *_DC delivery centres — real, different buildings.
```

---

## THE SOURCE TAG GRAMMAR (Change 37)
<a id="the-source-tag-grammar-change-37"></a>

```text
A joint tag is either the stage family on its own or FAMILY_<site>. Three of the four families
name a lodgement place and carry the site the freight entered Melbourne at; the fourth is
yesterday's stock, whose "site" is the depot it is already standing in — which is the row, not
the tag, exactly as STG worked before it.

The parsing is a FUNCTION and not `tag.split("_")` because METRO_DEPOT contains the separator:
splitting it naively yields family "METRO", which silently merges the overnight stage into the
same-day metro family and balances anyway. That is the kind of bug that survives every assert.
```

---

## A DEPOT WHOSE ROUNDS ARE RUN FROM ANOTHER DEPOT'S ADDRESS
<a id="a-depot-whose-rounds-are-run"></a>

```text
Mulgrave's delivery contractor is REGISTERED AT OAKLEIGH SOUTH (ops, 2026-08-18), and the scans
carry the consequence end to end rather than only on the delivery event:

  * MULGRAVE PDC is never the facility on a ZPT_DELIVER scan — not once in 2.96 M rows;
  * of the 5,169 EA that terminate at Mulgrave, 100% are physically at OAKLEIGH SOUTH PDC at
    some point and only 37.5% are ever at MULGRAVE PDC (which is a real building — 2,057
    acceptances and 1,985 unloads — just not one that closes a round);
  * and for 5,168 of those 5,169 EA, Oakleigh South is the LAST of the two buildings the parcel
    is in. Even freight that visits Mulgrave comes back to Oakleigh South before it goes out.

So an Oakleigh South scan on a Mulgrave-bound parcel is not evidence that the parcel changed
depot; it is the same operation under the address it is registered at. This dictionary says so
once, and TWO rules read it:

  1. the delivering depot — the scan may not move volume from a depot to its contractor's base
     (`consignment_identity`), so Mulgrave keeps its 5,169 EA;
  2. the overnight stage — the contractor's building counts as the depot's OWN building
     (`compute_kept_on_site`), because otherwise every Mulgrave parcel reads as having "left"
     for Oakleigh South and the final-stay rule can never find a stay. Without this the fix in
     (1) would hand Mulgrave its volume back and pin its stage at zero by construction.

It is deliberately a PAIR, not a blanket exemption: only the Mulgrave->Oakleigh South pairing is
overridden, so a Mulgrave parcel genuinely delivered by some third depot still moves.
```

---

## WHERE IN VICTORIA WAS IT LODGED — METRO OR REGIONAL
<a id="where-in-victoria-was-it-lodged"></a>

```text
Change 36: the Victorian band splits in two, and the split is GEOMETRIC rather than typed. The
question is asked in the order the parcel answers it — interstate FIRST, off the state on the
lodgement scan (step 1's `origin`, unchanged), and only then, for Victorian freight, is the
lodgement POINT tested against the first-mile catchment: inside is METRO, anything else REGION.

The boundary is the 411 postcode polygons of the first-mile manifest catchment dissolved into
one footprint with its interior rings filled, so a lodgement in an enclave — a postcode no van
route happens to touch — does not read as regional for want of a hole.
```

---

## WHY REGION IS AN UPPER BOUND
<a id="why-region-is-an-upper-bound"></a>

```text
The extract carries no coordinates, only a facility NAME, so the point has to be recovered
(`lodgement_geography`) from three layers in order. The banding rule is then LITERAL: a
lodgement that cannot be placed is not inside the boundary, so it bands REGION. That is honest
about direction — region is an upper bound and metro a lower one — but it is not free, so the
three reasons a parcel lands in REGION are printed apart on every run and never pooled:

    placed outside   a real point-in-polygon answer — Avalon, Geelong, Bendigo, Seymour, …
    unplaceable      a name no layer knows. Genuine country towns sit here alongside CBD post
                     shops and shopping-centre StarTrack counters, which are plainly metro.
                     This is the half of the assumption that hurts.
    no lodge scan    origin came from step 1's RULE 2, which reads a STATE off a later scan and
                     never a place, so there is no point to test at all.

VIC_UNPLACED_BAND = "METRO" sends the last two the other way and makes REGION a lower bound
instead. Nothing else in the file changes.
```

---

## WHICH DEPOT DELIVERED IT — THE PLAN, OR THE VAN?
<a id="which-depot-delivered-it-the-plan"></a>

```text
Two columns in the extract answer that question and they disagree about one article in eight:

  Terminating_facility_name   the destination the network PLANNED. One value per consignment,
                              present on every parcel, always one of the 11 modelled depots.
  the last ZPT_DELIVER scan   the building whose round ACTUALLY put the parcel out. Present on
                              99.6% of parcels, but the facility named is often a post office,
                              a parcel locker or a small delivery centre — not a depot.

Change 34 (2026-08-18) tried the DELIVER scan as the basis and REVERSED IT the same day. The
argument for it was that the model sizes depot work, so a parcel handed to an LPO for
collection did not consume a depot's delivery round. The argument against won: the price is a
tenth of the day, and the volume it removes is not noise — it is real freight that a depot
really did carry up to the last leg. Measured on this extract, of 167,445 EA:

  147,235 EA (87.93%) carry a DELIVER scan at one of the 11 depots and are kept
     753 EA ( 0.45%) carry no DELIVER scan anywhere in the file
   19,457 EA (11.62%) were delivered from a site that is not a modelled depot — 938 distinct
                      names, and the shape of them is the whole story: 11,712 EA from LPOs,
                      3,042 from post shops, 1,170 from parcel lockers, 1,161 from delivery
                      centres, 402 from business centres, 194 from PDCs outside the eleven
                      (Whittlesea, Golden Square). This is retail collection, not depot
                      delivery, and on the planned basis it was being charged to a depot.

One depot disappears entirely. MULGRAVE PDC never appears as a delivering facility — not once
in 2.96 M scans. Of the 5,169 EA that TERMINATE at Mulgrave, 4,499 are put out by OAKLEIGH
SOUTH PDC and the rest by post offices. On the deliver basis Mulgrave is a destination that no
longer runs a round, and its volume moves to Oakleigh South (+1,152 EA net). Everything else
agrees on 99.1-99.96% of its volume, so this switch is one depot plus the retail tail.

THE SHIPPED ANSWER IS THE HYBRID: take the delivery scan when it names one of our depots, and
fall back to the plan when it does not. That keeps every article — nothing is dropped, no depot
empties — while letting the scan overrule the plan on the only question it answers better,
which is WHICH DEPOT ACTUALLY RAN THE ROUND. What it moves is one arrangement plus noise:
5,042 EA (3.01%) change depot, and 4,499 of those are Mulgrave's rounds being run by Oakleigh
South. Mulgrave keeps only the 670 EA whose delivery scan was off-network, which is honest —
on the evidence it is a destination that no longer runs a round of its own.

The asymmetry is deliberate and worth being able to say out loud: a delivery scan at a depot is
BETTER evidence than the plan (it is what happened, not what was intended), while a delivery
scan at a post office is not evidence about depots at all — it describes the last 100 metres of
a journey whose depot leg already happened. 93.5% of that off-network volume physically passed
through its planned depot first, so falling back to the plan there is not a guess.

CHANGE 38 — ACCEPT_FACILITY GOES IN BETWEEN. The hybrid above falls from the delivery scan
straight to the PLAN, which is an intention. But a parcel handed over at a post office was
ACCEPTED at a depot on its way there, and that acceptance is a record of which building actually
had the freight — still evidence, where the plan is not. So the ladder is now three rungs:

  1. the last ZPT_DELIVER scan naming one of the 11 depots   — who ran the round
  2. the last ZPT_ACCEPT naming one of the 11                — who last had it, if 1 is silent
  3. Terminating_facility_name                                — the plan, if neither speaks

Every rung is mapped through LODGE_PUD, so a rung can only ever answer with one of the eleven
modelled depots; a post office or a locker returns nothing and the ladder simply continues. That
is what keeps the result inside the terminating list by construction rather than by filtering.

ZPT_ACCEPT, NOT ZPT_ACCEPT_FACILITY. They are different events and only one of them answers
this question. ZPT_ACCEPT fires 170,100 times at 224 facilities, 99.3% of them one of our
eleven depots, at a median Event_seq of 15 — it is the DELIVERING DEPOT taking the parcel on
for its round, and its biggest sites are Sunshine West, Oakleigh South, Holloway Dr, Darebin.
ZPT_ACCEPT_FACILITY fires at 763 facilities, only 52.1% of them our depots, at a median seq of
11, and its biggest sites are MELBOURNE PARCEL FACILITY and TULLAMARINE PARCEL FACILITY — it is
a facility-level acceptance that mostly happens at a HUB. Using it here would answer "which hub
received it" for a question about which depot delivered it.

ONE CAVEAT, and it is why ACCEPT sits BELOW the delivery scan rather than above it. This file
already refuses to treat ZPT_ACCEPT_FACILITY as proof of physical presence when deciding a
CROSS-DOCK (see the note beside HANDLED: counting it there took cross-dock from 10.4% to 18.6%
on parcels with no unload, depart, load or transfer at the accepting site). The question here is
weaker and an acceptance can answer it: not "did this building work the freight" but "which of
our depots last had custody of it". It is used only where the stronger evidence is absent, and
`pdc_from` records which rung answered so the two can always be separated again.

The reduction carries ALL THREE columns whichever basis is active (`pdc_term`, `pdc_deliver`,
`pdc_accept`), and `report_pdc_basis` measures them every rebuild, so none of this needs
re-deriving to be re-argued.
```

---

## CHANGE 39 — DROP THE PARCELS NO DEPOT SCAN CAN PLACE
<a id="change-39-drop-the-parcels-no"></a>

```text
A parcel with no ZPT_DELIVER and no ZPT_ACCEPT at any of the eleven depots has nothing behind
its depot but the plan. That is 1,286 EA (0.77%), spread thinly — no depot loses more than
2.86% of its volume — and carrying it means 0.77% of every share in this file rests on an
intention rather than a record.

WHAT THIS DELIBERATELY DOES NOT DROP, and the distinction is the whole point. The plan rung
carries 6,453 EA, and 5,167 of that is MULGRAVE: its parcels DO have both scans, at Oakleigh
South, where its contractor is registered — CONTRACTOR_BASE masks them so the depot keeps its
own volume, and they land on the plan rung by design. Dropping "everything on the plan rung"
would delete Mulgrave entirely, 100% of it, which is not removing bad data but removing a depot
because of where its contractor is registered. So the test is "no depot scan AND no contractor
base to explain it", never "fell through to the plan".

False restores the old behaviour: keep them, sourced from Terminating_facility_name.
```

---

## THE REDUCTION — 2.96 M scan events down to one row per parcel
<a id="the-reduction-2-96-m-scan"></a>

```text
══════════════════════════════════════════════════════════════════════════════════════

 Read it top to bottom. Each step is a function of the scan table that returns the columns
 it owns, indexed by consignment; `build_paths()` at the end is nothing but the assembly.

     step 0   READ        every scan, ordered by Event_seq
     step 0b  IDENTITY    the facts that belong to the consignment, not to any one scan
     step 0c  PRESENCE    every building the parcel touched, in order  (steps 1 and 2 share it)
     step 1   SOURCE      where it came from      -> origin, origin_from, lodge_*
     step 2   SORT        what Melbourne did to it -> chain, first/second/last, received,
                                                      crossdocked
     step 3   DELIVERY    where it ended up       -> pdc (in step 0b), kept_on_site, dates

 Steps 1-3 are independent of each other except for two stated dependencies, both in step 3:
 `same_depot_end_to_end` needs step 1's origin, and `pdc` comes from step 0b.
```

---

## THREE EVIDENCE BARS, NOT ONE
<a id="three-evidence-bars-not-one"></a>

```text
There are three sets below and they are deliberately different, because they answer three
different questions and the cost of being wrong runs in opposite directions:

  HANDLED   "did this BUILDING physically work the parcel?"   -> `received`, the cross-dock
  PHYSICAL  "was the parcel in this building AT ALL?"         -> the journey chain, `away`
  ARRIVAL   "did this DEPOT take the parcel into stock?"      -> `kept_on_site` (see below)

They are not a trust ranking. All three share the same CORE — the five DOCK events, which
cannot be recorded unless the freight was physically in the building: unload, depart container,
load, transfer, machine sort. The sets differ only on the three BOUNDARY events:

             dock x5   ACCEPT   LODGE   DELIVER
  HANDLED       in       out     out      out     = the core, nothing else
  PHYSICAL      in       in      in       in      = the core plus all three
  ARRIVAL       in       in      out      out     = the core plus ACCEPT

The rule that decides each cell is: WHAT DOES A FALSE POSITIVE COST THIS PARTICULAR NUMBER?
A wrong cross-dock invents a truck leg and a dock touch the model then has to build and pay
for, so that number takes the core alone. A wrong node in the journey chain is merely a
descriptive detail, and a MISSED building is what actually breaks it, so that one takes the
widest defensible net. A wrong arrival says stock was standing overnight when it was not.

ZPT_ACCEPT_FACILITY is the hinge: out of HANDLED, in the other two. Measured on this extract
(re-runnable, `event_hitrate.py`):

  * an acceptance at a site that also SORTED the parcel is corroborated by an unload / depart /
    load / transfer in the same building 82.4% of the time (96.4% counting the per-consignment
    first-accept form). An acceptance at a site that did NOT sort it: 55.8%. That gap is the
    whole argument — roughly two in five "accepted here" events leave no other trace, and they
    are custody changing hands, not freight moving through a building.
  * price of getting it wrong: cross-dock is 17,374 EA (10.4% of the day) on HANDLED, 31,116 EA
    (18.6%) if ACCEPT counts, 51,999 EA (31.1%) on all eight PHYSICAL events. 1.8x and 3.0x.
  * but at the parcel's OWN delivering depot the asymmetry reverses: 81.1% are corroborated
    anyway, and 100% of the uncorroborated remainder carry a DELIVER scan — the parcel provably
    left that building on a van, which is downstream proof of presence a hub cross-dock never
    has. Dropping ACCEPT from ARRIVAL would cut the measured stage 37,005 -> 31,903 EA
    (22.1% -> 19.1%) and lose stock we can prove was in the building.

ZPT_LODGE is excluded from HANDLED for a separate reason: a bulk customer lodging at MPF and
having it sorted at Sunshine West is an ordinary first-mile flow, not a cross-dock. Including it
added ~14 points. It stays in PHYSICAL (a lodgement IS a physical handover) and is out of
ARRIVAL (a customer drop-off is not the depot holding inbound stock).
```

---

## WHICH OF THOSE EVENTS MAY ANSWER "WHERE DID IT COME FROM"
<a id="which-of-those-events-may-answer"></a>

```text
Change 35 (2026-08-18). Step 1's fallback used to read the state off the first presence scan of
ANY kind. Presence and origin are different claims: DELIVER, DEPART_CONTAINER and UNLOAD_ITEMS
say where the parcel ended up or was worked, not where it started. DELIVER is the clear case —
it happens in Melbourne by construction, so as a first candidate it called INTERSTATE on 0.47%
of the freight and scored 76.0% against the lodge scan, the worst of the seven.
The four kept are the ones that can sit at the lodgement end, with their accuracy where each is
the first candidate: TRANSFER 99.7%, LOAD_ITEM 98.8%, MACHINE_SORT 98.5%, ACCEPT_FACILITY 65.9%.
ACCEPT_FACILITY is the weak one (on 575 articles) and is kept deliberately: it is the only
origin-end evidence 303 articles of the gap have, and it is the one of the four that can name an
interstate gateway before anything sorts the parcel.
The rule now ABSTAINS rather than guessing from a delivery: 596 articles (0.36%) DELIVER used to
call VIC become origin=UNKNOWN and are dropped by cohort(). Full scoring in source().
```

---

## STEP 0d — THE ITINERARY. Which buildings the parcel was IN, in…
<a id="step-0d-the-itinerary-which-buildings"></a>

```text
══════════════════════════════════════════════════════════════════════════════════════

 Moved here from sankey_facility_path.py (2026-08-28), for the reason sankey_from_scans.py's
 reduction was moved here before it: deciding what a scan MEANS is this file's job and drawing a
 picture of it is not. The diagram imports this machinery now instead of keeping a second copy,
 so the page and the model cannot disagree about which buildings exist, which scans put a parcel
 in one, or where a journey stops.
```

---

## WHY THE MODEL READS A PATH AND NOT A ROLE
<a id="why-the-model-reads-a-path"></a>

```text
 Everything above builds the journey out of ROLES: `first` is the first machine sort, `received`
 is the first site that HANDLED it, `second` is the second machine sort. A building that neither
 sorted nor handled the freight is not on the journey at all, and one truck is asked about twice
 — was this a cross-dock, was this a second sort — under two different names.

 This step asks one question instead: WHICH BUILDINGS, IN WHAT ORDER. A stop is a stop, whatever
 the building did with the parcel. Measured on the 20 May extract, that buys three things:

   * cross-dock and second sort stop being different questions. MPF -> SWP is ONE lane carrying
     10,957 EA; on the role basis that same truck is split across obs_recv_entry (interstate
     cross-dock) and obs_round2 (Victorian second sort) — which is exactly why Change 42 had to
     pool the two back onto the physical (from, to) pair before the fold would behave. The pool
     is now the measurement rather than a repair applied to it.
   * a DEPOT can be an entry. 5,236 EA (3.2%) are first seen at a delivering depot, 2,158 of
     them at Oakleigh South. The role basis has no sort node there, so those parcels fell to the
     assumed rung of `entry()` and were handed to a hub they were never in.
   * the shape barely moves, which is the reassuring part: 59.3% of the day touches one of our
     buildings before its depot and 26.7% touches two, against 62.9% one machine sort and 27.0%
     two. The basis changes WHICH LANES EXIST, not how much sorting the day contains.
```

---

## ONE BUILDING, SEVERAL NAMES
<a id="one-building-several-names"></a>

```text
 The scans name one operation two and three ways, and until the names are folded the path draws
 a step between two names of the same building and charges the parcel a touch for it. Three
 relations say two names are one building, and they overlap (Sunshine West is all three at
 once), so they are UNIONED rather than applied in turn and the busiest name wins:

   <base> PARCEL DELIVERY / <base> PDC   a delivery centre IS a PDC (ops, 2026-08-24), and the
                                         pair sits 0.7 km apart with one name absent from
                                         `wcc_list` — the alias tell. Suffix-matched, so
                                         BENTLEIGH EAST DC PCL LKR keeps its own name.
   two names with one SORT_SITE code     the aliases the reduction already checked and
                                         committed to, read from the dictionary rather than
                                         retyped, so this cannot drift from the model.
   <depot> VAN OPERATIONS / VAN SERVICES the depot's own red-van pickup fleet. A MOVEMENT, not a
                                         place: drawing it apart filled the picture with lanes
                                         like MELBOURNE NORTH PDC -> MELBOURNE NORTH VAN
                                         OPERATIONS, where the parcel never left the depot. The
                                         van goes to the depot's TERMINATING building, never to
                                         whatever else its PUD owns.

 LODGE_PUD is deliberately NOT used as a fold key: it groups a DEPOT's names, and Melbourne
 North's group holds a parcel centre 6.2 km from the depot. That one pair is folded by NAME
 below, because ops say it is one operation and no rule can derive it.
```

---

## WHICH EVENTS MAY NAME A BUILDING ON THE PATH
<a id="which-events-may-name-a-building"></a>

```text
The path is only as good as the bar it is built on, and this file already sets out three. A
fourth — ENTRY — is the rule ops asked for: the five events that can fire when a building
INDUCTS freight, walked in `Event_seq` order.

  entry     LODGE, MACHINE_SORT, TRANSFER, LOAD_ITEM, ACCEPT_FACILITY. `ORIGIN_FALLBACK_EVENTS`
            plus the customer's own handover. It leaves out UNLOAD_ITEMS and DEPART_CONTAINER
            (a container arriving or leaving says the freight moved, not that this building
            worked it) and DELIVER (the other end of the journey).
  physical  all eight presence events — what `full_chain` is built from.
  dock      the five events that cannot be recorded unless the freight was in the building.
            The bar `sankey_from_scans.py` counts a HANDLED touch on.
  sort      machine sorts only, which is the role basis' own bar.
```

---

## THE MODEL HAS TWO SORT ROUNDS, SO THE PATH IS CAPPED AT TWO
<a id="the-model-has-two-sort-rounds"></a>

```text
6.3% of the day is in three or more of our buildings before its depot, and the model has no
third sort to put them in. Something has to give, and the choice is between two endpoints:

  first_last  keep the FIRST building and the LAST, fold the interior. Both ends stay measured
              — where the freight entered (which stage 1 pins) and where it despatched from
              (which stage 4 verifies) — and what is invented is the DIRECTNESS of the middle.
  first_n     keep the first PATH_DEPTH buildings and drop the tail. The first leg stays exactly
              as measured, but the despatch site moves to a building that did not despatch it,
              so obs_delivery stops agreeing with the scans for that 6.3%.

first_last is the default: an invented directness is a claim about ONE lane, a moved despatch
site is a claim about where the depot's freight comes from, and this file's standing rule is
that a fold may say where freight was sorted, never where it came from. The two rules disagree
about 10,016 EA (6.05%), and `path_interior` counts what either of them folded, per parcel.
```

---

## The events that mean "this depot took the parcel in". The third…
<a id="the-events-that-mean-this-depot"></a>

```text
beside HANDLED for why it is neither of the other two. It is the WIDEST of the three on custody
(ACCEPT counts, because at the parcel's own delivering depot the DELIVER scan proves presence
downstream) and the NARROWEST on what it calls an arrival:
  * ZPT_LODGE is out — a bulk customer lodging at a depot two days before it goes out is not the
    depot holding inbound stock. Worth only 299 EA (0.2 pt) now that the final-stay rule catches
    most of it anyway, so this is belt and braces rather than the load-bearing exclusion it was.
  * ZPT_DELIVER is out — it is the END of the stay, not its start.
  * ZPT_MACHINE_SORT is not in this list but IS added at the call site: a depot that sorted the
    parcel obviously had it, and keeping it separate keeps this list "custody and dock events".
```

---

## AND THE DEPOT MUST HAVE WORKED IT BEFORE THE DELIVERY DATE
<a id="and-the-depot-must-have-worked"></a>

```text
Change 34 (2026-08-18). Standing in the building overnight is necessary but not sufficient for
the model's stage: the model's stage supplier hands the depot freight that is ALREADY SORTED
for the round, so the depot must have run its sort on the parcel on an earlier date than the
one it went out on. A parcel that arrived last night and was sorted this morning is worked on
the day, not staged for it.

The evidence is ZPT_MACHINE_SORT at the delivering depot — the same bar step 2a uses to say a
building sorted a parcel — restricted to the final stay, so a sort run before the parcel last
left the building cannot qualify it. Two things measured before settling on that:

  * TIMING. Of the volume that passes the arrival test and carries an own-depot sort inside
    the final stay, the sort is dated EXACTLY ONE DAY before delivery for 10,772 EA and the
    same day for 6,955 EA; only 91 EA sort two or more days out. "Sorted the day before" and
    "sorted before the delivery date" are therefore the same rule on this extract, and the
    date comparison is the one written, for the same reason the arrival test uses dates.
  * COVERAGE, and this is why the condition is CONDITIONAL. Only THREE of the eleven
    delivering depots run a machine that scans: Bayswater 91%, Melbourne North 86%, Sunshine
    West 80% of the volume they deliver. The other eight — Oakleigh South, Darebin, Dandenong
    South, Tullamarine, Pakenham, Abbotsford, Mount Waverley, Mulgrave — carry no
    ZPT_MACHINE_SORT at all, because they sort by hand and a manual sort leaves no scan.
    Asking them for one does not measure that they staged nothing; it measures that they have
    no sorter, which we already knew from the facility dictionary. ZPT_REMOVE_AGGR was tested
    as a manual-sort proxy (present at 36-98% of volume at every depot) and rejected: only
    21.4% of its own-depot occurrences are corroborated by a dock event in the same building,
    the same failure that kept the admin events out of `away`.

    So the condition is applied WHERE IT CAN BE ANSWERED and skipped where it cannot: a depot
    in SORT_CAPABLE_PUDS must show the sort, every other depot falls back to the arrival test
    alone. That keeps the tightening honest at the three depots that hold most of the stage
    without erasing eight depots on an absence of evidence. `sorted_before_delivery` is written
    out per parcel either way, so the two halves can always be separated again.

WHICH SORT, when a depot sorted the same parcel twice. 2,240 EA were machine-sorted at their
delivering depot BOTH before the delivery date and again on the day. "last" reads the final
sort, so those fall out: the depot demonstrably did sort work on this parcel on the day it went
out, which is exactly what the model's stage supplier is defined not to need. "any" reads the
earliest and keeps them, on the narrower reading that a sort the day before happened whatever
came after. "last" is shipped because the condition exists to identify freight the depot did
NOT have to work on the day; the switch is here because the other reading is defensible.
```

---

## CHANGE 41 — DROP THE VOLUME NO SCAN CAN PLACE AT A SORT SITE
<a id="change-41-drop-the-volume-no"></a>

```text
`entry()` below decides a parcel's entry site three ways, and only two of them are evidence:

  sorted    it was machine-sorted at one of the eight modelled sites          measured
  handled   it was not, but a HANDLED scan puts it in one of them             measured
  assumed   neither, so the site comes from DEPOT_CODE / BIG_HUB              a rule

The assumed rung is 4,665 EA. It is the weakest thing in this file: BIG_HUB names the class's
likeliest door, which for Express Post is nearly certain (TPF takes 79.5% of EP first sorts)
but for Parcel Post is a 30.4% plurality — we name one building while 70% of comparable freight
went through another. And in obs_joint those cells are indistinguishable from measured ones.

They USED to be dropped, and the diagram was built both ways so the cost stayed visible. Change
39 reversed the drop (see below) and the second, "cleaned" page was retired with it — there is
one diagram now, sankey-from-scans.html, and it shows this freight as NONE / ELSE. The RESCUE
stays — "handled at a modelled site" is real evidence and places 5,204 EA.
```

---

## CHANGE 39 : REVERSED. The assumed rung is now KEPT
<a id="change-39-reversed-the-assumed-rung"></a>

```text
The argument for dropping was that BIG_HUB invents an entry site. That argument still holds
and nothing below weakens it. What changed is what these parcels are FOR.

All 4,665 EA sit inside the residual — the 9,869 EA with no machine sort at any of the eight
sites — and they are exactly the half of it with no handling scan either. The residual has
stopped being noise to be cleaned off the entry columns: it is the MEASUREMENT behind the
manual-sort floor (MANUAL_SORT_SHARE, dials.csv). A depot that sorts by hand leaves no
ZPT_MACHINE_SORT and, at the eight sites, frequently no dock scan we recognise either — see
the SORT_CAPABLE_PUDS note above, where eight of eleven depots carry no machine-sort scan at
all because they sort by hand.

So dropping them is not neutral for the factor that now depends on them, and the arithmetic
says so plainly:

    DROP_UNPLACED_ENTRY = True    cohort 160,902, residual 5,204   ->  3.23%
    DROP_UNPLACED_ENTRY = False   cohort 165,567, residual 9,869   ->  5.96%

Deleting the un-scanned half of a population and then measuring how big that population is
gives 3.2% for a mechanism that is 6.0% of the day, and the error runs in the direction that
flatters the model: less hand labour, cheaper solve. Keeping them costs an invented entry site
on 2.8% of the day; dropping them costs half the manual-sort measurement. The first is the
smaller lie, and `entry_from` is written out per parcel either way, so any table that cannot
tolerate an assumed entry can still filter on it.

True restores the drop. Doing so WITHOUT re-deriving MANUAL_SORT_SHARE understates manual
sort by roughly half.
```

---

## Change 30, and it survives the dial collapse unchanged in…
<a id="change-30-and-it-survives-the"></a>

```text
which ORIGINS matter for a depot. It must not be allowed to decide that a SORTING SITE does
not exist. A site that feeds all 11 depots a thin slice each clears the flavour test on its
total and then loses every individual cell, and with the cells go its supplier cap, its sort
recipe and the site itself, all silently. So a flavour that cleared the flavour test but has
no cell anywhere above the fold is EXEMPT and keeps all of its cells — a thin slice at every
depot is exactly what a small sorting site looks like, and keeping only its biggest cell
would model Avalon as sorting for one depot alone.
```

---

## WHERE A FAMILY GOES WHEN IT LOSES EVERY CELL IN A ROW
<a id="where-a-family-goes-when-it"></a>

```text
The rule this function is built on is that dropping a thin site says where freight was
SORTED and never where it came from. The orphan branch below used to break that rule at the
one moment it mattered most: a (depot, class) row whose interstate volume all entered at
sites too small to survive lost its interstate share to the metro family, because there was
no interstate cell left to carry it. At a 1,300 EA fold that happened often enough to pull
the model's interstate share 5.3 points below the measurement and push metro 4.2 above it.

So a family that still has a surviving site SOMEWHERE for its class keeps its share and
enters at that site — its largest, by observed volume. This invents a routing, which is the
cheaper error: the alternative invents an origin, and origin is what obs_joint is for.
```

---

## STAGES 2 AND 3 ARE ONE TABLE NOW
<a id="stages-2-and-3-are-one"></a>

```text
`recv_entry` comes back EMPTY, and that is the change rather than a gap in it. A
cross-dock leg and a round-2 leg are the same truck between the same two buildings; the
role basis had to ask about it twice because it had two names for it, and Change 42
then had to pool the two answers back onto the physical (from, to) pair before the fold
would behave. The path basis never splits them, so there is nothing to pool: one leg,
one row, one test. Everything downstream — the ledger, the fold, the ONCE convention —
is unchanged and simply has one stage fewer to reconcile.
```

---

## Stage 4 despatch runs are the same kind of movement as stages 2…
<a id="stage-4-despatch-runs-are-the"></a>

```text
not one of the eight sort sites, so a site->depot pair can never collide with a site->site one
and the stages share one ledger safely. The one building that is BOTH — Bayswater, which the
scans call "BAYSWATER PDC" whether it is sorting or delivering — is left split, because the model
carries BAY and PUD_Bayswater as two nodes and pooling them here would be a claim about the
network that this function is not the place to make. It is worth 154 EA.

Stage 4 is also the one stage with NO ORIGIN: `deliver_rows` is (class, despatch site, depot),
because by the despatch column every family is already mixed in the building. It gets this
sentinel rather than a family, and the within-origin rule simply cannot be asked of it.
```
