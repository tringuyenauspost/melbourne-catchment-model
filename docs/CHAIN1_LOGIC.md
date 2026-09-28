# Chain 1 — collection entity: what is derived from CCP booking data

Built by Macro 4. Chain 1 models the first mile: **pickup → round-0 sort → round-1 sort → terminate**.

**Balance**
- **Pickup = Kept at depot + Vic Metro to Metro + PDO terminate + Interstate + Regional**.

---

## 1. Where each part of chain 1 comes from

- **From CCP bookings:**
  - which stops are real pickups;
  - where the pickups are;
  - how each site's collection is divided into cells (the van rounds).
- **Not from CCP**, with the reasons:
  - **Volume level per site:** 2025 peak collection (`PEAK_2025_<site>`, from the originating-volume scans) × `PEAK_FACTOR` 0.70. CCP is not used because it records collected volume at only 2 of the 7 sites, 0.9% of the model's volume.
  - **Vehicle:** `first_mile_pickup.csv`. Transports use Truck, depots use Red_Van.
  - **Round-1 destination:** `first_mile_despatch.csv`, the manager's operating split (e.g. Melbourne Transport MPF 50 / MNP 30 / BAY 20).
  - **Terminate volumes (sinks):** read from chain 2's `SupplierCapabilities`.
  - **PP:EP split:** `PP_SHARE` 0.90.

---

## 2. The CCP extract

- `inputs/red_vans_inputs/CCP-18-05_to_25-05.csv`: one row per booked job, with location, location type (Customer / Network), address, facility, booking type, route, planned time, booking comments and measures.
- **Window:** Mon–Fri 18–22 May 2026, one clean week.
  - Out: Saturday (895 jobs), 25 May (so no Monday counts twice) and 26 May.
- **Seven collecting sites:**
  - five red-van depots: Sunshine West, Oakleigh South, Melbourne North, Bayswater, Dandenong South;
  - two transports: Melbourne Transport, Dandenong Transport Facility.

---

## 3. Which bookings count as a pickup

