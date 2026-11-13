"""Tests der Enterprise-Telephony-Prüfung (ohne Windows und ohne Start)."""

from __future__ import annotations

from pathlib import Path

from autopikett import telephony

EXE = "enterprise_telephony.exe"


def test_tasklist_reports_process() -> None:
    assert telephony.tasklist_reports_process(
        "enterprise_telephony.exe          4711 Console      1     12'345 K"
    )
    assert not telephony.tasklist_reports_process(
        "INFO: No tasks are running which match the specified criteria."
    )
    assert not telephony.tasklist_reports_process("")


def test_ensure_running_accepts_a_running_process() -> None:
    started: list[Path] = []

    status = telephony.ensure_running(
        r"C:\Swisscom\enterprise_telephony.exe",
        running=lambda name: True,
        find=lambda saved: None,
        start=started.append,
    )

    assert status.running is True
    assert status.started is False
    assert status.path == r"C:\Swisscom\enterprise_telephony.exe"
    assert started == []


def test_ensure_running_starts_the_found_executable(tmp_path: Path) -> None:
    exe = tmp_path / EXE
    exe.write_text("", encoding="utf-8")
    state = {"running": False}
    started: list[Path] = []

    def fake_start(path: Path) -> None:
        started.append(path)
        state["running"] = True

    status = telephony.ensure_running(
        None,
        running=lambda name: state["running"],
        find=lambda saved: exe,
        start=fake_start,
        sleep=lambda _: None,
    )

    assert status.running is True
    assert status.started is True
    assert status.path == str(exe)
    assert started == [exe]


def test_ensure_running_reports_missing_executable() -> None:
    status = telephony.ensure_running(
        None, running=lambda name: False, find=lambda saved: None
    )

    assert status.running is False
    assert status.started is False
    assert EXE in status.message


def test_ensure_running_reports_failed_start(tmp_path: Path) -> None:
    exe = tmp_path / EXE
    exe.write_text("", encoding="utf-8")

    def broken_start(path: Path) -> None:
        raise OSError("Zugriff verweigert")

    status = telephony.ensure_running(
        None,
        running=lambda name: False,
        find=lambda saved: exe,
        start=broken_start,
    )

    assert status.running is False
    assert "Zugriff verweigert" in status.message
    assert status.path == str(exe)


def test_find_executable_uses_the_saved_path(tmp_path: Path) -> None:
    exe = tmp_path / EXE
    exe.write_text("", encoding="utf-8")

    assert telephony.find_executable(str(exe)) == exe


def test_find_executable_without_saved_path_searches_the_disk(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(telephony, "KNOWN_PATH", tmp_path / "nicht_da" / EXE)

    target = tmp_path / "Program Files" / "Swisscom" / "Enterprise Telephony" / EXE
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")

    monkeypatch.setattr(telephony, "drive_roots", lambda: [tmp_path])
    assert telephony.find_executable(None) == target


def test_search_disk_skips_system_folders(tmp_path: Path) -> None:
    (tmp_path / "Windows").mkdir()
    (tmp_path / "Windows" / EXE).write_text("", encoding="utf-8")
    target = tmp_path / "Swisscom" / "Telephony" / EXE
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")

    assert telephony.search_disk(roots=[tmp_path]) == target


def test_iter_files_stops_at_the_depth_limit(tmp_path: Path) -> None:
    deep = tmp_path
    for part in ("a", "b", "c", "d"):
        deep = deep / part
    deep.mkdir(parents=True)
    (deep / EXE).write_text("", encoding="utf-8")
    (tmp_path / "flach.txt").write_text("", encoding="utf-8")

    found = [path.name for path in telephony.iter_files(tmp_path, max_depth=2)]

    assert "flach.txt" in found
    assert EXE not in found
