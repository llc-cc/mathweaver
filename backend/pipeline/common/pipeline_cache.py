import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .io import atomic_write_json, json_sha256, read_json


SCHEMA_VERSION = 1
_TYPE_KEY = "__pipeline_cache_type__"


class PipelineCacheError(RuntimeError):
    pass


def _encode_key(key):
    if isinstance(key, bool):
        return {"type": "bool", "value": key}
    if isinstance(key, int):
        return {"type": "int", "value": key}
    if isinstance(key, float):
        return {"type": "float", "value": key}
    if key is None:
        return {"type": "none", "value": None}
    return {"type": "str", "value": str(key)}


def _decode_key(key):
    kind = key.get("type")
    value = key.get("value")
    if kind == "bool":
        return bool(value)
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "none":
        return None
    return str(value)


def encode_cache_value(value):
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value) and _TYPE_KEY not in value:
            return {key: encode_cache_value(item) for key, item in value.items()}
        return {
            _TYPE_KEY: "mapping",
            "items": [
                [_encode_key(key), encode_cache_value(item)]
                for key, item in value.items()
            ],
        }
    if isinstance(value, tuple):
        return {_TYPE_KEY: "tuple", "items": [encode_cache_value(item) for item in value]}
    if isinstance(value, set):
        ordered = sorted(value, key=repr)
        return {_TYPE_KEY: "set", "items": [encode_cache_value(item) for item in ordered]}
    if isinstance(value, list):
        return [encode_cache_value(item) for item in value]
    return value


