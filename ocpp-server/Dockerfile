ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python dependencies
RUN apk add --no-cache python3 py3-pip

# Copy requirements and install
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY run.py /
COPY src /src/

# Copy rootfs (s6-overlay services)
COPY rootfs /
