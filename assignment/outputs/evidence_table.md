# Evidence table: green taxi street hails and the service area

Built by `python run_all.py` from TLC green trip records 2025-07 to 2026-06. Pooled = all published months added up before dividing (12 of 12 months published).

## The 5 metrics

| #       | Metric                                                                                                                            | 2025-07                   | 2026-06                   | Pooled                      | Decision trigger                                                                   |
|:--------|:----------------------------------------------------------------------------------------------------------------------------------|:--------------------------|:--------------------------|:----------------------------|:-----------------------------------------------------------------------------------|
| 1 (KPI) | Street hails picked up in the Boro Zone, % of hails with a known zone                                                             | 92.94%                    | 93.65%                    | 93.39%                      | below 92.0% in a month: review that month's intervention targets                   |
| 2       | Street hails in a boundary Yellow Zone, % of hails                                                                                | 7.00%                     | 6.26%                     | 6.56% (29,222)              | no trigger, used to rank zones and hours                                           |
| 3       | Street hails clearly inside the exclusionary zone or at an airport, % of hails                                                    | 0.06%                     | 0.09%                     | 0.05% (239)                 | above 0.2% in a month: field check at the top clear zones                          |
| 4       | Valid trips with no trip_type (the KPI can't see them), % of valid trips, and how many start clearly inside the exclusionary zone | 10.79% (477 in clear HEZ) | 14.69% (743 in clear HEZ) | 12.52% (6,741 in clear HEZ) | above 15.0%, or over 900 in clear HEZ, in a month: raise with the vendor data team |
| 5       | Raw rows excluded by FAIL rules, % of raw rows                                                                                    | 0.30%                     | 0.28%                     | 0.32%                       | above 2% in a month: month is held and not published                               |

## Context

| Context (not a metric)                            | 2025-07               | 2026-06              | Pooled                 |
|:--------------------------------------------------|:----------------------|:---------------------|:-----------------------|
| Valid green trips per day                         | 1,550                 | 1,468                | 541,384 trips in total |
| File rows vs TLC's published count (API)          | -0.15%                | +0.02%               | see monthly table      |
| Median duration / speed of Boro Zone street hails | 12.38 min / 10.26 mph | 12.58 min / 9.73 mph | 12.50 min / 9.89 mph   |

## Monthly values

| Month   | Status   | 1 KPI %   | 2 Boundary %   | 3 Clear HEZ %   | 4 Unclassified %   | 4 Unclassified in clear HEZ   | 5 Excluded %   | Trips/day   | API gap %   | Triggers fired                                                  |
|:--------|:---------|:----------|:---------------|:----------------|:-------------------|:------------------------------|:---------------|:------------|:------------|:----------------------------------------------------------------|
| 2025-07 | OK       | 92.94     | 7.00           | 0.06            | 10.79              | 477                           | 0.30           | 1,550       | -0.15       | none                                                            |
| 2025-08 | OK       | 93.89     | 6.06           | 0.05            | 10.28              | 375                           | 0.34           | 1,489       | -0.03       | none                                                            |
| 2025-09 | OK       | 92.84     | 7.09           | 0.07            | 11.10              | 489                           | 0.31           | 1,625       | 0.01        | none                                                            |
| 2025-10 | OK       | 92.81     | 7.14           | 0.05            | 10.18              | 517                           | 0.31           | 1,589       | -0.01       | none                                                            |
| 2025-11 | OK       | 93.23     | 6.72           | 0.06            | 11.91              | 616                           | 0.35           | 1,558       | -0.01       | none                                                            |
| 2025-12 | OK       | 93.65     | 6.30           | 0.05            | 12.16              | 585                           | 0.39           | 1,550       | 0.02        | none                                                            |
| 2026-01 | OK       | 93.32     | 6.64           | 0.04            | 13.49              | 522                           | 0.35           | 1,295       | 0.00        | none                                                            |
| 2026-02 | OK       | 94.08     | 5.89           | 0.03            | 14.46              | 464                           | 0.32           | 1,330       | -0.04       | none                                                            |
| 2026-03 | OK       | 93.19     | 6.78           | 0.03            | 15.18              | 679                           | 0.28           | 1,422       | 0.01        | unclassified trips above 15.0%: raise with the vendor data team |
| 2026-04 | OK       | 93.30     | 6.66           | 0.04            | 14.27              | 686                           | 0.36           | 1,469       | -0.01       | none                                                            |
| 2026-05 | WARN     | 94.02     | 5.90           | 0.08            | 12.89              | 588                           | 0.30           | 1,445       | -2.08       | none                                                            |
| 2026-06 | OK       | 93.65     | 6.26           | 0.09            | 14.69              | 743                           | 0.28           | 1,468       | 0.02        | none                                                            |

Status notes:

- 2026-05 (WARN): volume -2.08% vs TLC aggregate; vendor_6_reports_to_month_end (4048 valid trips, last pickup date 2026-05-26)

## Intervention targets: top 10 zone x hour band for street hails outside the Boro Zone

| zone                  |   location_id | zone_class   | hour_band   |   street_hails |   hails_per_week |   % of these hails |
|:----------------------|--------------:|:-------------|:------------|---------------:|-----------------:|-------------------:|
| Central Park          |            43 | boundary     | 15-18       |           9738 |            186.8 |               33.1 |
| Central Park          |            43 | boundary     | 11-14       |           6020 |            115.5 |               20.4 |
| Central Park          |            43 | boundary     | 19-23       |           3567 |             68.4 |               12.1 |
| Central Park          |            43 | boundary     | 07-10       |           2855 |             54.8 |                9.7 |
| Upper East Side North |           236 | boundary     | 15-18       |           1101 |             21.1 |                3.7 |
| Upper East Side North |           236 | boundary     | 07-10       |            982 |             18.8 |                3.3 |
| Bloomingdale          |            24 | boundary     | 07-10       |            965 |             18.5 |                3.3 |
| Upper East Side North |           236 | boundary     | 11-14       |            880 |             16.9 |                3   |
| Bloomingdale          |            24 | boundary     | 11-14       |            877 |             16.8 |                3   |
| Bloomingdale          |            24 | boundary     | 15-18       |            744 |             14.3 |                2.5 |
