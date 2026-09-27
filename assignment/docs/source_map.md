# Source map

The client is hypothetical (TLC's green taxi program team). All data is real public TLC / NYC Open Data.

## Business question -> information -> source

| # | Business question | Information needed | Source | Field(s) used |
|---|---|---|---|---|
| Q1 | How much green taxi service is there each month, and which way is it moving? | trips per month | Green trip records (S1) | one row = one trip, `lpep_pickup_datetime` |
| Q2 | What share of street hails start in the area green taxis are allowed to serve? | pickup zone, hail or dispatch | S1 + zone lookup (S2) + TLC rule (S5) | `PULocationID`, `trip_type`, `service_zone` |
| Q3 | Where and when do street hails get coded to the exclusionary zone or airports? | pickup zone, dropoff zone, pickup hour | S1 + S2 + zone shapes (S4) | `PULocationID`, `DOLocationID`, pickup hour |
| Q4 | What does a Boro Zone street hail look like (how long, how fast)? | duration, distance | S1 | pickup/dropoff times, `trip_distance` |
| Q5 | Is the data complete and clean enough to act on? | an independent count of the same trips, rule checks | S3 (API) + validation of S1 | `trip_count` per month and zone |

## Sources

| ID | Source | Owner | Grain | Retrieval mode | Refresh | Used for |
|---|---|---|---|---|---|---|
| S1 | Green (LPEP) trip record parquet, `green_tripdata_YYYY-MM.parquet` | TLC; records are submitted by the technology providers (VendorID 1 Creative Mobile Technologies, 2 Curb Mobility, 6 Myle Technologies) | one row per trip record, meter on to meter off | file download over HTTPS (CloudFront) | monthly, about 2 months behind; files get re-posted (Dec 2025, Jan and Feb 2026 files all show last-modified 2026-03-25). July 2026 is also published (2026-09-17); I kept a fixed 12-month window | everything |
| S2 | Taxi zone lookup CSV | TLC | one row per taxi zone (265) | file download over HTTPS | rarely (last-modified 2024-02-22) | service zone of every pickup and dropoff |
| S3 | "Pickups and Drop-offs by Taxi Zone and Industry", NYC Open Data `c5iv-bn4s` | TLC, published on NYC Open Data | month x industry x pickup/drop-off x zone | SODA API (JSON), filtered to `industry='Green Cab'` and `pickup_dropoff='Pick-up'` | monthly (dataset last updated 2026-08-28 when I pulled it) | independent completeness check of S1 |
| S4 | "NYC Taxi Zones" shapes, NYC Open Data `8meu-9t5y` | TLC | one polygon set per zone | SODA API (GeoJSON), downloaded by stage1_ingest.py with a checksum in the manifest, used in notebook 1 | rarely | finding which Yellow Zone zones touch the Boro Zone |
| S5 | TLC green cab page and the TLC proposed-rule notice for the Boro Taxi decal | TLC | text | read by hand | when rules change | defining the permitted area |
| S6 | LPEP data dictionary PDF (dated March 18, 2025) | TLC | field definitions | read by hand | when schema changes | meaning of `trip_type`, vendor codes, rate codes |

What the rule sources say (only what I could find and read):

- TLC green cab page (nyc.gov/site/tlc/businesses/green-cab.page): "Street-Hail Liveries may not operate in
  the Hail Exclusionary Zone, south of West 110th St and East 96th St." It also says they must affiliate with
  FHV bases and can accept dispatches.
- TLC proposed-rule notice for the Boro Taxi decal (nyc.gov/assets/tlc/downloads/pdf/proposed_rule_boro_taxi_decal.pdf):
  the decal tells passengers the vehicles are "not authorized to pick up passengers in Manhattan south of East
  96th Street and south of West 110th Street or at the La Guardia or John F. Kennedy airports. (These areas are
  known as the Hail Exclusionary Zone.)"
- Data dictionary: `trip_type` is "1 = Street-hail, 2 = Dispatch", and it is "automatically assigned based on
  the metered rate in use but can be altered by the driver."

I did not find a TLC page that talks about drop-offs, so I don't measure drop-off location against any rule.
I only use drop-offs as context.

## Why this API and not another one

I looked at two TLC datasets on Open Data as the cross-check:

- `v6kb-cqej` "TLC Industry Indicators": has `trips_per_day` for license class "Green", but it is a daily
  average for the whole month and June 2026 was not there yet when I checked.
- `c5iv-bn4s` "Pickups and Drop-offs by Taxi Zone and Industry": monthly counts per zone. This is the same
  grain as my KPI (pickup zone), so I can compare month totals and single zones. I picked this one.

## Gaps and things the sources don't tell me

| Gap | Where | Effect |
|---|---|---|
| Location is a taxi zone, not a GPS point | S1 | Boundary zones can't be split into legal/illegal pickups |
| `trip_type` is null on 12.48% of raw rows (all of vendor 6, and some of vendor 2) | S1 | those trips can't be counted as hail or dispatch. 6,741 of them (valid trips, 6,681 from vendor 6) start clearly inside the exclusionary zone |
| `trip_type` "can be altered by the driver" | S6 | a hail flag only shows what the meter or driver recorded |
| New column `request_source` in 2026-06 (value "A" on the null trip_type rows), not in the dictionary | S1 | I record it but don't interpret it |
| Vendor 6 has no trips after 2026-05-26 in the May 2026 file (as of 2026-09-25 the file has not been re-posted) | S1 vs S3 | May volume is about 2% short |
| The API excludes zones 264/265 | S3 | compare like with like: my rows with a known zone |
| No driver, vehicle or base ID | S1 | can't say if a few drivers cause most boundary hails |
| TLC "makes no representations as to the accuracy of these data" (trip record page) | S1 | all findings describe the records, which may not match what actually happened |
| Randalls Island is labelled Yellow Zone and Roosevelt Island Boro Zone in the lookup | S2 | I follow the lookup; Randalls Island is in my boundary set anyway |
