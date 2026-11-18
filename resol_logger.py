#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
resol_logger.py
A lightweight, zero-dependency RESOL VBus-over-TCP client that decodes 
Sonnenkraft SKSR 1/2/3 (0x7210) packets and logs them into a SQLite database.

Compatible with:
- Sonnenkraft SKSR 1/2/3 / SKSC3 Solar Controllers
- Sonnenkraft COMFORT plus 3 / SLM50 Schichtlademodul
- RESOL VBus/LAN Adapter, DL2, DL3, KM2, or VBus-over-TCP bridges
"""

import sys
import os
import socket
import time
import sqlite3
from datetime import datetime

# Configuration (override via environment variables or set your device IP here)
IP_ADDRESS = os.getenv("VBUS_HOST", "192.168.1.21")
TCP_PORT = int(os.getenv("VBUS_PORT", "7053"))
PASSWORD = os.getenv("VBUS_PASSWORD", "vbus")
DB_NAME = os.getenv("VBUS_DB_PATH", "resol.db")
INTERVAL_SECONDS = int(os.getenv("VBUS_INTERVAL", "60"))  # Write interval to database

# VBus protocol constants
SOURCE_SKSR = 0x7210
DEST_DFA = 0x0010
CMD_DATA = 0x0100

def parse_sksr_payload(payload):
    """
    Parses the 40-byte payload of the Sonnenkraft SKSR 1/2/3 controller (0x7210)
    (10 frames * 4 bytes = 40 bytes total).
    
    Verified Sonnenkraft COMFORT plus 3 hydraulic mapping:
      F1 (S1): Kollektorfühler (°C)
      F2 (S2): Sekundär Vorlauf (°C, Modulausgang Wärmetauscher)
      F3 (S3): Pufferspeicher Unten (°C, Referenz Solar)
      F4 (S4): Sekundär Rücklauf (°C, Speicher zu WT)
      F5 (S5): Nicht verbaut (offener Eingang)
      F6 (S6): Speicher Oben (°C, Warmwasserzone)
      F7 (S7): Puffer Mitte (°C, Heizungszone)
      F8 (S8): Heizungsrücklauf (°C)
      
      A1: Primärpumpe (Solarkreispumpe, Drehzahl 0..100 %)
      A2: Sekundärpumpe (Pufferladepumpe, Drehzahl 0..100 %)
      A3: Dreiwege-Umschaltventil (Rücklaufanhebung, 0/100 %)
      A:  Dreiwege-Schichtladeventil (Speicher Oben/Mitte, 0/100 %)
    """
    if len(payload) < 40:
        return None
        
    def read_int16(idx):
        return int.from_bytes(payload[idx:idx+2], byteorder='little', signed=True)

    def read_uint16(idx):
        return int.from_bytes(payload[idx:idx+2], byteorder='little', signed=False)

    def read_temp_sensor(idx):
        raw = read_int16(idx)
        # 888.8 / 999.9 or corresponding sentinel markers indicate sensor errors/disconnected
        if raw in (8888, 9999, -8888, -9999):
            return None
        val = round(raw * 0.1, 1)
        # Physical feasibility check: Solar thermal sensors operate between -30°C and 150°C.
        # Any value outside this range is electrical noise or bus corruption and discarded as NULL.
        if val < -30.0 or val > 150.0:
            return None
        return val

    data = {}
    
    # Sensors F1..F8
    data["temp_sensor_1"] = read_temp_sensor(0)   # F1: Kollektor
    data["temp_sensor_2"] = read_temp_sensor(2)   # F2: Sekundär Vorlauf (Wärmetauscher Ausgang)
    data["temp_sensor_3"] = read_temp_sensor(4)   # F3: Puffer Unten (Solar-Referenz)
    data["temp_sensor_4"] = read_temp_sensor(6)   # F4: Sekundär Rücklauf (zum Wärmetauscher)
    data["temp_sensor_5"] = None                  # F5: Nicht verbaut (offener Eingang)
    data["temp_sensor_6"] = read_temp_sensor(10)  # F6: Speicher Oben (Trinkwarmwasser)
    data["temp_sensor_7"] = read_temp_sensor(12)  # F7: Puffer Mitte (Heizungspuffer)
    data["temp_sensor_8"] = read_temp_sensor(14)  # F8: Heizungsrücklauf
    
    # Einstrahlung: Nicht verbaut (kein CS10 Sensor angeschlossen)
    data["irradiation"] = None
    
    # Volumenstrom (l/min) vom SKSRV Durchflusssensor
    raw_flow = read_int16(18)
    data["volume_flow"] = round(raw_flow * 0.1, 1) if (raw_flow != -9999 and 0 <= raw_flow <= 100) else 0.0
    
    # Active Actuators: A1, A2, A3, and Schichtventil A
    data["pump_speed_a1"] = payload[20] if payload[20] <= 100 else 0  # A1: Primärpumpe Solar (0..100 %)
    data["pump_speed_a2"] = payload[21] if payload[21] <= 100 else 0  # A2: Sekundärpumpe Puffer (0..100 %)
    data["pump_speed_a3"] = payload[22] if payload[22] <= 100 else 0  # A3: Rücklaufanhebung Ventil (0..100 %)
    data["pump_speed_a"]  = payload[23] if payload[23] <= 100 else 0  # A:  Dreiwege-Schichtladeventil (0..100 %)
    
    # Heat Counter Wh (Wärmemenge)
    wh = read_int16(24)
    kwh = read_int16(26)
    mwh = read_int16(28) if len(payload) >= 30 else 0
    data["heat_wh"] = max(0, wh + (kwh * 1000) + (mwh * 1000000))
    
    # Diagnostics & Error Masks
    data["error_mask"] = payload[32] if len(payload) > 32 else 0
    data["sensor_bruch"] = payload[33] if len(payload) > 33 else 0
    data["sensor_kurz"] = payload[34] if len(payload) > 34 else 0
    
    # Systemzeit
    if len(payload) >= 38:
        sys_min_total = read_int16(36)
        sys_hour = max(0, min(23, sys_min_total // 60))
        sys_min = max(0, min(59, sys_min_total % 60))
        data["system_time"] = f"{sys_hour:02d}:{sys_min:02d}"
    else:
        data["system_time"] = None
        
    return data

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS measurements (
        timestamp DATETIME PRIMARY KEY,
        temp_sensor_1 REAL,
        temp_sensor_2 REAL,
        temp_sensor_3 REAL,
        temp_sensor_4 REAL,
        temp_sensor_5 REAL,
        temp_sensor_6 REAL,
        temp_sensor_7 REAL,
        temp_sensor_8 REAL,
        irradiation INTEGER,
        volume_flow REAL,
        pump_speed_a1 INTEGER,
        pump_speed_a2 INTEGER,
        pump_speed_a3 INTEGER,
        pump_speed_a INTEGER,
        heat_wh INTEGER,
        error_mask INTEGER,
        sensor_bruch INTEGER,
        sensor_kurz INTEGER,
        system_time TEXT
    )
    """)
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    conn.commit()
    conn.close()

