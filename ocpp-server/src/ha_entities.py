"""Home Assistant entity definitions for the OCPP Server add-on.

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
    # Derived from the commanded amps (amps × watts-per-amp). Display only —
    # the control entity is number.wallbox_current_limit.
    "sensor.wallbox_power_limit": {
        "name": "Wallbox Power Limit",
        "unique_id": "ocpp_wallbox_power_limit_w",
        "device_class": "power",
        "state_class": "measurement",
        "unit_of_measurement": "W",
        "icon": "mdi:speedometer",
        "initial_state": 0,
    },
    # The one watts↔amps conversion factor in use, for the phase count now
    # detected. Consumers read this instead of hardcoding their own.
    "sensor.wallbox_watts_per_amp": {
        "name": "Wallbox Watts Per Amp",
        "unique_id": "ocpp_wallbox_watts_per_amp",
        "state_class": "measurement",
        "unit_of_measurement": "W/A",
        "icon": "mdi:math-compass",
        "initial_state": 637,
    },
    "sensor.wallbox_min_current_a": {
        "name": "Wallbox Min Current",
        "unique_id": "ocpp_wallbox_min_current_a",
        "device_class": "current",
        "unit_of_measurement": "A",
        "icon": "mdi:current-ac",
        "initial_state": 6,
    },
    "sensor.wallbox_max_current_a": {
        "name": "Wallbox Max Current",
        "unique_id": "ocpp_wallbox_max_current_a",
        "device_class": "current",
        "unit_of_measurement": "A",
        "icon": "mdi:current-ac",
        "initial_state": 16,
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
        "options": [
            "Available",
            "Preparing",
            "Charging",
            "SuspendedEV",
            "SuspendedEVSE",
            "Finishing",
            "Faulted",
            "Unknown",
        ],
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
    "binary_sensor.car_ready": {
        "name": "Car Ready",
        "unique_id": "ocpp_car_ready",
        "icon": "mdi:car-electric",
        "initial_state": False,
    },
}

# Control entities (HA → wallbox via OCPP)
CONTROLS = {
    # The control unit is amps: OCPP carries amps and the wallbox applies them
    # per phase, so the commanded value reaches the wallbox unconverted. The
    # matching watts are published read-only as sensor.wallbox_power_limit.
    "number.wallbox_current_limit": {
        "name": "Wallbox Current Limit",
        "unique_id": "ocpp_wallbox_current_limit",
        "device_class": "current",
        "unit_of_measurement": "A",
        "icon": "mdi:speedometer",
        "min": 0,  # 0 = pause charging; otherwise min_current_a..max_current_a
        "max": 16,
        "step": 1,  # the wallbox only accepts whole amps
        "initial_state": 0,
        "mode": "box",
        # Triggers: SetChargingProfile
    },
    # Phase count the consumer wants those amps on. Amps alone cannot express a
    # power on a switchable wallbox — 6 A is 1380 W on one phase and 3822 W on
    # three — so the pair (amps, phases) is the complete request and nothing has
    # to be converted to decide. Ignored for `three_phase`, where the connected
    # cable owns the phase count (FSD 3.6.4).
    "number.wallbox_phase_request": {
        "name": "Wallbox Phase Request",
        "unique_id": "ocpp_wallbox_phase_request",
        "icon": "mdi:transmission-tower",
        "min": 1,
        "max": 3,
        "step": 2,  # 1 or 3 — two-phase charging is not commanded
        "initial_state": 3,
        "mode": "box",
    },
}

# All entities grouped for registration
ALL_ENTITIES = {
    "sensors": SENSORS,
    "binary_sensors": BINARY_SENSORS,
    "controls": CONTROLS,
}

# Flat lookup used by HAEntityManager.set_state() to recover attributes when a
# caller passes only state — without this fallback, every state update wipes
# unit_of_measurement / state_class / device_class via the REST replace-semantics
# of POST /api/states/, which trips HA's recorder repair detectors.
ALL_DEFS = {**SENSORS, **BINARY_SENSORS, **CONTROLS}
