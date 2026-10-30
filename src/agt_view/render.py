"""Terminal output. Everything printed from inspected files passes through clean()."""

import re
import unicodedata
from pathlib import Path

from agt_view.model import AgentReport, Item, Status

_ESCAPE_SEQUENCE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\^_][^\x1b]*(?:\x1b\\)?|.)?"
)
_COLUMNS = ("KIND", "NAME", "SCOPE", "STATUS", "SHARED WITH", "PATH")
_BOLD, _RED, _DIM, _RESET = "\x1b[1m", "\x1b[31m", "\x1b[2m", "\x1b[0m"


def clean(text: str) -> str:
    """Remove terminal escape sequences and characters that could spoof the display."""
    text = _ESCAPE_SEQUENCE.sub("", text)
    return "".join(
        " " if char in "\t\n\r" else "?" if unicodedata.category(char) == "Cs" else char
        for char in text
        if char in "\t\n\r" or unicodedata.category(char) not in ("Cc", "Cf")
    )


def display_path(path: Path, *, home: Path, cwd: Path) -> str:
    if cwd != home and path.is_relative_to(cwd):
        return f"./{path.relative_to(cwd)}"
    if path.is_relative_to(home):
        return f"~/{path.relative_to(home)}"
    return str(path)


def item_path(item: Item, *, home: Path, cwd: Path) -> str:
    path = display_path(item.source_path, home=home, cwd=cwd)
    return f"{path} (symlink)" if item.is_symlink else path


def header(report: AgentReport, *, home: Path, cwd: Path) -> str:
    location = (
        display_path(report.config_path, home=home, cwd=cwd) if report.config_path else ""
    )
    if report.active:
        note = f" (set by {report.override_var})" if report.override_var else ""
        return f"{location}{note}"
    if report.configured:
        return f"not installed, configured at {location}"
    return f"{'installed' if report.installed else 'not installed'}, not configured"


def render_report(
    report: AgentReport, items: list[Item], *, home: Path, cwd: Path, color: bool
) -> str:
    name = clean(report.agent)
    state = clean(header(report, home=home, cwd=cwd))
    lines = [f"{_BOLD}{name}{_RESET}  {state}" if color else f"{name}  {state}"]
    if not items:
        lines.append("  no items")
        return "\n".join(lines)

    rows = [
        (
            clean(item.kind),
            clean(item.name),
            clean(item.scope),
            clean(item.status),
            clean(", ".join(item.shared_with)) or "-",
            clean(item_path(item, home=home, cwd=cwd)),
        )
        for item in items
    ]
    widths = [max(len(row[i]) for row in [_COLUMNS, *rows]) for i in range(len(_COLUMNS))]

    def format_row(row: tuple[str, ...], item: Item | None = None) -> str:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        if color and item and item.status is Status.BROKEN:
            cells[3] = f"{_RED}{cells[3]}{_RESET}"
        elif color and item and item.status is not Status.ENABLED:
            cells[3] = f"{_DIM}{cells[3]}{_RESET}"
        return ("  " + "  ".join(cells)).rstrip()

    lines.append(format_row(_COLUMNS))
    lines.extend(format_row(row, item) for row, item in zip(rows, items))
    return "\n".join(lines)
