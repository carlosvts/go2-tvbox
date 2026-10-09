"""Reconhecimento: áudio entra, sai uma decisão ou nada.

Quem o usa só chama `feed(chunk)` com PCM 16 kHz mono s16le.

    PASSIVO ──wake word──▶ DESCARTE (eco da wake word e do beep)
       ▲                        │
       └── decisão ou nada ── ESCUTA (STT com gramática fechada, até o timeout)

A decisão sobre o que foi ouvido, nesta ordem:

1. Tem palavra de parada ⇒ PARADA (a menos que seja exatamente uma frase de
   movimento, como "andar para frente").
2. Tem "[unk]", confiança baixa, fala longa demais ou não é uma frase do mapa
   ⇒ FALLBACK: o áudio da frase vai para o servidor de inferência.
3. Senão ⇒ LOCAL: o comando da frase.

Sem servidor configurado, o que cairia no fallback é descartado.

No fallback, a escuta ainda segue até a pessoa parar de falar (ESPERA): com a
gramática fechada o Vosk fecha a frase no meio quando o resto dela não é de
nenhuma frase conhecida, e o servidor precisa da fala inteira.

O tempo é contado em áudio recebido, não em relógio: o comportamento é o mesmo
com áudio ao vivo, atrasado ou vindo de arquivo.
"""

import json
import logging
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum, auto
from pathlib import Path
from typing import Protocol

import numpy as np

from sense.commands import Command, CommandMap
from sense.config import SAMPLE_RATE, Config
from sense.ring_buffer import RingBuffer
from sense.wake import OpenWakeWordEngine

log = logging.getLogger(__name__)

# Áudio jogado fora logo após a wake word: é o eco dela e do beep.
POST_WAKE_DISCARD_S = 0.9
# Depois de uma escuta, a wake word fica ignorada por este tempo, para o áudio
# antigo sair dos buffers internos do openWakeWord.
REARM_S = 0.5
# Folga de áudio antes e depois da fala no recorte que vai para o fallback.
CLIP_MARGIN_S = 0.3
# Fim de fala para o recorte do fallback: este tempo seguido de chunks "sem
# voz". Um chunk tem voz se o volume (RMS) dele passa de SPEECH_RATIO vezes o
# ruído de fundo dos últimos segundos, e nunca menos que MIN_SPEECH_RMS.
TAIL_SILENCE_S = 0.6
# Escuta sem nenhuma palavra reconhecida, mas com pelo menos este tanto de voz,
# também vai para o fallback: a pessoa disse algo que a gramática não conhece.
MIN_VOICED_S = 0.4
SPEECH_RATIO = 3.0
MIN_SPEECH_RMS = 150.0
LEVEL_HISTORY = 400  # chunks de volume guardados para estimar o ruído de fundo
UNK = "[unk]"


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float  # a menor confiança entre as palavras
    # Início e fim da fala, em segundos desde o começo da escuta (0 = não sei).
    start_s: float = 0.0
    end_s: float = 0.0


class Kind(Enum):
    LOCAL = auto()
    STOP = auto()
    FALLBACK = auto()


@dataclass(frozen=True)
class Decision:
    kind: Kind
    hypothesis: str  # o que a gramática local ouviu
    confidence: float
    command: Command | None = None  # LOCAL e STOP
    reason: str = ""  # FALLBACK: "unk", "low_conf" ou "too_long"
    audio: bytes = b""  # FALLBACK: PCM da frase, com folga


class WakeEngine(Protocol):
    def score(self, chunk: bytes) -> float: ...
    def reset(self) -> None: ...


class SttEngine(Protocol):
    def accept(self, chunk: bytes) -> Transcript | None:
        """Consome o chunk. Devolve a transcrição quando a fala termina."""

    def partial(self) -> str:
        """O que o STT acha que ouviu até agora, sem fechar a frase."""

    def finish(self) -> Transcript:
        """Devolve o que foi ouvido até agora (usado no timeout)."""

    def reset(self) -> None: ...


