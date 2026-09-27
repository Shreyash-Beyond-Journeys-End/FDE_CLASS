"""Stage 4: compute the monthly metrics in SQL and write the evidence outputs.

A month is HELD (no metric values published) if any FAIL check fired for it.
A month is published with WARN if the API cross-check is off by more than 1%,
the API had no data, or a vendor stopped reporting before month end.
Each published month is also checked against the decision triggers in common.py.
"""
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import common as c

MONTHLY_SQL = """
WITH t AS (
    SELECT f.*, z.zone_class
    FROM fact_trip f LEFT JOIN dim_zone z ON z.location_id = f.pu_location_id
)
SELECT
    m.month,
    m.parquet_rows                                                        AS raw_rows,
    SUM(t.status = 'excluded')                                            AS excluded_rows,
    SUM(t.status = 'valid')                                               AS valid_trips,
    SUM(t.status = 'valid' AND t.request_mode = 'street_hail')            AS street_hails,
    SUM(t.status = 'valid' AND t.request_mode = 'dispatch')               AS dispatch_trips,
    SUM(t.status = 'valid' AND t.request_mode = 'unclassified')           AS unclassified_trips,
    SUM(t.status = 'valid' AND t.request_mode = 'unclassified' AND t.zone_class = 'clear_hez') AS unclassified_clear_hez,
    SUM(t.status = 'valid' AND t.request_mode = 'street_hail' AND t.pickup_known = 1) AS hails_known_zone,
    SUM(t.status = 'valid' AND t.request_mode = 'street_hail' AND t.zone_class = 'boro_zone') AS hails_boro_zone,
    SUM(t.status = 'valid' AND t.request_mode = 'street_hail' AND t.zone_class = 'boundary')  AS hails_boundary,
    SUM(t.status = 'valid' AND t.request_mode = 'street_hail' AND t.zone_class = 'clear_hez') AS hails_clear_hez,
    SUM(t.pickup_known = 1)                                               AS raw_rows_known_zone
FROM dim_month m LEFT JOIN t ON t.file_month = m.month
GROUP BY m.month
ORDER BY m.month
"""

# median via row numbers, SQLite has no MEDIAN()
MEDIAN_SQL = """
WITH x AS (
    SELECT f.file_month AS month, f.{col} AS v,
           ROW_NUMBER() OVER (PARTITION BY f.file_month ORDER BY f.{col}) AS rn,
           COUNT(*) OVER (PARTITION BY f.file_month) AS n
    FROM fact_trip f JOIN dim_zone z ON z.location_id = f.pu_location_id
    WHERE f.status = 'valid' AND f.request_mode = 'street_hail'
      AND z.zone_class = 'boro_zone' AND f.duration_ok = 1
)
SELECT month, AVG(v) AS median_{col}
FROM x WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
GROUP BY month ORDER BY month
"""

POOLED_MEDIAN_SQL = """
WITH x AS (
    SELECT f.{col} AS v,
           ROW_NUMBER() OVER (ORDER BY f.{col}) AS rn,
           COUNT(*) OVER () AS n
    FROM fact_trip f JOIN dim_zone z ON z.location_id = f.pu_location_id
    WHERE f.status = 'valid' AND f.request_mode = 'street_hail'
      AND z.zone_class = 'boro_zone' AND f.duration_ok = 1
      AND f.file_month IN ({months})
)
SELECT AVG(v) FROM x WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
"""

API_SQL = "SELECT month, SUM(trip_count) AS api_pickups FROM api_zone_pickups GROUP BY month"

HEZ_ZONE_SQL = """
SELECT z.location_id, z.zone, z.zone_class,
       COUNT(*) AS street_hails,
       ROUND(100.0 * SUM(dz.service_zone = 'Yellow Zone') / COUNT(*), 1) AS pct_dropoff_in_yellow_zone
FROM fact_trip f
JOIN dim_zone z  ON z.location_id = f.pu_location_id
JOIN dim_zone dz ON dz.location_id = f.do_location_id
WHERE f.status = 'valid' AND f.request_mode = 'street_hail'
  AND z.zone_class IN ('boundary', 'clear_hez')
  AND f.file_month IN ({months})
GROUP BY z.location_id, z.zone, z.zone_class
ORDER BY street_hails DESC, z.location_id
"""

