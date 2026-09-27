# Workflow and data model

## The business workflow

One green taxi trip goes through these steps. The program's rule applies at step 2 (where the pickup
happens), so that is where the KPI is measured.

```mermaid
flowchart LR
    A[Passenger request] -->|street hail, trip_type=1| B
    A -->|dispatch from a base or app, trip_type=2| B
    A -->|not recorded, trip_type null| B
    B[Meter on: pickup zone + time] --> C[Meter off: dropoff zone + time]
    C --> D[Payment: fare, tip, payment_type]
    D --> E[Vendor submits record to TLC]
    E --> F[TLC publishes monthly file]
    F --> G{Pickup zone class}
    G -->|Boro Zone| H[Permitted area: counts toward KPI]
    G -->|boundary Yellow Zone| I[Can't tell from zone: potential HEZ pickup]
    G -->|other Yellow Zone, JFK, LGA, EWR| J[Clearly outside permitted area]
    G -->|264 / 265| K[Unknown: out of KPI denominator]
    I --> L[Intervention: field checks / driver outreach]
    J --> L
```

Plain-text version:

```
request (hail / dispatch / unknown)
   -> pickup (meter on, zone, time)          <- the rule applies here
   -> dropoff (meter off, zone, time)
   -> payment (fare, total, payment_type)    <- reversals show up here as negative totals
   -> vendor record -> TLC monthly file
   -> outcome: boro_zone | boundary | clear_hez | unknown
   -> intervention (TLC): where to send field checks or outreach
```

Entities: trip, taxi zone, service zone, month (file), vendor.
Events/states: request mode, pickup, dropoff, payment, fare reversal, record status (valid / excluded).
Intervention: TLC enforcement or driver outreach, aimed at zone x hour bands (`outputs/intervention_targets.csv`).
Outcome: the pickup's zone class, rolled up into the monthly KPI.

The grain of the source and of my model is the trip record. TLC does not publish separate event records,
so the events come from the timestamps on each trip. The SQLite view `trip_event` turns them into rows:

| event | from | count |
|---|---|---|
| pickup | `pickup_ts`, `pu_location_id` | 543,143 (541,384 valid) |
| dropoff | `dropoff_ts`, `do_location_id` | 543,143 (541,384 valid) |
| fare_reversal | rows with rule F03 (negative total), at `dropoff_ts` | 1,549 |

## The relational model (SQLite, `data/processed/green_taxi.db`)

```mermaid
erDiagram
    dim_month ||--o{ fact_trip : "file_month"
    dim_zone  ||--o{ fact_trip : "pu_location_id"
    dim_zone  ||--o{ fact_trip : "do_location_id"
    fact_trip ||--o{ trip_flag : "trip_id"
    validation_rule ||--o{ trip_flag : "rule_id"
    dim_month ||--o{ api_zone_pickups : "month"
    dim_zone  ||--o{ api_zone_pickups : "location_id"
    dim_month ||--o{ month_check : "month"
    fact_trip ||--o{ trip_event : "view: pickup, dropoff, fare_reversal"

    fact_trip {
        text trip_id PK "file month + row number"
        text file_month FK
        int vendor_id
        text request_mode "street_hail / dispatch / unclassified"
        text pickup_ts
        text dropoff_ts
        int pickup_hour
        int pu_location_id FK
        int do_location_id FK
        real trip_distance
        real duration_min
        real speed_mph
        real total_amount
        text status "valid / excluded"
        int duration_ok
        int pickup_known
    }
    dim_zone {
        int location_id PK
        text borough
        text zone
        text service_zone "from TLC lookup"
        text zone_class "boro_zone / boundary / clear_hez / unknown"
    }
    dim_month {
        text month PK
        text source_url
        int size_bytes
        text sha256
        int parquet_rows
    }
    trip_flag {
        text trip_id FK
        text rule_id FK
    }
    validation_rule {
        text rule_id PK
        text severity "FAIL / WARN / INFO"
        text business_reason
        text action
    }
    api_zone_pickups {
        text month
        int location_id
        int trip_count
    }
    month_check {
        text month
        text check
        text status "PASS / WARN / FAIL / INFO"
    }
```

Plain-text version:

```
dim_month (1) ---< fact_trip >--- (1) dim_zone   [joined twice: pickup and dropoff]
                      |
                      +---< trip_flag >--- validation_rule
dim_month (1) ---< api_zone_pickups >--- dim_zone
dim_month (1) ---< month_check
fact_trip (1) ---< trip_event (view, 2 or 3 events per trip)
```

Notes on the choices:

- `fact_trip` keeps every raw row, including excluded ones, so `raw rows = valid + excluded` can be checked
  in SQL for every month.
- `zone_class` is a column I added. It isn't in TLC's lookup, and it adds one thing the lookup does not have: the six Yellow Zone zones
  whose shapes touch a Boro Zone zone (24, 43, 194, 236, 262, 263) are `boundary`.
- `trip_id` is built from the file month and row position, so it is the same on every rerun of the same file.
