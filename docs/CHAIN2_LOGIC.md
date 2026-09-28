# Chain 2 — delivery entity: how source, sink and facilities touched are determined

Built by Macro 2 (measurement) and Macro 3 (model tables). Everything in chain 2 is **measured** from one day of delivery scans. Nothing is assumed except machine rates and operating hours.

**Balance**
- Per depot × product class: **Kept at depot (stage) + Interstate + Victoria same-day = Delivered demand**.
- The balance is exact by construction. The build stops if the emitted tables do not reproduce the measured split.

---

## 1. The measurement

**Data**
- `melbourne-delivery-volume-all-scan-events.csv`: every scan event for parcels delivered by the 11 modelled Melbourne depots on the peak day (20 May 2026). About 3 M events and 167,445 articles.

**Clean (Macro 2, step 1)**
- Drop consignments with no delivery coordinate.
- Drop Western DC, Mulgrave PDC and StarTrack Mulgrave, **before** any remap.
- Keep in-scope products: eParcel Express / Standard / Returns, Parcel Post Plus, Metro Next Day.
- Remap Holloway Dr → Bayswater PDC, and V03974 → Mulgrave PDC.
- Keep consignments that terminate at the 11 modelled depots.
- Tag every event with its state, by point-in-polygon on the event coordinate.

**Reduce (Macro 2, step 2)**
- Each consignment becomes one row: what it is, where it came from, which buildings it passed through, and which depot delivered it.

---

## 2. Sink — where the parcel ends

**Delivering depot**
- The depot is `Terminating_facility_name`: the depot the network planned. It is on every parcel and is always one of the 11 depots.
- The last delivery scan was tested as the basis and **rejected**. It often names a post office, locker or delivery centre rather than a depot, and would drop about a tenth of the day.

**Delivery zones**
- Each depot's parcels are split into its last-mile route clusters (`temp_clustered.csv`, `cluster_summary.csv`).
- Each cluster becomes one customer, `CZ_<depot>_<n>`, located at the cluster centroid.

**Demand is tagged by origin**
- Each zone's demand row is split by where the volume came from, using the measured origin mix for its depot.
- This is what forces the path from source to sink. A zone asking for interstate volume that entered at Melbourne Parcel can only be served by parcels of that flavour. No routing constraint is needed.

---

## 3. Source — where the parcel came from

**Step 1: lodgement state**
- **Rule 1 (94% of articles):** the state recorded on the lodgement scan.
- **Rule 2 (fallback, 5.8%):** the state on the first scan that could plausibly happen at the lodgement end. Four physical event types are used.
  - Scored against parcels that do have a lodge scan: 98.2% correct by article.
- Parcels neither rule can place are UNKNOWN and leave the measurement.

**Step 2: metro or regional (Victoria only)**
- The lodgement facility is placed on the map and tested against the first-mile catchment polygons.
  - Inside the catchment → METRO.
  - Otherwise → REGION.
- REGION is an **upper bound**. It also holds lodgement points that could not be placed at all.

**Step 3: kept at depot (the overnight stage)**
- A parcel is kept at depot when both hold:
  1. its **final stay** at the delivering depot began before the delivery date;
  2. that depot **sorted** it before the delivery date.
- "Final stay" matters. A parcel that visited its depot, left for a hub and came back on delivery day was moving, not standing. Counting the first touch instead overstated the stage (25.6% vs 14.6%).
- Dates are compared, never clock times, because event timestamps are database write times.

**Resulting source bands → supplier families**
- **Interstate** (all non-Victorian lodgement) → `SUP_INT_<site>`: enters at its measured first Melbourne building. There are six entry sites: the 3 hubs plus Sunshine West, Melbourne North and Bayswater.
- **Metro same-day** → `SUP_MET_<site>`: enters at its measured first building.
- **Region** → `SUP_REG_<site>`: enters at its measured first building.
- **Kept at depot** (metro-lodged only) → `SUP_STAGE_<depot>`: appears at the delivering depot already delivery-ready. It has one recipe (the driver wave) and no unload, no sort and no hub.
- Supplier capacities are **exact** to the measured volume per site and class.

---

## 4. Facilities touched — the itinerary

**What counts as a building on the path**
- Buildings are read from **physical** events only: the scan types that prove the parcel was in the building.
  - Acceptance-only scans are excluded. An acceptance does not prove physical presence, and counting it would almost double the cross-dock (10.4% → 18.6%).
- **Victoria only.** The journey starts at the state border; interstate buildings before that are cut.
- The path **stops before the delivering depot**. The depot is the sink, not a touch.
- Consecutive repeats in one building collapse to one stop, and the collapse is re-run after every cut, so no lane runs from a building to itself.

**One building, several names**
- Names that are the same building are folded before the path is drawn. Examples:
  - `<base> PARCEL DELIVERY` / `<base> PDC`
  - Holloway Dr / Bayswater PDC
  - Avalon's three names
  - Melbourne North PDC / Melbourne North Parcel Facility (ops' ruling)
- A depot's van-operations arm is the depot itself, not a separate building.

**Path to model rounds**
- The model has two sort rounds, so the path is capped at two buildings.
- **Rule `first_last`:** keep the first building (the entry) and the last (the despatch site), and fold the interior.
  - 6.3% of the day touches 3 or more buildings. For those parcels the middle is simplified, but both ends stay measured.
- **1 building:** single sort at the entry site, then despatch to the depot.
- **2 buildings:** a second sort, which must be at a **different** site from the first. This rule is structural: a site is never given the round-2 recipe for its own flavour.
- **Cross-dock:** handled at a hub but sorted at the arrival depot. It is modelled as an unsorted hub → depot lane (`XDock`).

**Folding small lanes**
- A lane (a physical from → to pair) carrying fewer than `FOLD_MIN_ARTICLES` (200 EA) is folded into the surviving lanes.
- Folding is done band by band, after the lanes are built, so it never moves volume between source bands.

**How strictly the model follows the measured routing**
- Each round-2 lane share is banded by `SORT_BAND` (±5 percentage points) around the measured share. Macro 5 then pins it (`--band 0`).
- Any lane measured under 5% gets a floor of zero in the band. This is a known clip.

---

## 5. Measured tables handed to the build

- `ObsJoint`: depot × class × source band volume. This is the balance.
- `ObsDemand`: delivered demand per depot.
- `ObsLegs`: building → building lanes (entry → second building).
- `ObsSingleSort` / `ObsSecondSort`: the sort-round shares per family × site × class.
- `ObsRound2Sites`: where second sorts happen.
- `ObsDelivery`: the despatch site → delivering depot matrix.
- `ObsPath`: the full path, read only if a third round is ever turned on.
- `Provenance`: the measurement's metadata. The build re-derives a ratio from it.

---

## 6. Checks built into the run

- **Drift guard:** Macro 3 re-derives every factor from `ConsignmentPaths`, and stops if a dial changed after Macro 2 ran.
- **Balance check:** the model's tables must reproduce the measured stage / interstate / Victoria split exactly.
- **Structural checks:**
  - No round-2 or driver recipe consumes a pickup product.
  - Every interstate flavour has an unbroken chain to its own delivered product.
  - Every product-named minimum is within what its lanes can physically carry.

---

## 7. Known limits

- The 6.3% of parcels that touch 3 or more buildings are modelled as 2-building journeys.
- The band clips lanes under 5%. Banded second sorts come out about 17% below measured.
- REGION is an upper bound, because unplaceable lodgement points fall into it.
- This is a single measured day. The whole file is treated as 20 May delivery, which is ops' position.