class VoskEngine:
    def __init__(self, model_path: Path, grammar: tuple[str, ...]) -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not model_path.is_dir():
            raise FileNotFoundError(
                f"Modelo Vosk não encontrado em {model_path}. "
                "Rode scripts/download_models.py ou ajuste VOSK_MODEL_PATH."
            )
        SetLogLevel(-1)
        # Gramática fechada: o Vosk só pode devolver palavras destas frases.
        # "[unk]" é para onde vai o que não se parece com nenhuma delas.
        closed_grammar = json.dumps([*grammar, "[unk]"], ensure_ascii=False)
        self._recognizer = KaldiRecognizer(Model(str(model_path)), SAMPLE_RATE, closed_grammar)
        self._recognizer.SetWords(True)
        # Os tempos das palavras do Vosk contam desde a criação do
        # reconhecedor, não desde o último Reset: guardo quanto áudio ele já
        # recebeu para devolvê-los relativos à escuta atual.
        self._fed_s = 0.0
        self._base_s = 0.0

    def accept(self, chunk: bytes) -> Transcript | None:
        self._fed_s += len(chunk) / 2 / SAMPLE_RATE
        if self._recognizer.AcceptWaveform(chunk):
            return self._transcript(self._recognizer.Result())
        return None

    def partial(self) -> str:
        return json.loads(self._recognizer.PartialResult()).get("partial", "")

    def finish(self) -> Transcript:
        return self._transcript(self._recognizer.FinalResult())

    def reset(self) -> None:
        self._recognizer.Reset()
        self._base_s = self._fed_s

    def _transcript(self, raw: str) -> Transcript:
        result = json.loads(raw)
        words = result.get("result", [])
        return Transcript(
            text=result.get("text", ""),
            confidence=min((word["conf"] for word in words), default=0.0),
            start_s=words[0]["start"] - self._base_s if words else 0.0,
            end_s=words[-1]["end"] - self._base_s if words else 0.0,
        )


class _State(Enum):
    PASSIVE = auto()
    DISCARDING = auto()
    LISTENING = auto()
    TAILING = auto()  # decisão de fallback tomada; esperando a fala acabar


