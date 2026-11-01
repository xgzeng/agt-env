"""Safe summaries of MCP server entries. Values that could hold secrets stay out."""

import re
import shlex
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit


def stdio_details(command: object, env_names: object = None) -> dict[str, object]:
    """Describe a local server. Arguments are left out: they can carry credentials."""
    safe_command = without_credentials(command) if isinstance(command, str) else command
    return {"transport": "stdio", "command": safe_command, "env": _names(env_names)}


def remote_details(transport: str, url: object, header_names: object = None) -> dict[str, object]:
    return {"transport": transport, "url": safe_url(url), "headers": _names(header_names)}


def safe_url(url: object) -> str | None:
    """Drop credentials, query, and fragment from a URL."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
        host = parts.netloc.rpartition("@")[2]
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return None


_MASK = "***"
_QUOTED_OR_WORD = r"""('[^']*'|"[^"]*"|\S+)"""
_SECRET_WORDS = r"(?:token|secret|password|passwd|pass|api[-_]?key|apikey|key|auth|credential)"
_SECRET_PATTERNS = (
    # A URL's query, which often carries a key.
    (re.compile(r"""(://[^\s'"?]+)\?[^\s'"]*"""), rf"\1?{_MASK}"),
    # Header lines, such as `Authorization: Bearer x` and `X-Api-Key: x`.
    (
        re.compile(
            rf"""(?i)\b(authorization|cookie|[\w-]*{_SECRET_WORDS}[\w-]*)(\s*:\s*)[^'"\n]+"""
        ),
        rf"\1\2{_MASK}",
    ),
    (re.compile(r"""(?i)\b(bearer|basic)\s+[^\s'"]+"""), rf"\1 {_MASK}"),
    # Flags named like a secret: `--token x`, `--api-key=x`.
    (
        re.compile(rf"(?i)(--?[\w-]*{_SECRET_WORDS}[\w-]*)(=|\s+){_QUOTED_OR_WORD}"),
        rf"\1\2{_MASK}",
    ),
    # `curl -u user:password`.
    (re.compile(r"(\s(?:-u|--user)[=\s]\s*)\S+"), rf"\1{_MASK}"),
    # Assignments, such as `TOKEN=x cmd`.
    (re.compile(rf"(?<![\w-])([A-Za-z_]\w*)={_QUOTED_OR_WORD}"), rf"\1={_MASK}"),
    # Well-known token shapes.
    (
        re.compile(
            r"\b(?:(?:sk|pk|rk)-[\w-]{8,}|gh[pousr]_\w{8,}|github_pat_\w+|xox[a-z]-[\w-]+"
            r"|AKIA[0-9A-Z]{12,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)"
        ),
        _MASK,
    ),
    # Anything long and unbroken that isn't part of a path.
    (re.compile(r"(?<![\w/.-])[A-Za-z0-9+_-]{32,}={0,2}(?![\w/.-])"), _MASK),
)


def masked(text: str) -> str:
    """Hide what looks like a secret in shell or prompt text, on one line.

    This recognizes common forms, so it can't promise to catch everything. A bare argument such
    as `tool hunter2` looks like any other word.
    """
    text = " ".join(without_credentials(text).split())
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def command_name(text: str) -> str | None:
    """Return only the program of a shell command, or None if it can't be told safely."""
    try:
        words = shlex.split(text)
    except ValueError:
        return None
    # A first word with `=` is a variable assignment, which can hold a secret.
    return without_credentials(words[0]) if words and "=" not in words[0] else None


def without_credentials(text: str) -> str:
    """Remove `user:password@` from any URL inside a package source or similar."""
    return re.sub(r"(://)[^/@\s]+@", r"\1", text)


def _names(values: object) -> list[str]:
    if isinstance(values, Mapping):
        return sorted(str(key) for key in values)
    return []
