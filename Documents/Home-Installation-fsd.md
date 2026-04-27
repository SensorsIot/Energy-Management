# Home Installation

**Last Updated:** 2026-04-27

## 1. Overview
This document consolidates operational knowledge needed to maintain the home infrastructure and automation stack.

Key scope
- Host and virtualization: Proxmox with VMs/containers for services.
- Core services: IoTstack, Home Assistant, MQTT, InfluxDB, Grafana.
- Field systems: Zigbee devices, smart meter (MBUS/gPlug/Tasmota), PV plant (Huawei SUN2000), wallbox/EV charging (OCPP Server add-on).
- Data flow: sensor data -> MQTT -> HA/InfluxDB -> Grafana/HA dashboards.

Support goals
- Keep services running (restart/restore/upgrade paths).
- Preserve data integrity (backups, retention, migration steps).
- Enable quick recovery (known commands, file locations, and dependencies).

Operational notes
- Credentials and tokens exist across the legacy docs; consolidate into a secured secret store and remove from plaintext references.
- ESPHome YAML credentials were migrated to `!secret` references on 2026-04-27. Rotate previously exposed values later and keep reusable credentials in `/homeassistant/esphome/secrets.yaml`.
- Use a change log for system modifications and document versions.

Secrets store (proposal)
- File: D:\Dropbox\Documentation\AAHome\Secrets Store.md
- Store all passwords, tokens, API keys, and client secrets here, then delete them from the legacy docs.
- Restrict access to this file and rotate any exposed secrets.


Notes / gaps to resolve
- ~~Hardware inventory~~ Proxmox host documented in §4, devices in §15.
- ~~Network inventory~~ Full MikroTik config in §3 (VLANs, IPs, firewall, NAT).
- ~~Service owners and access methods~~ SSH, web UIs documented throughout.
- ~~Wi-Fi SSIDs and IoT network segmentation~~ Documented in §3.3.
- ~~Firewall rules and port forwarding~~ Documented in §3.4, §3.6.
- Backup policy and storage targets (locations, retention, restore drills).
- ~~DHCP static lease device identification~~ Full inventory in §3.7 (5 devices still unidentified: .128, .130, .131, .146, .151).

## 2. Architecture and data flow
High-level service graph
- Proxmox host (192.168.0.230) runs 8 VMs + 1 LXC container.
- **HA OS VM** (192.168.0.202): Home Assistant with add-ons (ESPHome, Modbus-proxy, Energy forecasting, OCPP Server, Nginx Proxy Manager).
- **IoTstack VM** (192.168.0.203): Docker containers for Mosquitto (MQTT), Node-RED, InfluxDB 2.x, Grafana, Portainer.
- **Zigbee:** ZHA integration directly in HA (not Zigbee2MQTT). USB coordinator passed through from Proxmox.
- **Field devices:** ESPHome (alarm bell, water meter, mailbox, BT proxy), Tasmota (Enphase), Shelly (3EM, 2PM), Zigbee sensors, 433 MHz weather gateway.
- **External:** MS365 Calendar, MeteoSwiss weather, Smart #1 car (smarthashtag), Telegram notifications.

ASCII diagrams (support view)
Proxmox layout (VMs in parallel)
```
+-------------------------+
|       Proxmox Host      |
|    192.168.0.230        |
+-----------+-------------+
            |
   +--------+--------+     +--------+--------+
   |   IoTstack VM   |     |   HA OS VM     |
   | (docker-compose)|     | (Home Assistant)|
   |  192.168.0.203  |     |  192.168.0.202  |
   +-----------------+     +-----------------+
```

IoTstack VM (service flow)
```
          +-------------------+
          |    IoTstack VM    |
          +---------+---------+
                    |
   +--------+   +---+----+   +--------+   +---------+
   |  MQTT  |<->| Node-RED |->| InfluxDB|<-| Grafana |
   | :1883  |   |  :1880   |  |  :8087  |  |  :3000  |
   +--------+   +----------+  +---------+  +---------+
        |
   +----+-----+
   | Zigbee2  |
   |  MQTT    |
   +----+-----+
        |
     Zigbee devices
```

HA OS VM (core + add-ons)
```
          +------------------+
          |     HA OS VM     |
          +--------+---------+
                   |
          +--------+---------+
          |  Home Assistant  |
          |     :8123        |
          +--------+---------+
                   |
          +--------+-----------------------------+
          |            HA Add-ons                |
          |  - ESPHome                           |
          |  - modbus-proxy (:502)               |
          |  - SwissSolarForecast, LoadForecast  |
          |  - EnergyManager, OCPP Server        |
          +--------+-----------------------------+
                   |
      +------------+-------------+-------------+
      |                          |             |
  Smart meter                  PV plant       Wallbox
  (gPlug/Tasmota)              (SUN2000)      (OCPP add-on)
```

Primary data flow (support view)
- Zigbee devices -> ZHA -> Home Assistant (direct, no Zigbee2MQTT).
- Tasmota/433 MHz devices -> MQTT broker -> Home Assistant.
- Node-RED -> InfluxDB (time series storage) -> Grafana (dashboards).
- Home Assistant -> InfluxDB (HomeAssistant bucket) -> energy bucket consolidation tasks.
- Huawei SUN2000 data -> InfluxDB (HuaweiNew bucket) -> EnergyV1 bucket via InfluxDB tasks.
- Smart meter (gPlug) -> MQTT bridge from provisioning server -> local MQTT -> Home Assistant -> InfluxDB.
- Wallbox -> OCPP Server add-on -> Home Assistant (status/controls and power limit).

Important paths/locations for support
- InfluxDB data: /var/lib/influxdb
- Grafana data: /var/lib/grafana/grafana.db
- IoTstack compose project: ~/IOTstack (menu.sh creates docker-compose.yml)

Notes / gaps to resolve
- Confirm which VM or container hosts each service (HA, MQTT, Node-RED, InfluxDB, Grafana, Zigbee2MQTT).
- Document IPs/hostnames and service ports for each component.
- Determine the authoritative MQTT topic namespace and discovery conventions.
- Confirm InfluxDB version split (v1 vs v2) and which services connect to which.

## 3. Network and addressing

### 3.1 Router

