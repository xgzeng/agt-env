"""Safe summaries of MCP server entries. Values that could hold secrets stay out."""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit


def stdio_details(command: object, env_names: object = None) -> dict[str, object]:
    """Describe a local server. Arguments are left out: they can carry credentials."""
    return {"transport": "stdio", "command": command, "env": _names(env_names)}


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


def without_credentials(text: str) -> str:
    """Remove `user:password@` from any URL inside a package source or similar."""
    return re.sub(r"(://)[^/@\s]+@", r"\1", text)


def _names(values: object) -> list[str]:
    if isinstance(values, Mapping):
        return sorted(str(key) for key in values)
    return []
