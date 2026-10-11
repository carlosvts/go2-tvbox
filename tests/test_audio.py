import numpy as np

from sense.audio import alsa_card, downsample


def test_downsample_pega_uma_de_cada_tres_amostras():
    raw_48k = np.arange(1440, dtype=np.int16).tobytes()  # 30 ms a 48 kHz
    out = np.frombuffer(downsample(raw_48k), dtype=np.int16)
    assert len(out) == 480  # 30 ms a 16 kHz
    assert list(out[:3]) == [0, 3, 6]


def test_alsa_card_sai_do_nome_do_dispositivo():
    assert alsa_card("Anker PowerConf S3: USB Audio (hw:1,0)") == 1
    assert alsa_card("pulse") is None
