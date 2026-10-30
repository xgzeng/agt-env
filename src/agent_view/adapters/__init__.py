"""The adapter registry, in rank order."""

from agent_view.adapters.base import Adapter
from agent_view.adapters.claude_code import ClaudeCode
from agent_view.adapters.codex import Codex

ADAPTERS: tuple[Adapter, ...] = (
    ClaudeCode(),
    Codex(),
)