HEZ_HOUR_SQL = """
SELECT f.pickup_hour AS hour,
       SUM(z.zone_class = 'boundary')  AS boundary_hails,
       SUM(z.zone_class = 'clear_hez') AS clear_hez_hails,
       SUM(z.zone_class = 'boro_zone') AS boro_zone_hails
FROM fact_trip f JOIN dim_zone z ON z.location_id = f.pu_location_id
WHERE f.status = 'valid' AND f.request_mode = 'street_hail' AND f.file_month IN ({months})
GROUP BY f.pickup_hour ORDER BY f.pickup_hour
"""

# zone x hour band for street hails outside the Boro Zone: where and when a field check would go
TARGET_SQL = """
SELECT z.zone, z.location_id, z.zone_class,
       CASE WHEN f.pickup_hour <= 6 THEN '00-06'
            WHEN f.pickup_hour <= 10 THEN '07-10'
            WHEN f.pickup_hour <= 14 THEN '11-14'
            WHEN f.pickup_hour <= 18 THEN '15-18'
            ELSE '19-23' END AS hour_band,
       COUNT(*) AS street_hails
FROM fact_trip f JOIN dim_zone z ON z.location_id = f.pu_location_id
WHERE f.status = 'valid' AND f.request_mode = 'street_hail'
  AND z.zone_class IN ('boundary', 'clear_hez')
  AND f.file_month IN ({months})
GROUP BY z.location_id, hour_band
ORDER BY street_hails DESC, z.location_id, hour_band
"""


def pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def month_status(checks, api_gap_pct):
    fails = checks[checks["status"] == "FAIL"]["check"].tolist()
    if fails:
        return "HELD", "FAIL: " + ", ".join(fails)
    notes = []
    if api_gap_pct is None or pd.isna(api_gap_pct):
        notes.append("no API data to cross-check")
    elif abs(api_gap_pct) > 100 * c.MAX_API_GAP:
        notes.append(f"volume {api_gap_pct:+.2f}% vs TLC aggregate")
    notes += [f"{r.check} ({r.detail})" for r in checks[checks["status"] == "WARN"].itertuples()]
    if notes:
        return "WARN", "; ".join(notes)
    return "OK", ""


def triggers(row):
    """Which decision triggers fire for one published month."""
    fired = []
    if row["kpi_boro_zone_share_pct"] < c.TRIGGER_KPI_BELOW:
        fired.append(f"KPI below {c.TRIGGER_KPI_BELOW}%: review this month's intervention targets")
    if row["clear_hez_hail_pct"] > c.TRIGGER_CLEAR_HEZ_ABOVE:
        fired.append(f"clear HEZ hails above {c.TRIGGER_CLEAR_HEZ_ABOVE}%: field check at the top clear zones")
    if row["unclassified_pct"] > c.TRIGGER_UNCLASSIFIED_ABOVE:
        fired.append(f"unclassified trips above {c.TRIGGER_UNCLASSIFIED_ABOVE}%: raise with the vendor data team")
    if row["unclassified_clear_hez"] > c.TRIGGER_UNCLASSIFIED_CLEAR_HEZ_ABOVE:
        fired.append(f"over {c.TRIGGER_UNCLASSIFIED_CLEAR_HEZ_ABOVE} unclassified trips in clear HEZ: raise with the vendor data team")
    return "; ".join(fired)


