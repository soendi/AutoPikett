"""Core scheduling logic for AutoPikett."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class TeamMember:
    name: str
    role: str = "member"
    active: bool = True


@dataclass
class Shift:
    title: str
    members: List[str] = field(default_factory=list)
    notes: str = ""


class Scheduler:
    """Simple placeholder scheduler for future logic."""

    def __init__(self) -> None:
        self.members: List[TeamMember] = []
        self.shifts: List[Shift] = []

    def add_member(self, member: TeamMember) -> None:
        self.members.append(member)

    def add_shift(self, shift: Shift) -> None:
        self.shifts.append(shift)

    def list_members(self) -> list[str]:
        return [member.name for member in self.members]

    def list_shifts(self) -> list[str]:
        return [shift.title for shift in self.shifts]
