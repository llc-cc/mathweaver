"""Desktop FastAPI wrapper for the existing MathGraph API.

This file is intentionally separate from api_v2.py. It provides the Windows
desktop packaging surface, streaming OCR endpoints, health checks, static
frontend hosting, and a thin proxy to the current Flask API implementation.
"""

from __future__ import annotations

import mimetypes
import multiprocessing
import os
import sys
import threading
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent


def _load_desktop_storage_environment() -> Path | None:
    """Load the host-local database configuration without bundling secrets."""
    data_dir = os.environ.get("MATHGRAPH_DATA_DIR", "").strip()
    if not data_dir:
        return None
    env_path = Path(data_dir).expanduser() / "storage.env"
    if not env_path.is_file():
        return None
    load_dotenv(env_path, override=False)
    return env_path


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base.joinpath(*parts)


FRONTEND_DIR = _resource_path("frontend")
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = PROJECT_ROOT / "build" / "client"

os.environ.setdefault("AI4MATH_DESKTOP", "1")
_load_desktop_storage_environment()

from api_v2 import (  # noqa: E402
    app as flask_app,
    reconcile_interrupted_history,
)
from ocr_runtime import CHUNK_SIZE, IMAGE_MAX_BYTES, PDF_MAX_BYTES, OcrError, get_ocr_manager, shutdown_ocr_runtime  # noqa: E402

app = FastAPI(title="MathGraph Desktop")

_PARENT_EXIT_GRACE_SECONDS = 5.0


@app.on_event("startup")
def initialize_runtime_storage() -> None:
    reconcile_interrupted_history()


def _wait_for_windows_process_exit(pid: int) -> None:
    """Wait until the specific Windows process represented by pid exits."""
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(synchronize, False, pid)
    if not handle:
        return
    try:
        wait_for_single_object(handle, infinite)
    finally:
        close_handle(handle)


def _start_parent_exit_watcher(
    server,
    shutdown_complete: threading.Event,
    *,
    force_exit=os._exit,
    grace_seconds: float = _PARENT_EXIT_GRACE_SECONDS,
):
    if os.name != "nt":
        return None

    raw_parent_pid = os.environ.get("MATHGRAPH_PARENT_PID", "").strip()
    try:
        parent_pid = int(raw_parent_pid)
    except ValueError:
        return None
    if parent_pid <= 0:
        return None

    def watch_parent() -> None:
        _wait_for_windows_process_exit(parent_pid)
        server.should_exit = True
        if not shutdown_complete.wait(grace_seconds):
            force_exit(0)

    watcher = threading.Thread(
        target=watch_parent,
        name="mathweaver-parent-watcher",
        daemon=True,
    )
    watcher.start()
    return watcher


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mathgraph-desktop"})


def _ocr_error_response(exc: OcrError) -> JSONResponse:
    payload = {"error": exc.code, "message": exc.message, "retryable": exc.retryable, "error_code": exc.code}
    if exc.code == "ocr_component_unavailable":
        payload["installable"] = False
    return JSONResponse(
        payload,
        status_code=exc.status_code,
    )


@app.get("/api/v2/ocr/runtime")
async def ocr_runtime_status_desktop() -> JSONResponse:
    return JSONResponse(get_ocr_manager().runtime_status())


@app.post("/api/v2/ocr/runtime/install")
async def ocr_runtime_install_desktop() -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().start_install(), status_code=202)
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.get("/api/v2/ocr/runtime/install/{install_id}")
async def ocr_runtime_install_status_desktop(install_id: str) -> JSONResponse:
    state = get_ocr_manager().runtime_status()
    if state.get("install_id") != install_id:
        return JSONResponse({"error": "install_not_found", "message": "install task not found"}, status_code=404)
    return JSONResponse(state)


