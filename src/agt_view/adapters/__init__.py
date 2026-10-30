"""The adapter registry, in rank order."""

from agt_view.adapters.base import Adapter
from agt_view.adapters.claude_code import ClaudeCode
from agt_view.adapters.codex import Codex

ADAPTERS: tuple[Adapter, ...] = (
    ClaudeCode(),
    Codex(),
)