def save_to_db(db_path, data_row):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT OR IGNORE INTO measurements (
            timestamp, temp_sensor_1, temp_sensor_2, temp_sensor_3, temp_sensor_4,
            temp_sensor_5, temp_sensor_6, temp_sensor_7, temp_sensor_8,
            irradiation, volume_flow, pump_speed_a1, pump_speed_a2, pump_speed_a3,
            pump_speed_a, heat_wh, error_mask, sensor_bruch, sensor_kurz, system_time
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """, (
            data_row["timestamp"],
            data_row.get("temp_sensor_1"), data_row.get("temp_sensor_2"), data_row.get("temp_sensor_3"), data_row.get("temp_sensor_4"),
            data_row.get("temp_sensor_5"), data_row.get("temp_sensor_6"), data_row.get("temp_sensor_7"), data_row.get("temp_sensor_8"),
            data_row.get("irradiation"), data_row.get("volume_flow"),
            data_row.get("pump_speed_a1"), data_row.get("pump_speed_a2"), data_row.get("pump_speed_a3"), data_row.get("pump_speed_a"),
            data_row.get("heat_wh"), data_row.get("error_mask"), data_row.get("sensor_bruch"), data_row.get("sensor_kurz"),
            data_row.get("system_time")
        ))
        conn.commit()
    except Exception as e:
        print(f"[{datetime.now()}] Database write error: {e}", file=sys.stderr, flush=True)
    finally:
        conn.close()

def handle_vbus_stream(db_path):
    global IP_ADDRESS, TCP_PORT, PASSWORD, INTERVAL_SECONDS
    last_db_write = 0
    
    while True:
        try:
            print(f"[{datetime.now()}] Connecting to VBus/LAN Adapter {IP_ADDRESS}:{TCP_PORT}...", flush=True)
            s = socket.create_connection((IP_ADDRESS, TCP_PORT), timeout=10)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            greeting = s.recv(1024).decode(errors="ignore").strip()
            if not greeting.startswith("+HELLO"):
                raise socket.error("Invalid protocol greeting")
            
            s.sendall(f"PASS {PASSWORD}\r\n".encode())
            auth_resp = s.recv(1024).decode(errors="ignore").strip()
            if not auth_resp.startswith("+OK"):
                raise socket.error("VBus authentication failed")
                
            s.sendall(b"DATA\r\n")
            stream_resp = s.recv(1024).decode(errors="ignore").strip()
            if not stream_resp.startswith("+OK"):
                raise socket.error("Failed to start data stream")
                
            print(f"[{datetime.now()}] Connected successfully. Logging active.", flush=True)
            
            buffer = bytearray()
            HEADER_MARKER = bytes.fromhex("aa 10 00 10 72 10 00 01 0b 51")
            
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    raise socket.error("Connection closed by peer")
                buffer.extend(chunk)
                
                while True:
                    pos = buffer.find(HEADER_MARKER)
                    if pos == -1:
                        if len(buffer) > 4096:
                            buffer = buffer[-512:]
                        break
                        
                    # SKSR 1/2/3 payload has 10 frames = 60 frame bytes + 10 header bytes = 70 bytes
                    if pos + 70 > len(buffer):
                        buffer = buffer[pos:]
                        break
                        
                    pkt = buffer[pos : pos + 70]
                    buffer = buffer[pos + 70 :]
                    
                    frame_bytes = pkt[10:]
                    payload = bytearray()
                    for f_i in range(10):
                        fr = frame_bytes[f_i*6 : (f_i+1)*6]
                        septet = fr[4]
                        for b_i in range(4):
                            b_val = fr[b_i]
                            if septet & (1 << b_i):
                                b_val |= 0x80
                            payload.append(b_val)
                            
                    decoded = parse_sksr_payload(payload)
                    if decoded is not None:
                        s1 = decoded.get("temp_sensor_1")
                        s2 = decoded.get("temp_sensor_2")
                        s3 = decoded.get("temp_sensor_3")
                        s6 = decoded.get("temp_sensor_6")
                        s7 = decoded.get("temp_sensor_7")
                        
                        # Only accept uncorrupted frames where physical sensors are present
                        if (s1 is not None and -25.0 <= s1 <= 140.0) and \
                           (s2 is not None and 10.0 <= s2 <= 95.0) and \
                           (s3 is not None and 10.0 <= s3 <= 95.0) and \
                           (s6 is not None and 10.0 <= s6 <= 95.0) and \
                           (s7 is not None and 10.0 <= s7 <= 95.0):
                            
                            now = time.time()
                            if (now - last_db_write >= INTERVAL_SECONDS or last_db_write == 0):
                                decoded["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                save_to_db(db_path, decoded)
                                s8 = decoded.get("temp_sensor_8")
                                p1 = decoded.get("pump_speed_a1")
                                p2 = decoded.get("pump_speed_a2")
                                p3 = decoded.get("pump_speed_a3")
                                pa = decoded.get("pump_speed_a")
                                print(f"[{datetime.now()}] Saved record to SQLite: F1={s1}°C, F6={s6}°C, F7={s7}°C, F3={s3}°C, F2={s2}°C, F8={s8}°C | PumpeA1={p1}%, PumpeA2={p2}%, VentilA3={p3}%, VentilA={pa}%", flush=True)
                                last_db_write = now
                                
        except (socket.error, socket.timeout) as se:
            print(f"[{datetime.now()}] Connection error: {se}. Retrying in 10s...", file=sys.stderr, flush=True)
            time.sleep(10)
        except Exception as ex:
            print(f"[{datetime.now()}] Unexpected exception in stream: {ex}. Reconnecting...", file=sys.stderr, flush=True)
            time.sleep(10)

def main():
    print(f"=== RESOL VBus Headless Logger ===", flush=True)
    init_db(DB_NAME)
    print(f"Database {DB_NAME} initialized with WAL mode.", flush=True)
    try:
        handle_vbus_stream(DB_NAME)
    except KeyboardInterrupt:
        print("\nStopping logger cleanly.", flush=True)
        sys.exit(0)

if __name__ == "__main__":
    main()
