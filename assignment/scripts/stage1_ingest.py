"""Stage 1: download raw inputs and check the download is complete.

Sources:
  - TLC green trip parquet files (HTTPS file download, one per month)
  - TLC taxi zone lookup CSV (HTTPS file download)
  - NYC Open Data SODA API, dataset c5iv-bn4s (monthly Green Cab pickups per zone)
  - NYC Open Data SODA API, dataset 8meu-9t5y (taxi zone shapes, used in notebook 1)

Raw files are never edited. data/raw/manifest.json records size, sha256, parquet row count
and API row counts, so a rerun can tell if anything changed.
"""
import json
import time
from datetime import datetime, timezone

import pyarrow.parquet as pq
import requests

import common as c

RETRIES = 3
TIMEOUT = 60


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_with_retries(url, log, params=None, method="GET"):
    """GET or HEAD with 3 attempts. 403/404 is returned without retrying (file not published)."""
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.request(method, url, params=params, timeout=TIMEOUT)
            if r.status_code in (403, 404):
                return r
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == RETRIES:
                raise c.PipelineError(f"{url}: failed after {RETRIES} attempts ({e})")
            wait = 2 ** attempt
            log.warning(f"{url}: attempt {attempt} failed ({e}), retrying in {wait}s")
            time.sleep(wait)


def have_local_copy(month, manifest):
    entry = manifest.get("trip_files", {}).get(month)
    path = c.trip_file(month)
    return bool(entry) and path.exists() and c.sha256_of(path) == entry["sha256"]


def check_all_published(months, manifest, log):
    """HEAD every requested month first, so a missing month stops the run before anything is downloaded.

    If the server can't be reached but every month is already on disk and matches the manifest,
    carry on offline with the local copies (sizes come back as None).
    """
    sizes, missing = {}, []
    for month in months:
        try:
            r = get_with_retries(c.TRIP_URL.format(month=month), log, method="HEAD")
        except c.PipelineError as e:
            if all(have_local_copy(m, manifest) for m in months):
                log.warning(f"can't reach TLC ({e}); all months are on disk and match the manifest, "
                            f"continuing offline with the local copies")
                return {m: None for m in months}
            raise
        if r.status_code in (403, 404):
            missing.append(month)
        else:
            sizes[month] = int(r.headers["Content-Length"])
    if missing:
        raise c.PipelineError(f"not published by TLC yet: {missing}. Nothing was downloaded.")
    log.info(f"all {len(months)} requested months are published")
    return sizes


