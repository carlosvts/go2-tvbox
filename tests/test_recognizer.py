from collections import Counter

from sense.commands import load_commands
from sense.config import load_config
from sense.recognizer import Recognizer, Transcript

CHUNK = bytes(960)  # 30 ms de silêncio a 16 kHz
CONFIG = load_config(env={"SENSE_MODE": "edge", "GO2_API_URL": "http://api:8000"})


class FakeWake:
    def __init__(self):
        self.next_score = 0.0
        self.resets = 0

    def score(self, chunk):
        return self.next_score

    def reset(self):
        self.resets += 1


class FakeStt:
    """Devolve `answer` no chunk de número `after`; antes disso, None."""

    def __init__(self, answer=None, after=10):
        self.answer = answer
        self.after = after
        self.fed = 0

    def accept(self, chunk):
        self.fed += 1
        return self.answer if self.fed == self.after else None

    def finish(self):
        return Transcript("", 0.0)

    def reset(self):
        self.fed = 0


def make(stt):
    wake, counters, beeps = FakeWake(), Counter(), []
    recognizer = Recognizer(
        wake, stt, load_commands(), CONFIG, counters, on_wake=lambda: beeps.append(1)
    )
    return recognizer, wake, counters, beeps


def say_wake_word(recognizer, wake):
    wake.next_score = 0.99
    assert recognizer.feed(CHUNK) is None
    wake.next_score = 0.0


def feed_until_result(recognizer, seconds):
    """Alimenta `seconds` de áudio; devolve os comandos que saíram."""
    results = [recognizer.feed(CHUNK) for _ in range(int(seconds / 0.03))]
    return [result for result in results if result is not None]


def test_sem_wake_word_nao_escuta_nada():
    stt = FakeStt(Transcript("desligar motores", 0.99))
    recognizer, _, counters, beeps = make(stt)
    assert feed_until_result(recognizer, 5) == []
    assert stt.fed == 0 and beeps == [] and not counters


def test_wake_word_seguida_de_frase_devolve_o_comando():
    stt = FakeStt(Transcript("desligar motores", 0.95))
    recognizer, wake, counters, beeps = make(stt)
    say_wake_word(recognizer, wake)
    assert beeps == [1]
    (command,) = feed_until_result(recognizer, 3)
    assert command.name == "damp"
    assert counters == {"wake_detections": 1, "commands_recognized": 1}


def test_audio_logo_apos_a_wake_word_e_descartado():
    stt = FakeStt()
    recognizer, wake, _, _ = make(stt)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.6)
    assert stt.fed == 0  # ainda dentro dos 0,9 s de descarte


def test_frase_fora_do_mapa_e_evento_no_match(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters, _ = make(FakeStt(Transcript("oi", 0.99)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["no_match"] == 1
    assert "'oi' não casa com nenhuma frase" in caplog.text


def test_confianca_baixa_e_evento_low_confidence(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters, _ = make(FakeStt(Transcript("desligar motores", 0.4)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["low_confidence"] == 1
    assert "confiança 0.40 está abaixo de 0.70" in caplog.text


def test_silencio_ate_o_timeout_e_evento_listen_timeout():
    recognizer, wake, counters, _ = make(FakeStt(answer=None))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 6) == []
    assert counters["listen_timeouts"] == 1


def test_wake_word_e_ignorada_logo_apos_uma_escuta():
    recognizer, wake, counters, _ = make(FakeStt(Transcript("oi", 0.99)))
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 3)  # volta ao passivo após ~1,2 s; rearme já passou
    wake.next_score = 0.99
    recognizer.feed(CHUNK)
    assert counters["wake_detections"] == 2

    feed_until_result(recognizer, 1.25)  # termina a escuta; rearme de 1,5 s começa
    recognizer.feed(CHUNK)
    assert counters["wake_detections"] == 2  # ignorada durante o rearme
    assert wake.resets == 2
