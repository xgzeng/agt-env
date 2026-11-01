"""The adapter registry, in rank order."""

from agt_env.adapters.antigravity import Antigravity
from agt_env.adapters.base import Adapter
from agt_env.adapters.claude_code import ClaudeCode
from agt_env.adapters.codex import Codex
from agt_env.adapters.opencode import OpenCode
from agt_env.adapters.pi import Pi

ADAPTERS: tuple[Adapter, ...] = (
    ClaudeCode(),
    Codex(),
    OpenCode(),
    Pi(),
    Antigravity(),
)
