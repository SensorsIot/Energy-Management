# Home Assistant Add-on: SwissSolarForecast
# Swiss PV power forecast using MeteoSwiss ICON ensemble data

ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libeccodes-dev \
    libeccodes-tools \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Set up Python environment
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy source code and config
COPY src/ /app/src/
COPY run.py /app/
COPY config_pv.yaml /app/

# Copy s6-overlay service definition and template
COPY rootfs /

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV ECCODES_DIR=/usr

# Labels for Home Assistant
LABEL \
    io.hass.name="SwissSolarForecast" \
    io.hass.description="Swiss PV forecast using MeteoSwiss ICON data" \
    io.hass.arch="aarch64|amd64|armv7" \
    io.hass.type="addon" \
    io.hass.version="1.1.6"