def build_monthly(con, months):
    m = pd.read_sql(MONTHLY_SQL, con)
    missing = sorted(set(months) - set(m["month"]))
    if missing:
        raise c.PipelineError(f"no data in the model for {missing}, rerun the earlier stages for this range")
    m = m[m["month"].isin(months)]
    for col in ["duration_min", "speed_mph"]:
        m = m.merge(pd.read_sql(MEDIAN_SQL.format(col=col), con), on="month", how="left")
    m = m.merge(pd.read_sql(API_SQL, con), on="month", how="left")
    checks = pd.read_sql("SELECT * FROM month_check", con)

    m["api_gap_pct"] = [round(100 * (r / a - 1), 2) if a and a > 0 else None
                        for r, a in zip(m["raw_rows_known_zone"], m["api_pickups"])]
    m["kpi_boro_zone_share_pct"] = [pct(a, b) for a, b in zip(m["hails_boro_zone"], m["hails_known_zone"])]
    m["boundary_hail_pct"] = [pct(a, b) for a, b in zip(m["hails_boundary"], m["hails_known_zone"])]
    m["clear_hez_hail_pct"] = [pct(a, b) for a, b in zip(m["hails_clear_hez"], m["hails_known_zone"])]
    m["excluded_pct"] = [pct(a, b) for a, b in zip(m["excluded_rows"], m["raw_rows"])]
    m["unclassified_pct"] = [pct(a, b) for a, b in zip(m["unclassified_trips"], m["valid_trips"])]
    m["days"] = [pd.Period(x).days_in_month for x in m["month"]]
    m["valid_trips_per_day"] = (m["valid_trips"] / m["days"]).round(0)
    m["median_duration_min"] = m["median_duration_min"].round(2)
    m["median_speed_mph"] = m["median_speed_mph"].round(2)

    status = [month_status(checks[checks["month"] == x], g) for x, g in zip(m["month"], m["api_gap_pct"])]
    m["publish_status"] = [s for s, _ in status]
    m["status_note"] = [n for _, n in status]
    m["triggers_fired"] = [triggers(r) if r["publish_status"] != "HELD" else "n/a (held)" for _, r in m.iterrows()]

    cols = ["month", "publish_status", "raw_rows", "excluded_rows", "excluded_pct", "valid_trips",
            "valid_trips_per_day", "api_pickups", "raw_rows_known_zone", "api_gap_pct",
            "street_hails", "dispatch_trips", "unclassified_trips", "unclassified_pct", "unclassified_clear_hez",
            "hails_known_zone", "hails_boro_zone", "hails_boundary", "hails_clear_hez",
            "kpi_boro_zone_share_pct", "boundary_hail_pct", "clear_hez_hail_pct",
            "median_duration_min", "median_speed_mph", "status_note", "triggers_fired"]
    m = m[cols].reset_index(drop=True)
    # held months keep their raw counts and excluded share for debugging, every other metric is blanked
    metric_cols = cols[5:-2]
    m.loc[m["publish_status"] == "HELD", metric_cols] = None
    count_cols = ["raw_rows", "excluded_rows", "valid_trips", "valid_trips_per_day", "api_pickups",
                  "raw_rows_known_zone", "street_hails", "dispatch_trips", "unclassified_trips",
                  "unclassified_clear_hez", "hails_known_zone", "hails_boro_zone", "hails_boundary",
                  "hails_clear_hez"]
    m[count_cols] = m[count_cols].astype("Int64")
    return m


def pooled(m):
    s = m[m["publish_status"] != "HELD"].sum(numeric_only=True)
    return {
        "valid_trips": int(s["valid_trips"]),
        "kpi": pct(s["hails_boro_zone"], s["hails_known_zone"]),
        "boundary": pct(s["hails_boundary"], s["hails_known_zone"]),
        "clear": pct(s["hails_clear_hez"], s["hails_known_zone"]),
        "hails_boundary": int(s["hails_boundary"]),
        "hails_clear": int(s["hails_clear_hez"]),
        "excluded": pct(s["excluded_rows"], s["raw_rows"]),
        "unclassified": pct(s["unclassified_trips"], s["valid_trips"]),
        "unclassified_clear_hez": int(s["unclassified_clear_hez"]),
    }


