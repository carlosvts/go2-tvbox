"""Modo thin: a TV Box só detecta a wake word e manda o áudio do comando.

Nenhuma inferência de comando roda aqui (nada de Vosk). Quem transcreve,
interpreta, enfileira e chama a go2-api é o servidor de inferência; a resposta
dele só vira som de retorno e log.

    OCIOSO ──wake word──▶ ESCUTA (até a fala acabar) ──▶ ENVIO ──resposta──▶ OCIOSO
       ▲                     │
       └──── não falou ──────┘

O áudio enviado começa `PRE_ROLL_S` antes da detecção, tirado do buffer
circular: a wake word é detectada com atraso e o comando pode já ter começado.
"""

import json
import logging
import queue
import signal
import time
import uuid
import wave
from collections import Counter, deque
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from typing import Protocol

from sense.audio import Microphone, play_sound
from sense.config import SAMPLE_RATE, Config
from sense.fallback_client import OUTCOME_SOUNDS, FallbackClient, Reply, Utterance
from sense.ring_buffer import RingBuffer
from sense.stats import Stats
from sense.vad import End, Segmenter, rms, speech_level

log = logging.getLogger(__name__)

# Áudio acumulado no microfone acima disto é jogado fora em vez de processado
# atrasado.
MAX_BACKLOG_S = 0.2
# Silêncio mantido no fim do áudio enviado.
TAIL_MARGIN_S = 0.3
# Folga além do timeout do envio até desistir de uma resposta que não veio.
REPLY_GRACE_S = 2.0
LEVEL_HISTORY = 400  # chunks de volume guardados para estimar o ruído de fundo


class WakeEngine(Protocol):
    def score(self, chunk: bytes) -> float: ...


class Sender(Protocol):
    def submit(self, utterance: Utterance, on_reply: Callable[[Reply], None]) -> bool: ...


class _State(Enum):
    IDLE = auto()
    LISTENING = auto()
    SENDING = auto()


class Metrics:
    """Uma linha de log por interação e o resumo de todas no encerramento."""

    def __init__(self) -> None:
        self.results: Counter = Counter()  # status do servidor, ou o motivo local
        self.latencies_s: list[float] = []  # fim da fala → resposta
        self.wakes = 0
        self.wakes_without_speech = 0

    def record(self, interaction: dict) -> None:
        self.results[interaction["result"]] += 1
        if "reply_latency_s" in interaction:
            self.latencies_s.append(interaction["reply_latency_s"])
        log.info("Interação: %s", json.dumps(interaction, ensure_ascii=False))

    def summary(self) -> str:
        results = " ".join(f"{name}={count}" for name, count in sorted(self.results.items()))
        latency = "sem respostas"
        if self.latencies_s:
            ordered = sorted(self.latencies_s)
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            latency = (
                f"latência fim da fala → resposta: média {sum(ordered) / len(ordered):.2f}s, "
                f"p95 {p95:.2f}s ({len(ordered)} respostas)"
            )
        return (
            f"wake words={self.wakes} | sem fala depois (possível falso disparo)="
            f"{self.wakes_without_speech} | {results or 'nenhuma interação'} | {latency}"
        )