1. **Booking type** is `Pickup` or `Pickup & Delivery`. Delivery-only jobs are out.
2. **Own buildings are out.** The test runs only on `Location Type = Network`, because the same words match real customers, e.g. "Chemist Warehouse (Chadstone DC)".
   - Out: PDCs (including the van's own base), hubs, transport facilities, StarTrack and gateways.
   - These stops move freight already inside our network, so they are not new collection.
3. **Red vans: delivery centres count** (rule change 2026-09-24). A red-van pickup at a DC / MDC, e.g. Moorabbin DC – Vans, is a pickup point, adding 1,402 stops a week.
4. **Transports: customers and lodgement points only.**
   - Kept: post offices, retail outlets, lockers and street posting boxes.
   - Out: other Network stops. Their comments name a destination building ("Collect all for Mulgrave PDC"), so they are linehaul, not collection.
5. **Postcode cells with no real pickup are dropped:**
   - cells that only ever had delivery bookings;
   - cells whose only visits are to another site's hub dock (2 remain: Oakleigh South 3175 and 3045).

**Result**
- 42,022 pickup stops a week at 6,825 addresses (`outputs/pickup_points.csv`).
- Each address has its coordinate (`geocoding_result.csv`) and the vehicle that calls there.
- One geocode is wrong (Sandhurst SC, Cranbourne West, placed in Johannesburg). It is flagged `in_victoria = False`, not dropped.

---

## 4. From pickup stops to model cells

**Step 1: per-stop file for one day**
- `utilities/pickup_points.py --per-stop --day 2026-05-18` writes one row per collection: warehouse (site) → pickup address, and vehicle (`red_van` / `white_van`).

**Step 2: first-mile routing run**
- Run `first_mile_pickup_v4` (OSRM road distances).
- Vehicle settings:
  - red_van: 200 parcels, target 25 stops;
  - white_van: 180 parcels, target 10 stops.
- It groups each site's stops into **van rounds (clusters)** and writes:
  - `cluster_summary.csv`: one row per cluster, with its centroid;
  - `temp_clustered.csv`: one row per stop, with its cluster and site.
- These files are saved in `inputs/pick_up_cluster/`.

**Step 3: chain 1 reads the clusters (`PICKUP_CLUSTERS = pick_up_cluster`)**
- One cluster becomes one supplier, `SUP_PKP_<site>_C<k>`, at the cluster centroid.
- **402 cells:**
  - Oakleigh South 100
  - Sunshine West 81
  - Bayswater 68
  - Melbourne North 49
  - Dandenong South 44
  - Melbourne Transport 38
  - Dandenong Transport 22
- Each site's volume is split **equally** across its clusters. A cluster carries no measured volume of its own.
- Leg-1 distance is the haversine distance from cluster centroid to collecting site. The mean is 16.0 km, against 22.1 km on postcode centroids.

---

## 5. Alternative basis, built but not switched on

**Postcode weights (`PICKUP_WEIGHTS`, `utilities/catchment_visit_weights.py`)**
- Share of stop-events per (site, postcode) over the week, using the same filter rules as section 3.
- Stop-events are the best predictor of collected volume where CCP records it: r = 0.998 at Dandenong Transport and 0.988 at Melbourne Transport.
- The weights replace the equal split across a site's **postcodes**.
- This is shape only: the site total stays at `PEAK_2025` × `PEAK_FACTOR`.
- It cannot run together with `PICKUP_CLUSTERS`. It is `none` today.

---

## 6. Measured from CCP but not in the model yet

**Transport despatch (`utilities/transport_middle_mile.py`)**
- Each transport pickup is paired with the next delivery on the same route, in planned-time order.
- **Customer pickup → next drop, stop counts:**
  - Melbourne Transport: MPF 42 / SWP 17 / TPF 16 / MNP 15 / BAY ~0 (assumed: MPF 50 / MNP 30 / BAY 20).
  - Dandenong Transport: BAY 40 / DLC 38 / OAK 12 / TPF 6 / MPF ~0 (assumed: MPF 80 / BAY 20).
- These are stop counts, not volume. "Next delivery" misses freight left on board for a later drop, and 7 pooled Melbourne Transport routes are flagged.
- **Needs ops confirmation** before `first_mile_despatch.csv` changes.

**Middle mile (network → network movements)**
- About 320 a day at Dandenong Transport and 650–700 a day at Melbourne Transport, Mon–Thu.
- Not modelled today.

**Fleet: routes per day and volume per vehicle (`utilities/pickup_points.py`)**
- **Route** = one CCP route name with at least one counted pickup that day. It stands in for one collecting vehicle.
- **Volume** = the model's daily peak (`PEAK_2025_<site>` × `PEAK_FACTOR`), not a CCP measurement.
- Output: `outputs/pickup_fleet_metrics.csv` (Mon–Fri mean), or `pickup_fleet_metrics_<date>.csv` with `--day`.

| Vehicle | Site | Stops/day | Routes/day | Stops/route | Peak volume (EA) | Volume/vehicle | Volume/stop |
|---|---|---|---|---|---|---|---|
| Red van | Bayswater | 1,441 | 72.0 | 20.0 | 26,404 | 367 | 18.3 |
| Red van | Dandenong South | 1,112 | 48.6 | 22.9 | 24,798 | 510 | 22.3 |
| Red van | Melbourne North | 1,146 | 71.8 | 16.0 | 30,460 | 424 | 26.6 |
| Red van | Oakleigh South | 2,035 | 90.4 | 22.5 | 40,245 | 445 | 19.8 |
| Red van | Sunshine West | 2,088 | 135.6 | 15.4 | 67,794 | 500 | 32.5 |
| Truck | Dandenong Transport | 214 | 64.0 | 3.3 | 16,856 | 263 | 78.9 |
| Truck | Melbourne Transport | 370 | 109.2 | 3.4 | 246,074 | 2,253 | 665.1 |
| **All red vans** | | 7,821 | 418 | 18.7 | 189,701 | **453** | 24.3 |
| **All trucks** | | 584 | 173 | 3.4 | 262,930 | **1,518** | 450.5 |

Mon–Fri mean. Monday 18 May runs more routes (red vans 447, trucks 174), so volume per vehicle is slightly lower that day.

- **Measured stops per route replace the assumed ones.**
  - Red vans do 15–25 pickups per route, not 12.
  - Trucks do about 3.4 customer pickups per route, not 8.
  - Stops ÷ 12 (red van) and ÷ 8 (truck) therefore gives about 2× too many red vans and 2.4× too few trucks.
- **One number per vehicle type: red van 19 stops, truck 3.4 stops.**
  - It is the **mean** over every route on every day, Mon–Fri: total stops ÷ total route-days. Red van 18.7 over 2,092 route-days; truck 3.4 over 866.
  - Use the mean, not the median (red van 17, truck 3). Fleet = stops ÷ stops-per-vehicle only gives back the measured route count when the divisor is the mean, because the long routes carry a large share of the stops. The median over-counts the fleet.
  - Check: 7,821 ÷ 19 = 412 red vans against 418 measured; 584 ÷ 3.4 = 172 trucks against 173 measured.
  - If a whole number is required for trucks, use 3. It over-counts by about 12% (195 trucks), the conservative side for sizing.
  - It hides the site spread: red-van sites range from 15.4 (Sunshine West) to 22.9 (Dandenong South) stops per route. One number over-sizes Dandenong South and Oakleigh South by about 20% and under-sizes Sunshine West by about 20%. The two trucks agree (3.3 and 3.4).
- **Red-van sites are fairly even:** 367–510 EA per vehicle.
  - Sunshine West runs many short routes with big stops (15.4 stops, 32.5 EA each).
  - Dandenong South runs fewer, longer routes with smaller stops (22.9 stops, 22.3 EA each).
- **Every red-van site is above van capacity.** The model's red van carries about 160 EA effective (180 physical), so each route would need 2–3 loads. Either vans unload and go out again, or part of the peak is not van-collected (e.g. counter lodgement at the depot).
- **Trucks against red vans:** trucks average 3.3× more per vehicle, but that is all Melbourne Transport.
  - Dandenong Transport carries **less** per vehicle than any red-van site (263 EA).
  - Melbourne Transport carries 2,253 EA per vehicle, 8.6× Dandenong Transport, on the same 3.4 stops per route and above the model's truck capacity (1,500 EA).
- **Caveats:**
  - A vehicle running two routes a day means a smaller fleet carrying more each.
  - Truck routes doing only network linehaul are not counted: 109 of Melbourne Transport's ~210 routes a day have no customer pickup.

---

## 7. Known gaps

- **The clusters predate the delivery-centre rule.** They were built from 9,088 stops on 18 May; the rule adds about 290 stops that day. Re-run the routing on the new `pickup_stops` file, then rebuild Macro 4.
- **Equal split per cluster:** a busy round and a quiet round collect the same volume.
- **Volume level is not from CCP.** It rests on the 2025 peak × 0.70.
- **Melbourne Transport's peak does not match its pickups.**
  - Against Dandenong Transport, CCP shows 1.7× the stops, 1.7× the routes and 2.3× the recorded collected volume. The peak is 14.6× (351,535 against 24,081 EA).
  - The peak most likely includes volume that never passes a customer pickup stop: network linehaul, dock lodgement by large shippers, pooled ad-hoc routes.
  - **Needs ops confirmation** of what the 351,535 counts. Until then, Melbourne Transport's volume per vehicle is not reliable.
- **The despatch split is an assumption** that the CCP measurement contradicts at Dandenong Transport.