class Recognizer:
    def __init__(
        self,
        wake: WakeEngine,
        stt: SttEngine,
        commands: CommandMap,
        config: Config,
        counters: Counter,
        on_wake: Callable[[], None] = lambda: None,
    ) -> None:
        self._wake = wake
        self._stt = stt
        self._commands = commands
        self._config = config
        self._counters = counters
        self._on_wake = on_wake
        self._state = _State.PASSIVE
        self._remaining_s = 0.0  # quanto falta do estado atual (ou do rearme)
        self._listened_s = 0.0  # áudio da escuta atual
        self._buffer = RingBuffer(config.audio_buffer_s)
        self._levels: deque[float] = deque(maxlen=LEVEL_HISTORY)  # RMS por chunk
        self._speech_level = MIN_SPEECH_RMS  # acima disto o chunk tem voz
        self._silence_s = 0.0  # há quanto tempo a escuta está sem voz
        self._voiced_s = 0.0  # quanto da escuta teve voz
        self._pending: Decision | None = None  # fallback à espera do fim da fala

    def feed(self, chunk: bytes) -> Decision | None:
        """Consome um chunk de áudio. Devolve uma decisão só quando a escuta acaba."""
        seconds = len(chunk) / 2 / SAMPLE_RATE
        self._remaining_s -= seconds
        self._buffer.append(chunk)
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float64)
        level = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
        self._levels.append(level)

        if self._state is _State.PASSIVE:
            score = self._wake.score(chunk)
            if self._remaining_s > 0 or score < self._config.wake_threshold:
                return None
            log.info("Wake word detectada (score=%.2f). Ouvindo comando...", score)
            self._counters["wake_detections"] += 1
            self._on_wake()
            self._state = _State.DISCARDING
            self._remaining_s = POST_WAKE_DISCARD_S
            return None

        if self._state is _State.DISCARDING:
            if self._remaining_s <= 0:
                self._stt.reset()
                self._state = _State.LISTENING
                self._remaining_s = self._config.command_timeout_s
                self._listened_s = 0.0
                self._silence_s = 0.0
                self._voiced_s = 0.0
                # Ruído de fundo: o 20º percentil do volume recente (que inclui
                # a wake word e o beep, bem mais altos).
                noise = float(np.percentile(self._levels, 20))
                self._speech_level = max(SPEECH_RATIO * noise, MIN_SPEECH_RMS)
            return None

        self._listened_s += seconds
        if level > self._speech_level:
            self._silence_s = 0.0
            self._voiced_s += seconds
        else:
            self._silence_s += seconds
        if self._state is _State.TAILING:
            return None if self._still_speaking() else self._finish(self._pending)

        transcript = self._stt.accept(chunk)
        if transcript is None or not transcript.text:
            if self._remaining_s > 0:
                return None
            # Passou do teto. Se há fala em curso, espera o Vosk fechar a frase
            # (até MAX_UTTERANCE_S) em vez de cortá-la no meio.
            if self._listened_s < self._config.max_utterance_s and self._stt.partial():
                return None
            transcript = self._stt.finish()

        decision = self._decide(transcript)
        if decision is not None and decision.kind is Kind.FALLBACK and self._still_speaking():
            self._pending = decision
            self._state = _State.TAILING
            return None
        return self._finish(decision)

    def _still_speaking(self) -> bool:
        return (
            self._silence_s < TAIL_SILENCE_S and self._listened_s < self._config.max_utterance_s
        )

    def _finish(self, decision: Decision | None) -> Decision | None:
        """Encerra a escuta. No fallback, anexa o áudio dela, com folga."""
        self._wake.reset()
        self._state = _State.PASSIVE
        self._remaining_s = REARM_S
        self._pending = None
        if decision is None or decision.kind is not Kind.FALLBACK:
            return decision
        audio = self._buffer.clip(
            self._listened_s + CLIP_MARGIN_S, max(self._silence_s - CLIP_MARGIN_S, 0.0)
        )
        log.info(
            "Fallback: %.1fs de áudio (escuta de %.1fs).", len(audio) / 2 / SAMPLE_RATE,
            self._listened_s,
        )
        return replace(decision, audio=audio)

    def _decide(self, transcript: Transcript) -> Decision | None:
        """Decide o que fazer com a transcrição. Todo "nada" é logado e contado."""
        text, confidence = transcript.text, transcript.confidence
        fallback = self._config.server_url is not None
        if not text:
            if fallback and self._voiced_s >= MIN_VOICED_S:
                log.info(
                    "Fallback (unk): nenhuma palavra reconhecida, mas %.1fs de voz.", self._voiced_s
                )
                self._counters["no_match"] += 1
                return Decision(Kind.FALLBACK, "", 0.0, reason="unk")
            log.info("Sem comando: nada reconhecido em %.1fs de escuta.", self._listened_s)
            self._counters["listen_timeouts"] += 1
            return None

        command = self._commands.lookup(text)
        has_unk = UNK in text.split()
        low_confidence = confidence < self._config.stt_min_confidence

        # Parada antes de tudo. Uma frase de movimento inteira ("andar para
        # frente") não conta: o "para" dela é preposição.
        stop = self._commands.stop
        if stop is not None and self._commands.has_stop_word(text) and command in (None, stop):
            sure = command is stop and not low_confidence
            if sure or self._config.stop_failsafe:
                log.info(
                    "Parada: %r (conf=%.2f, %.1fs de escuta%s).",
                    text, confidence, self._listened_s, "" if sure else "; fail-safe",
                )
                self._counters["stops_recognized"] += 1
                return Decision(Kind.STOP, text, confidence, command=stop)

        duration_s = transcript.end_s - transcript.start_s
        if has_unk or command is None:
            reason, counter, why = "unk", "no_match", "não casa com nenhuma frase"
        elif low_confidence:
            reason, counter = "low_conf", "low_confidence"
            why = f"casaria com {command.name}, mas a confiança é baixa"
        elif fallback and duration_s > self._config.max_local_utterance_s:
            reason, counter = "too_long", "too_long"
            why = f"fala de {duration_s:.1f}s, longa demais"
        else:
            log.info(
                "Comando: %r → %s (conf=%.2f, %.1fs de escuta).",
                text, command.name, confidence, self._listened_s,
            )
            self._counters["commands_recognized"] += 1
            return Decision(Kind.LOCAL, text, confidence, command=command)

        self._counters[counter] += 1
        if not fallback:
            log.info("Sem comando: %r %s (conf=%.2f).", text, why, confidence)
            return None
        log.info("Fallback (%s): %r %s (conf=%.2f).", reason, text, why, confidence)
        return Decision(Kind.FALLBACK, text, confidence, reason=reason)


def build_recognizer(
    config: Config,
    commands: CommandMap,
    counters: Counter,
    on_wake: Callable[[], None] = lambda: None,
) -> Recognizer:
    """Monta o reconhecedor com os motores reais (openWakeWord + Vosk)."""
    wake = OpenWakeWordEngine(config.wake_word, config.wake_vad_threshold)
    stt = VoskEngine(config.vosk_model_path, commands.grammar)
    log.info(
        "Reconhecedor pronto: wake word %s (limiar %.2f), Vosk %s, %d frases na gramática.",
        config.wake_word, config.wake_threshold, config.vosk_model_path.name, len(commands.grammar),
    )
    return Recognizer(wake, stt, commands, config, counters, on_wake)
