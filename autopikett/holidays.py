"""Kommende Feiertage des Kantons St. Gallen (OpenHolidays API).

Die Liste wird bei jedem Start und danach periodisch live vom Open-Data-Dienst
``openholidaysapi.org`` geladen. Ohne Internet bleibt die App nutzbar; die
Abfrage liefert dann ``None``.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable

from autopikett.core import USER_AGENT

__all__ = [
    "Holiday",
    "fetch_holidays",
    "milliseconds_until_midnight",
    "parse_holidays",
    "pick_upcoming",
    "upcoming_holidays",
]

API_URL = "https://openholidaysapi.org/PublicHolidays"
COUNTRY = "CH"
SUBDIVISION = "CH-SG"
LANGUAGE = "DE"

# So weit im Voraus wird abgefragt (die API erlaubt maximal drei Jahre).
MAX_DAYS_AHEAD = 400
# So viele Termine zeigt die Liste im Fenster.
UPCOMING_LIMIT = 6

WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

logger = logging.getLogger("autopikett.holidays")


@dataclass(frozen=True)
class Holiday:
    """Ein Feiertag mit lokalem Namen."""

    day: date
    name: str

    def label(self) -> str:
        """Zeile für die Liste, z. B. ``Fr 25.12.2026  Weihnachten``."""
        weekday = WEEKDAY_SHORT[self.day.weekday()]
        return f"{weekday} {self.day:%d.%m.%Y}  {self.name}"


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _holiday_name(value: Any) -> str:
    """Name aus der OpenHolidays-Antwort (Liste mit Sprachvarianten)."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, Iterable):
        return ""

    entries = [item for item in value if isinstance(item, dict)]
    for entry in entries:
        if str(entry.get("language") or "").upper() == LANGUAGE:
            text = str(entry.get("text") or "").strip()
            if text:
                return text
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if text:
            return text
    return ""


def parse_holidays(raw: Any) -> list[Holiday]:
    """Antwort der API in eine sortierte, eindeutige Feiertagsliste wandeln."""
    if not isinstance(raw, list):
        return []

    found: dict[tuple[date, str], Holiday] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        day = _parse_date(item.get("startDate"))
        name = _holiday_name(item.get("name"))
        if day is None or not name:
            continue
        found[(day, name)] = Holiday(day, name)

    return sorted(found.values(), key=lambda holiday: (holiday.day, holiday.name))


def pick_upcoming(
    holidays: Iterable[Holiday],
    today: date | None = None,
    limit: int = UPCOMING_LIMIT,
) -> list[Holiday]:
    """Nur heutige und künftige Feiertage, höchstens ``limit`` Stück."""
    today = today or date.today()
    upcoming = sorted(
        (holiday for holiday in holidays if holiday.day >= today),
        key=lambda holiday: (holiday.day, holiday.name),
    )
    return upcoming[:limit] if limit > 0 else upcoming


def fetch_holidays(
    today: date | None = None,
    days: int = MAX_DAYS_AHEAD,
    timeout: float = 10.0,
) -> list[Holiday] | None:
    """Feiertage ab heute laden; ``None`` ohne Verbindung oder bei Fehlern."""
    today = today or date.today()
    query = urllib.parse.urlencode(
        {
            "countryIsoCode": COUNTRY,
            "subdivisionCode": SUBDIVISION,
            "languageIsoCode": LANGUAGE,
            "validFrom": today.isoformat(),
            "validTo": (today + timedelta(days=days)).isoformat(),
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "text/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_holidays(json.loads(response.read().decode("utf-8")))
    except (urllib.error.URLError, ValueError, OSError) as error:
        logger.warning("Feiertage konnten nicht geladen werden: %s", error)
        return None


def milliseconds_until_midnight(
    now: datetime | None = None, margin_s: int = 90
) -> int:
    """Millisekunden bis kurz nach Mitternacht (für den Tageswechsel)."""
    now = now or datetime.now()
    tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min)
    delay = (tomorrow - now).total_seconds() + margin_s
    return max(1, int(delay * 1000))


def upcoming_holidays(
    today: date | None = None,
    limit: int = UPCOMING_LIMIT,
    fetch: Callable[..., list[Holiday] | None] = fetch_holidays,
) -> list[Holiday] | None:
    """Die nächsten Feiertage des Kantons St. Gallen (``None`` ohne Netz)."""
    today = today or date.today()
    loaded = fetch(today=today)
    if loaded is None:
        return None
    return pick_upcoming(loaded, today=today, limit=limit)
