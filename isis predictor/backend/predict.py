"""Flag forecast for the Isis (OURCs flag), served from forecast.py's output.

A background thread reissues the forecast (refreshing data first) whenever the
latest one is older than the most recent 07:00 cut-off, checking every 15 minutes.
"""

from pathlib import Path
import json
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FORECAST_FILE = ROOT / "model" / "latest_forecast.json"
CHECK_SECONDS = 900


class ForecastService:
    def __init__(self):
        self._lock = threading.Lock()
        self.latest = json.loads(FORECAST_FILE.read_text(encoding="utf-8")) if FORECAST_FILE.is_file() else None
        self.last_error = None
        self.running = False

    def snapshot(self):
        with self._lock:
            return {"forecast": self.latest, "updating": self.running, "last_error": self.last_error}

    def refresh_if_stale(self):
        import forecast  # Imported here: TensorFlow is slow to load, so keep it off the startup path.

        expected = f"{forecast.issue_day().date()}T{forecast.CUTOFF_HOUR:02d}:00"
        with self._lock:
            if self.latest and self.latest["issued_at"].startswith(expected):
                return
            self.running = True
        try:
            result = forecast.run_forecast(fetch=True)
            with self._lock:
                self.latest, self.last_error = result, None
        except Exception as error:  # Keep serving the last good forecast.
            with self._lock:
                self.last_error = f"{type(error).__name__}: {error}"
        finally:
            with self._lock:
                self.running = False

    def start(self):
        def loop():
            while True:
                self.refresh_if_stale()
                time.sleep(CHECK_SECONDS)

        threading.Thread(target=loop, daemon=True, name="flag-forecast").start()


forecasts = ForecastService()


def predict() -> bool:
    """Return True if the OURCs flag is expected to be green tomorrow."""
    latest = forecasts.snapshot()["forecast"]
    if not latest:
        raise RuntimeError("No forecast available yet")
    return latest["days"][0]["flag"] == "Green"
