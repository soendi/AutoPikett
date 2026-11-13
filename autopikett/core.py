"""Kernlogik von AutoPikett.

Enthält das Zeitplanmodell, die Speicherung im Benutzerordner, die
Berechnung der fälligen Umschaltungen und die Update-Prüfung gegen die
GitHub-Releases. Bewusst frei von Windows-, GUI- und Anruf-Abhängigkeiten,
damit dieser Teil überall lauffähig und testbar bleibt.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "ACTION_LABELS",
    "ACTION_OFF",
    "ACTION_ON",
    "APP_NAME",
    "APP_VERSION",
    "DayPlan",
    "GITHUB_REPO",
    "HISTORY_LIMIT",
    "HISTORY_MAX_AGE_DAYS",
    "HistoryEntry",
    "history_cutoff",
    "holiday_weekdays",
    "prune_history",
    "HITS_PER_TRIGGER",
    "Settings",
    "SLOTS_PER_DAY",
    "Slot",
    "TRAY_COLOR_OFF",
    "TRAY_COLOR_ON",
    "UPDATE_INTERVAL_S",
    "WAIT_BETWEEN_TRIES_S",
    "WEEKDAYS",
    "available_update",
    "clear_history",
    "default_settings",
    "download_file",
    "due_slots",
    "format_timestamp",
    "history_path",
    "load_history",
    "record_history",
    "fetch_latest_release",
    "installer_asset_url",
    "job_key",
    "load_settings",
    "next_event",
    "normalize_time",
    "parse_version",
    "pending_jobs",
    "save_settings",
    "settings_path",
    "state_label",
    "user_data_dir",
]

APP_NAME = "AutoPikett"
APP_VERSION = "1.0.10"

# Quelle der Updates (GitHub-Releases dieses Repositories).
GITHUB_REPO = "soendi/AutoPikett"

WEEKDAYS = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)

ACTION_ON = "on"
ACTION_OFF = "off"
ACTION_LABELS = {ACTION_ON: "Ein", ACTION_OFF: "Aus"}

# Vier Umschaltungen pro Tag; Montag bis Freitag sind voreingestellt aktiv.
SLOTS_PER_DAY = 4
DEFAULT_ENABLED_DAYS = WEEKDAYS[:5]
DEFAULT_TIMES = ("08:00", "12:00", "13:30", "17:00")
DEFAULT_ACTIONS = (ACTION_OFF, ACTION_ON, ACTION_OFF, ACTION_ON)

# Die echten Pikett-Nummern. Steuercodes wie * und # gehören zur Nummer
# und werden unverändert gewählt.
DEFAULT_ON_NUMBER = "*21+41860812851990"
DEFAULT_OFF_NUMBER = "#21"
# Frühere Platzhalter, die in gespeicherten Einstellungen stecken können.
LEGACY_PLACEHOLDER_NUMBERS = {"079 000 00 01", "079 000 00 02"}

# Jede Umschaltung wird dreimal gewählt: wählen, 10 s warten, wählen,
# 10 s warten, wählen.
HITS_PER_TRIGGER = 3
WAIT_BETWEEN_TRIES_S = 10

# Prüfintervall für neue Releases.
UPDATE_INTERVAL_S = 6 * 60 * 60

# Status-Tabelle: ein Eintrag pro Auftrag, so viele werden aufbewahrt.
HISTORY_LIMIT = 200
# Einträge, die älter sind, werden automatisch gelöscht.
HISTORY_MAX_AGE_DAYS = 14

# Farben des Systray-Punkts.
TRAY_COLOR_ON = "#2ecc71"
TRAY_COLOR_OFF = "#e74c3c"

TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:.]\s*(\d{1,2})\s*$")
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

logger = logging.getLogger(__name__)


# ============================================================
# ZEITPLAN
# ============================================================


def normalize_time(value: Any) -> str:
    """Wandelt '8:00', '08.00' usw. in 'HH:MM' um.

    Raises:
        ValueError: wenn der Text keine gültige Uhrzeit ist.
    """
    match = TIME_RE.match(str(value or ""))
    if not match:
        raise ValueError(f"Ungültige Uhrzeit: {value!r} (erwartet HH:MM)")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Ungültige Uhrzeit: {value!r} (erwartet HH:MM)")
    return f"{hour:02d}:{minute:02d}"


def time_of_day(value: str) -> time:
    normalized = normalize_time(value)
    hour, minute = normalized.split(":")
    return time(hour=int(hour), minute=int(minute))


@dataclass
class Slot:
    """Eine Umschaltung: Uhrzeit und Ziel (Pikett ein oder aus)."""

    time: str = DEFAULT_TIMES[0]
    action: str = ACTION_OFF

    def to_dict(self) -> dict[str, str]:
        return {"time": self.time, "action": self.action}

    @classmethod
    def from_dict(cls, data: Any, fallback: "Slot") -> "Slot":
        if not isinstance(data, dict):
            return cls(fallback.time, fallback.action)
        try:
            slot_time = normalize_time(data.get("time", fallback.time))
        except ValueError:
            slot_time = fallback.time
        action = data.get("action", fallback.action)
        if action not in (ACTION_ON, ACTION_OFF):
            action = fallback.action
        return cls(slot_time, action)


@dataclass
class DayPlan:
    """Ein Wochentag mit Schalter und vier Umschaltungen."""

    enabled: bool = False
    slots: list[Slot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "slots": [slot.to_dict() for slot in self.slots],
        }

    @classmethod
    def from_dict(cls, data: Any, fallback: "DayPlan") -> "DayPlan":
        if not isinstance(data, dict):
            return cls(
                enabled=fallback.enabled,
                slots=[Slot(slot.time, slot.action) for slot in fallback.slots],
            )

        raw_slots = data.get("slots")
        if not isinstance(raw_slots, list):
            raw_slots = []

        slots = []
        for index, default_slot in enumerate(fallback.slots):
            raw = raw_slots[index] if index < len(raw_slots) else None
            slots.append(Slot.from_dict(raw, default_slot))

        return cls(enabled=bool(data.get("enabled", fallback.enabled)), slots=slots)


def default_days() -> dict[str, DayPlan]:
    return {
        day: DayPlan(
            enabled=day in DEFAULT_ENABLED_DAYS,
            slots=[
                Slot(slot_time, action)
                for slot_time, action in zip(DEFAULT_TIMES, DEFAULT_ACTIONS)
            ],
        )
        for day in WEEKDAYS
    }


@dataclass
class Settings:
    """Der komplette Zustand der App (wird als JSON gespeichert)."""

    on_number: str = DEFAULT_ON_NUMBER
    off_number: str = DEFAULT_OFF_NUMBER
    days: dict[str, DayPlan] = field(default_factory=default_days)
    pikett_on: bool = False
    auto_update: bool = True
    autostart: bool = True
    # Gemerkter Pfad zu enterprise_telephony.exe (leer = noch nicht gefunden).
    telephony_path: str = ""
    last_runs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "on_number": self.on_number,
            "off_number": self.off_number,
            "pikett_on": self.pikett_on,
            "auto_update": self.auto_update,
            "autostart": self.autostart,
            "telephony_path": self.telephony_path,
            "last_runs": dict(self.last_runs),
            "days": {day: plan.to_dict() for day, plan in self.days.items()},
        }


def default_settings() -> Settings:
    return Settings()


def _clean_number(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = value.strip()
    # Alte Platzhalter gelten als "noch nicht eingetragen".
    if cleaned in LEGACY_PLACEHOLDER_NUMBERS:
        return fallback
    return cleaned or fallback


def _clean_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_runs(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    runs: dict[str, str] = {}
    for key, moment in value.items():
        if isinstance(key, str) and isinstance(moment, str):
            runs[key] = moment
    return runs


def settings_from_dict(data: Any) -> Settings:
    """Baut Einstellungen aus JSON-Daten und füllt Fehlendes mit Vorgaben."""
    defaults = default_days()
    if not isinstance(data, dict):
        return Settings()

    raw_days = data.get("days")
    days: dict[str, DayPlan] = {}
    for day in WEEKDAYS:
        raw = raw_days.get(day) if isinstance(raw_days, dict) else None
        days[day] = DayPlan.from_dict(raw, defaults[day])

    return Settings(
        on_number=_clean_number(data.get("on_number"), DEFAULT_ON_NUMBER),
        off_number=_clean_number(data.get("off_number"), DEFAULT_OFF_NUMBER),
        days=days,
        pikett_on=bool(data.get("pikett_on", False)),
        auto_update=bool(data.get("auto_update", True)),
        autostart=bool(data.get("autostart", True)),
        telephony_path=_clean_path(data.get("telephony_path")),
        last_runs=_clean_runs(data.get("last_runs")),
    )


# ============================================================
# SPEICHERORT IM BENUTZERORDNER
# ============================================================


def user_data_dir() -> Path:
    """Datenordner des angemeldeten Benutzers (Windows: %APPDATA%)."""
    base = os.environ.get("APPDATA")
    if not base:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    folder = Path(base) / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    """Lädt die Einstellungen; bei Fehlern gelten die Vorgaben."""
    target = Path(path) if path is not None else settings_path()
    if not target.exists():
        return default_settings()

    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        logger.warning("Einstellungen %s nicht lesbar: %s", target, error)
        return default_settings()

    return settings_from_dict(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Schreibt die Einstellungen atomar in den Benutzerordner."""
    target = Path(path) if path is not None else settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temporary.replace(target)
    return target


