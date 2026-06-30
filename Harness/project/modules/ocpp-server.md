# Module HOW — ocpp-server

How the OCPP Server add-on is built and structured. Behaviour (OCPP messages, wallbox states,
phase-switching, meter correction, the HA entity contract) is in its FSD:
[`ocpp-server/Documents/ocpp-server-fsd.md`](../../../ocpp-server/Documents/ocpp-server-fsd.md).

## Software stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| OCPP library | `ocpp` (Python) |
| WebSocket | `websockets` |
| HA integration | REST API (Supervisor) |
| Phase switch | EARU breaker: BK7231N (LibreTiny/ESPHome) + BL0942 energy meter |
| Deployment | HA add-on (Docker, s6-overlay) |

## File structure

```
ocpp-server/
├── config.yaml              # HA add-on manifest
├── Dockerfile
├── requirements.txt
├── run.py                   # Entry point: OCPPServer class
├── src/
│   ├── ha_entities.py       # HA entity definitions
│   └── ocpp_handler.py      # OCPP message handlers
├── rootfs/                  # s6 service definition
├── tests/
│   └── test_ocpp_handler.py
└── docs/
    └── this file
```

## Tests

The 21 unit tests live in `tests/test_ocpp_handler.py`; the test-case specifications (TC-01…TC-14)
are acceptance criteria in the FSD §8. Run via the project test command
(see [`../build-and-release.md`](../build-and-release.md)).
