import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from JoinAgent import LLMParser


def test_parse_standard_json_literals():
    parser = LLMParser()
    parsed_dict = parser.parse_dict(
        '{"accepted": true, "rejected": false, "reason": null}'
    )
    parsed_list = parser.parse_list('[true, false, null]')
    assert parsed_dict == {
        "accepted": True,
        "rejected": False,
        "reason": None,
    }
    assert parsed_list == [True, False, None]


def test_parse_dict_preserves_single_backslash_latex():
    parser = LLMParser()
    payload = '{"content": "\\textstyle \\mu \\neq \\psi \\in \\operatorname{Irr}(H)"}'
    parsed = parser.parse_dict(payload)
    assert parsed["content"] == r"\textstyle \mu \neq \psi \in \operatorname{Irr}(H)"


def test_parse_dict_does_not_expand_already_escaped_latex():
    parser = LLMParser()
    payload = '{"content": "\\\\psi and \\\\operatorname{CF}(H,A)"}'
    parsed = parser.parse_dict(payload)
    assert parsed["content"] == r"\psi and \operatorname{CF}(H,A)"


def test_parse_dict_preserves_braces_mid_and_multiline_content():
    parser = LLMParser()
    payload = "{\"content\": \"line1\nline2 \\{ \\chi_i \\mid i \\in I \\}\"}"
    parsed = parser.parse_dict(payload)
    assert parsed["content"] == "line1\nline2 " + r"\{ \chi_i \mid i \in I \}"


def test_parse_list_matches_dict_behavior():
    parser = LLMParser()
    payload = '["\\\\psi", "\\textstyle \\neq", "\\{ \\chi_i \\mid i \\in I \\}"]'
    parsed = parser.parse_list(payload)
    assert parsed == [
        r"\psi",
        r"\textstyle \neq",
        r"\{ \chi_i \mid i \in I \}",
    ]


def test_parse_pads_escapes_once_without_expanding_existing_pairs():
    parser = LLMParser()
    payload = '{"pos1": "line1\nline2 \\textstyle \\neq \\\\psi"}'
    parsed = parser.parse_pads(payload)
    assert r'\n' in parsed
    assert r'\\textstyle \\neq \\psi' in parsed
    assert r'\\\\textstyle' not in parsed
    assert r'\\\\\\psi' not in parsed


def test_parse_dict_does_not_emit_syntaxwarning_for_raw_latex():
    parser = LLMParser()
    payload = '{"content": "\\( x \\in A \\)"}'
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", SyntaxWarning)
        parsed = parser.parse_dict(payload)
    assert parsed["content"] == r"\( x \in A \)"
    assert not any(issubclass(item.category, SyntaxWarning) for item in captured)


def test_parse_dict_preserves_raw_triple_quoted_latex_verbatim():
    parser = LLMParser()
    payload = r'''{
        "content": r"""A=\begin{pmatrix}
a&b\\
\end{pmatrix}"""
    }'''
    parsed = parser.parse_dict(payload)
    assert parsed["content"] == (
        "A=\\begin{pmatrix}\n"
        "a&b\\\\\n"
        "\\end{pmatrix}"
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    test_parse_standard_json_literals()
    test_parse_dict_preserves_single_backslash_latex()
    test_parse_dict_does_not_expand_already_escaped_latex()
    test_parse_dict_preserves_braces_mid_and_multiline_content()
    test_parse_list_matches_dict_behavior()
    test_parse_pads_escapes_once_without_expanding_existing_pairs()
    test_parse_dict_does_not_emit_syntaxwarning_for_raw_latex()
    test_parse_dict_preserves_raw_triple_quoted_latex_verbatim()
    print("llm parser tests passed")
