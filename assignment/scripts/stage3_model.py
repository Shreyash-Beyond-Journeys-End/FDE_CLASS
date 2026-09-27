"""Stage 3: build the relational model in SQLite and run the between-stage checks.

Tables (data/processed/green_taxi.db):
  dim_zone          one row per taxi zone, with service_zone and our zone_class
  dim_month         one row per file month, with what we downloaded
  fact_trip         one row per raw trip record (valid and excluded), the event record
  trip_flag         one row per (trip, rule) that fired
  validation_rule   the rule catalogue
  api_zone_pickups  TLC's published monthly pickups per zone (from the API)
  month_check       month-level checks from stages 2 and 3
  trip_event        view: the pickup, dropoff and fare-reversal events of each trip

The grain of the model is the trip record. The events are not separate source records,
they come from the timestamps (and the reversal flag) on each trip row.
"""
import json
import sqlite3

import pandas as pd

import common as c
import rules

SCHEMA = """
CREATE TABLE dim_zone (
    location_id INTEGER PRIMARY KEY,
    borough TEXT, zone TEXT, service_zone TEXT,
    zone_class TEXT NOT NULL
);
CREATE TABLE dim_month (
    month TEXT PRIMARY KEY,
    source_url TEXT, size_bytes INTEGER, sha256 TEXT, parquet_rows INTEGER,
    api_zone_rows INTEGER
);
CREATE TABLE fact_trip (
    trip_id TEXT PRIMARY KEY,
    file_month TEXT NOT NULL REFERENCES dim_month(month),
    vendor_id INTEGER,
    request_mode TEXT NOT NULL,          -- street_hail / dispatch / unclassified
    request_source TEXT,
    pickup_ts TEXT, dropoff_ts TEXT,
    pickup_date TEXT, pickup_hour INTEGER,
    pu_location_id INTEGER REFERENCES dim_zone(location_id),
    do_location_id INTEGER REFERENCES dim_zone(location_id),
    passenger_count INTEGER, trip_distance REAL,
    duration_min REAL, speed_mph REAL,
    payment_type INTEGER, fare_amount REAL, total_amount REAL,
    status TEXT NOT NULL,                -- valid / excluded
    duration_ok INTEGER NOT NULL,        -- 1 if no duration/distance/speed WARN
    pickup_known INTEGER NOT NULL        -- 0 if pickup zone is 264/265
);
CREATE TABLE validation_rule (rule_id TEXT PRIMARY KEY, rule TEXT, severity TEXT, "check" TEXT, business_reason TEXT, action TEXT);
CREATE TABLE trip_flag (
    trip_id TEXT REFERENCES fact_trip(trip_id),
    rule_id TEXT REFERENCES validation_rule(rule_id),
    PRIMARY KEY (trip_id, rule_id)
);
CREATE TABLE api_zone_pickups (
    month TEXT REFERENCES dim_month(month),
    location_id INTEGER REFERENCES dim_zone(location_id),
    borough TEXT, zone TEXT, trip_count INTEGER,
    PRIMARY KEY (month, location_id)
);
CREATE TABLE month_check (month TEXT REFERENCES dim_month(month), stage TEXT, "check" TEXT, status TEXT, detail TEXT);
CREATE VIEW trip_event AS
    SELECT trip_id, file_month, 'pickup' AS event, pickup_ts AS event_ts, pu_location_id AS location_id, request_mode, status
    FROM fact_trip
    UNION ALL
    SELECT trip_id, file_month, 'dropoff', dropoff_ts, do_location_id, request_mode, status
    FROM fact_trip
    UNION ALL
    SELECT t.trip_id, t.file_month, 'fare_reversal', t.dropoff_ts, t.pu_location_id, t.request_mode, t.status
    FROM fact_trip t JOIN trip_flag f ON f.trip_id = t.trip_id AND f.rule_id = 'F03';
"""