| Item | Value |
|------|-------|
| Model | MikroTik RB5009UPr+S+ |
| RouterOS | 7.20.1 |
| Serial | HEJ08KP6767 |
| Management IP | 192.168.0.1 |
| WebFig HTTPS | port 8443 (www-ssl with Let's Encrypt cert) |
| SSH | port 22 (restricted to 192.168.0.0/24 and WireGuard) |
| WAN | P1-WAN (DHCP client to ISP) |
| DNS | AdGuard (192.168.0.101) primary, 1.1.1.1 fallback |
| NTP | ch.pool.ntp.org, broadcasts to all networks |
| DynDNS | MikroTik Cloud (`/ip cloud ddns-enabled=yes`), hostname: hej08kp6767.sn.mynetname.net |
| Config export | `C:\Users\AndreasSpiess\Downloads\config.rsc` (2026-03-31) |

### 3.2 VLANs and Network Segments

All VLANs are tagged on the bridge and trunked to APs via CAPsMAN.

| VLAN | Name | Subnet | Gateway | DHCP Pool | Purpose |
|------|------|--------|---------|-----------|---------|
| 100 | Intern | 192.168.0.0/24 | 192.168.0.1 | .2–.229 | Main LAN — all servers, PCs, HA, Proxmox |
| 200 | Guest | 172.16.0.0/24 | 172.16.0.1 | .10–.250 | Guest Wi-Fi (internet only, no LAN access) |
| 555 | IoT | 10.0.0.0/24 | 10.0.0.1 | .5–.200 | IoT devices Wi-Fi |
| 300 | Remote | 10.99.4.0/24 | 10.99.4.1 | .2–.254 | Remote station (Flex radio) |
| 20 | VoIP | - | - | - | VoIP phones (AREDN) |
| 2 | DtD | - | - | - | Device-to-Device (AREDN mesh) |
| 400 | AREDN-Mgmt | - | - | - | AREDN management consoles |

**Bridge port assignments:**

| Port | Label | PVID | Tagged VLANs | Notes |
|------|-------|------|-------------|-------|
| P1 | P1-WAN | - | - | WAN uplink (not bridged) |
| P2 | P2-Proxmox | 100 | 2,20,100,200,555,300,400 | Proxmox host (trunk) |
| P3 | P3-AP2-Keller | - | 2,20,100,200,555,300,400 | AP basement (trunk) |
| P4 | P4-2-Stock | - | 2,20,100,200,555,300,400 | AP 2nd floor (trunk) |
| P5 | P5-PC | 100 | 20,40 | Main PC |
| P6 | P6-Desk-Gelb | 100 | 2,20,300,400 | Desk (yellow cable) |
| P7 | P7-Keller | 100 | - | Basement wired |
| P8 | P8-REOLINK | 100 | - | Camera |
| SFP+ | sfp-sfpplus1 | 100 | - | 2.5G uplink |

### 3.3 Wi-Fi SSIDs (CAPsMAN)

| SSID | Band | VLAN | Security | Purpose |
|------|------|------|----------|---------|
| `private-2G` | 2.4 GHz | 100 | WPA2-PSK | Main network |
| `private-5G` | 5 GHz | 100 | WPA2-PSK | Main network |
| `Guest-2G` | 2.4 GHz | 200 | WPA2-PSK | Guest (internet only) |
| `Guest-5G` | 5 GHz | 200 | WPA2-PSK | Guest (internet only) |
| `IoT` | 2.4 GHz | 555 | WPA2-PSK | IoT devices |
| `VoIP-2G` | 2.4 GHz | 20 | WPA/WPA2-PSK | VoIP phones |
| `VoIP-5G` | 5 GHz | 20 | WPA/WPA2-PSK | VoIP phones |
| `Birch` | 2.4 GHz | 300 | WPA/WPA2-PSK | Remote station |

Wi-Fi passwords stored in Secrets Store (`D:\Dropbox\Documentation\AAHome\Secrets Store.md`).

### 3.4 Port Forwarding (NAT)

| WAN Port | Protocol | Destination | Purpose |
|----------|----------|-------------|---------|
| 443 | TCP | 192.168.0.202:443 | HA Nginx HTTPS |
| 8443 | TCP | 192.168.0.202:443 | HA Nginx HTTPS (alt port for DynDNS) |
| 80 | TCP | 192.168.0.202:80 | HA Nginx HTTP |
| 5525 | TCP | 192.168.0.208 | AREDN tunnel server |
| 5525–5570 | UDP | 192.168.0.208 | AREDN WireGuard tunnels |
| 6526–6550 | UDP | 192.168.0.199 | AREDN supernodes |
| 51820 | UDP | 192.168.0.174:51820 | LEG-Provisioning WireGuard |
| 5000 | TCP | 192.168.0.174:5000 | LEG-Provisioning API |

### 3.5 VPN

**WireGuard** (port 13231 on router):

| Peer | Allowed addresses | Status |
|------|------------------|--------|
| iPhone | 10.99.5.2/32 | Disabled |
| toBirch | 192.168.32.2/32, 10.99.6.0/24 | Disabled |
| ToTeltonika | 10.99.7.2/32, 10.99.6.1/24 | Disabled |
| toRUTX14 | 10.99.7.3/32, 10.99.6.0/24 | Active |
| toATL | 10.99.7.33/32, 10.99.5.0/24, 192.168.0.0/24 | Active |

**MikroTik Cloud VPN** (back-to-home-vpn, port 58859): Enabled for remote access via MikroTik Cloud.

**ZeroTier** (zt1): Two networks configured but **disabled**.

### 3.6 Firewall Rules Summary

- **Guest network:** DNS, DHCP, NTP allowed; all other LAN access blocked; internet only.
- **IoT network:** Classified as Guest in interface list (same restrictions apply).
- **WAN input:** WireGuard (13231/UDP), established/related accepted; all else dropped.
- **WAN forward:** Only DSTNAT traffic forwarded; invalid dropped.
- **Fast-track:** Enabled for established/related connections with hardware offload.
- **IPv6:** Disabled (`disable-ipv6=yes`).
- **SMB:** Allowed from LAN only (port 445).

### 3.7 IP Address Reference

**Key services:**

| Device | IP | Port | Service |
|--------|-----|------|---------|
| Router | 192.168.0.1 | 8443 | WebFig HTTPS |
| Home Assistant | 192.168.0.202 | 8123 | Web UI |
| HA Nginx Proxy Manager | 192.168.0.202 | 81 | NPM Admin |
| Modbus Proxy | 192.168.0.202 | 502 | Huawei Modbus |
| IOTstack | 192.168.0.203 | - | Docker host |
| InfluxDB | 192.168.0.203 | 8087 | API |
| Grafana | 192.168.0.203 | 3000 | Dashboards |
| MQTT | 192.168.0.203 | 1883 | Broker |
| Node-RED | 192.168.0.203 | 1880 | Flows |
| Portainer | 192.168.0.203 | 9000 | Container mgmt |
| Proxmox | 192.168.0.230 | 8006 | Management |
| PBS | 192.168.0.236 | 8007 | Backup Server |
| AdGuard (DNS) | 192.168.0.101 | 80/53 | DNS + adblock |
| Dev VM (dev-1) | 192.168.0.160 | 22 | Development |
| RTL_433 Gateway | 192.168.0.15 | MQTT | 433 MHz receiver |
| Wallbox | 192.168.0.81 | 80 | API |
| gPlug Smart Meter | 192.168.0.52 | 80 | Captive portal |
| gPlug Provisioning | provision.dhamstack.com | 5000/8883 | HTTP/MQTT TLS |
| HA External URL | hej08kp6767.sn.mynetname.net | 8443 | DynDNS |

**DHCP static and dynamic leases** (all devices on VLAN 100, inventory 2026-03-31):

| IP | MAC | Device | Manufacturer | Function |
|----|-----|--------|-------------|----------|
| 192.168.0.3 | 34:5F:45:AB:E4:58 | ESPHome AlarmBell | Espressif | Meeting bell → HA (ESPHome) |
| 192.168.0.9 | 88:FC:A6:23:5D:21 | Devolo Magic Powerline | Devolo | Network infra (powerline) |
| 192.168.0.10 | F0:2F:74:D0:D4:43 | Andreas WiFi (ASUS PC) | ASUSTek | User workstation |
| 192.168.0.11 | B4:2E:99:96:43:85 | DESKTOP-HB9BLA | Gigabyte | User workstation |
| 192.168.0.12 | 88:FC:A6:21:29:09 | Devolo Magic Powerline | Devolo | Network infra (powerline) |
| 192.168.0.13 | 54:2B:1C:BF:CC:7A | MikroTik AP-1 (alt IP) | MikroTik | CAPsMAN AP (also .180) |
| 192.168.0.15 | D4:D4:DA:9D:7C:D8 | RTL_433 WeatherStation | Espressif | 433 MHz → MQTT → HA |
| 192.168.0.17 | 88:71:B1:6A:2F:E4 | UPC-TV-BOX | UPC | TV set-top box |
| 192.168.0.20 | AC:67:B2:F9:C4:38 | ESP32 Bluetooth Proxy | Espressif | BLE proxy → HA (ESPHome) |
| 192.168.0.23 | 00:23:24:9E:43:D0 | Unknown ESP (APKIZ21606) | Espressif | Unknown — responds to ping |
| 192.168.0.33 | 30:AE:A4:84:5A:4C | tablet_switch | Espressif | Tablet charging control → HA (ESPHome) |
| 192.168.0.49 | 3A:5D:77:EF:B1:D2 | Android phone | Android | Mobile device |
| 192.168.0.52 | B0:81:84:25:22:5C | gPlug Smart Meter | gPlug | MBUS smart meter → HA |
| 192.168.0.72 | D0:CF:13:09:65:80 | IOS-Keyboard Debug ESP32 | Espressif | Custom ESP32 project |
| 192.168.0.87 | 00:E0:4C:53:44:58 | Raspberry Pi 2W (Workbench) | Raspberry Pi | Workbench PC |
| 192.168.0.98 | 74:D4:DD:1F:BC:8D | FlexRadio 6600 | FlexRadio | Ham radio transceiver |
| 192.168.0.99 | 10:05:01:4F:8A:57 | DESKTOP-HB9BLA (alt NIC) | Intel | User workstation (wired) |
| 192.168.0.100 | 4C:82:A9:E8:D7:58 | Brother MFC-L3760CDW | Brother | Laser printer |
| 192.168.0.101 | BC:24:11:CF:08:6E | AdGuard DNS (LXC) | Proxmox | DNS/adblock server |
| 192.168.0.110 | 30:05:05:DE:29:02 | Grosser Laptop WLAN | ASUS | Laptop (wireless) |
| 192.168.0.120 | 54:32:04:77:6D:AC | Wallbox ESP32 (OCPP) | AcTec | Wallbox controller → HA |
| 192.168.0.128 | 06:76:66:31:03:BE | Unknown | Unknown | Responds to ping, no services |
| 192.168.0.130 | 4E:4D:AF:7C:34:CD | Unknown | Unknown | Offline |
| 192.168.0.131 | 12:BE:9F:AF:3F:E3 | Unknown | Unknown | Offline |
| 192.168.0.132 | B4:E6:2D:57:BE:65 | ESPHome AlarmBell (dynamic) | Espressif | Duplicate of .3 (dynamic lease) |
| 192.168.0.146 | 98:0D:AF:27:91:BC | sensorsiot | Unknown | Unknown sensor — offline |
| 192.168.0.150 | BC:24:11:9B:18:3D | EVCC (EV Charge Controller) | Proxmox | EV charging management → HA |
| 192.168.0.151 | 7C:9E:BD:F2:14:C8 | Unknown ESP32 (F214C8) | Espressif | Unknown |
| 192.168.0.160 | BC:24:11:09:78:43 | Dev VM (dev-1) | Proxmox | Development VM |
| 192.168.0.171 | EA:F6:0A:CB:5A:48 | SMLIGHT SLZB-MR1U | SMLIGHT | Zigbee coordinator → HA (ZHA) |
| 192.168.0.172 | 3C:0B:59:36:EE:42 | EARU Breaker (ESPHome) | ESPHome | Wallbox phase switch → HA |
| 192.168.0.173 | D0:CF:13:30:F8:34 | Unknown ESP32 | Espressif | Offline |
| 192.168.0.174 | BC:24:11:48:70:B9 | LEG-Provisioner VM | Proxmox | AREDN LEG provisioning |
| 192.168.0.175 | 24:6F:28:4E:D8:A4 | Unknown ESP32 (4ED8A4) | Espressif | Offline |
| 192.168.0.176 | 10:06:1C:98:A5:54 | BLLED (Bambu Lab LED) | Espressif | 3D printer LED control |
| 192.168.0.177 | 94:A9:90:47:5B:48 | Modbus Proxy ESP32 | Espressif | Huawei SUN2000 → HA (Modbus) |
| 192.168.0.178 | 5C:15:C5:02:12:B7 | Netgear GS108 Switch | Netgear | Network infra (managed switch) |
| 192.168.0.179 | B4:E6:2D:85:96:65 | ESPHome Water Meter | Espressif | Pulse counter → HA (ESPHome) |
| 192.168.0.180 | 74:4D:28:13:7E:A5 | MikroTik AP-2 (CAPsMAN) | MikroTik | Wi-Fi access point |
| 192.168.0.181 | 74:4D:28:13:87:34 | MikroTik AP-1 (CAPsMAN) | MikroTik | Wi-Fi access point |
| 192.168.0.182 | 04:91:62:58:A2:6C | Judo water treatment | Microchip | eWAC water softener (web UI) |
| 192.168.0.183 | EC:71:DB:2A:88:DB | Reolink Camera | Reolink | IP camera (nginx web UI) |
| 192.168.0.184 | C4:DE:E2:0F:F5:18 | Sonoff POW Elite (Enphase) | Espressif | Tasmota energy monitor → HA |
| 192.168.0.185 | 60:01:94:98:DA:EF | LabLight Sonoff Dual | Espressif | Lab lighting → HA (ESPHome) |
| 192.168.0.186 | 44:17:93:A7:CF:34 | Shelly 2PM White | Espressif | Lab lights relay → HA (Shelly) |
| 192.168.0.187 | 24:62:AB:CA:1A:C8 | Mailbox Notifier | Espressif | BME280 + reed switch → HA (ESPHome) |
| 192.168.0.191 | EC:FA:BC:C7:F0:F5 | Shelly 3EM | Espressif | 3-phase energy monitor → HA |
| 192.168.0.192 | D8:A0:1D:40:4A:50 | APRS iGate HB9BLA-10 | Espressif | Ham radio APRS gateway |
| 192.168.0.193 | F4:12:FA:D9:4D:8C | NTRIP-X | Espressif | GNSS NTRIP caster |
| 192.168.0.195 | B8:27:EB:63:9F:B1 | NTP Server (Raspberry Pi) | Raspberry Pi | Network time server |
| 192.168.0.196 | 78:9A:18:A8:E6:69 | RoofTop MikroTik | MikroTik | Rooftop router (RouterOS) |
| 192.168.0.197 | 08:C2:24:24:22:FE | Amazon Fire Tablet | Amazon | HA Kiosk display (Fully Kiosk) |
| 192.168.0.198 | 02:1C:F4:42:3E:79 | HB9BLA-VM-1 | Proxmox | AREDN mesh node VM |
| 192.168.0.199 | 02:34:61:A4:78:24 | HB9BLA-BASEL-SUPERNODE | Proxmox | AREDN mesh supernode VM |
| 192.168.0.200 | D8:A0:1D:40:49:D0 | TinyGS | Espressif | Satellite ground station |
| 192.168.0.202 | 96:53:6C:2A:85:8E | Home Assistant OS VM | Proxmox | Core home automation |
| 192.168.0.203 | D6:7D:B2:2E:94:B7 | IOTstack (Docker host) | Proxmox | MQTT/InfluxDB/Grafana/Node-RED |
| 192.168.0.204 | B8:27:EB:5E:02:0E | Pi-Star (DMR Hotspot) | Raspberry Pi | Ham radio DMR hotspot |
| 192.168.0.205 | E8:4E:06:26:BD:6F | TTN LoRa Gateway | Raspberry Pi | LoRaWAN gateway |
| 192.168.0.206 | A4:17:8B:1F:F1:49 | Huawei WiFi Dongle (Andreas) | Huawei | SUN2000 WiFi dongle |
| 192.168.0.207 | A4:17:8B:1F:F1:4D | Huawei WiFi Dongle (Hugo) | Huawei | SUN2000 WiFi dongle |
| 192.168.0.208 | 92:E6:52:0D:E8:AA | HB9BLA-VM-TUNNELSERVER | Proxmox | AREDN tunnel server VM |
| 192.168.0.209 | DC:A6:32:00:6C:74 | kxyTrack (Raspberry Pi) | Raspberry Pi | Tracking device — offline |
| 192.168.0.211 | 56:95:72:3B:00:1D | FreePBX | Proxmox | VoIP PBX |
| 192.168.0.213 | 18:FD:74:4F:F5:65 | MikroTik CRS310 | MikroTik | 10G network switch |
| 192.168.0.230 | F8:75:A4:05:58:E3 | Proxmox Server (Lenovo) | Lenovo | Hypervisor host |
| 192.168.0.237 | 24:58:7C:DF:E0:C8 | Bambu Lab P1S | Bambu Labs | 3D printer |

**SSH access** (master ed25519 key):

| Host | User |
|------|------|
| 192.168.0.202 (Home Assistant) | root |
| 192.168.0.203 (IOTstack) | pi |
| 192.168.0.230 (Proxmox) | root |
| 192.168.0.160 (dev-1) | dev |
| 192.168.0.101 (AdGuard) | root |
| provision.dhamstack.com | root |
| GitHub | SensorsIot |

## 4. Proxmox host
Support summary (from legacy Proxmox doc)
- Install Proxmox and ensure host is stable before creating VMs.
- For Debian-based VMs: use netinst ISO, graphical install, add SSH.
- VM defaults: CPU type = Host, network = VirtIO.
- Ensure locale/time zone and hostname are set correctly after install.

Current host details (from 192.168.0.230)
- Hostname: Proxmox
- Proxmox VE: 8.4.14 (kernel 6.8.12-17-pve)
- Hardware:
  - CPU: Intel Core i5-8400T @ 1.70GHz (6 cores, 6 threads)
  - RAM: 32 GB total
  - Running VMs/CTs use ~25 GB, ~6 GB available
- Management settings: email_from asarumba@gmail.com, keyboard de-ch
- Network:
  - vmbr0: 192.168.0.230/24, gateway 192.168.0.1, bridge to eno1
  - eno1 EEE off; TSO/GSO off (via ethtool post-up)
- Storage:
  - local: /var/lib/vz (iso, vztmpl, backup) - 94 GB total, 19% used
  - local-lvm: LVM thinpool (images, rootdir) - 794 GB total
  - pbs: Proxmox Backup Server at 192.168.0.236 (datastore Backup)
  - ext: /mnt/ext (backup) - 94 GB total, 18% used
- Backup job:
  - vzdump job: daily 01:00, snapshot mode, storage pbs, keep-all=1
  - notifications: always, to andreas.spiess@arumba.com

Operational details (to verify on host)
- SSH access enabled on Proxmox host.
- NTP/time zone aligned across host and VMs.
- USB passthrough used for Zigbee dongle.
- SMB mounts may be used for backup or data access.

Notes / gaps to resolve
- Which devices are passed through (USB IDs, target VMs).

## 5. Proxmox
### VM and LXC overview
All VMs and containers (inventory 2026-03-27)
- QEMU VMs: 100 HA, 101 IOTstack, 102 Birch, 103 AREDN-local, 104 AREDN-Tunnel, 105 AREDN-Supernode, 107 FreePBX, 108 debian-ztnet, 119 dev-1, 1000 debian-cloudinit.
- LXC CTs: 106 LEG-Provisioner, 113 aredn-dev, 114 reverse, 115 esp-idf, 117 evcc (decommissioned), 118 adguard, 130 MasterOfDesaster.

Memory allocation summary:
- Total host RAM: 32 GB
- Running VMs: ~30 GB allocated (HA 6GB, IOTstack 14GB, Birch 2GB, AREDN nodes 768MB, FreePBX 2GB, dev-1 5GB)
- Running LXC: ~512 MB allocated (adguard)

Inventory table (updated 2026-03-27)
| ID | Type | Name | Memory | Disk | Status | Description | Remark |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 100 | VM | HA | 6 GB | 50 GB | running | Home Assistant OS VM (core + add-ons). | USB passthrough for Zigbee dongle. |
| 101 | VM | IOTstack | 14 GB | 92 GB | running | IoTstack VM hosting docker-compose services (MQTT, Node-RED, InfluxDB, Grafana, Zigbee2MQTT). |  |
| 102 | VM | Birch | 2 GB | 40 GB | running | Birch VM. |  |
| 103 | VM | AREDN-local | 256 MB | - | running | AREDN node (local). |  |
| 104 | VM | AREDN-Tunnel | 256 MB | 0.5 GB | running | AREDN tunnel node. |  |
| 105 | VM | AREDN-Supernode | 256 MB | 0.5 GB | running | AREDN supernode. |  |
| 107 | VM | FreePBX | 2 GB | 32 GB | running | FreePBX VoIP server. |  |
| 108 | VM | debian-ztnet | 2 GB | 4 GB | stopped | Debian helper VM (proxmox-helper-scripts). |  |
| 119 | VM | dev-1 | 5 GB | 100 GB | running | Development VM (Claude Code). |  |
| 1000 | VM | debian-cloudinit | 1 GB | 10 GB | stopped | Cloud-init template VM. |  |
| 106 | LXC | LEG-Provisioner | - | - | stopped | LEG provisioning container. |  |
| 113 | LXC | aredn-dev | 2 GB | - | stopped | AREDN dev container. |  |
| 114 | LXC | reverse | - | - | stopped | Reverse proxy container. |  |
| 115 | LXC | esp-idf | 2 GB | - | stopped | ESP-IDF build/dev container. | Passthrough: /dev/ttyACM0, /dev/ttyUSB0. |
| 117 | LXC | ~~evcc~~ | 2 GB | - | decommissioned | Replaced by OCPP Server HA add-on. | Can be deleted. |
| 118 | LXC | adguard | 512 MB | - | running | AdGuard (DNS/adblock). |  |
| 130 | LXC | MasterOfDesaster | - | - | stopped | MasterOfDesaster container. |  |

### Backup (Proxmox topic)
- Backup job: vzdump at 01:00, snapshot mode, storage `pbs`, prune keep-all=1.
- Backup target: Proxmox Backup Server `192.168.0.236` (datastore `Backup`).
- Additional backup storage: `ext` at `/mnt/ext` (content=backup).
- Notifications: always, to andreas.spiess@arumba.com.
- PBS wake/shutdown automation (from Proxmox Docu):
  - On Proxmox VE: `wakeonlan` installed; `wakebigstor.service` sends WOL to PBS MAC `00:23:24:9e:43:d0`.
  - `wakebigstor.timer` triggers at `02:58` (2 minutes before backup window).
  - On PBS: `autoshutdown` script checks for running tasks and shuts down after ~60s idle.
  - `autoshutdown.timer` starts 300s after boot to give clients time to start backups.

### Monitoring, alerting, and maintenance

- Health checks, logs, and update cadence.

## 6. IoTstack (192.168.0.203)

**Last inventory:** 2026-03-27

### 6.1 Host details

| Property | Value |
|----------|-------|
| Hostname | hub |
| OS | Debian 11 (Bullseye), kernel 5.10.0-19-amd64 |
| IP | 192.168.0.203/24 (ens18, DHCP) |
| Proxmox VM | 101 IOTstack |
| Memory | 14 GB allocated, ~5.2 GB used |
| Swap | 8 GB |
| Disk | 89 GB total, 38 GB used (44%) |

### 6.2 Docker containers

| Container | Status | Ports | Volume Size |
|-----------|--------|-------|-------------|
| influxdb2 | running (healthy) | 8087->8086 | 6.5 GB |
| grafana | running (healthy) | 3000 | 8.9 MB |
| nodered | running (not used by HA) | 1880 | 221 MB |
| mosquitto | running (healthy) | 1883 | 1.4 MB |
| portainer-ce | running | 8000, 9000, 9443 | 264 KB |

Docker-compose location: `~/IOTstack/docker-compose.yml`

Operations:
- Start: `cd ~/IOTstack && docker-compose up -d`
- Stop: `cd ~/IOTstack && docker-compose down`
- Logs: `docker logs <container> --tail 50`

### 6.3 InfluxDB 2.x

| Property | Value |
|----------|-------|
| Container | influxdb2 |
| Port | 8087 (external), 8086 (internal) |
| Organization | spiessa |
| Token | mounted at `/home/dev/.secrets/influxdb` in add-on containers |

**Buckets:**
| Bucket | Retention | Purpose |
|--------|-----------|---------|
| HomeData | infinite | Primary energy data (consolidated) |
| HomeAssistant | 1 year | Raw HA sensor data |
| EnergyV1 | infinite | Legacy energy data (archive) |
| energy_manager | infinite | Energy manager decisions/signals |
| load_forecast | 30 days | Load forecast predictions |
| pv_forecast | 30 days | PV forecast predictions |
| Water | infinite | Water meter data |
| Weight | infinite | Weight data |
| weather/autogen | infinite | Weather data |
| HugoNew | infinite | Hugo data |
| TempEnergy | infinite | Temporary energy data |
| YOUTUBE/autogen | infinite | YouTube data |

**Active tasks:**
| Task | Interval | Purpose |
|------|----------|---------|
| RegularDataLoadFromHomeAssistantNew | 1m | Copy HA sensor data to HomeData bucket (includes `grid_power` → MBUS/M_Grid mapping) |

### 6.4 Grafana

| Property | Value |
|----------|-------|
| URL | http://192.168.0.203:3000 |
| User | admin |
| Password | admin |
| API | Basic auth: `-u admin:admin` |

**Dashboards (updated 2026-03-27):**
| UID | Name | Tags | Purpose |
|-----|------|------|---------|
| LbosmjiR | Huawei | - | Main energy dashboard |
| LbosmjiRo | Huawei Longterm | - | Long-term energy analysis |
| df9feonzp10xsc | BatteryForecast | energy, forecast, load, pv | Battery/load/PV forecast display |
| afasawqszcz5sd | ForecastAccuracy | accuracy, forecast, pv | Forecast accuracy metrics |
| BZuC4SZRk | Weather | - | Weather data |
| ce5xr0eidnbpce | Water | - | Water consumption |
| leg-community | LEG Community | LEG, community, energy | LEG community overview |
| leg-grid | LEG Grid | LEG, energy, grid | LEG grid data |
| leg-house-1 | LEG House 1 | LEG, energy, house | LEG House 1 data |
| leg-house-2 | LEG House 2 | LEG, energy, house | LEG House 2 data |
| leg-house-3 | LEG House 3 | LEG, energy, house | LEG House 3 data |
| leg-house-4 | LEG House 4 | LEG, energy, house | LEG House 4 data |
| leg-house-5 | LEG House 5 | LEG, energy, house | LEG House 5 data |

### 6.5 Mosquitto (MQTT)

| Property | Value |
|----------|-------|
| Port | 1883 |
| Config | `~/IOTstack/volumes/mosquitto/config/mosquitto.conf` |

Used by:
- Home Assistant (sensor data publishing)
- Zigbee2MQTT (if enabled)
- MQTT bridge to gPlug provisioning server (smart meter data)

**MQTT Bridge Configuration:**

The local Mosquitto broker bridges to the gPlug provisioning server to receive smart meter data:

```
connection gplug-bridge
address provision.dhamstack.com:8883
bridge_capath /etc/ssl/certs
bridge_tls_version tlsv1.2
remote_username bridge_ha
remote_password bridge2025
topic B0-81-84-25-22-5C/SENSOR in 1
topic 94-A9-90-98-01-2C/SENSOR in 1
restart_timeout 10 60
keepalive_interval 30
cleansession true
```

- Direction: inbound only (`in`) - receives smart meter data from remote broker
- Remote broker: `provision.dhamstack.com:8883` (TLS, credentials: `bridge_ha`/`bridge2025`)
- Topics: specific gPlug device MACs only (prevents simulator/other traffic leaking through)
- gPlug topic format: `<MAC-with-dashes>/SENSOR` (e.g., `B0-81-84-25-22-5C/SENSOR`)

### 6.6 Node-RED

| Property | Value |
|----------|-------|
| URL | http://192.168.0.203:1880 |
| Data | `~/IOTstack/volumes/nodered` (221 MB) |

### 6.7 Backup

**Local backup:**
- Script: `~/IOTstack/scripts/backup.sh`
- Note: Stack may go offline during backup

**Remote backup (rclone/Dropbox):**
- Config: `~/.config/iotstack_backup/config.yml`
- Cron: `00 23 * * * iotstack_backup >>./Logs/iotstack_backup.log 2>&1`

### 6.8 Legacy volumes (not currently running)

| Volume | Size | Notes |
|--------|------|-------|
| zigbee2mqtt | 59 MB | Zigbee coordinator (moved to HA) |
| home_assistant | 1.2 GB | Old HA instance (now on VM 100) |
| wireguard | 116 KB | VPN (not active) |
| zerotier-one | 76 KB | VPN (not active) |

## 7. Home Assistant (192.168.0.202)

### 7.1 System overview and versions

**Last inventory:** 2026-04-26

| Component | Version | Status |
|-----------|---------|--------|
| Core | 2026.3.4 | (latest 2026.4.4 — pending) |
| Supervisor | 2026.4.2 | Current |
| OS | 17.2 | Current |
| Machine | qemux86-64 (KVM) | Healthy |
| Architecture | amd64 | - |

- Recorder: purge_keep_days=7, auto_purge=true
- Timezone: Europe/Zurich
- Country: CH

### 7.2 Network and access
- Primary LAN IP: 192.168.0.202/24 (enp0s18, auto), gateway 192.168.0.1, DNS 192.168.0.1.
- HA UI: http://192.168.0.202:8123 (internal).
- External URL: https://hej08kp6767.sn.mynetname.net:8443 (via Nginx Proxy Manager).
- SSH access via Advanced SSH & Web Terminal add-on (v23.0.3).
- Supervisor network: hassio 172.30.32.0/23, docker DNS 172.30.32.3.

### 7.3 File layout and key artifacts
- Root config: `/homeassistant` (symlink `/config`).
- Core files: `configuration.yaml`, `automations.yaml`, `scripts.yaml`, `scenes.yaml`, `customize.yaml`, `templates.yaml`, `sensors.yaml`, `secrets.yaml`.
- Databases/logs: `home-assistant_v2.db`, `zigbee.db`, `home-assistant.log`.
- Add-on data: `backup.db`, `backup_config.yaml`, `.storage/*`.
- ESPHome: `/homeassistant/esphome/*.yaml`.

### 7.4 Add-ons (active)

**Last inventory:** 2026-04-26

| Add-on | Version | State | Repository | Description |
|--------|---------|-------|------------|-------------|
| ESPHome Device Builder | 2026.3.3 | ✅ started | ESPHome (5c53de3b) | Build smart home devices |
| File editor | 6.0.0 | ✅ started | Core | Browser-based file editor |
| Advanced SSH & Web Terminal | 23.0.6 | ✅ started | Community (a0d7b954) | SSH & terminal access |
| Studio Code Server | 6.0.1 | ✅ started | Community (a0d7b954) | VS Code in browser |
| Matter Server | 8.4.0 | ✅ started | Core | Matter protocol support (no devices paired yet) |
| modbus-proxy | 1.0.18 | ✅ started | Modbus Proxy (bc3b947b) | Multi-client Modbus proxy (Huawei inverter) |
| SwissSolarForecast | 1.3.4 | ✅ started | Energy Management (8d023bea) | PV power forecasting |
| LoadForecast | 1.2.6 | ✅ started | Energy Management (8d023bea) | Load consumption forecasting |
| EnergyManager | 1.8.5 | ✅ started | Energy Management (8d023bea) | Energy optimization signals |
| OCPP Server | 0.9.56 | ✅ started | Energy Management (8d023bea) | OCPP 1.6j wallbox server |
| Nginx Proxy Manager | 2.1.0 | ✅ started | Community (a0d7b954) | Reverse proxy management |

**Energy Management add-on documentation:**

| Add-on | Short role in this installation | Detailed FSD |
|--------|---------------------------------|--------------|
| SwissSolarForecast | Produces PV forecast data from MeteoSwiss ICON models and writes it to InfluxDB. | [SwissSolarForecast FSD](../swisssolarforecast/Documents/swisssolarforecast-fsd.md) |
| LoadForecast | Produces statistical household load forecasts from historical InfluxDB data. | [LoadForecast FSD](../loadforecast/Documents/loadforecast-fsd.md) |
| EnergyManager | Consumes PV/load forecasts and live HA state to control battery discharge, EV charging, and appliance signals. | [EnergyManager FSD](../energymanager/Documents/energymanager-fsd.md) |
| OCPP Server | Hosts the OCPP wallbox connection and exposes wallbox state/control entities to HA. | [OCPP Server FSD](../ocpp-server/Documents/ocpp-server-fsd.md) |

**Removed since previous inventory:**
- `Frigate` — uninstalled 2026-04-26 (NVR add-on with no camera connected; no Frigate entities in HA).

**Add-on Repositories:**

| Repository | Slug | Purpose |
|------------|------|---------|
| Official add-ons | core | Core HA add-ons |
| Home Assistant Community Add-ons | a0d7b954 | Community maintained |
| ESPHome | 5c53de3b | ESPHome builder |
| Energy Management Add-ons | 8d023bea | SwissSolarForecast, LoadForecast, EnergyManager, OCPP Server |
| Frigate Add-ons | ccab4aaf | Frigate NVR |
| Modbus Proxy Repository | bc3b947b | Modbus proxy for Huawei |
| Home Assistant Google Drive Backup | cebe7a76 | Google Drive backups |
| Music Assistant | d5369777 | Music assistant |
| Local add-ons | local | Custom local add-ons |

### 7.5 Integrations (configured)

**Last inventory:** 2026-04-26 (post-cleanup audit)

#### Energy & Solar

| Integration | Title | Host/Details | Status |
|-------------|-------|--------------|--------|
| huawei_solar | SUN2000-8KTL-M1 | 192.168.0.202:502 (Modbus via proxy) | ✅ Enabled |

#### Network & Communication

| Integration | Title | Host/Details | Status |
|-------------|-------|--------------|--------|
| mqtt | MQTT Broker | 192.168.0.203:1883 | ✅ Enabled |
| telegram_bot | sensorsIOTHA | Webhooks | ✅ Enabled |

#### Devices - Shelly

| Integration | Title | Host/IP | Model |
|-------------|-------|---------|-------|
| shelly | Shelly3em | 192.168.0.191 | SHEM-3 |
| shelly | Shelly 2PM White | 192.168.0.186 | SNSW-102P16EU |

#### Devices - ESPHome

| Integration | Title | Host/IP | Purpose |
|-------------|-------|---------|---------|
| esphome | Sonoff Lab Light | 192.168.0.185 | Lab lighting control |
| esphome | sonoff-s26-Enphase | 192.168.0.59 | Enphase monitoring |
| esphome | ESPHome AlarmBell | 192.168.0.3 | Alarm bell |
| esphome | tablet_switch | 192.168.0.33 | Tablet charging control |
| esphome | P1S_Mains | 192.168.0.40 | 3D printer power |
| esphome | Bluetooth Proxy f9c438 | 192.168.0.20 | BLE proxy |
| esphome | ESPHome Water Meter | 192.168.0.179 | Water metering |
| esphome | ESPHome Web 4ed8d0 | 192.168.0.34 | Generic ESP device |
| esphome | ESPHome Web 10fcf4 | 192.168.0.16 | Soil moisture sensor |
| esphome | Mailbox-Notifier | 192.168.0.187 | Mailbox notification |
| esphome | iphoneswitch | 192.168.0.42 | iPhone charging control |
| esphome | my-pc-power-remote-control | 192.168.0.36 | PC power control |

#### Devices - Xiaomi BLE

| Integration | Title | MAC Address | Type |
|-------------|-------|-------------|------|
| xiaomi_ble | Temperature/Humidity Sensor CC21 | 58:2D:34:35:CC:21 | LYWSDCGQ |
| xiaomi_ble | Mi Smart Scale (32C7) | C8:47:8C:B8:32:C7 | Scale |
| xiaomi_ble | Plant Sensor 87F7 | C4:7C:8D:62:87:F7 | HHCCJCY01 |
| xiaomi_ble | Temperature/Humidity Sensor 77B5 | A4:C1:38:99:77:B5 | LYWSD03MMC |

#### Devices - Mobile Apps

| Integration | Title | Device | Platform |
|-------------|-------|--------|----------|
| mobile_app | Iphone | sensorsiot | iOS 18.6.2 |
| mobile_app | iPad von AS | iPad7,3 | iPadOS 16.6.1 |
| mobile_app | Kitchen | KFMAWI (Amazon) | Android 28 |
| mobile_app | iPad | iPad13,18 | iPadOS 26.2 |
| mobile_app | HD1900 | OnePlus | Android 31 |

#### Devices - Media & Display

| Integration | Title | Host/IP | Type |
|-------------|-------|---------|------|
| fully_kiosk | Fire Tablet | 192.168.0.197 | Kiosk display (kitchen tablet) |

**Configured but unused** — kept for reference only, no live entities, not used in any automation/dashboard:
`braviatv`, `androidtv_remote`, `dlna_dmr`, `cast` (all KD-49XF9005 / generic Cast). Safe to remove via Settings → Devices & services if desired.

#### Devices - Printers

| Integration | Title | Host/IP | Model |
|-------------|-------|---------|-------|
| ipp | Brother MFC-L3760CDW | 192.168.0.100:631 | IPP printer |
| brother | MFC-L3760CDW | 192.168.0.100 | Printer status |

#### Weather & Calendar

| Integration | Title | Details | Status |
|-------------|-------|---------|--------|
| meteoswiss | Lausen / Rünenberg | Postcode 4415, Station RUE | ✅ Enabled |
| meteoswiss | Samedan / Samedan | Postcode 7503, Station SAM | ✅ Enabled |
| ms365_calendar | HomeAssistant | Microsoft 365 | ✅ Enabled |
| sun | Sun | Built-in | ✅ Enabled |

#### Smart Home Protocols

| Integration | Title | Details | Status |
|-------------|-------|---------|--------|
| zha | Sonoff Zigbee 3.0 USB Dongle Plus | USB serial | ✅ Enabled |
| matter | Matter | via Matter Server add-on | ✅ Enabled |
| thread | Thread | Built-in | ✅ Enabled |
| tasmota | Tasmota | MQTT discovery | ✅ Enabled |
| bluetooth | Shelly 2PM White BLE | 44:17:93:A7:CF:34 | ✅ Enabled |
| bluetooth | Bluetooth Proxy f9c438 | AC:67:B2:F9:C4:38 | ✅ Enabled |
| ibeacon | iBeacon Tracker | BLE beacons | ✅ Enabled |

#### Vehicles

| Integration | Title | Details | Status |
|-------------|-------|---------|--------|
| smarthashtag | Smart HESYA4C44SG200806 | Smart EV (custom integration; runs with local pysmarthashtag patch — see § 7.7.1) | ✅ Enabled |

**Removed since previous inventory:**
- `evcc_intg` — uninstalled 2026-04-26. EV management is fully covered by the OCPP Server add-on + smarthashtag; the evcc bridge added 131 entities with no consumer.
- `tuya` — uninstalled previously (no Tuya devices in use).

#### System & Utilities

| Integration | Title | Purpose | Status |
|-------------|-------|---------|--------|
| hassio | Supervisor | Add-on management | ✅ System |
| backup | Backup | Backup management | ✅ System |
| hacs | HACS | Custom components | ✅ Enabled |
| watchman | Watchman | Config monitoring | ✅ Enabled |
| spook | Spook | HA enhancements | ✅ Enabled |
| threshold | surplus_ok | Power threshold sensor | ✅ Enabled |
| nut | NUT UPS | UPS monitoring (4 entities) | ✅ Enabled |
| telegram_bot | sensorsIOTHA | Notifications | ✅ Enabled |

**Configured but unused** (kept; zero live entities): `go2rtc` (was used by Frigate), `simpleicons`, `radio_browser`. Safe to remove if desired.

### 7.6 Core configuration (configuration.yaml)
- `default_config:` enabled.
- `tts:` Google Translate.
- `logger:` default info; specific overrides for yamaha, my_integration, meteo-swiss, automation.
- `recorder:` 7-day retention.
- Includes: templates, automations, scripts, scenes, sensors.
- `mqtt:` sensors for Enphase (Tasmota), Grid Power (gPlug), and Weather Station (Fineoffset-WHx080 via OpenMQTTGateway RTL_433).
- `mqtt_statestream:` base_topic (legacy evcc removed; wallbox now managed by the OCPP Server add-on).
- `influxdb:` v2 at 192.168.0.203:8087, org `spiessa`, bucket `HomeAssistant`, include/exclude domains.
- `command_line` sensors:
  - Solar Forecast Today/Tomorrow from `/config/scripts/query_solar_forecast.sh`.
  - Appliance Signal (missing script `appliance_signal.py`).

### 7.7 Custom components (HACS)

Custom components installed in `/config/custom_components/` (updated 2026-03-27):

| Component | Purpose | Integration |
|-----------|---------|-------------|
| `hacs` | Home Assistant Community Store | ✅ |
| `huawei_solar` | Huawei SUN2000 inverter | ✅ |
| `meteoswiss` | MeteoSwiss weather | ✅ |
| `ms365_calendar` | Microsoft 365 calendar | ✅ |
| `simpleicons` | Simple Icons library | ✅ |
| `smarthashtag` | Smart car integration | ✅ |
| `spook` | HA enhancements | ✅ |
| `spook_inverse` | Spook inverse helpers | ✅ |
| `watchman` | Config watcher | ✅ |

**Removed since last inventory:** `bambu_lab`, `browser_mod`, `localtuya`, `tuya_ble`.

**Notes:**
- MS365 calendars stored in `/homeassistant/ms365_storage/ms365_calendars_HomeAssistant.yaml`
- Zigbee stack is ZHA (presence of `zigbee.db` and ZHA automations)
- Zigbee2MQTT is not configured inside HA OS (runs on IOTstack at 192.168.0.203)

#### 7.7.1 Local patches to custom components

The `smarthashtag` integration depends on the `pysmarthashtag` Python library, which is bundled inside HA's core container. We maintain a **local patch** to that library to add adaptive rate-limit backoff (the upstream library retried login every ~80 s after a single failure, which trips Smart's WAF and locks the API for hours).

