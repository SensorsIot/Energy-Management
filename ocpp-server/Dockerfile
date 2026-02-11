# Home Assistant Add-on: OCPP Server
# OCPP 1.6J WebSocket server for EV wallbox control via HA entities

ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Set up Python environment
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy source code
COPY src/ /app/src/
COPY run.py /app/

# Copy rootfs (s6-overlay services)
COPY rootfs /

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Labels for Home Assistant
LABEL \
    io.hass.name="OCPP Server" \
    io.hass.description="OCPP 1.6J server for EV wallbox control via HA entities" \
    io.hass.arch="aarch64|amd64|armv7" \
    io.hass.type="addon" \
    io.hass.version="0.5.0"
