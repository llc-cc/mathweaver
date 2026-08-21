from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.docker_entrypoint import load_runtime_environment


def test_runtime_environment_loader_preserves_fixed_values_and_stays_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text(
        "MATHWEAVER_DATABASE_URL='mysql+pymysql://app:p%40ss@db/mathweaver'\n"
        "MATHWEAVER_DATABASE_NAME=uniprism_alphatest_user\n"
        "PDFPIPELINE_API_KEY=model-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATHWEAVER_DATABASE_NAME", "mathweaver")
    monkeypatch.delenv("MATHWEAVER_DATABASE_URL", raising=False)
    monkeypatch.delenv("PDFPIPELINE_API_KEY", raising=False)

    load_runtime_environment(runtime_env)

    assert os.environ["MATHWEAVER_DATABASE_NAME"] == "mathweaver"
    assert os.environ["MATHWEAVER_DATABASE_URL"].endswith("/mathweaver")
    assert os.environ["PDFPIPELINE_API_KEY"] == "model-secret"
    assert capsys.readouterr() == ("", "")


def test_runtime_environment_loader_has_stable_missing_file_error(tmp_path: Path):
    missing = tmp_path / "database-secret-do-not-log.env"

    with pytest.raises(RuntimeError, match="runtime environment loading failed") as error:
        load_runtime_environment(missing)

    assert str(missing) not in str(error.value)

