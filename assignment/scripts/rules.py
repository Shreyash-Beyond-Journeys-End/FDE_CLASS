"""Validation rules and small classification helpers.

FAIL = the row can't be trusted as a trip in this month, so it is excluded from all metrics.
WARN = the trip still counts, but a field is suspect, so it is left out of the metric that uses that field.
INFO = recorded and counted only, no row is excluded.
Nothing is deleted: every raw row stays in fact_trip with its flags.
"""
import pandas as pd

from common import BOUNDARY_ZONES, UNKNOWN_ZONES

RULES = [
    ("F01", "pickup_outside_file_month", "FAIL",
     "pickup timestamp is not in the month of the file",
     "a monthly KPI must only count trips from that month; also catches clock errors (2008/2009 dates)",
     "excluded from all metrics"),
    ("F02", "dropoff_before_pickup", "FAIL",
     "dropoff timestamp earlier than pickup",
     "physically impossible, timestamps can't be trusted",
     "excluded from all metrics"),
    ("F03", "negative_total_amount", "FAIL",
     "total_amount < 0",
     "these are fare reversals; most copy a real trip with the same times and zones, so counting them double counts the trip",
     "excluded from all metrics"),
    ("F04", "pickup_zone_not_in_lookup", "FAIL",
     "PULocationID not found in taxi_zone_lookup",
     "can't place the pickup at all",
     "excluded from all metrics"),
    ("W01", "zero_duration", "WARN",
     "dropoff timestamp equals pickup timestamp",
     "trip may be real but its duration is not",
     "kept, left out of duration and speed metrics"),
    ("W02", "duration_over_3h", "WARN",
     "trip longer than 180 minutes",
     "almost all are about 23 hours with normal distance and fare, looks like the meter was not closed",
     "kept, left out of duration and speed metrics"),
    ("W03", "zero_distance", "WARN",
     "trip_distance = 0",
     "meter distance missing or trip cancelled after meter start",
     "kept, left out of duration and speed metrics"),
    ("W04", "distance_over_100mi", "WARN",
     "trip_distance > 100 miles",
     "not believable for a city cab trip, odometer glitch",
     "kept, left out of duration and speed metrics"),
    ("W05", "speed_over_80mph", "WARN",
     "trip_distance / duration > 80 mph",
     "distance and time can't both be right",
     "kept, left out of duration and speed metrics"),
    ("W06", "pickup_zone_unknown", "WARN",
     "PULocationID is 264 (Unknown) or 265 (Outside of NYC)",
     "we can't say if the pickup was in the permitted area",
     "kept in volume, left out of the KPI denominator"),
    ("I01", "trip_type_missing", "INFO",
     "trip_type is null (so hail vs dispatch is unknown)",
     "the KPI is about street hails; these trips can't be put in or out of it",
     "counted as 'unclassified', not in the KPI denominator"),
    ("I02", "passenger_count_missing_or_zero", "INFO",
     "passenger_count is null or 0",
     "not used by any metric, recorded for the data owner",
     "no action"),
    ("I03", "ratecode_missing_or_99", "INFO",
     "RatecodeID is null or 99",
     "not used by any metric, recorded for the data owner",
     "no action"),
]
RULE_IDS = [r[0] for r in RULES]
FAIL_IDS = [r[0] for r in RULES if r[2] == "FAIL"]
DURATION_WARN_IDS = ["W01", "W02", "W03", "W04", "W05"]


def rules_table():
    return pd.DataFrame(RULES, columns=["rule_id", "rule", "severity", "check", "business_reason", "action"])


def add_derived(df):
    df = df.copy()
    secs = (df["lpep_dropoff_datetime"] - df["lpep_pickup_datetime"]).dt.total_seconds()
    df["duration_min"] = secs / 60.0
    hours = secs / 3600.0
    df["speed_mph"] = (df["trip_distance"] / hours).where(hours > 0)
    return df


def flag_trips(df, month, zone_ids):
    """Return a DataFrame of booleans, one column per rule id. df must have add_derived() columns."""
    pickup_month = df["lpep_pickup_datetime"].dt.strftime("%Y-%m")
    f = pd.DataFrame(index=df.index)
    f["F01"] = pickup_month != month
    f["F02"] = df["lpep_dropoff_datetime"] < df["lpep_pickup_datetime"]
    f["F03"] = df["total_amount"] < 0
    f["F04"] = ~df["PULocationID"].isin(zone_ids)
    f["W01"] = df["duration_min"] == 0
    f["W02"] = df["duration_min"] > 180
    f["W03"] = df["trip_distance"] == 0
    f["W04"] = df["trip_distance"] > 100
    f["W05"] = df["speed_mph"] > 80
    f["W06"] = df["PULocationID"].isin(UNKNOWN_ZONES)
    f["I01"] = df["trip_type"].isna()
    f["I02"] = df["passenger_count"].isna() | (df["passenger_count"] == 0)
    f["I03"] = df["RatecodeID"].isna() | (df["RatecodeID"] == 99)
    return f.fillna(False).astype(bool)


def trip_status(flags):
    return flags[FAIL_IDS].any(axis=1).map({True: "excluded", False: "valid"})


def request_mode(trip_type):
    if pd.isna(trip_type):
        return "unclassified"
    return {1: "street_hail", 2: "dispatch"}.get(int(trip_type), "unclassified")


def zone_class(location_id, service_zone):
    """Where a pickup sits relative to the green taxi street-hail rules."""
    if location_id in UNKNOWN_ZONES or service_zone == "N/A":
        return "unknown"
    if service_zone == "Boro Zone":
        return "boro_zone"
    if location_id in BOUNDARY_ZONES:
        return "boundary"
    return "clear_hez"  # rest of the Yellow Zone, JFK, LaGuardia and Newark
