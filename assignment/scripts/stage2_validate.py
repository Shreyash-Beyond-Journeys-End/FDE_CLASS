"""Stage 2: read raw parquet, check each month, flag every row against the rules.

Output:
  data/processed/flagged_trips.parquet  every raw row + trip_id + derived fields + rule flags
  data/processed/month_checks.json      month-level checks used as gates later
  outputs/validation_report.csv         rule x month counts
"""
import pandas as pd
import pyarrow.parquet as pq

import common as c
import rules

KEEP_EXTRA = ["request_source"]  # appeared in 2026-06, not in the data dictionary


def check(name, ok, detail, fail_status="FAIL"):
    return {"check": name, "status": "PASS" if ok else fail_status, "detail": detail}


def validate_month(month, manifest, zone_ids, max_fail_share, log):
    path = c.trip_file(month)
    if not path.exists():
        raise c.PipelineError(f"{month}: raw file {path.name} not found, run stage1_ingest.py first")

    checks = []
    expected_sha = manifest["trip_files"][month]["sha256"]
    sha_ok = c.sha256_of(path) == expected_sha
    checks.append(check("raw_checksum_matches_manifest", sha_ok, expected_sha[:12]))
    if not sha_ok:
        return None, checks, []

    try:
        df = pq.read_table(path).to_pandas()
    except Exception as e:  # a broken parquet file should hold the month, not crash the run
        checks.append(check("parquet_file_readable", False, str(e)[:200]))
        return None, checks, []
    meta_rows = pq.ParquetFile(path).metadata.num_rows
    checks.append(check("rows_read_equal_parquet_metadata", len(df) == meta_rows, f"{len(df)} read vs {meta_rows} in metadata"))

    missing = [col for col in c.REQUIRED_COLUMNS if col not in df.columns]
    checks.append(check("required_columns_present", not missing, f"missing: {missing}" if missing else "all present"))
    if missing:
        log.error(f"{month}: missing required columns {missing}, month cannot be validated")
        return None, checks, []
    extra = [col for col in df.columns if col not in c.DOCUMENTED_COLUMNS]
    checks.append(check("no_undocumented_columns", not extra, f"extra: {extra}" if extra else "none", "INFO"))
    if extra:
        log.info(f"{month}: columns not in the data dictionary: {extra}")

    df = rules.add_derived(df)
    flags = rules.flag_trips(df, month, zone_ids)
    df["status"] = rules.trip_status(flags)
    df = pd.concat([df, flags], axis=1)
    df.insert(0, "trip_id", [f"{month}-{i:06d}" for i in range(len(df))])
    df.insert(1, "file_month", month)
    for col in KEEP_EXTRA:
        if col not in df.columns:
            df[col] = None

    fail_share = (df["status"] == "excluded").mean()
    checks.append(check("fail_share_below_limit", fail_share <= max_fail_share,
                        f"{fail_share:.2%} of rows hit a FAIL rule (limit {max_fail_share:.2%})"))

    counts = []
    for rule_id, name, severity, *_rest in rules.RULES:
        n = int(df[rule_id].sum())
        counts.append({"month": month, "rule_id": rule_id, "rule": name, "severity": severity,
                       "rows_flagged": n, "pct_of_rows": round(100 * n / len(df), 3)})
    log.info(f"{month}: {len(df):,} rows, {int((df['status'] == 'excluded').sum()):,} excluded by FAIL rules "
             f"({fail_share:.2%}), {int(df['W06'].sum())} unknown pickup zone, {int(df['I01'].sum()):,} missing trip_type")
    return df, checks, counts


def main(log):
    args = c.parse_args("Stage 2: validate raw trips")
    months = c.month_range(args.start, args.end)
    manifest = c.read_json(c.MANIFEST_PATH)
    if manifest is None:
        raise c.PipelineError("no manifest found, run stage1_ingest.py first")
    missing = [m for m in months if m not in manifest["trip_files"]]
    if missing:
        raise c.PipelineError(f"months not ingested: {missing}")

    # remove the old stage 2 output first, so stages 3 and 4 can never use a stale copy
    c.FLAGGED_PATH.unlink(missing_ok=True)
    c.MONTH_CHECKS_PATH.unlink(missing_ok=True)

    zones = pd.read_csv(c.ZONE_CSV)
    zone_ids = set(zones["LocationID"])

    frames, month_checks, all_counts = [], {}, []
    for month in months:
        df, checks, counts = validate_month(month, manifest, zone_ids, args.max_fail_share, log)
        month_checks[month] = checks
        if df is not None:
            frames.append(df)
            all_counts.extend(counts)
        for ch in checks:
            if ch["status"] == "FAIL":
                log.error(f"{month}: check {ch['check']} FAILED ({ch['detail']}), month will be held")

    if not frames:
        raise c.PipelineError("no month could be validated")
    flagged = pd.concat(frames, ignore_index=True)
    c.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    flagged.to_parquet(c.FLAGGED_PATH, index=False)
    c.write_json(c.MONTH_CHECKS_PATH, month_checks)

    report = pd.DataFrame(all_counts)
    totals = report.groupby(["rule_id", "rule", "severity"], as_index=False)["rows_flagged"].sum()
    totals["month"] = "ALL"
    totals["pct_of_rows"] = (100 * totals["rows_flagged"] / len(flagged)).round(3)
    report = pd.concat([report, totals[report.columns]], ignore_index=True)
    report = report.merge(rules.rules_table()[["rule_id", "action"]], on="rule_id")
    report.to_csv(c.OUTPUT_DIR / "validation_report.csv", index=False)
    rules.rules_table().to_csv(c.OUTPUT_DIR / "validation_rules.csv", index=False)

    log.info(f"validate done: {len(flagged):,} rows flagged, "
             f"{int((flagged['status'] == 'excluded').sum()):,} excluded, report in outputs/validation_report.csv")
    return 0


if __name__ == "__main__":
    c.run_stage(main, "stage2_validate")
