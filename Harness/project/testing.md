# Project — Testing structure & index

How this suite is tested, and the single index of where every test case is **defined** (its FSD
chapter) and **implemented** (its code). The portable levels and rules are in
[`../standards/testing.md`](../standards/testing.md); this doc binds them to the four add-ons.

## Where test cases are defined (per add-on) — the canonical chapters

| Add-on | Test cases defined in (FSD chapter) | Test code |
|---|---|---|
| **energy-manager** | **Chapter 6 — Test Cases** (6.1 battery discharge, 6.2 appliance, 6.3 EV state machine, 6.4 discharge blocking, 6.5 EV power, **6.6 integration** A–F, **6.7 passive observer**) | `energy-manager/tests/test_*.py` |
| **ocpp-server** | **§8 Test Cases** (TC-01…TC-14, incl. TC-13 full charge cycle) | `ocpp-server/tests/test_ocpp_handler.py` |
| **swiss-solar-forecast** | **§16 Tests and validation** | `swiss-solar-forecast/test_pipeline.py`, `test_data_integrity.py` |
| **load-forecast** | **§14 Tests and validation** | *(none yet — gap)* |
| suite / cross-cutting | — (InfluxDB overwrite semantics) | `tests/test_influxdb_overwrite.py` |

Per-add-on build/run detail is in each `modules/<addon>.md`; the run commands are in
[`build-and-release.md`](build-and-release.md).

## The pyramid, as built

| Level | Where the cases are | Status |
|---|---|---|
| **Unit** | energy-manager §6.1–6.5 (per component); ocpp-server §8 | ✅ implemented across `test_*.py` |
| **Integration** (cross-module) | energy-manager **§6.6** (Categories A–E) | ⚠️ partial — some ✅ (`IT-PHASE-01`, `IT-BATT-01…04`), some 🔮 future (need mocks) |
| **End-to-end** (full system) | energy-manager **§6.6.6** (`IT-E2E-01`, `IT-E2E-02`) | 🔮 **specified, not yet built** (need PV/load/Smart-car/OCPP mocks together) |
| **Live / observer** | energy-manager **§6.7** | ✅ `test_integration_observer.py` (runs against the live system) |

## Known gaps (kept honest)

- **End-to-end IT-E2E-01/02** are defined (§6.6.6) but unbuilt — the only full-system coverage today is
  the passive observer (§6.7).
- **load-forecast has no test code** — §14 defines the validation approach; no `tests/` yet.
- Several §6.6 integration cases are 🔮 future pending mocks (HA client, scheduler, OCPP, Smart car).

## Security-testing gaps (per add-on)

Per the security-testing standard ([`../standards/testing.md`](../standards/testing.md)),
`ocpp-server` has **security cases §8.1 SEC-01…08 — 5 built, 3 open**; the other three add-ons have
no security cases yet. The security surface, per add-on (with the standard to anchor the cases to):

| Add-on | Security surface & coverage | Anchor |
|---|---|---|
| **ocpp-server** (highest — network-facing) | OCPP 1.6j **WebSocket server** (`0.0.0.0`, no connection auth, unvalidated charge-point id), `on_authorize` accepts every id_tag, cleartext MQTT. **§8.1: ✅ SEC-01/02/03 (untrusted-input validation), SEC-05/07 (pinned); 🔮 SEC-06 (run.py secret-leak test); Proposed SEC-04/08 (trust-boundary policy).** | OWASP ASVS V2/V4/V5/V6, **CWE-287**, **CWE-20**, **CWE-400**, **CWE-532** |
| **energy-manager** | Loads InfluxDB / HA / Telegram / Smart-car **secrets** from env and issues **hardware-control** commands via the HA API. No case asserts secrets don't reach logs / InfluxDB / MQTT, or that control values are bounded. | ASVS V6 (secrets), **CWE-532** (log exposure), **CWE-306** |
| **swiss-solar-forecast** | Ingests **external weather data over HTTP** and parses GRIB (eccodes) + JSON metadata — untrusted external input. No case for malformed/oversized input, transport verification, or parser-failure handling. | ASVS V5, **CWE-20**, **CWE-494** (no integrity check on fetched data) |
| **load-forecast** | Smallest surface — reads InfluxDB with a token, writes a forecast bucket. No security case (and no test code at all). | ASVS V6 (secret handling) |

Cross-cutting: all four load tokens from env (HA UI) and reach InfluxDB / HA / MQTT over the LAN in
cleartext; no test pins the **secret-non-leak** or the transport assumptions. Close each gap by adding
security cases — tagged with the CWE/ASVS IDs above and scored by CVSS — to the owning add-on's test
chapter.

Each case's `Status` column in the owning FSD is authoritative; this table is the overview.
