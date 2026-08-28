from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import requests
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import api_v2


COMPLETE_CONFIG = {
    "api_url": "https://aihubmix.com/v1",
    "model_name": "qwen3-max",
    "api_key": "sk-test-secret",
    "embedding_url": "",
    "embedding_model": "qwen3-embedding-8b",
    "embedding_api_key": "",
}


def _ok_result():
    return api_v2._config_validation_result(True, "ok", 12)


def test_missing_fields_return_400_without_probes():
    client = api_v2.app.test_client()
    with (
        patch.object(api_v2, "_probe_chat_config") as chat_probe,
        patch.object(api_v2, "_probe_embedding_config") as embedding_probe,
    ):
        response = client.post("/api/v2/config/validate", json={"api_url": "https://example.test/v1"})

    assert response.status_code == 400
    assert set(response.get_json()["fields"]) == {"model_name", "api_key", "embedding_model"}
    chat_probe.assert_not_called()
    embedding_probe.assert_not_called()


def test_same_provider_fallback_and_no_job_creation():
    client = api_v2.app.test_client()
    captured = {}
    before_jobs = set(api_v2._jobs)

    def chat_probe(config):
        captured["chat"] = dict(config)
        return _ok_result()

    def embedding_probe(config):
        captured["embedding"] = dict(config)
        return _ok_result()

    with (
        patch.object(api_v2, "_provider_url_error", return_value=None),
        patch.object(api_v2, "_probe_chat_config", chat_probe),
        patch.object(api_v2, "_probe_embedding_config", embedding_probe),
    ):
        response = client.post("/api/v2/config/validate", json=COMPLETE_CONFIG)

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert captured["embedding"]["embedding_url"] == COMPLETE_CONFIG["api_url"]
    assert captured["embedding"]["embedding_api_key"] == COMPLETE_CONFIG["api_key"]
    assert set(api_v2._jobs) == before_jobs


def test_targeted_chat_validation_only_runs_chat_probe():
    client = api_v2.app.test_client()
    payload = {
        "target": "chat",
        "api_url": COMPLETE_CONFIG["api_url"],
        "model_name": COMPLETE_CONFIG["model_name"],
        "api_key": COMPLETE_CONFIG["api_key"],
    }
    with (
        patch.object(api_v2, "_provider_url_error", return_value=None),
        patch.object(api_v2, "_probe_chat_config", return_value=_ok_result()) as chat_probe,
        patch.object(api_v2, "_probe_embedding_config") as embedding_probe,
    ):
        response = client.post("/api/v2/config/validate", json=payload)

    body = response.get_json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["llm"]["ok"] is True
    assert "embedding" not in body
    chat_probe.assert_called_once()
    embedding_probe.assert_not_called()


def test_targeted_embedding_validation_only_runs_embedding_probe():
    client = api_v2.app.test_client()
    payload = {
        "target": "embedding",
        "api_url": COMPLETE_CONFIG["api_url"],
        "api_key": COMPLETE_CONFIG["api_key"],
        "embedding_model": COMPLETE_CONFIG["embedding_model"],
    }
    with (
        patch.object(api_v2, "_provider_url_error", return_value=None),
        patch.object(api_v2, "_probe_chat_config") as chat_probe,
        patch.object(api_v2, "_probe_embedding_config", return_value=_ok_result()) as embedding_probe,
    ):
        response = client.post("/api/v2/config/validate", json=payload)

    body = response.get_json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["embedding"]["ok"] is True
    assert "llm" not in body
    chat_probe.assert_not_called()
    embedding_probe.assert_called_once()
    tested_config = embedding_probe.call_args.args[0]
    assert tested_config["embedding_url"] == COMPLETE_CONFIG["api_url"]
    assert tested_config["embedding_api_key"] == COMPLETE_CONFIG["api_key"]


def test_separate_embedding_config_and_partial_failure_are_reported():
    client = api_v2.app.test_client()
    captured = {}

    def embedding_probe(config):
        captured.update(config)
        return api_v2._config_validation_result(False, "model_not_found", 9)

    payload = {
        **COMPLETE_CONFIG,
        "embedding_url": "https://embedding.example.test/v1",
        "embedding_api_key": "embedding-secret",
    }
    with (
        patch.object(api_v2, "_provider_url_error", return_value=None),
        patch.object(api_v2, "_probe_chat_config", return_value=_ok_result()) as chat_probe,
        patch.object(api_v2, "_probe_embedding_config", side_effect=embedding_probe) as embedding_call,
    ):
        response = client.post("/api/v2/config/validate", json=payload)

    body = response.get_json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["llm"]["ok"] is True
    assert body["embedding"]["code"] == "model_not_found"
    assert captured["embedding_url"] == payload["embedding_url"]
    assert captured["embedding_api_key"] == payload["embedding_api_key"]
    chat_probe.assert_called_once()
    embedding_call.assert_called_once()
    assert COMPLETE_CONFIG["api_key"] not in response.get_data(as_text=True)
    assert payload["embedding_api_key"] not in response.get_data(as_text=True)


