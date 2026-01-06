#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# SwissSolarForecast Add-on
# Runs the PV forecast service
# ==============================================================================

bashio::log.info "Starting SwissSolarForecast..."

cd /app
exec python3 /app/run.py