@app.post("/api/v2/ocr/runtime/install/{install_id}/cancel")
async def ocr_runtime_install_cancel_desktop(install_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().cancel_install(install_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.get("/api/v2/ocr/recovery")
async def ocr_recovery_list_desktop() -> JSONResponse:
    return JSONResponse({"jobs": get_ocr_manager().list_recovery_jobs()})


@app.post("/api/v2/ocr/uploads")
async def ocr_upload_desktop(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    if not file.filename:
        return JSONResponse({"error": "invalid_file", "message": "file is required"}, status_code=400)
    try:
        content_length = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        content_length = 0
    max_bytes = PDF_MAX_BYTES if Path(file.filename).suffix.lower() == ".pdf" else IMAGE_MAX_BYTES
    if content_length > max_bytes + 2 * CHUNK_SIZE:
        return JSONResponse({"error": "file_too_large", "message": "request exceeds the OCR upload limit"}, status_code=413)
    manager = get_ocr_manager()
    writer = None
    try:
        writer = manager.begin_upload(file.filename)
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
        return JSONResponse(writer.finish(), status_code=201)
    except OcrError as exc:
        if writer:
            writer.abort()
        return _ocr_error_response(exc)
    except Exception as exc:
        if writer:
            writer.abort()
        return JSONResponse({"error": "upload_failed", "message": str(exc), "retryable": True}, status_code=400)
    finally:
        await file.close()


@app.delete("/api/v2/ocr/uploads/{upload_id}")
async def ocr_upload_delete_desktop(upload_id: str) -> JSONResponse:
    try:
        get_ocr_manager().delete_upload(upload_id)
        return JSONResponse({"ok": True})
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.post("/api/v2/ocr/jobs")
async def ocr_job_create_desktop(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        return JSONResponse(get_ocr_manager().create_job(str(body.get("upload_id") or "")), status_code=202)
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.get("/api/v2/ocr/jobs/{ocr_job_id}")
async def ocr_job_status_desktop(ocr_job_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().get_job(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.get("/api/v2/ocr/jobs/{ocr_job_id}/result")
async def ocr_job_result_desktop(ocr_job_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().get_result(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.post("/api/v2/ocr/jobs/{ocr_job_id}/cancel")
async def ocr_job_cancel_desktop(ocr_job_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().cancel_job(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.post("/api/v2/ocr/jobs/{ocr_job_id}/retry")
async def ocr_job_retry_desktop(ocr_job_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().retry_job(ocr_job_id), status_code=202)
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.delete("/api/v2/ocr/recovery/{ocr_job_id}")
async def ocr_recovery_delete_desktop(ocr_job_id: str) -> JSONResponse:
    try:
        return JSONResponse(get_ocr_manager().delete_recovery(ocr_job_id))
    except OcrError as exc:
        return _ocr_error_response(exc)


@app.post("/api/v2/proof-import-ocr")
async def proof_import_ocr_desktop() -> JSONResponse:
    return JSONResponse(
        {"error": "ocr_api_replaced", "message": "Use the streaming OCR upload and job APIs.", "retryable": False},
        status_code=410,
    )


@app.api_route("/api/v2/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_v2_proxy(path: str, request: Request) -> Response:
    """Forward desktop API calls to the existing Flask app without changing it."""
    body = await request.body()
    target = f"/api/v2/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }

    with flask_app.test_client() as client:
        flask_response = client.open(
            path=target,
            method=request.method,
            headers=headers,
            data=body,
        )

    excluded = {"content-length", "transfer-encoding", "connection"}
    response_headers = [
        (key, value)
        for key, value in flask_response.headers.items()
        if key.lower() not in excluded
    ]
    return Response(
        content=flask_response.get_data(),
        status_code=flask_response.status_code,
        headers=dict(response_headers),
        media_type=flask_response.mimetype,
    )


@app.get("/{path:path}")
def frontend(path: str = ""):
    """Serve the React Router SPA bundle and fall back to index.html."""
    if not FRONTEND_DIR.exists():
        return JSONResponse(
            {"error": f"Frontend build not found: {FRONTEND_DIR}"},
            status_code=500,
        )

    requested = (FRONTEND_DIR / path).resolve()
    try:
        requested.relative_to(FRONTEND_DIR.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if requested.is_file():
        media_type, _ = mimetypes.guess_type(str(requested))
        # Windows maps .mjs to text/plain by default, which makes Chromium
        # reject PDF.js' dynamically imported worker as a module script.
        if requested.suffix.lower() == ".mjs":
            media_type = "text/javascript"
        return FileResponse(requested, media_type=media_type)

    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")

    return JSONResponse({"error": "index.html not found"}, status_code=500)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MATHGRAPH_PORT", "8000"))
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        http="h11",
    )
    server = uvicorn.Server(config)
    shutdown_complete = threading.Event()
    _start_parent_exit_watcher(server, shutdown_complete)
    try:
        server.run()
    finally:
        shutdown_ocr_runtime()
        shutdown_complete.set()
