"""Modo thin: máquina de estados, envio ao servidor falso e parada manual."""

import io
import json
import subprocess
import sys
import threading
import time
import wave
from collections import Counter

import numpy as np
import pytest

from sense import stop as stop_module
from sense.config import ConfigError, load_config
from sense.fallback_client import FallbackClient, Outcome, Reply
from sense.thin import ThinMachine
from tests.test_dispatcher import api  # noqa: F401  (fixture: a go2-api falsa)
from tests.test_fallback_client import OK, server, wait_for  # noqa: F401  (fixtures)

ENV = {"GO2_API_URL": "http://api:8000", "SERVER_URL": "http://server:9000", "SENSE_MODE": "thin"}
CONFIG = load_config(env=ENV)
CHUNK_S = 0.03


def chunk(amplitude, tag=0):
    """30 ms de tom. `tag` vai na primeira amostra, para achar o chunk no recorte."""
    t = np.arange(480) / 16000
    samples = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    samples[0] = tag
    return samples.tobytes()


QUIET, LOUD = chunk(30), chunk(3000)


class FakeWake:
    def __init__(self):
        self.next_score = 0.0

    def score(self, chunk):
        return self.next_score


class FakeSender:
    """Guarda o que seria enviado; o teste entrega a resposta quando quiser."""

    def __init__(self):
        self.utterances, self.callbacks = [], []

    def submit(self, utterance, on_reply):
        self.utterances.append(utterance)
        self.callbacks.append(on_reply)
        return True

    def reply(self, outcome=Outcome.CONFIRMED, status="ok", transcript="senta", error=None):
        utterance = self.utterances[-1]
        self.callbacks[-1](Reply(outcome, utterance.utterance_id, status, transcript, error))


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def make(config=CONFIG):
    wake, sender, counters, sounds, clock = FakeWake(), FakeSender(), Counter(), [], Clock()
    machine = ThinMachine(wake, sender, config, counters, sounds.append, clock)
    return machine, wake, sender, counters, sounds, clock


def feed(machine, clock, chunks):
    for one in chunks:
        machine.feed(one)
        clock.now += CHUNK_S


def say_wake_word(machine, wake, clock):
    wake.next_score = 0.99
    feed(machine, clock, [QUIET])
    wake.next_score = 0.0


def speak(machine, wake, clock, seconds=1.2):
    """Wake word, espera o beep passar, fala e silêncio até fechar."""
    feed(machine, clock, [QUIET] * 20)  # áudio de antes, para o pré-roll existir
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [QUIET] * 22 + [LOUD] * round(seconds / CHUNK_S) + [QUIET] * 30)


# ─── Máquina de estados ──────────────────────────────────────────────────────


def test_ocioso_nao_envia_nada():
    machine, _, sender, counters, sounds, clock = make()
    feed(machine, clock, [LOUD] * 200)  # barulho, mas sem wake word
    assert sender.utterances == [] and sounds == [] and not counters


def test_wake_word_fala_envio_e_resposta_voltam_ao_ocioso(caplog):
    caplog.set_level("INFO")
    machine, wake, sender, counters, sounds, clock = make()
    feed(machine, clock, [QUIET] * 100)
    speak(machine, wake, clock)

    assert sounds == ["beep", "processing"]  # retorno na wake word e ao enviar
    (utterance,) = sender.utterances
    reason = (utterance.reason, utterance.hypothesis, utterance.confidence)
    assert reason == ("wake_word", None, None)
    assert len(utterance.utterance_id) == 36

    clock.now += 0.8  # o servidor leva 0,8 s
    sender.reply(status="ok", transcript="senta")
    feed(machine, clock, [QUIET])
    assert sounds[-1] == "confirmed"

    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Interação:"))
    interaction = json.loads(line.removeprefix("Interação: "))
    assert interaction["result"] == "ok" and interaction["transcript"] == "senta"
    assert interaction["wake_at"] < interaction["speech_end_at"] <= interaction["sent_at"]
    assert 0.8 <= interaction["reply_latency_s"] < 1.1
    assert counters["wake_detections"] == 1

    speak(machine, wake, clock)  # de volta ao ocioso: aceita outra
    assert len(sender.utterances) == 2


