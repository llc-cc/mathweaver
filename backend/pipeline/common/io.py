import json
import os
import hashlib
import uuid


def ensure_parent_dir(path):
    if not path:
        return
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)


def save_stage_json(output_dir, filename, data, label):
    ensure_parent_dir(os.path.join(output_dir, filename))
    save_path = os.path.join(output_dir, filename)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print(f"Saved {label} to: {save_path}")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def atomic_write_json(path, data):
    ensure_parent_dir(path)
    temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, path)


def json_sha256(data):
    payload = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_default_analysis_output_path(input_node_path):
    abs_path = os.path.abspath(input_node_path)
    directory = os.path.dirname(abs_path)
    filename = os.path.basename(abs_path)
    stem, ext = os.path.splitext(filename)
    if ext.lower() != ".json":
        return os.path.join(directory, f"{filename}_analysis.json")
    return os.path.join(directory, f"{stem}_analysis.json")

