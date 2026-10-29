from __future__ import annotations

import webbrowser
from urllib.parse import quote_plus


def weather_action(
    parameters: dict,
    player=None,
) -> str:
    city     = parameters.get("city")
    when     = parameters.get("time", "today")  

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    summary = _fetch_weather(city, when)
    if summary:
        _log(summary, player)
        return summary

    search_query  = f"weather in {city} {when}"
    url           = f"https://www.google.com/search?q={quote_plus(search_query)}"

    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't open the browser for the weather report: {e}"
        _log(msg, player)
        return msg

    msg = f"Showing the weather for {city}, {when}, sir."
    _log(msg, player)

    return msg


def _fetch_weather(city: str, when: str) -> str | None:
    """Real conditions from wttr.in (no key). None on any failure so the caller
    falls back to opening a browser search."""
    try:
        import requests
        r = requests.get(f"https://wttr.in/{quote_plus(city)}",
                         params={"format": "j1"}, timeout=8,
                         headers={"User-Agent": "aether"})
        r.raise_for_status()
        data = r.json()
        w = (when or "today").lower()
        if w in ("tomorrow", "day after tomorrow") and len(data.get("weather", [])) > 1:
            day = data["weather"][1 if w == "tomorrow" else min(2, len(data["weather"]) - 1)]
            desc = (day.get("hourly") or [{}])[4].get("weatherDesc", [{}])[0].get("value", "").strip()
            return (f"{w.capitalize()} in {city}: {desc.lower()}, between "
                    f"{day['mintempC']} and {day['maxtempC']} degrees Celsius, sir.")
        cur = data["current_condition"][0]
        desc = cur["weatherDesc"][0]["value"].strip().lower()
        return (f"It is {cur['temp_C']} degrees Celsius in {city}, {desc}, feels like "
                f"{cur['FeelsLikeC']}, humidity {cur['humidity']} percent, sir.")
    except Exception as e:
        print(f"[Weather] wttr.in lookup failed: {e}")
        return None


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"Aether: {message}")
        except Exception:
            pass