def test_audio_enviado_comeca_antes_da_deteccao_e_vai_ate_o_fim_da_fala():
    machine, wake, sender, _, _, clock = make()
    tagged = [chunk(30, tag=1000 + i) for i in range(100)]
    feed(machine, clock, tagged)  # o último chunk antes da detecção tem a marca 1099
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [QUIET] * 22 + [LOUD] * 40 + [QUIET] * 30)

    samples = np.frombuffer(sender.utterances[0].audio, dtype=np.int16)
    # 0,3 s (10 chunks) de pré-roll antes do instante da detecção: o chunk
    # em que ela aconteceu e os 9 anteriores.
    assert samples[0] == 1091
    # pré-roll + 22 de silêncio + 40 de fala + 0,3 s de silêncio final.
    assert len(samples) == (10 + 22 + 40 + 10) * 480
    assert np.abs(samples[-11 * 480 : -10 * 480]).max() > 2000  # o fim da fala está lá
    assert np.abs(samples[-10 * 480 :]).max() < 100  # e depois dela só a folga


def test_sem_fala_depois_da_wake_word_descarta_e_volta_ao_ocioso(caplog):
    caplog.set_level("INFO")
    machine, wake, sender, counters, sounds, clock = make()
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [QUIET] * 200)  # 6 s: passa do timeout de 5 s
    assert sender.utterances == []
    assert sounds == ["beep", "rejected"]
    assert counters["wake_without_speech"] == 1
    assert '"result": "no_speech"' in caplog.text

    speak(machine, wake, clock)
    assert len(sender.utterances) == 1


def test_fala_longa_e_cortada_na_duracao_maxima(caplog):
    caplog.set_level("INFO")
    machine, wake, sender, _, _, clock = make()
    feed(machine, clock, [QUIET] * 20)
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [LOUD] * 400)  # 12 s sem parar
    (utterance,) = sender.utterances
    assert len(utterance.audio) / 32000 == pytest.approx(8.0 + 0.3, abs=0.05)
    assert "cortado na duração máxima" in caplog.text


def test_wake_word_durante_o_envio_e_ignorada(caplog):
    caplog.set_level("INFO")
    machine, wake, sender, counters, sounds, clock = make()
    speak(machine, wake, clock)
    feed(machine, clock, [QUIET] * 70)  # passa o cooldown; o servidor ainda não respondeu

    say_wake_word(machine, wake, clock)
    feed(machine, clock, [LOUD] * 40 + [QUIET] * 40)
    assert len(sender.utterances) == 1  # nada de segundo envio
    assert counters["wake_ignored_sending"] == 1
    assert "Wake word ignorada" in caplog.text
    assert sounds.count("beep") == 1

    sender.reply()
    feed(machine, clock, [QUIET] * 70)
    speak(machine, wake, clock)
    assert len(sender.utterances) == 2


def test_cooldown_segura_disparos_repetidos():
    machine, wake, sender, counters, _, clock = make()
    wake.next_score = 0.99  # o score fica alto por vários chunks seguidos
    feed(machine, clock, [QUIET] * 30)
    wake.next_score = 0.0
    assert counters["wake_detections"] == 1

    # Sem fala: volta ao ocioso; uma wake word ainda dentro do cooldown não conta.
    config = load_config(env={**ENV, "WAKE_COOLDOWN_S": "10", "VAD_SPEECH_TIMEOUT_S": "1"})
    machine, wake, sender, counters, _, clock = make(config)
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [QUIET] * 60)  # timeout sem fala em ~1 s
    say_wake_word(machine, wake, clock)
    assert counters["wake_detections"] == 1
    feed(machine, clock, [QUIET] * 300)  # passa dos 10 s
    say_wake_word(machine, wake, clock)
    assert counters["wake_detections"] == 2


def test_limiar_da_wake_word_e_som_sao_configuraveis():
    config = load_config(env={**ENV, "WAKE_THRESHOLD": "0.5", "WAKE_SOUND": "0"})
    machine, wake, _, counters, sounds, clock = make(config)
    wake.next_score = 0.6
    feed(machine, clock, [QUIET])
    assert counters["wake_detections"] == 1 and sounds == []


