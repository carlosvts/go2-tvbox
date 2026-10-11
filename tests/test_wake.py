"""O motor de wake word de verdade, com fala sintética e ruído.

Pulado nas mesmas condições de test_recognizer_real.py.
"""

import numpy as np
import pytest

from sense.wake import OpenWakeWordEngine
from tests.test_recognizer_real import speech

NOISE = np.random.default_rng(0).normal(0, 4000, 16000 * 5).astype(np.int16).tobytes()


def peak(engine, pcm):
    """A maior nota da wake word ao longo do áudio, em chunks de 30 ms."""
    engine.reset()
    return max(engine.score(pcm[i : i + 960]) for i in range(0, len(pcm) - 960, 960))


@pytest.fixture(scope="module")
def hey_jarvis(tmp_path_factory):
    return speech(tmp_path_factory.mktemp("wake"), "en-us", "hey jarvis", 1.5)


@pytest.mark.parametrize("vad_threshold", [0.0, 0.5])
def test_wake_word_e_detectada_com_e_sem_vad(hey_jarvis, vad_threshold):
    engine = OpenWakeWordEngine("hey_jarvis", vad_threshold)
    assert peak(engine, hey_jarvis) > 0.85


def test_vad_zera_a_nota_quando_nao_ha_voz():
    assert peak(OpenWakeWordEngine("hey_jarvis"), NOISE) > 0.0  # sem VAD o ruído pontua
    assert peak(OpenWakeWordEngine("hey_jarvis", 0.5), NOISE) == 0.0


def test_reset_esquece_a_voz_ouvida_antes(hey_jarvis):
    engine = OpenWakeWordEngine("hey_jarvis", 0.5)
    assert peak(engine, hey_jarvis) > 0.85
    engine.reset()
    assert engine.score(NOISE[:960]) == 0.0
