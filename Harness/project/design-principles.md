# Project — Design principles

Project-wide principles that govern how the Energy-Management system is built. They hold across all
four add-ons.

1. **Deterministic core logic** — all numerical calculations produce identical results for identical
   inputs.
2. **Probabilistic uncertainty** — P10/P50/P90 percentiles quantify forecast uncertainty.
3. **InfluxDB as single source of truth** — all data is stored as time series.
4. **Rolling horizon** — decisions are recalculated every 5–15 minutes.
5. **Decoupled components** — each add-on operates independently with clear interfaces; they
   cooperate only through InfluxDB buckets and Home Assistant entities.
6. **Power for storage, energy for calculations** — forecasts are stored as power (W) and converted
   to energy (Wh) only when needed.
7. **Trusted-LAN security posture** — the suite runs on a private home LAN behind the router. **LAN
   traffic is trusted and not encrypted** (MQTT, InfluxDB, HA REST, and the OCPP WebSocket run in
   cleartext); confidentiality relies on the network boundary, not transport crypto. Two corollaries:
   *secrets are still protected* — API tokens are never logged or placed in payloads/entity
   attributes (only used in request auth headers); and *device authorization is not used* — the
   single wallbox is trusted, so RFID/id_tag authorization is accept-all. This posture
   is the reference for the ocpp-server security cases (FSD §8.1: SEC-04, SEC-05, SEC-08).
