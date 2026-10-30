import json

import pytest

from agt_view.jsonc import loads_jsonc


def test_comments_and_trailing_commas():
    text = """{
      // line comment
      "a": 1, /* block
      comment */
      "b": [1, 2, /* last */ ],
      "c": {"d": true, // note
      },
    }"""
    assert loads_jsonc(text) == {"a": 1, "b": [1, 2], "c": {"d": True}}


def test_comment_like_text_in_strings_is_kept():
    data = {"url": "https://x.test/a", "glob": "/* not a comment */", "quote": 'a",b', "c": ",]"}
    assert loads_jsonc(json.dumps(data)) == data


@pytest.mark.parametrize("text", ["{ not json", '{"a": 1 /* unterminated', ""])
def test_invalid_input_raises(text):
    with pytest.raises(ValueError):
        loads_jsonc(text)
