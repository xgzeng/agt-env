"""Command-line entry point."""

import argparse
import os
import sys
from collections.abc import Container, Sequence
from pathlib import Path

from agt_view import __version__
from agt_view.adapters import ADAPTERS
from agt_view.adapters.base import Context
from agt_view.model import AgentReport, Item, Kind, Scope
from agt_view.render import clean, render_item, render_report

_TOP_LEVEL_FLAGS = {"-h", "--help", "--version"}


def _agent_id(value: str) -> str:
    """Stands in for `choices`, which rejects an empty list of agents on Python 3.11."""
    ids = [adapter.id for adapter in ADAPTERS]
    if value not in ids:
        raise argparse.ArgumentTypeError(
            f"unknown agent {clean(value)!r} (choose from {', '.join(ids)})"
        )
    return value


def _build_parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="agt-view",
        description="List what each coding agent can use from this directory. "
        "With no command, runs `list`.",
    )
    parser.add_argument("--version", action="version", version=f"agt-view {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument(
        "agents",
        nargs="*",
        metavar="agent",
        type=_agent_id,
        help=f"only these agents: {', '.join(adapter.id for adapter in ADAPTERS)}",
    )
    filters.add_argument(
        "-k", "--kind", action="append", choices=[kind.value for kind in Kind], help="(repeatable)"
    )
    filters.add_argument("-s", "--scope", choices=[scope.value for scope in Scope])
    filters.add_argument(
        "-p",
        "--project",
        type=Path,
        help="project directory to inspect (default: current directory)",
    )
    filters.add_argument("--no-color", action="store_true")

    list_parser = commands.add_parser("list", parents=[filters], help="list agents and their items")
    list_parser.add_argument(
        "--all", action="store_true", help="also show agents not both installed and configured"
    )
    # A parent, because the name has to come before the agents.
    show_filters = argparse.ArgumentParser(add_help=False)
    show_filters.add_argument("name", help="item name; a plugin can omit its @marketplace")
    show_parser = commands.add_parser(
        "show",
        parents=[show_filters, filters],
        help="show all details of an item, across every agent",
    )
    return parser, {"list": list_parser, "show": show_parser}


def _with_default_command(argv: Sequence[str], commands: Container[str]) -> list[str]:
    arguments = list(argv)
    if arguments and (arguments[0] in commands or arguments[0] in _TOP_LEVEL_FLAGS):
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
    if args.agents:
        shown = [report for report in reports if report.agent in args.agents]
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
        if args.agents or args.all
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


def _matches(item: Item, name: str) -> bool:
    if item.name == name:
        return True
    # `plugin@marketplace` can be asked for by its plugin name. `@org/x` is a whole name.
    plugin, separator, _ = item.name.rpartition("@")
    return item.kind is Kind.EXTENSION and bool(separator and plugin) and plugin == name


def run_show(args: argparse.Namespace, context: Context, color: bool) -> int:
    """Search all detected agents, even hidden ones: naming an item is an explicit request."""
    reports = [adapter.scan(context) for adapter in ADAPTERS]
    searched = [
        report
        for report in reports
        if (report.agent in args.agents if args.agents else report.installed or report.configured)
    ]
    matches = [
        (report, [i for i in _filtered(report, args.kind, args.scope) if _matches(i, args.name)])
        for report in searched
    ]
    matches = [(report, items) for report, items in matches if items]
    matched_reports = [report for report, _ in matches]
    _mark_shared([report for report in reports if report.active or report in matched_reports])

    blocks = [
        render_item(report, item, home=context.home, cwd=context.cwd, color=color)
        for report, items in matches
        for item in items
    ]
    if blocks:
        print("\n\n".join(blocks))
    else:
        print(f'agt-view: no item named "{clean(args.name)}"', file=sys.stderr)

    # A broken config can be why nothing matched, so warn for every agent searched.
    for report in searched:
        for warning in report.warnings:
            print(f"warning: {report.agent}: {clean(warning)}", file=sys.stderr)
    return 0 if blocks else 1


def main(argv: Sequence[str] | None = None) -> int:
    top_parser, parsers = _build_parsers()
    arguments = _with_default_command(sys.argv[1:] if argv is None else argv, parsers)
    if arguments[0] in _TOP_LEVEL_FLAGS:
        top_parser.parse_args(arguments)  # Prints the help or version, and exits.

    # Intermixed parsing, unavailable through subparsers, allows `show name --kind mcp codex`.
    command, *rest = arguments
    parser = parsers[command]
    args = parser.parse_intermixed_args(rest)

    cwd = (args.project or Path.cwd()).resolve()
    if not cwd.is_dir():
        parser.error(f"--project is not a directory: {clean(str(args.project))}")
    context = Context(home=Path.home(), cwd=cwd, env=os.environ)

    color = _use_color(args.no_color)
    try:
        if command == "show":
            return run_show(args, context, color)
        run_list(args, context, color)
    except BrokenPipeError:
        # The reader closed the pipe, as with `| head`; stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError as error:
        print(f"agt-view: {clean(str(error))}", file=sys.stderr)
        return 1
    return 0
