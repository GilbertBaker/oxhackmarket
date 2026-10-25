# Data sources for predicting Oxford rowing flags

| Parameter | Why it matters | Free data source | Update frequency | Notes |
|---|---|---|---|---|
| **Osney and Iffley river levels** | Primary driver of the rowing flag | [Environment Agency Flood Monitoring API](https://environment.data.gov.uk/flood-monitoring) | Every 15 minutes | Approximately 4 weeks of recent data via API. Longer history from the [Hydrology Data Explorer](https://environment.data.gov.uk/hydrology/). Check stations *River Thames at Osney Lock – Downstream* and *River Thames at Iffley Lock*. |
| **River flow / speed** | Flow speed is a major safety factor | EA flow gauges; calculated using the Dudhia formula | Hourly | Estimate speed using the Osney–Iffley level difference and an empirical relationship. Cross-check against flow gauges at Farmoor and Sutton Courtenay. Flow tends to be smoother than speed. |
| **Rainfall** | Predicts river-level changes after a delay | EA rainfall API, Oxford AOPP station, [Open-Meteo](https://open-meteo.com/) | Hourly forecasts; observations vary | Include rainfall over the previous 24–48 hours and forecast rainfall for the next 24 hours. |
| **Temperature** | Relevant to cold-water risk, but not a direct flag rule | Open-Meteo, [Met Office DataHub](https://datahub.metoffice.gov.uk/) | Hourly | Use `temperature_2m` and `apparent_temperature`. EA water-temperature readings exist at some sites but may not be quality-checked. |
| **Wind speed and maximum gust** | Affects rowing safety, particularly in exposed areas and near bridges | Open-Meteo, [Aviation Weather API](https://aviationweather.gov/data/api/) | Hourly; METAR observations roughly half-hourly | Use `wind_speed_10m` and `wind_gusts_10m`. Oxford Airport METAR (EGTK, to verify) provides observed conditions. |
| **Visibility** | Important for spinning, navigation and avoiding collisions | Open-Meteo, Met Office DataHub, METAR | Hourly | Use `visibility`. OURCs rules require clear sight lines for spinning. |
| **Lightning / thunderstorms** | Potential hard safety stop | Open-Meteo weather codes and atmospheric instability indicators; METAR | Hourly forecasts; observations roughly half-hourly | Blitzortung / LightningMaps raw data is not available for third-party apps. Use WMO weather codes `95`, `96`, `99`, CAPE, lifted index and METAR `TS` / `VCTS` as proxies. These do not reliably detect all nearby lightning. |
| **Sunrise and sunset** | Determines permitted rowing hours for novice coxes under Light Blue rules | Open-Meteo | Daily | Use daily `sunrise` and `sunset`. |
| **Term and flag-monitoring calendar** | Determines whether OURCs flags are actively monitored | [Oxford dates of term](https://www.ox.ac.uk/about/the-university/facts-and-figures/dates-of-term) | Daily | Flag is **Grey** outside full term ±1 week. See 2026–27 dates below. |
| **Current and historical OURCs flag** | Target variable for model training and prediction | [OURCs flags page](https://ourcs.co.uk/information/flags/), Status API, Dudhia's historical records | A few updates per day | Historical flag logs are the main data gap. |

## Oxford term dates, 2026–27

| Term | Start | End |
|---|---|---|
| **Michaelmas 2026** | 11 October 2026 | 5 December 2026 |
| **Hilary 2027** | 17 January 2027 | 13 March 2027 |
| **Trinity 2027** | 25 April 2027 | 19 June 2027 |

## Key considerations for the prediction model

1. **Primary predictors:** Osney and Iffley river levels, estimated river speed, and recent rainfall.
2. **Secondary predictors:** Wind, gusts, visibility, temperature and forecast weather conditions.
3. **Hard constraints:** Lightning risk, daylight restrictions and whether flags are being monitored.
4. **Training labels:** Historical OURCs flags, ideally with timestamps corresponding to the environmental observations.
5. **Main limitation:** Historical flag availability and the accuracy of derived river-speed estimates.

**Important distinction:** The river-level difference divided by distance gives an approximate water-surface slope, not physical water speed directly. The Dudhia formula requires an additional empirical relationship to estimate speed.
