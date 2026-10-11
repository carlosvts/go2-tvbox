"""Segmentação do comando por volume, com áudio sintético."""

import numpy as np
import pytest

from sense.vad import End, Segmenter, rms, speech_level

CHUNK_S = 0.03


def chunks(seconds, amplitude):
    """`seconds` de tom com a amplitude dada, em chunks de 30 ms."""
    t = np.arange(480) / 16000
    chunk = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16).tobytes()
    return [chunk] * round(seconds / CHUNK_S)


SILENCE = lambda seconds: chunks(seconds, 30)  # noqa: E731  ruído de fundo baixo
SPEECH = lambda seconds: chunks(seconds, 3000)  # noqa: E731


def segment(audio, **overrides):
    """Alimenta o segmentador; devolve (resultado, quantos chunks ele consumiu)."""
    params = dict(end_silence_s=0.7, min_speech_s=0.2, max_s=8.0, speech_timeout_s=5.0)
    segmenter = Segmenter(speech_level=300.0, **{**params, **overrides})
    for index, chunk in enumerate(audio, start=1):
        result = segmenter.feed(chunk)
        if result is not None:
            return result, index
    return None, len(audio)


def test_rms_e_limiar_adaptado_ao_ruido():
    assert rms(bytes(960)) == 0.0
    assert 2000 < rms(SPEECH(0.03)[0]) < 2300  # amplitude 3000 → RMS ≈ 2121
    quiet, noisy = [20.0] * 80 + [3000.0] * 20, [400.0] * 80 + [3000.0] * 20
    assert speech_level(quiet, ratio=3.0, min_rms=150.0) == 150.0  # piso
    assert speech_level(noisy, ratio=3.0, min_rms=150.0) == 1200.0  # 3× o ruído
    assert speech_level([], ratio=3.0, min_rms=150.0) == 150.0


def test_fala_seguida_de_silencio_fecha_apos_o_silencio_de_fim():
    result, used = segment(SILENCE(0.5) + SPEECH(1.2) + SILENCE(3.0))
    assert result.end is End.SPEECH
    assert used * CHUNK_S == pytest.approx(0.5 + 1.2 + 0.7, abs=0.06)
    assert result.voiced_s == pytest.approx(1.2, abs=0.03)
    assert result.silence_s == pytest.approx(0.7, abs=0.03)


def test_so_silencio_acaba_no_timeout_de_espera():
    result, used = segment(SILENCE(10))
    assert result.end is End.NO_SPEECH
    assert round(used * CHUNK_S, 2) == 5.01


def test_ruido_curto_e_descartado_e_nao_vira_fala():
    # 90 ms de estalo: menos que a fala mínima de 200 ms.
    result, _ = segment(SILENCE(0.5) + SPEECH(0.09) + SILENCE(10))
    assert result.end is End.NO_SPEECH and result.voiced_s == 0.0


def test_fala_depois_de_um_ruido_curto_ainda_vale():
    result, _ = segment(SPEECH(0.09) + SILENCE(1.0) + SPEECH(1.0) + SILENCE(2.0))
    assert result.end is End.SPEECH
    assert result.voiced_s == pytest.approx(1.0, abs=0.03)  # o estalo não conta


def test_fala_longa_e_truncada_na_duracao_maxima():
    result, used = segment(SPEECH(12))
    assert result.end is End.TRUNCATED
    assert round(used * CHUNK_S, 2) == 8.01


def test_pausa_curta_no_meio_da_fala_nao_fecha():
    result, _ = segment(SPEECH(0.8) + SILENCE(0.4) + SPEECH(0.8) + SILENCE(2.0))
    assert result.end is End.SPEECH
    assert result.voiced_s == pytest.approx(1.6, abs=0.03)


def test_fala_que_comeca_perto_do_timeout_nao_e_cortada():
    result, _ = segment(SILENCE(4.8) + SPEECH(1.0) + SILENCE(2.0))
    assert result.end is End.SPEECH


def test_comeco_ignorado_nao_conta_como_voz():
    # O beep (0,5 s) toca logo depois da wake word.
    result, _ = segment(SPEECH(0.5) + SILENCE(10), ignore_s=0.6)
    assert result.end is End.NO_SPEECH
    result, _ = segment(SPEECH(0.5) + SILENCE(0.3) + SPEECH(1.0) + SILENCE(2), ignore_s=0.6)
    assert result.end is End.SPEECH and result.voiced_s == pytest.approx(1.0, abs=0.03)
