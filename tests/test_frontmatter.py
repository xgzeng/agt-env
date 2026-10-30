import pytest

from agent_view.frontmatter import parse_frontmatter


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain text", "plain text"),
        ("plain text # comment", "plain text"),
        ("'it''s quoted'", "it's quoted"),
        ('"line\\nbreak \\"q\\""', 'line\nbreak "q"'),
        (">\n  folded\n  text", "folded text"),
        (">-\n  folded\n\n  text", "folded text"),
        ("|\n  one\n  two", "one\ntwo"),
        ("plain\n  continued", "plain continued"),
    ],
)
def test_scalar_styles(raw, expected):
    fields, _ = parse_frontmatter(f"---\ndescription: {raw}\nname: x\n---\nbody")
    assert fields == {"description": expected, "name": "x"}


def test_returns_body_after_frontmatter():
    assert parse_frontmatter("---\nname: x\n---\none\ntwo")[1] == "one\ntwo"


@pytest.mark.parametrize("text", ["no frontmatter", "\n---\nname: x\n---\n", "---\nname: x\n", ""])
def test_absent_or_unterminated_frontmatter(text):
    assert parse_frontmatter(text) == (None, text)


def test_non_scalar_values_do_not_break_parsing():
    text = "---\nallowed-tools:\n  - Read\n  - Write\nmetadata: {a: 1}\nname: x\n---\n"
    assert parse_frontmatter(text)[0]["name"] == "x"
