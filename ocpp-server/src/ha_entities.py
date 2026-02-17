"""
Home Assistant entity definitions for the OCPP Server add-on.

Entities are published via the HA Supervisor REST API.
The add-on updates entity states when OCPP messages arrive,
and watches control entities for EnergyManager commands.
"""

# Sensor entities (wallbox state → HA)
SENSORS = {
    "sensor.wallbox_power": {
        "name": "Wallbox Power",
        "unique_id": "ocpp_wallbox_power",
        "device_class": "power",
        "state_class": "measurement",
        "unit_of_measurement": "W",
        "icon": "mdi:ev-station",
        "initial_state": 0,
    },
    "sensor.wallbox_energy": {
        "name": "Wallbox Energy",
        "unique_id": "ocpp_wallbox_energy",
        "device_class": "energy",
        "state_class": "total_increasing",
        "unit_of_measurement": "Wh",
        "icon": "mdi:lightning-bolt",
        "initial_state": 0,
    },
    "sensor.wallbox_status": {
        "name": "Wallbox Status",
        "unique_id": "ocpp_wallbox_status",
        "icon": "mdi:ev-plug-type2",
        "initial_state": "Unknown",
        "options": ["Available", "Preparing", "Charging", "SuspendedEV",
                     "SuspendedEVSE", "Finishing", "Faulted", "Unknown"],
    },
    "sensor.wallbox_transaction": {
        "name": "Wallbox Transaction",
        "unique_id": "ocpp_wallbox_transaction",
        "icon": "mdi:swap-horizontal",
        "initial_state": "idle",
        "options": ["idle", "charging"],
    },
    "sensor.wallbox_phases": {
        "name": "Wallbox Phases",
        "unique_id": "ocpp_wallbox_phases",
        "icon": "mdi:sine-wave",
        "initial_state": 3,
    },
    "sensor.wallbox_min_power_w": {
        "name": "Wallbox Min Power",
        "unique_id": "ocpp_wallbox_min_power_w",
        "device_class": "power",
        "unit_of_measurement": "W",
        "icon": "mdi:lightning-bolt-outline",
        "initial_state": 0,
    },
    "sensor.wallbox_max_power_w": {
        "name": "Wallbox Max Power",
        "unique_id": "ocpp_wallbox_max_power_w",
        "device_class": "power",
        "unit_of_measurement": "W",
        "icon": "mdi:lightning-bolt",
        "initial_state": 0,
    },
}

BINARY_SENSORS = {
    "binary_sensor.wallbox_connected": {
        "name": "Wallbox Connected",
        "unique_id": "ocpp_wallbox_connected",
        "device_class": "connectivity",
        "icon": "mdi:lan-connect",
        "initial_state": False,
    },
    "binary_sensor.wallbox_single_phase_supported": {
        "name": "Wallbox Single Phase Supported",
        "unique_id": "ocpp_wallbox_single_phase_supported",
        "icon": "mdi:lightning-bolt",
        "initial_state": False,
    },
}

# Control entities (HA → wallbox via OCPP)
CONTROLS = {
    "number.wallbox_power_limit": {
        "name": "Wallbox Power Limit",
        "unique_id": "ocpp_wallbox_power_limit",
        "device_class": "power",
        "unit_of_measurement": "W",
        "icon": "mdi:speedometer",
        "min": 0,           # 0 = pause charging
        "max": 11000,       # 3-phase × 16A × 230V
        "step": 100,
        "initial_state": 0,
        "mode": "slider",
        # Triggers: SetChargingProfile
    },
}

# All entities grouped for registration
ALL_ENTITIES = {
    "sensors": SENSORS,
    "binary_sensors": BINARY_SENSORS,
    "controls": CONTROLS,
}
