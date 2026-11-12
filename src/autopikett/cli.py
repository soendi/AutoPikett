"""Command-line interface for AutoPikett."""

from __future__ import annotations

import argparse

from autopikett.scheduler import Scheduler, Shift, TeamMember


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoPikett - Verwaltung von Pikett- und Bereitschaftszeiten")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="Zeigt Mitglieder und Schichten an")
    list_parser.set_defaults(handler=list_command)

    add_member_parser = subparsers.add_parser("member", help="Fügt ein Teammitglied hinzu")
    add_member_parser.add_argument("name")
    add_member_parser.add_argument("--role", default="member")
    add_member_parser.set_defaults(handler=add_member_command)

    return parser


def list_command(args: argparse.Namespace) -> int:
    scheduler = Scheduler()
    scheduler.add_member(TeamMember("Max Mustermann", role="oncall"))
    scheduler.add_member(TeamMember("Erika Beispiel", role="ops"))
    scheduler.add_shift(Shift("Nacht", ["Max Mustermann"], notes="Standard-Shift"))

    print("Mitglieder:")
    for member in scheduler.list_members():
        print(f"- {member}")

    print("\nSchichten:")
    for shift in scheduler.list_shifts():
        print(f"- {shift}")

    return 0


def add_member_command(args: argparse.Namespace) -> int:
    scheduler = Scheduler()
    scheduler.add_member(TeamMember(args.name, role=args.role))
    print(f"Mitglied hinzugefügt: {args.name} ({args.role})")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "handler"):
        parser.print_help()
        return 0

    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
