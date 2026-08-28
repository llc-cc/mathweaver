"""Deterministic MatrixFlow v2 parser for authored TeX and OCR-derived Markdown."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import MatrixBinding, MatrixFlow, MatrixReference, MatrixState, MatrixTransform


_MATRIX_ENVS = {"matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "array"}
_ENV_TOKEN_RE = re.compile(r"\\(?P<kind>begin|end)\s*\{\s*(?P<env>[A-Za-z*]+)\s*\}")
_BEGIN_RE = re.compile(r"\\begin\s*\{\s*(?P<env>matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|array)\s*\}")
_COMMAND_SPACE_RE = re.compile(r"\\(?:,|;|:|!|quad|qquad|enspace|hspace\s*\{[^{}]*\})")
_SIMPLE_ARROW_RE = re.compile(r"\\(?:longrightarrow|rightarrow|to|Rightarrow)|[→⇒⟶⟹]|=>|->")
_ARROW_START_RE = re.compile(r"\\(?:xrightarrow|overset|stackrel|longrightarrow|rightarrow|to|Rightarrow)|[→⇒⟶⟹]|=>|->")
_INVISIBLE_LAYOUT_RE = re.compile(
    r"(?:\s+|&(?:\{\})?|\\\\(?:\s*\[[^\]]*\])?|"
    r"\\(?:quad|qquad|enspace|displaystyle|textstyle|,|;|!|:)|"
    r"\\hspace\s*\{[^{}]*\}|\{\}|\$|\\\[|\\\]|\\\(|\\\))"
)
_SYMBOL_ATOM = (
    r"(?:[A-Za-z]|\\[A-Za-z]+|"
    r"\\(?:mathbf|mathrm|mathsf|mathcal|mathbb|boldsymbol)\s*\{\s*(?:[A-Za-z]|\\[A-Za-z]+)\s*\})"
    r"(?:\s*_\s*(?:\{\s*[A-Za-z0-9]+\s*\}|[A-Za-z0-9]))?"
)
_DEFINITION_RE = re.compile(
    rf"(?P<symbol>\\det\s*\(\s*{_SYMBOL_ATOM}\s*\)|{_SYMBOL_ATOM})"
    r"\s*(?P<operator>:=|=|\\coloneqq|\\equiv)\s*$"
)
_MATH_REGION_RE = re.compile(r"\$\$.*?\$\$|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$)|\\\(.*?\\\)|\\\[.*?\\\]", re.DOTALL)


@dataclass
class _Candidate:
    start: int
    end: int
    env: str
    latex: str
    cells: list[list[str]]
    kind: str
    augmented_after_column: int | None
    outer_factor: str | None
    recovered: bool = False
    recovery_actions: list[str] = field(default_factory=list)
    definition: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "env": self.env,
            "latex": self.latex,
            "cells": self.cells,
            "kind": self.kind,
            "augmented_after_column": self.augmented_after_column,
            "outer_factor": self.outer_factor,
            "recovered": self.recovered,
            "recovery_actions": list(self.recovery_actions),
            **({"definition": dict(self.definition)} if self.definition else {}),
        }


@dataclass(frozen=True)
class _Arrow:
    start: int
    end: int
    label: str | None
    raw: str
    recovered: bool
    recovery_actions: tuple[str, ...] = ()


def _stable_id(*parts: object, length: int = 20) -> str:
    value = "|".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _balanced_group(text: str, start: int) -> tuple[int, str] | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1, text[start + 1:index]
    return None


def _strip_outer_braces(value: str) -> tuple[str, bool]:
    value = value.strip()
    changed = False
    while value.startswith("{"):
        group = _balanced_group(value, 0)
        if not group or group[0] != len(value):
            break
        value = group[1].strip()
        changed = True
    return value, changed


def _normalize_cell(value: str) -> tuple[str, list[str]]:
    actions: list[str] = []
    value = _COMMAND_SPACE_RE.sub("", value)
    value = value.replace("\\displaystyle", "").replace("\\textstyle", "")
    value = value.replace("\\left", "").replace("\\right", "")
    if "−" in value:
        value = value.replace("−", "-")
        actions.append("normalized_unicode_minus")
    if "\\dfrac" in value or "\\tfrac" in value:
        value = value.replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac")
        actions.append("normalized_fraction_command")
    value, _ = _strip_outer_braces(value)
    return re.sub(r"\s+", " ", value).strip(), actions


def _split_rows(body: str) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    actions: list[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and body.startswith("\\cr", index):
            rows.append(body[start:index])
            index += 3
            start = index
            actions.append("normalized_cr_row_separator")
            continue
        if depth == 0 and body.startswith("\\\\", index):
            rows.append(body[start:index])
            index += 2
            if index < len(body) and body[index] == "[":
                close = body.find("]", index + 1)
                if close >= 0:
                    index = close + 1
            start = index
            continue
        index += 1
    rows.append(body[start:])
    return rows, actions


def _split_columns(row: str) -> list[str]:
    row, _ = _strip_outer_braces(row)
    cells: list[str] = []
    start = 0
    depth = 0
    escaped = False
    for index, char in enumerate(row):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "&" and depth == 0:
            cells.append(row[start:index])
            start = index + 1
    cells.append(row[start:])
    return cells


def _array_divider(spec: str | None) -> int | None:
    if not spec or "|" not in spec:
        return None
    before = spec.split("|", 1)[0]
    columns = re.findall(r"[clrmpb]", before, flags=re.IGNORECASE)
    return len(columns) if columns else None


def _find_environment_end(source: str, match: re.Match[str]) -> tuple[int, int] | None:
    env = match.group("env")
    depth = 1
    for token in _ENV_TOKEN_RE.finditer(source, match.end()):
        if token.group("env") != env:
            continue
        depth += 1 if token.group("kind") == "begin" else -1
        if depth == 0:
            return token.start(), token.end()
    return None


def _read_array_spec(source: str, start: int) -> tuple[int, str | None, bool]:
    cursor = start
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    spaced = cursor != start
    if cursor >= len(source) or source[cursor] != "{":
        return start, None, spaced
    group = _balanced_group(source, cursor)
    if not group:
        return start, None, spaced
    return group[0], group[1], spaced or cursor != start


def _delimiter_kind(token: str) -> str | None:
    compact = token.replace(" ", "")
    if compact in {"|", "\\vert", "\\lvert", "\\rvert", "\\Vert", "\\lVert", "\\rVert", "\\|"}:
        return "determinant"
    if compact in {"(", ")", "[", "]", "\\{", "\\}"}:
        return "matrix"
    return None


def _delimiter_pair_matches(left: str, right: str) -> bool:
    left = left.replace(" ", "")
    right = right.replace(" ", "")
    pairs = {
        "(": ")",
        "[": "]",
        "\\{": "\\}",
        "|": "|",
        "\\vert": "\\vert",
        "\\lvert": "\\rvert",
        "\\Vert": "\\Vert",
        "\\lVert": "\\rVert",
        "\\|": "\\|",
    }
    return pairs.get(left) == right


def _expand_array_wrapper(source: str, start: int, end: int) -> tuple[int, int, str | None, list[str]]:
    left_window = source[max(0, start - 100):start]
    right_window = source[end:min(len(source), end + 100)]
    left = re.search(r"(?P<all>\{?\s*\\left\s*(?P<delimiter>\\(?:lVert|rVert|Vert|vert|\{|\})|[()\[\]|])\s*)$", left_window)
    right = re.match(r"(?P<all>\s*\\right\s*(?P<delimiter>\\(?:lVert|rVert|Vert|vert|\{|\})|[()\[\]|])\s*\}?)", right_window) if left else None
    if not left or not right:
        left = re.search(r"(?P<all>\s*(?P<delimiter>\\(?:lVert|Vert|vert|\{)|\|\||[([|])\s*)$", left_window)
        right = re.match(r"(?P<all>\s*(?P<delimiter>\\(?:rVert|Vert|vert|\})|\|\||[)\]|]))", right_window) if left else None
    if not left or not right:
        return start, end, None, []
    left_kind = _delimiter_kind(left.group("delimiter"))
    right_kind = _delimiter_kind(right.group("delimiter"))
    if not left_kind or left_kind != right_kind or not _delimiter_pair_matches(left.group("delimiter"), right.group("delimiter")):
        return start, end, None, []
    expanded_start = start - len(left.group("all"))
    expanded_end = end + len(right.group("all"))
    return expanded_start, expanded_end, left_kind, []


def _outer_factor(source: str, start: int) -> str | None:
    prefix = source[max(0, start - 48):start]
    match = re.search(
        r"(?:^|\\(?:to|rightarrow|longrightarrow)|[→⇒⟶⟹]|=>|->|[\s=([+*])\s*"
        r"(?P<factor>-?(?:\d+(?:/\d+)?|[A-Za-z][A-Za-z0-9_]*))\s*(?:\\cdot|\*)?\s*$",
        prefix,
    )
    return match.group("factor") if match else None


def _definition_before(source: str, start: int) -> dict[str, Any] | None:
    window_start = max(0, start - 180)
    prefix = source[window_start:start]
    match = _DEFINITION_RE.search(prefix)
    if not match:
        return None
    symbol_start = window_start + match.start("symbol")
    symbol_end = window_start + match.end("symbol")
    return {
        "symbol_latex": match.group("symbol").strip(),
        "source_span": {"start": symbol_start, "end": symbol_end},
        "source_excerpt": source[symbol_start:symbol_end],
        "operator": match.group("operator"),
    }


def _parse_candidate(source: str, match: re.Match[str]) -> tuple[_Candidate | None, dict[str, Any] | None]:
    found_end = _find_environment_end(source, match)
    if not found_end:
        return None, {
            "field": None,
            "source_span": {"start": match.start(), "end": min(len(source), match.end() + 120)},
            "source_excerpt": source[match.start():min(len(source), match.end() + 120)],
            "reason": "unclosed_environment",
            "recovery_actions": [],
        }
    body_end, environment_end = found_end
    env = match.group("env")
    body_start = match.end()
    spec = None
    actions: list[str] = []
    if env == "array":
        body_start, spec, spaced_spec = _read_array_spec(source, body_start)
        if spaced_spec or source[match.start():match.end()] != f"\\begin{{{env}}}":
            actions.append("normalized_array_command_spacing")
    elif source[match.start():match.end()] != f"\\begin{{{env}}}":
        actions.append("normalized_environment_spacing")
    raw_body = source[body_start:body_end]
    if _BEGIN_RE.search(raw_body):
        return None, {
            "field": None,
            "source_span": {"start": match.start(), "end": environment_end},
            "source_excerpt": source[match.start():environment_end],
            "reason": "layout_container_with_inner_matrix",
            "recovery_actions": ["selected_inner_matrix"],
        }
    row_texts, row_actions = _split_rows(raw_body)
    actions.extend(row_actions)
    parsed_rows: list[list[str]] = []
    blank_rows = 0
    partial_empty = False
    for row_text in row_texts:
        raw_cells = _split_columns(row_text)
        normalized: list[str] = []
        for raw_cell in raw_cells:
            cell, cell_actions = _normalize_cell(raw_cell)
            normalized.append(cell)
            actions.extend(cell_actions)
        if not any(normalized):
            blank_rows += 1
            continue
        if any(not cell for cell in normalized):
            partial_empty = True
        parsed_rows.append(normalized)
    if partial_empty:
        return None, {
            "field": None,
            "source_span": {"start": match.start(), "end": environment_end},
            "source_excerpt": source[match.start():environment_end],
            "reason": "partial_empty_row",
            "recovery_actions": sorted(set(actions)),
        }
    if not parsed_rows:
        return None, {
            "field": None,
            "source_span": {"start": match.start(), "end": environment_end},
            "source_excerpt": source[match.start():environment_end],
            "reason": "empty_matrix",
            "recovery_actions": sorted(set(actions)),
        }
    width = len(parsed_rows[0])
    if width == 0 or any(len(row) != width for row in parsed_rows):
        return None, {
            "field": None,
            "source_span": {"start": match.start(), "end": environment_end},
            "source_excerpt": source[match.start():environment_end],
            "reason": "non_rectangular_matrix",
            "recovery_actions": sorted(set(actions)),
        }
    if blank_rows:
        actions.append("removed_blank_pseudo_rows")
    start = match.start()
    end = environment_end
    wrapper_kind = None
    if env == "array":
        start, end, wrapper_kind, wrapper_actions = _expand_array_wrapper(source, start, end)
        actions.extend(wrapper_actions)
    divider = _array_divider(spec)
    kind = "augmented" if divider is not None else (
        "determinant" if env in {"vmatrix", "Vmatrix"} or wrapper_kind == "determinant" else "matrix"
    )
    actions = sorted(set(actions))
    candidate = _Candidate(
        start=start,
        end=end,
        env=env,
        latex=source[start:end],
        cells=parsed_rows,
        kind=kind,
        augmented_after_column=divider,
        outer_factor=_outer_factor(source, start),
        recovered=bool(actions),
        recovery_actions=actions,
    )
    candidate.definition = _definition_before(source, start)
    return candidate, None


def _explicit_rejections(source: str) -> list[dict[str, Any]]:
    patterns = (
        (re.compile(r"\\binom\s*\{[^{}]*\}\s*\{[^{}]*\}"), "binomial_not_matrix"),
        (re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL), "html_table_not_matrix"),
        (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "image_placeholder_not_matrix"),
        (re.compile(rf"{_SYMBOL_ATOM}\s*(?::=|=|\\coloneqq|\\equiv)\s*\([^()\n]*\)"), "unstructured_parenthesized_sequence"),
    )
    rejected: list[dict[str, Any]] = []
    for pattern, reason in patterns:
        for match in pattern.finditer(source):
            excerpt = match.group(0)
            if reason == "unstructured_parenthesized_sequence" and "\\begin" in excerpt:
                continue
            rejected.append({
                "field": None,
                "source_span": {"start": match.start(), "end": match.end()},
                "source_excerpt": excerpt,
                "reason": reason,
                "recovery_actions": [],
            })
    return rejected


def extract_matrix_candidates_with_diagnostics(source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return safe rectangular candidates and rejected structural candidates."""

    source = source or ""
    candidates: list[_Candidate] = []
    rejected: list[dict[str, Any]] = _explicit_rejections(source)
    for match in _BEGIN_RE.finditer(source):
        candidate, rejection = _parse_candidate(source, match)
        if rejection:
            rejected.append(rejection)
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.start, item.end))
    selected: list[_Candidate] = []
    for candidate in candidates:
        if selected and candidate.start < selected[-1].end:
            previous = selected[-1]
            if candidate.end <= previous.end:
                selected[-1] = candidate
            continue
        selected.append(candidate)
    return [candidate.public() for candidate in selected], rejected


