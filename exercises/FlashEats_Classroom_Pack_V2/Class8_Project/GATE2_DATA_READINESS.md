# Gate 2 — Data Readiness Review

Reviewed on the runs for logical date 2026-09-22 (see `FlashEats_Class8_Walkthrough.ipynb` and `logs/pipeline_2026-09-22.log`, which has all seven runs of the day).

## Pipeline run

- [x] Complete flow runs with one command. `python run_pipeline.py --run-date 2026-09-22` started the mock API, fetched all pages, validated, saved and exited with 0.
- [x] Same run can be safely repeated. First run 1600 rows, second run 1600 rows, same partition replaced.
- [x] Raw API responses are preserved. 8 JSON pages in `data/raw/dispatch/run_date=2026-09-22/`.
- [x] Processed output is written only after validation. The three failed runs (missing column, stale data, `MAX_DATA_AGE_DAYS=20`) each end with "no processed output written".

## Validation

| Check | Status | Evidence / note |
|---|---|---|
| Required columns | PASS | "8 required columns present". With `promised_eta` dropped: `orders: missing required columns: ['promised_eta']`, exit 2 |
| Critical nulls | WARN | 37 delivered orders without `actual_delivery_at` (2.4% of delivered). Same 37 as in Class 5 and 6 |
| Order uniqueness | WARN | 6 rows involved (O00120, O00723, O01302 twice each), deduplicated to 1600. With an injected duplicate: 8 rows, still 1600 after clean |
| Freshness | PASS | latest order 2026-08-28 23:33, 25 days old, limit 60. Fails correctly with shifted data (390 days) and with the limit lowered to 20 |
| Dispatch retrieval completeness | PASS | 1600 records received = 1600 expected in every run |

## Reliability

| Capability | Status | Evidence / note |
|---|---|---|
| Bounded retries | PASS | every run: page 3 HTTP 500 and page 5 HTTP 429, each retried once after 1.0s (attempt 1/3), then OK |
| Useful failure message | PASS | message names the dataset and column or the age and limit; exit code 2 for validation stops |
| Logging | WARN | the log is detailed enough that I rebuilt every run from it (start time, chaos mode, warnings, result), but it does not record the config values, so the `MAX_DATA_AGE_DAYS=20` run looks like a normal run |
| Idempotent rerun | PASS | same row count, same partition, no extra files |
| Configuration outside core logic | PASS | changing `MAX_DATA_AGE_DAYS` by environment variable changed the result with no code change |

## Known limitations

- There is still no driver-arrival or food-ready event. The pipeline's median order-to-pickup is 26.07 min, and that is exactly where late orders lose their time (Class 7), but we can't split it between restaurant and driver.
- The transform only keeps `current_delivery_eta` from dispatch. `estimated_pickup_at`, `driver_id` and `reassigned_at` are dropped, so the Late Pickup Rate from my Class 7 model (42.0%) can't be built from this output.
- The late definition (`delay > 0`) has no documented owner yet (Class 6).
- `order_journey.csv` is written with `0o600` permissions because of the temp file, so other users on a shared machine can't read it.
- The log file is appended to for every run of the same date and the raw pages are overwritten even when a run fails, so the raw folder may come from a failed run.
- Nothing alerts anyone when a run fails, and the previous partition stays in place.
- The pack's `order_interventions.csv` is the Class 7 version (430 rows), not the 260-row file shipped with the Class 8 download. I kept it so the numbers line up with Class 7.

## Gate decision

**NOT READY**

Reason: the pipeline itself is dependable. It runs with one command, retries only what it should, stops bad data at the gate and reruns safely. But the data foundation is not ready for the next phase. The main driver of lateness (the wait before pickup) has no events to explain it, the pickup fields my Class 7 metrics need are dropped by the transform, and the KPI definition still has no owner. I'd pass it once `estimated_pickup_at` and the driver fields are carried into the order journey, the KPI owner signs off the definition, and there is at least an alert on failed runs.
