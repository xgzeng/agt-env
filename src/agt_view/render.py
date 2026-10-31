"""Terminal output. Everything printed from inspected files passes through clean()."""

import re
import unicodedata
from pathlib import Path

from agt_view.model import AgentReport, Item, Status

_ESCAPE_SEQUENCE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\^_][^\x1b]*(?:\x1b\\)?|.)?"
)
_COLUMNS = ("KIND", "NAME", "SCOPE", "STATUS", "SHARED", "PATH")
_MAX_LIST_PATH_LENGTH = 48
_ELLIPSIS = "..."
_BOLD, _RED, _DIM, _RESET = "\x1b[1m", "\x1b[31m", "\x1b[2m", "\x1b[0m"
_DETAIL_LABELS = {
    "transport": "Transport",
    "command": "Command",
    "url": "URL",
    "env": "Env names",
    "headers": "Header names",
    "version": "Version",
}


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


def _shorten_middle(text: str, limit: int) -> str:
    """Cut the middle, keeping more of the end, which names the item."""
    if len(text) <= limit:
        return text
    kept = limit - len(_ELLIPSIS)
    start = kept // 3
    return f"{text[:start]}{_ELLIPSIS}{text[start - kept :]}"


def item_path(item: Item, *, home: Path, cwd: Path, limit: int | None = None) -> str:
    path = clean(display_path(item.source_path, home=home, cwd=cwd))
    if limit is not None:
        path = _shorten_middle(path, limit)
    return f"{path} (symlink)" if item.is_symlink else path


def header(report: AgentReport, *, home: Path, cwd: Path) -> str:
    location = display_path(report.config_path, home=home, cwd=cwd) if report.config_path else ""
    if report.active:
        note = f" (set by {report.override_var})" if report.override_var else ""
        return f"{location}{note}"
    if report.configured:
        return f"not installed, configured at {location}"
    return f"{'installed' if report.installed else 'not installed'}, not configured"


def _status_text(status: Status, width: int, color: bool) -> str:
    text = clean(status).ljust(width)
    if color and status is Status.BROKEN:
        return f"{_RED}{text}{_RESET}"
    if color and status is not Status.ENABLED:
        return f"{_DIM}{text}{_RESET}"
    return text


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
            str(len(item.shared_with)) if item.shared_with else "-",
            item_path(item, home=home, cwd=cwd, limit=_MAX_LIST_PATH_LENGTH),
        )
        for item in items
    ]
    widths = [max(len(row[i]) for row in [_COLUMNS, *rows]) for i in range(len(_COLUMNS))]

    def format_row(row: tuple[str, ...], item: Item | None = None) -> str:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        if item:
            cells[3] = _status_text(item.status, widths[3], color)
        return ("  " + "  ".join(cells)).rstrip()

    lines.append(format_row(_COLUMNS))
    lines.extend(format_row(row, item) for row, item in zip(rows, items))
    return "\n".join(lines)


def render_item(report: AgentReport, item: Item, *, home: Path, cwd: Path, color: bool) -> str:
    """Show everything known about one item. Fields with no value are left out."""
    title = f"{clean(item.name)}  ({clean(item.kind)}, {clean(report.agent)})"
    fields: list[tuple[str, str]] = []
    if item.native_kind != item.kind:
        fields.append(("Agent's term", clean(item.native_kind)))
    fields.append(("Scope", clean(item.scope)))
    fields.append(("Status", _status_text(item.status, 0, color)))
    if not report.active:
        fields.append(("Agent", clean(header(report, home=home, cwd=cwd))))
    if item.description:
        fields.append(("Description", clean(item.description)))
    fields.append(("Path", item_path(item, home=home, cwd=cwd)))
    if item.resolved_path != item.source_path:
        fields.append(("Real path", clean(display_path(item.resolved_path, home=home, cwd=cwd))))
    if item.shared_with:
        fields.append(("Shared with", clean(", ".join(item.shared_with))))
    for key, label in _DETAIL_LABELS.items():
        value = item.details.get(key)
        if isinstance(value, list):
            value = ", ".join(str(entry) for entry in value)
        if value:
            fields.append((label, clean(str(value))))

    width = max(len(label) for label, _ in fields) + 1
    lines = [f"{_BOLD}{title}{_RESET}" if color else title]
    lines.extend(f"  {f'{label}:'.ljust(width)}  {value}" for label, value in fields)
    return "\n".join(lines)
