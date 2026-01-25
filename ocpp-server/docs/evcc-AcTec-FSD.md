# AcTec Wallbox Integration with evcc - FSD

## AcTec Wallbox Integration Solution

### Problem
The AcTec wallbox generates malformed WebSocket URLs with double slashes (e.g., `ws://192.168.0.150:8887//AcTec001`) which evcc cannot handle directly. This prevents proper OCPP 1.6 communication between the wallbox and evcc.

### Solution: WebSocket Proxy
A custom WebSocket proxy has been developed to solve this integration issue by acting as an intermediary between the AcTec wallbox and evcc.

#### Proxy Features
- **URL Cleaning**: Automatically fixes malformed URLs by removing double slashes
- **OCPP Protocol Support**: Handles `Sec-WebSocket-Protocol: ocpp1.6` negotiation
- **Bidirectional Message Forwarding**: Transparent message passing between wallbox and evcc
- **Error Handling**: Robust connection management and error recovery
- **Debug Logging**: Comprehensive logging for troubleshooting

#### Architecture
```
AcTec Wallbox → WebSocket Proxy → evcc
Port 8888         Port 8888      Port 8887
```

#### Implementation Details
- **Language**: Python 3.11+ with websockets library
- **Deployment**: Home Assistant custom add-on
- **Configuration**: Configurable listen/target ports and hosts
- **Protocol**: Full OCPP 1.6-J support with proper subprotocol handling

#### Verified OCPP Communication
The proxy successfully enables complete OCPP communication including:
- BootNotification handshake
- Heartbeat messages
- StatusNotification updates
- GetCompositeSchedule requests
- TriggerMessage commands
- MeterValues reporting

#### Usage
1. **Configure wallbox** to connect to: `ws://[ha-ip]:8888/AcTec001`
2. **Configure evcc** to listen on: `ws://[ha-ip]:8887/AcTec001`
3. **Deploy proxy** as Home Assistant add-on on port 8888
4. The proxy automatically handles URL cleaning and protocol negotiation

This solution provides a robust, production-ready bridge for AcTec wallbox integration with evcc, enabling full OCPP functionality without requiring firmware modifications to the wallbox.

## Automatic Proxy Startup

To ensure the WebSocket proxy starts automatically on system boot, you can set up a systemd service.

### Step 1: Create Systemd Service File

Create a systemd service file for the proxy:

```bash
sudo nano /etc/systemd/system/ws-proxy.service
```

Add the following content:

```ini
[Unit]
Description=WebSocket Proxy for AcTec Wallbox
Requires=network-online.target
After=syslog.target network.target network-online.target
Wants=network-online.target
StartLimitIntervalSec=10
StartLimitBurst=10

[Service]
Type=simple
ExecStart=/usr/bin/python3 /home/opcc/ws_proxy_ocpp.py --listen-host=0.0.0.0 --listen-port=8888 --target-host=192.168.0.150 --target-port=8887
Restart=always
RestartSec=10
User=opcc
Group=opcc
WorkingDirectory=/home/opcc

# Environment variables (optional)
Environment="PYTHONUNBUFFERED=1"

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ws-proxy

[Install]
WantedBy=multi-user.target
```

### Step 2: Install Python Dependencies

Ensure the required Python packages are installed system-wide:

```bash
sudo apt update
sudo apt install python3 python3-pip
pip3 install websockets asyncio
```

### Step 3: Make Proxy Script Executable

```bash
chmod +x /home/opcc/ws_proxy_ocpp.py
```

### Step 4: Enable and Start Service

```bash
# Reload systemd to recognize the new service
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable ws-proxy.service

# Start the service immediately
sudo systemctl start ws-proxy.service

# Check service status
sudo systemctl status ws-proxy.service
```

### Step 5: Verify Service Operation

Check that the service is running correctly:

```bash
# View service logs
sudo journalctl -u ws-proxy.service -f

# Check service status
sudo systemctl status ws-proxy.service

# Test connectivity
netstat -tlnp | grep 8888
```

### Service Management Commands

```bash
# Start the proxy service
sudo systemctl start ws-proxy.service

# Stop the proxy service
sudo systemctl stop ws-proxy.service

# Restart the proxy service
sudo systemctl restart ws-proxy.service

# Check service status
sudo systemctl status ws-proxy.service

# View service logs
sudo journalctl -u ws-proxy.service

# View real-time logs
sudo journalctl -u ws-proxy.service -f
```

### Configuration Notes

- **Script Path**: Update `/home/opcc/ws_proxy_ocpp.py` to match your actual proxy script location
- **User/Group**: Change `opcc` to the appropriate user account that should run the proxy
- **Network Settings**: Adjust `--target-host` and `--target-port` to match your evcc installation
- **Listen Port**: The proxy listens on port 8888 by default, ensure this port is available

### Alternative: Docker Deployment

For containerized deployment, you can also use Docker:

```dockerfile
FROM python:3.11-slim

RUN pip install websockets

COPY ws_proxy_ocpp.py /app/ws_proxy_ocpp.py
WORKDIR /app

EXPOSE 8888

CMD ["python3", "ws_proxy_ocpp.py", "--listen-host=0.0.0.0", "--listen-port=8888", "--target-host=192.168.0.150", "--target-port=8887"]
```

Build and run:

```bash
docker build -t ws-proxy .
docker run -d --name ws-proxy --restart=unless-stopped -p 8888:8888 ws-proxy
```