class ThinMachine:
    def __init__(
        self,
        wake: WakeEngine,
        sender: Sender,
        config: Config,
        counters: Counter,
        feedback: Callable[[str], None] = lambda name: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._wake = wake
        self._sender = sender
        self._config = config
        self._counters = counters
        self._feedback = feedback
        self._clock = clock
        self.metrics = Metrics()
        self._state = _State.IDLE
        self._buffer = RingBuffer(config.audio_buffer_s)
        self._levels: deque[float] = deque(maxlen=LEVEL_HISTORY)  # volume por chunk
        self._cooldown_s = 0.0  # quanto falta para aceitar outra wake word
        self._segmenter: Segmenter | None = None
        self._interaction: dict = {}
        self._speech_end_at = 0.0
        self._sending_s = 0.0  # há quanto áudio o envio está em curso
        # As respostas chegam da thread de envio e são tratadas no `feed`.
        self._replies: queue.SimpleQueue[Reply] = queue.SimpleQueue()

    def feed(self, chunk: bytes) -> None:
        """Consome um chunk de áudio (PCM 16 kHz mono s16le)."""
        seconds = len(chunk) / 2 / SAMPLE_RATE
        self._buffer.append(chunk)
        self._cooldown_s -= seconds
        self._drain_replies()

        # A wake word é pontuada sempre, para os buffers internos do
        # openWakeWord acompanharem o áudio; fora do OCIOSO ela não dispara.
        woke = self._wake.score(chunk) >= self._config.wake_threshold and self._cooldown_s <= 0
        if woke:
            self._cooldown_s = self._config.wake_cooldown_s

        if self._state is _State.SENDING:
            self._sending_s += seconds
            if self._sending_s > self._config.fallback_timeout_s + REPLY_GRACE_S:
                # O cliente sempre responde dentro do timeout; isto é só para
                # a máquina nunca ficar presa no ENVIO.
                log.warning("Envio sem resposta do cliente; voltando a ouvir.")
                self.metrics.record({**self._interaction, "result": "unconfirmed"})
                self._feedback("unconfirmed")
                self._state = _State.IDLE

        if self._state is _State.LISTENING:
            self._listen(chunk)
        elif woke and self._state is _State.SENDING:
            # Não cancela o envio em curso: o servidor pode já ter executado a
            # frase, e recomeçar arriscaria um comando duplicado.
            log.info("Wake word ignorada: ainda esperando a resposta da frase anterior.")
            self._counters["wake_ignored_sending"] += 1
        elif woke:
            self._start_listening()
        if self._state is _State.IDLE:
            self._levels.append(rms(chunk))

    def _start_listening(self) -> None:
        config = self._config
        log.info("Wake word detectada. Ouvindo o comando...")
        self._counters["wake_detections"] += 1
        self.metrics.wakes += 1
        if config.wake_sound:
            self._feedback("beep")
        self._interaction = {
            "utterance_id": str(uuid.uuid4()),
            "wake_at": round(self._clock(), 3),
        }
        self._segmenter = Segmenter(
            speech_level(self._levels, config.vad_speech_ratio, config.vad_min_rms),
            end_silence_s=config.vad_end_silence_s,
            min_speech_s=config.vad_min_speech_s,
            max_s=config.max_utterance_s,
            speech_timeout_s=config.vad_speech_timeout_s,
            # O beep toca no começo da escuta e não pode passar por fala.
            ignore_s=config.beep_ignore_s if config.wake_sound else 0.0,
        )
        self._state = _State.LISTENING

    def _listen(self, chunk: bytes) -> None:
        segment = self._segmenter.feed(chunk)
        if segment is None:
            return
        interaction = self._interaction
        if segment.end is End.NO_SPEECH:
            log.info("Sem comando: nenhuma fala em %.1fs depois da wake word.", segment.listened_s)
            self._counters["wake_without_speech"] += 1
            self.metrics.wakes_without_speech += 1
            self.metrics.record({**interaction, "result": "no_speech"})
            self._feedback("rejected")
            self._state = _State.IDLE
            return

        # Do pré-roll antes da detecção até o fim da fala, com um resto de silêncio.
        audio = self._buffer.clip(
            segment.listened_s + self._config.pre_roll_s,
            max(segment.silence_s - TAIL_MARGIN_S, 0.0),
        )
        self._speech_end_at = self._clock()
        interaction.update(
            speech_end_at=round(self._speech_end_at, 3),
            audio_s=round(len(audio) / 2 / SAMPLE_RATE, 2),
            truncated=segment.end is End.TRUNCATED,
        )
        cut = " (cortado na duração máxima)" if interaction["truncated"] else ""
        log.info(
            "Comando gravado: %.1fs de áudio%s. Enviando ao servidor...",
            interaction["audio_s"], cut,
        )
        self._save(audio, interaction["utterance_id"])
        utterance = Utterance(
            audio, self._config.utterance_reason, None, None, interaction["utterance_id"]
        )
        if self._sender.submit(utterance, self._replies.put):
            interaction["sent_at"] = round(self._clock(), 3)
            self._sending_s = 0.0
            self._feedback("processing")
            self._state = _State.SENDING
        else:  # não deveria acontecer: só há envio no estado ENVIO
            self.metrics.record({**interaction, "result": "busy"})
            self._feedback("rejected")
            self._state = _State.IDLE

    def _drain_replies(self) -> None:
        while not self._replies.empty():
            reply = self._replies.get()
            if self._state is not _State.SENDING:
                continue  # resposta de um envio do qual a máquina já desistiu
            now = self._clock()
            self.metrics.record(
                {
                    **self._interaction,
                    "reply_at": round(now, 3),
                    "reply_latency_s": round(now - self._speech_end_at, 3),
                    "result": reply.status or "unconfirmed",
                    "outcome": reply.outcome.name.lower(),
                    "transcript": reply.transcript,
                    "error": reply.error,
                }
            )
            self._feedback(OUTCOME_SOUNDS[reply.outcome])
            self._state = _State.IDLE

    def _save(self, audio: bytes, utterance_id: str) -> None:
        directory = self._config.save_utterances_dir
        if directory is None:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
            _write_wav(directory / f"{stamp}_{utterance_id}.wav", audio)
        except OSError as error:
            log.warning("Não salvei o áudio enviado: %s", error)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


class _Shutdown(Exception):
    """O serviço mandou encerrar (SIGTERM)."""


def _raise_shutdown(signum: int, frame: object) -> None:
    raise _Shutdown


def run(config: Config) -> None:
    from sense.wake import OpenWakeWordEngine

    stats = Stats()
    # O modelo carrega antes de o microfone abrir, para ele não acumular áudio.
    wake = OpenWakeWordEngine(config.wake_word)
    log.info("Wake word pronta: %s (limiar %.2f).", config.wake_word, config.wake_threshold)
    client = FallbackClient(
        config.server_url,
        config.edge_id,
        config.fallback_timeout_s,
        config.cancel_timeout_s,
        config.fallback_ok_statuses,
        stats.counters,
    )
    if client.healthy():
        log.info("Servidor de inferência no ar: %s", config.server_url)
    if config.save_utterances_dir is not None:
        log.info("Salvando os áudios enviados em %s", config.save_utterances_dir)
    mic = Microphone(config.mic_name, config.chunk_ms)
    machine = ThinMachine(
        wake, client, config, stats.counters, feedback=lambda name: play_sound(mic.card, name)
    )

    signal.signal(signal.SIGTERM, _raise_shutdown)
    try:
        while True:
            machine.feed(mic.read())
            stats.counters["chunks_dropped_late"] += mic.drop_backlog(MAX_BACKLOG_S)
            stats.maybe_log()
    except (KeyboardInterrupt, _Shutdown):
        log.info("Encerrando. Resumo: %s", machine.metrics.summary())
