"""Command-line entry point."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agt_view import __version__
from agt_view.adapters import ADAPTERS
from agt_view.adapters.base import Context
from agt_view.filters import ItemFilter, select_reports
from agt_view.model import AgentReport, Item, Kind, Scope
from agt_view.render import clean, render_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agt-view",
        description="List what each coding agent can use from this directory.",
    )
    parser.add_argument("--version", action="version", version=f"agt-view {__version__}")
    parser.add_argument(
        "keywords",
        nargs="*",
        metavar="keyword",
        help="search all item fields: words in one argument narrow, separate arguments widen",
    )
    parser.add_argument(
        "-a",
        "--agent",
        action="append",
        choices=[adapter.id for adapter in ADAPTERS],
        help="only this agent, even if hidden by default (repeatable)",
    )
    parser.add_argument(
        "-n", "--name", action="append", help="item name contains this text (repeatable)"
    )
    parser.add_argument(
        "-k", "--kind", action="append", choices=[kind.value for kind in Kind], help="(repeatable)"
    )
    parser.add_argument("-s", "--scope", choices=[scope.value for scope in Scope])
    parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="project directory to inspect (default: current directory)",
    )
    parser.add_argument(
        "--all", action="store_true", help="also show agents not both installed and configured"
    )
    parser.add_argument(
        "--detail", action="store_true", help="add each item's path and other details"
    )
    parser.add_argument("--no-color", action="store_true")
    return parser


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


def _in_display_order(items: list[Item]) -> list[Item]:
    scope_order = {scope: rank for rank, scope in enumerate(Scope)}
    kind_order = {kind: rank for rank, kind in enumerate(Kind)}
    return sorted(
        items, key=lambda item: (scope_order[item.scope], kind_order[item.kind], item.name)
    )


def _run(args: argparse.Namespace, context: Context, color: bool) -> None:
    reports = [adapter.scan(context) for adapter in ADAPTERS]
    shown = select_reports(reports, args.agent, args.all)

    # Before filtering, because the agents sharing an item are searched.
    _mark_shared([report for report in reports if report.active or report in shown])

    item_filter = ItemFilter(args.kind, args.scope, args.name, args.keywords)
    sections = []
    for report in shown:
        items = _in_display_order(
            [
                item
                for item in report.items
                if item_filter.matches(item, home=context.home, cwd=context.cwd)
            ]
        )
        # Without a search, an empty section still shows the agent's state.
        if items or not item_filter.is_search:
            sections.append(
                render_report(
                    report,
                    items,
                    home=context.home,
                    cwd=context.cwd,
                    color=color,
                    detail=args.detail,
                )
            )
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
    elif not sections:
        print("No items match.")
    if hidden:
        if sections:
            print()
        print(f"Hidden: {', '.join(hidden)}. Use --all for details.")

    for report in shown:
        for warning in report.warnings:
            print(f"warning: {report.agent}: {clean(warning)}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    # Intermixed parsing allows `browser --kind mcp test`.
    args = parser.parse_intermixed_args(argv)

    cwd = (args.project or Path.cwd()).resolve()
    if not cwd.is_dir():
        parser.error(f"--project is not a directory: {clean(str(args.project))}")
    context = Context(home=Path.home(), cwd=cwd, env=os.environ)

    try:
        _run(args, context, _use_color(args.no_color))
    except BrokenPipeError:
        # The reader closed the pipe, as with `| head`; stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError as error:
        print(f"agt-view: {clean(str(error))}", file=sys.stderr)
        return 1
    return 0
