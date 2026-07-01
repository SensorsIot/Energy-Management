# Project — Testing structure & index

How this suite is tested, and the single index of where every test case is **defined** (its FSD
chapter) and **implemented** (its code). The portable levels and rules are in
[`../standards/testing.md`](../standards/testing.md); this doc binds them to the four add-ons.

## Where test cases are defined (per add-on) — the canonical chapters

| Add-on | Test cases defined in (FSD chapter) | Test code |
|---|---|---|
| **energymanager** | **Appendix D — Test Cases** (D.1 battery discharge, D.2 appliance, D.3 EV state machine, D.4 discharge blocking, D.5 EV power, **D.6 integration** A–F, **D.7 passive observer**) | `energymanager/tests/test_*.py` |
| **ocpp-server** | **§8 Test Cases** (TC-01…TC-14, incl. TC-13 full charge cycle) | `ocpp-server/tests/test_ocpp_handler.py` |
| **swisssolarforecast** | **§16 Tests and validation** | `swisssolarforecast/test_pipeline.py`, `test_data_integrity.py` |
| **loadforecast** | **§14 Tests and validation** | *(none yet — gap)* |
| suite / cross-cutting | — (InfluxDB overwrite semantics) | `tests/test_influxdb_overwrite.py` |

Per-add-on build/run detail is in each `modules/<addon>.md`; the run commands are in
[`build-and-release.md`](build-and-release.md).

## The pyramid, as built

| Level | Where the cases are | Status |
|---|---|---|
| **Unit** | energymanager Appendix D.1–D.5 (per component); ocpp-server §8 | ✅ implemented across `test_*.py` |
| **Integration** (cross-module) | energymanager **Appendix D.6** (Categories A–E) | ⚠️ partial — some ✅ (`IT-PHASE-01`, `IT-BATT-01…04`), some 🔮 future (need mocks) |
| **End-to-end** (full system) | energymanager **Appendix D.6.6** (`IT-E2E-01`, `IT-E2E-02`) | 🔮 **specified, not yet built** (need PV/load/Smart-car/OCPP mocks together) |
| **Live / observer** | energymanager **Appendix D.7** | ✅ `test_integration_observer.py` (runs against the live system) |

## Known gaps (kept honest)

- **End-to-end IT-E2E-01/02** are defined (D.6.6) but unbuilt — the only full-system coverage today is
  the passive observer (D.7).
- **loadforecast has no test code** — §14 defines the validation approach; no `tests/` yet.
- Several D.6 integration cases are 🔮 future pending mocks (HA client, scheduler, OCPP, Smart car).

Each case's `Status` column in the owning FSD is authoritative; this table is the overview.