@pytest.mark.parametrize(
    ("outcome", "status", "sound", "result"),
    [
        (Outcome.CONFIRMED, "stopped", "confirmed", "stopped"),
        (Outcome.REJECTED, "rejected", "rejected", "rejected"),
        (Outcome.UNCONFIRMED, None, "unconfirmed", "unconfirmed"),
    ],
)
def test_resposta_vira_so_som_e_log(outcome, status, sound, result):
    machine, wake, sender, _, sounds, clock = make()
    speak(machine, wake, clock)
    sender.reply(outcome, status, transcript=None, error=None if status else "timed out")
    feed(machine, clock, [QUIET])
    assert sounds[-1] == sound
    assert machine.metrics.results == {result: 1}


def test_envio_sem_resposta_nao_prende_a_maquina():
    machine, wake, sender, _, sounds, clock = make()
    speak(machine, wake, clock)
    feed(machine, clock, [QUIET] * 300)  # 9 s: passa do timeout de 5 s + folga
    assert sounds[-1] == "unconfirmed"
    sender.reply()  # resposta atrasada de um envio já abandonado: ignorada
    feed(machine, clock, [QUIET])
    assert machine.metrics.results == {"unconfirmed": 1}
    speak(machine, wake, clock)
    assert len(sender.utterances) == 2


def test_resumo_conta_status_latencia_e_wake_words_sem_fala():
    machine, wake, sender, _, _, clock = make()
    for latency, status in ((0.5, "ok"), (1.5, "ok"), (1.0, "rejected")):
        speak(machine, wake, clock)
        clock.now += latency
        outcome = Outcome.CONFIRMED if status == "ok" else Outcome.REJECTED
        sender.reply(outcome, status)
        feed(machine, clock, [QUIET] * 70)
    say_wake_word(machine, wake, clock)
    feed(machine, clock, [QUIET] * 200)

    summary = machine.metrics.summary()
    assert "wake words=4" in summary
    assert "sem fala depois (possível falso disparo)=1" in summary
    assert "no_speech=1 ok=2 rejected=1" in summary
    # Cada latência soma ~0,2 s: o tempo entre o fim da fala e a resposta ser lida.
    assert "média 1.21s, p95 1.71s (3 respostas)" in summary