| File | Purpose |
|------|---------|
| `scripts/pysmarthashtag-patch/authentication.py` | Patched library file (AIMD backoff + circuit breaker) |
| `scripts/pysmarthashtag-patch/apply.sh` | Idempotent deploy: SSH + base64 + `docker cp` to HA, with checksum guards |
| `scripts/pysmarthashtag-patch/test_backoff.py` | Offline tests for the state machine |
| `scripts/pysmarthashtag-patch/0001-adaptive-rate-limit-backoff.patch` | Unified diff, for replay |

**When to re-apply:** after any HA core update that bumps `pysmarthashtag` (the deploy script fails loudly with a checksum mismatch — refresh the patch before re-running).

**Upstream PR:** [DasBasti/pySmartHashtag #187](https://github.com/DasBasti/pySmartHashtag/pull/187) — once merged, the local patch can be retired.

### 7.8 Templates and sensors
- `templates.yaml`: inverter power, load totals (direct from Shelly 3EM phases), power meter net energy, wallbox power, lab sub-metering (desk/bench/rest).
- `templates/1_Sensors.yaml`: battery/grid/panel power, PV energy, calendar status sensors, mail sensor.
- `templates/HomeAssistantSolarCalculations.yaml`: duplicate solar/load templates (legacy).
- `sensors.yaml`: `time_date` only. The legacy `platform: template` sensors that lived here (`solar_forecast_today_sum`, `solar_forecast_tomorrow_sum`, `power_phase1`) were briefly migrated to modern `template:` syntax (2026-04-26) ahead of HA 2026.6's removal of the legacy syntax, then deleted entirely (2026-04-27) once it was confirmed they were summing entities from a long-since-removed `forecast_solar` integration. The one remaining consumer — the *Huawei postpone charging* automation — was re-pointed to `sensor.solar_forecast_today` from the SwissSolarForecast add-on, which provides the same kWh-day-total quantity natively.
- `customize.yaml`: state_class fixes for energy dashboard.

**Trigger-based "last-known" caches** (added 2026-04 to mask the smarthashtag integration's "unavailable when car is asleep" gaps and the Mi Smart Scale's wife-readings):

| Sensor | Source | Trigger | Purpose |
|---|---|---|---|
| `sensor.smart_battery_last_known` | `sensor.smart_battery` | not_to ∈ {unavailable, unknown, none} | Latches last numeric SOC |
| `sensor.smart_charging_target_last_known` | `sensor.smart_charging_target` | same | Latches last numeric target |
| `sensor.andreas_weight` | `sensor.mi_smart_scale_32c7_mass` | same + condition `value ≥ 70 kg` | Filters wife's weighings, keeps user's last reading |

Pattern: trigger fires only on state changes that match a (`not_to` + optional `condition`) gate. The sensor latches to the last triggering value; a HA restart restores the value via the recorder. See `templates.yaml` for the full definitions.

**InfluxDB filter:** `sensor.mi_smart_scale_32c7_mass` is in the `influxdb.exclude.entities` list in `configuration.yaml` so wife-readings are not recorded; `sensor.andreas_weight` (already filtered ≥ 70 kg) is recorded normally.

#### 7.8.1 EnergyV1 Template Sensors

The following template sensors provide calculated values for InfluxDB EnergyV1 bucket. Add to `templates.yaml`:

```yaml
# EnergyV1 Calculated Sensors
# These provide derived metrics written to InfluxDB via HA InfluxDB integration

template:
  - sensor:
      # PV String Power (V × A calculations)
      - name: "PV String 1 Power"
        unique_id: pv_string_1_power
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ (states('sensor.inverter_pv_1_voltage') | float(0) *
              states('sensor.inverter_pv_1_current') | float(0)) | round(1) }}

      - name: "PV String 2 Power"
        unique_id: pv_string_2_power
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ (states('sensor.inverter_pv_2_voltage') | float(0) *
              states('sensor.inverter_pv_2_current') | float(0)) | round(1) }}

      # Battery Net Energy (charge - discharge)
      - name: "Battery Net Energy"
        unique_id: battery_net_energy
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total
        state: >
          {{ (states('sensor.battery_total_charge') | float(0) -
              states('sensor.battery_total_discharge') | float(0)) | round(2) }}

      # Grid Total Energy (import + export)
      - name: "Grid Total Energy"
        unique_id: grid_total_energy
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
        state: >
          {{ (states('sensor.power_meter_consumption') | float(0) +
              states('sensor.power_meter_exported') | float(0)) | round(2) }}

      # Solar Total Power (Huawei + Enphase)
      - name: "Solar Total Power"
        unique_id: solar_total_power
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ (states('sensor.inverter_input_power') | float(0) +
              states('sensor.enphase_power') | float(0)) | round(1) }}

      # Grid State (On-grid = 1, Off-grid = 0)
      - name: "Grid State"
        unique_id: grid_state_binary
        state: >
          {{ 1 if states('sensor.inverter_off_grid_status') == 'On-grid' else 0 }}

      # Self Consumption Ratio
      - name: "Self Consumption Ratio"
        unique_id: self_consumption_ratio
        unit_of_measurement: "%"
        state: >
          {% set ac = states('sensor.inverter_active_power') | float(0) | abs %}
          {% set load = states('sensor.load_total_power') | float(0.01) %}
          {{ ((ac / load) * 100) | round(1) if load > 0.01 else 0 }}

      # Autarchy (100 if consuming from grid, 0 if exporting)
      - name: "Autarchy"
        unique_id: autarchy_ratio
        unit_of_measurement: "%"
        state: >
          {{ 0 if states('sensor.power_meter_active_power') | float(0) < -0.1 else 100 }}

      # Surplus Power (Enphase - Load)
      - name: "Surplus Power"
        unique_id: surplus_power
        unit_of_measurement: "W"
        device_class: power
        state_class: measurement
        state: >
          {{ (states('sensor.enphase_power') | float(0) -
              states('sensor.load_total_power') | float(0)) | round(1) }}

      # Total Energy Yield (Huawei + Enphase)
      - name: "Total Energy Yield"
        unique_id: total_energy_yield
        unit_of_measurement: "kWh"
        device_class: energy
        state_class: total_increasing
        state: >
          {{ (states('sensor.inverter_total_yield') | float(0) +
              states('sensor.enphase_energy_total') | float(0)) | round(2) }}
```

#### 7.8.2 HA InfluxDB Integration Configuration

Configure HA InfluxDB integration to write energy sensors to EnergyV1 bucket:

```yaml
# configuration.yaml
influxdb:
  api_version: 2
  host: 192.168.0.203
  port: 8087
  token: !secret influxdb_token
  organization: spiessa
  bucket: EnergyV1
  ssl: false

  # Include only energy-related sensors
  include:
    entities:
      # Battery (direct)
      - sensor.battery_bus_voltage
      - sensor.battery_power
      - sensor.battery_state_of_capacity
      - sensor.battery_total_charge
      - sensor.battery_total_discharge
      - sensor.battery_charge_discharge_power

      # DC/Inverter (direct)
      - sensor.inverter_input_power
      - sensor.inverter_pv_1_voltage
      - sensor.inverter_pv_1_current
      - sensor.inverter_pv_2_voltage
      - sensor.inverter_pv_2_current
      - sensor.inverter_daily_yield
      - sensor.inverter_total_yield
      - sensor.inverter_efficiency
      - sensor.inverter_active_power
      - sensor.inverter_off_grid_status

      # AC Voltage (direct)
      - sensor.inverter_phase_a_voltage
      - sensor.inverter_phase_b_voltage
      - sensor.inverter_phase_c_voltage

      # Grid/Power Meter (direct)
      - sensor.power_meter_active_power
      - sensor.power_meter_phase_a_active_power
      - sensor.power_meter_phase_b_active_power
      - sensor.power_meter_phase_c_active_power
      - sensor.power_meter_consumption
      - sensor.power_meter_exported

      # Load/Shelly (direct)
      - sensor.load_phase_1_power
      - sensor.load_phase_2_power
      - sensor.load_phase_3_power
      - sensor.load_total_power
      - sensor.load_desk_power
      - sensor.load_bench_power

      # Enphase (MQTT sensor - see 7.8.3)
      - sensor.enphase_power
      - sensor.enphase_energy_total

      # Calculated (template sensors)
      - sensor.pv_string_1_power
      - sensor.pv_string_2_power
      - sensor.battery_net_energy
      - sensor.grid_total_energy
      - sensor.solar_total_power
      - sensor.grid_state
      - sensor.self_consumption_ratio
      - sensor.autarchy
      - sensor.surplus_power
      - sensor.total_energy_yield
```

#### 7.8.3 Enphase MQTT Sensors

The Enphase inverter reports via Tasmota to MQTT topic `tele/Enphase/SENSOR`. Add to `configuration.yaml`:

```yaml
# Enphase MQTT Sensors (Tasmota device)
mqtt:
  sensor:
    - name: "Enphase Power"
      unique_id: enphase_power
      state_topic: "tele/Enphase/SENSOR"
      value_template: "{{ value_json.ENERGY.Power | default(0) }}"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement

    - name: "Enphase Energy Total"
      unique_id: enphase_energy_total
      state_topic: "tele/Enphase/SENSOR"
      value_template: "{{ value_json.ENERGY.Total | default(0) }}"
      unit_of_measurement: "kWh"
      device_class: energy
      state_class: total_increasing

    - name: "Enphase Voltage"
      unique_id: enphase_voltage
      state_topic: "tele/Enphase/SENSOR"
      value_template: "{{ value_json.ENERGY.Voltage | default(0) }}"
      unit_of_measurement: "V"
      device_class: voltage
      state_class: measurement

    - name: "Enphase Current"
      unique_id: enphase_current
      state_topic: "tele/Enphase/SENSOR"
      value_template: "{{ value_json.ENERGY.Current | default(0) }}"
      unit_of_measurement: "A"
      device_class: current
      state_class: measurement
```

#### 7.8.4 Weather Station MQTT Sensors

The Fineoffset-WHx080 weather station on the roof transmits 433 MHz data received by an OpenMQTTGateway (v1.6.0) running RTL_433. The gateway publishes JSON to MQTT topic `OpenMQTT/433/RTL_433toMQTT/Fineoffset-WHx080/0/169`.

Previously these sensors were created by Node-RED; since 2026-02-19 they are native HA MQTT sensors in `configuration.yaml`:

```yaml
# Weather Station (Fineoffset-WHx080 via OpenMQTTGateway RTL_433)
mqtt:
  sensor:
    - name: "Roof Temp"
      object_id: roof_temp
      unique_id: weather_roof_temp
      state_topic: "OpenMQTT/433/RTL_433toMQTT/Fineoffset-WHx080/0/169"
      value_template: "{{ value_json.temperature_C }}"
      unit_of_measurement: "°C"
      device_class: temperature
      state_class: measurement

    - name: "Weather Wind"
      object_id: weather_wind
      unique_id: weather_wind
      state_topic: "OpenMQTT/433/RTL_433toMQTT/Fineoffset-WHx080/0/169"
      value_template: "{{ value_json.wind_avg_km_h }}"
      unit_of_measurement: "km/h"
      device_class: wind_speed
      state_class: measurement

    - name: "Weather Gust"
      object_id: weather_gust
      unique_id: weather_gust
      state_topic: "OpenMQTT/433/RTL_433toMQTT/Fineoffset-WHx080/0/169"
      value_template: "{{ value_json.wind_max_km_h | round(2) }}"
      unit_of_measurement: "km/h"
      device_class: wind_speed
      state_class: measurement

    - name: "Weather Rainrate"
      object_id: weather_rainrate
      unique_id: weather_rainrate
      state_topic: "OpenMQTT/433/RTL_433toMQTT/Fineoffset-WHx080/0/169"
      value_template: "{{ value_json.rain_mm }}"
      unit_of_measurement: "mm"
      state_class: measurement
```

| Entity ID | Source Field | Unit |
|-----------|-------------|------|
| `sensor.roof_temp` | `temperature_C` | °C |
| `sensor.weather_wind` | `wind_avg_km_h` | km/h |
| `sensor.weather_gust` | `wind_max_km_h` | km/h |
| `sensor.weather_rainrate` | `rain_mm` | mm |

### 7.9 Automations (high-level)
- Tablet charging: start below 30%, stop above 80%.
- Lab lights: PC/bench lights on motion, off after 5–10 min no motion.
- P1S: switch on via ZHA button, manual off, automatic off after print/beds.
- Energy: Charge to SOC at 05:00; Huawei charging start/postpone.
- Calendar/bell: ring 15 minutes before and at event start.
- Lighting: turn all lights off.
- Telegram: mail arrived, mailbox emptied, sensor timeout.
- Trash: minute-by-minute reminder.
- Logging: log solar sensor states.
- Tag automation: tag scan handler.

### 7.10 Dashboards and UI
- Lovelace dashboards (storage mode): AmazonFire, Map, Portainer, Zigbee2MQTT, Grafana.
- Panel iframes migrated (panel_iframe storage entry present).
- AmazonFire overview chart uses `sensor.load_power` (consolidated 2026-02-19, previously `sensor.load_w`).
- AmazonFire includes weight sensor `sensor.mi_smart_scale_32c7_mass` (Xiaomi BLE scale).

### 7.11 ESPHome integration pointer
- ESPHome is documented as a primary subsystem in §8 because it provides multiple edge devices, sensors, relays, BLE proxying, mailbox logic, and automation inputs.
- HA integration entries are still tracked in §7.5; device firmware/configuration ownership is tracked in §8.

### 7.12 Backups and storage
- Google Drive Backup add-on enabled.
- `backup.db` present; `backup_config.yaml` appears to be a Frigate-style template (verify usage).
- Recorder DB is `home-assistant_v2.db` (~1.2GB on disk at time of review).

### 7.13 Maintenance tasks

#### 7.13.1 Stale entity cleanup

HA's MQTT auto-discovery accepts every device any gateway hears, so the entity registry accumulates ghost entries from neighbours' broadcasts. Two main sources observed:

- **OpenMQTTGateway → rtl_433** picks up neighbours' 433 MHz weather/freezer sensors (Oregon, Acurite, Bresser, Fineoffset, inFactory, …) and auto-publishes HA discovery topics for each.
- **iBeacon** registers every transient BLE iBeacon broadcast (Withings scales, generic Apple-format tags from passing pedestrians, e-bikes, …).

The receiver side cannot reliably filter at source (OMG firmware has no per-device-ID allow-list for RTL_433), so the policy is **periodic registry purge**.

**Tool:** `scripts/ha-purge-stale.py` (in this repo). Runs from the dev workspace via SSH + HA REST. Defaults:

- threshold: state ∈ {unavailable, unknown, none, ""} AND `last_changed` > 30 days
- match patterns: rtl_433 device-family prefixes + iBeacon noise UUIDs
- keep-list: `wgx_ibeacon_93fa`, `wgx_ibeacon_bbf4`, `fineoffset_whx080` (your active devices)

```bash
# dry run (default — list candidates, no changes)
python scripts/ha-purge-stale.py

# apply: backup registry, delete matched entries, clear retained
# MQTT discovery topics so OMG can't immediately re-create them, then
# restart HA core
python scripts/ha-purge-stale.py --apply --clear-mqtt
```

**Cadence:** ad-hoc, when registry feels cluttered. Two known clean-up triggers in 2026-04 (iBeacon: 690 ghosts → 10; rtl_433: pending). Expected ~quarterly.

#### 7.13.2 Other maintenance recipes

- Mass device deletion via browser console script (see legacy `Home Assistant.docx`).
- MQTT discovery examples with `mosquitto_pub` for sensors.
- Outlook integration credentials are stored in Secrets Store.

### 7.14 Troubleshooting notes

**Current issues (2026-01-22):**
- `appliance_signal.py` referenced in configuration.yaml but script is missing

**Current versions (2026-04-26):** see § 7.1 (host) and § 7.4 (add-ons) for the canonical tables.

---

## 8. ESPHome Edge Devices

ESPHome is a primary part of the Home Assistant installation, not only a device protocol. It owns edge firmware for relays, sensors, LoRa mailbox receivers, BLE proxying, water-meter pulse counting, wallbox phase switching, and several experimental boards. The ESPHome Device Builder add-on runs inside HA and stores active YAML configurations in `/homeassistant/esphome/`.

### 8.1 Operating model

| Item | Value |
|------|-------|
| HA add-on | ESPHome Device Builder |
| Active config path | `/homeassistant/esphome/*.yaml` |
| Secret file | `/homeassistant/esphome/secrets.yaml` |
| HA integration | `esphome` config entries discovered by zeroconf/API |
| Primary dependencies | Wi-Fi, HA ESPHome API, MQTT for selected nodes |
| Documentation policy | Document node purpose, entities, and automations; never document secret values |
| Config validation | All active YAML files pass `esphome config` as of 2026-04-27 |

### 8.2 Active YAML inventory

Inventory source: live HA configuration read from `/homeassistant/esphome/` on 2026-04-27 after ESPHome dashboard cleanup. `secrets.yaml` is intentionally excluded.

### 8.2.1 Live HA ESPHome device inventory

Inventory source: HA device/entity registry plus live state API on 2026-04-27 after ESPHome dashboard and HA registry cleanup. Stale HA config entries for `iphoneswitch`, `earu-breaker`, `ESPHome Web 4ed8d0`, `my-pc-power-remote-control`, `ESPHome Web 10fcf4`, `P1S_Mains`, and `tablet_switch` were removed from HA on 2026-04-27.

| Status | Device | Summary | Key live / important entities | Notes |
|--------|--------|---------|-------------------------------|-------|
| Online | AlarmBell | ESP8266 relay used as office/calendar/mailbox bell | `switch.esphome_alarmbell_bell` | Used by calendar reminder and mailbox notification automations |
| Online | ESPHome Water Meter | ESPHome pulse counter on the water meter | `sensor.esphome_water_meter_flow`, `sensor.esphome_water_meter_consumption` | Flow currently reports `0.0`; consumption counter reported `26476.0 L` during inventory |
| Online | light-lab | Dual relay/light controller for lab PC and bench lights | `light.lab_pc`, `light.lab_bench`, `binary_sensor.dual_ls_status_2` | Used by motion-driven lab lighting automations |
| Online | Mailbox-Notifier | LoRa mailbox receiver exposing mailbox full/empty state | `binary_sensor.esphome_web_10fcf4_mailbox_state` | Other helper buttons were unavailable during inventory; mailbox state was live |
| Online | sonoff-s26 | Sonoff smart plug / relay device | `binary_sensor.sonoff_s20_status_2`, `switch.sonoff_s20_relay_2`, `light.sonoff_s20_green_led_2` | Only status entity was live during inventory; relay/light entities were unavailable |
| Online / active scanner | Bluetooth Proxy Bathroom T7 | ESP32 BLE proxy for Xiaomi BLE, iBeacon, and nearby BLE sensors | Bluetooth scanner source `AC:67:B2:F9:C4:3A`; `button.esp32_bluetooth_proxy_f9c438_*` | ESPHome dashboard shows online; HA button/update entities are not a reliable online-status signal for this proxy |

Current orphan check result:

| Check | Result |
|-------|--------|
| ESPHome entities pointing to missing HA device IDs | 0 |
| ESPHome entities pointing to missing config entries | 0 |
| ESPHome entities without device IDs | 0 |
| Stale/unavailable ESPHome devices removed from HA on 2026-04-27 | 7 config entries, 8 devices, 40 entities |
| Remaining ESPHome config entries in HA | 6 |

Remaining HA ESPHome config entries after cleanup: `Bluetooth Proxy f9c438`, `ESPHome AlarmBell`, `ESPHome Water Meter`, `Mailbox-Notifier`, `Sonoff Lab Light`, and `sonoff-s26-Enphase`.

Post-cleanup reference check: `Stop charging Tablet` and `Start Charging Tablet` still reference the removed `Tabletswitch` device ID in `automations.yaml`. If tablet charging is no longer used, disable or delete those automations; if it is restored, re-add the ESPHome device first.

Configured YAML files without a HA ESPHome config entry after cleanup: `earu-breaker.yaml`, `esphome-web-4ed8d0.yaml`, `esphome-web-57be65.yaml` (cat-pump experiment), `esphome-web-b27518.yaml` (QO-100/BME280), `esphome-web-cb281c.yaml` (HopfeNerd mailbox), `esphome-web-d6c833.yaml` (Birch WOL), `liliane-mailbox.yaml`, and `p1s-mains.yaml`. These files are valid YAML but should be treated as not currently integrated into HA unless rediscovered/re-added.

| YAML file | Node / friendly name | Main function | Key HA entities / outputs | Automation or dashboard dependency | Notes |
|-----------|----------------------|---------------|----------------------------|------------------------------------|-------|
| `earu-breaker.yaml` | `earu-breaker` | Wallbox phase switch with BL0942 energy monitor | Re-add to HA to recreate entities | EV/wallbox phase switching if used | YAML remains; HA config entry removed 2026-04-27 |
| `esp32-bluetooth-proxy-f9c438.yaml` | Bluetooth Proxy Bathroom T7 | BLE proxy for nearby Xiaomi/BLE devices | Bluetooth proxy device; HA xiaomi_ble entities depend on BLE coverage | BLE sensor availability | Uses ESPHome bluetooth-proxy package |
| `esphome-water-meter.yaml` | ESPHome Water Meter | Pulse counter for water meter | `sensor.esphome_water_meter_consumption`, `sensor.esphome_water_meter_flow` | `Telegram: Water Counter Timeout`; water monitoring | Exposes `set_pulse_total` API action |
| `esphome-web-4ed8d0.yaml` | TestBoard Web 4ed8d0 | Generic ESP32 test board / soil fallback board | Re-add to HA to recreate entities | None confirmed | YAML remains; HA config entry removed 2026-04-27 |
| `esphome-web-57be65.yaml` | `cat_pump_controller` | ESP32-CAM cat pump experiment | Re-add to HA to create entities | None confirmed | YAML exists but no HA config entry; old AI picture callback removed during 2026.4 validation cleanup |
| `esphome-web-b27518.yaml` | QO-100 | Ethernet ESP32 with BME280 | Re-add to HA to create entities | Environmental monitoring if enabled | YAML exists but no HA config entry |
| `esphome-web-ca1ac8.yaml` | Mailbox-Notifier | LoRa mailbox receiver | `binary_sensor.esphome_web_10fcf4_mailbox_state` and related mailbox entities | Telegram mailbox notifications; mail sensor timeout | Uses custom UART include |
| `esphome-web-cb281c.yaml` | HopfeNerd-Mailbox | LoRa mailbox receiver variant | Re-add to HA to create entities | Not confirmed active | YAML exists but no HA config entry |
| `esphome-web-d6c833.yaml` | Birch-WOL | Wake-on-LAN bridge | Re-add to HA to create entities | Remote PC wake-up if used | YAML exists but no HA config entry |
| `liliane-mailbox.yaml` | Liliane-Mailbox | LoRa mailbox receiver | Re-add to HA to create entities | Not confirmed active | YAML exists but no HA config entry |
| `p1s-mains.yaml` | P1S-Mains | Bambu Lab P1S mains relay | Re-add to HA to recreate entities | P1S automations if restored | YAML remains; HA config entry removed 2026-04-27 |

### 8.3 ESPHome automations and dependencies

| Workflow | ESPHome node | HA entities | Automation dependency |
|----------|--------------|-------------|-----------------------|
| Tablet charge management | `tabletswitch` | tablet relay switch plus Fire Tablet battery sensor | `Start Charging Tablet`, `Stop charging Tablet` |
| Water-meter watchdog | `esphome-water-meter` | `sensor.esphome_water_meter_flow` | `Telegram: Water Counter Timeout` |
| P1S printer power | `p1s-mains` | `switch.p1s_mains_relay` | `P1S Switch ON`, `P1S Manual OFF`, `P1S automatic OFF` |
| Mailbox notification | `Mailbox-Notifier` / LoRa mailbox nodes | `binary_sensor.esphome_web_10fcf4_mailbox_state`, `sensor.mail_sensor_last_update` | `Telegram: Mail arrived`, `Telegram: Mailbox emptied`, `Telegram: Mail Sensor Timeout` |
| Calendar bell | ESPHome AlarmBell integration entry | `switch.esphome_alarmbell_bell` | `Ring bell 15 minutes before`, `Ring bell at event start`, mailbox arrival chime |
| Lab lights | Sonoff Lab Light ESPHome integration entry | `light.lab_pc`, `light.lab_bench` | PC/bench light motion automations |
| BLE sensor coverage | `esp32-bluetooth-proxy-f9c438` | Xiaomi BLE sensors and iBeacon/BLE discovery | Indirect dependency for temperature, plant, scale, and BLE presence sensors |
| Wallbox phase switching | `earu-breaker` | wallbox phase switch and BL0942 sensors | EV/OCPP phase-switching logic |

### 8.4 Secret handling and remediation

ESPHome credentials must be treated as live infrastructure secrets. The documentation may name the secret store and secret keys, but must not include values.

Observed issue from the live YAML audit: some ESPHome YAML files contained plaintext Wi-Fi fallback passwords, OTA passwords, MQTT credentials, web-server credentials, and API keys. These were migrated to `!secret` references on 2026-04-27, but the previously exposed values should still be considered compromised until rotated.

Validation cleanup on 2026-04-27 updated active YAML files for ESPHome 2026.4 compatibility: removed legacy `esphome.platform` syntax, changed `bme280` to `bme280_i2c`, replaced removed `text_sensor.custom` mailbox UART handling with native UART debug callbacks plus a template text sensor, updated OTA syntax, removed obsolete API password auth, and removed the old ESP32-CAM AI picture callback blocks.

Remediation plan:

1. Rotate previously exposed external API keys and passwords.
2. Recompile and deploy each affected node in small batches, starting with non-critical experimental nodes.
3. After each deployment, verify HA entity availability and dependent automations.
4. Keep only secret names and remediation status in this document.

Suggested documentation status values:

| Status | Meaning |
|--------|---------|
| `clean` | No plaintext credentials in node YAML |
| `uses secrets` | Credentials referenced via `!secret` |
| `needs rotation` | Plaintext API key/password was observed and should be rotated |
| `legacy/test` | Node appears experimental or historical; verify before maintaining |

---

## 10. Node-RED

### 10.1 Overview
Node-RED provides flow-based data processing and automation, running as a Docker container on IOTstack (192.168.0.203:1880).

### 10.2 Energy Data Processing

**Status:** Most energy flows migrated to Home Assistant (see Section 7.8 and 11.8).

| Flow | Status | Replacement |
|------|--------|-------------|
| Huawei Mapping | ❌ Disabled | HA Huawei Solar integration + InfluxDB |
| Shelly 3em Mapping | ❌ Disabled | HA Shelly integration |
| Enphase preparation | ❌ Disabled | HA MQTT sensors (Section 7.8.3) |
| MBUS | ❌ Removed | Replaced by HA MQTT sensor `sensor.grid_power` + InfluxDB task |

### 10.3 MBUS Smart Meter Flow (Removed - Replaced by HA + InfluxDB Task)

**Previous:** Node-RED processed MBUS data from MQTT topics `MBUS/values/#`, `MBUS/SENSOR/#` and wrote to InfluxDB.

**Current:** Smart meter data flows through the MQTT bridge and Home Assistant:

```
gPlug device → WireGuard VPN → Remote MQTT (provision.dhamstack.com:8883)
  → Mosquitto bridge → Local MQTT (B0-81-84-25-22-5C/SENSOR)
    → HA MQTT sensor (sensor.grid_power)
      → HA InfluxDB integration → HomeAssistant bucket
        → InfluxDB task (RegularDataLoadFromHomeAssistantNew, every 1m)
          → HomeData bucket (measurement: MBUS, field: M_Grid)
```

**HA MQTT Sensor** (in `configuration.yaml`):
```yaml
mqtt:
  sensor:
    - name: "Grid Power"
      unique_id: gplug_grid_power
      state_topic: "B0-81-84-25-22-5C/SENSOR"
      value_template: "{{ ((value_json.Po | float(0)) - (value_json.Pi | float(0))) * 1000 | round(0) }}"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement
```

**Sign convention:** `M_Grid = (Po - Pi) * 1000` — negative = import, positive = export (same as Huawei `power_meter_active_power`)

**InfluxDB task mapping:** `grid_power` → measurement `MBUS`, field `M_Grid`

**Fields in HomeData/MBUS** (from gPlug JSON payload via InfluxDB task):
| Field | Description | Unit |
|-------|-------------|------|
| M_Grid | Net grid power (Po - Pi) | W |

### 10.4 Legacy Flows (Reference Only)

<details>
<summary>Huawei Mapping (disabled - replaced by HA)</summary>

```javascript
// Battery readings
msg.payload.BATT_V = parseFloat(global.get('homeassistant.homeAssistant.states')["sensor.battery_bus_voltage"].state);
msg.payload.BATT_W = parseFloat(global.get('homeassistant.homeAssistant.states')["sensor.battery_charge_discharge_power"].state);

// Inverter DC side
msg.payload.DC_W = parseFloat(global.get('homeassistant.homeAssistant.states')["sensor.inverter_input_power"].state);

// Inverter AC side
msg.payload.AC_W = parseFloat(global.get('homeassistant.homeAssistant.states')["sensor.inverter_active_power"].state);

// Grid / Smart meter
msg.payload.Grid_W = parseFloat(global.get('homeassistant.homeAssistant.states')["sensor.power_meter_active_power"].state);
```
</details>

<details>
<summary>Enphase Integration (disabled - replaced by HA MQTT sensors)</summary>

```javascript
msg.payload.Enphase_Power = global.get("Enphase_Power");
msg.payload.DC_W_tot = msg.payload.DC_W + global.get("Enphase_Power");
```
</details>

### 10.6 Key Add-ons
- `node-red-contrib-home-assistant-websocket`
- `node-red-contrib-influxdb`
- `node-red-contrib-telegrambot`
- `node-red-contrib-modbus`
- `node-red-contrib-bigtimer`

---

## 11. InfluxDB

### 11.1 Overview

**Last inventory:** 2026-03-27

| Property | Value |
|----------|-------|
| Version | InfluxDB 2.5.0 |
| Container | influxdb2 |
| Host | 192.168.0.203 |
| Port | 8087 (external) → 8086 (internal) |
| Organization | spiessa |
| Data Size | ~6.0 GB |

**Note:** Only InfluxDB v2 is running. Grafana datasources using port 8086 internally access v2's 1.x compatibility API.

### 11.2 Buckets

| Bucket | Purpose | Retention | Category |
|--------|---------|-----------|----------|
| `HomeAssistant` | HA entity states via influxdb integration | 1 year | Core |
| `HuaweiNew` | Raw inverter data from Node-RED | Infinite | Energy |
| `EnergyV1` | Consolidated energy data | Infinite | Energy |
| `MBUS` | Smart meter readings (gPlug/Tasmota) | Infinite | Energy |
| `Enphase` | Enphase microinverter data | Infinite | Energy |
| `pv_forecast` | PV power forecasts (SwissSolarForecast) | 30 days | Forecast |
| `load_forecast` | Load consumption forecasts (LoadForecast) | 30 days | Forecast |
| `energy_manager` | EnergyManager decisions and SOC forecast | Infinite | Forecast |
| `solarForecast` | Legacy solar forecast data | Infinite | Forecast |
| `Water` | Water meter data | Infinite | Utility |
| `Weight` | Weight scale data | Infinite | Health |
| `HugoNew` | Hugo-related data | Infinite | Other |
| `TCP_Monitoring` | Network monitoring | Infinite | System |
| `weather/autogen` | Weather data | Infinite | Weather |
| `YOUTUBE/autogen` | YouTube metrics | Infinite | Other |
| `_monitoring` | InfluxDB internal metrics | 7 days | System |
| `_tasks` | InfluxDB task results | 3 days | System |

### 11.3 Energy Bucket Schemas

#### 11.3.1 HuaweiNew (measurement: Energy)

Primary energy data from Node-RED, written every ~10 seconds.

| Field | Unit | Description |
|-------|------|-------------|
| **Solar DC** | | |
| `DC_W` | W | Total DC input power (Huawei) |
| `DC1_V`, `DC1_A`, `DC1_W` | V/A/W | String 1 voltage/current/power |
| `DC2_V`, `DC2_A`, `DC2_W` | V/A/W | String 2 voltage/current/power |
| `DC_W_tot` | W | Total DC power (Huawei + Enphase) |
| `DC_kWh` | kWh | Lifetime DC energy |
| **Solar AC** | | |
| `AC_W` | W | Inverter AC output |
| `AC_kWh` | kWh | Lifetime AC yield |
| `AC1_V`, `AC1_W` | V/W | Phase 1 voltage/power |
| `AC2_V`, `AC2_W` | V/W | Phase 2 voltage/power |
| `AC3_V`, `AC3_W` | V/W | Phase 3 voltage/power |
| `Inv_Efficiency` | % | Inverter efficiency |
| **Battery** | | |
| `BATT_W` | W | Battery power (+charge/-discharge) |
| `BATT_V` | V | Battery bus voltage |
| `BATT_Level` | % | State of charge |
| `Batt_charge_kWh` | kWh | Lifetime charge energy |
| `Batt_discharge_kWh` | kWh | Lifetime discharge energy |
| `Batt_tot_kWh` | kWh | Net battery throughput |
| **Grid** | | |
| `Grid_W` | W | Grid power (+export/-import) |
| `Grid1_W`, `Grid2_W`, `Grid3_W` | W | Per-phase grid power |
| `Grid_import_kWh` | kWh | Lifetime grid import |
| `Grid_export_kWh` | kWh | Lifetime grid export |
| `Grid_tot_kWh` | kWh | Net grid (export - import) |
| `Grid_State` | - | Grid connection state |
| **Load** | | |
| `Load_W` | W | Calculated house load |
| `Load_W_tot` | W | Total load (incl. Enphase) |
| `Load_kWh` | kWh | Cumulative load energy |
| **Enphase** | | |
| `Enphase_Power` | W | Enphase microinverter power |
| `Enphase_Energy` | kWh | Enphase cumulative energy |
| `Enphase1_power`, `Enphase1_energy` | W/kWh | Individual Enphase data |
| **Calculated** | | |
| `Self` | % | Self-consumption ratio |
| `Autarchy` | % | Energy autarky |
| `surplus` | W | Current surplus |
| `Energy_tot` | kWh | Total energy balance |

#### 11.3.2 pv_forecast

PV power forecasts from the SwissSolarForecast add-on. Home Assistant consumes the resulting forecast entities and InfluxDB stores short-retention forecast output in the `pv_forecast` bucket.

Detailed add-on behavior, measurements, and data contracts: [SwissSolarForecast FSD](../swisssolarforecast/Documents/swisssolarforecast-fsd.md).

#### 11.3.3 load_forecast

Load consumption forecasts from the LoadForecast add-on. Home Assistant and Grafana use this bucket to compare expected household demand against PV forecast and battery state.

Detailed add-on behavior, measurements, and data contracts: [LoadForecast FSD](../loadforecast/Documents/loadforecast-fsd.md).

#### 11.3.4 energy_manager

EnergyManager combines live Home Assistant state with PV/load forecasts to produce battery discharge decisions, appliance signals, SOC forecast data, and wallbox charging limits.

Detailed add-on behavior, measurements, and data contracts: [EnergyManager FSD](../energymanager/Documents/energymanager-fsd.md).

#### 11.3.5 MBUS

Smart meter data from gPlug via HA MQTT sensor and InfluxDB task.

**Measurement:** `MBUS`

**Data path:** gPlug → MQTT bridge → HA sensor (`sensor.grid_power`) → HomeAssistant bucket → InfluxDB task → HomeData bucket

| Field | Unit | Description |
|-------|------|-------------|
| `M_Grid` | W | Net grid power (Po - Pi) × 1000, negative = import, positive = export |

### 11.4 Data Migration Tasks

Copy data between buckets:
```flux
option task = {name: "Migration", every: 1h}

from(bucket: "HuaweiV2")
    |> range(start: -1h)
    |> filter(fn: (r) => r["_measurement"] == "Energy")
    |> to(bucket: "EnergyV1")
```

Modify values during migration:
```flux
from(bucket: "Huawei/autogen")
    |> range(start: 2022-07-02T06:40:00Z)
    |> filter(fn: (r) => r["_measurement"] == "Energy")
    |> filter(fn: (r) => r["_field"] == "Net3")
    |> map(fn: (r) => ({r with _value: r._value * 1000.0}))
    |> to(bucket: "bTarget")
```

### 11.5 Common Queries

Daily aggregation:
```flux
from(bucket: "Huawei/autogen")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r["_measurement"] == "Energy")
  |> filter(fn: (r) => r["_field"] == "Self")
  |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
  |> yield(name: "mean")
```

Smart meter check (quarterly readings):
```flux
from(bucket: "MBUS")
  |> range(start: 2025-01-01T00:00:00Z, stop: 2025-03-31T23:59:00Z)
  |> filter(fn: (r) => r["_measurement"] == "8a")
  |> filter(fn: (r) => r["_field"] == "Ei1" or r["_field"] == "Ei2" or r["_field"] == "Eo1" or r["_field"] == "Eo2")
  |> last()
```

### 11.6 CLI Operations

Delete data range:
```bash
influx delete --bucket "Huawei/autogen" --org "spiessa" \
  --predicate '_measurement="Energy"' \
  --start "2022-11-24T14:15:00Z" --stop "2022-11-24T17:11:00Z" \
  --token "YOUR_TOKEN"
```

Export to CSV:
```bash
docker exec influxdb influx -database 'weather' \
  -execute 'SELECT * FROM "stations"' -format 'csv' \
  > /var/lib/influxdb/backup/weather.csv
```

### 11.7 Backup/Restore

```bash
# Backup
docker exec influxdb influxd backup -portable /var/lib/influxdb/backup

# Restore
docker exec -it influxdb influxd restore -portable backup
```

### 11.8 Data Consolidation Analysis

**Last analysis:** 2026-01-23

EnergyV1 is now the single consolidated bucket for all energy data. All data flows through Home Assistant via the HA InfluxDB integration, including smart meter data (previously handled by Node-RED).

#### 11.8.1 EnergyV1 Field Schema

**Direct HA Sensors (no calculation):**

| Category | HA Sensor | InfluxDB Field |
|----------|-----------|----------------|
| **Battery** |||
| | sensor.battery_bus_voltage | battery_bus_voltage |
| | sensor.battery_power | battery_power |
| | sensor.battery_state_of_capacity | battery_state_of_capacity |
| | sensor.battery_total_charge | battery_total_charge |
| | sensor.battery_total_discharge | battery_total_discharge |
| | sensor.battery_charge_discharge_power | battery_charge_discharge_power |
| **DC/Inverter** |||
| | sensor.inverter_input_power | inverter_input_power |
| | sensor.inverter_pv_1_voltage | inverter_pv_1_voltage |
| | sensor.inverter_pv_1_current | inverter_pv_1_current |
| | sensor.inverter_pv_2_voltage | inverter_pv_2_voltage |
| | sensor.inverter_pv_2_current | inverter_pv_2_current |
| | sensor.inverter_daily_yield | inverter_daily_yield |
| | sensor.inverter_total_yield | inverter_total_yield |
| | sensor.inverter_efficiency | inverter_efficiency |
| | sensor.inverter_active_power | inverter_active_power |
| | sensor.inverter_off_grid_status | inverter_off_grid_status |
| **AC Voltage** |||
| | sensor.inverter_phase_a_voltage | inverter_phase_a_voltage |
| | sensor.inverter_phase_b_voltage | inverter_phase_b_voltage |
| | sensor.inverter_phase_c_voltage | inverter_phase_c_voltage |
| **Grid/Power Meter** |||
| | sensor.power_meter_active_power | power_meter_active_power |
| | sensor.power_meter_phase_a_active_power | power_meter_phase_a_active_power |
| | sensor.power_meter_phase_b_active_power | power_meter_phase_b_active_power |
| | sensor.power_meter_phase_c_active_power | power_meter_phase_c_active_power |
| | sensor.power_meter_consumption | power_meter_consumption |
| | sensor.power_meter_exported | power_meter_exported |
| **Load (Shelly 3EM)** |||
| | sensor.load_phase_1_power | load_phase_1_power |
| | sensor.load_phase_2_power | load_phase_2_power |
| | sensor.load_phase_3_power | load_phase_3_power |
| | sensor.load_total_power | load_total_power |
| | sensor.load_desk_power | load_desk_power |
| | sensor.load_bench_power | load_bench_power |
| **Enphase** |||
| | sensor.enphase_power | enphase_power |
| | sensor.enphase_energy_total | enphase_energy_total |

**HA Template Sensors (calculated):**

| HA Template Sensor | Formula | InfluxDB Field |
|--------------------|---------|----------------|
| sensor.pv_string_1_power | pv_1_voltage × pv_1_current | pv_string_1_power |
| sensor.pv_string_2_power | pv_2_voltage × pv_2_current | pv_string_2_power |
| sensor.battery_net_energy | charge - discharge | battery_net_energy |
| sensor.grid_total_energy | consumption + exported | grid_total_energy |
| sensor.solar_total_power | inverter + enphase | solar_total_power |
| sensor.grid_state | On-grid → 1, Off-grid → 0 | grid_state |
| sensor.self_consumption_ratio | (AC / Load) × 100 | self_consumption_ratio |
| sensor.autarchy | Grid < -0.1 → 0, else 100 | autarchy |
| sensor.surplus_power | enphase - load | surplus_power |
| sensor.total_energy_yield | inverter_yield + enphase_total | total_energy_yield |

**Note:** All energy data now flows through HA. Enphase handled by HA MQTT sensors (Section 7.8.3), Shelly by HA Shelly integration, MBUS smart meter by HA MQTT sensor `sensor.grid_power` (Section 10.3).

#### 11.8.2 Data Flow Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Home Assistant                                │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │ Huawei Solar │  │ Shelly       │  │ MQTT         │  │ MQTT    │ │
│  │ Integration  │  │ Integration  │  │ (Enphase)    │  │ (gPlug) │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────┬────┘ │
│         │                 │                 │                │      │
│         │    ┌────────────┴─────────────────┘                │      │
│         │    │                                               │      │
│         v    v                                               v      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │            Template Sensors (calculated)                     │   │
│  │  pv_string_1/2_power, battery_net_energy, grid_power, etc. │   │
│  └──────────────────────────┬──────────────────────────────────┘   │
│                             │                                       │
│                             v                                       │
│  ┌─────────────────────────────────────────────────┐               │
│  │         HA InfluxDB Integration                  │               │
│  │    (28 direct + 11 template = 39 sensors)        │               │
│  └──────────────────────────┬──────────────────────┘               │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
                              v
               ┌──────────────────────────┐
               │  InfluxDB HomeAssistant  │
               │      (raw HA data)       │
               └────────────┬─────────────┘
                            │
                            v
               ┌──────────────────────────┐
               │  InfluxDB Task (1m)      │
               │  RegularDataLoad...      │
               └────────────┬─────────────┘
                            │
                            v
               ┌──────────────────────────┐
               │    InfluxDB HomeData     │
               │  (consolidated bucket)    │
               └──────────────────────────┘

gPlug data path:
  gPlug ESP32 → WireGuard VPN → provision.dhamstack.com:8883
    → Mosquitto bridge (inbound) → local MQTT (B0-81-84-25-22-5C/SENSOR)
      → HA sensor.grid_power → HomeAssistant bucket → HomeData/MBUS/M_Grid
```

#### 11.8.3 Migration Status

| Source | Status | Action |
|--------|--------|--------|
| Huawei (Node-RED) | ❌ Disable | Replace with HA Huawei Solar integration + InfluxDB |
| Shelly 3EM (Node-RED) | ❌ Disable | Replace with HA Shelly integration (already active) |
| Shelly 3EM (HA) | ✅ Active | load_* fields already in EnergyV1 |
| Enphase (Node-RED) | ❌ Disable | Replace with HA MQTT sensors (Section 7.8.3) |
| MBUS (Node-RED) | ❌ Removed | Replaced by HA MQTT sensor `sensor.grid_power` + InfluxDB task |

#### 11.8.4 Implementation Checklist

**Step 1: Add HA Configuration**
- [ ] Add template sensors to `templates.yaml` (Section 7.8.1)
- [ ] Add Enphase MQTT sensors to `configuration.yaml` (Section 7.8.3)
- [ ] Add InfluxDB integration to `configuration.yaml` (Section 7.8.2)
- [ ] Restart Home Assistant

**Step 2: Verify HA Data Flow**
- [ ] Confirm all 28 direct sensors appear in HA
- [ ] Confirm 10 template sensors calculate correctly
- [ ] Confirm Enphase MQTT sensors receive data
- [ ] Confirm data appears in EnergyV1 bucket

**Step 3: Disable Node-RED Flows**
- [ ] Disable "Huawei Mapping" function node
- [ ] Disable "Shelly 3em Mapping" function node
- [ ] Disable "Enphase preparation" function node
- [x] MBUS flow removed (replaced by HA MQTT sensor + InfluxDB task)

**Step 4: Cleanup**
- [ ] Remove unused InfluxDB output nodes pointing to old buckets
- [ ] Update Grafana dashboards if field names changed

**Result:** All Node-RED energy flows removed. All energy data now flows through Home Assistant.

---

## 12. Grafana

### 12.1 Overview

**Last inventory:** 2026-03-27

| Property | Value |
|----------|-------|
| Container | grafana |
| Version | 11.1.4 |
| Host | 192.168.0.203 |
| Port | 3000 |
| URL | http://192.168.0.203:3000 |
| Auth | admin/admin |

### 12.2 Data Sources

**Last inventory:** 2026-03-27

#### InfluxDB v2 (Flux) - Recommended

| Name | UID | Default Bucket | Purpose |
|------|-----|----------------|---------|
| InfluxDB-V2 | byzPPbd4z | Huawei/autogen | **Default** - Main energy data |
| Huawei_Andreas_V2 | fowyxAd4k | EnergyV1 | Consolidated energy |
| HomeAssistant-V2 | ef9hp9j6nv30gd | HomeAssistant | HA entity states |
| LEG-InfluxDB | af9l1jbyffri8c | energy | Remote LEG data |

#### InfluxDB v1 (InfluxQL) - Via Compatibility API

| Name | UID | Database | Purpose |
|------|-----|----------|---------|
| InfluxDB | xgBMzgkRk | Huawei | Legacy main energy |
| Huawei | WenKkgkRz | Huawei | Huawei inverter data |
| Energy | lM9LzRzRk | Energy | Energy metrics |
| Homeassistant | cQ7OBZzgz | homeassistant | HA entity states |
| MBUS | - | MBUS | Smart meter data |
| Shelly3em | wgoVxDmgk | Shelly3em | Shelly 3EM energy monitor |
| Shellies | IEDZmRzgk | Shellies | All Shelly devices |
| Enphase | - | Enphase | Enphase microinverters |
| Weather | Nd3_kRzRz | weather | Weather data |
| Weight | -IHskRzgk | weight | Scale data |
| Water | - | Water | Water meter |
| Mailbox | VJPIiRzRk | mailbox | Mailbox sensor |
| Light | kkiiigzgk | Light | Light data |
| TempTest | z7hjmRkgz | TempTest | Temperature testing |
| YouTube | -aLHigzgz | YOUTUBE | YouTube metrics |
| Gateways | 3jJ7mgzgz | Gateways | Gateway monitoring |
| Lacuna | D1Eezgkgk | Lacuna | Lacuna data |
| Hugo | LKjstZigz | Hugo | Hugo data |
| Curtain | IIs-kgkgz | CURTAIN | Curtain control |

### 12.3 Dashboards

**Last inventory:** 2026-03-27

All dashboards are in the General folder (no subfolders).

#### Energy Dashboards (Local)

| Dashboard | UID | Tags | Starred | Purpose |
|-----------|-----|------|---------|---------|
| **Huawei** | LbosmjiR | - | ⭐ | Real-time solar, battery, grid monitoring |
| **Huawei Longterm** | LbosmjiRo | - | - | Monthly/daily aggregates, self-consumption |
| **BatteryForecast** | df9feonzp10xsc | energy, forecast, load, pv | - | Battery/PV/load forecasts with SOC simulation |
| **ForecastAccuracy** | afasawqszcz5sd | accuracy, forecast, pv | - | Forecast vs actual comparison |

**Removed dashboards:** Forecast (cf9druxb33yf4e), Forecast comparison (ce5ncpdixp24gb) — no longer present in Grafana.

#### Utility Dashboards

| Dashboard | UID | Starred | Purpose |
|-----------|-----|---------|---------|
| **Water** | ce5xr0eidnbpce | ⭐ | Water consumption and flow |
| **Weather** | BZuC4SZRk | ⭐ | Temperature, rain, humidity, wind |

#### LEG (Local Energy Group) Dashboards

| Dashboard | UID | Tags | Purpose |
|-----------|-----|------|---------|
| LEG Community | leg-community | LEG, community, energy | Community power/value flow |
| LEG Grid | leg-grid | LEG, energy, grid | Grid connection metrics |
| LEG House 1-5 | leg-house-1..5 | LEG, energy, house | Per-house energy monitoring |

### 12.4 Dashboard Panels

#### Huawei (Real-time Monitoring)

| Panel | Type | Description |
|-------|------|-------------|
| Overview | stat | Key metrics (production, consumption, SOC) |
| Grid | timeseries | Grid import/export power |
| Total Power | timeseries | Solar production vs load |
| Battery | gauge/timeseries | SOC level and charge/discharge |
| East/West String Power | timeseries | Per-string solar output |
| East/West Current | timeseries | String currents |
| Voltages East/West String | timeseries | String voltages |
| Mains Voltages | timeseries | AC grid voltages |
| Consumption | timeseries | House load |
| Load | timeseries | Detailed load breakdown |

#### Huawei Longterm (Historical Analysis)

| Panel | Description |
|-------|-------------|
| Monthly Self | Self-consumption percentage by month |
| Daily Self | Daily self-consumption trend |
| Daily Autarchy | Energy independence ratio |
| Daily Grid | Grid import/export daily |
| Monthly Overview | Monthly energy totals |
| Daily Overview | Daily energy breakdown |
| Forecast & Production | Forecast accuracy comparison |

#### LoadForecast (Energy Forecasting)

| Panel | Description |
|-------|-------------|
| PV Forecast (Wh per 15min) | P10/P50/P90 solar forecast |
| Load Forecast (Wh per 15min) | P10/P50/P90 consumption forecast |
| Cumulative Energy Balance (Wh) | Running total of net energy |
| Battery SOC Forecast | Simulated SOC trajectory |

#### Forecast (Solar Overview)

| Panel | Description |
|-------|-------------|
| PV Power Forecast (P10/P50/P90) | Probabilistic power bands |
| Net Power (PV - Load) | Surplus/deficit forecast |
| Cumulative Energy Balance (Wh) | Energy flow integral |
| Weather - GHI | Global horizontal irradiance |
| Weather - Temperature | Air temperature |
| Load Forecast (W) | Expected consumption |
| PV Forecast Values | Current forecast numbers |
| Today's Energy Forecast | Total expected kWh |
| Peak Power Today | Maximum expected power |
| Current Temperature | Current reading |
| Battery SOC Forecast | SOC simulation |

#### Weather

| Panel | Description |
|-------|-------------|
| Outside Temperature | Ambient temperature |
| Inside Temperature | Indoor temperature |
| Rain | Precipitation |
| Humidity | Relative humidity |
| Wind | Wind speed |
| RP4 Balloon Receiver Temperature | Equipment monitoring |
| Gateway Temperature | Equipment monitoring |
| QO-100 Temperature | Satellite equipment temp |

#### Water

| Panel | Description |
|-------|-------------|
| Consumption | Water usage over time |
| Flow | Current flow rate |

#### LEG House (Per-House)

| Panel | Description |
|-------|-------------|
| Power Flow (kW) | Real-time power flow |
| Value Flow (ct/h) | Cost/revenue per hour |
| House Tariff (ct/kWh) | Current tariff rate |
| Simulator Appliances (kW) | Simulated appliance load |

#### LEG Community

| Panel | Description |
|-------|-------------|
| Community Power Flow (kW) | Aggregate community power |
| Community Value Flow (ct/h) | Community cost/revenue |
| Community Tariff (ct/kWh) | Community tariff rate |

### 12.5 Dashboard URLs

Quick access links:

| Dashboard | URL |
|-----------|-----|
| Huawei | http://192.168.0.203:3000/d/LbosmjiR/huawei |
| Huawei Longterm | http://192.168.0.203:3000/d/LbosmjiRo/huawei-longterm |
| BatteryForecast | http://192.168.0.203:3000/d/df9feonzp10xsc/batteryforecast |
| ForecastAccuracy | http://192.168.0.203:3000/d/afasawqszcz5sd/forecastaccuracy |
| Water | http://192.168.0.203:3000/d/ce5xr0eidnbpce/water |
| Weather | http://192.168.0.203:3000/d/BZuC4SZRk/weather |
| LEG Community | http://192.168.0.203:3000/d/leg-community/leg-community |
| LEG Grid | http://192.168.0.203:3000/d/leg-grid/leg-grid |
| LEG House 1-5 | http://192.168.0.203:3000/d/leg-house-1/leg-house-1 (through leg-house-5) |

### 12.6 API Access

```bash
# List all dashboards
curl -s "http://192.168.0.203:3000/api/search?type=dash-db" -u admin:admin

# Get specific dashboard
curl -s "http://192.168.0.203:3000/api/dashboards/uid/DASHBOARD_UID" -u admin:admin

# Update dashboard
curl -s -X POST "http://192.168.0.203:3000/api/dashboards/db" \
  -H "Content-Type: application/json" \
  -u admin:admin \
  -d @dashboard.json

# List datasources
curl -s "http://192.168.0.203:3000/api/datasources" -u admin:admin

# Query InfluxDB via Grafana proxy
curl -s -X POST "http://192.168.0.203:3000/api/ds/query" -u admin:admin \
  -H "Content-Type: application/json" -d '{
  "queries": [{
    "refId": "A",
    "datasource": {"uid": "byzPPbd4z"},
    "query": "from(bucket: \"HuaweiNew\") |> range(start: -1h) |> limit(n: 5)"
  }],
  "from": "now-1h",
  "to": "now"
}'
```

### 12.7 Maintenance

**Reset admin password:**
```bash
# Via docker exec
ssh pi@192.168.0.203 "docker exec grafana grafana-cli admin reset-admin-password <new-password>"
```

**Backup dashboards:**
```bash
# Export all dashboards
for uid in $(curl -s "http://192.168.0.203:3000/api/search?type=dash-db" -u admin:admin | jq -r '.[].uid'); do
  curl -s "http://192.168.0.203:3000/api/dashboards/uid/$uid" -u admin:admin > "dashboard_$uid.json"
