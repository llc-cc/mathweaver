import ast
import json
import re


class LLMParser:
    _RAW_TRIPLE_STRING_RE = re.compile(
        r"(?i)(?<![A-Za-z0-9_])r(?:\"\"\"|''')"
    )
    _LATEX_N_COMMANDS = {
        "nabla",
        "neg",
        "neq",
        "ni",
        "nmid",
        "not",
        "notin",
    }

    def __init__(self):
        pass

    @staticmethod
    def _normalize_punctuation(text):
        return (
            text.replace("，", ",")
            .replace("“", "'")
            .replace("”", "'")
            .replace("‘", "'")
            .replace("’", "'")
            .replace("。", ".")
            .replace("：", ":")
            .replace("；", ";")
            .replace("？", "?")
            .replace("【", "[")
            .replace("】", "]")
            .replace("（", "(")
            .replace("）", ")")
            .replace("！", "!")
            .replace("—", "-")
            .replace("…", "...")
        )

    @staticmethod
    def _extract_bracketed_payload(text, open_char, close_char, allow_autoclose=False):
        start_index = text.find(open_char)
        end_index = text.rfind(close_char)

        if start_index != -1 and end_index == -1 and allow_autoclose:
            text = text + close_char
            end_index = len(text) - 1

        if start_index == -1 or end_index == -1 or end_index < start_index:
            raise ValueError(f"Failed to find payload bounded by {open_char}{close_char}. Raw text: {text}")

        return text[start_index : end_index + 1]

    @staticmethod
    def _is_escaped_by_backslashes(text, index):
        backslash_count = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslash_count += 1
            cursor -= 1
        return backslash_count % 2 == 1

    @staticmethod
    def _consume_ascii_letters(text, start):
        cursor = start
        while cursor < len(text) and text[cursor].isalpha():
            cursor += 1
        return text[start:cursor], cursor

    @classmethod
    def _looks_like_latex_n_command(cls, text, start):
        if start >= len(text) or text[start] != "n":
            return False
        command, _ = cls._consume_ascii_letters(text, start)
        return command in cls._LATEX_N_COMMANDS

    @classmethod
    def _should_preserve_single_backslash_escape(cls, text, run_end):
        next_char = text[run_end] if run_end < len(text) else ""
        if next_char in ('"', "'", "\\"):
            return True
        if next_char == "n":
            return not cls._looks_like_latex_n_command(text, run_end)
        return False

    @classmethod
    def _escape_string_literals_for_literal_eval(cls, text):
        result = []
        index = 0
        text_length = len(text)

        while index < text_length:
            char = text[index]
            if char not in ('"', "'"):
                result.append(char)
                index += 1
                continue

            quote = char
            result.append(char)
            index += 1

            while index < text_length:
                char = text[index]

                if char == quote and not cls._is_escaped_by_backslashes(text, index):
                    result.append(char)
                    index += 1
                    break

                if char == "\n":
                    result.append("\\n")
                    index += 1
                    continue

                if char == "\\":
                    run_end = index
                    while run_end < text_length and text[run_end] == "\\":
                        run_end += 1

                    next_char = text[run_end] if run_end < text_length else ""
                    run_length = run_end - index

                    if run_length % 2 == 1 and not cls._should_preserve_single_backslash_escape(text, run_end):
                        run_length += 1

                    result.append("\\" * run_length)
                    index = run_end
                    continue

                result.append(char)
                index += 1

        return "".join(result)

    @classmethod
    def _contains_risky_single_backslashes_in_string_literals(cls, text):
        index = 0
        text_length = len(text)

        while index < text_length:
            char = text[index]
            if char not in ('"', "'"):
                index += 1
                continue

            quote = char
            index += 1

            while index < text_length:
                char = text[index]

                if char == quote and not cls._is_escaped_by_backslashes(text, index):
                    index += 1
                    break

                if char == "\\":
                    run_end = index
                    while run_end < text_length and text[run_end] == "\\":
                        run_end += 1

                    next_char = text[run_end] if run_end < text_length else ""
                    run_length = run_end - index

                    if run_length % 2 == 1 and not cls._should_preserve_single_backslash_escape(text, run_end):
                        return True

                    index = run_end
                    continue

                index += 1

        return False

    @staticmethod
    def _deduplicate_attempts(candidates):
        unique_candidates = []
        seen = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            unique_candidates.append(candidate)
        return unique_candidates

    def _parse_literal_payload(
        self,
        raw_text,
        *,
        open_char,
        close_char,
        expected_type,
        allow_autoclose=False,
    ):
        normalized_text = self._normalize_punctuation(raw_text)
        payload = self._extract_bracketed_payload(
            normalized_text,
            open_char,
            close_char,
            allow_autoclose=allow_autoclose,
        )

        escaped_payload = self._escape_string_literals_for_literal_eval(payload)
        attempts = []
        if (
            self._RAW_TRIPLE_STRING_RE.search(payload)
            or not self._contains_risky_single_backslashes_in_string_literals(payload)
        ):
            attempts.append(payload)
        attempts.append(escaped_payload)
        attempts = self._deduplicate_attempts(attempts)

        last_error = None
        for candidate in attempts:
            for loader in (json.loads, ast.literal_eval):
                try:
                    parsed_value = loader(candidate)
                    if isinstance(parsed_value, expected_type):
                        return parsed_value
                    raise ValueError(f"Parsed object is not of type {expected_type.__name__}.")
                except Exception as exc:
                    last_error = exc

        raise RuntimeError(f"Parsing failed: {last_error}. Raw text: {normalized_text}")

    def parse_list(self, str_with_list):
        return self._parse_literal_payload(
            str_with_list,
            open_char="[",
            close_char="]",
            expected_type=list,
            allow_autoclose=True,
        )

    def parse_dict(self, str_with_dict):
        return self._parse_literal_payload(
            str_with_dict,
            open_char="{",
            close_char="}",
            expected_type=dict,
            allow_autoclose=False,
        )

    def parse_pads(self, str_with_pads):
        try:
            normalized_text = self._normalize_punctuation(str_with_pads)
            return self._escape_string_literals_for_literal_eval(normalized_text)
        except Exception as exc:
            raise RuntimeError(f"Parsing failed: {exc}. Raw text: {str_with_pads}")

    def parse_code(self, markdown_text):
        pattern = r"```([\w\s]+?)\n(.*?)```"
        matches = re.findall(pattern, markdown_text, re.DOTALL)
        if matches:
            code = matches[0][1].strip()
            return code
        return None

    def read_json(self, file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            json_data = json.load(file)
            self.json = json_data
        return json_data

    def write_json(self, content, file_path):
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(content, file, ensure_ascii=False, indent=4)