def extract_matrix_candidates(source: str) -> list[dict[str, Any]]:
    return extract_matrix_candidates_with_diagnostics(source)[0]


def _scan_arrows(gap: str) -> list[_Arrow]:
    arrows: list[_Arrow] = []
    cursor = 0
    while True:
        match = _ARROW_START_RE.search(gap, cursor)
        if not match:
            break
        raw = match.group(0)
        label = None
        end = match.end()
        recovered = bool(re.fullmatch(r"[→⇒⟶⟹]|=>|->", raw))
        actions: list[str] = ["normalized_ocr_arrow"] if recovered else []
        if raw == "\\xrightarrow":
            position = end
            while position < len(gap) and gap[position].isspace():
                position += 1
            group = _balanced_group(gap, position)
            if group:
                end, label = group
                raw = gap[match.start():end]
        elif raw in {"\\overset", "\\stackrel"}:
            position = end
            while position < len(gap) and gap[position].isspace():
                position += 1
            first = _balanced_group(gap, position)
            if first:
                position = first[0]
                while position < len(gap) and gap[position].isspace():
                    position += 1
                second = _balanced_group(gap, position)
                if second and _SIMPLE_ARROW_RE.fullmatch(second[1].strip()):
                    end = second[0]
                    label = first[1]
                    raw = gap[match.start():end]
                    recovered = True
                    actions.append("recovered_stacked_arrow")
        arrows.append(_Arrow(match.start(), end, label.strip() if label else None, raw, recovered, tuple(sorted(set(actions)))))
        cursor = max(end, match.end())
    return arrows


