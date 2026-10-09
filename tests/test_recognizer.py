from collections import Counter

import pytest

from sense.commands import load_commands
from sense.config import load_config
from sense.recognizer import Kind, Recognizer, Transcript

CHUNK = bytes(960)  # 30 ms de silêncio a 16 kHz
ENV = {"GO2_API_URL": "http://api:8000"}
CONFIG = load_config(env=ENV)  # sem servidor: só a gramática
WITH_SERVER = load_config(env={**ENV, "SERVER_URL": "http://server:9000"})


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

    def __init__(self, answer=None, after=10, speaking="", final=Transcript("", 0.0)):
        self.answer = answer
        self.after = after
        self.speaking = speaking  # parcial: não vazio = há fala em curso
        self.final = final
        self.fed = 0

    def accept(self, chunk):
        self.fed += 1
        return self.answer if self.fed == self.after else None

    def partial(self):
        return self.speaking

    def finish(self):
        return self.final

    def reset(self):
        self.fed = 0


def make(stt, config=CONFIG):
    wake, counters, beeps = FakeWake(), Counter(), []
    recognizer = Recognizer(
        wake, stt, load_commands(), config, counters, on_wake=lambda: beeps.append(1)
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
    (decision,) = feed_until_result(recognizer, 3)
    assert (decision.kind, decision.command.name) == (Kind.LOCAL, "damp")
    assert counters == {"wake_detections": 1, "commands_recognized": 1}


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
    assert "Sem comando: 'faz um mortal' não casa com nenhuma frase" in caplog.text


def test_confianca_baixa_e_evento_low_confidence(caplog):
    caplog.set_level("INFO")
    recognizer, wake, counters, _ = make(FakeStt(Transcript("desligar motores", 0.4)))
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 3) == []
    assert counters["low_confidence"] == 1
    assert "casaria com damp, mas a confiança é baixa (conf=0.40)" in caplog.text


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


def decide(text, confidence=0.95, config=WITH_SERVER, start_s=0.0, end_s=0.0):
    """A decisão para uma transcrição, com wake word e escuta simuladas."""
    stt = FakeStt(Transcript(text, confidence, start_s, end_s))
    recognizer, wake, counters, _ = make(stt, config)
    say_wake_word(recognizer, wake)
    decisions = feed_until_result(recognizer, 3)
    return (decisions[0] if decisions else None), counters


@pytest.mark.parametrize("text", ["para", "pare", "parar", "stop"])
def test_palavra_de_parada_e_parada(text):
    decision, counters = decide(text)
    assert (decision.kind, decision.command.name) == (Kind.STOP, "stop")
    assert counters["stops_recognized"] == 1


@pytest.mark.parametrize(
    ("text", "confidence"),
    [("para", 0.2), ("[unk] para", 0.9), ("andar para [unk]", 0.9), ("para frente", 0.9)],
)
def test_failsafe_parada_vence_o_fallback(text, confidence):
    decision, _ = decide(text, confidence)
    assert decision.kind is Kind.STOP


def test_sem_failsafe_parada_duvidosa_vai_para_o_fallback():
    config = load_config(env={**ENV, "SERVER_URL": "http://server:9000", "STOP_FAILSAFE": "0"})
    assert decide("para", 0.2, config)[0].kind is Kind.FALLBACK
    assert decide("[unk] para", 0.9, config)[0].kind is Kind.FALLBACK
    assert decide("para", 0.9, config)[0].kind is Kind.STOP  # parada clara continua local


@pytest.mark.parametrize("confidence", [0.95, 0.3])
def test_o_para_de_uma_frase_de_movimento_nao_e_parada(confidence):
    decision, _ = decide("andar para frente", confidence)
    assert decision.kind is not Kind.STOP
    assert decision.kind is (Kind.LOCAL if confidence > 0.7 else Kind.FALLBACK)


@pytest.mark.parametrize(
    ("text", "confidence", "end_s", "reason"),
    [
        ("levanta [unk]", 0.95, 1.0, "unk"),
        ("[unk]", 0.95, 1.0, "unk"),
        ("frente", 0.95, 1.0, "unk"),  # palavras da gramática fora de uma frase
        ("levanta", 0.5, 1.0, "low_conf"),
        ("[unk] levanta", 0.5, 1.0, "unk"),  # [unk] vem antes da confiança
        ("levanta", 0.95, 3.5, "too_long"),
    ],
)
def test_fallback_e_o_motivo(text, confidence, end_s, reason):
    decision, counters = decide(text, confidence, end_s=end_s)
    assert (decision.kind, decision.reason) == (Kind.FALLBACK, reason)
    assert (decision.hypothesis, decision.confidence, decision.command) == (text, confidence, None)
    assert not counters["commands_recognized"]


