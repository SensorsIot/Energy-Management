#!/usr/bin/env python3
"""Discover Gigya API rate limit cooldown time via binary search.

Usage:
    export SMART_USER="your@email.com"
    export SMART_PASSWORD="yourpassword"
    python3 scripts/smart_rate_limit_probe.py

Precondition: credentials must be valid (first successful login proves this).

Algorithm:
  1. Trigger a rate limit (rapid login attempts)
  2. Wait `interval` (start: 1 hour)
  3. Attempt login
  4. Rate limited  → double interval, go to 2
  5. Success       → cooldown is between interval/2 and interval
  6. Binary search to narrow down within ±30s precision
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

# Reuse the existing client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smart_car_status import HelloSmartClient


INITIAL_INTERVAL = 900   # 15 minutes
PRECISION = 30           # stop when uncertainty < 30 seconds


def ts():
    return datetime.now().strftime("%H:%M:%S")


def attempt_login(user: str, password: str) -> str:
    """Try to login. Returns 'success', 'rate_limited', or 'auth_error'."""
    client = HelloSmartClient(user, password, quiet=True)
    try:
        client.authenticate()
        return "success"
    except RuntimeError as e:
        msg = str(e).lower()
        if "rate limit" in msg:
            return "rate_limited"
        else:
            return f"auth_error: {e}"


def trigger_rate_limit(user: str, password: str):
    """Fire rapid login attempts until rate-limited."""
    log("Triggering rate limit with rapid login attempts...")
    for i in range(10):
        result = attempt_login(user, password)
        log(f"  Attempt {i+1}: {result}")
        if result == "rate_limited":
            log("Rate limit triggered.")
            return
        if "auth_error" in result:
            log(f"ABORT: {result}")
            sys.exit(1)
        time.sleep(1)
    log("Warning: could not trigger rate limit after 10 attempts")


def wait_with_progress(seconds: int):
    """Wait with periodic progress updates."""
    end = time.time() + seconds
    while True:
        remaining = end - time.time()
        if remaining <= 0:
            break
        if remaining > 60:
            mins = int(remaining / 60)
            log(f"  ... waiting {mins}m {int(remaining % 60)}s remaining")
            time.sleep(min(300, remaining))  # update every 5 min
        else:
            log(f"  ... waiting {int(remaining)}s remaining")
            time.sleep(min(30, remaining))
    log("  Wait complete.")


def log(msg: str):
    print(f"[{ts()}] {msg}", flush=True)


def main():
    user = os.environ.get("SMART_USER", "")
    password = os.environ.get("SMART_PASSWORD")

    if not password:
        print("Error: SMART_PASSWORD not set", file=sys.stderr)
        sys.exit(1)

    log("=== Smart API Rate Limit Discovery ===")
    log(f"Initial interval: {INITIAL_INTERVAL}s, precision target: ±{PRECISION}s")

    # Phase 1: Check if we're already rate-limited or can confirm credentials
    log("Phase 1: Initial login attempt...")
    result = attempt_login(user, password)
    log(f"  Result: {result}")

    if "auth_error" in result:
        log(f"ABORT: credentials invalid — {result}")
        sys.exit(1)

    credentials_confirmed = result == "success"
    already_rate_limited = result == "rate_limited"

    if credentials_confirmed:
        log("Credentials confirmed.")
        # Trigger rate limit so we can measure cooldown
        trigger_rate_limit(user, password)
    else:
        log("Already rate-limited. Credentials unconfirmed — will verify on first success.")

    # Phase 2: Find upper bound (double until success)
    interval = INITIAL_INTERVAL
    log(f"\nPhase 2: Finding upper bound (starting at {interval}s)...")

    while True:
        log(f"\nWaiting {interval}s ({interval/60:.0f} min)...")
        wait_with_progress(interval)

        result = attempt_login(user, password)
        log(f"Attempt after {interval}s wait: {result}")

        if "auth_error" in result:
            log(f"ABORT: {result}")
            sys.exit(1)

        if result == "rate_limited":
            log(f"Still rate-limited after {interval}s. Doubling interval.")
            interval *= 2
            continue

        # Success!
        if not credentials_confirmed:
            log("Credentials confirmed on first success.")
            credentials_confirmed = True

        log(f"SUCCESS after {interval}s wait.")
        log(f"Cooldown is between {interval // 2}s and {interval}s.")
        break

    # Phase 3: Binary search to narrow down
    lower = interval // 2  # last known failure (or 0 if first attempt succeeded)
    upper = interval        # first known success

    log(f"\nPhase 3: Binary search [{lower}s, {upper}s] (precision ±{PRECISION}s)...")

    while (upper - lower) > PRECISION:
        mid = (lower + upper) // 2
        log(f"\nRange: [{lower}s, {upper}s] — uncertainty: {upper - lower}s")
        log(f"Testing midpoint: {mid}s ({mid/60:.1f} min)")

        # Re-trigger rate limit
        trigger_rate_limit(user, password)

        # Wait midpoint duration
        log(f"Waiting {mid}s...")
        wait_with_progress(mid)

        result = attempt_login(user, password)
        log(f"Attempt after {mid}s wait: {result}")

        if "auth_error" in result:
            log(f"ABORT: {result}")
            sys.exit(1)

        if result == "rate_limited":
            lower = mid
            log(f"Still limited → cooldown > {mid}s")
        else:
            upper = mid
            log(f"Success → cooldown ≤ {mid}s")

    # Done
    log(f"\n{'='*50}")
    log(f"RESULT: Rate limit cooldown is {lower}s – {upper}s")
    log(f"        ({lower/60:.1f} min – {upper/60:.1f} min)")
    log(f"        Uncertainty: ±{(upper - lower) // 2}s")
    log(f"{'='*50}")


if __name__ == "__main__":
    main()