def evidence_markdown(m, p, targets, max_fail_share):
    pub = m[m["publish_status"] != "HELD"]
    first, last = pub.iloc[0], pub.iloc[-1]
    f, l = first["month"], last["month"]
    metrics = pd.DataFrame([
        ["1 (KPI)", "Street hails picked up in the Boro Zone, % of hails with a known zone",
         f"{first['kpi_boro_zone_share_pct']:.2f}%", f"{last['kpi_boro_zone_share_pct']:.2f}%", f"{p['kpi']:.2f}%",
         f"below {c.TRIGGER_KPI_BELOW}% in a month: review that month's intervention targets"],
        ["2", "Street hails in a boundary Yellow Zone, % of hails",
         f"{first['boundary_hail_pct']:.2f}%", f"{last['boundary_hail_pct']:.2f}%",
         f"{p['boundary']:.2f}% ({p['hails_boundary']:,})", "no trigger, used to rank zones and hours"],
        ["3", "Street hails clearly inside the exclusionary zone or at an airport, % of hails",
         f"{first['clear_hez_hail_pct']:.2f}%", f"{last['clear_hez_hail_pct']:.2f}%",
         f"{p['clear']:.2f}% ({p['hails_clear']:,})",
         f"above {c.TRIGGER_CLEAR_HEZ_ABOVE}% in a month: field check at the top clear zones"],
        ["4", "Valid trips with no trip_type (the KPI can't see them), % of valid trips, "
              "and how many start clearly inside the exclusionary zone",
         f"{first['unclassified_pct']:.2f}% ({first['unclassified_clear_hez']:,} in clear HEZ)",
         f"{last['unclassified_pct']:.2f}% ({last['unclassified_clear_hez']:,} in clear HEZ)",
         f"{p['unclassified']:.2f}% ({p['unclassified_clear_hez']:,} in clear HEZ)",
         f"above {c.TRIGGER_UNCLASSIFIED_ABOVE}%, or over {c.TRIGGER_UNCLASSIFIED_CLEAR_HEZ_ABOVE} in clear HEZ, "
         f"in a month: raise with the vendor data team"],
        ["5", "Raw rows excluded by FAIL rules, % of raw rows",
         f"{first['excluded_pct']:.2f}%", f"{last['excluded_pct']:.2f}%", f"{p['excluded']:.2f}%",
         f"above {100 * max_fail_share:g}% in a month: month is held and not published"],
    ], columns=["#", "Metric", f, l, "Pooled", "Decision trigger"])

    context = pd.DataFrame([
        ["Valid green trips per day", f"{int(first['valid_trips_per_day']):,}", f"{int(last['valid_trips_per_day']):,}",
         f"{p['valid_trips']:,} trips in total"],
        ["File rows vs TLC's published count (API)", f"{first['api_gap_pct']:+.2f}%", f"{last['api_gap_pct']:+.2f}%",
         "see monthly table"],
        ["Median duration / speed of Boro Zone street hails",
         f"{first['median_duration_min']:.2f} min / {first['median_speed_mph']:.2f} mph",
         f"{last['median_duration_min']:.2f} min / {last['median_speed_mph']:.2f} mph",
         f"{p['median_duration_min']:.2f} min / {p['median_speed_mph']:.2f} mph"],
    ], columns=["Context (not a metric)", f, l, "Pooled"])

    monthly = m[["month", "publish_status", "kpi_boro_zone_share_pct", "boundary_hail_pct", "clear_hez_hail_pct",
                 "unclassified_pct", "unclassified_clear_hez", "excluded_pct", "valid_trips_per_day", "api_gap_pct",
                 "triggers_fired"]].copy()
    monthly.columns = ["Month", "Status", "1 KPI %", "2 Boundary %", "3 Clear HEZ %", "4 Unclassified %",
                       "4 Unclassified in clear HEZ", "5 Excluded %", "Trips/day", "API gap %", "Triggers fired"]
    for col in ["1 KPI %", "2 Boundary %", "3 Clear HEZ %", "4 Unclassified %", "5 Excluded %", "API gap %"]:
        monthly[col] = ["held" if pd.isna(x) else f"{x:.2f}" for x in monthly[col]]
    for col in ["4 Unclassified in clear HEZ", "Trips/day"]:
        monthly[col] = ["held" if pd.isna(x) else f"{int(x):,}" for x in monthly[col]]
    monthly["Triggers fired"] = monthly["Triggers fired"].replace("", "none")

    top = targets.head(10).copy()
    top["% of these hails"] = (100 * top["street_hails"] / targets["street_hails"].sum()).round(1)
    top = top[["zone", "location_id", "zone_class", "hour_band", "street_hails", "hails_per_week", "% of these hails"]]

    held = m[m["publish_status"] == "HELD"]["month"].tolist()
    notes = [f"- {r.month} ({r.publish_status}): {r.status_note}" for r in m.itertuples() if r.status_note]
    parts = [
        "# Evidence table: green taxi street hails and the service area",
        "",
        f"Built by `python run_all.py` from TLC green trip records {m['month'].iloc[0]} to {m['month'].iloc[-1]}. "
        f"Pooled = all published months added up before dividing ({len(pub)} of {len(m)} months published"
        + (f", held: {', '.join(held)}" if held else "") + ").",
        "",
        "## The 5 metrics",
        "",
        metrics.to_markdown(index=False),
        "",
        "## Context",
        "",
        context.to_markdown(index=False),
        "",
        "## Monthly values",
        "",
        monthly.to_markdown(index=False, disable_numparse=True),
        "",
    ]
    if notes:
        parts += ["Status notes:", ""] + notes + [""]
    parts += [
        "## Intervention targets: top 10 zone x hour band for street hails outside the Boro Zone",
        "",
        top.to_markdown(index=False),
        "",
    ]
    return "\n".join(parts)


