from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr_runtime import OcrManager


class _MineruHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _read_request_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks: list[bytes] = []
            while True:
                size_line = self.rfile.readline()
                size = int(size_line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return b"".join(chunks)
                chunks.append(self.rfile.read(size))
                if self.rfile.read(2) != b"\r\n":
                    raise ValueError("invalid chunk terminator")

        content_length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(content_length)

    def do_POST(self):
        if self.path != "/tasks":
            self.send_error(404)
            return
        body = self._read_request_body()
        self.server.requests.append(("POST", self.path, body))  # type: ignore[attr-defined]
        payload = {"task_id": "mock-task"}
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        self.server.requests.append(("GET", self.path, b""))  # type: ignore[attr-defined]
        if self.path == "/tasks/mock-task":
            payload = {"task_id": "mock-task", "status": "completed"}
        elif self.path == "/tasks/mock-task/result":
            payload = {"md_content": "# calibrated\n"}
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


class OcrEngineProtocolTests(unittest.TestCase):
    def test_tasks_protocol_and_result_markdown(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _MineruHandler)
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                manager = OcrManager(root=Path(directory), manifest_path=Path(directory) / "manifest.json")
                engine = manager.engine
                engine.start = lambda: setattr(engine, "base_url", f"http://127.0.0.1:{server.server_port}")  # type: ignore[method-assign]
                with (Path(directory) / "input.pdf").open("wb") as path:
                    path.write(b"%PDF-1.0")
                markdown = engine.parse(Path(directory) / "input.pdf", threading.Event())
                self.assertEqual(markdown, "# calibrated\n")
                self.assertEqual(
                    [(method, path) for method, path, _body in server.requests],  # type: ignore[attr-defined]
                    [
                        ("POST", "/tasks"),
                        ("GET", "/tasks/mock-task"),
                        ("GET", "/tasks/mock-task/result"),
                    ],
                )
                upload_body = server.requests[0][2]  # type: ignore[attr-defined]
                self.assertIn(b'name="backend"', upload_body)
                self.assertIn(b"pipeline", upload_body)
                self.assertIn(b"%PDF-1.0", upload_body)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
