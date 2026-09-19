"""SQLite storage for sensor readings.

Called on a 5-minute timer with a SensorCache snapshot: writes one row per
sensor that has ever reported data, then prunes rows older than the
retention window.
"""

import sqlite3
from datetime import datetime, timedelta

from sensor_cache import PI_DEVICE_NAME

DB_PATH = "sensors.db"
RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_at TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    temperature_f REAL,
    humidity REAL,
    pressure REAL,
    pm25 INTEGER,
    battery INTEGER,
    rssi INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_saved_at ON readings (saved_at);
"""


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: saves are triggered from the MQTT client's
    # network thread (see SensorCache.set_save_callback), not the main thread.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    saved_at = datetime.now().isoformat(timespec="seconds")
    rows = []

    for dev_name, reading in snapshot["shelly"].items():
        if reading.get("last_seen") is None:
            continue
        rows.append((
            saved_at, dev_name,
            reading.get("temperature_f"), reading.get("humidity"),
            None, None,
            reading.get("battery"), reading.get("rssi"),
        ))

    pi = snapshot["pi"]
    if "temp_f" in pi or "pm25" in pi:
        rows.append((
            saved_at, PI_DEVICE_NAME,
            pi.get("temp_f"), pi.get("humidity"),
            pi.get("pressure"), pi.get("pm25"),
            None, None,
        ))

    if not rows:
        return

    conn.executemany(
        """INSERT INTO readings
           (saved_at, sensor_id, temperature_f, humidity, pressure, pm25, battery, rssi)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def prune_old(conn: sqlite3.Connection, retention_days: int = RETENTION_DAYS) -> None:
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM readings WHERE saved_at < ?", (cutoff,))
    conn.commit()
