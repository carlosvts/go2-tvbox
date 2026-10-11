"""Cliente do servidor de inferência: o fallback de quando a gramática não basta.

A TV Box só entrega o áudio da frase e avisa de paradas e comandos locais. Quem
interpreta, enfileira e chama a go2-api é o servidor: nada do que ele responde
é executado aqui, a resposta só vira feedback para a pessoa.

    POST /v1/utterance   multipart: `audio` (WAV mono 16 kHz PCM16) + `meta` (JSON)
    POST /v1/cancel      JSON: {"reason": "stop" | "local_command"}
    GET  /health         só para saber, na partida, se o servidor está no ar

Uma tentativa por chamada, sem retry e sem fila: no máximo uma frase em voo, e
as que chegarem nesse meio tempo são recusadas por `submit`.
"""

import io
import json
import logging
import threading
import urllib.error
import urllib.request
import uuid
import wave
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from sense.config import SAMPLE_RATE

log = logging.getLogger(__name__)


class Outcome(Enum):
    CONFIRMED = "confirmado"  # o servidor aceitou a frase
    REJECTED = "não entendi"  # o servidor respondeu, mas não aceitou
    UNCONFIRMED = "não confirmado"  # timeout, rede ou resposta ilegível


# Som de retorno (`media/<nome>.wav`) de cada resultado.
OUTCOME_SOUNDS = {
    Outcome.CONFIRMED: "confirmed",
    Outcome.REJECTED: "rejected",
    Outcome.UNCONFIRMED: "unconfirmed",
}


@dataclass(frozen=True)
class Utterance:
    audio: bytes  # PCM 16 kHz mono s16le
    reason: str  # "unk", "low_conf", "too_long" (edge) ou "wake_word" (thin)
    hypothesis: str | None  # o que a gramática local ouviu; None no thin
    confidence: float | None
    utterance_id: str | None = None  # None = o cliente cria um


@dataclass(frozen=True)
class Reply:
    """O que voltou de um envio. Só serve para retorno e log, nunca para executar."""

    outcome: Outcome
    utterance_id: str
    status: str | None = None  # o `status` do servidor, se ele respondeu
    transcript: str | None = None
    error: str | None = None  # por que não houve confirmação


def wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def multipart(audio_wav: bytes, meta: dict) -> tuple[bytes, str]:
    """Corpo multipart/form-data e o Content-Type com o boundary."""
    boundary = uuid.uuid4().hex
    parts = (
        ('name="audio"; filename="utterance.wav"', "audio/wav", audio_wav),
        ('name="meta"', "application/json", json.dumps(meta, ensure_ascii=False).encode()),
    )
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\n"
        f"Content-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n"
        for disposition, content_type, content in parts
    )
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class FallbackClient:
    def __init__(
        self,
        server_url: str,
        edge_id: str,
        timeout_s: float,
        cancel_timeout_s: float,
        ok_statuses: frozenset[str],
        counters: Counter,
        on_outcome: Callable[[Outcome], None] = lambda outcome: None,
    ) -> None:
        self._server_url = server_url
        self._edge_id = edge_id
        self._timeout_s = timeout_s
        self._cancel_timeout_s = cancel_timeout_s
        self._ok_statuses = ok_statuses
        self._counters = counters
        self._on_outcome = on_outcome
        self._in_flight = threading.Lock()

    def submit(
        self, utterance: Utterance, on_reply: Callable[[Reply], None] | None = None
    ) -> bool:
        """Envia a frase em segundo plano. False se já há uma em voo (descartada).

        O resultado vai para `on_reply`, se dado; senão, para o `on_outcome`
        do construtor.
        """
        if not self._in_flight.acquire(blocking=False):
            log.info("Fallback descartado (%s): já há uma frase em voo.", utterance.reason)
            self._counters["fallback_busy_discards"] += 1
            return False

        def run() -> None:
            try:
                reply = self.send(utterance)
            finally:
                self._in_flight.release()
            if on_reply is not None:
                on_reply(reply)
            else:
                self._on_outcome(reply.outcome)

        threading.Thread(target=run, name="fallback", daemon=True).start()
        return True

    def send_utterance(self, utterance: Utterance) -> Outcome:
        """Faz o POST e espera a resposta; devolve só o resultado."""
        return self.send(utterance).outcome

    def send(self, utterance: Utterance) -> Reply:
        """Faz o POST e espera a resposta. Só lê `status` e `transcript`."""
        utterance_id = utterance.utterance_id or str(uuid.uuid4())
        meta = {
            "utterance_id": utterance_id,
            "edge_id": self._edge_id,
            "reason": utterance.reason,
            "local_hypothesis": utterance.hypothesis,
            "local_confidence": utterance.confidence,
        }
        body, content_type = multipart(wav_bytes(utterance.audio), meta)
        request = urllib.request.Request(
            self._server_url + "/v1/utterance",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        self._counters["fallback_sent"] += 1
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                answer = json.load(response)
            status = str(answer["status"]).lower()
            transcript = answer.get("transcript")
        except urllib.error.HTTPError as error:
            # O servidor respondeu com erro; não dá para saber o que ele fez.
            log.warning("Fallback %s não confirmado: HTTP %d.", utterance_id, error.code)
            self._counters["fallback_unconfirmed"] += 1
            return Reply(Outcome.UNCONFIRMED, utterance_id, error=f"HTTP {error.code}")
        except (OSError, ValueError, KeyError, AttributeError) as error:
            # Timeout, rede ou resposta ilegível. O servidor pode ter recebido e
            # executado a frase mesmo assim.
            log.warning("Fallback %s não confirmado: %s.", utterance_id, error)
            self._counters["fallback_unconfirmed"] += 1
            return Reply(Outcome.UNCONFIRMED, utterance_id, error=str(error) or repr(error))

        if status in self._ok_statuses:
            log.info(
                "Fallback %s confirmado: status=%s transcript=%r.", utterance_id, status, transcript
            )
            self._counters["fallback_confirmed"] += 1
            return Reply(Outcome.CONFIRMED, utterance_id, status, transcript)
        log.info("Fallback %s recusado: status=%s transcript=%r.", utterance_id, status, transcript)
        self._counters["fallback_rejected"] += 1
        return Reply(Outcome.REJECTED, utterance_id, status, transcript)

    def healthy(self) -> bool:
        """O servidor responde ao `GET /health`? Só informativo."""
        try:
            with urllib.request.urlopen(self._server_url + "/health", timeout=self._timeout_s):
                return True
        except OSError as error:
            log.warning("Servidor de inferência não respondeu ao /health: %s.", error)
            return False

    def cancel(self, reason: str) -> None:
        """Avisa o servidor, sem esperar: uma falha aqui só vira log."""
        threading.Thread(
            target=self.send_cancel, args=(reason,), name="cancel", daemon=True
        ).start()

    def send_cancel(self, reason: str) -> bool:
        request = urllib.request.Request(
            self._server_url + "/v1/cancel",
            data=json.dumps({"reason": reason}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._cancel_timeout_s):
                pass
        except OSError as error:  # inclui HTTPError e timeout
            log.warning("Cancel (%s) não chegou ao servidor: %s.", reason, error)
            self._counters["cancel_failures"] += 1
            return False
        self._counters["cancels_sent"] += 1
        return True
