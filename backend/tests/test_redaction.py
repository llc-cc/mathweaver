import json

import api_v2
from storage.redaction import redact_structure, redact_text


def test_redact_structure_removes_nested_credentials_and_signed_url_queries():
    payload = {
        "api_key": "sk-secret",
        "nested": [{"authorization": "Bearer token-secret"}],
        "download_url": "https://oss.example/a.pdf?OSSAccessKeyId=id&Signature=sig&Expires=9",
        "ordinary": "keep-me",
    }

    redacted = redact_structure(payload)

    assert redacted["api_key"] == "***"
    assert redacted["nested"][0]["authorization"] == "***"
    assert redacted["download_url"] == "https://oss.example/a.pdf"
    assert redacted["ordinary"] == "keep-me"
    assert payload["api_key"] == "sk-secret"


def test_redact_text_masks_explicit_secret_without_exposing_it_in_replacement():
    message = "provider rejected sk-secret at https://oss.example/a?signature=sig"

    redacted = redact_text(message, secrets=("sk-secret",))

    assert "sk-secret" not in redacted
    assert "signature=" not in redacted.lower()
    assert "provider rejected ***" in redacted


def test_export_json_bytes_redacts_credential_shaped_fields():
    exported = json.loads(api_v2._export_json_bytes([
        {"id": 1, "api_key": "must-not-export", "content": "safe"}
    ]))

    assert exported == [{"id": 1, "api_key": "***", "content": "safe"}]


def test_redact_structure_handles_cycles_and_preserves_safe_query_parameters():
    payload = {"url": "https://oss.example/a?Signature=sig&download=1"}
    payload["cycle"] = payload

    redacted = redact_structure(payload)

    assert redacted["url"] == "https://oss.example/a?download=1"
    assert redacted["cycle"] == "[REDACTED:CYCLE]"