done
```

**Container logs:**
```bash
ssh pi@192.168.0.203 "docker logs grafana --tail 50"
```

---

## 13. PV Plant - Energy System

### 13.1 System Components

| Component | Model/Type | Function |
|-----------|------------|----------|
| Solar Inverter | Huawei SUN2000 (10kW) | DC-to-AC conversion, battery management |
| Battery Storage | Huawei LUNA2000 | Energy storage (97.8% efficiency) |
| Smart Meter | Landis+Gyr (via gPlug/Tasmota) | Grid import/export metering |
| EV Charger | Suntree SWJ7/Elecq (OCPP 1.6J) | Electric vehicle charging |
| Secondary PV | Enphase (POWR316D) | Additional solar generation |

### 13.2 SUN2000 Solar Inverter

**Integration:** Home Assistant custom component `huawei_solar` (HACS)

**Key Sensors:**

| Sensor | Entity ID | Unit | Description |
|--------|-----------|------|-------------|
| DC Input Power | `sensor.inverter_input_power` | W | Total PV array output |
| AC Output Power | `sensor.inverter_active_power` | W | Inverter output to house/grid |
| PV1 Voltage | `sensor.inverter_pv_1_voltage` | V | String 1 voltage |
| PV1 Current | `sensor.inverter_pv_1_current` | A | String 1 current |
| PV2 Voltage | `sensor.inverter_pv_2_voltage` | V | String 2 voltage |
| PV2 Current | `sensor.inverter_pv_2_current` | A | String 2 current |
| Daily Yield | `sensor.inverter_daily_yield` | kWh | Today's generation |
| Total Yield | `sensor.inverter_total_yield` | kWh | Lifetime generation |
| Inverter Efficiency | `sensor.inverter_efficiency` | % | Current efficiency |
| Off-Grid Status | `sensor.inverter_off_grid_status` | - | "On-grid" or "Off-grid" |

**Efficiency Loss Calculation (templates.yaml):**
```yaml
template:
  - sensor:
    - name: "input_power_with_efficiency_loss"
      unique_id: "input_power_with_efficiency_loss"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement
      state: >-
        {% set inverter_rating = 10000 %}
        {% set inpower = states('sensor.inverter_input_power')|float(0) %}
        {% if inpower < (inverter_rating*0.1) %}
          {{ inpower * 0.90 }}
        {% elif inpower < (inverter_rating*0.2) %}
          {{ inpower * 0.95 }}
        {% else %}
          {{ inpower * 0.98 }}
        {% endif %}
