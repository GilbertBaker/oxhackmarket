"""Live Isis conditions: Met Office weather (via Open-Meteo) and the OURCs Isis flag.

Weather comes from the Met Office UKV 2 km model (Open-Meteo `ukmo_seamless`),
sampled at the Isis boathouses. No API keys or pip packages required.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import threading
import time

# Isis boathouses, Christ Church Meadow (between Folly Bridge and Iffley Lock).
LATITUDE = 51.7437
LONGITUDE = -1.2466

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
FLAG_URL = "https://ourcs.co.uk/api/flags/status/isis/"

# Open-Meteo variable -> key used here (matches the training CSV columns).
FIELDS = {
    "temperature_2m": "temperature_2m_C",
    "precipitation": "precipitation_mm_prev_hour",
    "weather_code": "weather_code_WMO",
    "wind_speed_10m": "wind_speed_10m_kmh",
    "wind_direction_10m": "wind_direction_10m_deg",
    "wind_gusts_10m": "wind_gusts_10m_kmh",
    "visibility": "visibility_m",
    "cape": "cape_J_per_kg",
}

REFRESH_SECONDS = 3600


def fetch_json(url):
    request = Request(url, headers={"User-Agent": "OxHack Isis flag predictor"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_weather():
    """Return (hourly rows keyed by local 'YYYY-MM-DDTHH:00', UTC offset) for yesterday and today."""
    query = urlencode({
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "hourly": ",".join(FIELDS),
        "models": "ukmo_seamless",
        "timezone": "Europe/London",
        "past_days": 1,
        "forecast_days": 1,
    })
    data = fetch_json(f"{WEATHER_URL}?{query}")
    hourly = data["hourly"]
    rows = {
        stamp: {key: hourly[field][i] for field, key in FIELDS.items()}
        for i, stamp in enumerate(hourly["time"])
    }
    return rows, timedelta(seconds=data["utc_offset_seconds"])


def fetch_flag():
    data = fetch_json(FLAG_URL)
    set_at = datetime.fromisoformat(data["set_date"].replace("Z", "+00:00"))
    return {
        "status": data["status_text"],
        "notices": data.get("notices", []),
        "set_at": set_at,
    }


class IsisConditions:
    """Latest Isis weather and flag, refreshed hourly by a background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self.updated_at = None
        self.last_error = None
        self.current = None  # latest hour's weather + current flag
        self.at_0700 = None  # today's 07:00 weather

    def refresh(self):
        try:
            rows, utc_offset = fetch_weather()
            flag = fetch_flag()
        except Exception as error:  # keep the last good data on network/API errors
            with self._lock:
                self.last_error = f"{type(error).__name__}: {error}"
            return

        now_local = datetime.now(timezone.utc) + utc_offset
        today = now_local.date().isoformat()

        current_hour = now_local.strftime("%Y-%m-%dT%H:00")
        with self._lock:
            self.current = {
                "time_local": current_hour,
                **rows.get(current_hour, {}),
                "ourcs_isis_flag": flag["status"],
                "ourcs_isis_flag_set_at": flag["set_at"].isoformat(),
                "ourcs_isis_notices": flag["notices"],
            }
            self.at_0700 = {
                "date": today,
                "time_local": "07:00",
                **rows.get(f"{today}T07:00", {}),
            }
            self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.last_error = None

    def snapshot(self):
        with self._lock:
            return {
                "location": {"latitude": LATITUDE, "longitude": LONGITUDE},
                "source": "Met Office UKV via Open-Meteo; OURCs flag API",
                "updated_at": self.updated_at,
                "last_error": self.last_error,
                "current": self.current,
                "at_0700": self.at_0700,
            }

    def start(self):
        """Refresh now, then just after the top of every hour, in a daemon thread."""
        def loop():
            while True:
                self.refresh()
                time.sleep(REFRESH_SECONDS - time.time() % REFRESH_SECONDS + 30)

        threading.Thread(target=loop, daemon=True, name="isis-conditions").start()


conditions = IsisConditions()
