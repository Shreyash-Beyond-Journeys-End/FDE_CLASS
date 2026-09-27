# Validation rules

I profiled the raw data first (notebook 1) and only wrote rules for problems that actually showed up
or that would break a metric. The rules live in `scripts/rules.py`. Counts below are for all 12 months
(543,143 raw rows) and come from `outputs/validation_report.csv`, which also has the count per month.

Severity means:

- FAIL: the row can't be trusted as a trip in this month. It is excluded from every metric but stays in
  `fact_trip` with `status = 'excluded'`.
- WARN: the trip counts, but one field is suspect, so it is left out of the one metric that uses that field.
- INFO: recorded and counted. Nothing is excluded.

## Row-level rules

| ID | Rule | Severity | Rows | % of raw | Business reason | Action |
|---|---|---|---|---|---|---|
| F01 | pickup timestamp not in the file's month | FAIL | 205 | 0.038 | a monthly KPI should only count that month's trips. 4 of these are dated 2008/2009 (clock errors). The other 201 fall just outside the month, median about 17 hours, at most about 6 days | excluded |
| F02 | dropoff before pickup | FAIL | 8 | 0.001 | impossible, times can't be trusted | excluded |
| F03 | negative `total_amount` | FAIL | 1,549 | 0.285 | 1,526 of them have a positive twin with the same vendor, times and zones, so they are fare reversals. Counting them would count the trip twice. The other 23 have no exact twin, but 22 of them share vendor and pickup time with a positive row | excluded, the original row stays |
| F04 | pickup zone not in the lookup | FAIL | 0 | 0 | the pickup couldn't be placed | excluded |
| W01 | zero duration | WARN | 416 | 0.077 | trip may be real, duration isn't | kept, left out of duration/speed |
| W02 | duration over 3 hours | WARN | 2,279 | 0.420 | median of these is about 23 hours with normal distance and fare, so the meter wasn't closed | kept, left out of duration/speed |
| W03 | zero distance | WARN | 17,866 | 3.289 | distance missing or trip cancelled after meter on | kept, left out of duration/speed |
| W04 | distance over 100 miles | WARN | 161 | 0.030 | not believable for a cab trip (max was 262,316 miles) | kept, left out of duration/speed |
| W05 | speed over 80 mph | WARN | 2,164 | 0.398 | distance and time can't both be right | kept, left out of duration/speed |
| W06 | pickup zone 264 (Unknown) or 265 (Outside of NYC) | WARN | 1,773 | 0.326 | we can't say if the pickup was in the permitted area | kept in volume, left out of KPI denominator |
| I01 | `trip_type` missing | INFO | 67,796 | 12.482 | can't say hail or dispatch | labelled `unclassified`, left out of the KPI denominator and counted in metric 4 |
| I02 | `passenger_count` missing or 0 | INFO | 75,329 | 13.869 | not used by any metric | none, listed to report to the data owner |
| I03 | `RatecodeID` missing or 99 | INFO | 67,796 | 12.482 | not used by any metric | none, listed to report to the data owner |

After FAIL rules: 541,384 valid trips and 1,759 excluded rows (0.32%).

What I chose not to do:

- I did not fill in `trip_type` for the missing rows. 6,741 of these valid trips start clearly inside the
  exclusionary zone, while only 264 dispatch trips do. So they don't behave like dispatch trips, and I have
  no basis to call them hails either.
- I did not cap or change long durations. They stay in the data and are only left out of the duration and
  speed medians.
- I did not move out-of-month trips into the month they belong to. They are 205 rows in a year. Moving them
  would make one month's numbers depend on another month's file, so I just excluded them.

## Month-level checks (gates)

Stored in the `month_check` table and used by `scripts/stage4_metrics.py` to set each month's publish status.
Stage 2 deletes its old output files before it starts, so stages 3 and 4 can't pick up a stale copy.

| Check | Stage | If it fails |
|---|---|---|
| every requested month is published (HEAD request, 403/404 = not published), checked before any download | stage1_ingest | pipeline stops, exit code 1, nothing downloaded |
| bytes saved = HTTP Content-Length | stage1_ingest | retry, then stop |
| API rows received = API `count(*)` | stage1_ingest | month published as WARN without cross-check |
| local file checksum = manifest checksum | stage1_ingest / stage2_validate | re-download (ingest). If stage 2 is run on its own and the file doesn't match, FAIL: the file is not read and the month is held |
| parquet file can be read | stage2_validate | FAIL, month held |
| rows read = parquet footer row count | stage2_validate | FAIL, month held |
| required columns present | stage2_validate | FAIL, month held |
| no undocumented columns | stage2_validate | INFO (June 2026 has `request_source`) |
| FAIL rows at most 2% of the month (change with `--max-fail-share`) | stage2_validate | FAIL, month held (highest real month is 0.39%) |
| raw rows = valid + excluded | stage3_model | FAIL, month held |
| 100% of trips join to `dim_zone` on pickup and dropoff | stage3_model | FAIL, month held |
| each vendor reports up to the last 2 days of the month | stage3_model | WARN (vendor 6 in May 2026 stops on the 26th) |
| file rows with a known zone within 1% of TLC's API count | stage4_metrics | WARN (May 2026 is -2.08%) |

A HELD month keeps its row counts in `monthly_metrics.csv` but its metric columns are blank, and the
pipeline exits with code 2. With the normal 2% limit nothing was held in my 12 months, and May 2026 was
published as WARN. Run 5 in `outputs/sample_run_log.txt` sets `--max-fail-share 0.0038` to show a held
month (December 2025, 0.39% FAIL rows).