# ============================================================
# FÄLLIGE UMSCHALTUNGEN
# ============================================================


def plan_for_day(settings: Settings, day_index: int) -> DayPlan | None:
    if not 0 <= day_index < len(WEEKDAYS):
        return None
    return settings.days.get(WEEKDAYS[day_index])


def due_slots(settings: Settings, now: datetime) -> list[Slot]:
    """Alle Umschaltungen, die in der aktuellen Minute fällig sind."""
    plan = plan_for_day(settings, now.weekday())
    if plan is None or not plan.enabled:
        return []

    due = []
    for slot in plan.slots:
        moment = time_of_day(slot.time)
        if now.hour == moment.hour and now.minute == moment.minute:
            due.append(slot)
    return due


def job_key(now: datetime, slot: Slot) -> str:
    return f"{now:%Y-%m-%d}|{slot.time}|{slot.action}"


def pending_jobs(
    settings: Settings,
    now: datetime,
    skip_days: Collection[date] = (),
) -> list[tuple[str, str]]:
    """Fällige, noch nicht ausgeführte Aufträge als (Schlüssel, Aktion).

    Alte Einträge aus früheren Tagen werden dabei aus ``last_runs`` entfernt.
    ``skip_days`` sind Tage (z. B. Feiertage), an denen nichts geschaltet wird.
    """
    today = now.strftime("%Y-%m-%d")
    settings.last_runs = {
        key: moment
        for key, moment in settings.last_runs.items()
        if key.startswith(today)
    }

    if now.date() in skip_days:
        return []

    jobs: list[tuple[str, str]] = []
    for slot in due_slots(settings, now):
        key = job_key(now, slot)
        if key in settings.last_runs:
            continue
        jobs.append((key, slot.action))
    return jobs