def test_flag_salva_os_wavs_enviados(tmp_path):
    config = load_config(env={**ENV, "SAVE_UTTERANCES_DIR": str(tmp_path / "wavs")})
    machine, wake, sender, _, _, clock = make(config)
    speak(machine, wake, clock)
    (saved,) = (tmp_path / "wavs").glob("*.wav")
    assert sender.utterances[0].utterance_id in saved.name
    with wave.open(str(saved)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
        assert wav.readframes(wav.getnframes()) == sender.utterances[0].audio

    machine, wake, sender, _, _, clock = make()  # sem a flag, nada é salvo
    speak(machine, wake, clock)
    assert len(list((tmp_path / "wavs").glob("*.wav"))) == 1


# ─── Envio ao servidor (cliente de verdade, servidor falso) ──────────────────


def thin_with_server(server, timeout_s=2.0, port=None):  # noqa: F811
    counters, sounds, clock = Counter(), [], Clock()
    url = f"http://127.0.0.1:{port or server.server_port}"
    client = FallbackClient(url, "tvbox-teste", timeout_s, 0.3, OK | {"stopped"}, counters)
    wake = FakeWake()
    machine = ThinMachine(wake, client, CONFIG, counters, sounds.append, clock)
    return machine, wake, sounds, clock


def wait_reply(machine, clock, sounds):
    def replied():
        feed(machine, clock, [QUIET])
        return sounds[-1] != "processing"

    wait_for(replied)


def test_servidor_recebe_wav_e_meta_do_thin(server):  # noqa: F811
    machine, wake, sounds, clock = thin_with_server(server)
    speak(machine, wake, clock)
    wait_reply(machine, clock, sounds)

    ((audio, meta),) = server.utterances
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
        assert wav.getnframes() / 16000 == pytest.approx(0.3 + 22 * 0.03 + 1.2 + 0.3, abs=0.05)
    assert len(meta.pop("utterance_id")) == 36
    assert meta == {
        "edge_id": "tvbox-teste",
        "reason": "wake_word",
        "local_hypothesis": None,
        "local_confidence": None,
    }
    assert sounds == ["beep", "processing", "confirmed"]
    assert server.others == []  # o thin não manda cancel nem executa nada


def test_status_rejected_do_servidor_e_nao_entendi(server):  # noqa: F811
    server.answer = (200, {"status": "rejected", "transcript": "blá"})
    machine, wake, sounds, clock = thin_with_server(server)
    speak(machine, wake, clock)
    wait_reply(machine, clock, sounds)
    assert sounds[-1] == "rejected"
    assert machine.metrics.results == {"rejected": 1}


def test_timeout_do_servidor_e_nao_confirmado(server):  # noqa: F811
    server.release.clear()  # recebe e não responde
    machine, wake, sounds, clock = thin_with_server(server, timeout_s=0.3)
    speak(machine, wake, clock)
    wait_reply(machine, clock, sounds)
    assert sounds[-1] == "unconfirmed"
    assert len(server.utterances) == 1  # chegou lá: pode ter sido executado


def test_erro_de_rede_e_nao_confirmado(server):  # noqa: F811
    machine, wake, sounds, clock = thin_with_server(server, port=1)
    speak(machine, wake, clock)
    wait_reply(machine, clock, sounds)
    assert sounds[-1] == "unconfirmed"
    assert machine.metrics.results == {"unconfirmed": 1}


def test_health_do_servidor(server):  # noqa: F811
    client = FallbackClient(f"http://127.0.0.1:{server.server_port}", "x", 1.0, 0.3, OK, Counter())
    assert client.healthy() is True
    assert FallbackClient("http://127.0.0.1:1", "x", 1.0, 0.3, OK, Counter()).healthy() is False


# ─── Seleção de modo ─────────────────────────────────────────────────────────


def test_modo_padrao_e_edge_e_thin_exige_servidor():
    assert load_config(env={"GO2_API_URL": "http://api:8000"}).mode == "edge"
    assert CONFIG.mode == "thin"
    with pytest.raises(ConfigError, match="SERVER_URL não definido .obrigatório no modo thin"):
        load_config(env={"GO2_API_URL": "http://api:8000", "SENSE_MODE": "thin"})
    with pytest.raises(ConfigError, match="SENSE_MODE='cloud' inválido"):
        load_config(env={"GO2_API_URL": "http://api:8000", "SENSE_MODE": "cloud"})


def test_thin_nao_importa_o_vosk_nem_o_reconhecedor():
    code = (
        "import sys, sense.thin, sense.wake, sense.stop\n"
        "loaded = [m for m in ('vosk', 'sense.recognizer', 'sense.edge') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ─── Parada manual ───────────────────────────────────────────────────────────


def stop_config(api, server_port):  # noqa: F811
    return load_config(
        env={
            "GO2_API_URL": f"http://127.0.0.1:{api.server_port}",
            "SERVER_URL": f"http://127.0.0.1:{server_port}",
            "CANCEL_TIMEOUT_S": "0.3",
        }
    )


def test_parada_manual_chama_a_go2_api_e_avisa_o_servidor(api, server):  # noqa: F811
    assert stop_module.stop(stop_config(api, server.server_port)) is True
    assert api.requests == [("/commands/stop", None)]
    wait_for(lambda: server.others == [("/v1/cancel", {"reason": "stop"})])


def test_parada_manual_nao_e_segurada_pelo_cancel(api, server):  # noqa: F811
    server.cancel_delay_s = 2.0  # o servidor demora mais que o timeout do cancel
    done = threading.Event()
    api_answered_at = []
    original = api.RequestHandlerClass.do_POST

    def do_post(handler):
        original(handler)
        api_answered_at.append(time.monotonic())
        done.set()

    api.RequestHandlerClass.do_POST = do_post
    try:
        start = time.monotonic()
        assert stop_module.stop(stop_config(api, server.server_port)) is True
        assert api_answered_at[0] - start < 0.25  # a parada não esperou o servidor
        assert time.monotonic() - start < 1.0  # e o comando não fica preso no cancel
    finally:
        api.RequestHandlerClass.do_POST = original

    # Servidor fora do ar, ou sem servidor configurado: a parada sai igual.
    assert stop_module.stop(stop_config(api, 1)) is True
    assert stop_module.stop(load_config(env={"GO2_API_URL": f"http://127.0.0.1:{api.server_port}"}))
    assert len(api.requests) == 3


def test_parada_manual_devolve_falso_se_a_api_recusar(api, server):  # noqa: F811
    api.status = 503
    assert stop_module.stop(stop_config(api, server.server_port)) is False
