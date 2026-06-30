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
