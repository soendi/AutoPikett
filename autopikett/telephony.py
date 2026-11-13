"""Sicherstellen, dass ``enterprise_telephony.exe`` läuft.

Ohne das Swisscom-Fenster kann AutoPikett nicht wählen. Darum wird der Prozess
regelmässig geprüft und bei Bedarf gestartet. Der Pfad zur EXE wird zuerst an
der bekannten Stelle gesucht, sonst auf der Festplatte; was gefunden wurde,
merkt sich die App in den Einstellungen.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

__all__ = [
    "TelephonyStatus",
    "ensure_running",
    "find_executable",
    "is_running",
    "tasklist_reports_process",
]

PROCESS_NAME = "enterprise_telephony.exe"

# Erste Adresse, die geprüft wird.
KNOWN_PATH = Path(r"C:\Program Files (x86)\Swisscom\Enterprise Telephony\enterprise_telephony.exe")

# Ganze Ordner, die beim Suchen nicht durchlaufen werden.
SKIP_DIR_NAMES = {
    "windows",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "config.msi",
}

SEARCH_TIMEOUT_S = 300.0
SEARCH_MAX_DEPTH = 8

logger = logging.getLogger("autopikett.telephony")


@dataclass(frozen=True)
class TelephonyStatus:
    """Ergebnis einer Prüfung bzw. eines Startversuchs."""

    running: bool
    started: bool = False
    path: str = ""
    message: str = ""


def tasklist_reports_process(output: str, name: str = PROCESS_NAME) -> bool:
    """Steckt der Prozess in der Ausgabe von ``tasklist``?"""
    return name.lower() in (output or "").lower()


def is_running(name: str = PROCESS_NAME, timeout: float = 15.0) -> bool:
    """Läuft der Prozess gerade? (nur unter Windows möglich)"""
    if sys.platform != "win32":
        return False

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("tasklist fehlgeschlagen: %s", error)
        return False
    return tasklist_reports_process(result.stdout, name)


def drive_roots() -> list[Path]:
    """Alle vorhandenen Laufwerkswurzeln (unter Windows C:\\, D:\\ ...)."""
    if sys.platform != "win32":
        return []
    return [
        Path(f"{letter}:\\")
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB"
        if os.path.exists(f"{letter}:\\")
    ]


def iter_files(
    root: Path, max_depth: int = SEARCH_MAX_DEPTH
) -> Iterator[Path]:
    """Alle Dateien unter ``root``, ohne die System-Ordner."""
    base_depth = len(root.parts)
    for current, dirs, files in os.walk(root, onerror=lambda _: None):
        depth = len(Path(current).parts) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [name for name in dirs if name.lower() not in SKIP_DIR_NAMES]
        for name in files:
            yield Path(current) / name


def search_disk(
    name: str = PROCESS_NAME,
    roots: list[Path] | None = None,
    timeout: float = SEARCH_TIMEOUT_S,
) -> Path | None:
    """EXE auf der Festplatte suchen (mit Zeitlimit)."""
    deadline = time.monotonic() + timeout
    wanted = name.lower()
    for root in roots if roots is not None else drive_roots():
        logger.info("Suche %s unter %s …", name, root)
        for path in iter_files(root):
            if time.monotonic() > deadline:
                logger.warning("Suche nach %s nach %.0f s abgebrochen.", name, timeout)
                return None
            if path.name.lower() == wanted:
                return path
    return None


def find_executable(
    saved_path: str | None = None,
    name: str = PROCESS_NAME,
    timeout: float = SEARCH_TIMEOUT_S,
) -> Path | None:
    """Gemerkter Pfad, dann die bekannte Stelle, zuletzt die ganze Festplatte."""
    for candidate in (saved_path, str(KNOWN_PATH)):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path

    logger.info("%s nicht am üblichen Ort - durchsuche die Festplatte.", name)
    return search_disk(name=name, timeout=timeout)


def _start_process(exe: Path) -> None:
    subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def ensure_running(
    saved_path: str | None = None,
    *,
    name: str = PROCESS_NAME,
    running: Callable[[str], bool] = is_running,
    find: Callable[[str | None], Path | None] = find_executable,
    start: Callable[[Path], None] = _start_process,
    wait_s: float = 25.0,
    poll_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> TelephonyStatus:
    """Prüft den Prozess und startet ihn bei Bedarf.

    Returns:
        Status mit gefundenem Pfad; ``path`` gehört in die Einstellungen,
        damit beim nächsten Mal nicht mehr gesucht werden muss.
    """
    if running(name):
        return TelephonyStatus(True, path=saved_path or "", message="läuft bereits")

    exe = find(saved_path)
    if exe is None:
        return TelephonyStatus(
            False,
            message=f"{name} wurde nicht gefunden.",
        )

    logger.info("%s läuft nicht - starte %s", name, exe)
    try:
        start(exe)
    except OSError as error:
        logger.error("%s konnte nicht gestartet werden: %s", name, error)
        return TelephonyStatus(
            False, path=str(exe), message=f"Start fehlgeschlagen: {error}"
        )

    waited = 0.0
    while waited < wait_s:
        sleep(poll_s)
        waited += poll_s
        if running(name):
            return TelephonyStatus(
                True, started=True, path=str(exe), message="gestartet"
            )

    return TelephonyStatus(
        False,
        started=True,
        path=str(exe),
        message="gestartet, aber noch nicht bereit",
    )
