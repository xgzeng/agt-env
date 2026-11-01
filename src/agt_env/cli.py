"""Command-line entry point."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agt_env import __version__
from agt_env.adapters import ADAPTERS
from agt_env.adapters.base import Context
from agt_env.filters import ItemFilter, select_reports
from agt_env.model import AgentReport, Item, Kind, Scope
from agt_env.render_plain import clean, render_report

_AGENT_ALIASES = {"claude": "claude-code", "agy": "antigravity"}


def _agent_id(value: str) -> str:
    return _AGENT_ALIASES.get(value, value)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agt-env",
        description="List what each coding agent can use from this directory.",
    )
    parser.add_argument("--version", action="version", version=f"agt-env {__version__}")
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
        type=_agent_id,
        choices=[adapter.id for adapter in ADAPTERS],
        help="only this agent, even if hidden by default; also accepts claude and agy (repeatable)",
    )
    parser.add_argument(
        "-n", "--name", action="append", help="item name contains this text (repeatable)"
    )
    parser.add_argument(
        "-k", "--kind", action="append", choices=[kind.value for kind in Kind], help="(repeatable)"
    )
    parser.add_argument(
        "-s",
        "--scope",
        choices=[scope.value for scope in Scope],
        help="shown in the table as P, U, or S",
    )
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


def _use_rich(no_color_flag: bool) -> bool:
    return (
        not no_color_flag
        and "NO_COLOR" not in os.environ
        and sys.stdout.isatty()
        and os.environ.get("TERM") != "dumb"
        # Borders and status marks can't be encoded otherwise.
        and sys.stdout.encoding.lower() in ("utf-8", "utf8")
    )


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
    ordered = sorted(
        items, key=lambda item: (scope_order[item.scope], kind_order[item.kind], item.name)
    )
    # What a plugin brought goes below its row, if that row is shown.
    plugins = {(item.scope, item.name) for item in ordered if item.kind is Kind.PLUGIN}
    bundled: dict[tuple[Scope, object], list[Item]] = {}
    result = []
    for item in ordered:
        owner = (item.scope, item.details.get("plugin"))
        if item.kind is not Kind.PLUGIN and owner in plugins:
            bundled.setdefault(owner, []).append(item)
        else:
            result.append(item)
    return [
        placed
        for item in result
        for placed in (
            [item, *bundled.pop((item.scope, item.name), [])]
            if item.kind is Kind.PLUGIN
            else [item]
        )
    ]


def _run(args: argparse.Namespace, context: Context, use_rich: bool) -> None:
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
            sections.append((report, items))

    if use_rich:
        # Imported here so that plain output doesn't pay for loading Rich.
        from agt_env import render_pretty

        console = render_pretty.make_console()
        render_pretty.print_reports(
            console, sections, home=context.home, cwd=context.cwd, detail=args.detail
        )

        def say(text: str) -> None:
            render_pretty.print_message(console, text)
    else:
        say = print
        if sections:
            rendered = [
                render_report(report, items, home=context.home, cwd=context.cwd, detail=args.detail)
                for report, items in sections
            ]
            print("\n\n".join(rendered))

    hidden = (
        []
        if args.agent or args.all
        else [r.agent for r in reports if (r.installed or r.configured) and not r.active]
    )
    if not shown and not hidden:
        supported = ", ".join(adapter.id for adapter in ADAPTERS)
        say(f"No supported agents detected. Supported agents: {supported}.")
    elif not shown:
        say("No agents are both installed and configured.")
    elif not sections:
        say("No items match.")
    if hidden:
        # A panel's border already sets it apart.
        if sections and not use_rich:
            print()
        say(f"Hidden: {', '.join(hidden)}. Use --all for details.")

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
        _run(args, context, _use_rich(args.no_color))
    except BrokenPipeError:
        # The reader closed the pipe, as with `| head`; stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except OSError as error:
        print(f"agt-env: {clean(str(error))}", file=sys.stderr)
        return 1
    return 0
