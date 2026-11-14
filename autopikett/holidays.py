"""Die Feiertage von Balgach (SG) aus dem Feiertagskalender.ch-Export.

Bezogen wird der CSV-Export der Ortschaft Balgach (geoId 1903) mit
``klasse=3`` - das sind nur die gesetzlich anerkannten Feiertage (Klassen 1
und 2, dazu die halben Tage). Nicht anerkannte Tage wie der Berchtoldstag
(Klasse 4) sind dadurch gar nicht erst enthalten und müssen nicht mehr
ausgeblendet werden.

Der Export umfasst das vergangene, aktuelle und kommende Jahr. Er wird eine
Woche lang aus dem Benutzerordner bedient und danach neu geladen. Ohne
Internet bleibt die zuletzt gespeicherte Datei in Gebrauch.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable

from autopikett.core import USER_AGENT, user_data_dir

__all__ = [
    "CACHE_MAX_AGE_DAYS",
    "Holiday",
    "cache_path",
    "cache_saved_at",
    "fetch_holidays",
    "milliseconds_until_midnight",
    "parse_csv",
    "pick_upcoming",
    "upcoming_holidays",
]

CSV_URL = "https://fcal.ch/privat/fcal_holidays_csv.php"
# Ersatzquelle, falls der Export gesperrt ist (kein Schlüssel, keine Sperre).
NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/CH"
# Nager.Date kennt keine Gemeinden; landesweit plus Kanton St. Gallen ergibt
# für Balgach genau die gesetzlichen Feiertage.
CANTON_CODE = "CH-SG"
# Ortschaft Balgach (SG) bei feiertagskalender.ch.
GEO_ID = "1903"
# 3 = gesetzlich anerkannte Feiertage (ohne nicht anerkannte, z. B. Berchtoldstag).
KLASSE = "3"
LANGUAGE = "de"

# So weit im Voraus wird die Liste geführt.
MAX_DAYS_AHEAD = 400
# So viele Termine zeigt die Liste im Fenster.
UPCOMING_LIMIT = 8
# So lange wird der Export aus dem Benutzerordner verwendet.
CACHE_MAX_AGE_DAYS = 7
CACHE_NAME = "holidays.csv"

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


def cache_path() -> Path:
    """Ablage des zuletzt geladenen Exports im Benutzerordner."""
    return user_data_dir() / CACHE_NAME


def cache_saved_at(cache: Path | None = None) -> datetime | None:
    """Wann die gespeicherte CSV geschrieben wurde, also der letzte Download."""
    target = Path(cache) if cache is not None else cache_path()
    try:
        return datetime.fromtimestamp(target.stat().st_mtime)
    except OSError:
        return None


def _parse_csv_date(value: str) -> date | None:
    parts = str(value or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    except ValueError:
        return None


def is_export(text: str) -> bool:
    """Sieht der Text wie der CSV-Export aus (und nicht wie eine Fehlermeldung)?"""
    lines = text.lstrip("\ufeff").splitlines()
    return bool(lines) and "Datum" in lines[0]


def parse_csv(text: str) -> list[Holiday]:
    """Export in eine sortierte, eindeutige Feiertagsliste wandeln.

    Die Spalten sind ``Datum;Bezeichnung;Wochentag;Kalenderwoche`` mit
    Datumsangaben als ``TT.MM.JJJJ``. Kopfzeile und kaputte Zeilen fallen weg.
    """
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=";")

    found: dict[tuple[date, str], Holiday] = {}
    for row in reader:
        if len(row) < 2:
            continue
        day = _parse_csv_date(row[0])
        name = str(row[1]).strip()
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


def _in_window(holidays: Iterable[Holiday], today: date, days: int) -> list[Holiday]:
    last = today + timedelta(days=days)
    return [holiday for holiday in holidays if today <= holiday.day <= last]


def _read_cache(target: Path) -> str | None:
    try:
        return target.read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as error:
        if isinstance(error, FileNotFoundError):
            return None
        logger.warning("Feiertags-Datei %s nicht lesbar: %s", target, error)
        return None


def _cache_is_fresh(target: Path, now: datetime) -> bool:
    try:
        changed = datetime.fromtimestamp(target.stat().st_mtime)
    except OSError:
        return False
    return now - changed < timedelta(days=CACHE_MAX_AGE_DAYS)


def _write_cache(target: Path, text: str) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
    except OSError as error:
        logger.warning("Feiertags-Datei %s nicht schreibbar: %s", target, error)


def _download(timeout: float) -> str | None:
    query = urllib.parse.urlencode(
        {"hl": LANGUAGE, "klasse": KLASSE, "geo": GEO_ID}
    )
    request = urllib.request.Request(
        f"{CSV_URL}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8-sig", "replace")
    except (urllib.error.URLError, OSError) as error:
        logger.warning("Feiertage konnten nicht geladen werden: %s", error)
        return None

    if not is_export(text):
        logger.warning("Feiertags-Export nicht erhalten: %s", text.strip()[:120])
        return None
    return text


def _to_csv(rows: Iterable[tuple[date, str]]) -> str:
    """Feiertage im Format des Exports schreiben, damit der Parser gilt."""
    lines = ['"Datum";"Bezeichnung";"Wochentag";"Kalenderwoche"']
    for day, name in sorted(set(rows)):
        short = WEEKDAY_SHORT[day.weekday()]
        week = day.isocalendar()[1]
        lines.append(
            f'"{day:%d.%m.%Y}";"{name}";"{short}.";"Woche {week:02d}"'
        )
    return "\r\n".join(lines) + "\r\n"


def _download_nager(years: Iterable[int], timeout: float) -> str | None:
    """Ersatzquelle: die Schweizer Feiertage von Nager.Date als CSV."""
    rows: list[tuple[date, str]] = []
    for year in years:
        request = urllib.request.Request(
            NAGER_URL.format(year=year),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as error:
            logger.warning("Feiertage auch bei Nager.Date nicht erhältlich: %s", error)
            return None

        for item in data if isinstance(data, list) else []:
            counties = item.get("counties")
            if counties is not None and CANTON_CODE not in counties:
                continue
            if "Public" not in (item.get("types") or []):
                continue
            name = str(item.get("localName") or item.get("name") or "").strip()
            try:
                day = date.fromisoformat(str(item.get("date")))
            except ValueError:
                continue
            if name:
                rows.append((day, name))

    return _to_csv(rows) if rows else None


def fetch_holidays(
    today: date | None = None,
    days: int = MAX_DAYS_AHEAD,
    timeout: float = 15.0,
    force: bool = False,
    cache: Path | None = None,
    now: datetime | None = None,
) -> list[Holiday] | None:
    """Feiertage ab heute laden; ``None`` ohne Verbindung und ohne Speicher.

    Der Export wird höchstens einmal pro Woche geholt (``force`` erzwingt
    einen neuen Abruf). Schlägt der Abruf fehl, gelten die gespeicherten Daten.
    """
    today = today or date.today()
    now = now or datetime.now()
    target = Path(cache) if cache is not None else cache_path()

    stored = _read_cache(target)
    if stored is not None and is_export(stored):
        if not force and _cache_is_fresh(target, now):
            return _in_window(parse_csv(stored), today, days)
    else:
        stored = None

    loaded = _download(timeout)
    if loaded is None:
        # Gesperrt (zu viele Downloads, Bot-Schutz) oder offline: die zweite,
        # schlüsselfreie Quelle versuchen, bevor die alte Datei gilt.
        loaded = _download_nager((now.year, now.year + 1), timeout)
    if loaded is not None:
        _write_cache(target, loaded)
        return _in_window(parse_csv(loaded), today, days)

    if stored is not None:
        logger.info("Feiertage aus der gespeicherten Datei geladen.")
        return _in_window(parse_csv(stored), today, days)
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
    """Die nächsten Feiertage von Balgach (``None`` ohne Netz und Speicher)."""
    today = today or date.today()
    loaded = fetch(today=today)
    if loaded is None:
        return None
    return pick_upcoming(loaded, today=today, limit=limit)
