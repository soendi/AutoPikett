"""Anrufe über das Swisscom-Fenster "Enterprise Telephony" auslösen.

Der Ablauf ist von soendi/telefonerkennung übernommen: Nummer ins Wählfeld
(AutomationId ``DialNumberTextBox``) schreiben und die Anruf-Taste
(AutomationId ``CallButton``) betätigen. Jede Umschaltung wird dreimal
gewählt, damit das Pikett sicher umgeschaltet ist.
"""

from __future__ import annotations

import logging
import re
import time

from autopikett.core import (
    ACTION_LABELS,
    ACTION_OFF,
    ACTION_ON,
    HITS_PER_TRIGGER,
    WAIT_BETWEEN_TRIES_S,
)

__all__ = ["DialError", "dial_number", "switch_pikett", "to_dial_number"]

WINDOW_TITLE = "Enterprise Telephony"
DIAL_FIELD_ID = "DialNumberTextBox"
CALL_BUTTON_ID = "CallButton"

logger = logging.getLogger("autopikett.dialer")


class DialError(RuntimeError):
    """Der Anruf konnte nicht gestartet werden."""


# Zeichen, die im Wählfeld als Steuercode zählen und deshalb erhalten bleiben.
KEEP_CHARS_RE = re.compile(r"[^\d*#+]")

# pywinauto deutet diese Zeichen in type_keys als Sondertasten.
TYPE_KEYS_SPECIAL = {
    "+": "{+}",
    "^": "{^}",
    "%": "{%}",
    "~": "{~}",
    "(": "{(}",
    ")": "{)}",
    "[": "{[}",
    "]": "{]}",
}


def _for_type_keys(text: str) -> str:
    """Text so maskieren, dass ``type_keys`` jedes Zeichen wörtlich tippt."""
    return "".join(TYPE_KEYS_SPECIAL.get(char, char) for char in text)


def to_dial_number(number: str) -> str:
    """Nummer in der Form für das Wählfeld.

    Steuerzeichen wie ``*``, ``#`` und ``+`` bleiben erhalten, damit
    ``*21+41860812851990`` genau so gewählt wird. Nur reine
    Telefonnummern werden auf die nationale Form gebracht (0790000001).
    """
    cleaned = KEEP_CHARS_RE.sub("", number or "")
    if not cleaned:
        return ""
    # Steuercodes: alles so wählen, wie es dasteht.
    if any(char in cleaned for char in "*#"):
        return cleaned

    digits = cleaned.replace("+", "")
    if digits.startswith("0041"):
        digits = "0" + digits[4:]
    elif digits.startswith("41") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits


def dial_number(number: str, window_title: str = WINDOW_TITLE) -> str:
    """Wählt ``number`` im Swisscom-Fenster und drückt die Anruf-Taste."""
    # pywinauto gibt es nur unter Windows; Import erst beim Wählen.
    from pywinauto import Desktop

    dialed_number = to_dial_number(number)
    if not dialed_number:
        raise DialError(f"Keine gültige Nummer: {number!r}")

    window = Desktop(backend="uia").window(title_re=f".*{re.escape(window_title)}.*")
    if not window.exists(timeout=2):
        raise DialError(f'Fenster "{window_title}" nicht gefunden.')

    dial_field = None
    call_button = None
    for element in window.descendants():
        try:
            automation_id = element.element_info.automation_id
        except Exception:
            continue
        if automation_id == DIAL_FIELD_ID:
            dial_field = element
        elif automation_id == CALL_BUTTON_ID:
            call_button = element

    if dial_field is None:
        raise DialError("Wählfeld (DialNumberTextBox) nicht gefunden.")
    if call_button is None:
        raise DialError("Anruf-Taste (CallButton) nicht gefunden.")

    try:
        # ValuePattern braucht keinen Fokus und geht auch minimiert.
        dial_field.iface_value.SetValue(dialed_number)
    except Exception:
        try:
            dial_field.click_input()
            dial_field.type_keys(_for_type_keys(dialed_number), with_spaces=False)
        except Exception as error:
            raise DialError(f"Nummer konnte nicht eingetragen werden: {error}") from error

    try:
        call_button.invoke()
    except Exception:
        call_button.click_input()

    logger.info("Anruf gestartet: %s", dialed_number)
    return dialed_number


def switch_pikett(
    action: str,
    on_number: str,
    off_number: str,
    *,
    tries: int = HITS_PER_TRIGGER,
    wait_s: float = WAIT_BETWEEN_TRIES_S,
    dial=dial_number,
    sleep=time.sleep,
    log: logging.Logger = logger,
) -> bool:
    """Wählt die Pikett-Nummer ``tries`` Mal (dazwischen ``wait_s`` Pause).

    Returns:
        True, wenn mindestens ein Anruf gestartet werden konnte.
    """
    if action not in (ACTION_ON, ACTION_OFF):
        raise ValueError(f"Unbekannte Aktion: {action!r}")

    number = on_number if action == ACTION_ON else off_number
    label = ACTION_LABELS[action]
    placed = 0

    for attempt in range(1, tries + 1):
        try:
            dial(number)
            placed += 1
            log.info("Pikett %s: Anruf %d/%d gewählt.", label, attempt, tries)
        except Exception as error:  # noqa: BLE001 - Anruf darf den Lauf nicht abbrechen
            log.error("Pikett %s: Anruf %d/%d fehlgeschlagen: %s", label, attempt, tries, error)

        if attempt < tries:
            sleep(wait_s)

    return placed > 0