def download(url, dest, log):
    r = get_with_retries(url, log)
    if r.status_code in (403, 404):
        raise c.PipelineError(f"{url} returned HTTP {r.status_code}")
    # requests un-gzips text files, so only compare with Content-Length when nothing was compressed
    expected = r.headers.get("Content-Length")
    if expected and not r.headers.get("Content-Encoding") and int(expected) != len(r.content):
        raise c.PipelineError(f"{dest.name}: got {len(r.content)} bytes, expected {expected}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(r.content)
    part.replace(dest)
    log.info(f"downloaded {dest.name} ({len(r.content):,} bytes)")
    return {"url": url, "size_bytes": dest.stat().st_size, "sha256": c.sha256_of(dest), "downloaded_at": now()}


def ingest_file(url, dest, entry, remote_size, log):
    """Download unless we already have the same file. remote_size is None when the server didn't say."""
    if dest.exists() and entry:
        if c.sha256_of(dest) != entry["sha256"]:
            log.warning(f"{dest.name}: local file does not match manifest checksum, downloading again")
        elif remote_size is not None and remote_size != entry["size_bytes"]:
            log.warning(f"{dest.name}: size on server changed {entry['size_bytes']:,} -> {remote_size:,}, "
                        f"TLC re-posted it, downloading again")
        else:
            log.info(f"{dest.name}: already present and matches the manifest, skipping download")
            return entry
    return download(url, dest, log)


def ingest_api_month(month, old, refresh, log):
    """Fetch TLC's own monthly Green Cab pickups per zone. Each page is saved exactly as received."""
    if old and not refresh:
        files_ok = all(c.api_page_file(month, i + 1).exists() and
                       c.sha256_of(c.api_page_file(month, i + 1)) == sha
                       for i, sha in enumerate(old["page_sha256"]))
        if files_ok:
            log.info(f"API {month}: using cached response ({old['rows']} rows, use --refresh-api to re-fetch)")
            return old

    where = f"industry='Green Cab' AND pickup_dropoff='Pick-up' AND metric_month='{month}-01'"
    count = get_with_retries(c.API_URL, log, {"$select": "count(*) AS n", "$where": where}).json()
    expected = int(count[0]["n"])

    rows, page, page_size, shas = 0, 1, 1000, []
    while True:
        params = {"$where": where, "$order": "locationid", "$limit": page_size, "$offset": (page - 1) * page_size}
        r = get_with_retries(c.API_URL, log, params)
        path = c.api_page_file(month, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        shas.append(c.sha256_of(path))
        n = len(r.json())
        rows += n
        if n < page_size:
            break
        page += 1

    if rows != expected:
        raise c.PipelineError(f"API {month}: got {rows} rows but count(*) says {expected}")
    if expected == 0:
        log.warning(f"API {month}: TLC has not published this month in the aggregate dataset yet")
    log.info(f"API {month}: saved {rows} zone rows in {len(shas)} page(s) (count(*) = {expected})")
    return {"rows": rows, "expected_rows": expected, "page_sha256": shas, "fetched_at": now()}


def main(log):
    args = c.parse_args("Stage 1: ingest raw files and API data")
    months = c.month_range(args.start, args.end)
    log.info(f"ingest months {months[0]} .. {months[-1]} ({len(months)} months)")
    manifest = c.read_json(c.MANIFEST_PATH, default={})
    for key in ["trip_files", "api"]:
        manifest.setdefault(key, {})
    remote_sizes = check_all_published(months, manifest, log)

    # zone lookup (static CSV, the server may gzip it so there is no reliable size)
    manifest["zone_lookup"] = ingest_file(c.ZONE_URL, c.ZONE_CSV, manifest.get("zone_lookup"), None, log)
    n_zones = sum(1 for _ in open(c.ZONE_CSV)) - 1
    if n_zones != 265:
        raise c.PipelineError(f"zone lookup has {n_zones} rows, expected 265")
    manifest["zone_lookup"]["rows"] = n_zones

    # zone shapes (only used in notebook 1)
    manifest["zone_shapes"] = ingest_file(c.ZONE_SHAPES_URL, c.ZONE_SHAPES, manifest.get("zone_shapes"), None, log)
    manifest["zone_shapes"]["features"] = len(json.loads(c.ZONE_SHAPES.read_text())["features"])

    for month in months:
        url = c.TRIP_URL.format(month=month)
        entry = ingest_file(url, c.trip_file(month), manifest["trip_files"].get(month), remote_sizes[month], log)
        if remote_sizes[month] is not None:
            entry["remote_content_length"] = remote_sizes[month]
        entry["parquet_rows"] = pq.ParquetFile(c.trip_file(month)).metadata.num_rows
        manifest["trip_files"][month] = entry
        c.write_json(c.MANIFEST_PATH, manifest)
        log.info(f"{month}: parquet metadata says {entry['parquet_rows']:,} rows")

    # API cross-check. If it is down we carry on and the month gets a WARN later.
    for month in months:
        try:
            manifest["api"][month] = ingest_api_month(month, manifest["api"].get(month), args.refresh_api, log)
        except c.PipelineError as e:
            log.warning(f"{e}; month {month} will be published without the API cross-check")
            manifest["api"].pop(month, None)

    c.write_json(c.MANIFEST_PATH, manifest)

    # committed copy of the manifest, without download times
    c.OUTPUT_DIR.mkdir(exist_ok=True)
    drop = {"downloaded_at", "fetched_at"}
    evidence = {
        "zone_lookup": {k: v for k, v in manifest["zone_lookup"].items() if k not in drop},
        "zone_shapes": {k: v for k, v in manifest["zone_shapes"].items() if k not in drop},
        "trip_files": {m: {k: v for k, v in manifest["trip_files"][m].items() if k not in drop} for m in months},
        "api": {m: {k: v for k, v in manifest["api"][m].items() if k not in drop}
                for m in months if m in manifest["api"]},
    }
    c.write_json(c.OUTPUT_DIR / "raw_manifest.json", evidence)
    total = sum(manifest["trip_files"][m]["parquet_rows"] for m in months)
    log.info(f"ingest done: {len(months)} trip files, {total:,} rows in parquet metadata, "
             f"{len(evidence['api'])} API months")
    return 0


if __name__ == "__main__":
    c.run_stage(main, "stage1_ingest")
