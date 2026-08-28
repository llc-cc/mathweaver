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

    def _consume_request_body(self) -> None:
        """消费测试请求体，避免服务端提前关连接掩盖真实协议行为。"""
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            while True:
                chunk_size = int(self.rfile.readline().split(b";", 1)[0], 16)
                if chunk_size == 0:
                    self.rfile.readline()
                    return
                self.rfile.read(chunk_size)
                self.rfile.read(2)
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)

    def do_POST(self):
        if self.path != "/tasks":
            self.send_error(404)
            return
        self._consume_request_body()
        payload = {"task_id": "mock-task"}
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
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
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
