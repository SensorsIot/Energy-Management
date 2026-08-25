"""Longevity ceiling (FSD 4.2.4).

`battery_longevity()` is a goal plus one exception, so the whole contract is
three cases. The point of the pure function is that these need no manager, no
HA client and no forecast — the hardware-facing half is covered by
`test_charge_gate.py::TestSocCeiling`.
"""

from src.longevity import NO_CAP, LongevityConstraint, battery_longevity


class TestDisabled:
    def test_returns_no_cap(self) -> None:
        c = battery_longevity(enabled=False, calibration_due=False, ceiling=90.0)
        assert c.soc_ceiling == NO_CAP

    def test_reason_names_the_switch(self) -> None:
        c = battery_longevity(enabled=False, calibration_due=False, ceiling=90.0)
        assert c.reason == "longevity disabled"

    def test_calibration_is_irrelevant_when_disabled(self) -> None:
        """Disabled short-circuits first — 100% either way, but for its own reason."""
        c = battery_longevity(enabled=False, calibration_due=True, ceiling=90.0)
        assert c.soc_ceiling == NO_CAP
        assert c.reason == "longevity disabled"

    def test_never_returns_none(self) -> None:
        """'Off' is a value the caller writes, not an absence it skips."""
        assert isinstance(
            battery_longevity(enabled=False, calibration_due=False, ceiling=90.0),
            LongevityConstraint,
        )


class TestCalibration:
    def test_due_lifts_to_full(self) -> None:
        c = battery_longevity(enabled=True, calibration_due=True, ceiling=90.0)
        assert c.soc_ceiling == NO_CAP
        assert "calibration" in c.reason

    def test_due_overrides_the_ceiling(self) -> None:
        """The pack must reach the top so the LFP BMS can re-anchor."""
        c = battery_longevity(enabled=True, calibration_due=True, ceiling=80.0)
        assert c.soc_ceiling == NO_CAP


class TestCeiling:
    def test_enabled_returns_the_configured_goal(self) -> None:
        c = battery_longevity(enabled=True, calibration_due=False, ceiling=90.0)
        assert c.soc_ceiling == 90.0

    def test_ceiling_is_configurable(self) -> None:
        c = battery_longevity(enabled=True, calibration_due=False, ceiling=85.0)
        assert c.soc_ceiling == 85.0

    def test_reason_states_the_ceiling(self) -> None:
        c = battery_longevity(enabled=True, calibration_due=False, ceiling=90.0)
        assert c.reason == "longevity ceiling 90%"

    def test_returns_a_float(self) -> None:
        """An int from YAML must not leak into the SOC comparison as an int."""
        c = battery_longevity(enabled=True, calibration_due=False, ceiling=90)
        assert isinstance(c.soc_ceiling, float)

    def test_constraint_is_immutable(self) -> None:
        """A frozen constraint cannot be mutated between decision and write."""
        c = battery_longevity(enabled=True, calibration_due=False, ceiling=90.0)
        try:
            c.soc_ceiling = 50.0
        except AttributeError:
            return
        raise AssertionError("LongevityConstraint should be frozen")


class TestNoIO:
    def test_is_pure(self) -> None:
        """Same inputs, same output — no clock, no I/O, no hidden state."""
        args = {"enabled": True, "calibration_due": False, "ceiling": 90.0}
        assert battery_longevity(**args) == battery_longevity(**args)