def _arrow_between(source: str, left: dict[str, Any], right: dict[str, Any]) -> _Arrow | None:
    gap = source[left["end"]:right["start"]]
    arrows = _scan_arrows(gap)
    if len(arrows) != 1:
        return None
    arrow = arrows[0]
    invisible = gap[:arrow.start] + gap[arrow.end:]
    factor = str(right.get("outer_factor") or "").strip()
    if factor:
        invisible = re.sub(rf"{re.escape(factor)}\s*(?:\\cdot|\*)?\s*$", "", invisible)
    if _INVISIBLE_LAYOUT_RE.sub("", invisible):
        return None
    return arrow


def _index_token(value: str) -> tuple[str, int] | None:
    match = re.search(r"(?P<axis>[RC])\s*[_\^]?\s*\{?\s*(?P<index>\d+)\s*\}?", value or "", re.IGNORECASE)
    return (match.group("axis").upper(), int(match.group("index"))) if match else None


def _normalize_operation_text(label: str) -> str:
    return (
        (label or "")
        .replace("\\leftrightarrow", "↔")
        .replace("\\longleftrightarrow", "↔")
        .replace("\\leftarrow", "←")
        .replace("\\rightarrow", "→")
        .replace("\\longrightarrow", "→")
        .replace("\\to", "→")
        .replace("\\cdot", "*")
        .replace("\\times", "*")
        .replace("−", "-")
        .replace("\\,", "")
    )