def next_event(
    settings: Settings,
    now: datetime,
    skip_days: Collection[date] = (),
) -> tuple[datetime, str, str] | None:
    """Nächster geplanter Wechsel als (Zeitpunkt, Aktion, Wochentag).

    ``skip_days`` (Feiertage) werden übersprungen.
    """
    for offset in range(16):
        day: date = (now + timedelta(days=offset)).date()
        if day in skip_days:
            continue
        plan = plan_for_day(settings, day.weekday())
        if plan is None or not plan.enabled:
            continue

        candidates = []
        for slot in plan.slots:
            moment = datetime.combine(day, time_of_day(slot.time))
            if moment > now:
                candidates.append((moment, slot.action))

        if candidates:
            moment, action = min(candidates, key=lambda item: item[0])
            return moment, action, WEEKDAYS[day.weekday()]
    return None


def state_label(pikett_on: bool) -> str:
    return "Pikett EIN" if pikett_on else "Pikett AUS"


def holiday_weekdays(days: Collection[date], today: date | None = None) -> set[str]:
    """Wochentage, deren nächster Termin ein Feiertag ist.

    Geprüft wird von heute bis in sechs Tagen - so kommt jeder Wochentag der
    angezeigten Woche genau einmal vor.
    """
    today = today or date.today()
    last = today + timedelta(days=6)
    return {WEEKDAYS[day.weekday()] for day in days if today <= day <= last}


# ============================================================
# STATUS-TABELLE (ein Eintrag pro Auftrag)
# ============================================================


@dataclass
class HistoryEntry:
    """Ergebnis eines Auftrags - bewusst einer, nicht ein Eintrag pro Anruf."""

    timestamp: str
    action: str
    number: str
    source: str = ""
    ok: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "number": self.number,
            "source": self.source,
            "ok": self.ok,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "HistoryEntry | None":
        if not isinstance(data, dict):
            return None
        timestamp = data.get("timestamp")
        action = data.get("action")
        if not isinstance(timestamp, str) or action not in (ACTION_ON, ACTION_OFF):
            return None
        return cls(
            timestamp=timestamp,
            action=action,
            number=str(data.get("number") or ""),
            source=str(data.get("source") or ""),
            ok=bool(data.get("ok", False)),
            detail=str(data.get("detail") or ""),
        )

    def result_label(self) -> str:
        return "erfolgreich" if self.ok else "fehlgeschlagen"

    def action_label(self) -> str:
        return f"Pikett {ACTION_LABELS.get(self.action, self.action)}"


