"""MQTT subscriber for the Shelly and Raspberry Pi environmental sensors.

Connects to the HiveMQ Cloud broker over TLS, subscribes to the sensor
topics, and feeds parsed readings into a SensorCache. This module only
handles ingestion — the display and SQLite storage read from the cache on
their own schedules (see CLAUDE.md).
"""

import json
import logging
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from sensor_cache import SensorCache

logger = logging.getLogger(__name__)

TOPIC_BME280 = "gn_home/gn-pi-zero-2/bme280/J"
TOPIC_PM25 = "gn_home/gn-pi-zero-2/pm25/J"
TOPIC_SHELLY_BLU_GW = "gn_home/shelly_blu_gw/blu_ht"
TOPIC_SHELLY_THING = "gn_home/shelly_thing/blu_ht"

SUBSCRIBED_TOPICS = [TOPIC_BME280, TOPIC_PM25, TOPIC_SHELLY_BLU_GW, TOPIC_SHELLY_THING]


def load_config():
    load_dotenv()
    host = os.environ["MQTT_HOST"]
    port = int(os.environ.get("MQTT_PORT", "8883"))
    username = os.environ["MQTT_USERNAME"]
    password = os.environ["MQTT_PASSWORD"]
    return host, port, username, password


def handle_message(cache: SensorCache, topic: str, payload: bytes) -> None:
    data = json.loads(payload)

    if topic == TOPIC_BME280:
        cache.update_bme280(
            temp_f=float(data["temp_f"]),
            humidity=float(data["humidity"]),
            pressure=float(data["pressure"]),
        )
    elif topic == TOPIC_PM25:
        cache.update_pm25(pm25=int(data["pm25"]))
    elif topic in (TOPIC_SHELLY_BLU_GW, TOPIC_SHELLY_THING):
        sensor_data = data["data"]
        cache.update_shelly(
            dev_name=data["dev_name"],
            temperature_c=float(sensor_data["Temperature"]),
            humidity=float(sensor_data["Humidity"]),
            battery=int(sensor_data["Battery"]),
            rssi=int(sensor_data["rssi"]),
        )
    else:
        logger.warning("Received message on unexpected topic: %s", topic)


def build_client(cache: SensorCache) -> mqtt.Client:
    host, port, username, password = load_config()

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    client.tls_set()

    def on_connect(client, userdata, connect_flags, reason_code, properties):
        if reason_code != 0:
            logger.error("MQTT connect failed: %s", reason_code)
            return
        logger.info("Connected to MQTT broker %s:%s", host, port)
        for topic in SUBSCRIBED_TOPICS:
            client.subscribe(topic)
            logger.info("Subscribed to %s", topic)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        logger.warning("Disconnected from MQTT broker: %s", reason_code)

    def on_message(client, userdata, msg):
        logger.debug("Received on %s: %r", msg.topic, msg.payload)
        try:
            handle_message(cache, msg.topic, msg.payload)
        except (KeyError, ValueError, json.JSONDecodeError):
            logger.exception("Failed to parse message on %s: %r", msg.topic, msg.payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.connect(host, port)
    return client


if __name__ == "__main__":
    import time

    from logging_config import configure_logging

    configure_logging()

    shared_cache = SensorCache()
    mqtt_client = build_client(shared_cache)
    mqtt_client.loop_start()

    try:
        while True:
            time.sleep(30)
            logger.info("Cache snapshot: %s", shared_cache.snapshot())
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
