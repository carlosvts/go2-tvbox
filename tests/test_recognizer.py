from collections import Counter

import pytest

from sense.commands import load_commands, load_intents
from sense.config import load_config
from sense.recognizer import Recognizer, Transcript

CHUNK = bytes(960)  # 30 ms de silêncio a 16 kHz
ENV = {"SENSE_MODE": "edge", "GO2_API_URL": "http://api:8000"}
CONFIG = load_config(env={**ENV, "STT_MODE": "gramatica"})
FREE = load_config(env={**ENV, "STT_MODE": "livre"})


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

    def __init__(self, answer=None, after=10, partials=None, final=Transcript("", 0.0)):
        self.answer = answer
        self.after = after
        self.partials = partials or {}  # número do chunk → parcial a partir dele
        self.final = final
        self.fed = 0
        self.last_partial = ""

    def accept(self, chunk):
        self.fed += 1
        self.last_partial = self.partials.get(self.fed, self.last_partial)
        return self.answer if self.fed == self.after else None

    def partial(self):
        return self.partials.get(self.fed, self.last_partial)

    def finish(self):
        return self.final

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
    assert counters == {"wake_detections": 1, "commands_recognized": 1, "listen_closed_by_vosk": 1}


def test_audio_logo_apos_a_wake_word_e_descartado():
    stt = FakeStt()
    recognizer, wake, _, _ = make(stt)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.6)
    assert stt.fed == 0  # ainda dentro dos 0,9 s de descarte


def test_frase_fora_do_mapa_e_evento_no_match(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters, _ = make(FakeStt(Transcript("faz um mortal", 0.99)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["no_match"] == 1
    assert "'faz um mortal' (não casa com nenhuma frase" in caplog.text


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
    recognizer, wake, counters, _ = make(FakeStt(Transcript("faz um mortal", 0.99)))
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 3)  # volta ao passivo após ~1,2 s; rearme já passou
    wake.next_score = 0.99
    recognizer.feed(CHUNK)
    assert counters["wake_detections"] == 2

    feed_until_result(recognizer, 1.25)  # termina a escuta; rearme de 0,5 s começa
    recognizer.feed(CHUNK)
    assert counters["wake_detections"] == 2  # ignorada durante o rearme
    assert wake.resets == 2


def test_telemetria_quando_o_vosk_fecha_a_frase(caplog):
    caplog.set_level("INFO")
    stt = FakeStt(Transcript("desligar motores", 0.95), after=20, partials={5: "desligar"})
    recognizer, wake, counters, _ = make(stt)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 3)
    assert "Escuta aberta" in caplog.text
    assert "voz detectada aos 0.1s: 'desligar'" in caplog.text
    assert "Escuta fechada pelo Vosk em 0.6s" in caplog.text
    assert "Rearme terminou" in caplog.text
    assert counters["listen_closed_by_vosk"] == 1


def test_telemetria_quando_o_teto_corta_a_frase(caplog):
    caplog.set_level("INFO")
    stt = FakeStt(partials={5: "desligar"}, final=Transcript("desligar motores", 0.9))
    recognizer, wake, counters, _ = make(stt)
    say_wake_word(recognizer, wake)
    (command,) = feed_until_result(recognizer, 5)
    assert command.name == "damp"
    assert "Escuta cortada pelo teto de 2.5s" in caplog.text
    assert "parcial='desligar', final='desligar motores'" in caplog.text
    assert counters["listen_cut_by_timeout"] == 1


def test_wake_word_no_rearme_e_logada_uma_vez(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters, _ = make(FakeStt(Transcript("faz um mortal", 0.99)))
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 1.25)
    wake.next_score = 0.99
    recognizer.feed(CHUNK)
    recognizer.feed(CHUNK)
    assert counters["wake_ignored_rearm"] == 1
    assert caplog.text.count("Wake word ignorada") == 1


def make_free(stt):
    """Reconhecedor no modo livre: o interpretador de verdade, STT falso."""
    interpreter = pytest.importorskip("sense.interpreter")
    wake, counters = FakeWake(), Counter()
    recognizer = Recognizer(
        wake, stt, load_commands(), FREE, counters,
        interpreter=interpreter.Interpreter.load(), intents=load_intents(),
    )
    return recognizer, wake, counters


def test_modo_livre_aceita_variacao_da_frase(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters = make_free(FakeStt(Transcript("por favor anda pra frente", 0.9)))
    say_wake_word(recognizer, wake)
    (command,) = feed_until_result(recognizer, 3)
    assert command.body == {"vx": 1.0, "vy": 0.0, "vyaw": 0.0, "duration_s": 3.0}
    assert "interpretador: andar_frente, nota 100" in caplog.text


def test_modo_livre_frase_sem_comando_e_no_match(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters = make_free(FakeStt(Transcript("não anda para frente", 0.9)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["no_match"] == 1
    assert "interpretador: negação" in caplog.text


def test_modo_livre_parada_passa_mesmo_com_confianca_baixa():
    recognizer, wake, _ = make_free(FakeStt(Transcript("para", 0.3)))
    say_wake_word(recognizer, wake)
    (command,) = feed_until_result(recognizer, 3)
    assert command.name == "stop"

    recognizer, wake, counters = make_free(FakeStt(Transcript("senta", 0.3)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["low_confidence"] == 1