def _coefficient(value: str) -> str:
    value = value.strip().replace(" ", "")
    if value in {"", "+"}:
        return "1"
    if value == "-":
        return "-1"
    return value[1:] if value.startswith("*") else value


def _parse_one_operation(label: str) -> dict[str, Any] | None:
    text = _normalize_operation_text(label)
    swap = re.search(r"(?P<left>[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?)\s*↔\s*(?P<right>[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?)", text, re.IGNORECASE)
    if swap:
        left = _index_token(swap.group("left"))
        right = _index_token(swap.group("right"))
        if left and right and left[0] == right[0]:
            return {"type": "row_swap" if left[0] == "R" else "col_swap", "first": left[1], "second": right[1]}
    arrow = re.search(
        r"(?P<left>[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?)\s*(?:←|→)\s*(?P<right>.+)$",
        text,
        re.IGNORECASE,
    )
    if not arrow:
        return None
    left = _index_token(arrow.group("left"))
    if not left:
        return None
    axis, target = left
    rhs = arrow.group("right").strip()
    rhs_target = _index_token(rhs)
    axis_name = "row" if axis == "R" else "col"
    if rhs_target and rhs_target == left:
        token_match = re.search(r"[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?", rhs, re.IGNORECASE)
        remainder = rhs[token_match.end():].strip() if token_match else ""
        add_match = re.match(
            r"(?P<sign>[+-])\s*(?P<coeff>(?:\\(?:dfrac|tfrac|frac)\s*\{[^{}]*\}\s*\{[^{}]*\}|[A-Za-z0-9_./-]+)?)\s*\*?\s*(?P<source>[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?)",
            remainder,
            re.IGNORECASE,
        )
        if add_match:
            source_token = _index_token(add_match.group("source"))
            if source_token and source_token[0] == axis:
                coefficient = _coefficient(add_match.group("coeff").replace("\\dfrac", "\\frac").replace("\\tfrac", "\\frac"))
                if add_match.group("sign") == "-":
                    coefficient = f"-({coefficient})"
                return {"type": f"{axis_name}_add", "target": target, "source": source_token[1], "coefficient": coefficient}
        return None
    scale = re.fullmatch(
        r"(?P<coeff>-?(?:\\(?:dfrac|tfrac|frac)\s*\{[^{}]*\}\s*\{[^{}]*\}|[A-Za-z0-9_./]+))\s*\*?\s*(?P<token>[RC]\s*[_\^]?\s*\{?\s*\d+\s*\}?)",
        rhs,
        re.IGNORECASE,
    )
    if scale and _index_token(scale.group("token")) == left:
        return {"type": f"{axis_name}_scale", "target": target, "factor": _coefficient(scale.group("coeff"))}
    return None


