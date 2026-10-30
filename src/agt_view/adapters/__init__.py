"""The adapter registry, in rank order."""

from agt_view.adapters.antigravity import Antigravity
from agt_view.adapters.base import Adapter
from agt_view.adapters.claude_code import ClaudeCode
from agt_view.adapters.codex import Codex
from agt_view.adapters.opencode import OpenCode
from agt_view.adapters.pi import Pi

ADAPTERS: tuple[Adapter, ...] = (
    ClaudeCode(),
    Codex(),
    OpenCode(),
    Pi(),
    Antigravity(),
)
