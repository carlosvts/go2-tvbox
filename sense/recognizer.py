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
from typing import TYPE_CHECKING, Protocol

from sense.commands import Command, CommandMap
from sense.config import SAMPLE_RATE, Config

if TYPE_CHECKING:
    from sense.interpreter import Interpreter

log = logging.getLogger(__name__)

WAKE_WORD = "hey_jarvis"
# Áudio jogado fora logo após a wake word: é o eco dela e do beep.
POST_WAKE_DISCARD_S = 0.9
# Depois de uma escuta, a wake word fica ignorada por este tempo, para o áudio
# antigo sair dos buffers internos do openWakeWord.
REARM_S = 0.5


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

    def partial(self) -> str:
        """O que o STT acha que ouviu até agora, sem fechar a frase."""

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
    def __init__(self, model_path: Path, grammar: tuple[str, ...] | None) -> None:
        """`grammar=None` é o modo livre: o vocabulário inteiro do modelo."""
        from vosk import KaldiRecognizer, Model, SetLogLevel

        if not model_path.is_dir():
            raise FileNotFoundError(
                f"Modelo Vosk não encontrado em {model_path}. "
                "Rode scripts/download_models.py ou ajuste VOSK_MODEL_PATH."
            )
        SetLogLevel(-1)
        model = Model(str(model_path))
        if grammar is None:
            self._recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        else:
            # Gramática fechada: o Vosk só pode devolver palavras destas frases.
            # "[unk]" é para onde vai o que não se parece com nenhuma delas.
            closed_grammar = json.dumps([*grammar, "[unk]"], ensure_ascii=False)
            self._recognizer = KaldiRecognizer(model, SAMPLE_RATE, closed_grammar)
        self._recognizer.SetWords(True)

    def accept(self, chunk: bytes) -> Transcript | None:
        if self._recognizer.AcceptWaveform(chunk):
            return self._transcript(self._recognizer.Result())
        return None

    def partial(self) -> str:
        return json.loads(self._recognizer.PartialResult()).get("partial", "")

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
        interpreter: "Interpreter | None" = None,
        intents: dict[str, Command] | None = None,
    ) -> None:
        """Com `interpreter`, a frase passa por ele e o ID vira comando por
        `intents`; sem ele, vale a frase exata de `commands`."""
        self._interpreter = interpreter
        self._intents = intents or {}
        self._wake = wake
        self._stt = stt
        self._commands = commands
        self._config = config
        self._counters = counters
        self._on_wake = on_wake
        self._state = _State.PASSIVE
        self._remaining_s = 0.0  # quanto falta do estado atual (ou do rearme)
        # Só para a telemetria da escuta e do rearme.
        self._partial = ""
        self._partial_at_s = 0.0
        self._rearming = False

    def feed(self, chunk: bytes) -> Command | None:
        """Consome um chunk de áudio. Devolve um comando só quando reconhece um."""
        self._remaining_s -= len(chunk) / 2 / SAMPLE_RATE

        if self._state is _State.PASSIVE:
            score = self._wake.score(chunk)
            if self._remaining_s > 0:
                if score >= self._config.wake_threshold and self._rearming:
                    log.info(
                        "Wake word ignorada (score=%.2f): rearme, faltam %.1fs.",
                        score, self._remaining_s,
                    )
                    self._counters["wake_ignored_rearm"] += 1
                    self._rearming = False  # uma linha por rearme
                return None
            if self._rearming:
                log.info("Rearme terminou: wake word ativa de novo.")
                self._rearming = False
            if score < self._config.wake_threshold:
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
                self._partial = ""
                self._partial_at_s = 0.0
                log.info(
                    "Escuta aberta (descarte de %.1fs acabou; teto de %.1fs).",
                    POST_WAKE_DISCARD_S, self._config.command_timeout_s,
                )
            return None

        transcript = self._stt.accept(chunk)
        heard_after_s = self._config.command_timeout_s - self._remaining_s
        if transcript is not None and transcript.text:
            log.info(
                "Escuta fechada pelo Vosk em %.1fs: fim de fala, %.1fs depois da última "
                "palavra nova.",
                heard_after_s, heard_after_s - self._partial_at_s,
            )
            self._counters["listen_closed_by_vosk"] += 1
        else:
            if transcript is None:
                self._track_partial(heard_after_s)
            else:
                log.info(
                    "Escuta: o Vosk fechou um trecho sem fala em %.1fs; segue ouvindo.",
                    heard_after_s,
                )
            if self._remaining_s > 0:
                return None
            pending = self._partial
            transcript = self._stt.finish()
            log.info(
                "Escuta cortada pelo teto de %.1fs: o Vosk não fechou a frase "
                "(parcial=%r, final=%r).",
                self._config.command_timeout_s, pending, transcript.text,
            )
            if transcript.text:
                self._counters["listen_cut_by_timeout"] += 1

        self._wake.reset()
        self._state = _State.PASSIVE
        self._remaining_s = REARM_S
        self._rearming = True
        return self._match(transcript, heard_after_s)

    def _track_partial(self, heard_after_s: float) -> None:
        """Loga cada mudança do que o STT está ouvindo, com o tempo de escuta."""
        partial = self._stt.partial()
        if partial == self._partial:
            return
        if not self._partial:
            log.info("Escuta: voz detectada aos %.1fs: %r.", heard_after_s, partial)
        elif not partial:
            log.info("Escuta: o Vosk desistiu de %r aos %.1fs.", self._partial, heard_after_s)
        else:
            log.info("Escuta: parcial aos %.1fs: %r.", heard_after_s, partial)
        self._partial = partial
        self._partial_at_s = heard_after_s

    def _match(self, transcript: Transcript, heard_after_s: float) -> Command | None:
        """Decide se a transcrição vira comando. Todo "não" é logado e contado."""
        if not transcript.text:
            log.info("Sem comando: nada reconhecido em %.1fs de escuta.", heard_after_s)
            self._counters["listen_timeouts"] += 1
            return None

        is_stop = False
        if self._interpreter is None:
            command = self._commands.lookup(transcript.text)
            reason = "não casa com nenhuma frase"
        else:
            result = self._interpreter.interpret(transcript.text)
            command = self._intents.get(result.id) if result.id else None
            is_stop = result.is_stop
            reason = f"interpretador: {result.reason}"
            if result.id:
                reason = f"interpretador: {result.id}, nota {result.score:.0f}"
                if command is None:
                    reason += ", sem comando em interpreter.json"
        if command is None:
            log.info(
                "Sem comando: %r (%s; conf=%.2f).", transcript.text, reason, transcript.confidence
            )
            self._counters["no_match"] += 1
            return None
        if self._interpreter is not None:
            log.info("Interpretado: %r (%s).", transcript.text, reason)

        # A parada do interpretador é o comando de segurança: não espera
        # confiança alta do STT.
        if transcript.confidence < self._config.stt_min_confidence and not is_stop:
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
    if config.stt_mode == "gramatica":
        stt = VoskEngine(config.vosk_model_path, commands.grammar)
        interpreter, intents = None, None
        mode = f"{len(commands.grammar)} frases na gramática"
    else:
        from sense.commands import load_intents
        from sense.interpreter import Interpreter

        stt = VoskEngine(config.vosk_model_path, None)
        interpreter, intents = Interpreter.load(), load_intents()
        mode = f"transcrição livre, {len(intents)} comandos no interpretador"
    log.info(
        "Reconhecedor pronto: wake word %s (limiar %.2f), Vosk %s, %s.",
        WAKE_WORD, config.wake_threshold, config.vosk_model_path.name, mode,
    )
    return Recognizer(wake, stt, commands, config, counters, on_wake, interpreter, intents)
