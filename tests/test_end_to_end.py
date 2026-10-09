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
