"""Tests der Feiertagsliste (ohne Netzwerk)."""

from __future__ import annotations

from datetime import date, datetime

from autopikett import holidays

TODAY = date(2026, 11, 12)

SAMPLE = [
    {
        "id": "a",
        "startDate": "2026-12-25",
        "endDate": "2026-12-25",
        "type": "Public",
        "name": [{"language": "DE", "text": "Weihnachten"}],
    },
    {
        "id": "b",
        "startDate": "2026-11-01",
        "endDate": "2026-11-01",
        "type": "Public",
        "name": [
            {"language": "EN", "text": "All Saints' Day"},
            {"language": "DE", "text": "Allerheiligen"},
        ],
    },
    {
        "id": "c",
        "startDate": "2026-12-26",
        "endDate": "2026-12-26",
        "type": "Public",
        "name": [{"language": "DE", "text": "Stephanstag"}],
    },
]


def test_parse_holidays_prefers_german_names_and_sorts() -> None:
    parsed = holidays.parse_holidays(SAMPLE)

    assert [holiday.day for holiday in parsed] == [
        date(2026, 11, 1),
        date(2026, 12, 25),
        date(2026, 12, 26),
    ]
    assert parsed[0].name == "Allerheiligen"


def test_berchtoldstag_is_not_a_sunday_equivalent_holiday() -> None:
    parsed = holidays.parse_holidays(
        [
            {
                "startDate": "2027-01-02",
                "name": [{"language": "DE", "text": "Berchtoldstag"}],
            },
            {
                "startDate": "2027-01-01",
                "name": [{"language": "DE", "text": "Neujahrstag"}],
            },
            {
                "startDate": "2027-12-26",
                "name": [{"language": "DE", "text": "Stephanstag"}],
            },
        ]
    )

    assert [holiday.name for holiday in parsed] == ["Neujahrstag", "Stephanstag"]
    assert holidays.is_sunday_equivalent("Berchtoldstag") is False
    assert holidays.is_sunday_equivalent("Allerheiligen") is True


def test_parse_holidays_skips_broken_entries() -> None:
    parsed = holidays.parse_holidays(
        [
            "kaputt",
            {"startDate": "kein Datum", "name": [{"language": "DE", "text": "X"}]},
            {"startDate": "2027-01-01", "name": []},
            {"startDate": "2027-01-02", "name": "Neujahrstag"},
        ]
    )

    assert [(holiday.day, holiday.name) for holiday in parsed] == [
        (date(2027, 1, 2), "Neujahrstag")
    ]
    assert holidays.parse_holidays(None) == []
    assert holidays.parse_holidays({}) == []


def test_pick_upcoming_drops_past_days_and_limits() -> None:
    parsed = holidays.parse_holidays(SAMPLE)

    upcoming = holidays.pick_upcoming(parsed, today=TODAY)
    assert [holiday.name for holiday in upcoming] == ["Weihnachten", "Stephanstag"]

    assert [holiday.name for holiday in holidays.pick_upcoming(parsed, today=TODAY, limit=1)] == [
        "Weihnachten"
    ]
    # Ein Feiertag am heutigen Tag bleibt sichtbar, vergangene Tage nicht.
    assert [holiday.name for holiday in holidays.pick_upcoming(parsed, today=date(2026, 11, 1))] == [
        "Allerheiligen",
        "Weihnachten",
        "Stephanstag",
    ]
    assert holidays.pick_upcoming(parsed, today=date(2027, 1, 1)) == []


def test_milliseconds_until_midnight() -> None:
    # 23:59 -> eine Minute bis Mitternacht plus 90 s Reserve.
    assert (
        holidays.milliseconds_until_midnight(datetime(2026, 11, 12, 23, 59, 0)) == 150_000
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
    parsed = holidays.parse_holidays(SAMPLE)

    found = holidays.upcoming_holidays(
        today=TODAY, fetch=lambda **_: parsed
    )
    assert found is not None
    assert [holiday.name for holiday in found] == ["Weihnachten", "Stephanstag"]

    assert holidays.upcoming_holidays(today=TODAY, fetch=lambda **_: None) is None


def test_query_asks_for_canton_st_gallen(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b"[]"

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - Testdouble
        seen["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr(holidays.urllib.request, "urlopen", fake_urlopen)

    assert holidays.fetch_holidays(today=TODAY) == []
    url = seen["url"]
    assert "countryIsoCode=CH" in url
    assert "subdivisionCode=CH-SG" in url
    assert "languageIsoCode=DE" in url
    assert "validFrom=2026-11-12" in url
    assert "validTo=2027-12-17" in url


def test_fetch_holidays_returns_none_without_network(monkeypatch) -> None:
    def broken(request, timeout=None):  # noqa: ANN001 - Testdouble
        raise holidays.urllib.error.URLError("kein Netz")

    monkeypatch.setattr(holidays.urllib.request, "urlopen", broken)
    assert holidays.fetch_holidays(today=TODAY) is None