def test_invalid_url_does_not_prevent_other_probe():
    client = api_v2.app.test_client()

    def url_error(url):
        return "invalid_url" if "bad.example" in url else None

    payload = {**COMPLETE_CONFIG, "api_url": "ftp://bad.example/v1", "embedding_url": "https://ok.example/v1"}
    with (
        patch.object(api_v2, "_provider_url_error", side_effect=url_error),
        patch.object(api_v2, "_probe_chat_config") as chat_probe,
        patch.object(api_v2, "_probe_embedding_config", return_value=_ok_result()) as embedding_probe,
    ):
        response = client.post("/api/v2/config/validate", json=payload)

    body = response.get_json()
    assert body["llm"]["code"] == "invalid_url"
    assert body["embedding"]["ok"] is True
    chat_probe.assert_not_called()
    embedding_probe.assert_called_once()


def test_url_policy_blocks_private_hosts_except_in_desktop_mode():
    private_result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8000))]
    with (
        patch.dict(os.environ, {}, clear=False),
        patch.object(api_v2.socket, "getaddrinfo", return_value=private_result),
    ):
        os.environ.pop("AI4MATH_DESKTOP", None)
        assert api_v2._provider_url_error("http://localhost:8000/v1") == "invalid_url"

    with patch.dict(os.environ, {"AI4MATH_DESKTOP": "1"}):
        assert api_v2._provider_url_error("http://localhost:8000/v1") is None

    assert api_v2._provider_url_error("https://user:pass@example.com/v1") == "invalid_url"
    assert api_v2._provider_url_error("file:///tmp/provider") == "invalid_url"


def test_provider_error_codes_are_stable():
    cases = [
        (SimpleNamespace(status_code=401), "unauthorized"),
        (SimpleNamespace(status_code=403), "unauthorized"),
        (SimpleNamespace(status_code=404), "endpoint_not_found"),
        (SimpleNamespace(status_code=429), "rate_limited"),
        (SimpleNamespace(status_code=408), "timeout"),
        (SimpleNamespace(status_code=500), "provider_error"),
    ]
    for exc, expected in cases:
        assert api_v2._provider_failure_code(exc) == expected
    assert api_v2._provider_failure_code(Exception("model does not exist")) == "model_not_found"
    assert api_v2._provider_failure_code(Exception("connection refused")) == "unreachable"
    assert api_v2._provider_failure_code(Exception("invalid embedding response format")) == "incompatible_response"


def test_real_probe_paths_normalize_urls_limit_timeout_and_redact_key():
    key = "sk-should-never-appear"
    llm = api_v2.SimpleLLM(
        model="chat-model",
        api_url="https://api.example.test/v1/",
        api_key=key,
    )
    assert llm.api_url == "https://api.example.test/v1/chat/completions"
    llm.suppress_error_details = True
    response = requests.Response()
    response.status_code = 401
    response._content = f"provider echoed {key}".encode()
    response.request = requests.Request("POST", llm.api_url).prepare()
    output = StringIO()
    with (
        patch.object(llm.session, "post", return_value=response),
        redirect_stdout(output),
    ):
        try:
            llm.ask("OK", temperature=0)
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("expected HTTPError")
    assert key not in output.getvalue()

    with patch.object(api_v2, "get_embedding", return_value=[[0.1, 0.2]]) as embedding:
        result = api_v2._probe_embedding_config({
            "embedding_api_key": key,
            "embedding_url": "https://api.example.test/v1",
            "embedding_model": "embedding-model",
        })
    assert result["ok"] is True
    embedding.assert_called_once_with(
        "MathWeaver connection test",
        key,
        "https://api.example.test/v1",
        "embedding-model",
        raise_on_failure=True,
        timeout_seconds=30.0,
        max_retries_override=0,
    )


if __name__ == "__main__":
    test_missing_fields_return_400_without_probes()
    test_same_provider_fallback_and_no_job_creation()
    test_targeted_chat_validation_only_runs_chat_probe()
    test_targeted_embedding_validation_only_runs_embedding_probe()
    test_separate_embedding_config_and_partial_failure_are_reported()
    test_invalid_url_does_not_prevent_other_probe()
    test_url_policy_blocks_private_hosts_except_in_desktop_mode()
    test_provider_error_codes_are_stable()
    test_real_probe_paths_normalize_urls_limit_timeout_and_redact_key()
    print("config validation tests passed")