def load_zones():
    z = pd.read_csv(c.ZONE_CSV, keep_default_na=False)  # "N/A" is a real value here, not a null
    z = z.rename(columns={"LocationID": "location_id", "Borough": "borough", "Zone": "zone"})
    z["zone_class"] = [rules.zone_class(i, s) for i, s in zip(z["location_id"], z["service_zone"])]
    return z[["location_id", "borough", "zone", "service_zone", "zone_class"]]


def load_api(months, manifest):
    rows = []
    for m in months:
        n_pages = len(manifest.get("api", {}).get(m, {}).get("page_sha256", []))
        for page in range(1, n_pages + 1):
            for r in json.loads(c.api_page_file(m, page).read_text()):
                rows.append({"month": m, "location_id": int(r["locationid"]), "borough": r.get("borough"),
                             "zone": r.get("zone"), "trip_count": int(r["trip_count"])})
    return pd.DataFrame(rows, columns=["month", "location_id", "borough", "zone", "trip_count"])


def build_fact(flagged):
    f = pd.DataFrame({
        "trip_id": flagged["trip_id"],
        "file_month": flagged["file_month"],
        "vendor_id": flagged["VendorID"],
        "request_mode": [rules.request_mode(t) for t in flagged["trip_type"]],
        "request_source": flagged["request_source"],
        "pickup_ts": flagged["lpep_pickup_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "dropoff_ts": flagged["lpep_dropoff_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S"),
        "pickup_date": flagged["lpep_pickup_datetime"].dt.strftime("%Y-%m-%d"),
        "pickup_hour": flagged["lpep_pickup_datetime"].dt.hour,
        "pu_location_id": flagged["PULocationID"],
        "do_location_id": flagged["DOLocationID"],
        "passenger_count": flagged["passenger_count"],
        "trip_distance": flagged["trip_distance"],
        "duration_min": flagged["duration_min"].round(3),
        "speed_mph": flagged["speed_mph"].round(3),
        "payment_type": flagged["payment_type"],
        "fare_amount": flagged["fare_amount"],
        "total_amount": flagged["total_amount"],
        "status": flagged["status"],
        "duration_ok": (~flagged[rules.DURATION_WARN_IDS].any(axis=1)).astype(int),
        "pickup_known": (~flagged["W06"]).astype(int),
    })
    return f


def between_stage_checks(con, months):
    """raw rows = valid + excluded, and every trip joins to a zone. Returns month_check rows."""
    out = []
    q = """
        SELECT m.month, m.parquet_rows,
               SUM(t.status = 'valid') AS valid, SUM(t.status = 'excluded') AS excluded
        FROM dim_month m LEFT JOIN fact_trip t ON t.file_month = m.month
        GROUP BY m.month
    """
    for month, raw, valid, excluded in con.execute(q):
        valid, excluded = valid or 0, excluded or 0
        ok = raw == valid + excluded
        out.append((month, "stage3_model", "raw_equals_valid_plus_excluded", "PASS" if ok else "FAIL",
                    f"{raw} raw = {valid} valid + {excluded} excluded"))
    q = """
        SELECT t.file_month, COUNT(*) AS trips,
               SUM(pz.location_id IS NOT NULL AND dz.location_id IS NOT NULL) AS joined
        FROM fact_trip t
        LEFT JOIN dim_zone pz ON pz.location_id = t.pu_location_id
        LEFT JOIN dim_zone dz ON dz.location_id = t.do_location_id
        GROUP BY t.file_month
    """
    for month, trips, joined in con.execute(q):
        ok = trips == joined
        out.append((month, "stage3_model", "zone_join_coverage_100pct", "PASS" if ok else "FAIL",
                    f"{joined}/{trips} trips join to dim_zone on pickup and dropoff"))
    # a vendor that stops sending records before month end means the month is incomplete
    q = """
        SELECT file_month, vendor_id, COUNT(*) AS trips, MAX(pickup_date) AS last_day
        FROM fact_trip
        WHERE status = 'valid'
        GROUP BY file_month, vendor_id
    """
    for month, vendor, trips, last_day in con.execute(q):
        month_end = pd.Period(month).end_time.strftime("%Y-%m-%d")
        gap_days = (pd.Timestamp(month_end) - pd.Timestamp(last_day)).days
        ok = gap_days <= 2 or trips < 300
        out.append((month, "stage3_model", f"vendor_{vendor}_reports_to_month_end", "PASS" if ok else "WARN",
                    f"{trips} valid trips, last pickup date {last_day}"))
    return out


def main(log):
    args = c.parse_args("Stage 3: build SQLite model")
    months = c.month_range(args.start, args.end)
    manifest = c.read_json(c.MANIFEST_PATH)
    month_checks = c.read_json(c.MONTH_CHECKS_PATH)
    if manifest is None or month_checks is None or not c.FLAGGED_PATH.exists():
        raise c.PipelineError("stage 2 output missing, run stage2_validate.py first")

    flagged = pd.read_parquet(c.FLAGGED_PATH)
    flagged = flagged[flagged["file_month"].isin(months)]
    # a month can only be missing here if stage 2 failed it (bad checksum, unreadable file)
    failed = {m for m in months if any(ch["status"] == "FAIL" for ch in month_checks.get(m, []))}
    missing = sorted(set(months) - set(flagged["file_month"]) - failed)
    if missing:
        raise c.PipelineError(f"stage 2 output has no rows for {missing}, rerun stage2_validate.py for this range")
    zones = load_zones()
    api = load_api(months, manifest)
    fact = build_fact(flagged)
    flag_rows = flagged.melt(id_vars="trip_id", value_vars=rules.RULE_IDS, var_name="rule_id", value_name="hit")
    flag_rows = flag_rows[flag_rows["hit"]][["trip_id", "rule_id"]].sort_values(["trip_id", "rule_id"])

    dim_month = pd.DataFrame([{
        "month": m,
        "source_url": manifest["trip_files"][m]["url"],
        "size_bytes": manifest["trip_files"][m]["size_bytes"],
        "sha256": manifest["trip_files"][m]["sha256"],
        "parquet_rows": manifest["trip_files"][m]["parquet_rows"],
        "api_zone_rows": manifest.get("api", {}).get(m, {}).get("rows"),
    } for m in months])

    # build into a temp file and swap at the end, so a crash never leaves a half-built db
    c.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tmp = c.DB_PATH.with_suffix(".db.part")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    zones.to_sql("dim_zone", con, if_exists="append", index=False)
    dim_month.to_sql("dim_month", con, if_exists="append", index=False)
    fact.to_sql("fact_trip", con, if_exists="append", index=False, chunksize=50000)
    flag_rows.to_sql("trip_flag", con, if_exists="append", index=False, chunksize=50000)
    rules.rules_table().to_sql("validation_rule", con, if_exists="append", index=False)
    api.to_sql("api_zone_pickups", con, if_exists="append", index=False)
    con.execute("CREATE INDEX ix_trip_month ON fact_trip(file_month, status, request_mode)")

    check_rows = [(m, "stage2_validate", ch["check"], ch["status"], ch["detail"])
                  for m in months for ch in month_checks.get(m, [])]
    check_rows += between_stage_checks(con, months)
    con.executemany("INSERT INTO month_check VALUES (?, ?, ?, ?, ?)", check_rows)
    con.commit()
    con.close()
    tmp.replace(c.DB_PATH)

    for m, stage, name, status, detail in check_rows:
        if status == "FAIL":
            log.error(f"{m}: {name} FAILED ({detail})")
        elif status == "WARN":
            log.warning(f"{m}: {name} ({detail})")
    n_pass = sum(r[3] == "PASS" for r in check_rows)
    log.info(f"model done: {len(fact):,} trips, {len(flag_rows):,} flags, {len(zones)} zones, "
             f"{len(api)} API zone rows; month checks {n_pass}/{len(check_rows)} PASS")
    return 0


if __name__ == "__main__":
    c.run_stage(main, "stage3_model")