```

### 13.3 Battery Storage

**Specs:** Huawei LUNA2000, LiFePO4, 97.8% round-trip efficiency

**Key Sensors:**

| Sensor | Entity ID | Unit | Description |
|--------|-----------|------|-------------|
| Charge/Discharge Power | `sensor.battery_charge_discharge_power` | W | Positive=charging |
| State of Capacity | `sensor.battery_state_of_capacity` | % | Current charge level |
| Bus Voltage | `sensor.battery_bus_voltage` | V | Battery DC bus |
| Total Charge | `sensor.battery_total_charge` | kWh | Lifetime charged |
| Total Discharge | `sensor.battery_total_discharge` | kWh | Lifetime discharged |
| Day Charge | `sensor.battery_day_charge` | kWh | Today's charge |
| Day Discharge | `sensor.battery_day_discharge` | kWh | Today's discharge |

### 13.4 Smart Meter (MBUS / gPlug)

**Hardware:** Landis+Gyr E450 with gPlug ESP32-C3 interface
**Protocol:** DLMS/COSEM over MBUS
**Firmware:** gPlug firmware (v2.5.0)

**gPlug Device:**
- WiFi MAC: `B0:81:84:25:22:5C`
- Registered MAC: `F0:9E:9E:AD:AC:98`
- Local IP: `192.168.0.52`
- WireGuard VPN IP: `10.0.0.2`
- MQTT topic: `B0-81-84-25-22-5C/SENSOR`
- Smart meter ID: `60222760`
- Provisioning server: `provision.dhamstack.com` (port 5000 HTTP, port 8883 MQTT TLS)

**Second gPlug (registered):**
- MAC: `94:A9:90:98:01:2C`
- WireGuard VPN IP: `10.0.0.3`
- MQTT topic: `94-A9-90-98-01-2C/SENSOR`

**Data flow:** gPlug → WireGuard VPN → remote MQTT broker → Mosquitto bridge → local MQTT → HA `sensor.grid_power` → InfluxDB

**HA Entity:**
| Sensor | Entity ID | Description |
|--------|-----------|-------------|
| Grid Power (gPlug) | `sensor.grid_power` | Net grid power from MBUS smart meter (Po - Pi) × 1000, negative = import |

**OBIS Codes Monitored:**

| Parameter | OBIS Code | Unit | Description |
|-----------|-----------|------|-------------|
| Power In | 1.7.0 | kW | Real power import |
| Power Out | 2.7.0 | kW | Real power export |
| Energy In Total | 1.8.0 | kWh | Total import |
| Energy Out Total | 2.8.0 | kWh | Total export |
| Energy In Tariff 1 | 1.8.1 | kWh | Day tariff import |
| Energy In Tariff 2 | 1.8.2 | kWh | Night tariff import |
| Phase 1 Power | 21.7.0 | W | L1 power in |
| Phase 2 Power | 41.7.0 | W | L2 power in |
| Phase 3 Power | 61.7.0 | W | L3 power in |
| Phase 1 Voltage | 32.7.0 | V | L1 voltage |
| Phase 2 Voltage | 52.7.0 | V | L2 voltage |
| Phase 3 Voltage | 72.7.0 | V | L3 voltage |
| Phase 1 Current | 31.7.0 | A | L1 current |
| Phase 2 Current | 51.7.0 | A | L2 current |
| Phase 3 Current | 71.7.0 | A | L3 current |

**Tasmota Operations:**
```bash
# Backup
python -m esptool --chip esp32c3 --port COM79 --baud 921600 \
  read_flash 0x00000 0x400000 Tasmota_MBUS.bin

