# Metric definitions

All metrics are computed per file month in SQL (`scripts/stage4_metrics.py`) on the SQLite model. "Valid"
means the trip passed every FAIL rule. "Pooled" in the evidence table means all published months added
together before dividing, not an average of monthly percentages.

There are 5 metrics. Volume and trip duration/speed are reported too, but only as context.

## Metric 1 (project KPI): Boro Zone share of street hails

```
street hails (valid, trip_type = 1) with pickup zone_class = 'boro_zone'
------------------------------------------------------------------------
street hails (valid, trip_type = 1) with a known pickup zone (not 264/265)
```

Green taxis exist to take street hails outside the Hail Exclusionary Zone. Dispatch trips are allowed
anywhere, so they are left out. Trips with no trip_type are left out of the denominator on purpose and
counted in metric 4. If that group grows, metric 4 shows it, and the KPI still means the same thing.

## The other metrics

| # | Metric | Formula | Link to the KPI | Decision trigger (per month) |
|---|---|---|---|---|
| 2 | Boundary-zone hail rate | street hails in zone_class 'boundary' / KPI denominator | the part of 100% - KPI that the zone data can't decide | none, used to rank zones and hours in `intervention_targets.csv` |
| 3 | Clear exclusionary-zone hail rate | street hails in zone_class 'clear_hez' (other Yellow Zone, JFK, LaGuardia, Newark) / KPI denominator | the rest of 100% - KPI. Metrics 1 + 2 + 3 = 100% | above 0.2%: field check at the top clear zones |
| 4 | Unclassified trips | valid trips with null trip_type / valid trips, plus the count of those that start in a 'clear_hez' zone | trips the KPI can't see at all | above 15%, or more than 900 of them in clear HEZ zones: raise with the vendor data team |
| 5 | Excluded share | raw rows hitting any FAIL rule / raw rows | how much raw data could not be used | above 2%: the month is held (this is the pipeline gate) |

KPI trigger: below 92% in a month means reviewing that month's intervention targets.

Why these levels (from the 12 monthly values in `outputs/monthly_metrics.csv`):

- KPI 92%: the monthly mean is 93.41% with a standard deviation of 0.45, so mean - 3 SD = 92.08%. I rounded down.
- Metric 3, 0.2%: mean 0.054%, SD 0.019, so mean + 3 SD is 0.11%. That is only about 40 hails in a month, so a
  few extra airport pickups could fire it. I used 0.2%, about twice the highest month (0.09%).
- Metric 4, 15%: this one is a fixed level and not based on the spread. The share has been rising (10.79% to
  14.69%), and mean + 3 SD would be 18.0%, which is too late. 15% fired once, in March 2026 (15.18%).
- Metric 4 count, 900 trips in clear HEZ zones: mean 562, SD 107, so mean + 3 SD = 884. I rounded up. It has
  not fired (highest month 743).
- Metric 5, 2%: this is the pipeline's hold limit (`--max-fail-share`). The highest real month is 0.39%.

The values live in `scripts/common.py`.

## Context (not metrics)

| Item | Formula | Why it's there |
|---|---|---|
| Valid green trips per day | valid trips / days in month | program size, so the KPI isn't read without knowing the volume |
| Gap vs TLC's published count | file rows with known pickup zone / API pickups - 1 | completeness check. Above 1% the month is published as WARN |
| Median duration and speed of Boro Zone street hails | median of `duration_min` and `speed_mph` for valid Boro Zone street hails with `duration_ok = 1` | what a trip in the permitted area looks like, and a check on the time fields |

## Intervention targets

`outputs/intervention_targets.csv`: valid street hails in 'boundary' and 'clear_hez' zones grouped by pickup
zone and hour band (00-06, 07-10, 11-14, 15-18, 19-23), with hails per week over the published months. The
top rows are where a field check or outreach would go first.

## Zone classes

| zone_class | Zones | Meaning |
|---|---|---|
| boro_zone | 205 zones with `service_zone = 'Boro Zone'` | green taxis may take street hails here |
| boundary | 24 Bloomingdale, 43 Central Park, 194 Randalls Island, 236 Upper East Side North, 262 Yorkville East, 263 Yorkville West | Yellow Zone zones whose polygon touches a Boro Zone polygon. The pickup could be right on the 96th/110th line |
| clear_hez | the other 49 Yellow Zone zones, JFK (132), LaGuardia (138), Newark (1) | clearly outside the permitted area for a street hail |
| unknown | 264, 265 | no location |

## Outputs

- `outputs/monthly_metrics.csv`: one row per month with all metrics, counts, publish status and triggers fired
- `outputs/evidence_table.md`: the evidence table used in the README
- `outputs/intervention_targets.csv`: zone x hour band table
- `outputs/hez_hails_by_zone.csv`, `outputs/hails_by_hour.csv`: zone and hour tables behind metrics 2 and 3
- `outputs/api_reconciliation.csv`: the API gap per month
- `outputs/dashboard.png`: four charts
