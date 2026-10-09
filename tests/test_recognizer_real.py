"""Reconhecedor com os motores reais e fala sintética do espeak-ng.

Pulado se faltar o extra `recognition`, os modelos (scripts/download_models.py),
o espeak-ng ou o sox. Voz sintética não diz nada sobre a acurácia com gente de
verdade: o teste só prova que wake word, gramática e lookup estão ligados.
"""

import shutil
import subprocess
import wave
from collections import Counter

import pytest

from sense.commands import load_commands
from sense.config import load_config

CONFIG = load_config(env={"GO2_API_URL": "http://api:8000"})

pytest.importorskip("openwakeword")
pytest.importorskip("vosk")
if not (CONFIG.vosk_model_path.is_dir() and shutil.which("espeak-ng") and shutil.which("sox")):
    pytest.skip("sem modelo Vosk, espeak-ng ou sox", allow_module_level=True)

from sense.recognizer import build_recognizer  # noqa: E402


def speech(tmp_path, voice, text, silence_after_s):
    raw, out = tmp_path / "raw.wav", tmp_path / "out.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", "140", "-w", raw, text], check=True)
    subprocess.run(
        ["sox", raw, "-r", "16000", "-c", "1", "-b", "16", out, "pad", "0.5", str(silence_after_s)],
        check=True,
    )
    with wave.open(str(out)) as wav:
        return wav.readframes(wav.getnframes())


def run(tmp_path, phrase):
    counters = Counter()
    recognizer = build_recognizer(CONFIG, load_commands(), counters)
    pcm = speech(tmp_path, "en-us", "hey jarvis", 1.0) + speech(tmp_path, "pt-br", phrase, 5.0)
    results = [recognizer.feed(pcm[i : i + 960]) for i in range(0, len(pcm) - 960, 960)]
    return [r.command.name for r in results if r is not None], counters


def test_hey_jarvis_desligar_motores_vira_damp(tmp_path):
    names, counters = run(tmp_path, "desligar motores")
    assert names == ["damp"]
    assert counters["wake_detections"] == 1


def test_frase_fora_da_gramatica_nao_vira_comando(tmp_path):
    names, counters = run(tmp_path, "que horas são agora")
    assert names == []
    assert counters["wake_detections"] == 1
    # Com frases de uma palavra só, o Vosk às vezes encaixa a fala na palavra
    # mais parecida; aí quem barra é a confiança mínima.
    rejected = counters["no_match"] + counters["listen_timeouts"] + counters["low_confidence"]
    assert rejected == 1
