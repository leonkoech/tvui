"""Current weather for the home screen and Roku screensaver (Open-Meteo: free, no API key)."""
import json
import time
import urllib.request

# WMO weather codes -> short text
_TEXT = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
         51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
         61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
         71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
         82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms",
         96: "Thunderstorms", 99: "Thunderstorms"}
_cache = {"at": 0.0, "data": None}


def _get(url):
    with urllib.request.urlopen(url, timeout=8) as r:
        return json.loads(r.read())


def locate():
    """Approximate location from the public IP, used once when none is configured."""
    j = _get("http://ip-api.com/json/?fields=status,city,lat,lon")
    if j.get("status") != "success":
        raise RuntimeError("couldn't estimate location")
    return {"lat": j["lat"], "lon": j["lon"], "place": j["city"]}


def current(where, unit="fahrenheit", max_age=600):
    if _cache["data"] and time.time() - _cache["at"] < max_age:
        return _cache["data"]
    r = _get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
             "&current=temperature_2m,weather_code,is_day&daily=temperature_2m_max,temperature_2m_min"
             "&timezone=auto&forecast_days=1&temperature_unit=%s"
             % (where["lat"], where["lon"], unit))
    j, daily = r["current"], r.get("daily", {})
    data = {"temp": round(j["temperature_2m"]), "unit": "F" if unit == "fahrenheit" else "C",
            "text": _TEXT.get(j["weather_code"], ""), "code": j["weather_code"],
            "day": bool(j.get("is_day", 1)), "place": where.get("place", ""),
            "high": round(daily["temperature_2m_max"][0]) if daily.get("temperature_2m_max") else None,
            "low": round(daily["temperature_2m_min"][0]) if daily.get("temperature_2m_min") else None}
    _cache.update(at=time.time(), data=data)
    return data