def test_sem_servidor_o_que_iria_ao_fallback_e_descartado_e_fala_longa_executa():
    assert decide("levanta [unk]", config=CONFIG) == (None, {"wake_detections": 1, "no_match": 1})
    decision, _ = decide("levanta", end_s=3.5, config=CONFIG)
    assert decision.kind is Kind.LOCAL


LOUD = (1000).to_bytes(2, "little") * 480  # 30 ms de "voz"


def test_fallback_espera_a_fala_acabar_e_manda_a_escuta_inteira_com_folga():
    # O Vosk fecha no chunk 10 (0,3 s), mas a pessoa fala até o chunk 50 (1,5 s).
    stt = FakeStt(Transcript("frente", 0.9))
    recognizer, wake, _, _ = make(stt, WITH_SERVER)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.9)  # descarte, em silêncio
    decisions = [recognizer.feed(LOUD) for _ in range(50)]
    assert decisions == [None] * 50  # decidiu fallback, mas a fala continua
    decisions = [recognizer.feed(CHUNK) for _ in range(30)]
    (decision,) = [d for d in decisions if d is not None]
    assert decisions.index(decision) == 19  # 0,6 s de silêncio fecham a fala
    # 0,3 s antes da escuta + 1,5 s de fala + 0,3 s do silêncio final.
    assert len(decision.audio) == round(2.1 * 32000)
    assert decision.audio[-round(0.3 * 32000) - 960 :][:960] == LOUD


def test_fallback_com_a_fala_ja_encerrada_sai_na_hora():
    stt = FakeStt(Transcript("[unk]", 0.9), after=30)  # 0,9 s de silêncio antes de fechar
    recognizer, wake, _, _ = make(stt, WITH_SERVER)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.9)
    decisions = [recognizer.feed(CHUNK) for _ in range(30)]
    assert decisions[-1] is not None and decisions[-1].kind is Kind.FALLBACK


def test_voz_sem_nenhuma_palavra_reconhecida_tambem_vai_ao_fallback():
    recognizer, wake, counters, _ = make(FakeStt(answer=None), WITH_SERVER)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.9)
    decisions = [recognizer.feed(LOUD) for _ in range(40)]  # 1,2 s de fala
    decisions += [recognizer.feed(CHUNK) for _ in range(80)]
    (decision,) = [d for d in decisions if d is not None]
    assert (decision.kind, decision.reason, decision.hypothesis) == (Kind.FALLBACK, "unk", "")
    assert len(decision.audio) == pytest.approx((0.3 + 1.2 + 0.3) * 32000, abs=4)

    # Sem voz (ou sem servidor), continua sendo só um timeout de escuta.
    recognizer, wake, counters, _ = make(FakeStt(answer=None), WITH_SERVER)
    say_wake_word(recognizer, wake)
    assert feed_until_result(recognizer, 6) == []
    assert counters["listen_timeouts"] == 1


def test_parada_e_comando_local_nao_esperam_a_fala_acabar():
    stt = FakeStt(Transcript("para", 0.9))
    recognizer, wake, _, _ = make(stt, WITH_SERVER)
    say_wake_word(recognizer, wake)
    feed_until_result(recognizer, 0.9)
    decisions = [recognizer.feed(LOUD) for _ in range(10)]
    assert decisions[-1].kind is Kind.STOP


def test_fala_em_curso_no_teto_espera_o_vosk_fechar():
    # O resultado só sai no chunk 120 (3,6 s), depois do teto de 2,5 s.
    stt = FakeStt(Transcript("desligar motores", 0.95), after=120, speaking="desligar")
    recognizer, wake, _, _ = make(stt)
    say_wake_word(recognizer, wake)
    (decision,) = feed_until_result(recognizer, 6)
    assert decision.command.name == "damp"


def test_fala_que_nao_fecha_e_cortada_no_teto_maximo():
    stt = FakeStt(speaking="[unk]", final=Transcript("[unk]", 0.9))
    recognizer, wake, counters, _ = make(stt, WITH_SERVER)
    say_wake_word(recognizer, wake)
    (decision,) = feed_until_result(recognizer, 10)
    assert (decision.kind, decision.reason) == (Kind.FALLBACK, "unk")
    assert stt.fed == round(8.0 / 0.03)  # MAX_UTTERANCE_S
