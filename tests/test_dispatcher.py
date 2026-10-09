import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sense.commands import load_commands
from sense.dispatcher import Dispatcher

COMMANDS = load_commands()
DAMP = COMMANDS.lookup("desligar motores")
STOP = COMMANDS.lookup("para")
FRENTE = COMMANDS.lookup("andar para frente")
TRAS = COMMANDS.lookup("andar para trás")


class FakeApi(BaseHTTPRequestHandler):
    """Responde com `server.status` e guarda cada pedido em `server.requests`."""

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


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def make(api, clock=None):
    counters = Counter()
    url = f"http://127.0.0.1:{api.server_port}"
    return Dispatcher(url, cooldown_s=2.0, counters=counters, clock=clock or Clock()), counters


def test_envia_post_com_cmd_no_corpo(api):
    dispatcher, counters = make(api)
    assert dispatcher.dispatch(DAMP) is True
    assert api.requests == [("/commands/posture", {"cmd": "damp"})]
    assert counters["commands_sent"] == 1


def test_stop_vai_sem_corpo(api):
    dispatcher, _ = make(api)
    assert dispatcher.dispatch(STOP) is True
    assert api.requests == [("/commands/stop", None)]


def test_move_vai_com_os_valores_no_corpo(api):
    dispatcher, _ = make(api)
    assert dispatcher.dispatch(FRENTE) is True
    assert api.requests == [
        ("/commands/move", {"vx": 1.0, "vy": 0.0, "vyaw": 0.0, "duration_s": 3.0})
    ]


def test_cooldown_nao_confunde_movimentos_diferentes(api):
    dispatcher, counters = make(api)
    assert dispatcher.dispatch(FRENTE) is True
    assert dispatcher.dispatch(TRAS) is True  # outro move, não é repetição
    assert dispatcher.dispatch(TRAS) is False
    assert counters["cooldown_discards"] == 1


def test_cooldown_descarta_o_mesmo_comando_repetido(api):
    clock = Clock()
    dispatcher, counters = make(api, clock)
    assert dispatcher.dispatch(DAMP) is True
    clock.now += 1.0
    assert dispatcher.dispatch(DAMP) is False
    assert dispatcher.dispatch(STOP) is True  # comando diferente passa
    clock.now += 2.0
    assert dispatcher.dispatch(DAMP) is True  # passou o cooldown
    assert len(api.requests) == 3
    assert counters["cooldown_discards"] == 1


@pytest.mark.parametrize("status", [422, 503])
def test_erro_da_api_descarta_e_nao_arma_o_cooldown(api, status, caplog):
    dispatcher, counters = make(api)
    api.status = status
    assert dispatcher.dispatch(DAMP) is False
    assert f"HTTP {status}" in caplog.text and "motivo" in caplog.text
    assert counters["api_rejections"] == 1
    api.status = 202
    assert dispatcher.dispatch(DAMP) is True  # nova tentativa imediata é aceita


def test_api_fora_do_ar_descarta_sem_fila(api, caplog):
    dispatcher, counters = make(api)
    api.shutdown()
    api.server_close()
    assert dispatcher.dispatch(DAMP) is False
    assert "API fora do ar" in caplog.text
    assert counters["api_unreachable"] == 1
