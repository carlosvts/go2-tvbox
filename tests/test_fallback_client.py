"""O cliente do servidor de inferência, contra um servidor falso."""

import io
import wave
from collections import Counter

import pytest

from sense.fallback_client import OUTCOME_SOUNDS, FallbackClient, Outcome, Utterance
from tests.conftest import OK, wait_for

PCM = bytes(32000)  # 1 s de silêncio
UTTERANCE = Utterance(PCM, "wake_word")


def client(server, timeout_s=2.0, port=None):
    counters = Counter()
    url = f"http://127.0.0.1:{port or server.server_port}"
    return FallbackClient(url, "tvbox-teste", timeout_s, 0.3, OK, counters), counters


# ─── POST /v1/utterance ──────────────────────────────────────────────────────


def test_sucesso_envia_wav_e_meta_e_confirma(server):
    fallback, counters = client(server)
    reply = fallback.send(UTTERANCE)
    assert reply.outcome is Outcome.CONFIRMED
    assert (reply.status, reply.transcript) == ("ok", "levanta por favor")

    ((audio, meta),) = server.utterances
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 16000, 2)
        assert wav.readframes(wav.getnframes()) == PCM
    assert len(meta.pop("utterance_id")) == 36  # uuid
    assert meta == {
        "edge_id": "tvbox-teste",
        "reason": "wake_word",
        "local_hypothesis": None,
        "local_confidence": None,
    }
    assert counters == {"fallback_sent": 1, "fallback_confirmed": 1}


def test_utterance_id_dado_e_mantido(server):
    fallback, _ = client(server)
    fallback.send(Utterance(PCM, "wake_word", "id-da-tvbox"))
    assert server.utterances[0][1]["utterance_id"] == "id-da-tvbox"


@pytest.mark.parametrize("status", ["rejected", "not_understood", "qualquer_outro"])
def test_status_de_rejeicao_e_nao_entendi(server, status):
    server.answer = (200, {"status": status, "transcript": ""})
    fallback, counters = client(server)
    assert fallback.send(UTTERANCE).outcome is Outcome.REJECTED
    assert counters["fallback_rejected"] == 1


@pytest.mark.parametrize("reason", ["low_similarity", "ambiguous", "negation", None])
def test_outros_motivos_de_recusa_continuam_nao_entendi(server, reason):
    server.answer = (200, {"status": "rejected", "transcript": "x", "reject_reason": reason})
    fallback, _ = client(server)
    reply = fallback.send(UTTERANCE)
    assert reply.outcome is Outcome.REJECTED
    assert reply.reject_reason == reason


def test_comando_desligado_no_servidor_nao_e_nao_entendi(server, caplog):
    caplog.set_level("INFO")
    server.answer = (
        200,
        {"status": "rejected", "transcript": "Dançar!", "reject_reason": "command_disabled"},
    )
    fallback, counters = client(server)
    reply = fallback.send(UTTERANCE)
    assert reply.outcome is Outcome.BLOCKED
    assert (reply.status, reply.reject_reason) == ("rejected", "command_disabled")
    assert OUTCOME_SOUNDS[reply.outcome] == "blocked"
    assert counters == {"fallback_sent": 1, "fallback_blocked": 1}
    assert "comando desligado no servidor" in caplog.text


def test_timeout_e_nao_confirmado_e_nao_tenta_de_novo(server):
    server.release.clear()  # o servidor recebe e não responde
    fallback, counters = client(server, timeout_s=0.3)
    assert fallback.send(UTTERANCE).outcome is Outcome.UNCONFIRMED
    assert len(server.utterances) == 1  # chegou lá: não dá para presumir que não executou
    assert counters == {"fallback_sent": 1, "fallback_unconfirmed": 1}


def test_erro_de_rede_e_nao_confirmado(server):
    fallback, counters = client(server, port=1)  # porta sem ninguém
    assert fallback.send(UTTERANCE).outcome is Outcome.UNCONFIRMED
    assert counters["fallback_unconfirmed"] == 1


@pytest.mark.parametrize("answer", [(500, {"detail": "erro"}), (200, b"<html>"), (200, {"x": 1})])
def test_erro_http_ou_resposta_ilegivel_e_nao_confirmado(server, answer):
    server.answer = answer
    fallback, _ = client(server)
    assert fallback.send(UTTERANCE).outcome is Outcome.UNCONFIRMED


def test_uma_chamada_em_voo_por_vez(server):
    server.release.clear()
    replies = []
    fallback, counters = client(server)

    assert fallback.submit(UTTERANCE, replies.append) is True
    assert server.arrived.wait(3)
    assert fallback.submit(UTTERANCE, replies.append) is False  # descartada, não enfileirada
    assert counters["fallback_busy_discards"] == 1

    server.release.set()
    wait_for(lambda: [reply.outcome for reply in replies] == [Outcome.CONFIRMED])
    assert len(server.utterances) == 1
    assert fallback.submit(UTTERANCE, replies.append) is True  # livre de novo
    wait_for(lambda: len(replies) == 2)


def test_health_do_servidor(server):
    fallback, _ = client(server)
    assert fallback.healthy() is True
    assert client(server, port=1)[0].healthy() is False


# ─── POST /v1/cancel ─────────────────────────────────────────────────────────


def test_cancel_manda_so_o_motivo(server):
    fallback, counters = client(server)
    assert fallback.send_cancel("stop") is True
    assert server.others == [("/v1/cancel", {"reason": "stop"})]
    assert counters["cancels_sent"] == 1


def test_cancel_que_falha_so_vira_log(server, caplog):
    fallback, counters = client(server, port=1)
    assert fallback.send_cancel("stop") is False
    assert counters["cancel_failures"] == 1
    assert "Cancel (stop) não chegou ao servidor" in caplog.text
