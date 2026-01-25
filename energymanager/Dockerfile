# Home Assistant Add-on: EnergyManager
# Battery discharge optimization using PV and load forecasts

ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system dependencies
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

# Copy s6-overlay service definition
COPY rootfs /

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Labels for Home Assistant
LABEL \
    io.hass.name="EnergyManager" \
    io.hass.description="Battery optimization using PV and load forecasts" \
    io.hass.arch="aarch64|amd64|armv7" \
    io.hass.type="addon" \
    io.hass.version="1.4.0"
