"""Rich output for color terminals. Plain output is in render_plain.py.

Every value goes in as a Text, because Rich parses a plain string as markup, and inspected text
is untrusted. It still passes through clean(), or through a helper in render_plain.py that does.
"""

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from rich.cells import cell_len
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agt_env.model import AgentReport, Item, Kind, Scope, Status
from agt_env.render_plain import (
    clean,
    detail_fields,
    finding_text,
    header,
)

_STATUS_MARKS = {
    Status.ENABLED: ("●", "green"),
    Status.DISABLED: ("○", "dim"),
    Status.PENDING: ("◐", "yellow"),
    Status.BROKEN: ("✖", "bold red"),
    Status.UNKNOWN: ("?", "yellow"),
    Status.SHADOWED: ("◌", "yellow"),
}
_SCOPE_STYLES = {Scope.PROJECT: "magenta", Scope.USER: "cyan", Scope.SYSTEM: "blue"}
# Skills are most of what's listed, so they keep the default color and the rest stand out.
_KIND_COLORS = {
    Kind.MCP: "bright_blue",
    Kind.PLUGIN: "bright_magenta",
    Kind.HOOK: "dark_orange",
    Kind.SUBAGENT: "bright_cyan",
    Kind.COMMAND: "bright_green",
}
_DETAIL_COLUMNS = ("SCOPE", "NAME")
_COLUMNS = (*_DETAIL_COLUMNS, "S")  # The status column only holds a mark.
_SCOPE_WORDS = {Scope.PROJECT: "proj", Scope.USER: "user", Scope.SYSTEM: "sys"}
_COLUMN_GAP = 2
_PANEL_MARGIN = 4  # A border and a space on each side.
_MIN_NAME_WIDTH = 8
_DESCRIPTION_COLUMN = "DESCRIPTION"
_MIN_DESCRIPTION_WIDTH = 12


def make_console() -> Console:
    # The caller has already decided on Rich, so Rich doesn't get to disagree. Turning off parsing
    # here too means a stray string can't be read as markup.
    return Console(force_terminal=True, markup=False, emoji=False, highlight=False)


def _name_cell(item: Item) -> Text:
    """Highlight the item's own name, not the plugin or marketplace around it."""
    if item.status is Status.SHADOWED:
        return Text(clean(item.name), style="dim")
    color = _KIND_COLORS.get(item.kind)
    own_style = f"bold {color}" if color else "bold"
    if not (item.name_prefix or item.name_suffix):
        return Text(clean(item.name), style=own_style)
    own = item.name.removeprefix(item.name_prefix).removesuffix(item.name_suffix)
    return Text.assemble(
        (clean(item.name_prefix), "dim"), (clean(own), own_style), (clean(item.name_suffix), "dim")
    )


def _cells(item: Item) -> tuple[Text, Text, Text]:
    mark, style = _STATUS_MARKS[item.status]
    return (
        Text(_SCOPE_WORDS[item.scope], style=_SCOPE_STYLES[item.scope]),
        _name_cell(item),
        Text(mark, style=style),
    )


def _kind_label(item: Item) -> str:
    return item.kind.upper()


def _description_text(item: Item) -> str:
    """The kind, then the description. The kind is here so that every row says what it is."""
    label = _kind_label(item)
    return f"{label}: {clean(item.description)}" if item.description else label


def _description(item: Item) -> Text:
    return Text(_description_text(item), style="dim", no_wrap=True, overflow="ellipsis")


def _line(cells: Iterable[Text], widths: list[int]) -> Text:
    line = Text(no_wrap=True)
    for cell, width in zip(cells, widths):
        cell.truncate(width, overflow="ellipsis")  # Only a description can be too long.
        line.append_text(cell)
        line.append(" " * (width - cell.cell_len + _COLUMN_GAP))
    return line


def _rows(
    rows: list[tuple[Text, ...]] | list[list[Text]], widths: list[int], width: int
) -> RenderableType:
    """Lines of the table. Rich has no column spans, so these line up through fixed widths."""
    used = sum(widths) + _COLUMN_GAP * (len(widths) - 1) + _PANEL_MARGIN
    if used <= width and all(row[1].cell_len <= widths[1] for row in rows):
        # Rich's table layout is most of the time taken, and only folding needs it.
        return Text("\n", no_wrap=True).join(_line(row, widths) for row in rows)
    table = Table.grid(padding=(0, _COLUMN_GAP, 0, 0))
    for column_width in widths:
        table.add_column(width=column_width, overflow="fold")
    for cells in rows:
        table.add_row(*cells)
    return table