def decode_cache_value(value):
    if isinstance(value, list):
        return [decode_cache_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    kind = value.get(_TYPE_KEY)
    if kind == "mapping":
        return {
            _decode_key(key): decode_cache_value(item)
            for key, item in value.get("items") or []
        }
    if kind == "tuple":
        return tuple(decode_cache_value(item) for item in value.get("items") or [])
    if kind == "set":
        return set(decode_cache_value(item) for item in value.get("items") or [])
    return {key: decode_cache_value(item) for key, item in value.items()}


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value_sha256(value):
    return json_sha256(encode_cache_value(value))


def _plan_sha256(plan, options):
    return json_sha256(
        {
            "stages": [
                {
                    "key": stage.key,
                    "requires": list(stage.requires),
                    "consumes": list(stage.consumes),
                    "produces": list(stage.produces),
                    "nonempty": list(stage.nonempty),
                }
                for stage in plan
            ],
            "options": dict(options or {}),
        }
    )


class PipelineStageCache:
    def __init__(self, context, plan, *, options=None):
        self.context = context
        self.plan = tuple(plan)
        self.root = Path(getattr(context, "stage_cache_dir", context.output_dir))
        self.stages_root = self.root / "stages"
        self.manifest_path = self.root / "manifest.json"
        self.options = dict(options or {})
        self.source_sha256 = _file_sha256(context.file_path)
        self.plan_sha256 = _plan_sha256(self.plan, self.options)
        self.manifest = None

    def _new_manifest(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "source": {
                "filename": os.path.basename(self.context.file_path),
                "sha256": self.source_sha256,
                "format": getattr(self.context, "source_format", "auto"),
                "origin": getattr(self.context, "source_origin", "markdown"),
            },
            "plan_sha256": self.plan_sha256,
            "options": self.options,
            "status": "running",
            "current_stage": None,
            "completed_stages": [],
            "stages": {},
        }

    def initialize(self, *, resume, plan_migrations=()):
        self.root.mkdir(parents=True, exist_ok=True)
        self.stages_root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            try:
                manifest = read_json(str(self.manifest_path))
            except (OSError, json.JSONDecodeError) as exc:
                raise PipelineCacheError(f"Pipeline cache manifest is invalid: {exc}") from exc
            if resume and manifest.get("plan_sha256") != self.plan_sha256:
                manifest = self._migrate_plan(manifest, plan_migrations)
            self._validate_manifest(manifest)
            if not resume:
                raise PipelineCacheError(
                    f"Pipeline cache already exists and resume_from_cache is false: {self.manifest_path}"
                )
            self.manifest = manifest
        else:
            self.manifest = self._new_manifest()
            self._write_manifest()
        return self.manifest

    def _migrate_plan(self, manifest, plan_migrations):
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
            raise PipelineCacheError("Unsupported pipeline cache manifest schema.")
        source = manifest.get("source") or {}
        if source.get("sha256") != self.source_sha256:
            raise PipelineCacheError("Pipeline cache source hash does not match the current input.")

        current_keys = tuple(stage.key for stage in self.plan)
        for migration in plan_migrations or ():
            legacy_plan = tuple(migration.get("legacy_plan") or ())
            shared_stage_keys = tuple(migration.get("shared_stage_keys") or ())
            legacy_keys = tuple(stage.key for stage in legacy_plan)
            if manifest.get("plan_sha256") != _plan_sha256(legacy_plan, self.options):
                continue
            if (
                not shared_stage_keys
                or legacy_keys[: len(shared_stage_keys)] != shared_stage_keys
                or current_keys[: len(shared_stage_keys)] != shared_stage_keys
            ):
                raise PipelineCacheError("Pipeline cache migration does not share the required stage prefix.")

            existing_records = manifest.get("stages") or {}
            completed = [
                key
                for key in manifest.get("completed_stages") or []
                if key in shared_stage_keys
            ]
            preserved_records = {
                key: existing_records[key]
                for key in shared_stage_keys
                if key in existing_records
            }
            history = list(manifest.get("migration_history") or [])
            history.append(
                {
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "reason": str(migration.get("reason") or "compatible_plan_migration"),
                    "from_plan_sha256": manifest.get("plan_sha256"),
                    "to_plan_sha256": self.plan_sha256,
                    "preserved_stages": list(shared_stage_keys),
                    "invalidated_stages": list(legacy_keys[len(shared_stage_keys) :]),
                }
            )
            manifest["plan_sha256"] = self.plan_sha256
            manifest["stages"] = preserved_records
            manifest["completed_stages"] = completed
            manifest["current_stage"] = next(
                (key for key in current_keys if key not in completed),
                None,
            )
            manifest["status"] = "running"
            manifest["migration_history"] = history
            atomic_write_json(str(self.manifest_path), manifest)
            return manifest

        return manifest

    def _validate_manifest(self, manifest):
        if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
            raise PipelineCacheError("Unsupported pipeline cache manifest schema.")
        source = manifest.get("source") or {}
        if source.get("sha256") != self.source_sha256:
            raise PipelineCacheError("Pipeline cache source hash does not match the current input.")
        if manifest.get("plan_sha256") != self.plan_sha256:
            raise PipelineCacheError("Pipeline cache plan/options do not match the current pipeline.")

    def _write_manifest(self):
        atomic_write_json(str(self.manifest_path), self.manifest)

    def _stage_dir(self, index, stage):
        return self.stages_root / f"{index + 1:02d}_{stage.key}"

    def capture_fingerprints(self, state):
        return {str(key): _value_sha256(value) for key, value in state.items()}

    def write_stage_input(self, index, stage, state):
        stage_dir = self._stage_dir(index, stage)
        stage_dir.mkdir(parents=True, exist_ok=True)
        consumed_keys = []
        for key in (*stage.requires, *stage.consumes):
            if key not in consumed_keys:
                consumed_keys.append(key)
        values = {key: state[key] for key in consumed_keys if key in state}
        predecessor = None
        if index:
            previous = self.manifest.get("stages", {}).get(self.plan[index - 1].key) or {}
            predecessor = previous.get("output_sha256")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "stage_input",
            "stage": stage.key,
            "stage_index": index,
            "predecessor_output_sha256": predecessor,
            "values": encode_cache_value(values),
        }
        if index == 0:
            payload["source"] = {
                "filename": os.path.basename(self.context.file_path),
                "sha256": self.source_sha256,
                "format": getattr(self.context, "source_format", "auto"),
                "origin": getattr(self.context, "source_origin", "markdown"),
            }
        input_path = stage_dir / "input.json"
        atomic_write_json(str(input_path), payload)
        input_sha256 = json_sha256(payload)
        record = self.manifest.setdefault("stages", {}).setdefault(stage.key, {})
        record.update(
            {
                "index": index,
                "status": "running",
                "input_sha256": input_sha256,
            }
        )
        self.manifest["status"] = "running"
        self.manifest["current_stage"] = stage.key
        self._write_manifest()
        return input_sha256

    def write_stage_output(self, index, stage, before_fingerprints, state):
        changed = {
            key: value
            for key, value in state.items()
            if before_fingerprints.get(str(key)) != _value_sha256(value)
        }
        removed = [
            key
            for key in before_fingerprints
            if key not in {str(current_key) for current_key in state}
        ]
        predecessor = None
        if index:
            previous = self.manifest.get("stages", {}).get(self.plan[index - 1].key) or {}
            predecessor = previous.get("output_sha256")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "stage_output",
            "stage": stage.key,
            "stage_index": index,
            "predecessor_output_sha256": predecessor,
            "values": encode_cache_value(changed),
            "removed_keys": removed,
        }
        output_path = self._stage_dir(index, stage) / "output.json"
        atomic_write_json(str(output_path), payload)
        output_sha256 = json_sha256(payload)
        record = self.manifest.setdefault("stages", {}).setdefault(stage.key, {})
        record.update(
            {
                "index": index,
                "status": "completed",
                "output_sha256": output_sha256,
            }
        )
        completed = [
            item.key
            for item in self.plan[: index + 1]
            if (self.manifest.get("stages", {}).get(item.key) or {}).get("status") == "completed"
        ]
        self.manifest["completed_stages"] = completed
        self.manifest["current_stage"] = (
            self.plan[index + 1].key if index + 1 < len(self.plan) else None
        )
        self._write_manifest()
        return output_sha256

    def load_resume_state(self, validate_stage):
        state = {}
        predecessor = None
        completed = []
        reconciled_records = {}
        first_missing = len(self.plan)
        for index, stage in enumerate(self.plan):
            output_path = self._stage_dir(index, stage) / "output.json"
            if not output_path.exists():
                first_missing = index
                break
            try:
                payload = read_json(str(output_path))
            except (OSError, json.JSONDecodeError) as exc:
                raise PipelineCacheError(
                    f"Stage cache output is invalid for {stage.key}: {exc}"
                ) from exc
            if (
                payload.get("schema_version") != SCHEMA_VERSION
                or payload.get("kind") != "stage_output"
                or payload.get("stage") != stage.key
                or payload.get("stage_index") != index
            ):
                raise PipelineCacheError(f"Stage cache output metadata is invalid for {stage.key}.")
            if payload.get("predecessor_output_sha256") != predecessor:
                raise PipelineCacheError(f"Stage cache output chain is broken at {stage.key}.")
            values = decode_cache_value(payload.get("values") or {})
            if not isinstance(values, dict):
                raise PipelineCacheError(f"Stage cache output values are invalid for {stage.key}.")
            for key in payload.get("removed_keys") or []:
                state.pop(key, None)
            state.update(values)
            validate_stage(stage, state)
            predecessor = json_sha256(payload)
            completed.append(stage.key)
            existing = (self.manifest.get("stages", {}).get(stage.key) or {})
            expected = existing.get("output_sha256")
            if expected and expected != predecessor:
                raise PipelineCacheError(f"Stage cache output hash is invalid for {stage.key}.")
            reconciled_records[stage.key] = {
                **existing,
                "index": index,
                "status": "completed",
                "output_sha256": predecessor,
            }

        for index in range(first_missing + 1, len(self.plan)):
            stage = self.plan[index]
            if (self._stage_dir(index, stage) / "output.json").exists():
                raise PipelineCacheError(
                    f"Stage cache is non-contiguous: {stage.key} exists after an incomplete stage."
                )

        existing_records = self.manifest.get("stages", {})
        if first_missing < len(self.plan):
            current = self.plan[first_missing]
            if current.key in existing_records:
                reconciled_records[current.key] = existing_records[current.key]
        self.manifest["stages"] = reconciled_records
        self.manifest["completed_stages"] = completed
        self.manifest["current_stage"] = (
            self.plan[first_missing].key if first_missing < len(self.plan) else None
        )
        self.manifest["status"] = "running"
        self._write_manifest()
        return state, first_missing, completed

    def mark_status(self, status):
        self.manifest["status"] = status
        self.manifest.pop("error", None)
        self._write_manifest()

    def cleanup_work_dir(self):
        work_dir = getattr(self.context, "stage_work_dir", None)
        if not work_dir:
            return
        work_path = Path(work_dir).resolve()
        cache_parent = self.root.resolve().parent
        if work_path.parent != cache_parent or work_path.name != "_stage_work":
            raise PipelineCacheError(f"Refusing to clean unexpected stage work directory: {work_path}")
        shutil.rmtree(work_path, ignore_errors=True)
