"""Thread-safe in-memory store of the latest reading from each sensor.

The MQTT subscriber writes into this cache as messages arrive; the display
(and later, the SQLite writer) read snapshots from it on their own 5-minute
timer. Reads and writes can happen from different threads, so all access
goes through a lock.
"""

import threading
from datetime import datetime, timedelta

STALE_AFTER = timedelta(minutes=15)

PI_DEVICE_NAME = "gn-pi-zero-2"


def celsius_to_fahrenheit(temp_c: float) -> float:
    return temp_c * 9 / 5 + 32


class SensorCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._shelly = {}  # dev_name -> {temperature_f, humidity, battery, rssi, last_seen}
        self._pi_bme280 = {}  # {temp_f, humidity, pressure, last_seen}
        self._pi_pm25 = {}  # {pm25, last_seen}

        # A save is triggered once both the BME280 and pm25 topics have each
        # posted at least once since the last save — that naturally paces
        # storage (and thus how often Shelly readings get persisted) to the
        # Pi's own ~5-minute reporting cycle, rather than a wall-clock timer.
        self._bme280_since_save = False
        self._pm25_since_save = False
        self._on_ready_to_save = None

    def set_save_callback(self, callback) -> None:
        """Register a zero-arg callback, invoked once BME280 and pm25 have
        both reported since the last save."""
        self._on_ready_to_save = callback

    def _mark_ready_and_maybe_save(self, *, bme280: bool = False, pm25: bool = False) -> None:
        with self._lock:
            if bme280:
                self._bme280_since_save = True
            if pm25:
                self._pm25_since_save = True
            ready = self._bme280_since_save and self._pm25_since_save
            if ready:
                self._bme280_since_save = False
                self._pm25_since_save = False

        if ready and self._on_ready_to_save:
            self._on_ready_to_save()

    def update_shelly(self, dev_name: str, temperature_c: float, humidity: float,
                       battery: int, rssi: int) -> None:
        with self._lock:
            self._shelly[dev_name] = {
                "temperature_f": celsius_to_fahrenheit(temperature_c),
                "humidity": humidity,
                "battery": battery,
                "rssi": rssi,
                "last_seen": datetime.now(),
            }

    def update_bme280(self, temp_f: float, humidity: float, pressure: float) -> None:
        with self._lock:
            self._pi_bme280 = {
                "temp_f": temp_f,
                "humidity": humidity,
                "pressure": pressure,
                "last_seen": datetime.now(),
            }
        self._mark_ready_and_maybe_save(bme280=True)

    def update_pm25(self, pm25: int) -> None:
        with self._lock:
            self._pi_pm25 = {
                "pm25": pm25,
                "last_seen": datetime.now(),
            }
        self._mark_ready_and_maybe_save(pm25=True)

    def snapshot(self) -> dict:
        """Return a copy of the current cache, with a `stale` flag per entry."""
        now = datetime.now()

        def is_stale(last_seen):
            return last_seen is None or now - last_seen > STALE_AFTER

        with self._lock:
            shelly = {
                dev_name: {**reading, "stale": is_stale(reading["last_seen"])}
                for dev_name, reading in self._shelly.items()
            }
            bme280 = dict(self._pi_bme280)
            pm25 = dict(self._pi_pm25)

        pi_last_seen = [r["last_seen"] for r in (bme280, pm25) if r.get("last_seen")]
        pi_stale = is_stale(max(pi_last_seen)) if pi_last_seen else True

        return {
            "shelly": shelly,
            "pi": {**bme280, **pm25, "stale": pi_stale},
        }
