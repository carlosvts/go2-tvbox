"""Fixtures compartilhadas: uma go2-api falsa e um servidor de inferência falso."""

import cgi
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import pytest

OK = frozenset({"ok", "accepted"})


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condição não aconteceu a tempo"
        time.sleep(0.01)


class FakeApi(BaseHTTPRequestHandler):
    """go2-api: responde com `server.status` e guarda cada pedido em `server.requests`."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else None
        self.server.requests.append((self.path, body))
        self.send_response(self.server.status)
        self.end_headers()
        self.wfile.write(b'{"detail": "motivo"}')

    def log_message(self, *args):
        pass


@pytest.fixture
def api():
    server = HTTPServer(("127.0.0.1", 0), FakeApi)
    server.status = 202
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


class FakeServer(BaseHTTPRequestHandler):
    """Servidor de inferência: guarda cada pedido e responde o que `server.answer` mandar."""

    def do_POST(self):
        server = self.server
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/v1/utterance":
            _, params = cgi.parse_header(self.headers["Content-Type"])
            fields = cgi.parse_multipart(io.BytesIO(body), {"boundary": params["boundary"].encode()})
            server.utterances.append((fields["audio"][0], json.loads(fields["meta"][0])))
            server.arrived.set()
            server.release.wait(5)
        else:
            server.others.append((self.path, json.loads(body)))
            time.sleep(server.cancel_delay_s)
        status, answer = server.answer
        self.send_response(status)
        self.end_headers()
        self.wfile.write(answer if isinstance(answer, bytes) else json.dumps(answer).encode())

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
    server.answer = (200, {"status": "ok", "transcript": "levanta por favor"})
    server.utterances, server.others = [], []
    server.arrived, server.release = threading.Event(), threading.Event()
    server.release.set()  # responde na hora, a menos que o teste segure
    server.cancel_delay_s = 0.0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.release.set()
    server.shutdown()
