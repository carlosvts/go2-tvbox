"""Os modos de ponta a ponta: fala sintética entra, o POST chega numa API falsa.

O microfone é trocado por um falso que lê de um PCM; openWakeWord, Vosk, o
transporte TCP e o POST são os de verdade. Pulado nas mesmas condições de
test_recognizer_real.py.
"""

import pytest

from sense import edge
from sense.config import load_config
from tests.test_dispatcher import api  # noqa: F401  (fixture)
from tests.test_recognizer_real import speech


class EndOfAudio(Exception):
    pass


class FakeMicrophone:
    card = None

    def __init__(self, pcm: bytes) -> None:
        self._chunks = [pcm[i : i + 960] for i in range(0, len(pcm) - 960, 960)]

    def read(self) -> bytes:
        if not self._chunks:
            raise EndOfAudio
        return self._chunks.pop(0)

    def drop_backlog(self, max_backlog_s: float) -> int:
        return 0


@pytest.fixture
def pcm(tmp_path):
    return speech(tmp_path, "en-us", "hey jarvis", 1.0) + speech(
        tmp_path, "pt-br", "desligar motores", 3.0
    )


def test_edge_fala_vira_post_na_api(api, pcm, monkeypatch):  # noqa: F811
    monkeypatch.setattr(edge, "Microphone", lambda name, chunk_ms: FakeMicrophone(pcm))
    config = load_config(
        env={"SENSE_MODE": "edge", "GO2_API_URL": f"http://127.0.0.1:{api.server_port}"}
    )
    with pytest.raises(EndOfAudio):
        edge.run(config)
    assert api.requests == [("/commands/posture", {"cmd": "damp"})]


def test_thin_audio_pela_rede_vira_post_e_o_beep_volta(api, pcm):  # noqa: F811
    import socket
    import threading
    import time
    from collections import Counter

    from sense import receiver
    from sense.config import RECEIVER
    from sense.transport.tcp import TcpSender

    probe = socket.create_server(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    config = load_config(
        RECEIVER,
        env={"GO2_API_URL": f"http://127.0.0.1:{api.server_port}", "RECEIVER_PORT": str(port)},
    )
    threading.Thread(target=receiver.run, args=(config,), daemon=True).start()

    # O lado da TV Box: só o TcpSender, alimentado em tempo real.
    beeps, counters = [], Counter()
    sender = TcpSender("127.0.0.1", port, 30, counters, on_beep=lambda: beeps.append(1))
    deadline = time.monotonic() + 15
    while counters["chunks_sent"] == 0:  # espera o receptor carregar os modelos
        assert time.monotonic() < deadline
        sender._next_connect = 0.0
        sender.send(bytes(960))
        time.sleep(0.05)
    for i in range(0, len(pcm) - 960, 960):
        sender.send(pcm[i : i + 960])
        time.sleep(0.03)

    assert api.requests == [("/commands/posture", {"cmd": "damp"})]
    assert beeps == [1]
    assert counters["chunks_dropped_late"] == 0
