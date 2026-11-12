"""Autostart unter Windows (HKCU\\...\\Run) - ohne Admin-Rechte.

Der Eintrag startet die installierte EXE mit ``--tray``, damit AutoPikett
beim Login direkt im Systray landet und kein Fenster aufpoppt.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("autopikett.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AutoPikett"
TRAY_ARGUMENT = "--tray"


def is_supported() -> bool:
    return sys.platform.startswith("win")


def _exe_path() -> Path | None:
    """Pfad der laufenden EXE - nur im PyInstaller-Build sinnvoll."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None


def autostart_command() -> str | None:
    """Befehl für den Autostart oder ``None`` ohne installierte EXE."""
    exe = _exe_path()
    if exe is None:
        return None
    return f'"{exe}" {TRAY_ARGUMENT}'


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    except OSError as error:
        logger.warning("Autostart-Status nicht lesbar: %s", error)
        return False
    return True


def enable() -> bool:
    command = autostart_command()
    if command is None:
        logger.debug("Kein Autostart: AutoPikett läuft nicht als EXE.")
        return False

    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    except OSError as error:
        logger.error("Autostart konnte nicht gesetzt werden: %s", error)
        return False

    logger.info("Autostart gesetzt: %s", command)
    return True


def disable() -> bool:
    if not is_supported():
        return False

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True
    except OSError as error:
        logger.error("Autostart konnte nicht entfernt werden: %s", error)
        return False

    logger.info("Autostart entfernt.")
    return True


def apply(enabled: bool) -> bool:
    """Autostart passend zur Einstellung setzen oder entfernen."""
    if not is_supported():
        return False
    return enable() if enabled else disable()
