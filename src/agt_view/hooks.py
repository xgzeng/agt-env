"""Hook entries in the `event -> matcher group -> handlers` form, shared by adapters.

Hook handlers run shell text, which can carry credentials, so what a hook does is shown only after
`masked`, which can't promise to catch everything. See mcp.py.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agt_view.mcp import command_name, masked, safe_url
from agt_view.model import Finding, Item, Kind, Scope, Status

INVALID_HOOK = Finding("invalid hook entry")


@dataclass
class Handler:
    event: str
    matcher: str | None
    group: int
    index: int
    entry: object

    @property
    def name(self) -> str:
        return f"{self.event}:{self.matcher}" if self.matcher else self.event


def handlers(events: object) -> Iterator[Handler]:
    """Walk the nesting. Entries that aren't shaped like this are skipped."""
    for event, groups in events.items() if isinstance(events, dict) else ():
        for position, group in enumerate(groups) if isinstance(groups, list) else ():
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            entries = group.get("hooks")
            for index, entry in enumerate(entries) if isinstance(entries, list) else ():
                yield Handler(
                    event, matcher if isinstance(matcher, str) else None, position, index, entry
                )


def handler_details(entry: object) -> dict[str, object] | None:
    """Return a safe summary, or None if the handler can't run."""
    if not isinstance(entry, dict):
        return None
    kind = entry.get("type", "command")
    if kind == "command" and isinstance(entry.get("command"), str):
        return {"handler": kind, "command": command_name(entry["command"])}
    if kind == "http" and isinstance(entry.get("url"), str):
        return {"handler": kind, "url": safe_url(entry["url"])}
    server, tool = entry.get("server"), entry.get("tool")
    if kind == "mcp_tool" and isinstance(server, str) and isinstance(tool, str):
        return {"handler": kind, "tool": f"{server}/{tool}"}
    if kind in ("prompt", "agent") and isinstance(entry.get("prompt"), str):
        return {"handler": kind}
    return None


def handler_content(entry: object) -> str:
    """Say what a handler does, with anything that looks like a secret hidden."""
    if not isinstance(entry, dict):
        return ""
    for key in ("command", "prompt"):
        if isinstance(entry.get(key), str):
            return masked(entry[key])
    if isinstance(entry.get("url"), str):
        return safe_url(entry["url"]) or ""
    server, tool = entry.get("server"), entry.get("tool")
    return f"{server}/{tool}" if isinstance(server, str) and isinstance(tool, str) else ""


def hook_item(
    agent: str,
    handler: Handler,
    scope: Scope,
    status: Status,
    path: Path,
    native_kind: str = "hook",
) -> Item:
    """Build a hook item. A handler that can't run is broken, whatever else is true of it."""
    details = handler_details(handler.entry)
    return Item(
        name=handler.name,
        kind=Kind.HOOK,
        native_kind=native_kind,
        agent=agent,
        scope=scope,
        status=Status.BROKEN if details is None else status,
        description=handler_content(handler.entry),
        source_path=path,
        resolved_path=Path(os.path.realpath(path)),
        details={"event": handler.event, "matcher": handler.matcher, **(details or {})},
        findings=[] if details is not None else [INVALID_HOOK],
    )