def trend(a, labels, values, title, trigger, ylim):
    a.plot(labels, values, marker="o")
    a.axhline(trigger, ls="--", color="grey")
    a.text(0, trigger, " trigger", color="grey", va="bottom")
    a.set_title(title)
    a.set_ylim(*ylim)
    a.tick_params(axis="x", labelrotation=45)


def dashboard(m, zones_top, hours, path):
    pub = m[m["publish_status"] != "HELD"]
    labels = [pd.Period(x).strftime("%b %y") for x in pub["month"]]
    fig, ax = plt.subplots(2, 3, figsize=(16, 8))

    trend(ax[0, 0], labels, pub["kpi_boro_zone_share_pct"], "1. KPI: % of street hails in the Boro Zone",
          c.TRIGGER_KPI_BELOW, (90, 100))
    trend(ax[0, 1], labels, pub["clear_hez_hail_pct"].astype(float), "3. % of street hails clearly in the HEZ",
          c.TRIGGER_CLEAR_HEZ_ABOVE, (0, 0.3))
    trend(ax[0, 2], labels, pub["unclassified_clear_hez"].astype(float),
          "4. Unclassified trips in the clear HEZ", c.TRIGGER_UNCLASSIFIED_CLEAR_HEZ_ABOVE, (0, 1000))
    ax[0, 2].set_ylabel("trips per month")

    trend(ax[1, 0], labels, pub["unclassified_pct"].astype(float), "4. % of valid trips with no trip_type",
          c.TRIGGER_UNCLASSIFIED_ABOVE, (0, 20))

    a = ax[1, 1]
    top = zones_top.head(8).iloc[::-1]
    a.barh([f"{z} ({i})" for z, i in zip(top["zone"], top["location_id"])], top["street_hails"])
    a.set_title("2/3. Top zones for hails outside the Boro Zone")
    a.set_xlabel("street hails, all published months")

    a = ax[1, 2]
    total = hours["boundary_hails"] + hours["clear_hez_hails"] + hours["boro_zone_hails"]
    a.bar(hours["hour"], 100 * hours["boundary_hails"] / total)
    a.set_title("Boundary-zone hails by pickup hour")
    a.set_xlabel("hour of day")
    a.set_ylabel("% of all street hails in that hour")

    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main(log):
    args = c.parse_args("Stage 4: metrics and evidence")
    months = c.month_range(args.start, args.end)
    if not c.DB_PATH.exists():
        raise c.PipelineError("SQLite model missing, run stage3_model.py first")
    con = sqlite3.connect(c.DB_PATH)

    m = build_monthly(con, months)
    published = [x for x, s in zip(m["month"], m["publish_status"]) if s != "HELD"]
    if not published:
        raise c.PipelineError("every month is HELD, nothing to publish")
    in_list = ",".join(f"'{x}'" for x in published)
    zones_top = pd.read_sql(HEZ_ZONE_SQL.format(months=in_list), con)
    hours = pd.read_sql(HEZ_HOUR_SQL.format(months=in_list), con)
    targets = pd.read_sql(TARGET_SQL.format(months=in_list), con)
    weeks = sum(pd.Period(x).days_in_month for x in published) / 7
    targets["hails_per_week"] = (targets["street_hails"] / weeks).round(1)
    p = pooled(m)
    for col in ["duration_min", "speed_mph"]:
        p[f"median_{col}"] = con.execute(POOLED_MEDIAN_SQL.format(col=col, months=in_list)).fetchone()[0]
    con.close()

    # build the evidence text first, so a crash here doesn't leave half-written outputs
    evidence = evidence_markdown(m, p, targets, args.max_fail_share)

    out = c.OUTPUT_DIR
    previous = pd.read_csv(out / "monthly_metrics.csv") if (out / "monthly_metrics.csv").exists() else None
    m.to_csv(out / "monthly_metrics.csv", index=False)
    new = pd.read_csv(out / "monthly_metrics.csv")
    zones_top.to_csv(out / "hez_hails_by_zone.csv", index=False)
    hours.to_csv(out / "hails_by_hour.csv", index=False)
    targets.to_csv(out / "intervention_targets.csv", index=False)
    m[["month", "raw_rows", "raw_rows_known_zone", "api_pickups", "api_gap_pct", "publish_status"]].to_csv(
        out / "api_reconciliation.csv", index=False)
    (out / "evidence_table.md").write_text(evidence)
    dashboard(m, zones_top, hours, out / "dashboard.png")

    for r in m.itertuples():
        msg = f"{r.month}: {r.publish_status}"
        if r.publish_status != "HELD":
            msg += f", KPI {r.kpi_boro_zone_share_pct}%, {int(r.valid_trips):,} valid trips, API gap {r.api_gap_pct}%"
        if r.status_note:
            msg += f" [{r.status_note}]"
        (log.error if r.publish_status == "HELD" else log.warning if r.publish_status == "WARN" else log.info)(msg)
        if r.triggers_fired and r.publish_status != "HELD":
            log.warning(f"{r.month}: decision trigger fired: {r.triggers_fired}")

    if previous is None:
        log.info("first run, no previous monthly_metrics.csv to compare with")
    elif previous.equals(new):
        log.info("monthly_metrics.csv is identical to the previous run")
    else:
        log.info("monthly_metrics.csv changed since the previous run")

    log.info(f"metrics done: pooled KPI {p['kpi']}% over {len(published)} published months, outputs in outputs/")
    held = [x for x, s in zip(m["month"], m["publish_status"]) if s == "HELD"]
    if held:
        log.error(f"months held back, their metrics are blank: {held}")
        return 2
    return 0


if __name__ == "__main__":
    c.run_stage(main, "stage4_metrics")
