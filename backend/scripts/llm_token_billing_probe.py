"""Send one controlled request to inspect an OpenAI-compatible LLM's usage.

Edit the configuration constants below before running this file.  The script
intentionally makes exactly one request and never retries it, because a retry
could create a second billable request.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Edit these values for the provider you want to test.
# ---------------------------------------------------------------------------
API_URL = "https://yxai.chat/v1"
API_KEY = "yi-LyBaIntHZgUj27mzYGXcLJfznB8JqLrxgGPG"
MODEL_NAME = "gpt-5.6-sol"

TIMEOUT_SECONDS = 300
OUTPUT_JSON_PATH = "token_billing_probe.json"

# The repeated marker makes the user content approximately 1000 tokens for
# common BPE tokenizers.  The provider's returned prompt_tokens is the billing
# source of truth, because tokenizers and chat-message overhead vary.
INPUT_MARKER = "token"
INPUT_MARKER_COUNT = 1000
MAX_OUTPUT_TOKENS = 1000


def normalize_chat_completions_url(api_url: str) -> str:
    """Return the Chat Completions endpoint for a base or full API URL."""

    normalized = api_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("API_URL 不能为空")
    if normalized.lower().endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def build_prompt() -> str:
    """Build a stable, approximately 1000-token input prompt."""

    marker_text = " ".join([INPUT_MARKER] * INPUT_MARKER_COUNT)
    instruction = (
        "Return a long plain-text response and use the available output limit "
        "as much as possible. Do not explain this instruction.\n\n"
    )
    return instruction + marker_text


def get_request_id(response: requests.Response) -> str | None:
    """Read common provider request-id header spellings."""

    for header_name in ("x-request-id", "request-id", "x-request_id"):
        value = response.headers.get(header_name)
        if value:
            return value
    return None


def get_usage_value(usage: dict[str, Any], key: str) -> Any:
    """Read a usage field without assuming every provider returns one."""

    value = usage.get(key)
    if value is not None:
        return value

    # Some compatible providers use OpenAI's nested prompt-token details.
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict) and key == "cached_tokens":
        return prompt_details.get("cached_tokens")
    return None


def redact(value: str) -> str:
    """Remove the configured key from an error or diagnostic string."""

    if API_KEY and API_KEY not in {"在这里填写 API Key", "your-api-key"}:
        return value.replace(API_KEY, "[REDACTED]")
    return value


def write_json(path_text: str, payload: dict[str, Any]) -> None:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_configuration() -> None:
    if not API_KEY.strip() or API_KEY in {"在这里填写 API Key", "your-api-key"}:
        raise ValueError("请先在脚本顶部填写 API_KEY")
    if not MODEL_NAME.strip() or MODEL_NAME == "your-model":
        raise ValueError("请先在脚本顶部填写 MODEL_NAME")
    if not isinstance(TIMEOUT_SECONDS, (int, float)) or TIMEOUT_SECONDS <= 0:
        raise ValueError("TIMEOUT_SECONDS 必须是正数")


def extract_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""
    message = first_choice.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def build_result(
    *,
    endpoint: str,
    prompt: str,
    elapsed_seconds: float,
    response: requests.Response,
    response_json: dict[str, Any],
) -> dict[str, Any]:
    usage = response_json.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    choices = response_json.get("choices")
    finish_reason = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        finish_reason = choices[0].get("finish_reason")

    content = extract_content(response_json)
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": {
            "endpoint": endpoint,
            "model": MODEL_NAME,
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "input_marker": INPUT_MARKER,
            "input_marker_count": INPUT_MARKER_COUNT,
            "prompt_characters": len(prompt),
        },
        "response": {
            "http_status": response.status_code,
            "request_id": get_request_id(response),
            "model": response_json.get("model"),
            "finish_reason": finish_reason,
            "prompt_tokens": get_usage_value(usage, "prompt_tokens"),
            "completion_tokens": get_usage_value(usage, "completion_tokens"),
            "total_tokens": get_usage_value(usage, "total_tokens"),
            "cached_tokens": get_usage_value(usage, "cached_tokens"),
            "usage": usage,
            "response_characters": len(content),
            "content": content,
        },
        "raw_response": response_json,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def main() -> int:
    try:
        validate_configuration()
        endpoint = normalize_chat_completions_url(API_URL)
        prompt = build_prompt()
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        "n": 1,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "pdfPipeline-token-billing-probe/1.0",
    }

    session = requests.Session()
    # Avoid silently routing this billing probe through machine-level proxy
    # variables.  Configure an explicit proxy here if the provider requires it.
    session.trust_env = False

    started = time.perf_counter()
    try:
        response = session.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
        elapsed_seconds = time.perf_counter() - started
    except requests.RequestException as exc:
        elapsed_seconds = time.perf_counter() - started
        error_payload = {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "endpoint": endpoint,
                "model": MODEL_NAME,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "input_marker_count": INPUT_MARKER_COUNT,
            },
            "error": {
                "type": type(exc).__name__,
                "message": redact(str(exc)),
            },
            "elapsed_seconds": round(elapsed_seconds, 3),
        }
        write_json(OUTPUT_JSON_PATH, error_payload)
        print(f"请求失败，已保存诊断：{OUTPUT_JSON_PATH}", file=sys.stderr)
        print(redact(str(exc)), file=sys.stderr)
        return 1

    if not response.ok:
        error_payload = {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "endpoint": endpoint,
                "model": MODEL_NAME,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "input_marker_count": INPUT_MARKER_COUNT,
            },
            "response": {
                "http_status": response.status_code,
                "request_id": get_request_id(response),
                "body": redact(response.text[:4000]),
            },
            "elapsed_seconds": round(elapsed_seconds, 3),
        }
        write_json(OUTPUT_JSON_PATH, error_payload)
        print(f"HTTP {response.status_code}，已保存诊断：{OUTPUT_JSON_PATH}", file=sys.stderr)
        print(redact(response.text[:1000]), file=sys.stderr)
        return 1

    try:
        response_json = response.json()
    except ValueError as exc:
        elapsed_seconds = time.perf_counter() - started
        error_payload = {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "endpoint": endpoint,
                "model": MODEL_NAME,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "input_marker_count": INPUT_MARKER_COUNT,
            },
            "error": {
                "type": type(exc).__name__,
                "message": redact(str(exc)),
            },
            "elapsed_seconds": round(elapsed_seconds, 3),
        }
        write_json(OUTPUT_JSON_PATH, error_payload)
        print(f"响应不是有效 JSON，已保存诊断：{OUTPUT_JSON_PATH}", file=sys.stderr)
        print(redact(str(exc)), file=sys.stderr)
        return 1

    result = build_result(
        endpoint=endpoint,
        prompt=prompt,
        elapsed_seconds=elapsed_seconds,
        response=response,
        response_json=response_json,
    )
    write_json(OUTPUT_JSON_PATH, result)

    response_data = result["response"]
    print("请求成功（本次脚本只发送了 1 个请求）")
    print(f"endpoint: {endpoint}")
    print(f"model: {response_data['model'] or MODEL_NAME}")
    print(f"request_id: {response_data['request_id'] or '(provider did not return one)'}")
    print(f"prompt_tokens: {response_data['prompt_tokens']}")
    print(f"completion_tokens: {response_data['completion_tokens']}")
    print(f"total_tokens: {response_data['total_tokens']}")
    print(f"cached_tokens: {response_data['cached_tokens']}")
    print(f"finish_reason: {response_data['finish_reason']}")
    print(f"result_json: {OUTPUT_JSON_PATH}")
    print("请以以上 usage、request_id 和供应商后台账单作为实际扣费核对依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