def _details(item: Item, *, home: Path, cwd: Path, indent: int) -> Padding:
    table = Table.grid(padding=(0, _COLUMN_GAP, 0, 0))
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    mark, style = _STATUS_MARKS[item.status]
    fields = detail_fields(item, home=home, cwd=cwd)
    if not item.description:
        fields.insert(1, ("Description", ""))
    for label, value in fields:
        if label == "Status":
            table.add_row(Text(label, style="dim"), Text(f"{mark} {value}", style=style))
        elif label == "Description":
            table.add_row(Text(label, style="dim"), Text(_description_text(item)))
        else:
            table.add_row(Text(label, style="yellow" if label == "Finding" else "dim"), Text(value))
    return Padding(table, (0, 0, 0, indent))


def _findings(item: Item, *, home: Path, cwd: Path, indent: int) -> Padding:
    style = "bold red" if item.status is Status.BROKEN else "bold yellow"
    lines = [
        Text.assemble(("! ", style), finding_text(finding, home=home, cwd=cwd), overflow="fold")
        for finding in item.findings
    ]
    return Padding(Group(*lines), (0, 0, 0, indent))


def _items(
    items: list[Item], *, home: Path, cwd: Path, detail: bool, width: int
) -> list[RenderableType]:
    # With details, the status is one of them.
    columns = _DETAIL_COLUMNS if detail else _COLUMNS
    rows = [_cells(item)[: len(columns)] for item in items]
    heads = [Text(column, style="bold dim") for column in columns]
    widths = [max(cell_len(row[i].plain) for row in [heads, *rows]) for i in range(len(heads))]
    # Only the name gives up width on a narrow terminal, so the other columns stay readable. A
    # row's kind is in its description, so that always gets room for the kind.
    kind_width = max(cell_len(_kind_label(item)) for item in items) + 1  # One for the "…".
    others = sum(widths) - widths[1] + _COLUMN_GAP * (len(widths) - 1)
    if not detail:
        others += kind_width + _COLUMN_GAP
    widths[1] = max(min(widths[1], width - _PANEL_MARGIN - others), _MIN_NAME_WIDTH)

    if not detail:
        head = Text(_DESCRIPTION_COLUMN, style="bold dim", no_wrap=True, overflow="ellipsis")
        cut = [_description(item) for item in items]
        room = width - _PANEL_MARGIN - sum(widths) - _COLUMN_GAP * len(widths)
        if room >= kind_width:
            longest = max(cell_len(text.plain) for text in cut)
            widths.append(min(max(longest, cell_len(head.plain)), room))
            heads.append(head)
            rows = [(*row, text) for row, text in zip(rows, cut)]

    separator = Rule(style="dim")
    lines: list[RenderableType] = [_rows([heads], widths, width)]
    # Details start at the NAME column.
    indent = widths[0] + _COLUMN_GAP
    if not detail:
        # Findings start at the DESCRIPTION column, unless that leaves them too little room.
        start = sum(widths[:3]) + _COLUMN_GAP * 3
        if width - _PANEL_MARGIN - start >= _MIN_DESCRIPTION_WIDTH:
            indent = start
        lines.append(separator)
        pending: list[tuple[Text, ...]] = []
        for row, item in zip(rows, items):
            pending.append(row)
            if item.findings:
                lines.append(_rows(pending, widths, width))
                lines.append(_findings(item, home=home, cwd=cwd, indent=indent))
                pending = []
        if pending:
            lines.append(_rows(pending, widths, width))
        return lines
    for row, item in zip(rows, items):
        lines.extend(
            [
                separator,
                _rows([row], widths, width),
                _details(item, home=home, cwd=cwd, indent=indent),
            ]
        )
    return lines


def _counts(items: list[Item]) -> Text | None:
    counts = Counter(item.kind for item in items)
    parts = [f"{kind} {counts[kind]}" for kind in Kind if counts[kind]]
    return Text(" · ".join(parts), style="dim") if parts else None


def _panel(
    report: AgentReport, items: list[Item], *, home: Path, cwd: Path, detail: bool, width: int
) -> Panel:
    state = Text(
        clean(header(report, home=home, cwd=cwd)),
        style="dim" if report.active else "yellow",
        overflow="fold",
    )
    body = (
        _items(items, home=home, cwd=cwd, detail=detail, width=width)
        if items
        else [Text("no items", style="dim")]
    )
    return Panel(
        Group(state, *body),
        title=Text(clean(report.agent), style="bold"),
        title_align="left",
        subtitle=_counts(items),
        subtitle_align="right",
        border_style="cyan" if report.active else "dim",
    )


def print_reports(
    console: Console,
    sections: list[tuple[AgentReport, list[Item]]],
    *,
    home: Path,
    cwd: Path,
    detail: bool,
) -> None:
    for report, items in sections:
        console.print(_panel(report, items, home=home, cwd=cwd, detail=detail, width=console.width))


def print_message(console: Console, text: str) -> None:
    console.print(Text(text, style="dim", overflow="fold"))
