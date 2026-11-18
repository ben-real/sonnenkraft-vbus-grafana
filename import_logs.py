#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
import_logs.py
Headless historical log importer. Scans directories for legacy RESOL TSV
text log files (TextData_*.log), parses them, and imports them into our SQLite database
with robust date parsing, scale decoding, and upsert deduplication.
"""

import sys
import os
import glob
import sqlite3
from datetime import datetime

DB_NAME = os.getenv("VBUS_DB_PATH", "resol.db")

def parse_german_float(s):
    """
    Converts German decimal format string (e.g. "38,9") to float.
    """
    if not s or s.strip() == "":
        return None
    try:
        cleaned = s.replace(",", ".").strip()
        # Handle sensor disconnect placeholder values (888.8 / 999.9 or corresponding signs)
        if cleaned in ("888.8", "999.9", "-888.8", "-999.9"):
            return None
        val = float(cleaned)
        if val < -30.0 or val > 150.0:
            return None
        return val
    except ValueError:
        return None

def parse_integer(s):
    if not s or s.strip() == "":
        return 0
    try:
        return int(s.strip())
    except ValueError:
        return 0

def import_log_file(db_path, file_path):
    """
    Parses a single legacy RESOL TextData_*.log file and inserts rows into the database.
    """
    print(f"Processing: {os.path.basename(file_path)}")
    
    # Old logs use ISO-8859-1 (Latin-1) encoding
    try:
        with open(file_path, "r", encoding="iso-8859-1") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading file {file_path}: {e}", file=sys.stderr)
        return 0
        
    if len(lines) < 3:
        print(f"Skipping empty/invalid file: {file_path}")
        return 0
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    imported_count = 0
    duplicate_count = 0
    
    for row_idx, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
            
        fields = line.strip().split("\t")
        if len(fields) < 14:
            continue
            
        raw_date = fields[0].strip()
        try:
            dt = datetime.strptime(raw_date, "%d.%m.%Y %H:%M:%S")
            timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
            
        temp_1 = parse_german_float(fields[1])
        temp_2 = parse_german_float(fields[2])
        temp_3 = parse_german_float(fields[3])
        temp_4 = parse_german_float(fields[4])
        temp_5 = None  # Omitted open channel
        temp_6 = parse_german_float(fields[6])
        temp_7 = parse_german_float(fields[7])
        temp_8 = parse_german_float(fields[8])
        
        irradiation = None
        volume_flow = parse_german_float(fields[10])
        
        pump_1 = parse_integer(fields[11])
        pump_2 = parse_integer(fields[12])
        pump_3 = parse_integer(fields[13])
        pump_a = parse_integer(fields[14])
        
        heat_wh = parse_integer(fields[15])
        error_mask = parse_integer(fields[16])
        sensor_bruch = parse_integer(fields[17])
        sensor_kurz = parse_integer(fields[18])
        
        system_time = fields[19].strip() if len(fields) > 19 else None
        
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
            timestamp_str,
            temp_1, temp_2, temp_3, temp_4,
            temp_5, temp_6, temp_7, temp_8,
            irradiation, volume_flow,
            pump_1, pump_2, pump_3, pump_a,
            heat_wh, error_mask, sensor_bruch, sensor_kurz,
            system_time
        ))
        
        if cursor.rowcount > 0:
            imported_count += 1
        else:
            duplicate_count += 1
            
    conn.commit()
    conn.close()
    
    print(f"Successfully imported: {imported_count} rows ({duplicate_count} skipped duplicates).")
    return imported_count

def main():
    print("=== RESOL Legacy Log Importer ===")
    
    conn = sqlite3.connect(DB_NAME)
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
        
    log_files = sorted(glob.glob("TextData_*.log") + glob.glob("**/*/TextData_*.log"))
    if not log_files:
        print("No legacy 'TextData_*.log' files found to import in current folder.")
        sys.exit(0)
        
    total_imported = 0
    for f in log_files:
        total_imported += import_log_file(DB_NAME, f)
        
    print(f"=== Log Import Finished! Total rows added: {total_imported} ===")

if __name__ == "__main__":
    main()
