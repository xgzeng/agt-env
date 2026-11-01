"""Plain text output, and the text both outputs share. Rich output is in render_pretty.py.

Everything printed from inspected files passes through clean().
"""

import re
import unicodedata
from pathlib import Path

from agt_view.model import AgentReport, Finding, Item, Scope

_ESCAPE_SEQUENCE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\^_][^\x1b]*(?:\x1b\\)?|.)?"
)
COLUMNS = ("SCOPE/KIND", "NAME", "STATUS")
DETAIL_COLUMNS = COLUMNS[:2]
_DETAIL_LABELS = {
    "plugin": "Plugin",
    "event": "Event",
    "matcher": "Matcher",
    "handler": "Handler",
    "tool": "Tool",
    "transport": "Transport",
    "command": "Command",
    "url": "URL",
    "env": "Env names",
    "headers": "Header names",
    "version": "Version",
}


def clean(text: str) -> str:
    """Remove terminal escape sequences and characters that could spoof the display."""
    if text.isascii() and text.isprintable():
        return text  # Nothing to remove, and checking each character is slow.
    text = _ESCAPE_SEQUENCE.sub("", text)
    return "".join(
        " " if char in "\t\n\r" else "?" if unicodedata.category(char) == "Cs" else char
        for char in text
        if char in "\t\n\r" or unicodedata.category(char) not in ("Cc", "Cf")
    )


def scope_letter(scope: Scope) -> str:
    return scope[0].upper()


def display_path(path: Path, *, home: Path, cwd: Path) -> str:
    if cwd != home and path.is_relative_to(cwd):
        return f"./{path.relative_to(cwd)}"
    if path.is_relative_to(home):
        return f"~/{path.relative_to(home)}"
    return str(path)


def finding_text(finding: Finding, *, home: Path, cwd: Path) -> str:
    parts = [clean(finding.text)]
    if finding.path:
        parts.append(clean(display_path(finding.path, home=home, cwd=cwd)))
    return " ".join(parts)


def item_path(item: Item, *, home: Path, cwd: Path) -> str:
    path = clean(display_path(item.source_path, home=home, cwd=cwd))
    return f"{path} (symlink)" if item.is_symlink else path


def header(report: AgentReport, *, home: Path, cwd: Path) -> str:
    location = display_path(report.config_path, home=home, cwd=cwd) if report.config_path else ""
    if report.active:
        note = f" (set by {report.override_var})" if report.override_var else ""
        return f"{location}{note}"
    if report.configured:
        return f"not installed, configured at {location}"
    return f"{'installed' if report.installed else 'not installed'}, not configured"


def detail_fields(item: Item, *, home: Path, cwd: Path) -> list[tuple[str, str]]:
    """Everything the columns don't show. Fields with no value are left out."""
    fields: list[tuple[str, str]] = [("Status", clean(item.status))]
    if item.native_kind != item.kind:
        fields.append(("Agent's term", clean(item.native_kind)))
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
    for finding in item.findings:
        fields.append(("Finding", finding_text(finding, home=home, cwd=cwd)))
    return fields


def _detail_lines(item: Item, *, home: Path, cwd: Path) -> list[str]:
    fields = detail_fields(item, home=home, cwd=cwd)
    width = max(len(label) for label, _ in fields) + 1
    return [f"{f'{label}:'.ljust(width)}  {value}" for label, value in fields]


def render_report(
    report: AgentReport,
    items: list[Item],
    *,
    home: Path,
    cwd: Path,
    detail: bool = False,
) -> str:
    name = clean(report.agent)
    state = clean(header(report, home=home, cwd=cwd))
    lines = [f"{name}  {state}"]
    if not items:
        lines.append("  no items")
        return "\n".join(lines)

    # With details, the status is one of them.
    columns = DETAIL_COLUMNS if detail else COLUMNS
    rows = [
        (
            f"{scope_letter(item.scope)}/{clean(item.kind)}",
            clean(item.name),
            clean(item.status),
        )[: len(columns)]
        for item in items
    ]
    widths = [max(len(row[i]) for row in [columns, *rows]) for i in range(len(columns))]
    margin = "" if detail else "  "

    def format_row(row: tuple[str, ...]) -> str:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        return (margin + "  ".join(cells)).rstrip()

    # Details start at the NAME column, and findings where a description column would.
    indent = " " * (len(margin) + widths[0] + 2)
    finding_indent = " " * (len(margin) + sum(widths) + 2 * len(widths))
    table_width = sum(widths) + 2 * (len(widths) - 1)
    separator = "-" * table_width
    lines.append(format_row(columns))
    for row, item in zip(rows, items):
        if detail:
            lines.append(separator)
        lines.append(format_row(row))
        if detail:
            lines.extend(f"{indent}{line}" for line in _detail_lines(item, home=home, cwd=cwd))
        else:
            lines.extend(
                f"{finding_indent}! {finding_text(finding, home=home, cwd=cwd)}"
                for finding in item.findings
            )
    return "\n".join(lines)
