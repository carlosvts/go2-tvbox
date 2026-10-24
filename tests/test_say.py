import sense.say as say
from sense.audio import SoundPlayer


class FakeAplay:
    def __init__(self):
        self.played = []

    def __call__(self, command, **kwargs):
        self.played.append((command[2], command[-1].rsplit("/", 1)[-1]))


def _player(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"RIFF")
    fake = FakeAplay()
    return SoundPlayer(tmp_path, runner=fake), fake


def test_toca_cada_som_pedido_no_card_informado(tmp_path):
    player, fake = _player(tmp_path, "processing.wav", "confirmed.wav")
    assert say.main(["processing", "confirmed", "--card", "1", "--gap-s", "0"], player=player) == 0
    assert fake.played == [("plughw:1,0", "processing.wav"), ("plughw:1,0", "confirmed.wav")]


def test_repeat_toca_todas_as_vezes_sem_a_fila_descartar(tmp_path):
    player, fake = _player(tmp_path, "confirmed.wav", "confirmed_2.wav")
    say.main(["confirmed", "--repeat", "6", "--card", "2", "--gap-s", "0"], player=player)
    assert len(fake.played) == 6


def test_all_toca_beep_e_todas_as_falas(tmp_path):
    player, fake = _player(tmp_path, *(f"{n}.wav" for n in ("beep", *say.SPOKEN)))
    say.main(["--all", "--card", "1", "--gap-s", "0"], player=player)
    assert [name for _, name in fake.played] == ["beep.wav", *(f"{n}.wav" for n in say.SPOKEN)]


def test_sem_card_no_nome_do_mic_avisa_e_sai_com_erro(tmp_path, monkeypatch, capsys):
    player, fake = _player(tmp_path, "confirmed.wav")
    monkeypatch.setattr(say, "find_card", lambda mic: None)
    assert say.main(["confirmed", "--gap-s", "0"], player=player) == 1
    assert "--card" in capsys.readouterr().err
    assert fake.played == []
