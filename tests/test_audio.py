import numpy as np

import random
import threading
import time

from sense.audio import SoundPlayer, alsa_card, downsample


def test_downsample_pega_uma_de_cada_tres_amostras():
    raw_48k = np.arange(1440, dtype=np.int16).tobytes()  # 30 ms a 48 kHz
    out = np.frombuffer(downsample(raw_48k), dtype=np.int16)
    assert len(out) == 480  # 30 ms a 16 kHz
    assert list(out[:3]) == [0, 3, 6]


def test_alsa_card_sai_do_nome_do_dispositivo():
    assert alsa_card("Anker PowerConf S3: USB Audio (hw:1,0)") == 1
    assert alsa_card("pulse") is None


def _media(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"RIFF")
    return tmp_path


class FakeAplay:
    """Registra as chamadas e mede se alguma tocou ao mesmo tempo que outra."""

    def __init__(self, duration_s=0.0):
        self.played = []
        self.running = 0
        self.max_running = 0
        self.duration_s = duration_s
        self._lock = threading.Lock()

    def __call__(self, command, **kwargs):
        with self._lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        time.sleep(self.duration_s)
        self.played.append((command[2], command[-1].rsplit("/", 1)[-1]))
        with self._lock:
            self.running -= 1


def test_sem_card_ou_sem_arquivo_nao_toca_nem_quebra(tmp_path):
    fake = FakeAplay()
    player = SoundPlayer(_media(tmp_path, "confirmed.wav"), runner=fake)
    player.play(None, "confirmed")
    player.play(1, "nao_existe")
    player.wait_idle()
    assert fake.played == []


def test_chama_aplay_no_card_certo(tmp_path):
    fake = FakeAplay()
    player = SoundPlayer(_media(tmp_path, "confirmed.wav"), runner=fake)
    player.play(3, "confirmed")
    player.wait_idle()
    assert fake.played == [("plughw:3,0", "confirmed.wav")]


def test_dois_sons_seguidos_tocam_em_sequencia_e_nunca_juntos(tmp_path):
    fake = FakeAplay(duration_s=0.05)
    player = SoundPlayer(_media(tmp_path, "processing.wav", "confirmed.wav"), runner=fake)
    player.play(1, "processing")
    player.play(1, "confirmed")
    player.wait_idle()
    assert [name for _, name in fake.played] == ["processing.wav", "confirmed.wav"]
    assert fake.max_running == 1


def test_play_nao_bloqueia_quem_chama(tmp_path):
    fake = FakeAplay(duration_s=0.3)
    player = SoundPlayer(_media(tmp_path, "processing.wav"), runner=fake)
    start = time.perf_counter()
    player.play(1, "processing")
    assert time.perf_counter() - start < 0.1
    player.wait_idle()


def test_variantes_sao_so_nome_e_nome_numero(tmp_path):
    media = _media(tmp_path, "processing.wav", "processing_2.wav", "processing_extra.wav", "processing_10.wav")
    player = SoundPlayer(media, runner=FakeAplay())
    assert [p.name for p in player.variants("processing")] == [
        "processing.wav",
        "processing_10.wav",
        "processing_2.wav",
    ]


def test_variante_nao_repete_a_anterior(tmp_path):
    fake = FakeAplay()
    media = _media(tmp_path, "rejected.wav", "rejected_2.wav", "rejected_3.wav")
    player = SoundPlayer(media, runner=fake, rng=random.Random(0))
    for _ in range(30):
        player.play(1, "rejected")
        player.wait_idle()
    names = [name for _, name in fake.played]
    assert all(a != b for a, b in zip(names, names[1:]))
    assert set(names) == {"rejected.wav", "rejected_2.wav", "rejected_3.wav"}


def test_fila_cheia_descarta_o_som_mais_antigo_que_nao_comecou(tmp_path):
    fake = FakeAplay(duration_s=0.1)
    media = _media(tmp_path, "a.wav", "b.wav", "c.wav", "d.wav")
    player = SoundPlayer(media, runner=fake)
    for name in ("a", "b", "c", "d"):
        player.play(1, name)
    player.wait_idle()
    played = [name for _, name in fake.played]
    assert played[-1] == "d.wav"  # o mais novo sempre toca
    assert len(played) <= 3 and fake.max_running == 1


def test_erro_do_aplay_nao_derruba_o_player(tmp_path):
    calls = []

    def broken(command, **kwargs):
        calls.append(command)
        raise OSError("sem aplay")

    player = SoundPlayer(_media(tmp_path, "confirmed.wav"), runner=broken)
    player.play(1, "confirmed")
    player.play(1, "confirmed")
    player.wait_idle()
    assert len(calls) == 2


def test_blocked_sem_wav_toca_rejected_no_lugar(tmp_path, caplog):
    for name in ("rejected.wav", "rejected_2.wav"):
        (tmp_path / name).write_bytes(b"RIFF")
    calls = []
    player = SoundPlayer(tmp_path, runner=lambda command, **kwargs: calls.append(command[-1]))
    player.play(1, "blocked")
    player.wait_idle()
    assert calls and calls[0].rsplit("/", 1)[-1].startswith("rejected")
    assert "tocando 'rejected' no lugar" in caplog.text


def test_blocked_com_wav_toca_o_proprio(tmp_path):
    for name in ("rejected.wav", "blocked.wav"):
        (tmp_path / name).write_bytes(b"RIFF")
    calls = []
    player = SoundPlayer(tmp_path, runner=lambda command, **kwargs: calls.append(command[-1]))
    player.play(1, "blocked")
    player.wait_idle()
    assert calls[0].endswith("blocked.wav")
