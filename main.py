"""Entry point: runs the MQTT subscriber, the tkinter display, and SQLite
storage, all sharing one SensorCache.

The MQTT client runs its network loop on a background thread. A save to
SQLite is triggered from that thread once both the BME280 and pm25 topics
have reported (see SensorCache.set_save_callback) — that ties storage to the
Pi's own ~5-minute reporting cycle instead of a separate wall-clock timer,
so Shelly readings also only get persisted once per cycle. The display polls
the cache on its own short timer (see display.py) so Shelly values show up
as soon as they arrive, independent of when storage saves happen.
"""

import argparse
import logging

import storage
from display import SensorDisplay
from logging_config import configure_logging
from mqtt_subscriber import build_client
from sensor_cache import SensorCache

configure_logging()
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fullscreen", action="store_true",
        help="Run the display fullscreen (e.g. on the Pi's TFT). Default: windowed.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    cache = SensorCache()
    conn = storage.init_db()

    def save_snapshot():
        storage.save_snapshot(conn, cache.snapshot())
        storage.prune_old(conn)
        logger.info("Saved snapshot to SQLite")

    cache.set_save_callback(save_snapshot)

    client = build_client(cache)
    client.loop_start()

    display = SensorDisplay(cache, fullscreen=args.fullscreen)

    def shutdown():
        client.loop_stop()
        client.disconnect()
        conn.close()
        display.root.destroy()

    display.root.protocol("WM_DELETE_WINDOW", shutdown)
    display.set_on_quit(shutdown)

    try:
        display.start()
    except KeyboardInterrupt:
        shutdown()



if __name__ == "__main__":
    main()
