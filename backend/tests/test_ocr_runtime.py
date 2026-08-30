from __future__ import annotations

import os
import json
import hashlib
import io
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import call, patch

from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_runtime import OcrEngine, OcrError, OcrManager, PDF_MAX_BYTES


OBJECT_STREAM_PDF = Path(__file__).resolve().parents[1] / "assets" / "tex_templates" / "elegantbook" / "image" / "cert.pdf"
MINIMAL_PDF = OBJECT_STREAM_PDF.read_bytes()
MINIMAL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


def make_pdf(page_count: int, *, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class OcrRuntimeTests(unittest.TestCase):
    def test_retryable_install_error_is_not_component_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = OcrManager(
                root=root,
                manifest_path=Path(__file__).resolve().parents[1] / "assets" / "ocr" / "manifest.json",
            )
            old_id = "11111111-1111-1111-1111-111111111111"
            (manager.install_path).write_text(json.dumps({
                "install_id": old_id,
                "state": "error",
                "installable": True,
                "error": "ocr_download_failed",
                "error_code": "ocr_download_failed",
                "failed_stage": "downloading",
                "diagnostic": "HTTP 503",
                "retryable": True,
                "downloaded_bytes": 10,
                "total_bytes": 20,
            }), encoding="utf-8")
            status = manager.runtime_status()
            self.assertEqual(status["state"], "error")
            self.assertTrue(status["installable"])
            self.assertTrue(status["retryable"])
            self.assertEqual(status["error_code"], "ocr_download_failed")
            self.assertEqual(status["failed_stage"], "downloading")

            manager._install_worker = lambda _install_id: None  # type: ignore[method-assign]
            retry = manager.start_install()
            self.assertNotEqual(retry["install_id"], old_id)
            self.assertEqual(retry["state"], "downloading")
            if manager._install_thread:
                manager._install_thread.join(timeout=2)

    def test_download_failure_then_retry_resumes_partial_part(self) -> None:
        class Response:
            status = 206
            headers = {"Content-Range": "bytes 3-5/6"}

            def __init__(self) -> None:
                self._read = True

            def read(self, _size: int) -> bytes:
                if self._read:
                    self._read = False
                    return b"def"
                return b""

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = OcrManager(root=root, manifest_path=root / "manifest.json")
            destination = root / "part.bin"
            destination.write_bytes(b"abc")
            part = {
                "url": "https://example.invalid/part.bin",
                "size": 6,
                "sha256": hashlib.sha256(b"abcdef").hexdigest(),
            }
            with patch.object(manager._install_cancel, "wait", return_value=False) as wait:
                with patch("ocr_runtime.urlopen", side_effect=[URLError("offline"), URLError("offline"), Response()]) as mocked:
                    manager._download_part(part, destination)
            self.assertEqual(mocked.call_count, 3)
            self.assertEqual(wait.call_args_list, [call(1), call(2)])
            request = mocked.call_args_list[-1].args[0]
            self.assertEqual(request.headers.get("Range"), "bytes=3-")
            self.assertEqual(destination.read_bytes(), b"abcdef")

    def test_download_read_timeout_resumes_from_new_partial_size(self) -> None:
        class FlakyResponse:
            status = 206

            def __init__(self, content_range: str, chunks: list[bytes], fail_after_first: bool = False) -> None:
                self.headers = {"Content-Range": content_range}
                self._chunks = iter(chunks)
                self._fail_after_first = fail_after_first
                self._read_count = 0

            def read(self, _size: int) -> bytes:
                if self._fail_after_first and self._read_count == 1:
                    raise TimeoutError("read timed out")
                self._read_count += 1
                return next(self._chunks, b"")

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = OcrManager(root=root, manifest_path=root / "manifest.json")
            destination = root / "part.bin"
            destination.write_bytes(b"abc")
            part = {
                "url": "https://example.invalid/part.bin",
                "size": 6,
                "sha256": hashlib.sha256(b"abcdef").hexdigest(),
            }
            responses = [
                FlakyResponse("bytes 3-5/6", [b"d"], fail_after_first=True),
                FlakyResponse("bytes 4-5/6", [b"ef"]),
            ]
            with patch.object(manager._install_cancel, "wait", return_value=False):
                with patch("ocr_runtime.urlopen", side_effect=responses) as mocked:
                    manager._download_part(part, destination)
            self.assertEqual(destination.read_bytes(), b"abcdef")
            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(mocked.call_args_list[1].args[0].headers.get("Range"), "bytes=4-")

    def test_tls_handshake_timeout_retries_then_maps_to_proxy_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = OcrManager(root=root, manifest_path=root / "manifest.json")
            destination = root / "part.bin"
            part = {
                "url": "https://example.invalid/part.bin",
                "size": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            }
            timeout = URLError(TimeoutError("_ssl.c:993: The handshake operation timed out"))
            with patch.object(manager._install_cancel, "wait", return_value=False) as wait:
                with patch("ocr_runtime.urlopen", side_effect=[timeout, timeout, timeout, timeout]) as mocked:
                    with self.assertRaises(OcrError) as context:
                        manager._download_part(part, destination)
            self.assertEqual(context.exception.code, "ocr_download_proxy_timeout")
            self.assertEqual(mocked.call_count, 4)
            self.assertEqual(wait.call_args_list, [call(1), call(2), call(4)])

    def test_placeholder_manifest_is_not_installable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "missing-manifest.json")
            status = manager.runtime_status()
            self.assertEqual(status["state"], "error")
            self.assertFalse(status["installable"])
            self.assertEqual(status["error_code"], "ocr_component_unavailable")
            with self.assertRaises(OcrError) as context:
                manager.start_install()
            self.assertEqual(context.exception.code, "ocr_component_unavailable")
            self.assertFalse(context.exception.retryable)

    def test_object_stream_pdf_uses_real_page_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            writer = manager.begin_upload("object-stream.pdf")
            writer.write(MINIMAL_PDF)
            upload = writer.finish()
            self.assertEqual(upload["page_count"], 1)

    def test_non_terminal_job_recovers_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs" / "job-1"
            jobs.mkdir(parents=True)
            (jobs / "job.json").write_text(json.dumps({"ocr_job_id": "job-1", "status": "processing"}), encoding="utf-8")
            manager = OcrManager(root=root, manifest_path=root / "manifest.json")
            self.assertEqual(manager.get_job("job-1")["status"], "interrupted")

    def test_streamed_pdf_upload_is_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            writer = manager.begin_upload("example.pdf", declared_size=len(MINIMAL_PDF) + 1024)
            writer.write(MINIMAL_PDF[:10])
            writer.write(MINIMAL_PDF[10:])
            upload = writer.finish()
            self.assertEqual(upload["size_bytes"], len(MINIMAL_PDF))
            self.assertEqual(upload["page_count"], 1)
            self.assertTrue(Path(upload["source_path"]).is_file())

    def test_streamed_png_upload_is_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            writer = manager.begin_upload("example.png")
            writer.write(MINIMAL_PNG)
            upload = writer.finish()

            self.assertEqual(upload["size_bytes"], len(MINIMAL_PNG))
            self.assertEqual(upload["page_count"], 1)
            self.assertTrue(Path(upload["source_path"]).is_file())

    def test_png_multipart_request_uses_standard_boundaries_and_mime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.png"
            path.write_bytes(MINIMAL_PNG)

            body, content_type = OcrEngine._multipart(path, {"backend": "pipeline"}, threading.Event())
            payload = b"".join(body)

            self.assertIn("multipart/form-data; boundary=", content_type)
            self.assertIn(b'Content-Disposition: form-data; name="backend"\r\n\r\npipeline\r\n', payload)
            self.assertIn(
                b'Content-Disposition: form-data; name="files"; filename="example.png"\r\n'
                b"Content-Type: image/png\r\n\r\n",
                payload,
            )
            self.assertTrue(payload.endswith(b"\r\n"))

    def test_upload_rejects_declared_size_and_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            with self.assertRaises(OcrError) as context:
                manager.begin_upload("large.pdf", declared_size=PDF_MAX_BYTES + 1)
            self.assertEqual(context.exception.code, "file_too_large")

            writer = manager.begin_upload("fake.pdf")
            writer.write(b"not a pdf")
            with self.assertRaises(OcrError) as context:
                writer.finish()
            self.assertEqual(context.exception.code, "invalid_file")

            writer = manager.begin_upload("corrupt.pdf")
            writer.write(b"%PDF-1.7\nnot a parseable PDF")
            with self.assertRaises(OcrError) as context:
                writer.finish()
            self.assertEqual(context.exception.code, "invalid_pdf")
            self.assertFalse(writer.directory.exists())

            writer = manager.begin_upload("zero-pages.pdf")
            writer.write(make_pdf(0))
            with self.assertRaises(OcrError) as context:
                writer.finish()
            self.assertEqual(context.exception.code, "invalid_pdf")

            writer = manager.begin_upload("encrypted.pdf")
            writer.write(make_pdf(1, encrypted=True))
            with self.assertRaises(OcrError) as context:
                writer.finish()
            self.assertEqual(context.exception.code, "encrypted_pdf")

            writer = manager.begin_upload("too-many-pages.pdf")
            writer.write(make_pdf(1001))
            with self.assertRaises(OcrError) as context:
                writer.finish()
            self.assertEqual(context.exception.code, "page_limit")
            self.assertFalse(writer.directory.exists())

            writer = manager.begin_upload("boundary-pages.pdf")
            writer.write(make_pdf(1000))
            upload = writer.finish()
            self.assertEqual(upload["page_count"], 1000)

    def test_missing_pypdf_is_a_dependency_error(self) -> None:
        import builtins
        from unittest.mock import patch

        original_import = builtins.__import__

        def missing_pypdf(name, *args, **kwargs):
            if name == "pypdf":
                raise ImportError("pypdf intentionally hidden")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object-stream.pdf"
            path.write_bytes(MINIMAL_PDF)
            with patch("builtins.__import__", side_effect=missing_pypdf):
                from ocr_runtime import _inspect_document

                with self.assertRaises(OcrError) as context:
                    _inspect_document(path)
            self.assertEqual(context.exception.code, "pdf_parser_unavailable")

    def test_mocked_job_writes_result_without_external_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            previous = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR")
            os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = str(runtime)
            try:
                manager = OcrManager(root=Path(directory) / "state", manifest_path=Path(directory) / "manifest.json")
                writer = manager.begin_upload("example.pdf")
                writer.write(MINIMAL_PDF)
                upload = writer.finish()
                manager.engine.start = lambda: None  # type: ignore[method-assign]
                manager.engine.parse = lambda path, event: "# OCR result\n"  # type: ignore[method-assign]
                job = manager.create_job(upload["upload_id"])
                deadline = time.time() + 3
                while time.time() < deadline and manager.get_job(job["ocr_job_id"])["status"] not in {"done", "failed"}:
                    time.sleep(0.02)
                result = manager.get_result(job["ocr_job_id"])
                self.assertEqual(
                    result,
                    {
                        "importedText": "# OCR result\n",
                        "filename": "example.pdf",
                        "source": "ocr_file",
                        "ocr_job_id": job["ocr_job_id"],
                    },
                )
            finally:
                if previous is None:
                    os.environ.pop("MATHWEAVER_OCR_RUNTIME_DIR", None)
                else:
                    os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = previous

    def test_second_active_job_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            previous = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR")
            os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = str(runtime)
            try:
                manager = OcrManager(root=Path(directory) / "state", manifest_path=Path(directory) / "manifest.json")
                writer = manager.begin_upload("example.pdf")
                writer.write(MINIMAL_PDF)
                first = writer.finish()
                writer = manager.begin_upload("second.pdf")
                writer.write(MINIMAL_PDF)
                second = writer.finish()
                manager.engine.start = lambda: None  # type: ignore[method-assign]
                manager.engine.parse = lambda path, event: "# OCR result\n"  # type: ignore[method-assign]
                manager._jobs["busy"] = {"status": "processing", "upload_id": first["upload_id"]}
                with self.assertRaises(OcrError) as context:
                    manager.create_job(second["upload_id"])
                self.assertEqual(context.exception.code, "ocr_busy")
            finally:
                if previous is None:
                    os.environ.pop("MATHWEAVER_OCR_RUNTIME_DIR", None)
                else:
                    os.environ["MATHWEAVER_OCR_RUNTIME_DIR"] = previous

    def test_runtime_environment_keeps_local_model_config_and_offline_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            runtime = install / "runtime"
            runtime.mkdir(parents=True)
            (install / "mineru.json").write_text("{}", encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({"config_subpath": "mineru.json"}), encoding="utf-8")
            manager = OcrManager(root=root / "state", manifest_path=root / "manifest.json")
            env = manager._runtime_environment(runtime, install_root=install)
            self.assertEqual(env["MINERU_TOOLS_CONFIG_JSON"], str((install / "mineru.json").resolve()))
            self.assertEqual(env["MINERU_MODEL_SOURCE"], "local")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_recovery_listing_uses_retained_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            writer = manager.begin_upload("recovery.pdf")
            writer.write(MINIMAL_PDF)
            upload = writer.finish()
            job_id = "11111111-1111-1111-1111-111111111111"
            job_dir = manager.jobs_dir / job_id
            job_dir.mkdir(parents=True)
            job = {"ocr_job_id": job_id, "upload_id": upload["upload_id"], "filename": upload["filename"], "status": "interrupted", "page_count": 1}
            (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
            manager._jobs[job_id] = job
            recovered = manager.list_recovery_jobs()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["upload_id"], upload["upload_id"])

    def test_recovery_listing_deduplicates_interrupted_jobs_for_same_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
            writer = manager.begin_upload("recovery.pdf")
            writer.write(MINIMAL_PDF)
            upload = writer.finish()
            jobs = [
                ("11111111-1111-1111-1111-111111111111", "2026-08-28T09:00:00Z"),
                ("22222222-2222-2222-2222-222222222222", "2026-08-28T10:00:00Z"),
            ]
            for job_id, updated_at in jobs:
                job_dir = manager.jobs_dir / job_id
                job_dir.mkdir(parents=True)
                job = {
                    "ocr_job_id": job_id,
                    "upload_id": upload["upload_id"],
                    "filename": upload["filename"],
                    "status": "interrupted",
                    "page_count": 1,
                    "created_at": updated_at,
                    "updated_at": updated_at,
                }
                (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
                manager._jobs[job_id] = job

            recovered = manager.list_recovery_jobs()

            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["ocr_job_id"], jobs[1][0])
            self.assertEqual(recovered[0]["upload_id"], upload["upload_id"])

    def test_cancelled_install_generation_cannot_write_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = OcrManager(root=root, manifest_path=root / "manifest.json")
            install_id = "install-1"
            state = {"install_id": install_id, "state": "downloading"}
            (manager.install_path).write_text(json.dumps(state), encoding="utf-8")
            cancelled = {**state, "state": "error", "error": "install_cancelled"}
            manager._write_install_state(install_id, cancelled, force=True)
            self.assertFalse(manager._write_install_state(install_id, {**state, "state": "ready"}))
            self.assertEqual(json.loads(manager.install_path.read_text(encoding="utf-8"))["state"], "error")


if __name__ == "__main__":
    unittest.main()
