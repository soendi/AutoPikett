"""Tests der Feiertagsliste (ohne Netzwerk)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from autopikett import holidays

TODAY = date(2026, 11, 12)

CSV_TEXT = (
    '\ufeff"Datum";"Bezeichnung";"Wochentag";"Kalenderwoche"\r\n'
    '"01.01.2026";"Neujahr";"Do.";"Woche 01"\r\n'
    '"03.04.2026";"Karfreitag";"Fr.";"Woche 14"\r\n'
    '"25.12.2026";"Weihnachten";"Fr.";"Woche 52"\r\n'
    '"26.12.2026";"Stephanstag";"Sa.";"Woche 52"\r\n'
)

BLOCKED_TEXT = (
    "Feiertagskalender.ch: Datenausgabe blockiert: zu viele Downloads "
    "für private Nutzung. Bitte versuchen Sie es später noch einmal.\r\n"
)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _fake_download(monkeypatch, body: str = CSV_TEXT) -> dict[str, str]:
    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - Testdouble
        seen["url"] = request.full_url
        return FakeResponse(body.encode("utf-8"))

    monkeypatch.setattr(holidays.urllib.request, "urlopen", fake_urlopen)
    return seen


def _offline(monkeypatch) -> None:
    def broken(request, timeout=None):  # noqa: ANN001 - Testdouble
        raise AssertionError("Ohne gültigen Zwischenspeicher darf geladen werden")

    monkeypatch.setattr(holidays.urllib.request, "urlopen", broken)


def _age(path: Path, days: int) -> None:
    stamp = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_parse_csv_reads_german_dates_and_sorts(tmp_path) -> None:
    parsed = holidays.parse_csv(CSV_TEXT)

    assert [holiday.day for holiday in parsed] == [
        date(2026, 1, 1),
        date(2026, 4, 3),
        date(2026, 12, 25),
        date(2026, 12, 26),
    ]
    assert parsed[2].name == "Weihnachten"


def test_parse_csv_skips_header_and_broken_rows() -> None:
    parsed = holidays.parse_csv(
        '"Datum";"Bezeichnung";"Wochentag";"Kalenderwoche"\r\n'
        '"kaputt";"X";"Mo.";"Woche 01"\r\n'
        '"01.01.2026";"";"Do.";"Woche 01"\r\n'
        '"32.01.2026";"Unsinn";"Sa.";"Woche 05"\r\n'
        '"01.01.2026";"Neujahr";"Do.";"Woche 01"\r\n'
        '"01.01.2026";"Neujahr";"Do.";"Woche 01"\r\n'
    )

    assert [(holiday.day, holiday.name) for holiday in parsed] == [
        (date(2026, 1, 1), "Neujahr")
    ]
    assert holidays.parse_csv("") == []


def test_export_detection_rejects_block_message() -> None:
    assert holidays.is_export(CSV_TEXT) is True
    assert holidays.is_export(BLOCKED_TEXT) is False


def test_fresh_cache_is_used_without_network(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(CSV_TEXT, encoding="utf-8")
    _offline(monkeypatch)

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]


def test_download_stores_cache_and_asks_for_balgach(tmp_path, monkeypatch) -> None:
    seen = _fake_download(monkeypatch)
    cache = tmp_path / "holidays.csv"

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]
    assert "geo=1903" in seen["url"]
    assert "klasse=3" in seen["url"]
    assert cache.exists()
    assert "Weihnachten" in cache.read_text(encoding="utf-8")


def test_holidays_outside_the_window_are_dropped(tmp_path, monkeypatch) -> None:
    text = CSV_TEXT + '"01.01.2029";"Neujahr";"Mo.";"Woche 01"\r\n'
    _fake_download(monkeypatch, text)
    cache = tmp_path / "holidays.csv"

    found = holidays.fetch_holidays(today=TODAY, cache=cache, days=60)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]


def test_cache_older_than_a_week_is_refreshed(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(
        CSV_TEXT.replace('"26.12.2026";"Stephanstag";"Sa.";"Woche 52"\r\n', ""),
        encoding="utf-8",
    )
    _age(cache, holidays.CACHE_MAX_AGE_DAYS + 1)
    _fake_download(monkeypatch)

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]


def test_refresh_ignores_a_fresh_cache(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(
        CSV_TEXT.replace('"26.12.2026";"Stephanstag";"Sa.";"Woche 52"\r\n', ""),
        encoding="utf-8",
    )
    _fake_download(monkeypatch)

    found = holidays.fetch_holidays(today=TODAY, cache=cache, force=True)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]


def test_blocked_download_keeps_the_stored_list(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(CSV_TEXT, encoding="utf-8")
    _age(cache, holidays.CACHE_MAX_AGE_DAYS + 1)
    _fake_download(monkeypatch, BLOCKED_TEXT)

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]
    assert "Neujahr" in cache.read_text(encoding="utf-8")


def test_cache_saved_at_is_the_download_time(tmp_path) -> None:
    cache = tmp_path / "holidays.csv"
    assert holidays.cache_saved_at(cache) is None

    cache.write_text(CSV_TEXT, encoding="utf-8")
    stamp = datetime(2026, 11, 9, 7, 15)
    os.utime(cache, (stamp.timestamp(), stamp.timestamp()))

    assert holidays.cache_saved_at(cache) == stamp


def test_blocked_refresh_keeps_the_download_time(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(CSV_TEXT, encoding="utf-8")
    stamp = datetime(2026, 11, 2, 6, 30)
    os.utime(cache, (stamp.timestamp(), stamp.timestamp()))
    _fake_download(monkeypatch, BLOCKED_TEXT)

    found = holidays.fetch_holidays(today=TODAY, cache=cache, force=True)

    assert found is not None
    assert holidays.cache_saved_at(cache) == stamp


def _nager_body(year: int) -> str:
    return json.dumps(
        [
            {
                "date": f"{year}-01-01",
                "localName": "Neujahr",
                "counties": None,
                "types": ["Public"],
            },
            {
                "date": f"{year}-11-01",
                "localName": "Allerheiligen",
                "counties": ["CH-SG"],
                "types": ["Public"],
            },
            {
                "date": f"{year}-09-20",
                "localName": "Bettag",
                "counties": ["CH-SG"],
                "types": ["Observance"],
            },
            {
                "date": f"{year}-05-01",
                "localName": "Tag der Arbeit",
                "counties": ["CH-ZH"],
                "types": ["Public"],
            },
        ]
    )


def _fake_sources(monkeypatch, *, nager: bool = True) -> dict[str, int]:
    """Export liefert die Sperrseite; Nager.Date antwortet mit JSON."""
    calls = {"fcal": 0, "nager": 0}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - Testdouble
        url = request.full_url
        if "fcal.ch" in url:
            calls["fcal"] += 1
            return FakeResponse(BLOCKED_TEXT.encode("utf-8"))
        calls["nager"] += 1
        if not nager:
            raise holidays.urllib.error.URLError("kein Netz")
        year = int(url.rstrip("/").split("/")[-2])
        return FakeResponse(_nager_body(year).encode("utf-8"))

    monkeypatch.setattr(holidays.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_blocked_export_falls_back_to_nager(tmp_path, monkeypatch) -> None:
    calls = _fake_sources(monkeypatch)
    cache = tmp_path / "holidays.csv"

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert calls == {"fcal": 1, "nager": 2}
    assert found is not None
    assert [(holiday.day.isoformat(), holiday.name) for holiday in found] == [
        ("2027-01-01", "Neujahr"),
        ("2027-11-01", "Allerheiligen"),
    ]
    # Die Ersatzliste liegt im gleichen Speicher und gilt eine Woche.
    assert "Allerheiligen" in cache.read_text(encoding="utf-8")
    assert holidays.cache_saved_at(cache) is not None


def test_both_sources_failing_keeps_the_stored_file(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "holidays.csv"
    cache.write_text(CSV_TEXT, encoding="utf-8")
    _age(cache, holidays.CACHE_MAX_AGE_DAYS + 1)
    _fake_sources(monkeypatch, nager=False)

    found = holidays.fetch_holidays(today=TODAY, cache=cache)

    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]


def test_without_cache_and_network_there_is_no_list(tmp_path, monkeypatch) -> None:
    def broken(request, timeout=None):  # noqa: ANN001 - Testdouble
        raise holidays.urllib.error.URLError("kein Netz")

    monkeypatch.setattr(holidays.urllib.request, "urlopen", broken)

    assert holidays.fetch_holidays(today=TODAY, cache=tmp_path / "leer.csv") is None


def test_pick_upcoming_drops_past_days_and_limits() -> None:
    parsed = holidays.parse_csv(CSV_TEXT)

    upcoming = holidays.pick_upcoming(parsed, today=TODAY)
    assert [holiday.name for holiday in upcoming] == ["Weihnachten", "Stephanstag"]

    assert [
        holiday.name
        for holiday in holidays.pick_upcoming(parsed, today=TODAY, limit=1)
    ] == ["Weihnachten"]
    # Ein Feiertag am heutigen Tag bleibt sichtbar, vergangene Tage nicht.
    assert [
        holiday.name
        for holiday in holidays.pick_upcoming(parsed, today=date(2026, 12, 26))
    ] == ["Stephanstag"]
    assert holidays.pick_upcoming(parsed, today=date(2027, 1, 1)) == []


def test_milliseconds_until_midnight() -> None:
    # 23:59 -> eine Minute bis Mitternacht plus 90 s Reserve.
    assert (
        holidays.milliseconds_until_midnight(datetime(2026, 11, 12, 23, 59, 0))
        == 150_000
    )
    # 08:00 -> noch fast ein ganzer Tag.
    assert (
        holidays.milliseconds_until_midnight(datetime(2026, 11, 12, 8, 0, 0))
        == 16 * 3_600_000 + 90_000
    )


def test_label_shows_weekday_and_date() -> None:
    holiday = holidays.Holiday(date(2026, 12, 25), "Weihnachten")
    assert holiday.label() == "Fr 25.12.2026  Weihnachten"


def test_upcoming_holidays_uses_injected_fetch() -> None:
    parsed = holidays.parse_csv(CSV_TEXT)

    found = holidays.upcoming_holidays(today=TODAY, fetch=lambda **_: parsed)
    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]

    assert holidays.upcoming_holidays(today=TODAY, fetch=lambda **_: None) is None
