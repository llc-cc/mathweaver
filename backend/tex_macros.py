"""Extract a safe subset of TeX macros for KaTeX rendering."""

from __future__ import annotations

import re
from typing import Any


MacroResult = tuple[dict[str, str], list[str]]

_UNSAFE_TOKENS = (
    "\\input",
    "\\include",
    "\\write",
    "\\openout",
    "\\read",
    "\\catcode",
    "\\csname",
    "\\def",
    "\\edef",
    "\\xdef",
    "\\gdef",
    "\\let",
    "\\futurelet",
    "\\newenvironment",
    "\\renewenvironment",
    "\\begin",
    "\\end",
    "\\if",
)


def extract_latex_macros(source: str, filename: str = "") -> MacroResult:
    """Return KaTeX-compatible macro definitions found in a TeX source string.

    The extractor intentionally supports only common, side-effect-free commands.
    Unsupported or suspicious definitions are skipped and reported as warnings.
    """
    if not source or not _looks_like_tex(filename, source):
        return {}, []

    source = _strip_tex_comments(source)
    preamble = source.split("\\begin{document}", 1)[0]
    macros: dict[str, str] = {}
    warnings: list[str] = []

    for command in ("newcommand", "renewcommand", "providecommand"):
        pos = 0
        needle = f"\\{command}"
        while True:
            start = preamble.find(needle, pos)
            if start < 0:
                break
            parsed = _parse_command_macro(preamble, start, command)
            pos = start + len(needle)
            if not parsed:
                warnings.append(f"Skipped unsupported \\{command} near offset {start}")
                continue
            name, body, next_pos = parsed
            pos = next_pos
            _add_macro(macros, warnings, name, body, command)

    op_pattern = re.compile(r"\\DeclareMathOperator\*?\s*(\{\\[A-Za-z@]+\})\s*(\{)")
    pos = 0
    while True:
        match = op_pattern.search(preamble, pos)
        if not match:
            break
        name = match.group(1)[1:-1]
        body, end = _read_balanced_group(preamble, match.start(2))
        pos = end if body is not None else match.end()
        if body is None:
            warnings.append(f"Skipped unsupported \\DeclareMathOperator near offset {match.start()}")
            continue
        _add_macro(macros, warnings, name, f"\\operatorname{{{body}}}", "DeclareMathOperator")

    return macros, warnings


def merge_latex_macros(*items: Any) -> dict[str, str]:
    """Merge macro dictionaries from JSON-like values."""
    merged: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(key, str) and key.startswith("\\") and isinstance(value, str):
                    merged[key] = value
    return merged


def _looks_like_tex(filename: str, source: str) -> bool:
    lower = (filename or "").lower()
    return (
        lower.endswith(".tex")
        or "\\newcommand" in source
        or "\\renewcommand" in source
        or "\\providecommand" in source
        or "\\DeclareMathOperator" in source
    )


def _strip_tex_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines():
        escaped = False
        for idx, ch in enumerate(line):
            if ch == "%" and not escaped:
                line = line[:idx]
                break
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        lines.append(line)
    return "\n".join(lines)


def _parse_command_macro(text: str, start: int, command: str) -> tuple[str, str, int] | None:
    pos = start + len(command) + 1
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "*":
        pos = _skip_ws(text, pos + 1)

    name = None
    if pos < len(text) and text[pos] == "{":
        raw_name, pos = _read_balanced_group(text, pos)
        if raw_name and re.fullmatch(r"\\[A-Za-z@]+", raw_name.strip()):
            name = raw_name.strip()
    else:
        match = re.match(r"\\[A-Za-z@]+", text[pos:])
        if match:
            name = match.group(0)
            pos += len(name)
    if not name:
        return None

    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "[":
        arg_count, pos = _read_bracket_group(text, pos)
        if arg_count not in {None, "0"} and not str(arg_count).isdigit():
            return None
        pos = _skip_ws(text, pos)
        if pos < len(text) and text[pos] == "[":
            # Optional default arguments are not representable in KaTeX macros.
            return None

    body, pos = _read_balanced_group(text, pos)
    if body is None:
        return None
    return name, body, pos


def _add_macro(macros: dict[str, str], warnings: list[str], name: str, body: str, source: str) -> None:
    if _is_safe_macro_body(body):
        macros[name] = body
    else:
        warnings.append(f"Skipped unsafe macro {name} from \\{source}")


def _is_safe_macro_body(body: str) -> bool:
    return not any(token in body for token in _UNSAFE_TOKENS)


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _read_bracket_group(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != "[":
        return None, start
    end = text.find("]", start + 1)
    if end < 0:
        return None, start
    return text[start + 1:end], end + 1


def _read_balanced_group(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:idx], idx + 1
    return None, start
