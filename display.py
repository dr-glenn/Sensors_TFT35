"""tkinter dashboard for the 480x320 TFT.

Reads snapshots from a SensorCache on a 5-minute timer and draws a fixed
grid of boxes (5 Shelly sensors + 1 combined Pi box), greying out any box
whose sensor hasn't reported in 15+ minutes. Runs identically under a
desktop window (for development) or under X11 on the Raspberry Pi.
"""

import tkinter as tk
from datetime import datetime
from zoneinfo import ZoneInfo

from history_view import HistoryView
from sensor_cache import PI_DEVICE_NAME, SensorCache
from theme import BG_COLOR, BOX_COLOR, STALE_COLOR, STALE_TEXT_COLOR, TEXT_COLOR

WINDOW_WIDTH = 480
WINDOW_HEIGHT = 320
GRID_COLS = 3
GRID_ROWS = 2
MAX_SHELLY_BOXES = 5


# Sensor readings arrive continuously (Shelly posts many times/minute); the
# display polls the cache on this short timer so a box updates as soon as a
# new reading comes in, rather than waiting for a fixed 5-minute cycle. This
# is independent of the SQLite save cadence, which is triggered separately
# (see main.py / SensorCache.set_save_callback).
REFRESH_INTERVAL_MS = 2 * 1000
CLOCK_TICK_MS = 1000

DISPLAY_TZ = ZoneInfo("America/Los_Angeles")


