"""O cliente do fallback e o executor, contra um servidor de inferência falso."""

import cgi
import io
import json
import threading
import time
import wave
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sense.commands import load_commands
from sense.dispatcher import Dispatcher
from sense.edge import Executor
from sense.fallback_client import FallbackClient, Outcome, Utterance
from sense.recognizer import Decision, Kind
from tests.test_dispatcher import api  # noqa: F401  (fixture: a go2-api falsa)

COMMANDS = load_commands()
OK = frozenset({"ok", "accepted"})
PCM = bytes(32000)  # 1 s de silêncio
UTTERANCE = Utterance(PCM, "unk", "levanta [unk]", 0.42)


class FakeServer(BaseHTTPRequestHandler):
    """Guarda cada pedido e responde o que `server.answer` mandar."""

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


def client(server, timeout_s=2.0, on_outcome=lambda outcome: None, port=None):
    counters = Counter()
    url = f"http://127.0.0.1:{port or server.server_port}"
    return FallbackClient(url, "tvbox-teste", timeout_s, 0.3, OK, counters, on_outcome), counters


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condição não aconteceu a tempo"
        time.sleep(0.01)


# ─── POST /v1/utterance ──────────────────────────────────────────────────────


def test_sucesso_envia_wav_e_meta_e_confirma(server):
    fallback, counters = client(server)
    assert fallback.send_utterance(UTTERANCE) is Outcome.CONFIRMED

    ((audio, meta),) = server.utterances
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
        assert wav.readframes(wav.getnframes()) == PCM
    assert len(meta.pop("utterance_id")) == 36  # uuid
    assert meta == {
        "edge_id": "tvbox-teste",
        "reason": "unk",
        "local_hypothesis": "levanta [unk]",
        "local_confidence": 0.42,
    }
    assert counters == {"fallback_sent": 1, "fallback_confirmed": 1}


@pytest.mark.parametrize("status", ["rejected", "not_understood", "qualquer_outro"])
def test_status_de_rejeicao_e_nao_entendi(server, status):
    server.answer = (200, {"status": status, "transcript": ""})
    fallback, counters = client(server)
    assert fallback.send_utterance(UTTERANCE) is Outcome.REJECTED
    assert counters["fallback_rejected"] == 1


def test_timeout_e_nao_confirmado_e_nao_tenta_de_novo(server):
    server.release.clear()  # o servidor recebe e não responde
    fallback, counters = client(server, timeout_s=0.3)
    assert fallback.send_utterance(UTTERANCE) is Outcome.UNCONFIRMED
    assert len(server.utterances) == 1  # chegou lá: não dá para presumir que não executou
    assert counters == {"fallback_sent": 1, "fallback_unconfirmed": 1}


def test_erro_de_rede_e_nao_confirmado(server):
    fallback, counters = client(server, port=1)  # porta sem ninguém
    assert fallback.send_utterance(UTTERANCE) is Outcome.UNCONFIRMED
    assert counters["fallback_unconfirmed"] == 1


@pytest.mark.parametrize("answer", [(500, {"detail": "erro"}), (200, b"<html>"), (200, {"x": 1})])
def test_erro_http_ou_resposta_ilegivel_e_nao_confirmado(server, answer):
    server.answer = answer
    fallback, _ = client(server)
    assert fallback.send_utterance(UTTERANCE) is Outcome.UNCONFIRMED


def test_uma_chamada_em_voo_por_vez(server):
    server.release.clear()
    outcomes = []
    fallback, counters = client(server, on_outcome=outcomes.append)

    assert fallback.submit(UTTERANCE) is True
    assert server.arrived.wait(3)
    assert fallback.submit(UTTERANCE) is False  # descartada, não enfileirada
    assert counters["fallback_busy_discards"] == 1

    server.release.set()
    wait_for(lambda: outcomes == [Outcome.CONFIRMED])
    assert len(server.utterances) == 1
    assert fallback.submit(UTTERANCE) is True  # livre de novo
    wait_for(lambda: len(outcomes) == 2)


# ─── POST /v1/cancel ─────────────────────────────────────────────────────────


def test_cancel_manda_so_o_motivo(server):
    fallback, counters = client(server)
    assert fallback.send_cancel("stop") is True
    assert server.others == [("/v1/cancel", {"reason": "stop"})]
    assert counters["cancels_sent"] == 1


