"""Tests der Kernlogik: Zeitplan, Speicherung, Anruf-Wiederholungen."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from autopikett import autostart, core, dialer

MONDAY = datetime(2026, 11, 9, 8, 0)
SATURDAY = datetime(2026, 11, 14, 12, 0)


def test_weekdays_are_mapped_as_expected() -> None:
    assert MONDAY.weekday() == 0
    assert SATURDAY.weekday() == 5


def test_defaults_follow_the_requested_week() -> None:
    settings = core.default_settings()

    enabled = [settings.days[day].enabled for day in core.WEEKDAYS]
    assert enabled == [True, True, True, True, True, False, False]

    monday = settings.days["Montag"]
    assert [slot.time for slot in monday.slots] == ["08:00", "12:00", "13:30", "17:00"]
    assert [slot.action for slot in monday.slots] == [
        core.ACTION_OFF,
        core.ACTION_ON,
        core.ACTION_OFF,
        core.ACTION_ON,
    ]
    assert len(monday.slots) == core.SLOTS_PER_DAY

    assert settings.on_number == core.DEFAULT_ON_NUMBER
    assert settings.off_number == core.DEFAULT_OFF_NUMBER
    assert settings.on_number != settings.off_number
    assert settings.autostart is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("8:00", "08:00"), ("08.00", "08:00"), (" 17:5 ", "17:05"), ("23:59", "23:59")],
)
def test_normalize_time_accepts_common_notation(raw: str, expected: str) -> None:
    assert core.normalize_time(raw) == expected


@pytest.mark.parametrize("raw", ["", "24:00", "12:60", "abc", "12", "12:345"])
def test_normalize_time_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        core.normalize_time(raw)


def test_settings_roundtrip(tmp_path) -> None:
    target = tmp_path / "settings.json"
    settings = core.default_settings()
    settings.on_number = "0800 111 22 33"
    settings.auto_update = False
    settings.autostart = False
    settings.days["Montag"].slots[1] = core.Slot("11:45", core.ACTION_ON)
    settings.days["Sonntag"].enabled = True

    core.save_settings(settings, target)
    loaded = core.load_settings(target)

    assert loaded.on_number == "0800 111 22 33"
    assert loaded.off_number == core.DEFAULT_OFF_NUMBER
    assert loaded.auto_update is False
    assert loaded.autostart is False
    assert loaded.days["Montag"].slots[1].time == "11:45"
    assert loaded.days["Sonntag"].enabled is True
    assert loaded.days["Samstag"].enabled is False


def test_missing_file_falls_back_to_defaults(tmp_path) -> None:
    loaded = core.load_settings(tmp_path / "nicht_da.json")
    assert loaded.to_dict() == core.default_settings().to_dict()


def test_broken_file_falls_back_to_defaults(tmp_path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{ kaputt", encoding="utf-8")
    assert core.load_settings(target).days["Montag"].enabled is True


def test_partial_file_keeps_defaults_for_missing_parts(tmp_path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({"on_number": "079 123 45 67", "days": {"Montag": {"enabled": False}}}),
        encoding="utf-8",
    )
    loaded = core.load_settings(target)

    assert loaded.on_number == "079 123 45 67"
    assert loaded.days["Montag"].enabled is False
    assert loaded.days["Montag"].slots[0].time == "08:00"
    assert loaded.days["Dienstag"].enabled is True


def test_due_slots_match_the_current_minute() -> None:
    settings = core.default_settings()

    due = core.due_slots(settings, MONDAY)
    assert [(slot.time, slot.action) for slot in due] == [("08:00", core.ACTION_OFF)]

    assert core.due_slots(settings, MONDAY.replace(hour=8, minute=1)) == []
    assert core.due_slots(settings, MONDAY.replace(hour=12, minute=0))[0].action == core.ACTION_ON


def test_disabled_day_has_no_jobs() -> None:
    settings = core.default_settings()
    assert core.due_slots(settings, SATURDAY) == []
    assert core.pending_jobs(settings, SATURDAY) == []


def test_pending_jobs_run_only_once() -> None:
    settings = core.default_settings()

    jobs = core.pending_jobs(settings, MONDAY)
    assert len(jobs) == 1
    key, action = jobs[0]
    assert action == core.ACTION_OFF

    # Noch nicht ausgeführt -> erneut fällig, aber nicht doppelt.
    assert [pending_key for pending_key, _ in core.pending_jobs(settings, MONDAY)] == [key]

    settings.last_runs[key] = MONDAY.isoformat()
    assert core.pending_jobs(settings, MONDAY) == []


def test_pending_jobs_skip_holidays() -> None:
    settings = core.default_settings()

    assert core.pending_jobs(settings, MONDAY, skip_days=(MONDAY.date(),)) == []
    # Ein Feiertag an einem anderen Tag ändert nichts.
    other_day = MONDAY.date() + timedelta(days=1)
    assert core.pending_jobs(settings, MONDAY, skip_days=(other_day,)) != []


def test_pending_jobs_forget_previous_days() -> None:
    settings = core.default_settings()
    settings.last_runs = {"2026-11-08|08:00|off": "alt", "kaputt": "alt"}

    core.pending_jobs(settings, MONDAY)
    assert settings.last_runs == {}


def test_all_four_times_of_a_day_fire() -> None:
    settings = core.default_settings()
    found = []
    for hour, minute in [(8, 0), (12, 0), (13, 30), (17, 0)]:
        found.extend(
            action
            for _, action in core.pending_jobs(
                settings, MONDAY.replace(hour=hour, minute=minute)
            )
        )
    assert found == [
        core.ACTION_OFF,
        core.ACTION_ON,
        core.ACTION_OFF,
        core.ACTION_ON,
    ]


def test_next_event_skips_disabled_days_and_past_times() -> None:
    settings = core.default_settings()

    moment, action, day = core.next_event(settings, MONDAY.replace(hour=9, minute=0))
    assert (moment.strftime("%Y-%m-%d %H:%M"), action, day) == ("2026-11-09 12:00", "on", "Montag")

    # Freitag nach dem letzten Wechsel -> Montag der Folgewoche.
    friday_evening = datetime(2026, 11, 13, 18, 0)
    moment, action, day = core.next_event(settings, friday_evening)
    assert (moment.strftime("%Y-%m-%d %H:%M"), action, day) == ("2026-11-16 08:00", "off", "Montag")


def test_next_event_skips_holidays() -> None:
    settings = core.default_settings()

    moment, action, day = core.next_event(
        settings, MONDAY.replace(hour=9, minute=0), skip_days=(MONDAY.date(),)
    )
    assert (moment.strftime("%Y-%m-%d %H:%M"), action, day) == (
        "2026-11-10 08:00",
        "off",
        "Dienstag",
    )

    # Sonntag ist ohnehin aus, Montag als Feiertag -> Dienstag der Folgewoche.
    moment, _, day = core.next_event(
        settings,
        datetime(2026, 11, 13, 18, 0),
        skip_days=(datetime(2026, 11, 16).date(),),
    )
    assert (moment.strftime("%Y-%m-%d %H:%M"), day) == ("2026-11-17 08:00", "Dienstag")


def test_next_event_without_active_days() -> None:
    settings = core.default_settings()
    for plan in settings.days.values():
        plan.enabled = False
    assert core.next_event(settings, MONDAY) is None


def test_state_label() -> None:
    assert core.state_label(True) == "Pikett EIN"
    assert core.state_label(False) == "Pikett AUS"
    assert core.TRAY_COLOR_ON != core.TRAY_COLOR_OFF


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("079 000 00 01", "0790000001"),
        ("+41 79 000 00 01", "0790000001"),
        ("0041 79 000 00 01", "0790000001"),
        ("41790000001", "0790000001"),
        ("", ""),
        # Steuercodes bleiben vollständig erhalten.
        ("*21+41860812851990", "*21+41860812851990"),
        ("#21", "#21"),
        (" *21+41 860 812 851 990 ", "*21+41860812851990"),
    ],
)
def test_to_dial_number(raw: str, expected: str) -> None:
    assert dialer.to_dial_number(raw) == expected


def test_default_numbers_are_dialed_unchanged() -> None:
    settings = core.default_settings()
    assert settings.on_number == "*21+41860812851990"
    assert settings.off_number == "#21"
    assert dialer.to_dial_number(settings.on_number) == "*21+41860812851990"
    assert dialer.to_dial_number(settings.off_number) == "#21"


def test_old_placeholder_numbers_fall_back_to_real_numbers(tmp_path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({"on_number": "079 000 00 01", "off_number": "079 000 00 02"}),
        encoding="utf-8",
    )
    loaded = core.load_settings(target)

    assert loaded.on_number == core.DEFAULT_ON_NUMBER
    assert loaded.off_number == core.DEFAULT_OFF_NUMBER


def test_type_keys_keeps_control_characters_literal() -> None:
    assert dialer._for_type_keys("*21+41860812851990") == "*21{+}41860812851990"
    assert dialer._for_type_keys("#21") == "#21"


def test_switch_pikett_dials_three_times_with_ten_second_pauses() -> None:
    numbers: list[str] = []
    pauses: list[float] = []

    ok = dialer.switch_pikett(
        core.ACTION_ON,
        "079 000 00 01",
        "079 000 00 02",
        dial=numbers.append,
        sleep=pauses.append,
    )

    assert ok is True
    assert numbers == ["079 000 00 01"] * 3
    assert pauses == [10, 10]


def test_switch_pikett_uses_off_number() -> None:
    numbers: list[str] = []
    dialer.switch_pikett(
        core.ACTION_OFF,
        "079 000 00 01",
        "079 000 00 02",
        dial=numbers.append,
        sleep=lambda _: None,
    )
    assert numbers == ["079 000 00 02"] * 3


def test_switch_pikett_reports_failure() -> None:
    pauses: list[float] = []

    def broken(_: str) -> None:
        raise dialer.DialError("kein Fenster")

    ok = dialer.switch_pikett(
        core.ACTION_ON,
        "079 000 00 01",
        "079 000 00 02",
        dial=broken,
        sleep=pauses.append,
    )

    assert ok is False
    assert pauses == [10, 10]


def test_switch_pikett_rejects_unknown_action() -> None:
    with pytest.raises(ValueError):
        dialer.switch_pikett("vielleicht", "1", "2", dial=lambda _: None, sleep=lambda _: None)


def test_history_keeps_one_entry_per_job_newest_first(tmp_path) -> None:
    target = tmp_path / "history.json"
    first = core.HistoryEntry(
        "2026-11-09T08:00:00", core.ACTION_OFF, "0790000002", "Zeitplan", True
    )
    second = core.HistoryEntry(
        "2026-11-09T12:00:00",
        core.ACTION_ON,
        "0790000001",
        "Zeitplan",
        False,
        "kein Anruf gestartet",
    )

    core.record_history(first, target)
    entries = core.record_history(second, target)

    assert [entry.timestamp for entry in entries] == [
        "2026-11-09T12:00:00",
        "2026-11-09T08:00:00",
    ]

    loaded = core.load_history(target)
    assert len(loaded) == 2
    assert loaded[0].result_label() == "fehlgeschlagen"
    assert loaded[1].result_label() == "erfolgreich"
    assert loaded[1].action_label() == "Pikett Aus"
    assert loaded[0].detail == "kein Anruf gestartet"


def test_history_is_limited(tmp_path) -> None:
    target = tmp_path / "history.json"
    for index in range(5):
        core.record_history(
            core.HistoryEntry(
                f"2026-11-09T08:0{index}:00", core.ACTION_ON, "1", "Zeitplan", True
            ),
            target,
            limit=3,
        )
    assert len(core.load_history(target)) == 3


def test_history_tolerates_broken_file(tmp_path) -> None:
    target = tmp_path / "history.json"
    target.write_text("nicht json", encoding="utf-8")
    assert core.load_history(target) == []


def test_history_skips_invalid_entries(tmp_path) -> None:
    target = tmp_path / "history.json"
    target.write_text(
        json.dumps(
            {
                "entries": [
                    {"timestamp": "2026-11-09T08:00:00", "action": "on", "ok": True},
                    {"action": "on"},
                    "kaputt",
                ]
            }
        ),
        encoding="utf-8",
    )
    entries = core.load_history(target)
    assert len(entries) == 1
    assert entries[0].ok is True


def test_clear_history_removes_entries(tmp_path) -> None:
    target = tmp_path / "history.json"
    core.record_history(
        core.HistoryEntry("2026-11-09T08:00:00", core.ACTION_ON, "1", "Zeitplan", True),
        target,
    )
    assert core.load_history(target) != []

    core.clear_history(target)
    assert core.load_history(target) == []

    # Mehrfaches Leeren darf nicht scheitern.
    core.clear_history(target)
    assert core.load_history(target) == []


def test_format_timestamp() -> None:
    assert core.format_timestamp("2026-11-09T08:00:00") == "09.11.2026 08:00:00"
    assert core.format_timestamp("kaputt") == "kaputt"


def test_autostart_is_ignored_on_other_platforms(monkeypatch) -> None:
    monkeypatch.setattr(autostart.sys, "platform", "linux")

    assert autostart.is_supported() is False
    assert autostart.is_enabled() is False
    assert autostart.apply(True) is False
    assert autostart.apply(False) is False


def test_autostart_command_needs_installed_exe(monkeypatch) -> None:
    monkeypatch.delattr(autostart.sys, "frozen", raising=False)
    assert autostart.autostart_command() is None


def test_autostart_command_starts_in_tray(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "AutoPikett.exe"
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", str(exe))

    assert autostart.autostart_command() == f'"{exe}" --tray'


def test_parse_version() -> None:
    assert core.parse_version("v1.2.3") == (1, 2, 3)
    assert core.parse_version("2.0") == (2, 0, 0)
    assert core.parse_version("") == (0, 0, 0)
    assert core.parse_version("1.10.0") > core.parse_version("1.9.9")


def test_installer_asset_lookup() -> None:
    release = {
        "assets": [
            {"name": "AutoPikett-Setup-1.0.1.exe", "browser_download_url": "https://x/1.0.1"},
            {"name": "irgendwas.zip", "browser_download_url": "https://x/z"},
        ]
    }
    assert core.installer_asset_url(release, "1.0.1") == "https://x/1.0.1"
    assert core.installer_asset_url({"assets": []}, "1.0.1") is None
    assert core.installer_asset_url(None, "1.0.1") is None


def test_available_update_detects_newer_release(monkeypatch) -> None:
    release = {
        "tag_name": "v1.2.0",
        "assets": [
            {
                "name": "AutoPikett-Setup-1.2.0.exe",
                "browser_download_url": "https://example/setup.exe",
            }
        ],
    }
    monkeypatch.setattr(core, "fetch_latest_release", lambda *_, **__: release)

    assert core.available_update("1.1.9") == ("1.2.0", "https://example/setup.exe")
    assert core.available_update("1.2.0") is None
    assert core.available_update("2.0.0") is None


def test_available_update_without_network(monkeypatch) -> None:
    monkeypatch.setattr(core, "fetch_latest_release", lambda *_, **__: None)
    assert core.available_update() is None
