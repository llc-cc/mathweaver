import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .io import write_json


def _utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_sort_key(value):
    try:
        return (0, int(str(value)))
    except (TypeError, ValueError):
        return (1, str(value))


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"


def _format_template(template, data_template, mapping):
    values = dict(mapping or {})
    values["data_template"] = data_template
    for index in range(1, 11):
        key = f"data_template{str(index).zfill(2)}"
        values.setdefault(key, data_template)
    return template.format_map(_SafeDict(values))


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


class ClaudeCliEngine:
    """Batch-oriented Claude Code CLI engine that returns current stage JSON candidates."""

    def __init__(
        self,
        *,
        stage_name,
        output_dir,
        command="claude",
        model=None,
        agent=None,
        batch_size=8,
        timeout_seconds=900,
        max_retries=1,
        run_id=None,
    ):
        self.stage_name = stage_name or "unknown_stage"
        self.output_dir = Path(output_dir)
        self.command = command
        self.model = model
        self.agent = agent
        self.batch_size = max(1, int(batch_size or 1))
        self.timeout_seconds = max(1, int(timeout_seconds or 1))
        self.max_retries = max(0, int(max_retries or 0))
        self.run_id = run_id or uuid.uuid4().hex
        self.run_root = (
            self.output_dir
            / "agent_state"
            / "subagent_runs"
            / self.stage_name
            / self.run_id
        )
        self.run_root.mkdir(parents=True, exist_ok=True)

    def run_tasks(
        self,
        *,
        parse_method,
        data_template,
        prompt_template,
        correction_template,
        validator,
        index_dict,
        checkpoint,
        checkpoint_dir=None,
        active_reload=False,
        active_transform=False,
    ):
        index_dict = dict(index_dict or {})
        # Resolve the executable before entering batch fallback. A missing CLI is
        # an engine-level failure, not one failed extraction result per block.
        self._command_prefix()
        checkpoint_path = self._checkpoint_path(checkpoint_dir)
        results = self._load_checkpoint(checkpoint_path) if active_reload else {}
        remaining_keys = [key for key in index_dict.keys() if str(key) not in results or results.get(str(key)) is None]
        batches = self._make_batches(remaining_keys, index_dict)
        checkpoint_interval = max(1, int(checkpoint or 1))
        processed = 0

        for batch_number, batch in enumerate(batches, start=1):
            batch_result = self._run_batch_with_fallback(
                batch_number=batch_number,
                batch=batch,
                parse_method=parse_method,
                data_template=data_template,
                prompt_template=prompt_template,
                correction_template=correction_template,
                validator=validator,
                active_transform=active_transform,
            )
            for key, value in batch_result.items():
                if value is not None:
                    results[str(key)] = _json_safe(value)
            processed += len(batch)
            if processed % checkpoint_interval == 0:
                write_json(str(checkpoint_path), results)

        write_json(str(checkpoint_path), results)
        missing_keys = [str(key) for key in index_dict.keys() if str(key) not in results]
        if missing_keys:
            raise RuntimeError(
                f"Claude CLI failed to produce {len(missing_keys)} of {len(index_dict)} required source blocks. "
                "The stage result was rejected to protect the canonical cache. "
                f"Missing keys: {missing_keys[:20]}"
            )
        return {key: results[str(key)] for key in index_dict.keys() if str(key) in results}

    def _checkpoint_path(self, checkpoint_dir):
        base = Path(checkpoint_dir) if checkpoint_dir else self.run_root
        base.mkdir(parents=True, exist_ok=True)
        return base / f"checkpoint_claude_cli_{self.stage_name}.json"

    def _load_checkpoint(self, path):
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _make_batches(self, keys, index_dict):
        ordered_keys = sorted(keys, key=_safe_sort_key)
        return [
            [(key, index_dict[key]) for key in ordered_keys[index:index + self.batch_size]]
            for index in range(0, len(ordered_keys), self.batch_size)
        ]

    def _run_batch_with_fallback(self, **kwargs):
        batch = kwargs["batch"]
        try:
            return self._run_batch_with_retries(**kwargs)
        except Exception as batch_error:
            if len(batch) == 1:
                key = batch[0][0]
                self._write_batch_error(kwargs["batch_number"], [str(key)], batch_error)
                return {key: None}

            fallback_results = {}
            for offset, item in enumerate(batch, start=1):
                single_kwargs = dict(kwargs)
                single_kwargs["batch"] = [item]
                single_kwargs["batch_number"] = int(f"{kwargs['batch_number']}{offset:03d}")
                try:
                    fallback_results.update(self._run_batch_with_retries(**single_kwargs))
                except Exception as item_error:
                    key = item[0]
                    self._write_batch_error(single_kwargs["batch_number"], [str(key)], item_error)
                    fallback_results[key] = None
            return fallback_results

    def _run_batch_with_retries(self, **kwargs):
        last_error = None
        for attempt in range(1, self.max_retries + 2):
            try:
                return self._run_batch_once(attempt=attempt, **kwargs)
            except Exception as error:
                last_error = error
        raise last_error

    def _run_batch_once(
        self,
        *,
        batch_number,
        batch,
        parse_method,
        data_template,
        prompt_template,
        correction_template,
        validator,
        active_transform,
        attempt,
    ):
        batch_dir = self.run_root / f"batch_{batch_number:04d}" / f"attempt_{attempt}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        task_packet = self._build_task_packet(batch)
        prompt = self._build_prompt(task_packet, prompt_template, data_template)
        write_json(str(batch_dir / "task_packet.json"), task_packet)
        (batch_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        stdout, stderr, returncode = self._call_claude(prompt)
        (batch_dir / "raw_stdout.txt").write_text(stdout, encoding="utf-8")
        (batch_dir / "stderr.txt").write_text(stderr, encoding="utf-8")

        if returncode != 0:
            self._write_run_meta(batch_dir, task_packet, returncode, "cli_error")
            raise RuntimeError(f"Claude CLI exited with {returncode}: {stderr.strip()}")

        try:
            candidate = self._parse_candidate(stdout, parse_method)
            normalized = self._normalize_batch_result(candidate, [key for key, _ in batch], active_transform)
            self._validate_batch(normalized, validator)
        except Exception:
            correction_prompt = self._build_correction_prompt(stdout, correction_template, data_template)
            (batch_dir / "correction_prompt.md").write_text(correction_prompt, encoding="utf-8")
            correction_stdout, correction_stderr, correction_code = self._call_claude(correction_prompt)
            (batch_dir / "correction_stdout.txt").write_text(correction_stdout, encoding="utf-8")
            (batch_dir / "correction_stderr.txt").write_text(correction_stderr, encoding="utf-8")
            if correction_code != 0:
                self._write_run_meta(batch_dir, task_packet, correction_code, "correction_cli_error")
                raise RuntimeError(f"Claude correction exited with {correction_code}: {correction_stderr.strip()}")
            candidate = self._parse_candidate(correction_stdout, parse_method)
            normalized = self._normalize_batch_result(candidate, [key for key, _ in batch], active_transform)
            self._validate_batch(normalized, validator)

        write_json(str(batch_dir / "candidate_output.json"), normalized)
        self._write_run_meta(batch_dir, task_packet, returncode, "ok")
        return normalized

    def _build_task_packet(self, batch):
        return {
            "schema_version": 1,
            "stage": self.stage_name,
            "created_at": _utc_timestamp(),
            "batch_size": len(batch),
            "source_blocks": [
                {
                    "source_block_key": str(key),
                    "payload": _json_safe(value),
                }
                for key, value in batch
            ],
            "output_contract": {
                "top_level_keys": [str(key) for key, _ in batch],
                "value_contract": "Each value must match the current stage JSON candidate for that source block.",
            },
        }

    def _build_prompt(self, task_packet, prompt_template, data_template):
        batch_input = self._render_batch_input(task_packet)
        base_prompt = _format_template(prompt_template, data_template, {"pos1": batch_input})
        adapter = (
            "\n\n# Claude Code batch adapter\n"
            "You are running inside a stage wrapper. Return only a JSON object.\n"
            "The JSON object's top-level keys must exactly match the source_block_key values in the batch.\n"
            "Each top-level value must use the same candidate JSON shape required by the original stage prompt.\n"
            "Do not write files. Do not explain. Do not add markdown fences.\n"
            f"Required top-level keys: {json.dumps(task_packet['output_contract']['top_level_keys'], ensure_ascii=False)}\n"
        )
        return base_prompt + adapter

    def _render_batch_input(self, task_packet):
        lines = [
            "The following input is a batch of source blocks. Process every block independently.",
            "Return one top-level JSON entry per source_block_key.",
            "",
        ]
        for item in task_packet["source_blocks"]:
            lines.append(f'<source_block key="{item["source_block_key"]}">')
            lines.append(json.dumps(item["payload"], ensure_ascii=False, indent=2))
            lines.append("</source_block>")
            lines.append("")
        return "\n".join(lines)

    def _build_correction_prompt(self, answer, correction_template, data_template):
        return _format_template(correction_template, data_template, {"answer": answer}) + (
            "\n\nReturn only corrected JSON. Do not add markdown fences or explanation."
        )

    def _call_claude(self, prompt):
        command = self._command_prefix()
        command.extend(["-p", "--output-format", "json", "--no-session-persistence"])
        if self.model:
            command.extend(["--model", self.model])
        if self.agent:
            command.extend(["--agent", self.agent])
        completed = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_seconds,
            cwd=str(Path.cwd()),
        )
        return completed.stdout, completed.stderr, completed.returncode

    def run_prompt(self, prompt, run_dir=None):
        run_dir = Path(run_dir) if run_dir else self.run_root / "direct_prompt"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        stdout, stderr, returncode = self._call_claude(prompt)
        (run_dir / "raw_stdout.txt").write_text(stdout, encoding="utf-8")
        (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        write_json(
            str(run_dir / "run_meta.json"),
            {
                "schema_version": 1,
                "stage": self.stage_name,
                "run_id": self.run_id,
                "status": "ok" if returncode == 0 else "cli_error",
                "returncode": returncode,
                "updated_at": _utc_timestamp(),
                "command": self._command_prefix(),
            },
        )
        if returncode != 0:
            raise RuntimeError(f"Claude CLI exited with {returncode}: {stderr.strip()}")
        return self._extract_cli_text(stdout)

    def _command_prefix(self):
        if isinstance(self.command, (list, tuple)):
            return [str(item) for item in self.command]
        command_text = str(self.command)
        if Path(command_text).exists():
            return [command_text]
        if os.name == "nt" and command_text.lower() in {"claude", "claude.cmd"}:
            resolved = shutil.which("claude.cmd")
            if resolved:
                return [resolved]
            appdata = os.environ.get("APPDATA")
            if appdata:
                npm_claude = Path(appdata) / "npm" / "claude.cmd"
                if npm_claude.exists():
                    return [str(npm_claude)]
            raise FileNotFoundError(
                "Claude CLI was not found on Windows. Install Claude Code or pass "
                "--claude-command C:\\Users\\<user>\\AppData\\Roaming\\npm\\claude.cmd."
            )
        resolved = shutil.which(command_text)
        if resolved:
            return [resolved]
        return shlex.split(command_text, posix=os.name != "nt")

    def _parse_candidate(self, stdout, parse_method):
        text = self._extract_cli_text(stdout)
        return parse_method(text)

    def _extract_cli_text(self, stdout):
        raw = (stdout or "").strip()
        if not raw:
            return raw
        try:
            wrapper = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(wrapper, dict):
            for key in ("result", "content", "response"):
                value = wrapper.get(key)
                if isinstance(value, str):
                    return value
            message = wrapper.get("message")
            text = self._message_text(message)
            if text:
                return text
            return json.dumps(wrapper, ensure_ascii=False)
        return json.dumps(wrapper, ensure_ascii=False)

    def _message_text(self, message):
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return self._message_text(message.get("content"))
        if isinstance(message, list):
            parts = []
            for item in message:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return ""

    def _normalize_batch_result(self, candidate, batch_keys, active_transform):
        if not isinstance(candidate, dict):
            raise ValueError("Claude candidate must parse to a JSON object")
        key_lookup = {str(key): key for key in batch_keys}
        if len(batch_keys) == 1 and str(batch_keys[0]) not in candidate:
            value = candidate
            return {batch_keys[0]: self._map_answer_to_pos(value) if active_transform else value}
        normalized = {}
        for string_key, original_key in key_lookup.items():
            if string_key in candidate:
                value = candidate[string_key]
            elif original_key in candidate:
                value = candidate[original_key]
            else:
                raise ValueError(f"Claude candidate missing source block key: {string_key}")
            normalized[original_key] = self._map_answer_to_pos(value) if active_transform else value
        return normalized

    def _validate_batch(self, normalized, validator):
        for key, value in normalized.items():
            if not validator(value):
                raise ValueError(f"Claude candidate failed validator for source block key: {key}")

    def _map_answer_to_pos(self, answer_dict):
        if not isinstance(answer_dict, dict):
            return answer_dict
        return {f"pos{index}": value for index, (_, value) in enumerate(answer_dict.items(), start=1)}

    def _write_run_meta(self, batch_dir, task_packet, returncode, status):
        meta = {
            "schema_version": 1,
            "stage": self.stage_name,
            "run_id": self.run_id,
            "batch_keys": task_packet["output_contract"]["top_level_keys"],
            "status": status,
            "returncode": returncode,
            "updated_at": _utc_timestamp(),
            "command": self._command_prefix(),
        }
        write_json(str(batch_dir / "run_meta.json"), meta)

    def _write_batch_error(self, batch_number, batch_keys, error):
        error_dir = self.run_root / f"batch_{batch_number:04d}"
        error_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            str(error_dir / "error.json"),
            {
                "schema_version": 1,
                "stage": self.stage_name,
                "run_id": self.run_id,
                "batch_keys": batch_keys,
                "status": "failed",
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "updated_at": _utc_timestamp(),
            },
        )
