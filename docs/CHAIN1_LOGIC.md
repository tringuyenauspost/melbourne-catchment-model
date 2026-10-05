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

**In one line:** one day of pickup stops is grouped into van rounds, and each round becomes one pickup point (supplier) in the model.

| Step | What happens | Output |
|---|---|---|
| 1. List the stops | Every pickup on Mon 18 May, with its site and vehicle | one row per stop |
| 2. Group into rounds | The RCB routing run groups each site's stops into van rounds | 663 rounds from 9,378 stops |
| 3. Build the cells | Each round becomes one supplier at its centre | 663 pickup cells |

**What a cell takes from its round**
- **Location:** the round's centre.
- **Distance and time for leg 1:** the round's real route, meaning the road drive from the site and back plus the driving between stops.

**What a cell does NOT take from its round**
- **Volume.** Each site's total (2025 peak × 0.70) is split **equally** across its rounds, so a busy round and a quiet round collect the same amount.

**Rounds per site**

| Site | Rounds |
|---|---|
| Oakleigh South | 135 |
| Melbourne Transport | 126 |
| Sunshine West | 112 |
| Bayswater | 92 |
| Dandenong Transport | 72 |
| Melbourne North | 66 |
| Dandenong South | 60 |
| **Total** | **663** |

<details>
<summary>How to re-run it</summary>

1. `utilities/pickup_points.py --per-stop --day 2026-05-18` writes the stop list (site → address, `red_van` / `white_van`).
2. Run RCB routing on it (OSRM road distances). Vehicle settings: red_van 200 parcels / 19 stops, white_van 180 parcels / 3 stops. Save `cluster_summary.csv` and `temp_clustered.csv` to `inputs/pick_up_cluster/`. The current run is `pickup_new_version_2` (2026-09-29).
3. Rebuild Macro 4. It reads the folder named by `PICKUP_CLUSTERS` and makes one supplier `SUP_PKP_<site>_C<k>` per round. Leg 1 uses the round's route (`PICKUP_ROUTE_BASIS = route`); `haversine` restores the old straight-line distance.

</details>

---

## 5. Measured from CCP but not in the model yet

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
- **Every red-van site is above van capacity per route.** The model's red van carries about 160 EA effective (180 physical). Part of this is explained by pickup rounds (below); the rest is not.

**Pickup rounds: vans unload mid-shift and go out again**
- A **round** is a run of pickups ending at an **unload**: a delivery booking at one of our own buildings, in planned-time order along the route.
  - A red van's drop at a delivery centre is not an unload. It is a relay ("Deliver all ex-Moorabbin DC").
  - Pickups after the last booked unload count as one more round; the return to base is often not booked.
- **Example: HX000, Sunshine West, Mon 18 May.** 8 pickups (Footscray LPO/RP, posting boxes, CNP Brands, The Good Guys…) → unload at SWPDC at 13:25 (*"Deliver All Available – Customer Collections, Clearances"*) → 13 pickups (Woolworths CFC, Western Bulldogs, Chemist Warehouse…) → unload at SWPDC at 17:50. That is 21 stops per route, but 8 and 13 per trip.
- Output: the same `pickup_fleet_metrics.csv`, columns `rounds_per_day`, `rounds_per_route`, `multi_round_pct`, `stops_per_round`, `vol_per_round`.

| Site | Stops/route | Rounds/route | Routes with 2+ rounds | Stops/round | Volume/vehicle (day) | Volume/round |
|---|---|---|---|---|---|---|
| Bayswater | 20.0 | 1.56 | 51% | 12.8 | 367 | 235 |
| Dandenong South | 22.9 | 1.71 | 67% | 13.4 | 510 | 299 |
| Melbourne North | 16.0 | 1.39 | 35% | 11.5 | 424 | 305 |
| Oakleigh South | 22.5 | 1.76 | 52% | 12.8 | 445 | 253 |
| Sunshine West | 15.4 | 1.48 | 43% | 10.4 | 500 | 338 |
| **All red vans** | 18.7 | **1.57** | 48% | **11.9** | 453 | **290** |

- **For trip-based sizing, use 12 stops per round and 1.6 rounds per route** (red vans). The per-route number stays 19 for sizing vans.
- **Rounds are about the vehicle's load, not the fleet.** They don't change how many vans are needed, only how much each trip carries: 290 EA per trip against 453 per day.
- **Sunshine West's high volume per vehicle is mostly the single-number fleet.**
  - Dividing its 2,112 Monday stops by 19 gives 111 vans and 609 EA each.
  - CCP shows 136 routes a day at 15.4 stops each, so it's 500 EA per vehicle, and 338 per round at 1.48 rounds.
- **It is still the highest per trip, and still about 2× van capacity.** The reason is volume per stop: 32.5 EA against 18–27 elsewhere. Part of Sunshine West's peak (96,849) is probably not van-collected (counter lodgement, business-hub drop-offs). **Needs ops confirmation.**
- **Vans unload at their own depot, not a hub.** Sunshine West over the week: SWPDC 743 unloads, Tullamarine Parcel Facility 135, Sunshine West Van Operations 50, MPF none.
- **Trucks are not comparable on rounds.** They show 1.8 rounds per route at about 2 customer stops each, because a transport truck drops at our buildings as it goes (it runs as a service). The truck figure to use stays 3.4 stops per route.
- **Caveat:** 5–29% of red-van routes (17% at Sunshine West) have pickups after their last booked unload. They count as one final round. If some of those vans carry on to a second route instead, rounds are slightly understated.
- **Trucks against red vans:** trucks average 3.3× more per vehicle, but that is all Melbourne Transport.
  - Dandenong Transport carries **less** per vehicle than any red-van site (263 EA).
  - Melbourne Transport carries 2,253 EA per vehicle, 8.6× Dandenong Transport, on the same 3.4 stops per route and above the model's truck capacity (1,500 EA).
- **Caveats:**
  - A vehicle running two routes a day means a smaller fleet carrying more each.
  - Truck routes doing only network linehaul are not counted: 109 of Melbourne Transport's ~210 routes a day have no customer pickup.

---

## 6. Known gaps

- **The clusters predate the delivery-centre rule.** They were built from 9,088 stops on 18 May; the rule adds about 290 stops that day. Re-run the routing on the new `pickup_stops` file, then rebuild Macro 4.
- **Equal split per cluster:** a busy round and a quiet round collect the same volume.
- **Volume level is not from CCP.** It rests on the 2025 peak × 0.70.
- **Melbourne Transport's peak does not match its pickups.**
  - Against Dandenong Transport, CCP shows 1.7× the stops, 1.7× the routes and 2.3× the recorded collected volume. The peak is 14.6× (351,535 against 24,081 EA).
  - The peak most likely includes volume that never passes a customer pickup stop: network linehaul, dock lodgement by large shippers, pooled ad-hoc routes.
  - **Needs ops confirmation** of what the 351,535 counts. Until then, Melbourne Transport's volume per vehicle is not reliable.
- **The despatch split is an assumption** that the CCP measurement contradicts at Dandenong Transport.
