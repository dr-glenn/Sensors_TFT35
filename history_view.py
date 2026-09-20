"""24-hour temperature/humidity history page for one sensor.

A Frame that SensorDisplay places over the dashboard when a sensor box is
tapped; the [< Back] button removes it again. Data comes from the SQLite
`readings` table (see storage.load_history) and is re-read every minute while
the page is showing. Drawn on a plain tk.Canvas — no matplotlib needed on the Pi.

Layout (480x320): header, one readout line, then the chart. Temperature uses
the left axis, humidity the right axis, each in its own colour. Tap or drag on
the chart to read the values at that time.
"""

import math
import time
import tkinter as tk
from bisect import bisect_left
from datetime import datetime
from zoneinfo import ZoneInfo

import storage
from theme import BG_COLOR, BOX_COLOR, STALE_COLOR, TEXT_COLOR

HISTORY_HOURS = 24
RELOAD_MS = 60 * 1000
# The Pi saves about every 5 minutes; a longer hole than this (Pi off, MQTT
# down) breaks the line rather than drawing a misleading straight span across it.
GAP_SECONDS = 20 * 60

DISPLAY_TZ = ZoneInfo("America/Los_Angeles")

TEMP_COLOR = "#ff9f43"
HUMIDITY_COLOR = "#4cc9f0"
GRID_COLOR = "#2c2c70"
MUTED_COLOR = "#9a9ab0"

AXIS_FONT = ("Helvetica", 9)
DIVISIONS = 4  # horizontal grid intervals; both axes share them so gridlines line up
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, MARGIN_BOTTOM = 40, 40, 8, 22

# Columns in a point: (timestamp, temperature_f, humidity)
TEMP, HUMIDITY = 1, 2


def _nice_step(raw: float) -> float:
    """Smallest 1/2/5 x 10^n at or above `raw`."""
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if multiple * magnitude >= raw:
            return multiple * magnitude


def _axis(values: list) -> tuple:
    """Return (low, step) so that low + DIVISIONS*step covers `values`, with round numbers."""
    low_value, high_value = min(values), max(values)
    step = _nice_step(max(high_value - low_value, 1.0) / DIVISIONS)
    while True:
        low = math.floor(low_value / step) * step
        if low + DIVISIONS * step >= high_value:
            return low, step
        step = _nice_step(step * 1.01)


def _segments(points: list) -> list:
    """Split [(ts, value)] into runs with no gap longer than GAP_SECONDS."""
    runs, run = [], []
    for ts, value in points:
        if run and ts - run[-1][0] > GAP_SECONDS:
            runs.append(run)
            run = []
        run.append((ts, value))
    if run:
        runs.append(run)
    return runs


def _hour_label(local: datetime) -> str:
    return local.strftime("%I%p").lstrip("0").lower()  # "6am", "12pm"