def parse_operations(label: str | None) -> list[dict[str, Any]]:
    if not label:
        return []
    operations = []
    for chunk in (part.strip() for part in re.split(r"[,;]|\\quad", label)):
        if not chunk:
            continue
        operation = _parse_one_operation(chunk)
        if operation is not None:
            operations.append(operation)
    return operations


def _state(flow_id: str, index: int, candidate: dict[str, Any]) -> dict[str, Any]:
    state_id = _stable_id(flow_id, "node", index, candidate["start"], candidate["end"])
    return MatrixState(
        id=state_id,
        kind=candidate["kind"],
        rows=len(candidate["cells"]),
        columns=len(candidate["cells"][0]),
        cells=candidate["cells"],
        latex=candidate["latex"],
        augmented_after_column=candidate.get("augmented_after_column"),
        outer_factor=candidate.get("outer_factor"),
        source_span={"start": candidate["start"], "end": candidate["end"]},
    ).to_dict()


def _source_payload(source_origin: str, document_hash: str | None, candidates: Iterable[dict[str, Any]], arrows: Iterable[_Arrow] = ()) -> dict[str, Any]:
    actions = sorted({action for candidate in candidates for action in candidate.get("recovery_actions", [])} | {action for arrow in arrows for action in arrow.recovery_actions})
    payload: dict[str, Any] = {
        "kind": "ocr" if source_origin == "ocr" else "markdown",
        "evidence_ids": [],
    }
    if document_hash:
        payload["document_hash"] = document_hash
    if actions:
        payload["recovered"] = True
        payload["recovery_actions"] = actions
    return payload


