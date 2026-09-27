"""Run the whole pipeline: ingest -> validate -> model -> metrics.

    python run_all.py --start 2025-07 --end 2026-06

Each stage is its own script so it can also be run alone. If a stage exits with an
error the later stages are not run, so the metric outputs are only rewritten when every
earlier stage worked. Exit code 2 from the metrics stage means some months were held.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import common as c  # noqa: E402

STAGES = ["stage1_ingest.py", "stage2_validate.py", "stage3_model.py", "stage4_metrics.py"]


def main():
    args = c.parse_args("Run the full green taxi pipeline")
    log = c.get_logger("run_all")
    passthrough = ["--start", args.start, "--end", args.end, "--max-fail-share", str(args.max_fail_share)]
    if args.refresh_api:
        passthrough.append("--refresh-api")
    log.info(f"===== pipeline run start: {args.start} .. {args.end} =====")
    started = time.time()
    for stage in STAGES:
        t0 = time.time()
        code = subprocess.call([sys.executable, str(c.ROOT / "scripts" / stage)] + passthrough)
        log.info(f"{stage} finished with exit code {code} in {time.time() - t0:.1f}s")
        if code == 2 and stage == STAGES[-1]:
            log.error("pipeline finished but some months were HELD, see outputs/monthly_metrics.csv")
            sys.exit(2)
        if code != 0:
            log.error(f"pipeline stopped at {stage}; later stages not run")
            sys.exit(1)
    log.info(f"===== pipeline run OK in {time.time() - started:.1f}s =====")


if __name__ == "__main__":
    main()
