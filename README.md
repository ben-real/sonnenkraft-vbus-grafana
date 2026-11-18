# Sonnenkraft VBus Logger & Grafana Dashboards

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Grafana](https://img.shields.io/badge/Grafana-10%2B-orange.svg)](https://grafana.com)

A lightweight, zero-dependency 24/7 datalogger and turnkey Grafana monitoring stack for **Sonnenkraft SKSR 1/2/3 / SKSC3** solar controllers and **COMFORT plus 3 / SLM50** solar thermal heating systems.

Replaces the deprecated and buggy RESOL Service Center Java application with an autonomous, high-performance solution that runs on any low-power device (Synology NAS, Raspberry Pi, Linux home server) using **< 15 MB RAM**.

---

## Architecture Overview

```text
┌─────────────────────────┐          VBus-over-TCP          ┌──────────────────────────┐
│ Sonnenkraft SKSR 1/2/3  │ ──[VBus]──► RESOL VBus/LAN ──►  │ resol_logger.py (NAS/Pi) │
│ (Solar Controller 0x7210│               Adapter:7053      │  - Pure Python (Zero pip)│
└─────────────────────────┘                                 │  - Checksum & bounds filter
                                                            │  - 24/7 SQLite WAL append│
                                                            └─────────────┬────────────┘
                                                                          │ Writes
                                                                          ▼
┌─────────────────────────┐                                 ┌──────────────────────────┐
│     Windows PC / Mac    │  ◄──────── Read-only SMB/NFS ── │         resol.db         │
│  Grafana OSS in Docker  │                                 │ (Indexed SQLite WAL DB)  │
│  - SQLite Plugin        │                                 └──────────────────────────┘
│  - 3 Turnkey Dashboards │
└─────────────────────────┘
```

---

## Features

- **Zero External Dependencies:** Built entirely with Python's standard library (`socket`, `sqlite3`, `datetime`, `time`). No `pip install`, no compilers, no node runtime needed on the logging server.
- **Fixed SKSR 10-Frame Quirk:** Solves the known bug where standard VBus libraries parse 11 frames instead of 10 for `0x7210`, which causes cascading 6-byte phase shifts and corrupt readings (+3000°C spikes).
- **Physical Feasibility Filtering:** Automatically filters out floating ADC noise from unconnected analog terminals (e.g. open radiation or boiler sensor pins) as `NULL`.
- **High-Performance SQLite in WAL Mode:** Handles concurrent writes from the 24/7 daemon while Grafana queries over SMB/network shares without database lock collisions.
- **Legacy Importer Included:** Batch-import years of historical `TextData_*.log` files from the RESOL Java app into SQLite with automatic deduplication.
- **3 Pre-Built Grafana Dashboards:**
  1. **Overview Dashboard:** Compact single-row live stat cards for all active sensors and actuators + smooth temperature and pump speed graphs.
  2. **Hydraulikschema Live (Canvas):** Sonnenkraft COMFORT plus 3 hydraulic diagram with freely draggable live badges.
  3. **Control Strategy Dashboard:** Real-time diagnostics for all 4 solar control loops with calculated temperature differentials ($\Delta T$).

---

## Hydraulic Concept & Sensor Mapping (COMFORT plus 3)

| Channel | Controller Terminal | Hydraulic Function |
|---|---|---|
| **F1** (`temp_sensor_1`) | S1 | Solar collector temperature (Collector array roof output) |
| **F2** (`temp_sensor_2`) | S2 | Secondary flow temperature (SLM50 plate heat exchanger output) |
| **F3** (`temp_sensor_3`) | S3 | Buffer bottom temperature (Cold reference for solar charging) |
| **F4** (`temp_sensor_4`) | S4 | Secondary return temperature (Buffer outlet to heat exchanger) |
| **F6** (`temp_sensor_6`) | S6 | Storage top temperature (Domestic hot water / boiler zone) |
| **F7** (`temp_sensor_7`) | S7 | Buffer middle temperature (Space heating buffer zone) |
| **F8** (`temp_sensor_8`) | S8 | Heating circuit return temperature |
| **A1** (`pump_speed_a1`) | A1 | Primary solar loop circulation pump (Speed: 0..100%) |
| **A2** (`pump_speed_a2`) | A2 | Secondary buffer charging pump (Speed: 0..100%) |
| **A3** (`pump_speed_a3`) | A3 | 3-way heating return flow boost valve (Rücklaufanhebung) |
| **A** (`pump_speed_a`) | A | 3-way motorized stratified charging valve (Top F6 vs. Mid F7) |

### Control Strategies Monitored:
1. **Primary Solar Loop:** $F1 > F3 + \Delta T_{\text{on}} \implies \text{Pump } A1$
2. **Secondary Charging Loop:** $F2 > F3 + \Delta T_{\text{sec}} \implies \text{Pump } A2$
3. **Stratified Charging Valve:** $F2 > F6 + \Delta T_{\text{strat}} \implies \text{Valve } A \to \text{Top (Domestic Hot Water)}$
4. **Heating Return Boost:** $F7 > F8 + \Delta T_{\text{boost}} \implies \text{Valve } A3 \to \text{Pre-heat heating return}$

---

## Quickstart

### 1. Run the Logger (on your NAS, Raspberry Pi, or Linux host)

Clone this repository on your logging host:
```bash
git clone https://github.com/ben-real/sonnenkraft-vbus-grafana.git
cd sonnenkraft-vbus-grafana
```

Configure your VBus/LAN adapter IP via environment variables (or defaults in `resol_logger.py`):
```bash
export VBUS_HOST="192.168.1.21"
export VBUS_PORT="7053"
export VBUS_PASSWORD="vbus"
export VBUS_DB_PATH="resol.db"
export VBUS_INTERVAL="60"  # Log interval in seconds
```

Run in the background:
```bash
nohup python3 -u resol_logger.py >> logger.log 2>&1 & disown
```

*(Optional)* To start automatically at boot on a Synology NAS, add `cd /path/to/folder && nohup python3 -u resol_logger.py >> logger.log 2>&1 &` as a Boot-up Triggered Task in DSM Task Scheduler.

---

### 2. Import Historical Logs (Optional)

If you have legacy `TextData_*.log` files from the RESOL Service Center Java app:
```bash
# Place your TextData_*.log files in the directory or subfolders and run:
python3 import_logs.py
```
Deduplication is automatic (`INSERT OR IGNORE`), so running it multiple times will not create duplicates.

---

### 3. Launch Grafana in Docker

On your PC, Mac, or server:
```bash
docker compose up -d
```

Open your browser at:
👉 **`http://localhost:3000`**

Both datasources and dashboards will be loaded automatically:
- **`Solarthermie (Sonnenkraft SKSR)`**
- **`Solarthermie Hydraulikschema Live`**
- **`Solarthermie Regelungsstrategie (COMFORT plus 3)`**

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Legal & Trademark Notice

- **RESOL** and **VBus** are registered trademarks of *RESOL - Elektronische Regelungen GmbH*.
- **SONNENKRAFT** is a registered trademark of *Sonnenkraft GmbH*.
- All other product names, logos, and brands are property of their respective owners.
- This repository is an independent open-source project created solely for device interoperability and is not affiliated with, endorsed by, or sponsored by RESOL or Sonnenkraft.

