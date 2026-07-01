#!/usr/bin/env python3
"""
Backfill pv_accuracy data for missing evaluation days.

This script runs evaluate_forecast for each missing day to populate
the pv_accuracy measurement with forecast vs actual comparisons.

Usage:
    python scripts/backfill_accuracy.py --start 2026-02-02 --end 2026-02-08
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.accuracy_tracker import AccuracyTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def backfill_accuracy(
    start_date: str,
    end_date: str,
    influx_host: str = "192.168.0.203",
    influx_port: int = 8087,
    influx_token: str = "",
    influx_org: str = "spiessa",
    pv_bucket: str = "pv_forecast",
    local_timezone: str = "Europe/Zurich",
    dry_run: bool = False,
):
    """
    Backfill pv_accuracy data for a range of dates.

    Args:
        start_date: Start date (YYYY-MM-DD) - the snapshot_id to start from
        end_date: End date (YYYY-MM-DD) - the snapshot_id to end at (inclusive)
        influx_host: InfluxDB host
        influx_port: InfluxDB port
        influx_token: InfluxDB token (must have read access to HomeAssistant bucket)
        influx_org: InfluxDB organization
        pv_bucket: Bucket for pv_forecast data
        local_timezone: Local timezone for snapshot timing
        dry_run: If True, only print what would be done
    """
    local_tz = ZoneInfo(local_timezone)

    # Parse dates
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if start > end:
        logger.error(f"Start date {start_date} is after end date {end_date}")
        return

    # Read token from file if not provided
    if not influx_token:
        token_path = Path.home() / ".secrets" / "influxdb"
        if token_path.exists():
            influx_token = token_path.read_text().strip()
            logger.info(f"Read InfluxDB token from {token_path}")
        else:
            logger.error("No InfluxDB token provided and ~/.secrets/influxdb not found")
            return

    if dry_run:
        logger.info("DRY RUN - no data will be written")

    # Create tracker
    tracker = AccuracyTracker(
        influx_host=influx_host,
        influx_port=influx_port,
        influx_token=influx_token,
        influx_org=influx_org,
        pv_bucket=pv_bucket,
        local_timezone=local_timezone,
    )

    if not dry_run:
        tracker.connect()

    # Iterate through each day
    current = start
    success_count = 0
    fail_count = 0

    while current <= end:
        snapshot_id = current.strftime("%Y-%m-%d")

        # Evaluation runs at 21:15 the NEXT day
        # So to evaluate snapshot_id "2026-02-02", we simulate running at 21:15 on Feb 3rd
        next_day = current + timedelta(days=1)
        eval_time_local = next_day.replace(hour=21, minute=15, second=0, microsecond=0)
        eval_time_local = eval_time_local.replace(tzinfo=local_tz)
        eval_time_utc = eval_time_local.astimezone(timezone.utc)

        logger.info(f"Evaluating snapshot {snapshot_id} (simulated eval time: {eval_time_local})")

        if dry_run:
            logger.info(f"  Would evaluate {snapshot_id}")
        else:
            try:
                success = tracker.evaluate_forecast(evaluation_time=eval_time_utc)
                if success:
                    logger.info(f"  SUCCESS: {snapshot_id}")
                    success_count += 1
                else:
                    logger.warning(f"  NO DATA: {snapshot_id}")
                    fail_count += 1
            except Exception as e:
                logger.error(f"  FAILED: {snapshot_id} - {e}")
                fail_count += 1

        current += timedelta(days=1)

    if not dry_run:
        tracker.close()

    logger.info(f"Backfill complete: {success_count} succeeded, {fail_count} failed")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill pv_accuracy data for missing evaluation days"
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date (YYYY-MM-DD) - first snapshot_id to evaluate"
    )
    parser.add_argument(
        "--end",
        required=True,
        help="End date (YYYY-MM-DD) - last snapshot_id to evaluate (inclusive)"
    )
    parser.add_argument(
        "--host",
        default="192.168.0.203",
        help="InfluxDB host (default: 192.168.0.203)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8087,
        help="InfluxDB port (default: 8087)"
    )
    parser.add_argument(
        "--token",
        default="",
        help="InfluxDB token (default: read from ~/.secrets/influxdb)"
    )
    parser.add_argument(
        "--org",
        default="spiessa",
        help="InfluxDB organization (default: spiessa)"
    )
    parser.add_argument(
        "--bucket",
        default="pv_forecast",
        help="PV forecast bucket (default: pv_forecast)"
    )
    parser.add_argument(
        "--timezone",
        default="Europe/Zurich",
        help="Local timezone (default: Europe/Zurich)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing data"
    )

    args = parser.parse_args()

    backfill_accuracy(
        start_date=args.start,
        end_date=args.end,
        influx_host=args.host,
        influx_port=args.port,
        influx_token=args.token,
        influx_org=args.org,
        pv_bucket=args.bucket,
        local_timezone=args.timezone,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