class SensorDisplay:
    def __init__(self, cache: SensorCache, fullscreen: bool = False, on_quit=None):
        self.cache = cache
        # Fullscreen has no window-manager title bar/close button, and the TFT
        # is touchscreen-only (no keyboard) — so the on-screen [X] below is the
        # only way to exit. on_quit lets main.py wire in a clean shutdown
        # (stop MQTT, close the DB) instead of just destroying the window.
        self._on_quit = on_quit

        self.root = tk.Tk()
        self.root.title("Sensor Dashboard")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.configure(bg=BG_COLOR)

        if fullscreen:
            self.root.attributes("-fullscreen", True)
            self.root.config(cursor="none")
        # Escape always exits fullscreen, even when not started fullscreen,
        # so a keyboard-equipped dev/debug session on the Pi can get out of it.
        self.root.bind("<Escape>", lambda event: self.root.attributes("-fullscreen", False))

        top_frame = tk.Frame(self.root, bg=BG_COLOR)
        top_frame.pack(fill="x", pady=(4, 2))

        self.clock_label = tk.Label(
            top_frame, text="", font=("Helvetica", 16, "bold"),
            bg=BG_COLOR, fg=TEXT_COLOR,
        )
        self.clock_label.pack(side="left", expand=True, padx=(30, 0))

        exit_button = tk.Button(
            top_frame, text="X", font=("Helvetica", 12, "bold"),
            bg=BOX_COLOR, fg=TEXT_COLOR, activebackground=STALE_COLOR,
            relief="flat", bd=0, width=3, height=1,
            command=self._quit,
        )
        exit_button.pack(side="right", padx=(0, 6))

        grid_frame = tk.Frame(self.root, bg=BG_COLOR)
        grid_frame.pack(fill="both", expand=True)
        for col in range(GRID_COLS):
            grid_frame.columnconfigure(col, weight=1)
        for row in range(GRID_ROWS):
            grid_frame.rowconfigure(row, weight=1)

        self.boxes = []
        for i in range(GRID_COLS * GRID_ROWS):
            row, col = divmod(i, GRID_COLS)
            frame = tk.Frame(grid_frame, bg=BOX_COLOR, bd=1, relief="solid")
            frame.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)

            name_label = tk.Label(frame, font=("Helvetica", 16, "bold"), bg=BOX_COLOR, fg=TEXT_COLOR)
            name_label.pack(pady=(6, 2))
            value_label = tk.Label(frame, font=("Helvetica", 16), bg=BOX_COLOR, fg=TEXT_COLOR, justify="left")
            value_label.pack()

            # sensor_id/title are filled in by _refresh_boxes; sensor_id None = nothing to chart.
            box = {"frame": frame, "name_label": name_label, "value_label": value_label,
                   "sensor_id": None, "title": ""}
            self.boxes.append(box)
            # Labels sit on top of the frame and swallow clicks, so bind all three.
            for widget in (frame, name_label, value_label):
                widget.bind("<Button-1>", lambda event, box=box: self._open_history(box))

        # Overlays the dashboard when a box is tapped; created last so it stacks on top.
        self.history = HistoryView(self.root)

    def _open_history(self, box: dict) -> None:
        if box["sensor_id"] is not None:
            self.history.show(box["sensor_id"], box["title"])

    def set_on_quit(self, callback) -> None:
        """Register the callback the on-screen [X] button invokes."""
        self._on_quit = callback

    def _quit(self) -> None:
        if self._on_quit:
            self._on_quit()
        else:
            self.root.destroy()

    def start(self) -> None:
        self._tick_clock()
        self._refresh_boxes()
        self.root.mainloop()

    def _tick_clock(self) -> None:
        now = datetime.now(DISPLAY_TZ)
        self.clock_label.config(text=now.strftime("%A %b %d  %I:%M %p %Z"))
        self.root.after(CLOCK_TICK_MS, self._tick_clock)

    def _refresh_boxes(self) -> None:
        snapshot = self.cache.snapshot()
        box_data = self._build_box_data(snapshot)

        for box, data in zip(self.boxes, box_data):
            bg = STALE_COLOR if data["stale"] else BOX_COLOR
            fg = STALE_TEXT_COLOR if data["stale"] else TEXT_COLOR
            box["sensor_id"] = data["sensor_id"]
            box["title"] = data["name"]
            box["frame"].configure(bg=bg)
            box["name_label"].configure(text=data["name"], bg=bg, fg=fg)
            box["value_label"].configure(text=data["value_text"], bg=bg, fg=fg)

        self.root.after(REFRESH_INTERVAL_MS, self._refresh_boxes)

    @staticmethod
    def _build_box_data(snapshot: dict) -> list:
        boxes = []

        shelly_names = sorted(snapshot["shelly"].keys())[:MAX_SHELLY_BOXES]
        for dev_name in shelly_names:
            reading = snapshot["shelly"][dev_name]
            boxes.append({
                "name": dev_name,
                "sensor_id": dev_name,
                "value_text": f"{reading['temperature_f']:.1f}°F\n{reading['humidity']:.0f}% RH",
                "stale": reading["stale"],
            })

        while len(boxes) < MAX_SHELLY_BOXES:
            boxes.append({"name": "(no sensor)", "sensor_id": None, "value_text": "--", "stale": True})

        pi = snapshot["pi"]
        lines = []
        if "temp_f" in pi:
            lines.append(f"{pi['temp_f']:.1f}°F  {pi['humidity']:.0f}% RH")
        if "pressure" in pi:
            lines.append(f"{pi['pressure']:.1f} hPa")
        if "pm25" in pi:
            lines.append(f"PM2.5: {pi['pm25']}")
        boxes.append({
            "name": "Pi (BME280 / AQ)",
            "sensor_id": PI_DEVICE_NAME,
            "value_text": "\n".join(lines) if lines else "--",
            "stale": pi["stale"],
        })

        return boxes


if __name__ == "__main__":
    # Fake data for local testing without a Raspberry Pi / MQTT broker.
    demo_cache = SensorCache()
    demo_cache.update_shelly("MBR Deck", temperature_c=21.4, humidity=70, battery=84, rssi=-62)
    demo_cache.update_shelly("Living Room", temperature_c=23.1, humidity=45, battery=91, rssi=-58)
    demo_cache.update_shelly("Garage", temperature_c=18.9, humidity=55, battery=76, rssi=-70)
    demo_cache.update_bme280(temp_f=72.2, humidity=71.2, pressure=1008.1)
    demo_cache.update_pm25(pm25=1)

    SensorDisplay(demo_cache, fullscreen=False).start()