def format_timestamp(timestamp: str) -> str:
    """ISO-Zeitstempel als 'TT.MM.JJJJ HH:MM:SS' für die Tabelle."""
    try:
        return datetime.fromisoformat(str(timestamp)).strftime("%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError):
        return str(timestamp)


def history_path() -> Path:
    return user_data_dir() / "history.json"


def history_cutoff(now: datetime | None = None) -> datetime:
    """Ältester Zeitpunkt, der noch aufbewahrt wird (zwei Wochen)."""
    return (now or datetime.now()) - timedelta(days=HISTORY_MAX_AGE_DAYS)


def _is_recent(entry: HistoryEntry, cutoff: datetime) -> bool:
    """Eintrag jünger als die Aufbewahrungsfrist?"""
    try:
        moment = datetime.fromisoformat(entry.timestamp)
    except (TypeError, ValueError):
        # Ohne lesbaren Zeitstempel lässt sich das Alter nicht prüfen.
        return False
    return moment >= cutoff


def load_history(
    path: Path | None = None, limit: int = HISTORY_LIMIT, now: datetime | None = None
) -> list[HistoryEntry]:
    """Gespeicherte Status-Einträge, neueste zuerst.

    Einträge, die älter als ``HISTORY_MAX_AGE_DAYS`` Tage sind, werden
    nicht mehr zurückgegeben.
    """
    target = Path(path) if path is not None else history_path()
    if not target.exists():
        return []

    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        logger.warning("Status-Datei %s nicht lesbar: %s", target, error)
        return []

    if isinstance(data, dict):
        data = data.get("entries")
    if not isinstance(data, list):
        return []

    cutoff = history_cutoff(now)
    entries = [HistoryEntry.from_dict(item) for item in data]
    return [
        entry
        for entry in entries
        if entry is not None and _is_recent(entry, cutoff)
    ][:limit]


def clear_history(path: Path | None = None) -> None:
    """Löscht alle gespeicherten Status-Einträge."""
    target = Path(path) if path is not None else history_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return


def _write_history(target: Path, entries: list[HistoryEntry]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(
            {"entries": [item.to_dict() for item in entries]},
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")
    temporary.replace(target)


def record_history(
    entry: HistoryEntry,
    path: Path | None = None,
    limit: int = HISTORY_LIMIT,
    now: datetime | None = None,
) -> list[HistoryEntry]:
    """Hängt einen Eintrag an (neueste zuerst) und speichert die Liste.

    Zu alte Einträge fallen dabei weg.
    """
    target = Path(path) if path is not None else history_path()
    entries = ([entry] + load_history(target, limit=limit, now=now))[:limit]
    _write_history(target, entries)
    return entries


def prune_history(
    path: Path | None = None,
    limit: int = HISTORY_LIMIT,
    now: datetime | None = None,
) -> list[HistoryEntry]:
    """Löscht Einträge älter als zwei Wochen und schreibt die Datei neu."""
    target = Path(path) if path is not None else history_path()
    entries = load_history(target, limit=limit, now=now)

    if not target.exists():
        return entries

    try:
        _write_history(target, entries)
    except OSError as error:
        logger.warning("Status-Datei konnte nicht bereinigt werden: %s", error)
    return entries


# ============================================================
# UPDATE-PRÜFUNG (GitHub-Releases)
# ============================================================


def parse_version(text: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", text or "")[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def fetch_latest_release(
    repo: str = GITHUB_REPO, timeout: float = 10.0
) -> dict[str, Any] | None:
    """Holt das neueste Release; ``None`` bei fehlender Verbindung."""
    if not repo:
        return None

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as error:
        logger.warning("Update-Prüfung fehlgeschlagen: %s", error)
        return None


def installer_asset_url(release: Any, version: str) -> str | None:
    """URL des Installers ``AutoPikett-Setup-<version>.exe`` im Release."""
    if not isinstance(release, dict):
        return None

    assets = release.get("assets") or []
    expected = f"AutoPikett-Setup-{version}.exe"

    for asset in assets:
        if asset.get("name") == expected:
            return asset.get("browser_download_url")

    for asset in assets:
        name = str(asset.get("name") or "")
        if name.startswith("AutoPikett-Setup-") and name.endswith(".exe"):
            return asset.get("browser_download_url")
    return None


def available_update(
    current: str = APP_VERSION, repo: str = GITHUB_REPO
) -> tuple[str, str] | None:
    """Neuere Version als (Version, Download-URL) oder ``None``."""
    release = fetch_latest_release(repo)
    if not release:
        return None

    tag = str(release.get("tag_name") or "").strip().lstrip("vV").strip()
    if not tag or parse_version(tag) <= parse_version(current):
        return None

    url = installer_asset_url(release, tag)
    if not url:
        logger.warning("Release %s enthält keinen passenden Installer.", tag)
        return None
    return tag, url


def download_file(url: str, target: Path, timeout: float = 120.0) -> Path:
    """Lädt eine Datei nach ``target`` und gibt den Pfad zurück."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(target, "wb") as handle:
            shutil.copyfileobj(response, handle)
    return target
