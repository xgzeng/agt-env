"""Parse JSON with comments and trailing commas."""

import json
import re

_LINE_COMMENT = r"//[^\n]*"
# Spelled out so that a comment can't stretch past its first closing marker.
_BLOCK_COMMENT = r"/\*(?:[^*]|\*(?!/))*\*/"
# Strings match first, so comment-like text inside them is left alone.
_TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"'
    rf"|{_LINE_COMMENT}"
    rf"|{_BLOCK_COMMENT}"
    rf"|,(?=(?:\s|{_LINE_COMMENT}|{_BLOCK_COMMENT})*[}}\]])"
)


def loads_jsonc(text: str) -> object:
    return json.loads(_TOKEN.sub(lambda m: m[0] if m[0][0] == '"' else "", text))
