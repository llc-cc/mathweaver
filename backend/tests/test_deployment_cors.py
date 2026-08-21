from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROBE_CODE = r"""
import json
import sys
import types

join_agent = types.ModuleType("JoinAgent")
for name in ("LLMParser", "SimpleLLM", "TextDivider", "MultiProcessor"):
    setattr(join_agent, name, type(name, (), {}))
sys.modules["JoinAgent"] = join_agent

import api_v2

response = api_v2.app.test_client().get(
    "/api/v2/ping",
    headers={"Origin": sys.argv[1]},
)
print(json.dumps({
    "status": response.status_code,
    "payload": response.get_json(),
    "allow_origin": response.headers.get("Access-Control-Allow-Origin"),
    "allow_credentials": response.headers.get("Access-Control-Allow-Credentials"),
}))
"""


def _probe_cors(
    origin: str,
    *,
    allowed_origins: str | None,
    desktop: bool = False,
    database_url: str | None = None,
    database_name: str | None = None,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment.pop("MATHWEAVER_ALLOWED_ORIGINS", None)
    environment.pop("AI4MATH_DESKTOP", None)
    environment.pop("MATHWEAVER_DATABASE_URL", None)
    environment.pop("MATHWEAVER_DATABASE_NAME", None)
    if allowed_origins is not None:
        environment["MATHWEAVER_ALLOWED_ORIGINS"] = allowed_origins
    if desktop:
        environment["AI4MATH_DESKTOP"] = "1"
    else:
        environment["MATHWEAVER_DATABASE_URL"] = (
            database_url or "sqlite+pysqlite:///:memory:"
        )
        if database_name is not None:
            environment["MATHWEAVER_DATABASE_NAME"] = database_name

    with tempfile.TemporaryDirectory(prefix="mathweaver-cors-") as data_dir:
        environment["MATHGRAPH_DATA_DIR"] = data_dir
        result = subprocess.run(
            [sys.executable, "-c", PROBE_CODE, origin],
            cwd=BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_web_mode_without_allowed_origins_does_not_enable_cross_origin_access():
    result = _probe_cors(
        "https://outside.example",
        allowed_origins="   ",
    )

    assert result == {
        "status": 200,
        "payload": {"ok": True},
        "allow_origin": None,
        "allow_credentials": None,
    }


def test_web_mode_allows_only_trimmed_configured_origins():
    configured = " https://math.example.edu.cn, https://admin.example.edu.cn "

    allowed = _probe_cors(
        "https://math.example.edu.cn",
        allowed_origins=configured,
    )
    denied = _probe_cors(
        "https://outside.example",
        allowed_origins=configured,
    )

    assert allowed["allow_origin"] == "https://math.example.edu.cn"
    assert denied["allow_origin"] is None
    assert allowed["allow_credentials"] is None


def test_legacy_desktop_mode_keeps_noncredentialed_cors_compatibility():
    result = _probe_cors(
        "http://127.0.0.1:3000",
        allowed_origins=None,
        desktop=True,
    )

    assert result["status"] == 200
    assert result["allow_origin"] == "http://127.0.0.1:3000"
    assert result["allow_credentials"] is None


def test_web_ping_reports_database_unavailable_without_connection_details():
    result = _probe_cors(
        "https://math.example.edu.cn",
        allowed_origins=None,
        database_url=(
            "mysql+pymysql://user:secret-sentinel@127.0.0.1:1/"
            "mathweaver?connect_timeout=1"
        ),
        database_name="mathweaver",
    )

    assert result["status"] == 503
    assert result["payload"] == {"ok": False, "error": "database_unavailable"}
    assert "secret-sentinel" not in json.dumps(result)