# Restore
python -m esptool --chip esp32c3 --port COM79 --baud 921600 \
  write_flash 0x00000 Tasmota_MBUS.bin
```

**Huawei Built-in Power Meter Sensors:**

| Sensor | Entity ID | Description |
|--------|-----------|-------------|
| Active Power | `sensor.power_meter_active_power` | Net grid power (positive=export) |
| Consumption | `sensor.power_meter_consumption` | Total grid import |
| Exported | `sensor.power_meter_exported` | Total grid export |
| Phase A Power | `sensor.power_meter_phase_a_active_power` | L1 net power |
| Phase B Power | `sensor.power_meter_phase_b_active_power` | L2 net power |
| Phase C Power | `sensor.power_meter_phase_c_active_power` | L3 net power |

### 13.5 Wallbox and EV Charging (OCPP via HA Add-on)

The AcTec / Suntree SWJ7 wallbox is managed by the OCPP Server HA add-on. The Home Assistant dependency is the wallbox status, connection, power/energy, phase, and `number.wallbox_power_limit` entity set; EnergyManager writes the target charging limit and OCPP Server handles the OCPP 1.6J wallbox protocol.

| Item | Value |
|------|-------|
| Charge point ID | `AcTec001` |
| Wallbox IP | `192.168.0.81` |
| Control path | EnergyManager -> `number.wallbox_power_limit` -> OCPP Server -> wallbox |
| Detailed add-on spec | [OCPP Server FSD](../ocpp-server/Documents/ocpp-server-fsd.md) |

evcc LXC (CT 117) has been decommissioned; EV charging control is via the OCPP Server add-on and Smart car integration.

### 13.6 Calculated Power Flow Sensors

**Solar to Battery:**
```yaml
- name: Solar to Battery
  device_class: power
  unit_of_measurement: W
  state: >
    {% set inv = states('sensor.inverter_input_power')|int(0) %}
    {% set bat = states('sensor.battery_charge_discharge_power')|int(0) %}
    {% set out = states('sensor.inverter_active_power')|int(0) %}
    {% if bat > 0 %}
      {% if out < 0 %} {{ inv }}
      {% else %} {{ bat }}
      {% endif %}
    {% else %} 0
    {% endif %}
```

**Solar to Grid:**
```yaml
- name: Solar to Grid
  state: >
    {% set grid = states('sensor.power_meter_active_power')|int(0) %}
    {% set out = states('sensor.inverter_active_power')|int(0) %}
    {% if out > 0 and grid > 0 %} {{ grid }}
    {% else %} 0
    {% endif %}