def _make_transformation_flow(source: str, owner: dict[str, Any], field_name: str, candidates: list[dict[str, Any]], component: list[int], arrows: dict[tuple[int, int], _Arrow], region_index: int, document_hash: str | None, source_origin: str) -> tuple[dict[str, Any], dict[int, str]]:
    owner_id = str(owner.get("global_id") or owner.get("source_block_key") or "unknown")
    first = candidates[component[0]]
    last = candidates[component[-1]]
    flow_id = _stable_id(owner_id, field_name, first["start"], last["end"], region_index, "transformation")
    nodes = [_state(flow_id, index, candidates[candidate_index]) for index, candidate_index in enumerate(component)]
    state_ids = {candidate_index: nodes[index]["id"] for index, candidate_index in enumerate(component)}
    flow_arrows = [arrow for pair, arrow in arrows.items() if pair[0] in state_ids and pair[1] in state_ids]
    edges = []
    for (left, right), arrow in arrows.items():
        if left not in state_ids or right not in state_ids:
            continue
        edges.append(MatrixTransform(
            id=_stable_id(flow_id, "edge", left, right),
            from_id=state_ids[left],
            to_id=state_ids[right],
            operations=parse_operations(arrow.label),
            label=arrow.label,
            provenance="observed" if arrow.label else "inferred",
        ).to_dict())
    flow = MatrixFlow(
        id=flow_id,
        role="transformation",
        owner={
            "global_id": owner.get("global_id", ""),
            "source_block_key": owner.get("source_block_key", ""),
            "field": field_name,
            "source_span": {"start": first["start"], "end": last["end"]},
            "source_excerpt": source[first["start"]:last["end"]],
        },
        source=_source_payload(source_origin, document_hash, (candidates[index] for index in component), flow_arrows),
        nodes=nodes,
        edges=edges,
    ).to_dict()
    return flow, state_ids


def _make_named_flow(source: str, owner: dict[str, Any], field_name: str, candidate: dict[str, Any], index: int, document_hash: str | None, source_origin: str) -> tuple[dict[str, Any], str]:
    owner_id = str(owner.get("global_id") or owner.get("source_block_key") or "unknown")
    flow_id = _stable_id(owner_id, field_name, candidate["start"], candidate["end"], index, "named_matrix")
    node = _state(flow_id, 0, candidate)
    flow = MatrixFlow(
        id=flow_id,
        role="named_matrix",
        owner={
            "global_id": owner.get("global_id", ""),
            "source_block_key": owner.get("source_block_key", ""),
            "field": field_name,
            "source_span": {"start": candidate["start"], "end": candidate["end"]},
            "source_excerpt": source[candidate["start"]:candidate["end"]],
        },
        source=_source_payload(source_origin, document_hash, [candidate]),
        nodes=[node],
        edges=[],
    ).to_dict()
    return flow, node["id"]


