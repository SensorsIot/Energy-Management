#!/usr/bin/env python3
"""
Test script to verify InfluxDB overwrite behavior for forecast data.

This tests that writing to the same measurement + tags + timestamp
overwrites existing data when run_time is a FIELD (not a tag).

Usage:
    # Set token first (or pass as argument)
    export INFLUXDB_TOKEN="your-token"

    # Run test
    python tests/test_influxdb_overwrite.py

    # Or with SSH tunnel:
    # ssh -L 8087:localhost:8086 pi@192.168.0.203
    # Then run with --host localhost
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


def get_token_from_file(path: str = "/home/dev/.secrets/influxdb") -> str:
    """Try to read token from secrets file."""
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return ""


def connect(host: str, port: int, token: str, org: str) -> InfluxDBClient:
    """Connect to InfluxDB."""
    url = f"http://{host}:{port}"
    print(f"Connecting to InfluxDB at {url}")
    client = InfluxDBClient(url=url, token=token, org=org)

    # Verify connection
    health = client.health()
    print(f"Connection status: {health.status}")
    return client


def write_test_data(client: InfluxDBClient, bucket: str, org: str,
                    run_id: str, value_offset: float = 0):
    """
    Write test forecast data with a specific run_id.

    Args:
        client: InfluxDB client
        bucket: Target bucket
        org: Organization
        run_id: Identifier for this test run (stored in run_time field)
        value_offset: Add this to base values to distinguish different writes
    """
    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Use fixed timestamps in the future (aligned to 15-min)
    base_time = datetime.now(timezone.utc).replace(
        minute=(datetime.now().minute // 15) * 15,
        second=0,
        microsecond=0
    ) + timedelta(hours=1)

    points = []
    for i in range(4):  # 4 points, 15 min apart
        timestamp = base_time + timedelta(minutes=15 * i)

        # Base values + offset to distinguish writes
        p10 = 100.0 + i * 10 + value_offset
        p50 = 200.0 + i * 10 + value_offset
        p90 = 300.0 + i * 10 + value_offset

        point = (
            Point("test_forecast")
            .tag("model", "test")
            .field("power_w_p10", p10)
            .field("power_w_p50", p50)
            .field("power_w_p90", p90)
            .field("run_time", run_id)  # run_time as FIELD, not tag
            .time(timestamp, WritePrecision.S)
        )
        points.append(point)
        print(f"  {timestamp.isoformat()} -> p50={p50}, run_time={run_id}")

    write_api.write(bucket=bucket, org=org, record=points)
    print(f"Wrote {len(points)} points with run_id={run_id}")


def query_test_data(client: InfluxDBClient, bucket: str, org: str) -> list:
    """Query the test data and return results."""
    query_api = client.query_api()

    query = f'''
    from(bucket: "{bucket}")
      |> range(start: -1h, stop: 24h)
      |> filter(fn: (r) => r._measurement == "test_forecast")
      |> filter(fn: (r) => r._field == "power_w_p50" or r._field == "run_time")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"])
    '''

    tables = query_api.query(query, org=org)

    results = []
    for table in tables:
        for record in table.records:
            results.append({
                "time": record.get_time(),
                "power_w_p50": record.values.get("power_w_p50"),
                "run_time": record.values.get("run_time"),
            })

    return results


def delete_test_data(client: InfluxDBClient, bucket: str, org: str):
    """Clean up test data."""
    delete_api = client.delete_api()

    start = datetime.now(timezone.utc) - timedelta(hours=1)
    stop = datetime.now(timezone.utc) + timedelta(days=1)

    print(f"Deleting test_forecast data...")
    delete_api.delete(
        start=start,
        stop=stop,
        predicate='_measurement="test_forecast"',
        bucket=bucket,
        org=org,
    )
    print("Deleted test data")


def run_overwrite_test(client: InfluxDBClient, bucket: str, org: str):
    """
    Test that overwriting works correctly.

    1. Write data with run_id="first"
    2. Query and verify
    3. Write SAME timestamps with run_id="second" and different values
    4. Query and verify data was overwritten (not duplicated)
    """
    print("\n" + "="*60)
    print("OVERWRITE TEST")
    print("="*60)

    # Clean up any previous test data
    print("\n1. Cleaning up previous test data...")
    try:
        delete_test_data(client, bucket, org)
    except Exception as e:
        print(f"   (cleanup failed, continuing: {e})")

    # First write
    print("\n2. First write (run_id='first', base values)...")
    write_test_data(client, bucket, org, run_id="first", value_offset=0)

    # Query after first write
    print("\n3. Query after first write:")
    results = query_test_data(client, bucket, org)
    print(f"   Found {len(results)} records:")
    for r in results:
        print(f"   {r['time']} -> p50={r['power_w_p50']}, run_time={r['run_time']}")

    # Second write - SAME timestamps, different values
    print("\n4. Second write (run_id='second', values +1000)...")
    print("   Writing to SAME timestamps without delete...")
    write_test_data(client, bucket, org, run_id="second", value_offset=1000)

    # Query after second write
    print("\n5. Query after second write:")
    results = query_test_data(client, bucket, org)
    print(f"   Found {len(results)} records:")
    for r in results:
        print(f"   {r['time']} -> p50={r['power_w_p50']}, run_time={r['run_time']}")

    # Verify results
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)

    if len(results) == 4:
        print("✓ Correct number of records (4) - no duplicates!")

        # Check if values were overwritten
        all_second = all(r.get('run_time') == 'second' for r in results)
        all_high_values = all(r.get('power_w_p50', 0) > 1000 for r in results)

        if all_second and all_high_values:
            print("✓ All records have run_time='second' and updated values")
            print("✓ OVERWRITE WORKS CORRECTLY!")
            print("\n→ You can safely remove delete_future_forecasts() calls")
            return True
        else:
            print("✗ Values were not overwritten correctly")
            print("  This means the unique key includes something unexpected")
            return False
    elif len(results) == 8:
        print("✗ Found 8 records - data was DUPLICATED, not overwritten!")
        print("  This happens when run_time is a TAG (part of unique key)")
        print("  Solution: Ensure run_time is a FIELD, not a tag")
        return False
    else:
        print(f"✗ Unexpected number of records: {len(results)}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test InfluxDB overwrite behavior")
    parser.add_argument("--host", default="192.168.0.203", help="InfluxDB host")
    parser.add_argument("--port", type=int, default=8087, help="InfluxDB port")
    parser.add_argument("--org", default="spiessa", help="InfluxDB organization")
    parser.add_argument("--bucket", default="load_forecast", help="Test bucket")
    parser.add_argument("--token", help="InfluxDB token (or set INFLUXDB_TOKEN env)")
    parser.add_argument("--cleanup", action="store_true", help="Only delete test data")

    args = parser.parse_args()

    # Get token
    token = args.token or os.environ.get("INFLUXDB_TOKEN") or get_token_from_file()
    if not token:
        print("ERROR: No InfluxDB token provided")
        print("  Set INFLUXDB_TOKEN env var, use --token, or ensure secrets file exists")
        sys.exit(1)

    print(f"Token: {token[:8]}...{token[-4:]}")

    # Connect
    try:
        client = connect(args.host, args.port, token, args.org)
    except Exception as e:
        print(f"ERROR: Failed to connect: {e}")
        sys.exit(1)

    try:
        if args.cleanup:
            delete_test_data(client, args.bucket, args.org)
        else:
            success = run_overwrite_test(client, args.bucket, args.org)
            sys.exit(0 if success else 1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