```

**Solar to House:**
```yaml
- name: Solar to House
  state: >
    {% set grid = states('sensor.power_meter_active_power')|int(0) %}
    {% set out = states('sensor.inverter_active_power')|int(0) %}
    {% set bat = states('sensor.battery_charge_discharge_power')|int(0) %}
    {% if out > 0 %}
      {% if grid > 0 %}
        {% if bat > 0 %} {{ out - grid }}
        {% else %} {{ out + bat - grid | abs }}
        {% endif %}
      {% else %}
        {% if bat > 0 %} {{ out }}
        {% else %} {{ out + bat | abs }}
        {% endif %}
      {% endif %}
    {% else %} 0
    {% endif %}
```

**Grid to House:**
```yaml
- name: Grid to House
  state: >
    {% set inv = states('sensor.inverter_active_power')|int(0) %}
    {% set grid = states('sensor.power_meter_active_power')|int(0) %}
    {% if grid < 0 %}
      {% if inv < 0 %} {{ inv - grid | int }}
      {% else %} {{ 0 - grid | int }}
      {% endif %}
    {% else %} 0
    {% endif %}
```

**Battery to House:**
```yaml
- name: Battery to House
  state: >
    {% set bat = states('sensor.battery_charge_discharge_power')|int(0) %}
    {% set togrid = states('sensor.battery_to_grid')|int(0) %}
    {% if bat < 0 %} {{ togrid - bat | int }}
    {% else %} 0
    {% endif %}
```

### 13.7 True PV Energy Calculation

FusionSolar yield calculation (accounting for battery):
```yaml
- name: "energy_pv_daily"
  unique_id: "PV Energy daily"
  state: >
    {{ (states('sensor.inverter_daily_yield') | float)
       - (states('sensor.battery_day_discharge') | float)
       + (states('sensor.battery_day_charge') | float) }}
  device_class: energy
  state_class: total_increasing
  unit_of_measurement: 'kWh'
```

### 13.8 Visualization - Tesla Style Card

**HACS Component:** `tesla-style-solar-power-card`

**Required sensors:**
- `sensor.solar_to_grid`
- `sensor.solar_to_house`
- `sensor.solar_to_battery`
- `sensor.grid_to_house`
- `sensor.grid_to_battery`
- `sensor.battery_to_house`
- `sensor.battery_to_grid`

### 13.9 Visualization - Apex Charts

**Power Overview:**
```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Power
  show_states: true
  colorize_states: true
all_series_config:
  type: area
  opacity: 0.1
  stroke_width: 1
  group_by:
    func: last
    duration: 5ms
series:
  - entity: sensor.inverter_input_power
    color: lightgreen
  - entity: sensor.inverter_active_power
    color: blue
  - entity: sensor.battery_charge_discharge_power
    name: Battery Charge
    transform: return Math.max(0,x);
    color: orange
  - entity: sensor.battery_charge_discharge_power
    name: Battery Discharge
    color: '#800080'
    transform: return -Math.min(0,x);
  - entity: sensor.power_meter_active_power
    name: Grid Export
    transform: return Math.max(0,x);
    color: lime
  - entity: sensor.power_meter_active_power
    name: Grid Import
    color: red
    transform: return -Math.min(0,x);
```

**24-Hour Battery Status:**
```yaml
type: custom:apexcharts-card
header:
  title: Battery Status
series:
  - entity: sensor.battery_state_of_capacity
    type: line
    yaxis_id: pct
    stroke_width: 3
  - entity: sensor.battery_charge_discharge_power
    name: Battery Charge
    transform: return Math.max(0,x);
    color: darkgreen
    yaxis_id: watts
  - entity: sensor.battery_charge_discharge_power
    name: Battery Discharge
    transform: return -Math.min(0,x);
    color: '#800080'
    yaxis_id: watts
yaxis:
  - id: pct
    opposite: true
    max: 100
    min: 15
  - id: watts
```

### 13.10 Energy Automations

**Charge to SOC at scheduled time:**
```yaml
automation:
  - alias: "Charge to SOC at 05:00"
    trigger:
      - platform: time
        at: "05:00:00"
    action:
      - service: huawei_solar.set_tou_period
        data:
          # Configure time-of-use charging parameters
```

**Solar Forecast Sensors:**
```yaml
command_line:
  - sensor:
      name: "Solar Forecast Today"
      command: "/config/scripts/query_solar_forecast.sh today"
      unit_of_measurement: "kWh"
  - sensor:
      name: "Solar Forecast Tomorrow"
      command: "/config/scripts/query_solar_forecast.sh tomorrow"
      unit_of_measurement: "kWh"
```

---

## 14. Microsoft 365 Calendar Integration

### 14.1 Overview
The Home Assistant instance uses the **MS365-Calendar** HACS integration (by RogerSelwyn) to surface Microsoft 365 / Outlook calendars as HA calendar entities. This enables automations based on calendar events (e.g. presence, heating schedules).

| Item | Value |
|------|-------|
| HA integration ID | `ms365_calendar` |
| HACS component path | `custom_components/ms365_calendar/` |
| Token file | `/config/ms365_storage/.MS365-token-cache/ms365_calendar_HomeAssistant.token` |
| Config entry ID | `01JEKXE1JTDJ7K0APGGB91GY3B` |
| Entity name prefix | `HomeAssistant` |

### 14.2 Azure App Registration (prerequisite)
The integration authenticates via OAuth 2.0 against Microsoft Entra ID (formerly Azure AD). A registered application is required.

1. Sign in to the **Azure Portal** → **Microsoft Entra ID** → **App registrations** → **New registration**.
2. Set:
   - **Name:** `HomeAssistant` (or any descriptive name).
   - **Supported account types:** *Accounts in this organizational directory only* (single tenant) — or *Personal Microsoft accounts* if using a personal Outlook.com account.
   - **Redirect URI:** Select **Web**, enter the HA external URL callback:
     ```
     https://hej08kp6767.sn.mynetname.net:8443/api/ms365
     ```
     (This must match the external URL configured in HA. If the external URL is unreachable during auth, see §14.4 for the manual token exchange workaround.)
3. After creation, note the **Application (client) ID** and **Directory (tenant) ID** from the Overview page.
4. Go to **Certificates & secrets** → **New client secret** → set an expiry (e.g. 24 months) → copy the **Value** immediately (it is shown only once).
5. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → add:
   - `Calendars.ReadWrite`
   - `offline_access`
   - `User.Read`
6. Click **Grant admin consent** (if you are the tenant admin), or have an admin approve.

Current registration values:

| Item | Value |
|------|-------|
| Application (client) ID | `51e5d6a3-f143-44dd-9924-7d1bc2d2b1fd` |
| Tenant | `common` (multi-tenant) |
| Redirect URI | `https://hej08kp6767.sn.mynetname.net:8443/api/ms365` |

Store the **client ID**, **tenant ID**, and **client secret** in the Secrets Store (`D:\Dropbox\Documentation\AAHome\Secrets Store.md`).

### 14.3 HA Integration Setup

