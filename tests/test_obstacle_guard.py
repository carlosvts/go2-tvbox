"""O guarda do desvio de obstáculo, contra uma go2-api falsa."""

import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sense.commands import load_commands
from sense.dispatcher import Dispatcher
from sense.edge import Executor
from sense.obstacle_guard import ObstacleGuard
from sense.recognizer import Decision, Kind

COMMANDS = load_commands()


class FakeApi(BaseHTTPRequestHandler):
    """go2-api falsa: guarda os pedidos e imita o liga/desliga do desvio."""

    def _answer(self, status, body):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        server = self.server
        server.calls.append("GET")
        if server.get_status != 200:
            return self._answer(server.get_status, {"detail": "erro"})
        self._answer(200, {"enabled": server.enabled, "raw": {"data": "..."}})

    def do_PUT(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(("PUT", body))
        if server.put_works:
            server.enabled = body["enabled"]
        self._answer(202, {"accepted": True, "cmd": "ObstacleAvoidance"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else None
        self.server.calls.append(("POST", self.path, body))
        self._answer(202, {"accepted": True})

    def log_message(self, *args):
        pass


@pytest.fixture
def api():
    server = HTTPServer(("127.0.0.1", 0), FakeApi)
    server.enabled = False  # o que o GET devolve em `enabled`
    server.put_works = True  # o PUT liga de fato
    server.get_status = 200
    server.calls = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def guard(api, port=None):
    counters = Counter()
    url = f"http://127.0.0.1:{port or api.server_port}"
    return ObstacleGuard(url, counters, sleep=lambda seconds: None), counters


def test_ja_ligado_so_confere(api):
    api.enabled = True
    obstacle_guard, counters = guard(api)
    assert obstacle_guard.ensure_on() is True
    assert api.calls == ["GET"]
    assert not counters


def test_desligado_liga_e_confirma(api):
    obstacle_guard, counters = guard(api)
    assert obstacle_guard.ensure_on() is True
    assert api.calls == ["GET", ("PUT", {"enabled": True}), "GET"]
    assert counters["obstacle_avoidance_switched_on"] == 1


def test_estado_nao_reconhecido_tenta_ligar_e_nao_confirma(api, caplog):
    api.enabled = None  # a API não entendeu a resposta do robô
    api.put_works = False
    obstacle_guard, counters = guard(api)
    assert obstacle_guard.ensure_on() is False
    assert api.calls == ["GET", ("PUT", {"enabled": True}), "GET", "GET", "GET"]
    assert counters["obstacle_avoidance_unconfirmed"] == 1
    assert "estado não reconhecido" in caplog.text


@pytest.mark.parametrize("status", [404, 503, 504])
def test_api_sem_o_endpoint_ou_sem_o_robo_nao_confirma(api, status):
    api.get_status = status
    obstacle_guard, _ = guard(api)
    assert obstacle_guard.ensure_on() is False


def test_api_fora_do_ar_nao_confirma(api):
    obstacle_guard, counters = guard(api, port=1)
    assert obstacle_guard.ensure_on() is False
    assert counters["obstacle_avoidance_unconfirmed"] == 1


# ─── No executor ─────────────────────────────────────────────────────────────


def executor(api, with_guard=True):
    feedback, counters = [], Counter()
    url = f"http://127.0.0.1:{api.server_port}"
    obstacle_guard = ObstacleGuard(url, counters, sleep=lambda s: None) if with_guard else None
    run = Executor(Dispatcher(url, 2.0, counters), None, feedback.append, obstacle_guard)
    return run, feedback


def local(phrase):
    return Decision(Kind.LOCAL, phrase, 0.9, command=COMMANDS.lookup(phrase))


MOVES = [phrase for phrase in COMMANDS.grammar if COMMANDS.lookup(phrase).name == "move"]


@pytest.mark.parametrize("phrase", MOVES)
def test_todo_movimento_liga_o_desvio_antes_de_sair(api, phrase):
    run, _ = executor(api)
    run.handle(local(phrase))
    assert api.calls[:3] == ["GET", ("PUT", {"enabled": True}), "GET"]
    assert api.calls[3] == ("POST", "/commands/move", COMMANDS.lookup(phrase).body)


def test_movimento_sem_o_desvio_confirmado_nao_sai(api, caplog):
    api.put_works = False
    run, feedback = executor(api)
    run.handle(local("andar para frente"))
    assert not [call for call in api.calls if call[0] == "POST"]
    assert feedback == ["rejected"]
    assert "sem o desvio de obstáculo ligado" in caplog.text


def test_comandos_que_nao_sao_movimento_e_a_parada_nao_dependem_do_desvio(api):
    api.put_works = False
    run, _ = executor(api)
    run.handle(local("senta"))
    run.handle(Decision(Kind.STOP, "para", 0.9, command=COMMANDS.stop))
    assert api.calls == [
        ("POST", "/commands/posture", {"cmd": "sit"}),
        ("POST", "/commands/stop", None),
    ]


def test_sem_o_guarda_o_movimento_sai_direto(api):
    run, _ = executor(api, with_guard=False)
    run.handle(local("andar para frente"))
    assert [call[0] for call in api.calls] == ["POST"]
