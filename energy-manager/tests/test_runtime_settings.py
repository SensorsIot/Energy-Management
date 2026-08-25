"""Tablet-controlled runtime settings (FSD 4.7.6).

The contract is a precedence rule: the HA helper wins when it holds a definite
state, the YAML value applies otherwise. The failure that matters is a helper
that is missing or `unavailable` reading as "off" — that would disable a
feature because an entity failed to load.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from run import EnergyManager


@pytest.fixture
def manager() -> EnergyManager:
    m = EnergyManager.__new__(EnergyManager)
    m.ha_client = MagicMock()
    m.setting_entities = {
        "charge_shaving_enabled": "input_boolean.battery_shaving_enabled",
        "longevity_enabled": "input_boolean.battery_longevity_enabled",
        "longevity_ceiling": "input_number.battery_longevity_ceiling",
        "shaving_decision_hour": "input_number.shaving_decision_hour",
        "charge_shaving_reserve_soc": "input_number.shaving_reserve_soc",
        "mbus_enabled": "input_boolean.mbus_reader_enabled",
    }
    m._setting_defaults = {
        "charge_shaving_enabled": True,
        "longevity_enabled": False,
        "longevity_ceiling": 90.0,
        "shaving_decision_hour": 8,
        "charge_shaving_reserve_soc": 20.0,
        "mbus_enabled": True,
    }
    for attr, value in m._setting_defaults.items():
        setattr(m, attr, value)
    return m


class TestHelperWins:
    def test_toggle_on_overrides_a_false_default(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = True
        manager.ha_client.get_sensor_value.return_value = None
        manager._refresh_runtime_settings()
        assert manager.longevity_enabled is True

    def test_toggle_off_overrides_a_true_default(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = False
        manager.ha_client.get_sensor_value.return_value = None
        manager._refresh_runtime_settings()
        assert manager.charge_shaving_enabled is False

    def test_number_overrides_the_default(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = 95.0
        manager._refresh_runtime_settings()
        assert manager.longevity_ceiling == 95.0


class TestDefaultApplies:
    """A helper that is not there must never read as 'off'."""

    def test_missing_toggle_keeps_the_yaml_default(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = None
        manager._refresh_runtime_settings()
        assert manager.charge_shaving_enabled is True
        assert manager.mbus_enabled is True

    def test_missing_number_keeps_the_yaml_default(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = None
        manager._refresh_runtime_settings()
        assert manager.longevity_ceiling == 90.0
        assert manager.charge_shaving_reserve_soc == 20.0

    def test_default_survives_repeated_refreshes(self, manager) -> None:
        """The default is re-read from _setting_defaults, not from the attribute.

        Resolving against the live attribute would let one bad read latch
        permanently.
        """
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = None
        for _ in range(3):
            manager._refresh_runtime_settings()
        assert manager.charge_shaving_enabled is True


class TestTypes:
    def test_decision_hour_stays_an_int(self, manager) -> None:
        """It indexes an hour comparison — a float would still work, but the
        attribute is declared int and logging/publishing rely on it."""
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = 9.0
        manager._refresh_runtime_settings()
        assert manager.shaving_decision_hour == 9
        assert isinstance(manager.shaving_decision_hour, int)

    def test_reserve_soc_stays_a_float(self, manager) -> None:
        manager.ha_client.get_optional_bool.return_value = None
        manager.ha_client.get_sensor_value.return_value = 25
        manager._refresh_runtime_settings()
        assert isinstance(manager.charge_shaving_reserve_soc, float)


class TestOptionalBool:
    """`get_optional_bool` is what makes 'absent' distinguishable from 'off'."""

    @pytest.mark.parametrize(
        ("state", "expected"),
        [("on", True), ("off", False), ("unknown", None), ("unavailable", None)],
    )
    def test_states(self, state, expected) -> None:
        from src.ha_client import HAClient

        client = HAClient.__new__(HAClient)
        client.get_state = MagicMock(return_value={"state": state})
        assert client.get_optional_bool("input_boolean.x") is expected

    def test_missing_entity_is_none_not_false(self) -> None:
        from src.ha_client import HAClient

        client = HAClient.__new__(HAClient)
        client.get_state = MagicMock(return_value=None)
        assert client.get_optional_bool("input_boolean.x") is None
