from autopikett.scheduler import Scheduler, Shift, TeamMember


def test_scheduler_tracks_members_and_shifts() -> None:
    scheduler = Scheduler()
    scheduler.add_member(TeamMember("Anna", role="lead"))
    scheduler.add_shift(Shift("Woche", ["Anna"], notes="Testschicht"))

    assert scheduler.list_members() == ["Anna"]
    assert scheduler.list_shifts() == ["Woche"]