class HistoryView(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=BG_COLOR)
        self._sensor_id = None
        self._points = []  # (ts, temperature_f, humidity), oldest first
        self._times = []   # ts column of _points, for bisect
        self._end = time.time()
        self._geometry = None  # (x0, x1, y0, y1, start_ts, end_ts) of the last draw
        self._y_of = {}        # TEMP/HUMIDITY -> function mapping a value to a canvas y
        self._cursor_ts = None
        self._after_id = None

        header = tk.Frame(self, bg=BG_COLOR)
        header.pack(fill="x", pady=(4, 2))
        tk.Button(
            header, text="< Back", font=("Helvetica", 12, "bold"),
            bg=BOX_COLOR, fg=TEXT_COLOR, activebackground=STALE_COLOR, activeforeground=TEXT_COLOR,
            relief="flat", bd=0, padx=10, command=self.hide,
        ).pack(side="left", padx=(6, 0))
        self._title_label = tk.Label(header, font=("Helvetica", 14, "bold"), bg=BG_COLOR, fg=TEXT_COLOR)
        self._title_label.pack(side="left", expand=True, padx=(0, 60))  # offset the Back button

        readout = tk.Frame(self, bg=BG_COLOR)
        readout.pack(fill="x", padx=10)
        self._time_label = tk.Label(readout, font=("Helvetica", 11), bg=BG_COLOR, fg=MUTED_COLOR)
        self._time_label.pack(side="left")
        self._humidity_label = tk.Label(readout, font=("Helvetica", 12, "bold"), bg=BG_COLOR, fg=HUMIDITY_COLOR)
        self._humidity_label.pack(side="right")
        self._temp_label = tk.Label(readout, font=("Helvetica", 12, "bold"), bg=BG_COLOR, fg=TEMP_COLOR)
        self._temp_label.pack(side="right", padx=(0, 14))

        self._canvas = tk.Canvas(self, bg=BG_COLOR, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda event: self._draw())
        self._canvas.bind("<Button-1>", self._on_touch)
        self._canvas.bind("<B1-Motion>", self._on_touch)

    def show(self, sensor_id: str, title: str) -> None:
        self._sensor_id = sensor_id
        self._title_label.config(text=f"{title} — 24 h")
        self._cursor_ts = None
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self._load()

    def hide(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.place_forget()

    def _load(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self._end = time.time()
        rows = storage.load_history(self._sensor_id, HISTORY_HOURS)
        self._points = [(saved_at.timestamp(), temp_f, humidity) for saved_at, temp_f, humidity in rows
                        if saved_at.timestamp() <= self._end]
        self._times = [point[0] for point in self._points]
        self._draw()
        self._after_id = self.after(RELOAD_MS, self._load)

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 100 or height < 100:
            return  # not laid out yet; <Configure> will call us again

        x0, x1 = MARGIN_LEFT, width - MARGIN_RIGHT
        y0, y1 = MARGIN_TOP, height - MARGIN_BOTTOM
        end = self._end
        start = end - HISTORY_HOURS * 3600
        self._geometry = (x0, x1, y0, y1, start, end)
        self._y_of = {}

        canvas.create_rectangle(x0, y0, x1, y1, fill=BOX_COLOR, outline=GRID_COLOR)
        if not self._points:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="No data yet",
                               fill=MUTED_COLOR, font=("Helvetica", 14))
            self._draw_cursor()
            return

        def x_of(ts):
            return x0 + (ts - start) / (end - start) * (x1 - x0)

        # Vertical grid + labels every 4 hours on local-time boundaries. Stepping
        # in UTC seconds keeps this right across a DST change.
        for ts in range(math.ceil(start / 3600) * 3600, int(end) + 1, 3600):
            local = datetime.fromtimestamp(ts, DISPLAY_TZ)
            if local.hour % 4:
                continue
            canvas.create_line(x_of(ts), y0, x_of(ts), y1, fill=GRID_COLOR)
            canvas.create_text(x_of(ts), y1 + 4, text=_hour_label(local), anchor="n",
                               fill=MUTED_COLOR, font=AXIS_FONT)

        first_axis = True
        for column, color, on_left in ((TEMP, TEMP_COLOR, True), (HUMIDITY, HUMIDITY_COLOR, False)):
            series = [(point[0], point[column]) for point in self._points if point[column] is not None]
            if not series:
                continue
            low, step = _axis([value for _, value in series])

            def y_of(value, low=low, step=step):
                return y1 - (value - low) / (DIVISIONS * step) * (y1 - y0)

            self._y_of[column] = y_of

            for i in range(DIVISIONS + 1):
                y = y_of(low + i * step)
                if first_axis and 0 < i < DIVISIONS:
                    canvas.create_line(x0, y, x1, y, fill=GRID_COLOR)
                canvas.create_text(x0 - 5 if on_left else x1 + 5, y, text=f"{low + i * step:g}",
                                   anchor="e" if on_left else "w", fill=color, font=AXIS_FONT)
            first_axis = False

            for run in _segments(series):
                if len(run) == 1:
                    x, y = x_of(run[0][0]), y_of(run[0][1])
                    canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=color, outline=color)
                else:
                    coords = [c for ts, value in run for c in (x_of(ts), y_of(value))]
                    canvas.create_line(*coords, fill=color, width=2, capstyle="round", joinstyle="round")

        self._draw_cursor()

    def _on_touch(self, event) -> None:
        if not self._points or self._geometry is None:
            return
        x0, x1, _, _, start, end = self._geometry
        ts = start + (event.x - x0) / (x1 - x0) * (end - start)
        self._cursor_ts = self._nearest(ts)
        self._draw_cursor()

    def _nearest(self, ts: float) -> float:
        i = bisect_left(self._times, ts)
        candidates = self._times[max(i - 1, 0):i + 1]
        return min(candidates, key=lambda t: abs(t - ts))

    def _draw_cursor(self) -> None:
        """Draw the tap marker and update the readout (latest reading when nothing is tapped)."""
        canvas = self._canvas
        canvas.delete("cursor")
        if not self._points:
            self._set_readout(None)
            return

        point = self._points[-1]
        if self._cursor_ts is not None and self._geometry is not None:
            x0, x1, y0, y1, start, end = self._geometry
            if start <= self._cursor_ts <= end:
                point = self._points[bisect_left(self._times, self._cursor_ts)]
                x = x0 + (point[0] - start) / (end - start) * (x1 - x0)
                canvas.create_line(x, y0, x, y1, fill=TEXT_COLOR, dash=(2, 3), tags="cursor")
                for column, color in ((TEMP, TEMP_COLOR), (HUMIDITY, HUMIDITY_COLOR)):
                    if point[column] is not None and column in self._y_of:
                        y = self._y_of[column](point[column])
                        canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=color, outline=TEXT_COLOR, tags="cursor")
        self._set_readout(point, latest=point is self._points[-1] and self._cursor_ts is None)

    def _set_readout(self, point, latest: bool = False) -> None:
        if point is None:
            self._time_label.config(text="")
            self._temp_label.config(text="")
            self._humidity_label.config(text="")
            return
        ts, temp_f, humidity = point
        local = datetime.fromtimestamp(ts, DISPLAY_TZ)
        clock = local.strftime("%a %I:%M %p").replace(" 0", " ")
        self._time_label.config(text=f"Latest  {clock}" if latest else clock)
        self._temp_label.config(text="--" if temp_f is None else f"{temp_f:.1f}°F")
        self._humidity_label.config(text="--" if humidity is None else f"{humidity:.0f}% RH")
