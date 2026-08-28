import re

try:
    from pylatexenc.latexwalker import LatexWalker  # type: ignore
except Exception:  # pragma: no cover - depends on optional runtime package
    LatexWalker = None


DEFAULT_THEOREM_ENVS = {
    "theorem": "theorem",
    "thm": "theorem",
    "lemma": "lemma",
    "lem": "lemma",
    "proposition": "proposition",
    "prop": "proposition",
    "corollary": "corollary",
    "cor": "corollary",
    "definition": "definition",
    "defn": "definition",
    "def": "definition",
    "axiom": "axiom",
    "claim": "claim",
    "property": "property",
    "remark": "remark",
    "example": "example",
    "exercise": "exercise",
    "exer": "exercise",
    "conjecture": "conjecture",
    "problem": "problem",
    "observation": "observation",
    "fact": "fact",
}

PROOF_ENVS = {"proof"}
LABEL_RE = re.compile(r"\\label\s*\{([^{}]+)\}")
NEWTHEOREM_RE = re.compile(
    r"\\newtheorem(?P<star>\*)?\s*\{(?P<env>[^{}]+)\}"
    r"(?:\s*\[(?P<shared>[^\]]+)\])?\s*\{(?P<title>[^{}]+)\}"
    r"(?:\s*\[(?P<within>[^\]]+)\])?",
    re.DOTALL,
)
DECLARETHEOREM_RE = re.compile(
    r"\\declaretheorem(?P<star>\*)?(?:\s*\[(?P<options>[^\]]*)\])?\s*\{(?P<env>[^{}]+)\}",
    re.DOTALL,
)
NEWTcbTHEOREM_RE = re.compile(
    r"\\newtcbtheorem(?P<star>\*)?(?:\s*\[(?P<options>[^\]]*)\])?"
    r"\s*\{(?P<env>[^{}]+)\}\s*\{(?P<title>[^{}]+)\}",
    re.DOTALL,
)
NEWMDTHEOREMENV_RE = re.compile(
    r"\\newmdtheoremenv(?P<star>\*)?(?:\s*\[(?P<options>[^\]]*)\])?"
    r"\s*\{(?P<env>[^{}]+)\}(?:\s*\[(?P<shared>[^\]]+)\])?"
    r"\s*\{(?P<title>[^{}]+)\}(?:\s*\[(?P<within>[^\]]+)\])?",
    re.DOTALL,
)
NEWENVIRONMENT_RE = re.compile(
    r"\\newenvironment\*?\s*\{(?P<env>[^{}]+)\}",
    re.DOTALL,
)
BEGIN_RE = re.compile(r"\\begin\s*\{(?P<env>[A-Za-z0-9@:_*-]+)\}(?P<option>\s*\[[^\]]*\])?", re.DOTALL)
DOCUMENT_BEGIN_RE = re.compile(r"\\begin\s*\{document\}")
DOCUMENT_END_RE = re.compile(r"\\end\s*\{document\}")
SECTION_COMMAND_RE = re.compile(
    r"\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?"
    r"(?:\s*\[[^\]]*\])?\s*\{",
    re.IGNORECASE,
)
SECTION_COUNTER_RE = re.compile(
    r"\\(?P<counter>part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
    r"(?P<star>\*)?(?:\s*\[[^\]]*\])?\s*\{",
    re.IGNORECASE,
)
COUNTER_COMMAND_RE = re.compile(
    r"\\(?P<command>setcounter|addtocounter|numberwithin|counterwithin)"
    r"\s*\{(?P<counter>[^{}]+)\}\s*\{(?P<value>[^{}]+)\}",
    re.IGNORECASE,
)
CUSTOM_COUNTER_FORMAT_RE = re.compile(
    r"\\(?:renewcommand|newcommand|providecommand)\*?\s*\{\\the(?P<counter>[A-Za-z@:_-]+)\}",
    re.IGNORECASE,
)
DOCUMENTCLASS_RE = re.compile(
    r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{(?P<class_name>[^{}]+)\}",
    re.IGNORECASE,
)
APPENDIX_RE = re.compile(r"\\appendix\b", re.IGNORECASE)
PAR_BOUNDARY_RE = re.compile(r"\\par\b")
BLANK_LINE_BOUNDARY_RE = re.compile(r"(?:\r?\n[ \t]*){2,}")
SETUP_LINE_RE = re.compile(
    r"^[ \t]*\\(?:"
    r"allowdisplaybreaks|setcounter|addtocounter|numberwithin|counterwithin|appendix|"
    r"setlength|pagestyle|thispagestyle|"
    r"maketitle|tableofcontents|bibliography|bibliographystyle"
    r")\b[^\r\n]*(?:\r?\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
TARGET_RESIDUAL_CHARS = 6000
MAX_RESIDUAL_CHARS = 12000

NON_STATEMENT_ENVS = {
    "abstract",
    "array",
    "center",
    "document",
    "enumerate",
    "eqnarray",
    "equation",
    "figure",
    "flushleft",
    "flushright",
    "gather",
    "itemize",
    "matrix",
    "pmatrix",
    "proof",
    "split",
    "table",
    "tabular",
    "tikzpicture",
}

RESIDUAL_NOISE_ENVS = {
    "algorithm",
    "algorithm2e",
    "figure",
    "longtable",
    "table",
    "tabular",
    "thebibliography",
    "tikzpicture",
}

TITLE_ALIASES = {
    **DEFAULT_THEOREM_ENVS,
    "theorem": "theorem",
    "lemma": "lemma",
    "proposition": "proposition",
    "corollary": "corollary",
    "definition": "definition",
    "axiom": "axiom",
    "claim": "claim",
    "property": "property",
    "remark": "remark",
    "example": "example",
    "exercise": "exercise",
    "conjecture": "conjecture",
    "problem": "problem",
    "observation": "observation",
    "fact": "fact",
}


def is_tex_source_format(context):
    return getattr(context, "source_format", "markdown") == "tex"


def read_tex_source(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _strip_comments(text):
    """Mask comments without changing source offsets."""
    lines = []
    for line in text.splitlines(keepends=True):
        escaped = False
        cut = None
        for idx, char in enumerate(line):
            if char == "\\":
                escaped = not escaped
                continue
            if char == "%" and not escaped:
                cut = idx
                break
            escaped = False
        if cut is None:
            lines.append(line)
            continue
        newline_length = 2 if line.endswith("\r\n") else 1 if line.endswith("\n") else 0
        comment_end = len(line) - newline_length
        lines.append(line[:cut] + (" " * (comment_end - cut)) + line[comment_end:])
    return "".join(lines)


def _comment_spans(text):
    spans = []
    offset = 0
    for line in (text or "").splitlines(keepends=True):
        escaped = False
        for idx, char in enumerate(line):
            if char == "\\":
                escaped = not escaped
                continue
            if char == "%" and not escaped:
                newline_length = 2 if line.endswith("\r\n") else 1 if line.endswith("\n") else 0
                spans.append((offset + idx, offset + len(line) - newline_length, "comment"))
                break
            escaped = False
        offset += len(line)
    return spans


def _normalize_env_name(value):
    return (value or "").strip().rstrip("*")


def _normalize_title(value):
    title = re.sub(r"\\[A-Za-z]+\*?", " ", value or "")
    title = re.sub(r"[^A-Za-z]+", " ", title).strip().lower()
    return title


def _canonical_node_type(env_name, title=""):
    env_key = _normalize_env_name(env_name).lower()
    title_key = _normalize_title(title)
    if env_key in NON_STATEMENT_ENVS:
        return ""
    if env_key in DEFAULT_THEOREM_ENVS:
        return DEFAULT_THEOREM_ENVS[env_key]
    if title_key in TITLE_ALIASES:
        return TITLE_ALIASES[title_key]
    for token, node_type in TITLE_ALIASES.items():
        if token and (env_key == token or env_key.startswith(token) or env_key.endswith(token)):
            return node_type
    for token, node_type in TITLE_ALIASES.items():
        if token and title_key and token in title_key.split():
            return node_type
    return ""


def _option_name(options):
    options = options or ""
    match = re.search(r"(?:^|,)\s*name\s*=\s*\{([^{}]+)\}", options)
    if match:
        return match.group(1).strip()
    match = re.search(r"(?:^|,)\s*name\s*=\s*([^,\]]+)", options)
    if match:
        return match.group(1).strip()
    return ""


def _option_value(options, *names):
    options = options or ""
    for name in names:
        name_pattern = re.escape(name).replace(r"\ ", r"\s*")
        match = re.search(
            rf"(?:^|,)\s*{name_pattern}\s*=\s*(?:\{{([^{{}}]+)\}}|([^,\]]+))",
            options,
            re.IGNORECASE,
        )
        if match:
            return (match.group(1) or match.group(2) or "").strip()
    return ""


def _option_has_flag(options, flag):
    normalized_flag = re.sub(r"\s+", " ", flag or "").strip().lower()
    return any(
        re.sub(r"\s+", " ", item).strip().lower() == normalized_flag
        for item in (options or "").split(",")
    )


def _normalize_counter_name(value):
    return (value or "").strip().lower()


def _fallback_counter_title(node_type, env_name):
    title = str(node_type or env_name or "").strip()
    if title.isascii() and title:
        return title[:1].upper() + title[1:]
    return title


def _counter_spec(env_name, title, *, numbered=True, shared_with="", within="", family=""):
    env = _normalize_env_name(env_name)
    return {
        "env_name": env,
        "title": str(title or "").strip(),
        "numbered": bool(numbered),
        "shared_with": _normalize_counter_name(shared_with),
        "within": _normalize_counter_name(within),
        "declaration_family": family,
    }


def _theorem_counter_declaration_events(source):
    events = []
    for match in NEWTHEOREM_RE.finditer(source or ""):
        events.append(
            {
                "position": match.start(),
                "spec": _counter_spec(
                    match.group("env"),
                    match.group("title"),
                    numbered=not bool(match.group("star")),
                    shared_with=match.group("shared") or "",
                    within=match.group("within") or "",
                    family="newtheorem",
                ),
            }
        )

    for match in DECLARETHEOREM_RE.finditer(source or ""):
        options = match.group("options") or ""
        numbered_value = _option_value(options, "numbered").lower()
        events.append(
            {
                "position": match.start(),
                "spec": _counter_spec(
                    match.group("env"),
                    _option_name(options),
                    numbered=(
                        not bool(match.group("star"))
                        and numbered_value not in {"no", "false", "none"}
                    ),
                    shared_with=_option_value(options, "sibling", "use counter from"),
                    within=_option_value(options, "numberwithin", "number within", "within"),
                    family="declaretheorem",
                ),
            }
        )

    for match in NEWTcbTHEOREM_RE.finditer(source or ""):
        options = match.group("options") or ""
        events.append(
            {
                "position": match.start(),
                "spec": _counter_spec(
                    match.group("env"),
                    match.group("title"),
                    numbered=not bool(match.group("star")) and not _option_has_flag(options, "no counter"),
                    shared_with=_option_value(options, "use counter from", "use counter", "sibling"),
                    within=_option_value(options, "number within", "numberwithin", "within"),
                    family="newtcbtheorem",
                ),
            }
        )

    for match in NEWMDTHEOREMENV_RE.finditer(source or ""):
        options = match.group("options") or ""
        events.append(
            {
                "position": match.start(),
                "spec": _counter_spec(
                    match.group("env"),
                    match.group("title"),
                    numbered=not bool(match.group("star")) and not _option_has_flag(options, "no counter"),
                    shared_with=(
                        match.group("shared")
                        or _option_value(options, "sibling", "use counter from")
                    ),
                    within=(
                        match.group("within")
                        or _option_value(options, "numberwithin", "number within", "within")
                    ),
                    family="newmdtheoremenv",
                ),
            }
        )
    return events


def _default_section_counter_parents(source):
    parents = {
        "subsection": "section",
        "subsubsection": "subsection",
        "paragraph": "subsubsection",
        "subparagraph": "paragraph",
    }
    match = DOCUMENTCLASS_RE.search(source or "")
    class_name = (match.group("class_name") if match else "").strip().lower()
    book_like = (
        "book" in class_name
        or "report" in class_name
        or class_name.endswith("rep")
    )
    if book_like:
        parents["section"] = "chapter"
    return parents


def _resolve_declared_counter(env_name, active_specs):
    counter_name = _normalize_counter_name(env_name)
    seen = set()
    while counter_name:
        if counter_name in seen:
            return "", "shared_counter_cycle"
        seen.add(counter_name)
        spec = active_specs.get(counter_name)
        shared_with = _normalize_counter_name((spec or {}).get("shared_with"))
        if not shared_with:
            return counter_name, ""
        counter_name = shared_with
    return "", "missing_counter_name"


def _reset_counter_descendants(counter_name, values, parents, seen=None):
    seen = set(seen or ())
    if counter_name in seen:
        return
    seen.add(counter_name)
    direct_children = [
        child
        for child, parent in parents.items()
        if parent == counter_name
    ]
    for child in direct_children:
        values[child] = 0
        _reset_counter_descendants(child, values, parents, seen)


def _step_counter(counter_name, values, parents):
    values[counter_name] = int(values.get(counter_name, 0)) + 1
    _reset_counter_descendants(counter_name, values, parents)


def _counter_chain(counter_name, parents):
    chain = []
    seen = set()
    current = counter_name
    while current:
        if current in seen:
            return [], "counter_parent_cycle"
        seen.add(current)
        chain.append(current)
        current = parents.get(current, "")
    chain.reverse()
    return chain, ""


def _format_counter_number(counter_name, values, parents):
    chain, error = _counter_chain(counter_name, parents)
    if error:
        return "", chain, error
    return ".".join(str(int(values.get(name, 0))) for name in chain), chain, ""


def _assign_tex_counter_metadata(source, env_blocks, theorem_envs):
    """Replay supported TeX counter events and annotate theorem environment blocks."""
    values = {}
    parents = _default_section_counter_parents(source)
    active_specs = {}
    active_custom_formats = set()
    diagnostics = []
    diagnostic_keys = set()
    recognized_envs = {str(name).lower() for name in (theorem_envs or {})}

    def add_diagnostic(position, reason, *, counter="", value="", command=""):
        item = {
            "position": int(position),
            "reason": reason,
        }
        if counter:
            item["counter"] = counter
        if value:
            item["value"] = value
        if command:
            item["command"] = command
        key = tuple(sorted(item.items()))
        if key not in diagnostic_keys:
            diagnostic_keys.add(key)
            diagnostics.append(item)

    events = []
    for declaration in _theorem_counter_declaration_events(source):
        events.append((declaration["position"], 0, "declaration", declaration["spec"]))
    for match in COUNTER_COMMAND_RE.finditer(source or ""):
        events.append((match.start(), 1, "counter_command", match))
    for match in CUSTOM_COUNTER_FORMAT_RE.finditer(source or ""):
        events.append((match.start(), 1, "custom_format", match))
    for match in APPENDIX_RE.finditer(source or ""):
        events.append((match.start(), 1, "appendix", match))
    for match in SECTION_COUNTER_RE.finditer(source or ""):
        events.append((match.start(), 2, "section", match))
    for block in env_blocks:
        env_key = _normalize_env_name(block.get("env")).lower()
        if env_key in recognized_envs:
            events.append((block["start"], 3, "environment", block))

    for position, _, event_type, payload in sorted(events, key=lambda item: (item[0], item[1])):
        if event_type == "declaration":
            spec = payload
            env_key = _normalize_counter_name(spec.get("env_name"))
            active_specs[env_key] = spec
            counter_name, error = _resolve_declared_counter(env_key, active_specs)
            if error:
                add_diagnostic(position, error, counter=env_key)
                continue
            if spec.get("within"):
                parents[counter_name] = _normalize_counter_name(spec.get("within"))
            continue

        if event_type == "counter_command":
            command = payload.group("command").lower()
            counter_name = _normalize_counter_name(payload.group("counter"))
            value = str(payload.group("value") or "").strip()
            if command in {"numberwithin", "counterwithin"}:
                parents[counter_name] = _normalize_counter_name(value)
                continue
            if not re.fullmatch(r"[+-]?\d+", value):
                add_diagnostic(
                    position,
                    "unsupported_counter_expression",
                    counter=counter_name,
                    value=value,
                    command=command,
                )
                continue
            integer_value = int(value)
            if command == "setcounter":
                values[counter_name] = integer_value
            else:
                values[counter_name] = int(values.get(counter_name, 0)) + integer_value
            continue

        if event_type == "custom_format":
            active_custom_formats.add(_normalize_counter_name(payload.group("counter")))
            continue

        if event_type == "appendix":
            active_custom_formats.update({"chapter", "section"})
            add_diagnostic(position, "unsupported_appendix_counter_format", command="appendix")
            continue

        if event_type == "section":
            if payload.group("star"):
                continue
            counter_name = _normalize_counter_name(payload.group("counter"))
            _step_counter(counter_name, values, parents)
            continue

        block = payload
        raw_env_name = str(block.get("env") or "").strip()
        env_key = _normalize_env_name(raw_env_name).lower()
        spec = active_specs.get(env_key)
        explicitly_unnumbered = raw_env_name.endswith("*") or (
            spec is not None and not spec.get("numbered", True)
        )
        if explicitly_unnumbered:
            block["counter_meta"] = {
                "numbered": False,
                "auto_label": "",
                "label_source": "unnumbered",
                "tex_counter_name": "",
                "tex_counter_number": "",
                "tex_counter_within": "",
                "counter_fallback": False,
            }
            continue

        fallback = spec is None
        if fallback:
            counter_name = env_key
            title = _fallback_counter_title(theorem_envs.get(env_key), env_key)
            label_source = "tex_counter_fallback"
        else:
            counter_name, error = _resolve_declared_counter(env_key, active_specs)
            if error:
                add_diagnostic(position, error, counter=env_key)
                block["counter_meta"] = {
                    "numbered": True,
                    "auto_label": "",
                    "label_source": "unresolved",
                    "tex_counter_name": "",
                    "tex_counter_number": "",
                    "tex_counter_within": "",
                    "counter_fallback": False,
                }
                continue
            title = str(spec.get("title") or "").strip() or _fallback_counter_title(
                theorem_envs.get(env_key),
                env_key,
            )
            label_source = "tex_counter"

        _step_counter(counter_name, values, parents)
        number, chain, error = _format_counter_number(counter_name, values, parents)
        if error:
            add_diagnostic(position, error, counter=counter_name)
        custom_chain = [name for name in chain if name in active_custom_formats]
        if custom_chain:
            add_diagnostic(
                position,
                "custom_counter_format_approximated",
                counter=",".join(custom_chain),
            )
        block["counter_meta"] = {
            "numbered": True,
            "auto_label": f"{title} {number}".strip() if number else "",
            "label_source": label_source if number else "unresolved",
            "tex_counter_name": counter_name,
            "tex_counter_number": number,
            "tex_counter_within": parents.get(counter_name, ""),
            "counter_fallback": fallback,
        }

    diagnostics.sort(key=lambda item: (item.get("position", 0), item.get("reason", "")))
    return diagnostics


def _register_theorem_env(envs, env_name, title="", *, allow_env_inference=False):
    env = _normalize_env_name(env_name)
    if not env:
        return
    node_type = _canonical_node_type(env, title)
    if not node_type and allow_env_inference:
        node_type = _canonical_node_type(env)
    if node_type:
        envs[env] = node_type
        envs[env.lower()] = node_type


def discover_theorem_envs(source):
    envs = dict(DEFAULT_THEOREM_ENVS)
    for match in NEWTHEOREM_RE.finditer(source or ""):
        _register_theorem_env(envs, match.group("env"), match.group("title"), allow_env_inference=True)
    for match in DECLARETHEOREM_RE.finditer(source or ""):
        title = _option_name(match.group("options") or "")
        _register_theorem_env(envs, match.group("env"), title, allow_env_inference=True)
    for pattern in (NEWTcbTHEOREM_RE, NEWMDTHEOREMENV_RE):
        for match in pattern.finditer(source or ""):
            _register_theorem_env(envs, match.group("env"), match.group("title"), allow_env_inference=True)
    for match in NEWENVIRONMENT_RE.finditer(source or ""):
        _register_theorem_env(envs, match.group("env"), "", allow_env_inference=True)
    return envs


def _parse_braced_args(source, start):
    args = []
    pos = start
    while True:
        while pos < len(source) and source[pos].isspace():
            pos += 1
        if pos >= len(source) or source[pos] != "{":
            break
        depth = 0
        arg_start = pos + 1
        idx = pos
        while idx < len(source):
            char = source[idx]
            if char == "\\":
                idx += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    args.append(source[arg_start:idx])
                    pos = idx + 1
                    break
            idx += 1
        else:
            break
    return args, pos


def _declaration_spans(source):
    spans = []
    for pattern in (
        NEWTHEOREM_RE,
        DECLARETHEOREM_RE,
        NEWTcbTHEOREM_RE,
        NEWMDTHEOREMENV_RE,
        NEWENVIRONMENT_RE,
    ):
        for match in pattern.finditer(source or ""):
            line_end = source.find("\n", match.end())
            if line_end < 0:
                line_end = len(source)
            else:
                line_end += 1
            spans.append((match.start(), line_end))
    return spans


def _inside_spans(position, spans):
    return any(start <= position < end for start, end in spans or [])


def _find_environment_blocks(source, declaration_spans=None, original_source=None):
    original_source = source if original_source is None else original_source
    blocks = []
    for match in BEGIN_RE.finditer(source):
        if _inside_spans(match.start(), declaration_spans):
            continue
        env = match.group("env")
        end_re = re.compile(r"\\end\s*\{" + re.escape(env) + r"\}")
        end_match = end_re.search(source, match.end())
        if not end_match:
            continue
        begin_args, body_start = _parse_braced_args(source, match.end())
        blocks.append(
            {
                "env": env,
                "option": (match.group("option") or "").strip(),
                "begin_args": begin_args,
                "start": match.start(),
                "body_start": body_start,
                "body_end": end_match.start(),
                "end": end_match.end(),
                "body": source[body_start:end_match.start()],
                "raw_tex": original_source[match.start():end_match.end()],
            }
        )
    blocks.sort(key=lambda item: (item["start"], item["end"]))
    return blocks


def _first_label(text, begin_args=None):
    begin_args = begin_args or []
    if len(begin_args) >= 2 and begin_args[1].strip():
        return begin_args[1].strip()
    match = LABEL_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return ""


def _without_label_macros(text):
    return LABEL_RE.sub("", text or "").strip()


def _optional_title(option):
    option = (option or "").strip()
    if option.startswith("[") and option.endswith("]"):
        return option[1:-1].strip()
    return ""


def _begin_title(begin_args):
    begin_args = begin_args or []
    return begin_args[0].strip() if begin_args and begin_args[0].strip() else ""


def _attach_proofs(records, proof_blocks, source=None):
    for proof in proof_blocks:
        previous = None
        for record in records:
            if record["source_span"]["end"] <= proof["start"]:
                previous = record
            else:
                break
        if previous is None or previous.get("proof"):
            continue
        previous["proof"] = _without_label_macros(proof["body"])
        previous["proof_raw_tex"] = proof["raw_tex"]
        previous["proof_source_span"] = {"start": proof["start"], "end": proof["end"]}
        previous["source_span"]["end"] = proof["end"]
        if source is not None:
            previous["raw_tex"] = source[previous["source_span"]["start"]:proof["end"]]
        else:
            previous["raw_tex"] = previous["raw_tex"] + "\n" + proof["raw_tex"]


def _merge_protected_spans(spans, lower_bound, upper_bound):
    normalized = []
    for start, end, reason in spans:
        start = max(lower_bound, int(start))
        end = min(upper_bound, int(end))
        if start < end:
            normalized.append((start, end, reason))
    normalized.sort(key=lambda item: (item[0], item[1]))
    merged = []
    for start, end, reason in normalized:
        if merged and start <= merged[-1][1]:
            previous = merged[-1]
            merged[-1] = (
                previous[0],
                max(previous[1], end),
                ",".join(dict.fromkeys((previous[2] + "," + reason).split(","))),
            )
        else:
            merged.append((start, end, reason))
    return merged


def _document_body_span(masked_source):
    begin = DOCUMENT_BEGIN_RE.search(masked_source or "")
    end = DOCUMENT_END_RE.search(masked_source or "", begin.end() if begin else 0)
    return (
        begin.end() if begin else 0,
        end.start() if end else len(masked_source or ""),
    )


def _balanced_brace_end(source, open_brace):
    depth = 0
    index = open_brace
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return open_brace + 1


def _section_spans(masked_source):
    spans = []
    for match in SECTION_COMMAND_RE.finditer(masked_source or ""):
        open_brace = masked_source.find("{", match.start(), match.end())
        end = _balanced_brace_end(masked_source, open_brace)
        spans.append((match.start(), end, "section_command"))
    return spans


def _setup_spans(masked_source):
    return [(match.start(), match.end(), "setup_command") for match in SETUP_LINE_RE.finditer(masked_source or "")]


def _semantic_residual_text(text):
    value = re.sub(r"\\[A-Za-z@]+\*?", " ", text or "")
    value = re.sub(r"[\[\]{}$\\_^&~#=+\-*/<>:;,.!?()\d\s]+", "", value)
    return value


def _trim_span(source, start, end):
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    return start, end


def _hard_split_span(source, start, end):
    spans = []
    cursor = start
    while end - cursor > MAX_RESIDUAL_CHARS:
        limit = cursor + MAX_RESIDUAL_CHARS
        split = source.rfind("\n", cursor + TARGET_RESIDUAL_CHARS, limit)
        if split <= cursor:
            split = limit
        spans.append((cursor, split))
        cursor = split
    spans.append((cursor, end))
    return spans


def _group_at_soft_boundaries(source, start, end):
    points = [start]
    for match in BLANK_LINE_BOUNDARY_RE.finditer(source, start, end):
        position = match.start()
        if position > points[-1]:
            points.append(position)
    if points[-1] != end:
        points.append(end)

    atoms = [(points[index], points[index + 1]) for index in range(len(points) - 1)]
    grouped = []
    current_start = None
    current_end = None
    for atom_start, atom_end in atoms:
        if current_start is None:
            current_start, current_end = atom_start, atom_end
            continue
        if atom_end - current_start <= TARGET_RESIDUAL_CHARS:
            current_end = atom_end
            continue
        grouped.append((current_start, current_end))
        current_start, current_end = atom_start, atom_end
    if current_start is not None:
        grouped.append((current_start, current_end))

    result = []
    for group_start, group_end in grouped:
        result.extend(_hard_split_span(source, group_start, group_end))
    return result


def _split_residual_interval(source, start, end):
    paragraph_starts = [
        match.start()
        for match in PAR_BOUNDARY_RE.finditer(source, start, end)
    ]
    if not paragraph_starts:
        return _group_at_soft_boundaries(source, start, end)

    points = [start]
    points.extend(position for position in paragraph_starts if position > start)
    points.append(end)
    result = []
    for index in range(len(points) - 1):
        paragraph_start, paragraph_end = points[index], points[index + 1]
        if paragraph_end - paragraph_start <= MAX_RESIDUAL_CHARS:
            result.append((paragraph_start, paragraph_end))
        else:
            result.extend(
                _group_at_soft_boundaries(source, paragraph_start, paragraph_end)
            )
    return result


def _build_residual_blocks(source, masked_source, env_blocks, theorem_envs, declaration_spans):
    body_start, body_end = _document_body_span(masked_source)
    protected = [
        (start, end, "theorem_declaration")
        for start, end in declaration_spans
    ]
    protected.extend(_comment_spans(source))
    protected.extend(_section_spans(masked_source))
    protected.extend(_setup_spans(masked_source))

    for block in env_blocks:
        env_key = _normalize_env_name(block["env"])
        lookup_key = env_key if env_key in theorem_envs else env_key.lower()
        if lookup_key in theorem_envs:
            protected.append((block["start"], block["end"], "statement_environment"))
        elif env_key.lower() in PROOF_ENVS:
            protected.append((block["start"], block["end"], "proof_environment"))
        elif env_key.lower() in RESIDUAL_NOISE_ENVS:
            protected.append((block["start"], block["end"], "non_prose_environment"))

    merged = _merge_protected_spans(protected, body_start, body_end)
    intervals = []
    cursor = body_start
    for start, end, _ in merged:
        if cursor < start:
            intervals.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < body_end:
        intervals.append((cursor, body_end))

    blocks = []
    for interval_start, interval_end in intervals:
        for start, end in _split_residual_interval(source, interval_start, interval_end):
            start, end = _trim_span(source, start, end)
            if start >= end:
                continue
            raw_tex = source[start:end]
            if len(_semantic_residual_text(_strip_comments(raw_tex))) < 20:
                continue
            blocks.append(
                {
                    "block_id": f"tex_residual:{start}:{end}",
                    "source_kind": "tex_residual",
                    "source_span": {"start": start, "end": end},
                    "raw_tex": raw_tex,
                    "source_chars": len(raw_tex),
                    "split_reason": "outside_protected_tex_environments",
                }
            )
    return blocks, [
        {"source_span": {"start": start, "end": end}, "reason": reason}
        for start, end, reason in merged
    ]


def build_tex_source_model(source, source_file=""):
    source = source or ""
    clean_source = _strip_comments(source)
    theorem_envs = discover_theorem_envs(clean_source)
    declaration_spans = _declaration_spans(clean_source)
    env_blocks = _find_environment_blocks(
        clean_source,
        declaration_spans,
        original_source=source,
    )
    counter_diagnostics = _assign_tex_counter_metadata(
        clean_source,
        env_blocks,
        theorem_envs,
    )
    records = []
    proof_blocks = []
    ignored = []

    for block in env_blocks:
        env_key = _normalize_env_name(block["env"])
        env_lookup_key = env_key if env_key in theorem_envs else env_key.lower()
        if env_key in PROOF_ENVS:
            proof_blocks.append(block)
            continue
        if env_lookup_key not in theorem_envs:
            if LABEL_RE.search(block["body"]):
                ignored.append(
                    {
                        "env_name": block["env"],
                        "label": _first_label(block["body"], block.get("begin_args")),
                        "reason": "non_theorem_environment",
                        "source_span": {"start": block["start"], "end": block["end"]},
                    }
                )
            continue
        option_title = _optional_title(block["option"]) or _begin_title(block.get("begin_args"))
        tex_label_key = _first_label(block["body"], block.get("begin_args"))
        counter_meta = block.get("counter_meta") or {
            "numbered": True,
            "auto_label": "",
            "label_source": "unresolved",
            "tex_counter_name": "",
            "tex_counter_number": "",
            "tex_counter_within": "",
            "counter_fallback": False,
        }
        label = tex_label_key or counter_meta.get("auto_label", "")
        label_source = "tex_label_key" if tex_label_key else counter_meta.get("label_source", "unresolved")
        records.append(
            {
                "env_name": block["env"],
                "node_type": theorem_envs[env_lookup_key],
                "content": _without_label_macros(block["body"]),
                "proof": "",
                "label": label,
                "tex_label_key": tex_label_key,
                "label_source": label_source,
                "numbered": bool(counter_meta.get("numbered")),
                "tex_counter_name": counter_meta.get("tex_counter_name", ""),
                "tex_counter_number": counter_meta.get("tex_counter_number", ""),
                "tex_counter_within": counter_meta.get("tex_counter_within", ""),
                "counter_fallback": bool(counter_meta.get("counter_fallback")),
                "optional_title": option_title,
                "source_file": source_file,
                "source_span": {"start": block["start"], "end": block["end"]},
                "statement_source_span": {"start": block["start"], "end": block["end"]},
                "raw_tex": block["raw_tex"],
                "proof_raw_tex": "",
                "proof_source_span": {},
            }
        )

    _attach_proofs(records, proof_blocks, source=source)
    residual_blocks, protected_spans = _build_residual_blocks(
        source,
        clean_source,
        env_blocks,
        theorem_envs,
        declaration_spans,
    )
    return {
        "records": records,
        "ignored_labeled_environments": ignored,
        "theorem_envs": theorem_envs,
        "counter_diagnostics": counter_diagnostics,
        "residual_blocks": residual_blocks,
        "protected_spans": protected_spans,
        "source_file": source_file,
    }


def extract_tex_statement_records(source, source_file=""):
    model = build_tex_source_model(source, source_file=source_file)
    return (
        model["records"],
        model["ignored_labeled_environments"],
        model["theorem_envs"],
    )


def build_tex_stage_outputs(source, source_file=""):
    source_model = build_tex_source_model(source, source_file=source_file)
    records = source_model["records"]
    ignored = source_model["ignored_labeled_environments"]
    theorem_envs = source_model["theorem_envs"]
    residual_blocks = source_model["residual_blocks"]
    problem_dict = {}
    unsplit_statement_dict = {}
    blocks = []
    missing_tex_label_count = 0

    for index, record in enumerate(records):
        problem_dict[index] = {
            "pos1": record["raw_tex"],
            "source_kind": "tex_environment",
            "source_span": record.get("source_span", {}),
        }
        if not record.get("tex_label_key"):
            missing_tex_label_count += 1
        node = {
            "node_type": record["node_type"],
            "content": record["content"],
            "proof": record.get("proof", ""),
            "label": record.get("label", ""),
            "tex_label_key": record.get("tex_label_key", ""),
            "tex_env_name": record.get("env_name", ""),
            "source_span": record.get("source_span", {}),
            "source_file": record.get("source_file", source_file),
        }
        if record.get("optional_title"):
            node["title"] = {"english": record["optional_title"], "chinese": record["optional_title"]}
        unsplit_statement_dict[index] = {
            "pos1": node,
            "_orig_key": index,
            "source_block_key": index,
            "source_text": record["raw_tex"],
        }
        blocks.append(
            {
                "source_block_key": str(index),
                "source_kind": "tex_environment",
                "env_name": record["env_name"],
                "node_type": record["node_type"],
                "label": record.get("label", ""),
                "tex_label_key": record.get("tex_label_key", ""),
                "label_source": record.get("label_source", ""),
                "numbered": bool(record.get("numbered")),
                "tex_counter_name": record.get("tex_counter_name", ""),
                "tex_counter_number": record.get("tex_counter_number", ""),
                "tex_counter_within": record.get("tex_counter_within", ""),
                "counter_fallback": bool(record.get("counter_fallback")),
                "optional_title": record.get("optional_title", ""),
                "source_span": record.get("source_span", {}),
                "has_proof": bool(record.get("proof")),
            }
        )

    for block in residual_blocks:
        block_id = block["block_id"]
        problem_dict[block_id] = {
            "pos1": block["raw_tex"],
            "source_kind": "tex_residual",
            "source_span": block["source_span"],
        }

    report = {
        "schema_version": 2,
        "source_format": "tex",
        "source_file": source_file,
        "pylatexenc_available": LatexWalker is not None,
        "theorem_envs": theorem_envs,
        "source_block_count": len(problem_dict),
        "environment_block_count": len(blocks),
        "residual_block_count": len(residual_blocks),
        "node_count": len(unsplit_statement_dict),
        "missing_tex_label_count": missing_tex_label_count,
        "generated_counter_label_count": sum(
            record.get("label_source") in {"tex_counter", "tex_counter_fallback"}
            for record in records
        ),
        "fallback_counter_label_count": sum(
            record.get("label_source") == "tex_counter_fallback"
            for record in records
        ),
        "unnumbered_environment_count": sum(
            not bool(record.get("numbered"))
            for record in records
        ),
        "unresolved_numbered_label_count": sum(
            bool(record.get("numbered"))
            and not record.get("label")
            for record in records
        ),
        "counter_diagnostic_count": len(source_model.get("counter_diagnostics") or []),
        "counter_diagnostics": source_model.get("counter_diagnostics") or [],
        "ignored_labeled_environment_count": len(ignored),
        "ignored_labeled_environments": ignored,
        "blocks": blocks,
        "residual_blocks": residual_blocks,
        "protected_span_count": len(source_model["protected_spans"]),
    }
    document_model = {
        "schema_version": 2,
        "source_format": "tex",
        "source_file": source_file,
        "pylatexenc_available": LatexWalker is not None,
        "records": records,
        "residual_blocks": residual_blocks,
        "protected_spans": source_model["protected_spans"],
        "ignored_labeled_environments": ignored,
        "theorem_envs": theorem_envs,
        "counter_diagnostics": source_model.get("counter_diagnostics") or [],
    }
    return problem_dict, unsplit_statement_dict, report, document_model
