"""A small parser for the scalar fields of SKILL.md frontmatter.

Only string scalars are needed, so this avoids a YAML dependency. Lists and
mappings are ignored rather than rejected, and the input is never executed.
"""

import re

_KEY = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$")
_BLOCK = re.compile(r"^([|>])[+-]?\d?[+-]?$")


def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Return the frontmatter fields, or None if absent, and the body."""
    lines = text.removeprefix("﻿").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None, text
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            return _parse_fields(lines[1:end]), "\n".join(lines[end + 1 :])
    return None, text


def _parse_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(lines):
        match = _KEY.match(lines[index])
        index += 1
        if not match:
            continue
        continuation: list[str] = []
        while index < len(lines) and (not lines[index].strip() or lines[index][0] in " \t"):
            continuation.append(lines[index])
            index += 1
        fields[match[1]] = _scalar((match[2] or "").strip(), continuation)
    return fields


def _scalar(value: str, continuation: list[str]) -> str:
    stripped = [line.strip() for line in continuation]
    block = _BLOCK.match(value)
    if block:
        separator = "\n" if block[1] == "|" else " "
        return separator.join(line for line in stripped if line)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), value[1:-1])
    plain = re.sub(r"\s+#.*$", "", value)
    return " ".join(part for part in [plain, *stripped] if part)
