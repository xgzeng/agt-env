"""Command-line entry point."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agt_view import __version__
from agt_view.adapters import ADAPTERS
from agt_view.adapters.base import Context
from agt_view.model import AgentReport, Item, Kind, Scope
from agt_view.render import clean, render_report

_COMMANDS = {"list"}
_TOP_LEVEL_FLAGS = {"-h", "--help", "--version"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agt-view",
        description="List what each coding agent can use from this directory. "
        "With no command, runs `list`.",
    )
    parser.add_argument("--version", action="version", version=f"agt-view {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list agents and their items")
    list_parser.add_argument(
        "--all", action="store_true", help="also show agents not both installed and configured"
    )
    list_parser.add_argument(
        "--agent",
        action="append",
        choices=[adapter.id for adapter in ADAPTERS],
        help="show only this agent, whatever its state (repeatable)",
    )
    list_parser.add_argument(
        "--kind", action="append", choices=[kind.value for kind in Kind], help="(repeatable)"
    )
    list_parser.add_argument("--scope", choices=[scope.value for scope in Scope])
    list_parser.add_argument(
        "--cwd", type=Path, help="project root to inspect (default: current directory)"
    )
    list_parser.add_argument("--no-color", action="store_true")
    return parser


def _with_default_command(argv: Sequence[str]) -> list[str]:
    arguments = list(argv)
    if arguments and (arguments[0] in _COMMANDS or arguments[0] in _TOP_LEVEL_FLAGS):
        return arguments
    return ["list", *arguments]


def _use_color(no_color_flag: bool) -> bool:
    return not no_color_flag and "NO_COLOR" not in os.environ and sys.stdout.isatty()


def _mark_shared(reports: list[AgentReport]) -> None:
    """Record, on each skill, the other given agents using the same real path."""
    users: dict[Path, set[str]] = {}
    for report in reports:
        for item in report.items:
            if item.kind is Kind.SKILL:
                users.setdefault(item.resolved_path, set()).add(report.agent)
    order = {adapter.id: rank for rank, adapter in enumerate(ADAPTERS)}
    for report in reports:
        for item in report.items:
            if item.kind is Kind.SKILL:
                others = users[item.resolved_path] - {report.agent}
                item.shared_with = sorted(others, key=order.__getitem__)


def _filtered(report: AgentReport, kinds: list[str] | None, scope: str | None) -> list[Item]:
    items = [
        item
        for item in report.items
        if (not kinds or item.kind in kinds) and (not scope or item.scope == scope)
    ]
    kind_order = {kind: rank for rank, kind in enumerate(Kind)}
    return sorted(items, key=lambda item: (kind_order[item.kind], item.name, item.scope))


def run_list(args: argparse.Namespace, context: Context, color: bool) -> None:
    reports = [adapter.scan(context) for adapter in ADAPTERS]
    if args.agent:
        shown = [report for report in reports if report.agent in args.agent]
    elif args.all:
        shown = [report for report in reports if report.installed or report.configured]
    else:
        shown = [report for report in reports if report.active]

    _mark_shared([report for report in reports if report.active or report in shown])

    sections = [
        render_report(
            report,
            _filtered(report, args.kind, args.scope),
            home=context.home,
            cwd=context.cwd,
            color=color,
        )
        for report in shown
    ]
    if sections:
        print("\n\n".join(sections))

    hidden = (
        []
        if args.agent or args.all
        else [r.agent for r in reports if (r.installed or r.configured) and not r.active]
    )
    if not shown and not hidden:
        supported = ", ".join(adapter.id for adapter in ADAPTERS)
        print(f"No supported agents detected. Supported agents: {supported}.")
    elif not shown:
        print("No agents are both installed and configured.")
    if hidden:
        if sections:
            print()
        print(f"Hidden: {', '.join(hidden)}. Use --all for details.")

    for report in shown:
        for warning in report.warnings:
            print(f"warning: {report.agent}: {clean(warning)}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(_with_default_command(sys.argv[1:] if argv is None else argv))

    cwd = (args.cwd or Path.cwd()).resolve()
    if not cwd.is_dir():
        parser.error(f"--cwd is not a directory: {clean(str(args.cwd))}")
    context = Context(home=Path.home(), cwd=cwd, env=os.environ)

    try:
        run_list(args, context, _use_color(args.no_color))
    except BrokenPipeError:
        # The reader closed the pipe, as with `| head`; stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError as error:
        print(f"agt-view: {clean(str(error))}", file=sys.stderr)
        return 1
    return 0
