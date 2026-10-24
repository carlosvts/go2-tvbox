import re
import wave

import numpy as np

from scripts import generate_beeps as beeps

# Janela em que o VAD ignora o começo da escuta (BEEP_IGNORE_S padrão), com folga
# para o `aplay` abrir o dispositivo antes de o som sair.
BEEP_IGNORE_S = 0.9
START_LATENCY_S = 0.1


def sounds():
    return [beeps.finish(beeps.chime(*pair)) for pair in beeps.PAIRS]


def test_ha_variantes_e_nomes_seguem_o_padrao_do_tocador():
    names = [beeps.filename(i) for i in range(len(beeps.PAIRS))]
    assert names[0] == "beep.wav" and len(names) > 1
    assert all(re.fullmatch(r"beep(_\d+)?\.wav", name) for name in names)
    assert len(set(names)) == len(names)


def test_comeca_em_silencio_para_o_alto_falante_usb_acordar():
    for y in sounds():
        lead = int(beeps.RATE * beeps.LEAD_SILENCE_MS / 1000)
        assert np.max(np.abs(y[:lead])) == 0


def test_some_antes_do_fim_da_janela_ignorada_pelo_vad():
    for y in sounds():
        assert beeps.audible_until_s(y) + START_LATENCY_S < BEEP_IGNORE_S


def test_volume_igual_entre_variantes_sem_estourar():
    levels = []
    for y in sounds():
        lead = int(beeps.RATE * beeps.LEAD_SILENCE_MS / 1000)
        levels.append(20 * np.log10(np.sqrt(np.mean(y[lead:] ** 2))))
        assert np.max(np.abs(y)) <= beeps.PEAK_LIMIT
    assert max(levels) - min(levels) < 0.5


def test_gera_os_arquivos_e_troca_os_beeps_antigos(tmp_path, monkeypatch):
    (tmp_path / "beep_7.wav").write_bytes(b"velho")
    (tmp_path / "confirmed.wav").write_bytes(b"outro som")
    monkeypatch.setattr("sys.argv", ["generate_beeps", "--out", str(tmp_path)])
    beeps.main()
    assert not (tmp_path / "beep_7.wav").exists()
    assert (tmp_path / "confirmed.wav").read_bytes() == b"outro som"
    with wave.open(str(tmp_path / "beep.wav")) as wav:
        assert wav.getframerate() == beeps.RATE and wav.getnchannels() == 1
    assert len(list(tmp_path.glob("beep*.wav"))) == len(beeps.PAIRS)
