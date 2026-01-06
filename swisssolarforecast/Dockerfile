# Home Assistant Add-on: SwissSolarForecast
# Swiss PV power forecast using MeteoSwiss ICON ensemble data

ARG BUILD_FROM
FROM $BUILD_FROM

# Install system dependencies
# eccodes is required for GRIB file parsing
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy \
    py3-pandas \
    eccodes \
    build-base \
    python3-dev \
    gfortran \
    openblas-dev \
    && rm -rf /var/cache/apk/*

# Set up Python environment
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy source code
COPY src/ /app/src/
COPY run.py /app/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV ECCODES_DIR=/usr

# Labels for Home Assistant
LABEL \
    io.hass.name="SwissSolarForecast" \
    io.hass.description="Swiss PV forecast using MeteoSwiss ICON data" \
    io.hass.arch="aarch64|amd64|armv7" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"

# Run the application
CMD ["python3", "/app/run.py"]
