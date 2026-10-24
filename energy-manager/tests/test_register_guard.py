"""Tests for the inverter register write-rate guard (FSD 4.7.1).

`set_number` refuses to send a value the register already holds — that bounds
writes by *value*. These tests cover the other half: bounding them by *rate*,
so a decision that changes legitimately but often cannot chew the EEPROM.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.register_guard import DAY_S, RegisterWriteGuard

REG = "number.battery_maximum_discharging_power"
OTHER = "number.battery_maximum_charging_power"


@pytest.fixture()
def clock():
    """A controllable monotonic clock for the guard."""

    class Clock:
        now = 1000.0

        def tick(self, seconds):
            self.now += seconds

    c = Clock()
    with patch("src.register_guard.time.monotonic", side_effect=lambda: c.now):
        yield c


@pytest.fixture()
def guard():
    return RegisterWriteGuard(dead_zone_s=60.0, warn_per_day=5, max_per_day=8)


class TestDeadZone:
    def test_first_write_is_allowed(self, guard, clock) -> None:
        allowed, why = guard.allow(REG)
        assert allowed is True
        assert why == ""

    def test_second_write_inside_the_dead_zone_is_held(self, guard, clock) -> None:
        guard.record(REG)
        clock.tick(30)

        allowed, why = guard.allow(REG)
        assert allowed is False
        assert "dead zone" in why

    def test_write_after_the_dead_zone_is_allowed(self, guard, clock) -> None:
        guard.record(REG)
        clock.tick(61)

        allowed, _ = guard.allow(REG)
        assert allowed is True

    def test_dead_zone_is_per_register(self, guard, clock) -> None:
        """A write to one register must not hold back another."""
        guard.record(REG)
        clock.tick(1)

        assert guard.allow(OTHER)[0] is True

    def test_protective_write_ignores_the_dead_zone(self, guard, clock) -> None:
        """A block deferred is a block that did not happen."""
        guard.record(REG)
        clock.tick(1)

        allowed, why = guard.allow(REG, protective=True)
        assert allowed is True
        assert why == ""


class TestDailyBudget:
    def _fill(self, guard, clock, n):
        for _ in range(n):
            guard.record(REG)
            clock.tick(61)  # clear the dead zone each time

    def test_budget_holds_a_non_protective_write(self, guard, clock) -> None:
        self._fill(guard, clock, 8)

        allowed, why = guard.allow(REG)
        assert allowed is False
        assert "daily budget" in why

    def test_budget_never_holds_a_protective_write(self, guard, clock) -> None:
        """Over budget the battery must still be protectable."""
        self._fill(guard, clock, 20)

        assert guard.allow(REG, protective=True)[0] is True

    def test_budget_is_a_rolling_window(self, guard, clock) -> None:
        self._fill(guard, clock, 8)
        assert guard.allow(REG)[0] is False

        clock.tick(DAY_S)  # the whole window ages out

        assert guard.allow(REG)[0] is True
        assert guard.writes_last_day(REG) == 0

    def test_counts_are_per_register(self, guard, clock) -> None:
        self._fill(guard, clock, 8)

        assert guard.writes_last_day(REG) == 8
        assert guard.writes_last_day(OTHER) == 0
        assert guard.allow(OTHER)[0] is True


class TestWarning:
    def test_warning_fires_at_the_threshold(self, guard, clock) -> None:
        for _ in range(4):
            assert guard.record(REG) is None
            clock.tick(61)

        warning = guard.record(REG)  # the 5th, == warn_per_day
        assert warning is not None
        assert "5 writes" in warning

    def test_warning_does_not_repeat_every_write(self, guard, clock) -> None:
        """A busy day must not turn into a notification storm."""
        warnings = []
        for _ in range(8):
            warnings.append(guard.record(REG))
            clock.tick(61)

        assert len([w for w in warnings if w]) == 1

    def test_warning_rearms_once_the_window_clears(self, guard, clock) -> None:
        for _ in range(5):
            guard.record(REG)
            clock.tick(61)
        assert guard.record(REG) is None  # already warned

        clock.tick(DAY_S)
        for _ in range(4):
            assert guard.record(REG) is None
            clock.tick(61)
        assert guard.record(REG) is not None  # warns again on the new window
