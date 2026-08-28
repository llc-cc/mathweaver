"""Local OCR runtime distribution, upload preflight, and cancellable jobs.

The packaged application never searches the user's PATH for MinerU.  A release
manifest points at a versioned, pre-built CPU runtime and model archive.  The
manager keeps that component outside the PyInstaller archive and owns the
MinerU child process so cancellation and application shutdown are bounded.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests

from ocr_manifest import ManifestValidationError, validate_manifest


CHUNK_SIZE = 1 << 20
INSTALL_PROGRESS_INTERVAL = 0.25
ENGINE_START_TIMEOUT = 180
SELF_TEST_HEALTH_TIMEOUT = 180
SELF_TEST_CALIBRATION_TIMEOUT = 600
DOWNLOAD_READ_TIMEOUT = 60
DOWNLOAD_RETRY_COUNT = 3
DOWNLOAD_RETRY_BACKOFF_SECONDS = 1
PDF_MAX_BYTES = 100 * 1024 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_PAGES = 1000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PDF_SUFFIXES = {".pdf"}
OCR_SUFFIXES = PDF_SUFFIXES | IMAGE_SUFFIXES
TERMINAL_JOB_STATES = {"done", "cancelled", "failed", "interrupted"}
INSTALL_STATES = {"downloading", "verifying", "installing", "self_testing", "ready", "error"}


class OcrMarkdownResult(TypedDict):
    """The Markdown payload shared by the desktop API and ``main.py``."""

    importedText: str
    filename: str
    source: Literal["ocr_file"]
    ocr_job_id: str


class OcrError(RuntimeError):
    """A safe, user-facing OCR error."""

    def __init__(self, code: str, message: str, status_code: int = 400, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class OcrCancelled(OcrError):
    def __init__(self) -> None:
        super().__init__("ocr_cancelled", "OCR 已取消", 409, retryable=True)


class _RuntimeLease:
    """An OS-level, non-blocking lease for the shared MinerU runtime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.touch(exist_ok=True)
            self.handle = self.path.open("r+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, ImportError):
            self.release()
            return False

    def release(self) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        finally:
            try:
                handle.close()
            except OSError:
                pass


class _DownloadRetryableError(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


def _now() -> float:
    return time.time()


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_name(filename: str) -> str:
    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip(" .")
    suffix = Path(safe).suffix.lower()
    if not safe or suffix not in OCR_SUFFIXES:
        raise OcrError("invalid_file", "仅支持 PDF、PNG、JPEG 和 WebP 文件")
    return safe


def _file_signature_ok(path: Path, suffix: str) -> bool:
    with path.open("rb") as handle:
        head = handle.read(16)
    if suffix == ".pdf":
        return head.startswith(b"%PDF-")
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def _inspect_document(path: Path) -> tuple[int, bool]:
    suffix = path.suffix.lower()
    if not _file_signature_ok(path, suffix):
        raise OcrError("invalid_file", "文件内容与扩展名不匹配")
    if suffix != ".pdf":
        return 1, False
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise OcrError(
            "pdf_parser_unavailable",
            "PDF 解析组件未安装或未打包，请修复 pypdf 依赖后重试",
            503,
            retryable=False,
        ) from exc
    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            return 0, True
        page_count = len(reader.pages)
        if page_count <= 0:
            raise OcrError("invalid_pdf", "PDF 中没有可识别页面", 422)
        return page_count, False
    except OcrError:
        raise
    except Exception as exc:
        raise OcrError("invalid_pdf", "PDF 无法解析", 422) from exc


def _json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _json_read(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else (default or {})
    except (OSError, json.JSONDecodeError):
        return default or {}


def _manifest_digest(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_diagnostic(value: Any) -> str:
    """Return a bounded diagnostic without local paths or URL query values."""

    text = str(value or "").strip()
    text = re.sub(r"https?://[^\s]+", "[url]", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "[path]", text)
    return text[:500] or "暂无更多诊断信息"


def build_ocr_markdown_result(markdown: str, filename: str, ocr_job_id: str) -> OcrMarkdownResult:
    """Build the immutable Markdown result shape exposed by every OCR entrypoint."""

    if not markdown.strip():
        raise OcrError("ocr_output_missing", "OCR 未生成可处理的 Markdown", 502, retryable=True)
    return {
        "importedText": markdown,
        "filename": _safe_name(Path(filename).name),
        "source": "ocr_file",
        "ocr_job_id": str(ocr_job_id),
    }


def _is_tls_handshake_timeout(value: BaseException) -> bool:
    text = str(value).lower()
    return ("handshake" in text and "timed out" in text) or "_ssl.c:993" in text


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_closed(port: int | None) -> bool:
    if not port:
        return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _wait_port_closed(port: int | None, timeout: float = 5.0) -> bool:
    deadline = _now() + timeout
    while _now() < deadline:
        if _port_is_closed(port):
            return True
        time.sleep(0.1)
    return _port_is_closed(port)


def _terminate_process_tree(process: subprocess.Popen[Any] | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        if os.name == "nt" and process.pid:
            subprocess.run(
                ["taskkill.exe", "/pid", str(process.pid), "/t", "/f"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process.terminate()
            process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1)
        except OSError:
            pass
        except subprocess.TimeoutExpired:
            pass


class UploadWriter:
    def __init__(self, manager: "OcrManager", filename: str, declared_size: int | None = None):
        self.manager = manager
        self.filename = _safe_name(filename)
        self.suffix = Path(self.filename).suffix.lower()
        self.declared_size = declared_size
        self.upload_id = str(uuid.uuid4())
        self.directory = manager.uploads_dir / self.upload_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.partial_path = self.directory / f"source{self.suffix}.part"
        self.source_path = self.directory / f"source{self.suffix}"
        self.handle = self.partial_path.open("wb")
        self.digest = hashlib.sha256()
        self.total = 0
        self.closed = False
        self.max_bytes = PDF_MAX_BYTES if self.suffix == ".pdf" else IMAGE_MAX_BYTES
        if declared_size is not None and declared_size > self.max_bytes:
            self.abort()
            raise OcrError("file_too_large", f"文件不能超过 {self.max_bytes // (1024 * 1024)}MB")

    def write(self, chunk: bytes) -> None:
        if self.closed:
            raise OcrError("upload_closed", "上传已结束")
        if not chunk:
            return
        self.total += len(chunk)
        if self.total > self.max_bytes:
            self.abort()
            raise OcrError("file_too_large", f"文件不能超过 {self.max_bytes // (1024 * 1024)}MB")
        self.handle.write(chunk)
        self.digest.update(chunk)

    def finish(self) -> dict[str, Any]:
        if self.closed:
            raise OcrError("upload_closed", "上传已结束")
        self.closed = True
        self.handle.close()
        if self.total <= 0:
            self.abort()
            raise OcrError("empty_file", "文件为空")
        os.replace(self.partial_path, self.source_path)
        if not _file_signature_ok(self.source_path, self.suffix):
            self.abort()
            raise OcrError("invalid_file", "文件内容与扩展名不匹配")
        try:
            page_count, encrypted = _inspect_document(self.source_path)
        except OcrError:
            self.abort()
            raise
        if encrypted:
            self.abort()
            raise OcrError("encrypted_pdf", "暂不支持加密 PDF，请先解除密码保护")
        if self.suffix == ".pdf" and page_count > PDF_MAX_PAGES:
            self.abort()
            raise OcrError("page_limit", f"PDF 页数必须在 1 至 {PDF_MAX_PAGES} 页之间")
        metadata = {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "suffix": self.suffix,
            "size_bytes": self.total,
            "sha256": self.digest.hexdigest(),
            "page_count": page_count,
            "eta_seconds": self.manager.estimate(page_count),
            "source_path": str(self.source_path),
            "created_at": _iso_now(),
            "expires_at": _now() + self.manager.upload_retention_seconds,
        }
        _json_write(self.directory / "upload.json", metadata)
        return metadata

    def abort(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.handle.close()
            except OSError:
                pass
        shutil.rmtree(self.directory, ignore_errors=True)


class OcrEngine:
    def __init__(self, manager: "OcrManager") -> None:
        self.manager = manager
        self.process: subprocess.Popen[Any] | None = None
        self.log_handle: Any | None = None
        self.base_url: str | None = None
        self.port: int | None = None
        self.lock = threading.RLock()

    def _runtime_dir(self) -> Path:
        override = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR", "").strip()
        if override and not getattr(sys, "frozen", False):
            return Path(override).expanduser().resolve()
        current = _json_read(self.manager.current_path)
        runtime = current.get("runtime_dir")
        if runtime:
            return Path(str(runtime)).resolve()
        return self.manager.runtime_dir

    def _command(self, port: int) -> list[str]:
        runtime_dir = self._runtime_dir()
        manifest = self.manager.manifest()
        configured = manifest.get("entrypoint")
        if os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR", "").strip() and not getattr(sys, "frozen", False) and configured:
            configured_path = Path(str(configured).replace("{runtime_dir}", str(runtime_dir)))
            if not configured_path.is_file():
                configured = None
        if configured:
            entrypoint = Path(str(configured).replace("{runtime_dir}", str(runtime_dir)))
            command = [str(entrypoint)]
        elif (runtime_dir / "Scripts" / "mineru-api.exe").exists():
            command = [str(runtime_dir / "Scripts" / "mineru-api.exe")]
        elif (runtime_dir / "mineru-api.exe").exists():
            command = [str(runtime_dir / "mineru-api.exe")]
        elif (runtime_dir / "python.exe").exists():
            command = [str(runtime_dir / "python.exe"), "-m", "mineru.cli.fast_api"]
        else:
            raise OcrError("ocr_runtime_missing", "OCR 运行时缺少 mineru-api 可执行文件", 503, retryable=True)
        args = manifest.get("entrypoint_args") or manifest.get("args") or []
        return command + [str(arg).replace("{port}", str(port)) for arg in args] + ["--host", "127.0.0.1", "--port", str(port)]

    def start(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None and self.base_url:
                return
            self.stop()
            port = _free_port()
            command = self._command(port)
            runtime_dir = self._runtime_dir()
            env = self.manager._runtime_environment(
                runtime_dir,
                output_root=self.manager.root / "engine-output",
            )
            log_path = self.manager.root / "mineru-api.log"
            log_handle = log_path.open("a", encoding="utf-8", errors="replace")
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=str(runtime_dir),
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=log_handle,
                    stderr=log_handle,
                    creationflags=creationflags if os.name == "nt" else 0,
                )
            except OSError as exc:
                log_handle.close()
                raise OcrError("ocr_runtime_start_failed", f"OCR 引擎启动失败：{exc}", 503, retryable=True) from exc
            self.log_handle = log_handle
            self.port = port
            self.base_url = f"http://127.0.0.1:{port}"
            deadline = _now() + ENGINE_START_TIMEOUT
            while _now() < deadline:
                if self.process.poll() is not None:
                    self.stop()
                    raise OcrError("ocr_runtime_start_failed", "OCR 引擎启动后立即退出，请检查 OCR 组件日志", 503, retryable=True)
                try:
                    response = requests.get(f"{self.base_url}/health", timeout=2)
                    if response.ok:
                        return
                except requests.RequestException:
                    pass
                time.sleep(0.5)
            self.stop()
            raise OcrError("ocr_runtime_timeout", "OCR 引擎启动超时", 503, retryable=True)

    def stop(self, *, force: bool = False) -> None:
        with self.lock:
            process = self.process
            log_handle = self.log_handle
            port = self.port
            self.process = None
            self.log_handle = None
            self.base_url = None
            self.port = None
            if process:
                if os.name == "nt" and force:
                    _terminate_process_tree(process)
                else:
                    try:
                        if process.stdin:
                            process.stdin.close()
                    except OSError:
                        pass
                    deadline = _now() + 1
                    while process.poll() is None and _now() < deadline:
                        time.sleep(0.1)
                    if process.poll() is None:
                        _terminate_process_tree(process)
            if log_handle is not None:
                try:
                    log_handle.close()
                except OSError:
                    pass
            _wait_port_closed(port)

    @staticmethod
    def _multipart(path: Path, fields: dict[str, str], cancel_event: threading.Event) -> tuple[Iterable[bytes], str]:
        boundary = f"----MathWeaverOCR{uuid.uuid4().hex}"
        marker = boundary.encode("ascii")

        def chunks() -> Iterable[bytes]:
            for key, value in fields.items():
                yield b"--" + marker + b"\r\n"
                yield f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                yield value.encode()
                yield b"\r\n"
            yield b"--" + marker + b"\r\n"
            yield f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode()
            mime = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(path.suffix.lower(), "application/octet-stream")
            yield f"Content-Type: {mime}\r\n\r\n".encode("ascii")
            with path.open("rb") as handle:
                while True:
                    if cancel_event.is_set():
                        raise OcrCancelled()
                    chunk = handle.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            yield b"\r\n--" + marker + b"--\r\n"

        return chunks(), f"multipart/form-data; boundary={boundary}"

    def parse(self, path: Path, cancel_event: threading.Event) -> str:
        self.start()
        assert self.base_url is not None
        fields = {
            "backend": "pipeline",
            "parse_method": "auto",
            "return_md": "true",
            "return_content_list": "false",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_images": "false",
            "response_format_zip": "false",
        }
        body, content_type = self._multipart(path, fields, cancel_event)
        active_job = next((job for job in self.manager._jobs.values() if job.get("status") not in TERMINAL_JOB_STATES), None)
        page_count = int(active_job.get("page_count") or 1) if active_job else 1
        deadline = _now() + max(300, min(6 * 60 * 60, page_count * 120))
        try:
            response = requests.post(
                f"{self.base_url}/tasks",
                data=body,
                headers={"Content-Type": content_type},
                timeout=(10, 600),
            )
            response.raise_for_status()
            task = response.json()
            task_id = str(task.get("task_id") or "")
            if not task_id:
                raise OcrError("ocr_protocol_error", "OCR 引擎未返回任务 ID", 502, retryable=True)
            while True:
                if cancel_event.is_set():
                    raise OcrCancelled()
                if _now() >= deadline:
                    raise OcrError("ocr_timeout", "OCR 处理超时", 504, retryable=True)
                status_response = requests.get(f"{self.base_url}/tasks/{task_id}", timeout=5)
                status_response.raise_for_status()
                status = status_response.json()
                if status.get("status") == "completed":
                    break
                if status.get("status") == "failed":
                    raise OcrError("ocr_failed", str(status.get("error") or "OCR 引擎处理失败"), 502, retryable=True)
                time.sleep(1)
            self.manager._set_active_job_phase("collecting_output")
            result_response = requests.get(f"{self.base_url}/tasks/{task_id}/result", timeout=30)
            result_response.raise_for_status()
            payload = result_response.json()
        except OcrCancelled:
            self.stop()
            raise
        except (requests.RequestException, ValueError) as exc:
            if cancel_event.is_set():
                self.stop()
                raise OcrCancelled() from exc
            raise OcrError("ocr_failed", f"无法读取 OCR 引擎结果：{exc}", 502, retryable=True) from exc
        markdown = self._extract_markdown(payload, path.stem)
        if not markdown.strip():
            raise OcrError("ocr_output_missing", "OCR 未生成可处理的 Markdown", 502, retryable=True)
        return markdown

    @staticmethod
    def _extract_markdown(payload: Any, stem: str) -> str:
        if isinstance(payload, dict):
            if isinstance(payload.get("md_content"), str):
                return payload["md_content"]
            results = payload.get("results")
            if isinstance(results, dict):
                for key in (stem, Path(stem).name):
                    item = results.get(key)
                    if isinstance(item, dict) and isinstance(item.get("md_content"), str):
                        return item["md_content"]
            for value in payload.values():
                found = OcrEngine._extract_markdown(value, stem)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = OcrEngine._extract_markdown(value, stem)
                if found:
                    return found
        return ""


class OcrManager:
    def __init__(self, root: Path | None = None, manifest_path: Path | None = None) -> None:
        default_root = os.environ.get("MATHWEAVER_OCR_DIR", "").strip()
        if not default_root:
            data_root = os.environ.get("MATHGRAPH_DATA_DIR", "").strip()
            if data_root:
                default_root = str(Path(data_root) / "ocr")
            else:
                default_root = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MathWeaver" / "ocr")
        self.root = Path(root or default_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads_dir = self.root / "uploads"
        self.jobs_dir = self.root / "jobs"
        self.runtime_dir = self.root / "runtime"
        self.downloads_dir = self.root / "downloads"
        self.quarantine_dir = self.root / "quarantine"
        for directory in (self.uploads_dir, self.jobs_dir, self.runtime_dir, self.downloads_dir, self.quarantine_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.current_path = self.root / "current.json"
        self.install_path = self.root / "install.json"
        self.metrics_path = self.root / "metrics.json"
        self.runtime_lock_path = self.root / "ocr-runtime.lock"
        self._lock = threading.RLock()
        self._install_thread: threading.Thread | None = None
        self._install_cancel = threading.Event()
        self._job_threads: dict[str, threading.Thread] = {}
        self._job_cancel: dict[str, threading.Event] = {}
        self._job_leases: dict[str, _RuntimeLease] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._last_cleanup_at = 0.0
        self.upload_retention_seconds = 24 * 60 * 60
        self.engine = OcrEngine(self)
        self._manifest_path = manifest_path or self._resolve_manifest_path()
        self._load_jobs()
        self._cleanup_expired_uploads()

    def _resolve_manifest_path(self) -> Path:
        configured = os.environ.get("MATHWEAVER_OCR_MANIFEST", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return Path(__file__).resolve().parent / "assets" / "ocr" / "manifest.json"

    def manifest(self) -> dict[str, Any]:
        return _json_read(self._manifest_path)

    def _validated_manifest(self) -> dict[str, Any]:
        try:
            return validate_manifest(self.manifest(), production=True)
        except (ManifestValidationError, TypeError, ValueError) as exc:
            raise OcrError(
                "ocr_component_unavailable",
                "此版本未发布可安装的 OCR 组件，请安装 MathWeaver 0.1.1",
                503,
                retryable=False,
            ) from exc

    def _runtime_environment(
        self,
        runtime_dir: Path,
        *,
        install_root: Path | None = None,
        output_root: Path | None = None,
    ) -> dict[str, str]:
        """Build the only environment allowed for a local MinerU process."""

        manifest = self.manifest()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("MINERU_") or key in {"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"}:
                env.pop(key, None)
        env.update({
            "MINERU_MODEL_SOURCE": "local",
            "MINERU_API_ENABLE_FASTAPI_DOCS": "0",
            "MINERU_API_OUTPUT_ROOT": str(output_root or (self.root / "engine-output")),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        })
        config_subpath = str(manifest.get("config_subpath") or "")
        if config_subpath:
            config_root = install_root or runtime_dir.parent
            config_path = config_root / config_subpath
            if not config_path.is_file():
                raise OcrError("ocr_runtime_missing", "OCR 本地模型配置不存在", 503, retryable=True)
            env["MINERU_TOOLS_CONFIG_JSON"] = str(config_path.resolve())
        if os.name != "nt":
            env["MINERU_API_SHUTDOWN_ON_STDIN_EOF"] = "1"
        return env

    def _installed_metadata(self, runtime_path: Path | None) -> dict[str, Any]:
        if not runtime_path:
            return {}
        return _json_read(runtime_path.parent / "installed.json")

    def _installed_runtime_valid(self, current: dict[str, Any], manifest: dict[str, Any]) -> bool:
        runtime_path = Path(str(current.get("runtime_dir") or "")) if current.get("runtime_dir") else None
        if not runtime_path or not runtime_path.is_dir() or not (runtime_path / "python.exe").is_file():
            return False
        metadata = self._installed_metadata(runtime_path)
        if metadata.get("component_version") != manifest.get("version"):
            return False
        if metadata.get("manifest_sha256") != _manifest_digest(manifest):
            return False
        if metadata.get("model_revision") != manifest.get("model_revision"):
            return False
        config_subpath = str(manifest.get("config_subpath") or "")
        if config_subpath and not (runtime_path.parent / config_subpath).is_file():
            return False
        models_dir = runtime_path.parent / str(manifest.get("models_subdir") or "models/pipeline")
        model_manifest = models_dir / str(manifest.get("models_manifest_path") or "")
        if not model_manifest.is_file() or _sha256(model_manifest) != str(manifest.get("models_manifest_sha256") or ""):
            return False
        if metadata.get("self_test_status") != "passed":
            return False
        entrypoint = str(manifest.get("entrypoint") or "").replace("{runtime_dir}", str(runtime_path))
        return bool(entrypoint) and Path(entrypoint).is_file()

    @staticmethod
    def _verify_model_files(models_dir: Path, manifest_path: Path) -> None:
        try:
            model_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OcrError("ocr_model_manifest_invalid", "OCR 模型清单无法读取", 502, retryable=True) from exc
        files = model_manifest.get("files") if isinstance(model_manifest, dict) else None
        if not isinstance(files, list) or not files:
            raise OcrError("ocr_model_manifest_invalid", "OCR 模型清单为空", 502, retryable=True)
        for item in files:
            if not isinstance(item, dict):
                raise OcrError("ocr_model_manifest_invalid", "OCR 模型清单格式无效", 502, retryable=True)
            relative = Path(str(item.get("path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise OcrError("ocr_model_manifest_invalid", "OCR 模型路径无效", 502, retryable=True)
            path = models_dir / relative
            if not path.is_file() or int(item.get("size") or -1) != path.stat().st_size or str(item.get("sha256") or "").lower() != _sha256(path):
                raise OcrError("ocr_model_manifest_invalid", f"OCR 模型文件校验失败：{relative}", 502, retryable=True)

    def _write_install_state(self, install_id: str, state: dict[str, Any], *, force: bool = False) -> bool:
        current = _json_read(self.install_path)
        if not force and current.get("install_id") != install_id:
            return False
        if not force and current.get("state") == "error":
            return False
        _json_write(self.install_path, state)
        return True

    def _log_install_exception(self, install_id: str, exc: BaseException) -> None:
        log_path = self.root / "ocr-install.log"
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{_iso_now()}] install_id={install_id}\n")
                handle.write(traceback.format_exc())
                handle.write("\n")
        except OSError:
            pass

    def _maybe_cleanup_expired_uploads(self) -> None:
        if _now() - self._last_cleanup_at >= 60:
            self._cleanup_expired_uploads()
            self._last_cleanup_at = _now()

    def begin_upload(self, filename: str, declared_size: int | None = None) -> UploadWriter:
        self._maybe_cleanup_expired_uploads()
        return UploadWriter(self, filename, declared_size)

    def _validate_local_source(self, path: Path) -> tuple[str, int]:
        if not path.is_file():
            raise OcrError("upload_not_found", "输入文件不存在", 404)
        filename = _safe_name(path.name)
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise OcrError("invalid_file", "无法读取输入文件", 422) from exc
        max_bytes = PDF_MAX_BYTES if path.suffix.lower() == ".pdf" else IMAGE_MAX_BYTES
        if size <= 0:
            raise OcrError("empty_file", "文件为空")
        if size > max_bytes:
            raise OcrError("file_too_large", f"文件不能超过 {max_bytes // (1024 * 1024)}MB")
        page_count, encrypted = _inspect_document(path)
        if encrypted:
            raise OcrError("encrypted_pdf", "暂不支持加密 PDF，请先解除密码保护")
        if path.suffix.lower() == ".pdf" and page_count > PDF_MAX_PAGES:
            raise OcrError("page_limit", f"PDF 页数必须在 1 至 {PDF_MAX_PAGES} 页之间")
        return filename, page_count

    def _parse_markdown_locked(
        self,
        path: Path,
        *,
        ocr_job_id: str,
        cancel_event: threading.Event,
        filename: str | None = None,
    ) -> OcrMarkdownResult:
        markdown = self.engine.parse(path, cancel_event)
        if cancel_event.is_set():
            raise OcrCancelled()
        return build_ocr_markdown_result(markdown, filename or path.name, ocr_job_id)

    def parse_local_file(
        self,
        path: Path | str,
        *,
        ocr_job_id: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> OcrMarkdownResult:
        """Parse a local file through the same runtime and result contract as the desktop API."""

        source_path = Path(path).expanduser().resolve()
        _filename, page_count = self._validate_local_source(source_path)
        if self.runtime_status().get("state") != "ready":
            raise OcrError("ocr_runtime_missing", "请先安装本地 OCR 组件", 503, retryable=True)
        lease = self._acquire_runtime_lease()
        event = cancel_event or threading.Event()
        job_id = str(ocr_job_id or uuid.uuid4())
        started = _now()
        try:
            result = self._parse_markdown_locked(
                source_path,
                ocr_job_id=job_id,
                cancel_event=event,
                filename=_filename,
            )
            self._record_metric(page_count, _now() - started)
            return result
        finally:
            self.engine.stop(force=True)
            lease.release()

    def delete_upload(self, upload_id: str) -> None:
        upload = self._upload(upload_id)
        if any(job.get("upload_id") == upload_id and job.get("status") not in TERMINAL_JOB_STATES for job in self._jobs.values()):
            raise OcrError("ocr_busy", "该文件正在 OCR，不能删除", 409, retryable=True)
        shutil.rmtree(Path(upload["source_path"]).parent, ignore_errors=True)

    def _upload(self, upload_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}", upload_id):
            raise OcrError("upload_not_found", "上传文件不存在", 404)
        path = self.uploads_dir / upload_id / "upload.json"
        value = _json_read(path)
        if not value or not Path(str(value.get("source_path") or "")).is_file():
            raise OcrError("upload_not_found", "上传文件不存在", 404)
        return value

    def _load_jobs(self) -> None:
        recovery_lease = _RuntimeLease(self.runtime_lock_path)
        can_recover = recovery_lease.acquire()
        try:
            for path in self.jobs_dir.glob("*/job.json"):
                job = _json_read(path)
                job_id = str(job.get("ocr_job_id") or "")
                if not job_id:
                    continue
                if can_recover and job.get("status") not in TERMINAL_JOB_STATES:
                    job.update({"status": "interrupted", "phase": "interrupted", "error": "应用在 OCR 完成前退出", "retryable": True})
                    _json_write(path, job)
                self._jobs[job_id] = job
        finally:
            recovery_lease.release()

    def _refresh_jobs_from_disk(self) -> None:
        for path in self.jobs_dir.glob("*/job.json"):
            job = _json_read(path)
            job_id = str(job.get("ocr_job_id") or "")
            if job_id:
                self._jobs[job_id] = job

    def _acquire_runtime_lease(self) -> _RuntimeLease:
        lease = _RuntimeLease(self.runtime_lock_path)
        if not lease.acquire():
            raise OcrError("ocr_busy", "已有 OCR 任务正在运行", 409, retryable=True)
        return lease

    def _cleanup_expired_uploads(self) -> None:
        now = _now()
        for metadata_path in self.uploads_dir.glob("*/upload.json"):
            metadata = _json_read(metadata_path)
            if float(metadata.get("expires_at") or 0) and float(metadata.get("expires_at") or 0) < now:
                shutil.rmtree(metadata_path.parent, ignore_errors=True)

    def runtime_status(self) -> dict[str, Any]:
        self._maybe_cleanup_expired_uploads()
        available_disk = shutil.disk_usage(self.root).free
        override = os.environ.get("MATHWEAVER_OCR_RUNTIME_DIR", "").strip()
        if override and not getattr(sys, "frozen", False) and Path(override).expanduser().is_dir():
            return {
                "state": "ready",
                "installable": True,
                "version": "development-override",
                "component_version": "development-override",
                "manifest_schema_version": 2,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "installed_bytes": 0,
                "required_disk_bytes": 0,
                "available_disk_bytes": available_disk,
                "message": "使用显式开发运行时",
            }
        current = _json_read(self.current_path)
        manifest = self.manifest()
        install = _json_read(self.install_path)
        version = str(manifest.get("version") or current.get("version") or "")
        try:
            validated = validate_manifest(manifest, production=True)
        except (ManifestValidationError, TypeError, ValueError) as exc:
            return {
                "state": "error",
                "installable": False,
                "version": version,
                "component_version": version or None,
                "manifest_schema_version": manifest.get("schema_version"),
                "manifest_sha256": None,
                "installed_manifest_sha256": None,
                "repairable": False,
                "download_percent": 0,
                "self_test_status": None,
                "available_disk_bytes": available_disk,
                "message": "此版本未发布可安装的 OCR 组件，请安装 MathWeaver 0.1.1",
                "error": "ocr_component_unavailable",
                "error_code": "ocr_component_unavailable",
                "failed_stage": None,
                "retryable": False,
                "diagnostic": _safe_diagnostic(exc),
            }
        version = str(validated.get("version") or version)
        if install.get("state") in INSTALL_STATES - {"ready", "error"}:
            return {
                **install,
                "installable": True,
                "version": version,
                "component_version": version,
                "manifest_schema_version": validated.get("schema_version"),
                "available_disk_bytes": available_disk,
            }
        if install.get("state") == "error":
            return {
                **install,
                "installable": True,
                "version": version,
                "component_version": version,
                "manifest_schema_version": validated.get("schema_version"),
                "available_disk_bytes": available_disk,
            }
        runtime_path = Path(str(current.get("runtime_dir") or "")) if current.get("runtime_dir") else None
        installed_metadata = self._installed_metadata(runtime_path)
        if self._installed_runtime_valid(current, validated):
            return {
                "state": "ready",
                "installable": True,
                "version": version,
                "install_id": install.get("install_id"),
                "component_version": version,
                "manifest_schema_version": validated.get("schema_version"),
                "manifest_sha256": _manifest_digest(validated),
                "installed_manifest_sha256": installed_metadata.get("manifest_sha256"),
                "repairable": False,
                "download_percent": 100,
                "self_test_status": installed_metadata.get("self_test_status", "passed"),
                "downloaded_bytes": 0,
                "total_bytes": int(validated.get("download_bytes") or 0),
                "installed_bytes": int(validated.get("installed_bytes") or 0),
                "required_disk_bytes": int(validated.get("required_disk_bytes") or 0),
                "available_disk_bytes": available_disk,
                "message": "OCR 组件已安装",
            }
        return {
            "state": "missing",
            "installable": True,
            "version": version,
            "component_version": version,
            "manifest_schema_version": validated.get("schema_version"),
            "manifest_sha256": _manifest_digest(validated),
            "installed_manifest_sha256": installed_metadata.get("manifest_sha256"),
            "repairable": bool(runtime_path and runtime_path.exists()),
            "download_percent": 0,
            "self_test_status": installed_metadata.get("self_test_status"),
            "downloaded_bytes": 0,
            "total_bytes": int(validated.get("download_bytes") or 0),
            "installed_bytes": int(validated.get("installed_bytes") or 0),
            "required_disk_bytes": int(validated.get("required_disk_bytes") or 0),
            "available_disk_bytes": available_disk,
            "message": "需要安装本地 OCR 组件",
        }

    def start_install(self) -> dict[str, Any]:
        with self._lock:
            current = _json_read(self.install_path)
            if self._install_thread and self._install_thread.is_alive():
                return current
            manifest = self._validated_manifest()
            if self._installed_runtime_valid(_json_read(self.current_path), manifest):
                return self.runtime_status()
            required_disk = int(manifest["required_disk_bytes"])
            available_disk = shutil.disk_usage(self.root).free
            if available_disk < required_disk:
                raise OcrError("insufficient_disk", "可用磁盘空间不足以安装 OCR 组件", 507, retryable=True)
            install_id = str(uuid.uuid4())
            state = {
                "install_id": install_id,
                "state": "downloading",
                "installable": True,
                "version": str(manifest.get("version") or ""),
                "component_version": str(manifest.get("version") or ""),
                "manifest_schema_version": manifest.get("schema_version"),
                "downloaded_bytes": 0,
                "total_bytes": int(manifest.get("download_bytes") or 0),
                "download_percent": 0,
                "manifest_sha256": _manifest_digest(manifest),
                "repairable": True,
                "self_test_status": None,
                "failed_stage": None,
                "diagnostic": None,
                "message": "正在下载 OCR 组件",
            }
            _json_write(self.install_path, state)
            self._install_cancel = threading.Event()
            self._install_thread = threading.Thread(target=self._install_worker, args=(install_id,), daemon=True)
            self._install_thread.start()
            return state

    def cancel_install(self, install_id: str) -> dict[str, Any]:
        state = _json_read(self.install_path)
        if state.get("install_id") != install_id:
            raise OcrError("install_not_found", "组件安装任务不存在", 404)
        if state.get("state") in {"ready", "error"}:
            return state
        self._install_cancel.set()
        if self._install_thread and self._install_thread.is_alive():
            self._install_thread.join(timeout=5)
        state.update({
            "state": "error",
            "message": "组件安装已取消",
            "error": "install_cancelled",
            "error_code": "install_cancelled",
            "failed_stage": state.get("state"),
            "diagnostic": "用户取消了组件安装",
            "retryable": True,
        })
        self._write_install_state(install_id, state, force=True)
        return state

    def _install_worker(self, install_id: str) -> None:
        manifest = self.manifest()
        version = str(manifest.get("version") or install_id)
        state = _json_read(self.install_path)
        staging = self.root / f"staging-{version}-{install_id}"
        previous_dir: Path | None = None
        final_dir: Path | None = None
        try:
            manifest = self._validated_manifest()
            version = str(manifest.get("version") or install_id)
            staging.mkdir(parents=True, exist_ok=False)
            downloaded = 0
            total = int(manifest.get("download_bytes") or 0)
            archives = manifest.get("archives") or []
            archive_paths: list[tuple[dict[str, Any], Path]] = []
            for archive in archives:
                archive_name = Path(str(archive.get("name") or "archive.zip")).name
                archive_dir = self.downloads_dir / version
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / archive_name
                parts = archive.get("parts") or []
                with archive_path.open("wb") as combined:
                    for part in parts:
                        if self._install_cancel.is_set():
                            raise OcrCancelled()
                        part_name = Path(str(part.get("name") or "part.bin")).name
                        part_path = archive_dir / part_name
                        part_base = downloaded
                        progress_at = 0.0

                        def report_part_progress(part_written: int) -> None:
                            nonlocal progress_at
                            if self._install_cancel.is_set():
                                raise OcrCancelled()
                            now = _now()
                            if now - progress_at < INSTALL_PROGRESS_INTERVAL and part_written < int(part.get("size") or 0):
                                return
                            progress_at = now
                            current_downloaded = part_base + part_written
                            state.update({
                                "state": "downloading",
                                "downloaded_bytes": current_downloaded,
                                "total_bytes": total,
                                "download_percent": min(100, int(current_downloaded * 100 / total)) if total else 0,
                                "message": "正在下载 OCR 组件",
                            })
                            if not self._write_install_state(install_id, state):
                                raise OcrCancelled()

                        self._download_part(part, part_path, report_part_progress)
                        with part_path.open("rb") as source:
                            shutil.copyfileobj(source, combined, CHUNK_SIZE)
                        downloaded += int(part.get("size") or part_path.stat().st_size)
                        state.update({"state": "downloading", "downloaded_bytes": downloaded, "total_bytes": total, "download_percent": min(100, int(downloaded * 100 / total)) if total else 0, "message": "正在下载 OCR 组件"})
                        if not self._write_install_state(install_id, state):
                            raise OcrCancelled()
                expected_archive_hash = str(archive.get("sha256") or "")
                if expected_archive_hash and _sha256(archive_path) != expected_archive_hash:
                    raise OcrError("ocr_hash_mismatch", f"组件 {archive_name} 校验失败", 502, retryable=True)
                archive_paths.append((archive, archive_path))
            state.update({"state": "verifying", "message": "正在校验 OCR 组件"})
            if not self._write_install_state(install_id, state):
                raise OcrCancelled()
            state.update({"state": "installing", "message": "正在安装 OCR 组件"})
            if not self._write_install_state(install_id, state):
                raise OcrCancelled()
            for archive, archive_path in archive_paths:
                if self._install_cancel.is_set():
                    raise OcrCancelled()
                extract_to = staging / str(archive.get("extract_to") or "")
                extract_to.mkdir(parents=True, exist_ok=True)
                self._extract_archive(archive_path, extract_to)
            runtime_dir = staging / str(manifest.get("runtime_subdir") or "runtime")
            models_dir = staging / str(manifest.get("models_subdir") or "models/pipeline")
            if not runtime_dir.is_dir() or not models_dir.is_dir():
                raise OcrError("ocr_runtime_missing", "安装后的 OCR 目录不完整", 502, retryable=True)
            for license_name in manifest.get("license_files") or []:
                license_path = runtime_dir / Path(str(license_name)).name
                if not license_path.is_file():
                    raise OcrError("ocr_license_missing", "OCR 许可证文件缺失", 502, retryable=True)
            model_manifest_path = models_dir / str(manifest["models_manifest_path"])
            if not model_manifest_path.is_file() or _sha256(model_manifest_path) != str(manifest["models_manifest_sha256"]):
                raise OcrError("ocr_model_manifest_invalid", "OCR 模型清单缺失或校验失败", 502, retryable=True)
            self._verify_model_files(models_dir, model_manifest_path)
            calibration_path = runtime_dir / str(manifest.get("calibration_pdf") or "")
            if not calibration_path.is_file() or _sha256(calibration_path) != str(manifest.get("calibration_sha256") or ""):
                raise OcrError("ocr_calibration_missing", "OCR 校准 PDF 缺失或校验失败", 502, retryable=True)
            self._write_model_config(staging, models_dir, manifest)
            entrypoint = manifest.get("entrypoint")
            if entrypoint:
                entry_path = Path(str(entrypoint).replace("{runtime_dir}", str(runtime_dir)))
                if not entry_path.is_file():
                    raise OcrError("ocr_runtime_missing", "安装后的 OCR 入口文件不存在", 502, retryable=True)
            state.update({"state": "self_testing", "message": "正在测试 OCR 组件"})
            if not self._write_install_state(install_id, state):
                raise OcrCancelled()
            self._self_test_runtime(staging, runtime_dir, entry_path if entrypoint else None, manifest)
            if self._install_cancel.is_set():
                raise OcrCancelled()
            final_dir = self.root / version
            if final_dir.exists():
                quarantine = self.quarantine_dir / f"{version}-{int(_now())}-{install_id}"
                os.replace(final_dir, quarantine)
                previous_dir = quarantine
            os.replace(staging, final_dir)
            self._write_model_config(final_dir, final_dir / str(manifest.get("models_subdir") or "models/pipeline"), manifest)
            installed_runtime = final_dir / str(manifest.get("runtime_subdir") or "runtime")
            installed_metadata = {
                "component_version": version,
                "manifest_sha256": _manifest_digest(manifest),
                "model_revision": manifest.get("model_revision"),
                "runtime_dir": str(installed_runtime),
                "models_dir": str(final_dir / str(manifest.get("models_subdir") or "models/pipeline")),
                "config_path": str(final_dir / str(manifest.get("config_subpath") or "mineru.json")),
                "self_test_status": "passed",
                "self_test_at": _iso_now(),
            }
            _json_write(final_dir / "installed.json", installed_metadata)
            _json_write(self.current_path, {
                "version": version,
                "runtime_dir": str(installed_runtime),
                "installed_dir": str(final_dir),
                "manifest_sha256": _manifest_digest(manifest),
            })
            state.update({"state": "ready", "message": "OCR 组件已就绪", "runtime_dir": str(final_dir), "download_percent": 100, "self_test_status": "passed", "installed_manifest_sha256": _manifest_digest(manifest)})
            if not self._write_install_state(install_id, state, force=True):
                return
        except OcrCancelled:
            state.update({
                "state": "error",
                "message": "组件安装已取消",
                "error": "install_cancelled",
                "error_code": "install_cancelled",
                "failed_stage": state.get("state"),
                "diagnostic": "用户取消了组件安装",
                "retryable": True,
            })
            if not self._write_install_state(install_id, state, force=True):
                return
            shutil.rmtree(staging, ignore_errors=True)
        except OcrError as exc:
            failed_stage = str(state.get("state") or "installing")
            state.update({
                "state": "error",
                "message": exc.message,
                "error": exc.code,
                "error_code": exc.code,
                "failed_stage": failed_stage,
                "diagnostic": _safe_diagnostic(exc),
                "retryable": exc.retryable,
            })
            self._log_install_exception(install_id, exc)
            if not self._write_install_state(install_id, state, force=True):
                return
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            failed_stage = str(state.get("state") or "installing")
            state.update({
                "state": "error",
                "message": "OCR 组件安装失败，请查看诊断详情",
                "error": "install_failed",
                "error_code": "install_failed",
                "failed_stage": failed_stage,
                "diagnostic": _safe_diagnostic(exc),
                "retryable": True,
            })
            self._log_install_exception(install_id, exc)
            self._write_install_state(install_id, state, force=True)
            shutil.rmtree(staging, ignore_errors=True)
        finally:
            if final_dir and previous_dir and not final_dir.exists() and previous_dir.exists():
                os.replace(previous_dir, final_dir)

    @staticmethod
    def _write_model_config(install_root: Path, models_dir: Path, manifest: dict[str, Any]) -> Path:
        config_path = install_root / str(manifest.get("config_subpath") or "mineru.json")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _json_write(config_path, {
            "config_version": "1.0.0",
            "models-dir": {"pipeline": str(models_dir.resolve())},
        })
        return config_path

    def _self_test_runtime(self, install_root: Path, runtime_dir: Path, entrypoint: Path | None, manifest: dict[str, Any]) -> None:
        if entrypoint is None:
            return
        port = _free_port()
        command = [str(entrypoint)]
        command.extend(str(arg).replace("{port}", str(port)) for arg in (manifest.get("entrypoint_args") or manifest.get("args") or []))
        command.extend(["--host", "127.0.0.1", "--port", str(port)])
        log_path = self.root / "ocr-self-test.log"
        with log_path.open("a", encoding="utf-8", errors="replace") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(runtime_dir),
                env=self._runtime_environment(
                    runtime_dir,
                    install_root=install_root,
                    output_root=self.root / "ocr-self-test-output",
                ),
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=log_handle,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
            )
            try:
                health_deadline = _now() + SELF_TEST_HEALTH_TIMEOUT
                while _now() < health_deadline:
                    if self._install_cancel.is_set():
                        raise OcrCancelled()
                    if process.poll() is not None:
                        raise OcrError("ocr_self_test_failed", "OCR 组件自检进程提前退出", 502, retryable=True)
                    try:
                        if requests.get(f"http://127.0.0.1:{port}/health", timeout=2).ok:
                            calibration_path = runtime_dir / str(manifest["calibration_pdf"])
                            with calibration_path.open("rb") as calibration:
                                response = requests.post(
                                    f"http://127.0.0.1:{port}/tasks",
                                    files={"files": (calibration_path.name, calibration, "application/pdf")},
                                    data={"backend": "pipeline", "parse_method": "auto", "return_md": "true"},
                                    timeout=(5, 45),
                                )
                            response.raise_for_status()
                            task_id = str(response.json().get("task_id") or "")
                            if not task_id:
                                raise OcrError("ocr_self_test_failed", "OCR 校准任务没有返回任务 ID", 502, retryable=True)
                            calibration_deadline = _now() + SELF_TEST_CALIBRATION_TIMEOUT
                            while _now() < calibration_deadline:
                                if self._install_cancel.is_set():
                                    raise OcrCancelled()
                                task_response = requests.get(f"http://127.0.0.1:{port}/tasks/{task_id}", timeout=2)
                                task_response.raise_for_status()
                                task = task_response.json()
                                if task.get("status") == "failed":
                                    raise OcrError("ocr_self_test_failed", "OCR 校准任务失败", 502, retryable=True)
                                if task.get("status") == "completed":
                                    result_response = requests.get(f"http://127.0.0.1:{port}/tasks/{task_id}/result", timeout=10)
                                    result_response.raise_for_status()
                                    markdown = OcrEngine._extract_markdown(result_response.json(), calibration_path.stem)
                                    if not markdown.strip():
                                        raise OcrError("ocr_self_test_failed", "OCR 校准结果为空", 502, retryable=True)
                                    return
                                time.sleep(0.5)
                            raise OcrError("ocr_self_test_timeout", "OCR 校准任务超时", 502, retryable=True)
                    except requests.RequestException:
                        time.sleep(0.25)
                raise OcrError("ocr_self_test_timeout", "OCR 组件自检超时", 502, retryable=True)
            finally:
                try:
                    if process.stdin:
                        process.stdin.close()
                except OSError:
                    pass
                _terminate_process_tree(process)
                _wait_port_closed(port)

    def _download_part(self, part: dict[str, Any], destination: Path, on_progress: Callable[[int], None] | None = None) -> None:
        url = str(part.get("url") or "")
        if not url.startswith("https://"):
            raise OcrError("ocr_manifest_invalid", "OCR 组件下载地址必须使用 HTTPS", 502, retryable=True)
        expected_size = int(part.get("size") or 0)
        expected_hash = str(part.get("sha256") or "")
        last_error: BaseException | None = None
        for attempt in range(DOWNLOAD_RETRY_COUNT + 1):
            existing = destination.stat().st_size if destination.exists() else 0
            if expected_size and existing == expected_size and expected_hash and _sha256(destination) == expected_hash:
                return
            if expected_size and existing >= expected_size:
                existing = 0
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            request = Request(url, headers=headers)
            try:
                try:
                    response = urlopen(request, timeout=DOWNLOAD_READ_TIMEOUT)
                except (HTTPError, URLError, TimeoutError, OSError) as exc:
                    raise _DownloadRetryableError(exc) from exc
                status = int(getattr(response, "status", 200) or 200)
                content_range = str(getattr(response, "headers", {}).get("Content-Range", ""))
                append = existing > 0 and status == 206 and content_range.startswith(f"bytes {existing}-")
                if existing > 0 and status == 206 and not append:
                    response.close()
                    try:
                        response = urlopen(Request(url), timeout=DOWNLOAD_READ_TIMEOUT)
                    except (HTTPError, URLError, TimeoutError, OSError) as exc:
                        raise _DownloadRetryableError(exc) from exc
                if not append:
                    existing = 0
                mode = "ab" if append else "wb"
                written = existing
                with response, destination.open(mode) as handle:
                    while True:
                        if self._install_cancel.is_set():
                            raise OcrCancelled()
                        try:
                            chunk = response.read(CHUNK_SIZE)
                        except (HTTPError, URLError, TimeoutError, OSError) as exc:
                            raise _DownloadRetryableError(exc) from exc
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(written)
                if expected_size and written != expected_size:
                    raise OcrError("ocr_download_incomplete", "OCR 组件下载不完整", 502, retryable=True)
                if expected_hash and _sha256(destination) != expected_hash:
                    raise OcrError("ocr_hash_mismatch", "OCR 组件分片校验失败", 502, retryable=True)
                return
            except OcrCancelled:
                raise
            except _DownloadRetryableError as exc:
                last_error = exc.cause
                if attempt >= DOWNLOAD_RETRY_COUNT:
                    if _is_tls_handshake_timeout(exc.cause):
                        raise OcrError("ocr_download_proxy_timeout", "代理 TLS 握手超时，请检查网络或代理后重试", 502, retryable=True) from exc.cause
                    raise OcrError("ocr_download_failed", f"无法下载 OCR 组件：{exc.cause}", 502, retryable=True) from exc.cause
                delay = DOWNLOAD_RETRY_BACKOFF_SECONDS * (2 ** attempt)
                if self._install_cancel.wait(delay):
                    raise OcrCancelled()
        if last_error is not None:
            raise OcrError("ocr_download_failed", f"无法下载 OCR 组件：{last_error}", 502, retryable=True) from last_error

    @staticmethod
    def _extract_archive(path: Path, destination: Path) -> None:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                members = [member for member in archive.infolist() if not OcrManager._skip_archive_member(member.filename)]
                for member in members:
                    target = (destination / member.filename).resolve()
                    try:
                        target.relative_to(destination.resolve())
                    except ValueError as exc:
                        raise OcrError("ocr_archive_invalid", "OCR 组件压缩包包含非法路径", 502, retryable=True) from exc
                members = [member for member in members if len(str(destination / member.filename)) < 240]
                for member in members:
                    archive.extract(member, destination)
            return
        if path.suffix.lower() in {".tar", ".gz", ".tgz", ".xz", ".bz2"}:
            with tarfile.open(path) as archive:
                members = [member for member in archive.getmembers() if not OcrManager._skip_archive_member(member.name)]
                for member in members:
                    target = (destination / member.name).resolve()
                    try:
                        target.relative_to(destination.resolve())
                    except ValueError as exc:
                        raise OcrError("ocr_archive_invalid", "OCR 组件压缩包包含非法路径", 502, retryable=True) from exc
                members = [member for member in members if len(str(destination / member.name)) < 240]
                archive.extractall(destination, members=members)
            return
        raise OcrError("ocr_archive_invalid", f"不支持的 OCR 组件压缩格式：{path.name}", 502, retryable=True)

    @staticmethod
    def _skip_archive_member(name: str) -> bool:
        normalized = str(name).replace("\\", "/")
        return "__pycache__" in Path(normalized).parts or normalized.lower().endswith(".pyc")

    def estimate(self, page_count: int) -> dict[str, int] | None:
        metrics = _json_read(self.metrics_path)
        samples = [float(value) for value in metrics.get("seconds_per_page", []) if isinstance(value, (int, float)) and value > 0]
        if not samples:
            return None
        samples = sorted(samples[-5:])
        median = samples[len(samples) // 2]
        return {"low": max(1, int(page_count * median * 0.7)), "high": max(1, int(page_count * median * 1.8))}

    def _record_metric(self, page_count: int, elapsed: float) -> None:
        if page_count <= 0 or elapsed <= 0:
            return
        metrics = _json_read(self.metrics_path)
        samples = [float(value) for value in metrics.get("seconds_per_page", []) if isinstance(value, (int, float)) and value > 0]
        samples.append(elapsed / page_count)
        _json_write(self.metrics_path, {"seconds_per_page": samples[-5:]})

    def create_job(self, upload_id: str) -> dict[str, Any]:
        if self.runtime_status().get("state") != "ready":
            raise OcrError("ocr_runtime_missing", "请先安装本地 OCR 组件", 503, retryable=True)
        upload = self._upload(upload_id)
        lease = self._acquire_runtime_lease()
        with self._lock:
            try:
                self._refresh_jobs_from_disk()
                for job in self._jobs.values():
                    if job.get("status") not in TERMINAL_JOB_STATES:
                        raise OcrError("ocr_busy", "已有 OCR 任务正在运行", 409, retryable=True)
                job_id = str(uuid.uuid4())
                job = {
                    "ocr_job_id": job_id,
                    "upload_id": upload_id,
                    "filename": upload["filename"],
                    "size_bytes": upload["size_bytes"],
                    "page_count": upload["page_count"],
                    "status": "queued",
                    "phase": "queued",
                    "elapsed_seconds": 0,
                    "eta_seconds": self.estimate(int(upload["page_count"])),
                    "retryable": True,
                    "error": None,
                    "created_at": _iso_now(),
                    "updated_at": _iso_now(),
                    "result_path": str(self.jobs_dir / job_id / "result.md"),
                }
                self._jobs[job_id] = job
                self._job_leases[job_id] = lease
                job_dir = self.jobs_dir / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                _json_write(job_dir / "job.json", job)
                cancel_event = threading.Event()
                self._job_cancel[job_id] = cancel_event
                thread = threading.Thread(target=self._run_job, args=(job_id, cancel_event), daemon=True)
                self._job_threads[job_id] = thread
                thread.start()
                return job
            except Exception:
                lease.release()
                raise

    def _set_active_job_phase(self, phase: str) -> None:
        with self._lock:
            active = next(
                (job for job in self._jobs.values() if job.get("status") not in TERMINAL_JOB_STATES),
                None,
            )
            if active:
                self._update_job(active["ocr_job_id"], {"status": phase, "phase": phase})

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise OcrError("ocr_not_found", "OCR 任务不存在", 404)
        job = dict(job)
        if job.get("started_at") and job.get("status") not in TERMINAL_JOB_STATES:
            job["elapsed_seconds"] = max(0, int(_now() - float(job["started_at"])))
        return job

    def list_recovery_jobs(self) -> list[dict[str, Any]]:
        self._maybe_cleanup_expired_uploads()
        with self._lock:
            jobs = list(self._jobs.values())
        recovered: list[dict[str, Any]] = []
        for job in jobs:
            if job.get("status") != "interrupted":
                continue
            try:
                upload = self._upload(str(job.get("upload_id") or ""))
            except OcrError:
                continue
            recovered.append({**job, "upload_expires_at": upload.get("expires_at")})
        return recovered

    def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") != "interrupted":
            raise OcrError("ocr_not_retryable", "仅能重试应用退出时中断的 OCR 任务", 409, retryable=False)
        return self.create_job(str(job.get("upload_id") or ""))

    def delete_recovery(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") not in TERMINAL_JOB_STATES:
            raise OcrError("ocr_busy", "OCR 任务仍在运行，不能清理", 409, retryable=True)
        upload_id = str(job.get("upload_id") or "")
        with self._lock:
            self._jobs.pop(job_id, None)
            self._job_cancel.pop(job_id, None)
            self._job_threads.pop(job_id, None)
        shutil.rmtree(self.jobs_dir / job_id, ignore_errors=True)
        if upload_id:
            shutil.rmtree(self.uploads_dir / upload_id, ignore_errors=True)
        return {"ok": True, "ocr_job_id": job_id}

    def get_result(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") != "done":
            raise OcrError("ocr_not_ready", "OCR 结果尚未就绪", 409, retryable=True)
        path = Path(str(job.get("result_path") or ""))
        if not path.is_file():
            raise OcrError("ocr_output_missing", "OCR 结果文件不存在", 502, retryable=True)
        return build_ocr_markdown_result(
            path.read_text(encoding="utf-8", errors="replace"),
            str(job["filename"]),
            job_id,
        )

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job.get("status") in TERMINAL_JOB_STATES:
            return job
        event = self._job_cancel.get(job_id)
        if event:
            event.set()
        self.engine.stop()
        try:
            (self.jobs_dir / job_id / "result.md").unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(self.root / "engine-output", ignore_errors=True)
        self._update_job(job_id, {"status": "cancelled", "phase": "cancelled", "error": "OCR 已取消", "retryable": True, "completed_at": _iso_now()})
        return self.get_job(job_id)

    def _update_job(self, job_id: str, changes: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(changes)
            job["updated_at"] = _iso_now()
            _json_write(self.jobs_dir / job_id / "job.json", job)

    def _run_job(self, job_id: str, cancel_event: threading.Event) -> None:
        started = _now()
        self._update_job(job_id, {"status": "starting_engine", "phase": "starting_engine", "started_at": started})
        lease = self._job_leases.get(job_id)
        try:
            if lease is None:
                raise OcrError("ocr_busy", "OCR 运行锁不可用", 409, retryable=True)
            upload = self._upload(self._jobs[job_id]["upload_id"])
            self._update_job(job_id, {"status": "processing", "phase": "processing"})
            result = self._parse_markdown_locked(
                Path(upload["source_path"]),
                ocr_job_id=job_id,
                cancel_event=cancel_event,
            )
            result_path = Path(str(self._jobs[job_id]["result_path"]))
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(result["importedText"], encoding="utf-8")
            elapsed = _now() - started
            self._record_metric(int(upload["page_count"]), elapsed)
            self._update_job(job_id, {"status": "done", "phase": "done", "elapsed_seconds": int(elapsed), "eta_seconds": {"low": 0, "high": 0}, "retryable": False, "error": None, "completed_at": _iso_now()})
        except OcrCancelled:
            self.engine.stop()
            self._update_job(job_id, {"status": "cancelled", "phase": "cancelled", "elapsed_seconds": int(_now() - started), "error": "OCR 已取消", "retryable": True, "completed_at": _iso_now()})
        except OcrError as exc:
            if cancel_event.is_set():
                self._update_job(job_id, {"status": "cancelled", "phase": "cancelled", "elapsed_seconds": int(_now() - started), "error": "OCR 已取消", "retryable": True, "completed_at": _iso_now()})
            else:
                self._update_job(job_id, {"status": "failed", "phase": "failed", "elapsed_seconds": int(_now() - started), "error": exc.message, "error_code": exc.code, "retryable": exc.retryable, "completed_at": _iso_now()})
        except Exception:
            if cancel_event.is_set():
                self._update_job(job_id, {"status": "cancelled", "phase": "cancelled", "elapsed_seconds": int(_now() - started), "error": "OCR 已取消", "retryable": True, "completed_at": _iso_now()})
            else:
                self._update_job(job_id, {"status": "failed", "phase": "failed", "elapsed_seconds": int(_now() - started), "error": "OCR 处理失败，请查看诊断日志", "error_code": "ocr_failed", "retryable": True, "completed_at": _iso_now()})
        finally:
            with self._lock:
                owned_lease = self._job_leases.pop(job_id, None)
            if owned_lease is not None:
                owned_lease.release()

    def shutdown(self) -> None:
        self._install_cancel.set()
        self.engine.stop(force=True)
        with self._lock:
            leases = list(self._job_leases.values())
            self._job_leases.clear()
        for lease in leases:
            lease.release()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_MANAGER: OcrManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_ocr_manager() -> OcrManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = OcrManager()
        return _MANAGER


def shutdown_ocr_runtime() -> None:
    if _MANAGER is not None:
        _MANAGER.shutdown()


atexit.register(shutdown_ocr_runtime)
