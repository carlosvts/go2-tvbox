"""O motor de wake word de verdade, com fala sintética e ruído.

Pulado se faltar o extra `recognition`, o modelo (scripts/download_models.py), o
espeak-ng ou o sox. Voz sintética não diz nada sobre a acurácia com gente de
verdade: o teste só prova que o motor está ligado e que o VAD dele filtra ruído.
"""

import shutil
import subprocess
import wave

import numpy as np
import pytest

from sense.wake import OpenWakeWordEngine

pytest.importorskip("openwakeword")
if not (shutil.which("espeak-ng") and shutil.which("sox")):
    pytest.skip("sem espeak-ng ou sox", allow_module_level=True)
try:
    OpenWakeWordEngine("hey_jarvis")
except Exception as error:  # modelo não baixado
    pytest.skip(f"wake word não carregou: {error}", allow_module_level=True)


def speech(tmp_path, voice, text, silence_after_s):
    """Fala sintética do espeak-ng em 16 kHz mono PCM16, com silêncio depois."""
    raw, out = tmp_path / "raw.wav", tmp_path / "out.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", "140", "-w", raw, text], check=True)
    subprocess.run(
        ["sox", raw, "-r", "16000", "-c", "1", "-b", "16", out, "pad", "0.5", str(silence_after_s)],
        check=True,
    )
    with wave.open(str(out)) as wav:
        return wav.readframes(wav.getnframes())


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
