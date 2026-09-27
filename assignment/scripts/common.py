"""Shared paths, settings and helpers for the pipeline stages."""
import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
TRIP_DIR = RAW_DIR / "green"
API_DIR = RAW_DIR / "api"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"

MANIFEST_PATH = RAW_DIR / "manifest.json"
ZONE_CSV = RAW_DIR / "taxi_zone_lookup.csv"
FLAGGED_PATH = PROCESSED_DIR / "flagged_trips.parquet"
MONTH_CHECKS_PATH = PROCESSED_DIR / "month_checks.json"
DB_PATH = PROCESSED_DIR / "green_taxi.db"

TRIP_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/green_tripdata_{month}.parquet"
ZONE_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
# NYC Open Data: "Pickups and Drop-offs by Taxi Zone and Industry" (published by TLC)
API_URL = "https://data.cityofnewyork.us/resource/c5iv-bn4s.json"
# NYC Open Data: "NYC Taxi Zones" shapes, used in notebook 1 to check the boundary zones
ZONE_SHAPES_URL = "https://data.cityofnewyork.us/resource/8meu-9t5y.geojson?$limit=500"
ZONE_SHAPES = RAW_DIR / "taxi_zones.geojson"

DEFAULT_START = "2025-07"
DEFAULT_END = "2026-06"

# Yellow Zone taxi zones whose polygon touches a Boro Zone polygon (checked against the
# NYC Taxi Zones shapes in notebook 01). A pickup coded to one of these could be right on
# the 96th St / 110th St line, so the zone alone can't show it was inside the exclusionary zone.
BOUNDARY_ZONES = {24, 43, 194, 236, 262, 263}
UNKNOWN_ZONES = {264, 265}

# Decision triggers, checked every month in stage 4. Set from the 12 months 2025-07..2026-06
# (reasons in docs/metric_definitions.md):
TRIGGER_KPI_BELOW = 92.0                   # mean 93.41 - 3 SD (0.45) = 92.08, rounded down
TRIGGER_CLEAR_HEZ_ABOVE = 0.2              # mean + 3 SD is only 0.11% (~40 hails), so about 2x the worst month
TRIGGER_UNCLASSIFIED_ABOVE = 15.0          # a fixed level, not statistical: the share is rising
TRIGGER_UNCLASSIFIED_CLEAR_HEZ_ABOVE = 900  # mean 562 + 3 SD (107) = 884, rounded up

# Month-level thresholds
MAX_FAIL_SHARE = 0.02      # hold a month if more than 2% of its rows fail a FAIL rule
MAX_API_GAP = 0.01         # warn if our pickup count differs from TLC's aggregate by more than 1%

REQUIRED_COLUMNS = [
    "VendorID", "lpep_pickup_datetime", "lpep_dropoff_datetime", "RatecodeID",
    "PULocationID", "DOLocationID", "passenger_count", "trip_distance",
    "fare_amount", "total_amount", "payment_type", "trip_type",
]
DOCUMENTED_COLUMNS = REQUIRED_COLUMNS + [
    "store_and_fwd_flag", "extra", "mta_tax", "tip_amount", "tolls_amount", "ehail_fee",
    "improvement_surcharge", "congestion_surcharge", "cbd_congestion_fee",
]


class PipelineError(Exception):
    pass


def month_range(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    if (y, m) > (ey, em):
        raise PipelineError(f"--start {start} is after --end {end}")
    months = []
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def parse_args(description):
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--start", default=DEFAULT_START, help="first month, YYYY-MM")
    p.add_argument("--end", default=DEFAULT_END, help="last month, YYYY-MM")
    p.add_argument("--refresh-api", action="store_true", help="re-fetch the Open Data API even if cached")
    p.add_argument("--max-fail-share", type=float, default=MAX_FAIL_SHARE,
                   help="hold a month if more than this share of its rows hit a FAIL rule (default 0.02)")
    return p.parse_args()


def get_logger(stage):
    """Log to the console and to logs/pipeline.log. The logger name is the stage name."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[logging.FileHandler(LOG_DIR / "pipeline.log"), logging.StreamHandler(sys.stdout)],
        )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    return logging.getLogger(stage)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def trip_file(month):
    return TRIP_DIR / f"green_tripdata_{month}.parquet"


def api_page_file(month, page):
    return API_DIR / f"green_zone_pickups_{month}_page{page}.json"


def run_stage(main, stage):
    """Run a stage's main() and turn errors into a logged message and exit code 1."""
    log = get_logger(stage)
    try:
        code = main(log)
    except PipelineError as e:
        log.error(str(e))
        sys.exit(1)
    sys.exit(code or 0)
