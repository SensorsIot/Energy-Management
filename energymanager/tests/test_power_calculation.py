"""
Tests for EV Charging Power Calculation (FSD 4.5.6).

The power calculation lives in run.py control_ev_charging() and selects
between surplus capture and forecast strategy, applying threshold and
battery protection rules. These tests verify the logic in isolation.
"""

from __future__ import annotations


def compute_ev_charging_power(
    *,
    ev_mode: str,
    surplus_capture_power_w: float,
    ev_forecasted_power_w: float,
    reaches_target: bool,
    threshold: float,
) -> tuple[float, str]:
    """Replicate the FSD 4.5.6 power calculation from run.py."""
    ev_charging_power_w = 0.0
    ev_charging_source = "none"
    if ev_mode == "solar":
        # Rule 1: surplus capture has priority (exported energy is wasted)
        if surplus_capture_power_w >= threshold:
            ev_charging_power_w = surplus_capture_power_w
            ev_charging_source = "surplus"
        # Rule 2: forecast strategy (needs battery protection)
        elif reaches_target and ev_forecasted_power_w >= threshold:
            ev_charging_power_w = ev_forecasted_power_w
            ev_charging_source = "forecast"
    return ev_charging_power_w, ev_charging_source


class TestSurplusPriority:
    """Surplus capture has priority over forecast (FSD Rule 1)."""

    def test_surplus_above_threshold_wins(self):
        """Surplus above threshold wins even when forecast is available."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            ev_forecasted_power_w=5000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 1610
        assert source == "surplus"

    def test_surplus_below_threshold_uses_forecast(self):
        """Surplus below threshold falls through to forecast."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1000,
            ev_forecasted_power_w=5000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 5000
        assert source == "forecast"


class TestForecastWithProtection:
    """Forecast path requires battery protection (FSD Rule 2)."""

    def test_forecast_above_threshold_protection_passed(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            ev_forecasted_power_w=5000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 5000
        assert source == "forecast"

    def test_forecast_above_threshold_protection_failed(self):
        """Battery protection blocks forecast → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            ev_forecasted_power_w=5000,
            reaches_target=False,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_forecast_below_threshold(self):
        """Forecast below threshold → 0."""
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=0,
            ev_forecasted_power_w=1000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestBothBelowThreshold:
    def test_both_below_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=500,
            ev_forecasted_power_w=800,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestNonSolarMode:
    def test_immediate_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="immediate",
            surplus_capture_power_w=2000,
            ev_forecasted_power_w=5000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"

    def test_cheap_mode_returns_zero(self):
        power, source = compute_ev_charging_power(
            ev_mode="cheap",
            surplus_capture_power_w=2000,
            ev_forecasted_power_w=5000,
            reaches_target=True,
            threshold=1400,
        )
        assert power == 0.0
        assert source == "none"


class TestSurplusBypassesProtection:
    """Surplus doesn't need battery protection — it's free energy."""

    def test_surplus_works_without_protection(self):
        power, source = compute_ev_charging_power(
            ev_mode="solar",
            surplus_capture_power_w=1610,
            ev_forecasted_power_w=0,
            reaches_target=False,
            threshold=1400,
        )
        assert power == 1610
        assert source == "surplus"
