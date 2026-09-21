# Prompt: build Wip_C02_ScanClean_AI

Paste everything below the line into the Optilogic AI agent.

---

Build an output table named **`Wip_C02_ScanClean_AI`** from the two inputs below.

## Input 1 — raw scan events

`/projects/My Files/DataStar/Raw Inputs/melbourne/melbourne-delivery-volume-all-scan-events.csv`

3,537,411 rows. One row per scan event; a consignment has many events. Columns:

`Consignment_ID, Event_seq, Events_traced, Event_type, Event_sub_type, Event_stage,
Event_date, Event_local_time, Event_facility_name, Event_Lat, Event_Long, Del_date,
Del_local_time, Del_Lat, Del_Long, Del_state, Terminating_facility_name,
Terminating_Lat, Terminating_Long, Contract_ID, Product_type, Article_count`

`Consignment_ID` is TEXT, not a number — it has leading zeros (`0001004850031925420998`)
and letters (`336AV5179085`). Never cast it to an integer.

`Del_*`, `Terminating_*`, `Contract_ID`, `Product_type` and `Article_count` are constant
within a consignment. `Event_*` varies row to row.

## Input 2 — state boundaries

`/projects/My Files/DataStar/Raw Inputs/melbourne/aus_state_boundaries.csv`

9 rows, 2 columns: `STE_NAME21` (state name) and `geometry` (a WKT MULTIPOLYGON in
EPSG:4326, longitude first). Used only to look up which state a scan event happened in.

## Output — `Wip_C02_ScanClean_AI`

Exactly these 12 columns, in this order:

`Consignment_ID, Event_seq, Event_type, Event_sub_type, Event_stage, Event_date,
Event_local_time, Event_facility_name, Terminating_facility_name, Product_type,
Article_count, STE_NAME21`

Expected result: **2,962,228 rows / 163,250 consignments / 167,445 articles**.

## Steps — the order of steps 1 and 2 is not optional

### Step 1 — row filter. Must run FIRST, before any renaming.

Keep a row only if ALL four hold:

1. `Del_Lat` is not null
2. `Del_Long` is not null
3. `Terminating_facility_name` NOT IN (`WESTERN DC`, `MULGRAVE PDC`, `STARTRACK MULGRAVE`)
4. `Product_type` IN (`eParcel Express`, `eParcel Standard`, `eParcel Returns`,
   `rParcel Post Plus`, `Metro Next Day`)

Expect **3,022,589 rows** remaining.

### Step 2 — rename two facilities. Must run AFTER step 1, in this order.

1. Where `Terminating_facility_name` = `HOLLOWAY DR PARCEL OPERATIONS`, set it to
   `BAYSWATER PDC`. Expect **450,326 rows** changed.
2. Then, where `Contract_ID` = `V03974` AND `Terminating_facility_name` =
   `OAKLEIGH SOUTH PDC`, set it to `MULGRAVE PDC`. Expect **91,321 rows** changed.

**Why the order matters.** Step 1 excludes `MULGRAVE PDC`. That exclusion is aimed at 167
consignments that are natively Mulgrave (all have a null `Contract_ID`) and do not belong
in this table. The Mulgrave volume that DOES belong is created in step 2, out of Oakleigh
South. If you rename first and filter second, the exclusion deletes the rows you just
renamed: 0 Mulgrave rows survive instead of 89,203. Filter first, rename second.

### Step 3 — keep only modelled PDCs

Keep rows where `Terminating_facility_name` is one of:

`ABBOTSFORD PARCEL DELIVERY`, `BAYSWATER PDC`, `DANDENONG SOUTH PDC`, `DAREBIN PDC`,
`MELBOURNE NORTH PDC`, `MOUNT WAVERLEY PARCEL DELIVERY`, `MULGRAVE PDC`,
`OAKLEIGH SOUTH PDC`, `PAKENHAM PARCEL DELIVERY`, `SUNSHINE WEST PARCEL DELIVERY`,
`TULLAMARINE PDC`

On this extract that drops nothing (still **3,022,589 rows**) because step 1 already
removed everything out of scope. Keep it as a guard.

### Step 4 — derive `STE_NAME21`, then drop what does not place

For each row, take the point (`Event_Long`, `Event_Lat`) and find which boundary polygon
from Input 2 contains it. Write that polygon's `STE_NAME21` onto the row.

- The point is the EVENT coordinate, not `Del_Lat`/`Del_Long` and not `Terminating_*`.
- Longitude is X, latitude is Y. Reversing them puts every point outside Australia, and
  the symptom is an empty table with no error.
- A point matching no polygon (or with a null coordinate) gets a null.
- A point exactly on a border can match two polygons; keep one row, do not duplicate.

Then **drop rows where `STE_NAME21` is null**. Expect **2,962,228 rows** (60,361 dropped).

Performance note: there are only ~311,000 distinct `(Event_Lat, Event_Long)` pairs across
the 3.0M rows. Look up the distinct pairs once and join the answer back, rather than
testing all 3.0M points — it is roughly 10x less work.

### Step 5 — select the 12 output columns, then sort

Sort ascending by `Consignment_ID` (as TEXT) then `Event_seq` (as a number).

## Acceptance checks

Row count **2,962,228**. First and last rows, in full:

```
0001004850031925420998,1,ZPT_LODGE,,,2026-05-18,10:36:40,BERRIGAN LPO,DAREBIN PDC,eParcel Express,1,New South Wales
ZZZ5001652,19,ZPT_DELIVER,Success,Delivery,2026-05-20,11:54:18,MELBOURNE NORTH PDC,MELBOURNE NORTH PDC,eParcel Standard,1,Victoria
```

Row counts by `Terminating_facility_name`:

| facility | rows |
|---|---|
| SUNSHINE WEST PARCEL DELIVERY | 631,481 |
| BAYSWATER PDC | 441,952 |
| OAKLEIGH SOUTH PDC | 360,236 |
| DAREBIN PDC | 350,461 |
| MELBOURNE NORTH PDC | 303,113 |
| DANDENONG SOUTH PDC | 233,949 |
| TULLAMARINE PDC | 213,771 |
| PAKENHAM PARCEL DELIVERY | 182,350 |
| MULGRAVE PDC | 89,203 |
| ABBOTSFORD PARCEL DELIVERY | 87,885 |
| MOUNT WAVERLEY PARCEL DELIVERY | 67,827 |

Row counts by `Product_type`:

| product | rows |
|---|---|
| eParcel Standard | 2,130,763 |
| eParcel Express | 452,877 |
| rParcel Post Plus | 304,081 |
| eParcel Returns | 67,257 |
| Metro Next Day | 7,250 |

Row counts by `STE_NAME21`:

| state | rows |
|---|---|
| Victoria | 2,564,153 |
| New South Wales | 312,921 |
| Queensland | 38,681 |
| South Australia | 26,843 |
| Western Australia | 8,035 |
| Tasmania | 5,756 |
| Australian Capital Territory | 4,989 |
| Northern Territory | 850 |

If you report an article total, count `Article_count` **once per consignment**. It repeats
on every scan of a consignment, so summing it down the rows gives 3,872,922 instead of the
true 167,445 — a 20x overcount.

If any check fails, say which one and stop. Do not adjust the filters to make the numbers
match.
