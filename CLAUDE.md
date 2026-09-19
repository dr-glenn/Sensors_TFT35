# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is a fresh PyCharm project scaffold — `main.py` currently only contains PyCharm's
default "Hi, PyCharm" boilerplate. There is no build system, test suite, lint config, or README yet.
The sections below describe the intended purpose and architecture as relayed by the project owner, to
guide implementation as code is added — they are not yet reflected in the repo's contents.

## Purpose

A Raspberry Pi 4 subscribes to MQTT messages from two groups of environmental sensors and renders them
on an attached 3.5" TFT display (480x320 px), refreshing every 5 minutes.

Sensor sources:
- **5x Shelly SBHT-003C** sensors — temperature and humidity, published over MQTT.
- **1x Raspberry Pi** — publishes BME280 (temperature/humidity/pressure) and air quality sensor data
  over MQTT.

## Intended architecture

- **MQTT subscriber**: runs on the Pi 4 with the display; subscribes to topics for all 6 sensor sources
  (5 Shelly units + 1 Pi publisher).
- **Display renderer**: built with **tkinter**, running under X11 on the Pi (the TFT is the X display,
  not a raw framebuffer). Draws to the 480x320 window — current time plus one box per sensor (6 boxes
  total: 5 Shelly + 1 combined BME280/air-quality box, showing temp/humidity/pressure and pm25
  together).
- **Update cadence**: the display polls the cache on a short timer (a couple of seconds) so Shelly
  readings show up on screen as soon as they arrive, since Shelly posts many times per minute. This is
  independent of the SQLite save cadence below.

When implementing, keep the MQTT ingestion (subscribing, parsing/caching latest values per sensor) and
the display rendering (layout, drawing to the TFT) as separate concerns, since they run on different
cadences.

## MQTT
My broker is at HiveMQ.cloud. Connection is SSL.
URL and credentials are supplied via a `.env` file (broker URL, username, password) — keep it out of
version control.

The Raspberry Pi device posts temperature and humidity data from BME280 and from an air quality sensor.
This device posts every 5 minutes.
The topic is "gn_home/gn-pi-zero-2/bme280/J", payload example:
```json
{"time": "2026-09-04T16:35:26", "temp_c": "22.3", "temp_f": "72.2", "humidity": "71.2", "pressure": "1008.1", "topic": "gn_home/gn-pi-zero-2/bme280/J"}
```
Note `temp_f` is provided pre-converted — no need to convert `temp_c` for display.

There is an air quality sensor that posts to topic "gn_home/gn-pi-zero-2/pm25/J", payload example:
```json
{"time": "2026-09-04T16:35:26", "pm10": 0, "pm25": 1, "pm100": 1, "topic": "gn_home/gn-pi-zero-2/pm25/J"}
```
I am only interested in the "pm25" value from this payload (ignore `pm10`/`pm100`).

The Shelly sensors post two topics: "gn_home/shelly_blu_gw/blu_ht" or "gn_home/shelly_thing/blu_ht".
Each message identifies the device by `dev_name`; the environmental data is under `data.Temperature`
(Celsius — must convert to Fahrenheit for display) and `data.Humidity`. Payload example:
```json
{"type_name": "SBHT-003C", "type": "SBHT-003C", "data": {"encryption": false, "BTHome_version": 2, "pid": 54, "Battery": 84, "Humidity": 70, "Temperature": 21.4, "addr": "38:39:8f:70:40:65", "rssi": -62}, "dev_name": "MBR Deck", "topic": "gn_home/shelly_blu_gw/blu_ht"}
```
`data.Battery` and `data.rssi` are also available if useful later (not currently displayed).
These values are posted many times per minute, but the display should only be updated every 5 minutes.

## Data Storage
Save to **SQLite**, triggered once both the BME280 and pm25 topics have reported since the last save
(not a wall-clock timer) — since the Pi posts each of those roughly every 5 minutes, this naturally
saves a full snapshot (including the latest cached Shelly readings) about once every 5 minutes, without
persisting Shelly's much higher-frequency updates individually. Retain no more than 30 days of history
(prune older rows on each save).

## Display
Each sensor is displayed in a box, simply numeric values, in **Fahrenheit**.
The Shelly sensors should be labeled with the dev_name value.
The clock shows current time in **Pacific Time**.

If a sensor hasn't posted a new reading in the last **15 minutes**, grey out its box (rather than
showing a stale value or blanking it).

Create a fixed layout for now. I might want to update to a more flexible display config in the future.
  