1. Ensure HACS is installed and working (see §7.7).
2. In HACS → **Integrations** → search for **MS365 Calendar** → **Install**.
3. Restart Home Assistant.
4. Go to **Settings** → **Devices & Services** → **Add Integration** → search **MS365 Calendar**.
5. Enter:
   - **Entity Name** — `HomeAssistant` (used as a prefix for entity IDs and storage files).
   - **Client ID** — from the Azure app registration.
   - **Client Secret** — from Certificates & secrets.
   - **Alt Auth Method** — enable this (required when HA's external URL handles the OAuth redirect).
   - **Enable Update** — enable to get update notifications for the integration.
   - **Basic Calendar** — disable (allows full calendar features).
6. HA will display an **authorization URL**. Open it in a browser, sign in with the Microsoft account that owns the calendars, and **Accept** the permissions.
7. After signing in, Microsoft redirects to the HA external URL with an authorization code. If the redirect succeeds, the token is stored automatically.
8. The integration stores the resulting OAuth tokens in `/config/ms365_storage/.MS365-token-cache/ms365_calendar_HomeAssistant.token`.

### 14.4 Re-authorization (token expired or secret rotated)

OAuth tokens are refreshed automatically via the refresh token. Re-authorization is needed when:
- The **client secret** has expired (check expiry in Azure Portal → App registrations → Certificates & secrets).
- The **token file** is deleted or corrupted (`/config/ms365_storage/.MS365-token-cache/ms365_calendar_HomeAssistant.token`).
- API permissions were changed.

**Symptom:** All `calendar.homeassistant_*` entities show **unavailable**, and HA logs contain:
```
WARNING [custom_components.ms365_calendar.classes.permissions] Could not locate token at
/config/ms365_storage/.MS365-token-cache/ms365_calendar_HomeAssistant.token
```

**Method A — Via HA UI (when external URL is reachable):**

1. If the client secret expired, create a new one in the Azure Portal (§14.2 step 4) and update the Secrets Store.
2. In HA → **Settings** → **Devices & Services** → find the **MS365 Calendar** entry → **Reconfigure** (or delete and re-add the integration).
3. Enter the new client secret when prompted.
4. Complete the OAuth consent flow again (sign in + Accept).
5. Verify calendars reappear as entities under `calendar.*`.

**Method B — Manual token exchange (when external URL is unreachable, e.g. ERR_CONNECTION_REFUSED):**

When the HA external URL (`hej08kp6767.sn.mynetname.net:8443`) is not reachable from the browser, the OAuth redirect fails. The authorization code is still present in the browser URL bar and can be exchanged manually:

1. Start a new config flow via the HA API (or use Claude Code to do it):
   ```bash
   source ~/.secrets/env
   # Start the flow
   curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
     "$HA_URL/api/config/config_entries/flow" \
     -d '{"handler":"ms365_calendar"}'
   # Submit credentials (use the flow_id from the response)
   curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
     "$HA_URL/api/config/config_entries/flow/<flow_id>" \
     -d '{"entity_name":"HomeAssistant","client_id":"<CLIENT_ID>","client_secret":"<CLIENT_SECRET>","alt_auth_method":true,"enable_update":true,"basic_calendar":false,"groups":false,"shared_mailbox":""}'
   ```
2. The response contains `description_placeholders.auth_url` — open that URL in a browser and sign in.
3. After sign-in, the browser redirects to the external URL which fails. **Copy the full URL from the browser address bar** — it contains the `code=` parameter.
4. Exchange the authorization code for a token manually:
   ```bash
   curl -s -X POST "https://login.microsoftonline.com/common/oauth2/v2.0/token" \
     -d "client_id=<CLIENT_ID>" \
     -d "client_secret=<CLIENT_SECRET>" \
     -d "grant_type=authorization_code" \
     -d "redirect_uri=https://hej08kp6767.sn.mynetname.net:8443/api/ms365" \
     -d "scope=offline_access User.Read Calendars.ReadWrite" \
     -d "code=<CODE_FROM_URL>" > token.json
   ```
5. Upload the token to Home Assistant:
   ```bash
   ssh root@192.168.0.202 "mkdir -p /config/ms365_storage/.MS365-token-cache"
   cat token.json | ssh root@192.168.0.202 \
     "cat > /config/ms365_storage/.MS365-token-cache/ms365_calendar_HomeAssistant.token"
   ```
6. Abort the config flow (since the existing config entry is still valid):
   ```bash
   curl -s -X DELETE -H "Authorization: Bearer $HA_TOKEN" \
     "$HA_URL/api/config/config_entries/flow/<flow_id>"
   ```
7. Reload the integration:
   ```bash
   curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" \
     "$HA_URL/api/config/config_entries/entry/<entry_id>/reload"
   ```
   Current entry ID: `01JEKXE1JTDJ7K0APGGB91GY3B`

8. Verify calendars come back online.

### 14.5 Calendar Configuration

Calendars are managed via the integration's options flow: **Settings** → **Devices & Services** → **MS365 Calendar** → **Configure**. Each calendar can be individually enabled/disabled with configurable time offsets.

Current calendars (as of 2026-03-31):

| Calendar | Entity ID |
|----------|-----------|
| Kalender | `calendar.homeassistant_kalender` |
| Feiertage in Schweiz | `calendar.homeassistant_feiertage_in_schweiz` |
| Garbage | `calendar.homeassistant_garbage` |
| Geburtstage | `calendar.homeassistant_geburtstage` |
| Polar Trainingsergebnisse | `calendar.homeassistant_polar_trainingsergebnisse` |
| Polar Trainingsziele | `calendar.homeassistant_polar_trainingsziele` |

Calendars can also be added/removed via the HA API options flow:
```bash
source ~/.secrets/env
# Start options flow
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
  "$HA_URL/api/config/config_entries/options/flow" \
  -d '{"handler":"01JEKXE1JTDJ7K0APGGB91GY3B"}'
# Select calendars (submit with the flow_id from response)
curl -s -X POST -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" \
  "$HA_URL/api/config/config_entries/options/flow/<flow_id>" \
  -d '{"calendar_list":["Kalender","Feiertage in Schweiz","Geburtstage","Polar Trainingsergebnisse","Garbage","Polar Trainingsziele"],"track_new_calendar":true}'
# Then confirm defaults for each calendar when prompted
```

### 14.6 Migration Notes

The current `ms365_calendar` integration (by RogerSelwyn) replaced an older integration called `o365`. If orphaned entities from the old integration appear (entity IDs ending in `_account1`, platform `o365`, `config_entry_id: null`), they can be removed by:

1. Stop HA: `ssh root@192.168.0.202 "ha core stop"`
2. Edit `/config/.storage/core.entity_registry` — remove entries with `"platform": "o365"`.
3. Start HA: `ssh root@192.168.0.202 "ha core start"`

HA must be stopped before editing the entity registry, otherwise it overwrites the file on shutdown.

### 14.7 Troubleshooting

| Symptom | Check |
|---------|-------|
| "Could not locate token" / entities unavailable | Token file missing — re-authorize (§14.4) |
| "Insufficient privileges" | Verify API permissions + admin consent in Azure Portal |
| No calendars after setup | Check options flow — calendars may not be selected |
| Integration not found in HACS | Ensure HACS is up to date; search for "MS365" (not "Office 365") |
| OAuth redirect fails (ERR_CONNECTION_REFUSED) | External URL unreachable — use manual token exchange (§14.4 Method B) |
| Orphaned `_account1` entities showing unavailable | Old `o365` integration remnants — see §14.6 |

---

## 15. Devices and Protocols

### 15.1 Zigbee (ZHA)

Home Assistant uses the **ZHA** (Zigbee Home Automation) integration with a USB coordinator passed through from Proxmox to the HA VM.

**Active Zigbee devices:**

| Device | Entity prefix | Type | Status |
|--------|--------------|------|--------|
| LUMI motion sensor (PIR_PC) | `binary_sensor.lumi_lumi_sensor_motion_aq2` | Aqara motion + illuminance | Working (battery 73%) |
| LUMI motion sensor (PIR_Bench) | `sensor.pir_bench_illuminance` | Illuminance | Working |
| LUMI motion sensor (PIR_Entry) | `sensor.pir_entry_illuminance` | Illuminance | Working |
| LUMI door sensor | `binary_sensor.lumi_lumi_sensor_magnet_aq2` | Aqara door/window | Unavailable |
| LUMI button | `sensor.lumi_lumi_sensor_switch_battery` | Aqara wireless button | Working (battery 75%) |
| Xiaomi Switch1 | `sensor.xiaomi_switch1_battery` | Wireless switch | Working (battery 68%) |
| Espressif ZigbeeTempSensor | `sensor.espressif_zigbeetempsensor_*` | Custom temp/humidity | Unavailable |
| Sonoff SNZB-02 (sonoff_3) | `sensor.sonoff_3_*` | Temp/humidity | Working (19.5°C, 34%) |
| Sonoff SNZB-02 (sonoff_4) | `sensor.sonoff_4_*` | Temp/humidity | Unavailable |
| Plant Sensor 87F7 | `sensor.plant_sensor_87f7_*` | Xiaomi BLE plant (via ZHA or BLE proxy) | Working |

**Notes:**
- The old `Zigbee2MQTT` setup (referenced in legacy docs) has been replaced by ZHA directly in HA.
- Legacy Zigbee doc (`D:\Dropbox\Documentation\Zigbee\Zigbee.docx`) describes the old Zigbee2MQTT device list — no longer applicable.

### 15.2 Tasmota / MQTT Devices

Devices flashed with Tasmota firmware, communicating via MQTT to the Mosquitto broker on IOTstack.

| Device | Function | Key entities | Status |
|--------|----------|-------------|--------|
| **Enphase** | Micro-inverter energy monitor (Sonoff POW with HLW8012) | `sensor.enphase_energy_*` (power, voltage, current, today/total) | Working (213W currently) |
| **sonoff-s26** | Smart plug | `light.sonoff_s20_green_led_2`, `switch.*` | Unavailable |

**MQTT topic conventions:**
- Tasmota devices publish to `tele/<device>/SENSOR`, `stat/<device>/RESULT`
- Tasmota discovery: `tasmota/discovery/#`

**Legacy note:** The `D:\Dropbox\Documentation\Tasmota.docx` contains generic flashing instructions and the XY-WFUSB template. The `Lab Sonoffs Setup.docx` covers Espurna/Tasmota firmware for Sonoff POW (HLW8012 pin mapping). These are historical reference only.

### 15.3 Shelly Devices

Native Shelly integration in HA (auto-discovered).

| Device | Function | Key entities | Status |
|--------|----------|-------------|--------|
| **Shelly 3EM** (shelly3em) | 3-phase energy monitoring | `switch.3em`, `binary_sensor.3em_overpowering` | Working |
| **Shelly 2PM White** | Dual relay for lab lights | `light.lab_bench` (Bench), `light.lab_pc` (Desk) | Working |

### 15.4 RTL_433 Weather Gateway

An ESP32 device (at **192.168.0.15**) running an RTL_433-to-MQTT gateway, receiving 433 MHz weather sensor signals and publishing via MQTT.

| Item | Value |
|------|-------|
| IP Address | 192.168.0.15 |
| Entity prefix | `433_*` or `sensor.433_*` |
| Display | SSD1306 OLED (brightness controllable via `number.433_ssd1306_brightness`) |
| MQTT connectivity | `binary_sensor.433_sys_connectivity` |

**Legacy note:** The `D:\Dropbox\Documentation\RTL_433&Node-Red&InfluxDB&Grafana.docx` describes the old Raspberry Pi-based RTL_433 stack. This has been replaced by the ESP32 gateway publishing directly to MQTT.

### 15.5 Bluetooth / BLE Devices

| Device | Integration | Key entities | Status |
|--------|------------|-------------|--------|
| **ESP32 Bluetooth Proxy** (bathroom) | `esphome` | Proxies BLE advertisements to HA | Working |
| **Xiaomi Mi Smart Scale** | `xiaomi_ble` | `sensor.mi_smart_scale_32c7_mass` | Working |
| **Plant Sensor 87F7** | `xiaomi_ble` | temp, moisture, conductivity, illuminance, battery (43%) | Working |
| **iBeacon trackers** | `ibeacon` | `device_tracker.wgx_ibeacon_*` | Unavailable |

## 16. Notifications, Presence, and UI

### 16.1 Telegram Bot

The `telegram_bot` integration sends notifications for sensor events.

**Active automations:**

| Automation | Trigger | Message |
|-----------|---------|---------|
| Telegram: Mail arrived | `binary_sensor.esphome_web_10fcf4_mailbox_state` → on | Mail notification |
| Telegram: Mailbox emptied | Mailbox → off | Mailbox emptied |
| Mailbox Timeout | Mailbox sensor unavailable for 40s | Warning |
| Telegram: Water Counter Timeout | `sensor.esphome_water_meter_flow` → unavailable for 40s | Warning |

**Configuration:** Bot token stored in Secrets Store. Notify service: `notify.sensorsiotha_andreas_spiess_876235944`.

### 16.2 Presence Detection

| Entity | Method | Status |
|--------|--------|--------|
| `person.andreas_spiess` | Mobile app (iPhone, iPad, HD1900, Kitchen) | home |
| `person.brigitte_sutter` | Mobile app | unknown |
| `device_tracker.smart` | Smart #1 car (smarthashtag integration) | home |

### 16.3 Fire Tablet Dashboard (Fully Kiosk)

An **Amazon Fire Tablet** runs as a wall-mounted HA dashboard using the **Fully Kiosk Browser** integration.

| Item | Value |
|------|-------|
| Integration | `fully_kiosk` |
| Dashboard URL | `http://192.168.0.202:8123/lovelace-amazonfire/` |
| Features | Camera, screenshot, kiosk lock, motion detection, screen brightness |
| Charging automations | `automation.start_charging`, `automation.stop_charging` (currently off) |

**Dashboard views:** AmazonFire (main), Map, Portainer, Zigbee2MQTT, Grafana.

### 16.4 Waste Collection Indicators

Calendar-driven waste collection reminders displayed on the Fire Tablet dashboard.

**Flow:** MS365 Garbage calendar event starts (18:00 day before) → `calendar.homeassistant_garbage` turns on → automation checks event name → sets `input_boolean.indicator_*` → conditional card appears on dashboard → user taps to dismiss.

| Indicator | Entity | Collection type |
|-----------|--------|----------------|
| Green | `input_boolean.indicator_green` | Bio-/Grünsammlung (green waste) |
| Cardboard | `input_boolean.indicator_cardboard` | Kartonsammlung |
| Paper | `input_boolean.indicator_paper` | Papiersammlung |
| Cat | `input_boolean.indicator_cat` | (custom reminder) |

**Automation:** `automation.minute_by_minute_trash_collection_reminderx` — triggers on `calendar.homeassistant_garbage` state change to `on`.

**Calendar events:** Created for all 2026 collection dates from Entsorgungskalender Lausen 2026 (source PDF: `C:\Users\AndreasSpiess\Downloads\Entsorgungskalender-2026 (1).pdf`). Events are at 18:00 the evening before collection, 1 hour duration.

## 17. External Integrations

### 17.1 Smart #1 Car (smarthashtag)

HACS integration `smarthashtag` for the Smart #1 EV.

| Entity type | Examples |
|-------------|---------|
| Climate | `climate.smart_hesya4c44sg200806_conditioning` (A/C + seat heating) |
| Binary sensors | Central locking, trunk lock/open status |
| Device tracker | `device_tracker.smart` |
| Sensors | Various vehicle status |

### 17.2 MeteoSwiss Weather

HACS integration `meteoswiss` providing Swiss weather forecasts.

| Entity | Location |
|--------|----------|
| `weather.8_edletenstrasse_lausen` | Home (Lausen) |
| `weather.5_bugl_da_la_nina_samedan` | Samedan (vacation) |

### 17.3 Frigate NVR

Frigate add-on runs as a container for camera-based object detection.

| Item | Value |
|------|-------|
| Add-on | `ccab4aaf_frigate` v0.17.1 |
| Update entity | `update.frigate_update` |
| Cameras | 1 active (`sensor.cameras`: 1) |

### 17.4 Remote Access

| Component | Details |
|-----------|---------|
| External URL | `https://hej08kp6767.sn.mynetname.net:8443` |
| Nginx Proxy Manager | HA add-on on port 81, handles SSL termination and reverse proxy |
| DynDNS | mynetname.net (Swisscom router DynDNS) |
| AdGuard | LXC container 118 at 192.168.0.101 — DNS ad-blocking |

### 17.5 Other Proxmox Services

These VMs/LXCs run on the Proxmox host but are not directly part of the HA automation stack:

| VMID | Name | Purpose | Status |
|------|------|---------|--------|
| 102 | Birch | General purpose | Running |
| 103 | AREDN-local | AREDN mesh node (local) | Running |
| 104 | AREDN-Tunnel | AREDN mesh node (tunnel) | Running |
| 105 | AREDN-Supernode | AREDN mesh supernode | Running |
| 107 | FreePBX | PBX telephone system | Running |
| 119 | dev-1 | Development VM (5 GB RAM, 100 GB disk) | Running |
| 118 | adguard (LXC) | AdGuard DNS | Running |

### 17.6 USB/IP for Proxmox Development

Raspberry Pi devices serve as remote USB hosts for flashing microcontrollers from Proxmox VMs via USB/IP.

**Architecture:** Pi runs USB/IP server → binds USB device → Proxmox VM attaches via network.

**Reference docs:**
- `D:\Dropbox\Documentation\USB via Ethernet for Proxmox.md`
- `D:\Dropbox\Documentation\Instructions for Raspberry Pi USBIP Integration with Proxmox.md`

Key setup: systemd services on Pi (usbipd daemon, device bind) and VM (auto-attach, watchdog). Standard kernel required (not cloud kernel) for USB/IP kernel modules.

---

## 18. Troubleshooting and known issues

### General
- ~~evcc add-on~~ Decommissioned, replaced by the OCPP Server HA add-on. `evcc_intg` custom component removed (2026-02-19).
- ~~Node-RED companion~~ (`nodered` custom component) removed 2026-02-19. Weather sensors migrated to native MQTT sensors. Node-RED container still runs on IOTstack but is no longer used by HA.
- `appliance_signal.py` referenced but missing
- Custom components removed (2026-03-27): `bambu_lab`, `browser_mod`, `localtuya`, `tuya_ble` — no longer installed.
- HA resolution: no current backup — run `ha backups new --name "full"` to resolve.

### Energy System
- Wallbox OCPP now managed by OCPP Server HA add-on; see [OCPP Server FSD](../ocpp-server/Documents/ocpp-server-fsd.md).
- Wallbox OCPP issues: check the OCPP Server add-on logs and Home Assistant wallbox entities.
- Phase switching: check the OCPP Server add-on logs and related wallbox phase entities.
- Smart meter not reporting: check Tasmota console, verify MBUS wiring

### InfluxDB
- Query errors: verify bucket names (`Huawei/autogen` vs `HuaweiV2`)
- Data gaps: check HA influxdb integration (Node-RED no longer writes to InfluxDB)

---

## 19. Appendix: references and links

### Sources reviewed
- D:\Dropbox\Documentation\Raspberry Pi\InfluxDB Tricks.docx
- D:\Dropbox\!Infrastructure\Proxmox Docu.docx
- D:\Dropbox\Dokumentation Haus\Home Assistant\Home Assistant.docx
- D:\Dropbox\Documentation\Webland.docx
- D:\Dropbox\Wallboxen\Wallbox.docx
- D:\Dropbox\SmartMeter\MBUS.docx
- D:\Dropbox\Dokumentation Haus\Solaranlage\Huawei\Solaranlage Huawei SUN2000 HA Integration.docx

### External References
- Huawei Solar Wiki: https://github.com/wlcrs/huawei_solar/wiki
- OCPP Server add-on FSD: [ocpp-server-fsd.md](../ocpp-server/Documents/ocpp-server-fsd.md)
- gPlug: https://gplug.ch/
- Tasmota Smart Meter: https://tasmota.github.io/docs/Smart-Meter-Interface/
- Tesla Style Card: HACS frontend

### MQTT Topics
```
ocpp/AcTec001/#          # Legacy/diagnostic wallbox topic namespace
cmnd/LabLight/POWER2
zigbee2mqtt/#
```

### EnergyV1 Entity Mapping (HA → InfluxDB)

The following table shows the mapping between Home Assistant entity IDs and their corresponding field names in the EnergyV1 InfluxDB bucket. The "Legacy Node-RED" column shows the equivalent field names from the old Node-RED flows (for historical data compatibility).

#### Battery Entities

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| battery_bus_voltage | battery_bus_voltage | voltage | BATT_V | Battery DC bus voltage |
| battery_charge_discharge_power | battery_charge_discharge_power | Power | BATT_W | Battery charge/discharge power |
| battery_state_of_capacity | battery_state_of_capacity | Misc | BATT_Level | Battery SOC percentage |
| battery_total_charge | battery_total_charge_energy | Energy | Batt_charge_kWh | Total energy charged |
| battery_total_discharge | battery_total_discharge_energy | Energy | Batt_discharge_kWh | Total energy discharged |
| battery_net_energy | battery_net_energy | Energy | Batt_tot_kWh | Net energy (charge - discharge) |

#### Inverter/DC Entities

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| inverter_input_power | inverter_input_power | Power | DC_W | Total DC input power |
| inverter_pv_1_voltage | inverter_pv_1_voltage | voltage | DC1_V | PV string 1 voltage |
| inverter_pv_1_current | inverter_pv_1_current | Current | DC1_A | PV string 1 current |
| inverter_pv_1_power | inverter_pv_1_power | Power | DC1_W | PV string 1 power |
| inverter_pv_2_voltage | inverter_pv_2_voltage | voltage | DC2_V | PV string 2 voltage |
| inverter_pv_2_current | inverter_pv_2_current | Current | DC2_A | PV string 2 current |
| inverter_pv_2_power | inverter_pv_2_power | Power | DC2_W | PV string 2 power |
| inverter_daily_yield | inverter_daily_yield | Energy | - | Daily energy yield |
| inverter_total_yield | inverter_total_ac_yield | Energy | DC_kWh | Total AC energy yield |
| inverter_efficiency | inverter_efficiency | Misc | Inv_Efficiency | Inverter efficiency % |
| inverter_active_power | inverter_active_power | Power | AC_W | AC active power output |
| inverter_off_grid_status | inverter_off_grid_status | Misc | Grid_State | On-grid/Off-grid status |

#### AC Voltage Entities

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| inverter_phase_a_voltage | inverter_phase_a_voltage | voltage | AC1_V | Phase A voltage |
| inverter_phase_b_voltage | inverter_phase_b_voltage | voltage | AC2_V | Phase B voltage |
| inverter_phase_c_voltage | inverter_phase_c_voltage | voltage | AC3_V | Phase C voltage |

#### Grid/Power Meter Entities

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| power_meter_active_power | power_meter_active_power | Power | Grid_W | Grid active power (+import/-export) |
| power_meter_phase_a_active_power | power_meter_phase_a_active_power | Power | Grid1_W | Phase A grid power |
| power_meter_phase_b_active_power | power_meter_phase_b_active_power | Power | Grid2_W | Phase B grid power |
| power_meter_phase_c_active_power | power_meter_phase_c_active_power | Power | Grid3_W | Phase C grid power |
| power_meter_consumption | power_meter_consumption | Energy | Grid_import_kWh | Total grid import |
| power_meter_exported | power_meter_exported | Energy | Grid_export_kWh | Total grid export |
| power_meter_frequency | power_meter_frequency | Misc | - | Grid frequency (Hz) |
| power_meter_power_factor | power_meter_power_factor | Misc | Factor | Power factor |
| power_meter_total_energy_2 | power_meter_net_energy | Energy | Grid_tot_kWh | Net grid energy |
| grid_total_energy | grid_total_energy | Energy | - | Total grid energy (import + export) |
| grid_state | grid_state | Misc | Grid_State | Grid connection state (1=on-grid) |

#### Load Entities (Shelly 2PM and 3EM)

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| phase_1_power | load_phase_1_power | Power | - | Phase 1 load power |
| phase_2_power | load_phase_2_power | Power | - | Phase 2 load power |
| phase_3_power | load_phase_3_power | Power | - | Phase 3 load power |
| phase_1_energy | load_phase_1_energy | Energy | - | Phase 1 load energy |
| phase_2_energy | shelly_phase_2_energy | Energy | - | Phase 2 load energy |
| phase_3_energy | load_phase_3_energy | Energy | - | Phase 3 load energy |
| load_power (template) | load_total_power | Power | Load_W | Total load power (sum of 3 phases) |
| load_energy (template) | load_total_energy | Energy | Load_kWh | Total load energy (sum of 3 phases) |
| load_bench_power | load_bench_power | Power | - | Lab bench power (renamed from shelly_2pm_white_switch_0_power) |
| load_desk_power | load_desk_power | Power | - | Lab desk power (renamed from shelly_2pm_white_switch_1_power) |
| load_1_rest (template) | - | Power | - | Phase 1 minus desk minus bench (remaining load) |

**Load consolidation (2026-02-19):** Removed redundant template sensors `load_1x`, `load_2x`, `load_3x` (copies of Shelly phase sensors), `load_wx` (duplicate sum identical to `load_power`), and `load_w` (applied a 2% correction factor that was no longer needed). The canonical total load entity is `sensor.load_power` (`Load Total Power`), used by the EnergyManager, InfluxDB transfer task, Grafana dashboards, and the AmazonFire dashboard. `load_1_rest` was updated to reference `sensor.load_phase_1_power` directly.

#### Enphase Entities (MQTT)

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| enphase_energy_power | enphase_energy_power | Power | Enphase_Power | Enphase inverter power |
| enphase_energy_total | enphase_energy_total | Energy | Enphase_Energy | Total Enphase energy |
| enphase_power | enphase_power | Power | Enphase1_power | Enphase power (alternate) |
| enphase_voltage | enphase_voltage | voltage | Voltage | Enphase AC voltage |
| enphase_current | enphase_current | Current | Current | Enphase AC current |
| enphase_energy_today | enphase_energy_today | Energy | Today | Enphase daily energy |

#### Calculated/Template Entities

| HA Entity ID | EnergyV1 Field | Measurement | Legacy Node-RED | Description |
|--------------|----------------|-------------|-----------------|-------------|
| solar_pv_total_ac_power | solar_ac_total_power | Power | DC_W_tot | Total solar power (Huawei + Enphase) |
| solar_pv_total_ac_energy | solar_ac_total_energy | Energy | Energy_tot | Total solar energy |
| self_consumption_ratio | self_consumption_ratio | Misc | Self | Self-consumption percentage |
| autarchy | autarchy | Misc | Autarchy | Self-sufficiency indicator |
| surplus_power | surplus_power | Power | - | Surplus power (solar - load) |

#### MBUS Entities (via HA MQTT sensor + InfluxDB task)

Smart meter grid power flows through HA and the InfluxDB consolidation task:

| HA Entity | InfluxDB Task Mapping | HomeData Field | Measurement | Description |
|-----------|----------------------|----------------|-------------|-------------|
| `sensor.grid_power` | `grid_power` → `M_Grid` | M_Grid | MBUS | Net grid power (Po - Pi) × 1000 W |

### Current InfluxDB Bucket Schemas

**Last updated:** 2026-03-27

#### Bucket Overview

| Bucket | Retention | Purpose |
|--------|-----------|---------|
| HomeData | infinite | Primary energy data (consolidated) |
| HomeAssistant | 1 year | Raw HA sensor data |
| EnergyV1 | infinite | Legacy energy data (archive) |
| energy_manager | infinite | EnergyManager addon decisions |
| load_forecast | 30 days | Load predictions (P10/P50/P90) |
| pv_forecast | 30 days | PV predictions (P10/P50/P90) |
| Water | infinite | Water meter data |
| Weight | infinite | Weight data |
| weather/autogen | infinite | Weather data |
| HugoNew | infinite | Hugo data |
| TempEnergy | infinite | Temporary energy data |
| YOUTUBE/autogen | infinite | YouTube data |

#### HomeData Bucket

Primary energy data bucket with measurements organized by type.

| Measurement | Fields | Description |
|-------------|--------|-------------|
| **Power** | battery_charge_discharge_power, inverter_input_power, inverter_pv_1_power, inverter_pv_2_power, inverter_active_power, power_meter_active_power, power_meter_phase_a/b/c_active_power, load_total_power, load_phase_1/2/3_power, load_bench_power, load_desk_power, enphase_power, enphase_energy_power, solar_ac_total_power, Pi, Po | Power readings (W) |
| **Energy** | battery_total_charge_energy, battery_total_discharge_energy, battery_net_energy, battery_day_charge, battery_day_discharge, inverter_total_ac_yield, inverter_daily_yield, inverter_total_dc_input_energy, power_meter_consumption, power_meter_exported, power_meter_net_energy, grid_total_energy, load_total_energy, load_phase_1/2/3_energy, enphase_energy_total, enphase_energy_today, enphase_energy_yesterday, solar_ac_total_energy, Ei, Eo | Energy totals (kWh) |
| **voltage** | battery_bus_voltage, inverter_pv_1_voltage, inverter_pv_2_voltage, inverter_phase_a/b/c_voltage, power_meter_phase_a/b/c_voltage, enphase_voltage | Voltage readings (V) |
| **Current** | inverter_pv_1_current, inverter_pv_2_current, enphase_current | Current readings (A) |
| **Misc** | battery_state_of_capacity, inverter_efficiency, inverter_off_grid_status, inverter_device_status, power_meter_frequency, power_meter_power_factor, grid_state, enphase_energy_apparentpower, enphase_energy_reactivepower, enphase_energy_factor, enphase_energy_totalstarttime | Status and misc values |
| **MBUS** | M_Grid | Smart meter grid power via HA sensor.grid_power (legacy fields Pi, Po, Ei, Eo, I1-I3, Q5-Q8, SMid, ts no longer written) |

#### energy_manager Bucket

EnergyManager add-on output for optimization decisions, SOC projections, appliance signals, and EV charging control. Detailed schema: [EnergyManager FSD](../energymanager/Documents/energymanager-fsd.md).

#### load_forecast Bucket

LoadForecast add-on output for short-retention household demand predictions. Detailed schema: [LoadForecast FSD](../loadforecast/Documents/loadforecast-fsd.md).

#### pv_forecast Bucket

SwissSolarForecast add-on output for short-retention PV predictions and forecast snapshots. Detailed schema: [SwissSolarForecast FSD](../swisssolarforecast/Documents/swisssolarforecast-fsd.md).
