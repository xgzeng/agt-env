"""Which agents and items to show. Different filters narrow each other."""

from dataclasses import dataclass, field
from pathlib import Path

from agt_view.model import AgentReport, Item
from agt_view.render_plain import clean, display_path, finding_text, item_path, scope_letter


def select_reports(
    reports: list[AgentReport], agents: list[str] | None, include_inactive: bool
) -> list[AgentReport]:
    """Named agents are shown even if they would be hidden by default."""
    if agents:
        return [report for report in reports if report.agent in agents]
    if include_inactive:
        return [report for report in reports if report.installed or report.configured]
    return [report for report in reports if report.active]


def _keyword_groups(keywords: list[str]) -> list[list[str]]:
    """Each argument is a group of words that must all match. Any one group is enough."""
    groups = [keyword.casefold().split() for keyword in keywords]
    return [group for group in groups if group]


def _search_text(item: Item, *, home: Path, cwd: Path) -> str:
    """Everything printed for an item. Paths are in display form, so that a word in the
    home or project directory's own path doesn't match every item."""
    values = [
        item.name,
        item.description,
        f"{item.scope}/{item.kind}",
        f"{scope_letter(item.scope)}/{item.kind}",
        item.native_kind,
        item.agent,
        item.status,
        item_path(item, home=home, cwd=cwd),
        display_path(item.resolved_path, home=home, cwd=cwd),
        *item.shared_with,
        *[finding_text(finding, home=home, cwd=cwd) for finding in item.findings],
    ]
    for value in item.details.values():
        if isinstance(value, list):
            values.extend(str(entry) for entry in value)
        elif value is not None:
            values.append(str(value))
    return clean(" ".join(values)).casefold()


@dataclass(frozen=True)
class ItemFilter:
    kinds: list[str] | None = None
    scope: str | None = None
    names: list[str] | None = None
    keywords: list[str] = field(default_factory=list)

    @property
    def is_search(self) -> bool:
        """Whether a name or keyword was given. An empty keyword doesn't count."""
        return bool(self.names or _keyword_groups(self.keywords))

    def matches(self, item: Item, *, home: Path, cwd: Path) -> bool:
        if self.kinds and item.kind not in self.kinds:
            return False
        if self.scope and item.scope != self.scope:
            return False
        if self.names:
            item_name = clean(item.name).casefold()
            if not any(name.casefold() in item_name for name in self.names):
                return False
        groups = _keyword_groups(self.keywords)
        if not groups:
            return True
        text = _search_text(item, home=home, cwd=cwd)
        return any(all(word in text for word in group) for group in groups)
