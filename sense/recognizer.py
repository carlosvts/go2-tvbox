"""Reconhecimento: áudio entra, sai um comando ou nada.

É o mesmo componente nos dois modos: na TV Box (edge) e no PC (receptor do
thin). Quem o usa só chama `feed(chunk)` com PCM 16 kHz mono s16le.

    PASSIVO ──wake word──▶ DESCARTE (eco da wake word e do beep)
       ▲                        │
       └── comando ou nada ── ESCUTA (STT com gramática fechada, até o timeout)

O tempo é contado em áudio recebido, não em relógio: o comportamento é o mesmo
com áudio ao vivo, atrasado ou vindo de arquivo.
"""

import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Protocol

from sense.commands import Command, CommandMap
from sense.config import SAMPLE_RATE, Config

log = logging.getLogger(__name__)

WAKE_WORD = "hey_jarvis"
# Áudio jogado fora logo após a wake word: é o eco dela e do beep.
POST_WAKE_DISCARD_S = 0.9
# Depois de uma escuta, a wake word fica ignorada por este tempo, para o áudio
# antigo sair dos buffers internos do openWakeWord.
REARM_S = 1.5


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float  # a menor confiança entre as palavras


class WakeEngine(Protocol):
    def score(self, chunk: bytes) -> float: ...
    def reset(self) -> None: ...


class SttEngine(Protocol):
    def accept(self, chunk: bytes) -> Transcript | None:
        """Consome o chunk. Devolve a transcrição quando a fala termina."""

    def finish(self) -> Transcript:
        """Devolve o que foi ouvido até agora (usado no timeout)."""

    def reset(self) -> None: ...


class OpenWakeWordEngine:
    def __init__(self) -> None:
        import numpy as np
        from openwakeword.model import Model

        self._np = np
        self._model = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")

    def score(self, chunk: bytes) -> float:
        audio = self._np.frombuffer(chunk, dtype=self._np.int16)
        return float(self._model.predict(audio)[WAKE_WORD])

    def reset(self) -> None:
        self._model.reset()


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

    def accept(self, chunk: bytes) -> Transcript | None:
        if self._recognizer.AcceptWaveform(chunk):
            return self._transcript(self._recognizer.Result())
        return None

    def finish(self) -> Transcript:
        return self._transcript(self._recognizer.FinalResult())

    def reset(self) -> None:
        self._recognizer.Reset()

    @staticmethod
    def _transcript(raw: str) -> Transcript:
        result = json.loads(raw)
        words = result.get("result", [])
        return Transcript(
            text=result.get("text", ""),
            confidence=min((word["conf"] for word in words), default=0.0),
        )


class _State(Enum):
    PASSIVE = auto()
    DISCARDING = auto()
    LISTENING = auto()


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

    def feed(self, chunk: bytes) -> Command | None:
        """Consome um chunk de áudio. Devolve um comando só quando reconhece um."""
        self._remaining_s -= len(chunk) / 2 / SAMPLE_RATE

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
            return None

        transcript = self._stt.accept(chunk)
        if transcript is None or not transcript.text:
            if self._remaining_s > 0:
                return None
            transcript = self._stt.finish()

        heard_after_s = self._config.command_timeout_s - self._remaining_s
        self._wake.reset()
        self._state = _State.PASSIVE
        self._remaining_s = REARM_S
        return self._match(transcript, heard_after_s)

    def _match(self, transcript: Transcript, heard_after_s: float) -> Command | None:
        """Decide se a transcrição vira comando. Todo "não" é logado e contado."""
        if not transcript.text:
            log.info("Sem comando: nada reconhecido em %.1fs de escuta.", heard_after_s)
            self._counters["listen_timeouts"] += 1
            return None

        command = self._commands.lookup(transcript.text)
        if command is None:
            log.info(
                "Sem comando: %r não casa com nenhuma frase (conf=%.2f).",
                transcript.text, transcript.confidence,
            )
            self._counters["no_match"] += 1
            return None

        if transcript.confidence < self._config.stt_min_confidence:
            log.info(
                "Sem comando: %r casaria com %s, mas a confiança %.2f está abaixo de %.2f.",
                transcript.text, command.name, transcript.confidence,
                self._config.stt_min_confidence,
            )
            self._counters["low_confidence"] += 1
            return None

        log.info(
            "Comando: %r → %s (conf=%.2f, %.1fs de escuta).",
            transcript.text, command.name, transcript.confidence, heard_after_s,
        )
        self._counters["commands_recognized"] += 1
        return command


def build_recognizer(
    config: Config,
    commands: CommandMap,
    counters: Counter,
    on_wake: Callable[[], None] = lambda: None,
) -> Recognizer:
    """Monta o reconhecedor com os motores reais (openWakeWord + Vosk)."""
    wake = OpenWakeWordEngine()
    stt = VoskEngine(config.vosk_model_path, commands.grammar)
    log.info(
        "Reconhecedor pronto: wake word %s (limiar %.2f), Vosk %s, %d frases na gramática.",
        WAKE_WORD, config.wake_threshold, config.vosk_model_path.name, len(commands.grammar),
    )
    return Recognizer(wake, stt, commands, config, counters, on_wake)