def _transform_components(source: str, candidates: list[dict[str, Any]]) -> tuple[list[list[int]], dict[tuple[int, int], _Arrow]]:
    arrows: dict[tuple[int, int], _Arrow] = {}
    for left in range(len(candidates) - 1):
        arrow = _arrow_between(source, candidates[left], candidates[left + 1])
        if arrow:
            arrows[(left, left + 1)] = arrow
    adjacency = {index: set() for index in range(len(candidates))}
    for left, right in arrows:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: list[list[int]] = []
    seen: set[int] = set()
    for start in range(len(candidates)):
        if start in seen or not adjacency[start]:
            continue
        stack = [start]
        component: list[int] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(sorted(adjacency[current], reverse=True))
        components.append(sorted(component))
    return components, arrows


def _math_regions(source: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _MATH_REGION_RE.finditer(source)]


def _inside(position: int, spans: Iterable[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _reference_candidates(source: str, symbol: str) -> list[tuple[int, int, str]]:
    escaped = re.escape(symbol)
    patterns = [
        (re.compile(rf"\$\$\s*{escaped}\s*\$\$"), "math"),
        (re.compile(rf"\\\[\s*{escaped}\s*\\\]"), "math"),
        (re.compile(rf"\\\(\s*{escaped}\s*\\\)"), "math"),
        (re.compile(rf"(?<!\$)\$(?!\$)\s*{escaped}\s*(?<!\$)\$(?!\$)"), "math"),
    ]
    results: list[tuple[int, int, str]] = []
    math_regions = _math_regions(source)
    for pattern, context in patterns:
        results.extend((match.start(), match.end(), context) for match in pattern.finditer(source))
    boundary = re.compile(rf"(?<![A-Za-z0-9_\\]){escaped}(?![A-Za-z0-9_])")
    results.extend((match.start(), match.end(), "text") for match in boundary.finditer(source) if not _inside(match.start(), math_regions))
    return sorted(set(results))


def _overlaps(span: tuple[int, int], blocked: Iterable[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in blocked)


def _attach_bindings(fields: dict[str, str], owner: dict[str, Any], definitions: list[dict[str, Any]], flows: list[dict[str, Any]]) -> None:
    field_order = {"statement": 0, "proof": 1}
    definitions.sort(key=lambda item: (field_order[item["field"]], item["definition"]["source_span"]["start"]))
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for definition in definitions:
        by_symbol.setdefault(definition["definition"]["symbol_latex"], []).append(definition)
    flow_by_id = {flow["id"]: flow for flow in flows}
    flow_spans = {
        field_name: [(flow["owner"]["source_span"]["start"], flow["owner"]["source_span"]["end"]) for flow in flows if flow["owner"]["field"] == field_name and flow["role"] == "transformation"]
        for field_name in fields
    }
    matrix_spans = {
        field_name: [(node["source_span"]["start"], node["source_span"]["end"]) for flow in flows for node in flow["nodes"] if flow["owner"]["field"] == field_name]
        for field_name in fields
    }
    for symbol, symbol_definitions in by_symbol.items():
        for index, entry in enumerate(symbol_definitions):
            definition = entry["definition"]
            current_key = (field_order[entry["field"]], definition["source_span"]["start"])
            next_key = None
            if index + 1 < len(symbol_definitions):
                following = symbol_definitions[index + 1]
                next_key = (field_order[following["field"]], following["definition"]["source_span"]["start"])
            references: list[dict[str, Any]] = []
            occupied: list[tuple[str, int, int]] = []
            for field_name in ("statement", "proof"):
                text = fields.get(field_name, "")
                for start, end, context in _reference_candidates(text, symbol):
                    key = (field_order[field_name], start)
                    if key <= current_key or (next_key is not None and key >= next_key):
                        continue
                    blocked = list(matrix_spans.get(field_name, [])) + list(flow_spans.get(field_name, []))
                    blocked.extend((item[1], item[2]) for item in occupied if item[0] == field_name)
                    blocked.append((definition["source_span"]["start"], definition["source_span"]["end"])) if field_name == entry["field"] else None
                    if _overlaps((start, end), blocked):
                        continue
                    reference = MatrixReference(
                        id=_stable_id(entry["flow_id"], symbol, field_name, start, end),
                        field=field_name,
                        source_span={"start": start, "end": end},
                        source_excerpt=text[start:end],
                        context=context,
                    ).to_dict()
                    references.append(reference)
                    occupied.append((field_name, start, end))
            binding = MatrixBinding(
                id=_stable_id(entry["flow_id"], "binding", symbol, entry["field"], definition["source_span"]["start"]),
                symbol_latex=symbol,
                state_id=entry["state_id"],
                definition={
                    "field": entry["field"],
                    "source_span": definition["source_span"],
                    "source_excerpt": definition["source_excerpt"],
                },
                references=references,
            ).to_dict()
            flow_by_id[entry["flow_id"]]["bindings"].append(binding)


def parse_matrix_owner(fields: dict[str, str], *, owner: dict[str, Any] | None = None, document_hash: str | None = None, source_origin: str = "markdown") -> dict[str, Any]:
    """Parse statement and proof together so named-matrix bindings share one scope."""

    owner = dict(owner or {})
    normalized_fields = {field: str(fields.get(field) or "") for field in ("statement", "proof")}
    flows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    counts = {"strict": 0, "tolerant": 0, "rejected": 0, "transformation": 0, "named_matrix": 0}
    for field_name in ("statement", "proof"):
        source = normalized_fields[field_name]
        if not source:
            continue
        candidates, field_rejected = extract_matrix_candidates_with_diagnostics(source)
        for item in field_rejected:
            item["field"] = field_name
        rejected.extend(field_rejected)
        counts["rejected"] += len(field_rejected)
        components, arrows = _transform_components(source, candidates)
        component_members = {member for component in components for member in component}
        candidate_locations: dict[int, tuple[str, str]] = {}
        for region_index, component in enumerate(components):
            flow, state_ids = _make_transformation_flow(source, owner, field_name, candidates, component, arrows, region_index, document_hash, source_origin)
            flows.append(flow)
            counts["transformation"] += 1
            for candidate_index, state_id in state_ids.items():
                candidate_locations[candidate_index] = (flow["id"], state_id)
        named_index = 0
        for candidate_index, candidate in enumerate(candidates):
            definition = candidate.get("definition")
            if candidate_index not in component_members and definition:
                flow, state_id = _make_named_flow(source, owner, field_name, candidate, named_index, document_hash, source_origin)
                named_index += 1
                flows.append(flow)
                counts["named_matrix"] += 1
                candidate_locations[candidate_index] = (flow["id"], state_id)
            if candidate.get("recovered"):
                counts["tolerant"] += 1
            else:
                counts["strict"] += 1
            if definition and candidate_index in candidate_locations:
                flow_id, state_id = candidate_locations[candidate_index]
                definitions.append({"field": field_name, "definition": definition, "flow_id": flow_id, "state_id": state_id})
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index not in component_members and not candidate.get("definition"):
                rejected.append({
                    "field": field_name,
                    "source_span": {"start": candidate["start"], "end": candidate["end"]},
                    "source_excerpt": candidate["latex"],
                    "reason": "anonymous_isolated_matrix",
                    "recovery_actions": candidate.get("recovery_actions", []),
                })
                counts["rejected"] += 1
    _attach_bindings(normalized_fields, owner, definitions, flows)
    flows = [flow for flow in flows if flow["role"] != "named_matrix" or flow.get("bindings")]
    return {"flows": flows, "rejected": rejected, "counts": counts}


def parse_matrix_flows(source: str, *, owner: dict[str, Any] | None = None, field: str = "statement", document_hash: str | None = None, source_origin: str = "markdown") -> list[dict[str, Any]]:
    """Backward-compatible single-field entry point that emits MatrixFlow v2."""

    fields = {"statement": "", "proof": ""}
    fields[field if field in fields else "statement"] = source or ""
    return parse_matrix_owner(fields, owner=owner, document_hash=document_hash, source_origin=source_origin)["flows"]


__all__ = [
    "extract_matrix_candidates",
    "extract_matrix_candidates_with_diagnostics",
    "parse_matrix_flows",
    "parse_matrix_owner",
    "parse_operations",
]
