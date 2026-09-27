import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common  # noqa: E402
import rules  # noqa: E402
import stage4_metrics  # noqa: E402


def make_trips(rows):
    df = pd.DataFrame(rows, columns=["lpep_pickup_datetime", "lpep_dropoff_datetime", "trip_distance",
                                     "total_amount", "PULocationID", "trip_type"])
    df["lpep_pickup_datetime"] = pd.to_datetime(df["lpep_pickup_datetime"])
    df["lpep_dropoff_datetime"] = pd.to_datetime(df["lpep_dropoff_datetime"])
    df["passenger_count"] = 1
    df["RatecodeID"] = 1
    return rules.add_derived(df)


def test_good_trip_passes_every_rule():
    df = make_trips([("2025-07-10 08:00", "2025-07-10 08:15", 3.0, 20.0, 74, 1)])
    flags = rules.flag_trips(df, "2025-07", zone_ids=set(range(1, 266)))
    assert not flags.iloc[0].any()
    assert rules.trip_status(flags).iloc[0] == "valid"


def test_fail_rules_exclude_the_trip():
    df = make_trips([
        ("2008-12-31 23:00", "2008-12-31 23:10", 1.0, 10.0, 74, 1),   # clock error, wrong month
        ("2025-07-10 08:15", "2025-07-10 08:00", 1.0, 10.0, 74, 1),   # dropoff before pickup
        ("2025-07-10 08:00", "2025-07-10 08:10", 1.0, -10.0, 74, 1),  # reversal row
        ("2025-07-10 08:00", "2025-07-10 08:10", 1.0, 10.0, 999, 1),  # zone not in lookup
    ])
    flags = rules.flag_trips(df, "2025-07", zone_ids=set(range(1, 266)))
    assert flags["F01"].tolist() == [True, False, False, False]
    assert flags["F02"].tolist() == [False, True, False, False]
    assert flags["F03"].tolist() == [False, False, True, False]
    assert flags["F04"].tolist() == [False, False, False, True]
    assert (rules.trip_status(flags) == "excluded").all()


def test_warn_rules_keep_the_trip():
    df = make_trips([
        ("2025-07-10 08:00", "2025-07-11 07:30", 3.0, 20.0, 74, 1),   # meter left on, 23.5 h
        ("2025-07-10 08:00", "2025-07-10 08:02", 10.0, 20.0, 74, 1),  # 300 mph
        ("2025-07-10 08:00", "2025-07-10 08:10", 1.0, 10.0, 264, None),
    ])
    flags = rules.flag_trips(df, "2025-07", zone_ids=set(range(1, 266)))
    assert flags.loc[0, "W02"] and flags.loc[1, "W05"] and flags.loc[2, "W06"] and flags.loc[2, "I01"]
    assert (rules.trip_status(flags) == "valid").all()


@pytest.mark.parametrize("location_id, service_zone, expected", [
    (74, "Boro Zone", "boro_zone"),       # East Harlem North
    (43, "Yellow Zone", "boundary"),      # Central Park touches Central Harlem
    (161, "Yellow Zone", "clear_hez"),    # Midtown Center
    (132, "Airports", "clear_hez"),       # JFK
    (1, "EWR", "clear_hez"),              # Newark
    (264, "N/A", "unknown"),
])
def test_zone_class(location_id, service_zone, expected):
    assert rules.zone_class(location_id, service_zone) == expected


def test_request_mode():
    assert rules.request_mode(1) == "street_hail"
    assert rules.request_mode(2) == "dispatch"
    assert rules.request_mode(None) == "unclassified"
    assert rules.request_mode(float("nan")) == "unclassified"


def test_month_range_crosses_year():
    months = common.month_range("2025-11", "2026-02")
    assert months == ["2025-11", "2025-12", "2026-01", "2026-02"]
    with pytest.raises(common.PipelineError):
        common.month_range("2026-02", "2025-11")


def test_month_gate_holds_failed_month_and_warns_on_api_gap():
    ok = pd.DataFrame([{"check": "rows_read_equal_parquet_metadata", "status": "PASS", "detail": ""}])
    bad = pd.DataFrame([{"check": "raw_equals_valid_plus_excluded", "status": "FAIL", "detail": ""}])
    assert stage4_metrics.month_status(ok, 0.2)[0] == "OK"
    assert stage4_metrics.month_status(ok, -2.08)[0] == "WARN"
    assert stage4_metrics.month_status(ok, None)[0] == "WARN"
    assert stage4_metrics.month_status(bad, 0.0)[0] == "HELD"


def test_triggers_fire_only_past_thresholds():
    quiet = {"kpi_boro_zone_share_pct": 93.4, "clear_hez_hail_pct": 0.05, "unclassified_pct": 12.5,
             "unclassified_clear_hez": 560}
    assert stage4_metrics.triggers(quiet) == ""
    bad = {"kpi_boro_zone_share_pct": 91.0, "clear_hez_hail_pct": 0.3, "unclassified_pct": 16.0,
           "unclassified_clear_hez": 950}
    assert stage4_metrics.triggers(bad).count(";") == 3


def test_ingest_continues_offline_when_local_files_match(monkeypatch):
    import requests
    import stage1_ingest

    def no_network(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(stage1_ingest.requests, "request", no_network)
    monkeypatch.setattr(stage1_ingest.time, "sleep", lambda s: None)
    log = common.get_logger("test")

    monkeypatch.setattr(stage1_ingest, "have_local_copy", lambda m, manifest: True)
    assert stage1_ingest.check_all_published(["2025-07"], {}, log) == {"2025-07": None}

    monkeypatch.setattr(stage1_ingest, "have_local_copy", lambda m, manifest: False)
    with pytest.raises(common.PipelineError):
        stage1_ingest.check_all_published(["2025-07"], {}, log)
