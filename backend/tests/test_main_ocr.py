from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import run_pipeline
from ocr_runtime import OcrError, OcrManager


def make_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class MainOcrIntegrationTests(unittest.TestCase):
    def test_parse_local_file_uses_shared_markdown_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            source = root / "input.pdf"
            source.write_bytes(make_pdf())
            previous = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR")
            os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = str(runtime)
            try:
                manager = OcrManager(root=root / "state", manifest_path=root / "manifest.json")
                manager.engine.parse = lambda _path, _event: "$$A = B\n$$\n"  # type: ignore[method-assign]
                result = manager.parse_local_file(source)
                self.assertEqual(result["importedText"], "$$A = B\n$$\n")
                self.assertEqual(result["filename"], "input.pdf")
                self.assertEqual(result["source"], "ocr_file")
                self.assertTrue(result["ocr_job_id"])
            finally:
                if previous is None:
                    os.environ.pop("MATHWEAVER_OCR_RUNTIME_DIR", None)
                else:
                    os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = previous

    def test_parse_local_file_rejects_empty_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            source = root / "input.pdf"
            source.write_bytes(make_pdf())
            previous = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR")
            os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = str(runtime)
            try:
                manager = OcrManager(root=root / "state", manifest_path=root / "manifest.json")
                manager.engine.parse = lambda _path, _event: "  \n"  # type: ignore[method-assign]
                with self.assertRaises(OcrError) as context:
                    manager.parse_local_file(source)
                self.assertEqual(context.exception.code, "ocr_output_missing")
            finally:
                if previous is None:
                    os.environ.pop("MATHWEAVER_OCR_RUNTIME_DIR", None)
                else:
                    os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = previous

    def test_parse_local_file_does_not_install_missing_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.pdf"
            source.write_bytes(make_pdf())
            manager = OcrManager(root=root / "state", manifest_path=root / "missing-manifest.json")
            with patch.object(manager, "start_install") as start_install:
                with self.assertRaises(OcrError) as context:
                    manager.parse_local_file(source)
            self.assertEqual(context.exception.code, "ocr_runtime_missing")
            start_install.assert_not_called()

    def test_cli_missing_component_exits_nonzero_with_ocr_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.pdf"
            source.write_bytes(make_pdf())
            env = os.environ.copy()
            env.pop("MATHWEAVER_OCR_RUNTIME_DIR", None)
            env["MATHWEAVER_OCR_DIR"] = str(root / "ocr")
            env["MATHWEAVER_OCR_MANIFEST"] = str(root / "missing-manifest.json")
            env["PYTHONIOENCODING"] = "utf-8"
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    str(source),
                    "--output-root-dir",
                    str(root / "output"),
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("[ocr_runtime_missing]", completed.stdout + completed.stderr)

    def test_main_writes_shared_markdown_without_mineru_directory_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.pdf"
            source.write_bytes(b"%PDF-test")
            output_root = root / "output"
            fake_manager = SimpleNamespace(
                parse_local_file=lambda _path: {
                    "importedText": "# exact\n\n$$x_1$$\n",
                    "filename": "input.pdf",
                    "source": "ocr_file",
                    "ocr_job_id": "cli-test",
                }
            )
            resolved = SimpleNamespace(api_url=None, model_name=None, api_key=None)
            with contextlib.redirect_stdout(io.StringIO()):
                with patch("main.get_ocr_manager", return_value=fake_manager), patch(
                    "main.process_md", return_value=([], [])
                ), patch("main.load_env_file"), patch("main.resolve_llm_config", return_value=resolved):
                    result = run_pipeline(str(source), output_root_dir=str(output_root), return_data=True)
            self.assertEqual(result, {"nodes": [], "edges": []})
            markdown_path = output_root / "input_output" / "input.md"
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), "# exact\n\n$$x_1$$\n")
            self.assertFalse((root / "input" / "hybrid_auto").exists())

    def test_runtime_lock_rejects_second_manager(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager_one = OcrManager(root=root, manifest_path=root / "manifest.json")
            manager_two = OcrManager(root=root, manifest_path=root / "manifest.json")
            lease = manager_one._acquire_runtime_lease()
            try:
                with self.assertRaises(OcrError) as context:
                    manager_two._acquire_runtime_lease()
                self.assertEqual(context.exception.code, "ocr_busy")
            finally:
                lease.release()
            released = manager_two._acquire_runtime_lease()
            released.release()

    def test_runtime_lock_rejects_manager_in_another_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "ready"
            stop = root / "stop"
            child_code = """
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from ocr_runtime import OcrManager

root = Path(sys.argv[1])
ready = Path(sys.argv[2])
stop = Path(sys.argv[3])
manager = OcrManager(root=root, manifest_path=root / "manifest.json")
lease = manager._acquire_runtime_lease()
ready.write_text("ready", encoding="utf-8")
try:
    while not stop.exists():
        time.sleep(0.02)
finally:
    lease.release()
"""
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(root), str(ready), str(stop)],
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not ready.exists():
                    time.sleep(0.02)
                self.assertTrue(ready.exists(), "child process did not acquire OCR lock")
                manager = OcrManager(root=root, manifest_path=root / "manifest.json")
                with self.assertRaises(OcrError) as context:
                    manager._acquire_runtime_lease()
                self.assertEqual(context.exception.code, "ocr_busy")
            finally:
                stop.write_text("stop", encoding="utf-8")
                child.wait(timeout=5)
            self.assertEqual(child.returncode, 0)


if __name__ == "__main__":
    unittest.main()
