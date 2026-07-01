# Project — Testing structure & index

How this suite is tested, and the single index of where every test case is **defined** (its FSD
chapter) and **implemented** (its code). The portable levels and rules are in
[`../standards/testing.md`](../standards/testing.md); this doc binds them to the four add-ons.

## Where test cases are defined (per add-on) — the canonical chapters

| Add-on | Test cases defined in (FSD chapter) | Test code |
|---|---|---|
| **energymanager** | **Chapter 6 — Test Cases** (6.1 battery discharge, 6.2 appliance, 6.3 EV state machine, 6.4 discharge blocking, 6.5 EV power, **6.6 integration** A–F, **6.7 passive observer**) | `energymanager/tests/test_*.py` |
| **ocpp-server** | **§8 Test Cases** (TC-01…TC-14, incl. TC-13 full charge cycle) | `ocpp-server/tests/test_ocpp_handler.py` |
| **swisssolarforecast** | **§16 Tests and validation** | `swisssolarforecast/test_pipeline.py`, `test_data_integrity.py` |
| **loadforecast** | **§14 Tests and validation** | *(none yet — gap)* |
| suite / cross-cutting | — (InfluxDB overwrite semantics) | `tests/test_influxdb_overwrite.py` |

Per-add-on build/run detail is in each `modules/<addon>.md`; the run commands are in
[`build-and-release.md`](build-and-release.md).

## The pyramid, as built

| Level | Where the cases are | Status |
|---|---|---|
| **Unit** | energymanager §6.1–6.5 (per component); ocpp-server §8 | ✅ implemented across `test_*.py` |
| **Integration** (cross-module) | energymanager **§6.6** (Categories A–E) | ⚠️ partial — some ✅ (`IT-PHASE-01`, `IT-BATT-01…04`), some 🔮 future (need mocks) |
| **End-to-end** (full system) | energymanager **§6.6.6** (`IT-E2E-01`, `IT-E2E-02`) | 🔮 **specified, not yet built** (need PV/load/Smart-car/OCPP mocks together) |
| **Live / observer** | energymanager **§6.7** | ✅ `test_integration_observer.py` (runs against the live system) |

## Known gaps (kept honest)

- **End-to-end IT-E2E-01/02** are defined (§6.6.6) but unbuilt — the only full-system coverage today is
  the passive observer (§6.7).
- **loadforecast has no test code** — §14 defines the validation approach; no `tests/` yet.
- Several §6.6 integration cases are 🔮 future pending mocks (HA client, scheduler, OCPP, Smart car).

Each case's `Status` column in the owning FSD is authoritative; this table is the overview.