def test_cancel_que_falha_so_vira_log(server, caplog):
    fallback, counters = client(server, port=1)
    assert fallback.send_cancel("stop") is False
    assert counters["cancel_failures"] == 1
    assert "Cancel (stop) não chegou ao servidor" in caplog.text


# ─── Executor: o que cada decisão faz ────────────────────────────────────────


def executor(api, server, port=None):  # noqa: F811
    feedback = []
    fallback, counters = client(server, port=port, on_outcome=lambda o: feedback.append(o))
    dispatcher = Dispatcher(f"http://127.0.0.1:{api.server_port}", 2.0, counters)
    return Executor(dispatcher, fallback, feedback.append), feedback, counters


STOP = Decision(Kind.STOP, "para", 0.9, command=COMMANDS.stop)
SIT = Decision(Kind.LOCAL, "senta", 0.9, command=COMMANDS.lookup("senta"))
FALLBACK = Decision(Kind.FALLBACK, "levanta [unk]", 0.4, reason="unk", audio=PCM)


def test_parada_vai_a_go2_api_e_avisa_o_servidor_sem_virar_utterance(api, server):  # noqa: F811
    run, _, _ = executor(api, server)
    run.handle(STOP)
    assert api.requests == [("/commands/stop", None)]
    wait_for(lambda: server.others == [("/v1/cancel", {"reason": "stop"})])
    assert server.utterances == []


def test_parada_repetida_vai_de_novo(api, server):  # noqa: F811
    run, _, counters = executor(api, server)
    run.handle(STOP)
    run.handle(STOP)
    assert len(api.requests) == 2 and not counters["cooldown_discards"]


def test_cancel_lento_ou_falho_nao_segura_a_parada(api, server):  # noqa: F811
    server.cancel_delay_s = 1.0  # mais que o timeout do cancel (0,3 s)
    run, _, counters = executor(api, server)
    start = time.monotonic()
    run.handle(STOP)
    assert time.monotonic() - start < 0.25  # não esperou o servidor
    assert api.requests == [("/commands/stop", None)]
    wait_for(lambda: counters["cancel_failures"] == 1)

    run, _, counters = executor(api, server, port=1)  # servidor fora do ar
    run.handle(STOP)
    assert len(api.requests) == 2
    wait_for(lambda: counters["cancel_failures"] == 1)


def test_comando_local_executa_e_cancela_a_fila_do_servidor(api, server):  # noqa: F811
    run, _, _ = executor(api, server)
    run.handle(SIT)
    assert api.requests == [("/commands/posture", {"cmd": "sit"})]
    wait_for(lambda: server.others == [("/v1/cancel", {"reason": "local_command"})])


def test_comando_local_que_a_api_recusa_nao_cancela(api, server):  # noqa: F811
    api.status = 503
    run, _, _ = executor(api, server)
    run.handle(SIT)
    time.sleep(0.1)
    assert server.others == []


def test_fallback_manda_o_audio_da_o_retorno_e_nao_executa_nada(api, server):  # noqa: F811
    server.answer = (200, {"status": "ok", "transcript": "senta", "command": "sit"})
    run, feedback, _ = executor(api, server)
    run.handle(FALLBACK)
    assert feedback[0] == "processing"  # na hora, antes da resposta
    wait_for(lambda: feedback == ["processing", Outcome.CONFIRMED])
    ((_, meta),) = server.utterances
    assert (meta["reason"], meta["local_hypothesis"]) == ("unk", "levanta [unk]")
    assert api.requests == []  # a resposta do servidor nunca vira comando aqui
    assert server.others == []


def test_fallback_durante_outro_em_voo_e_descartado_com_retorno(api, server):  # noqa: F811
    server.release.clear()
    run, feedback, _ = executor(api, server)
    run.handle(FALLBACK)
    assert server.arrived.wait(3)
    run.handle(FALLBACK)
    assert feedback == ["processing", "rejected"]
    server.release.set()
    wait_for(lambda: len(feedback) == 3)
    assert len(server.utterances) == 1


def test_sem_servidor_parada_e_comando_local_funcionam(api):  # noqa: F811
    dispatcher = Dispatcher(f"http://127.0.0.1:{api.server_port}", 2.0, Counter())
    run = Executor(dispatcher, None, lambda name: None)
    run.handle(STOP)
    run.handle(SIT)
    run.handle(FALLBACK)  # não há para onde mandar: não faz nada
    assert api.requests == [("/commands/stop", None), ("/commands/posture", {"cmd": "sit"})]
