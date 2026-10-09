"""Fim de fala por volume, para recortar o comando dito depois da wake word.

Um chunk "tem voz" se o volume (RMS) dele passa de um limiar adaptado ao ruído
de fundo. O `Segmenter` acompanha os chunks de uma escuta e diz quando ela
acabou: a pessoa falou e parou, falou demais, ou não falou.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from sense.config import SAMPLE_RATE


def rms(chunk: bytes) -> float:
    """Volume de um chunk PCM s16le."""
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0


def speech_level(recent_levels: Iterable[float], ratio: float, min_rms: float) -> float:
    """Volume acima do qual um chunk conta como voz.

    O ruído de fundo é o 20º percentil do volume recente, que resiste à wake
    word e ao beep (bem mais altos) estarem na amostra.
    """
    levels = list(recent_levels)
    noise = float(np.percentile(levels, 20)) if levels else 0.0
    return max(ratio * noise, min_rms)


class End(Enum):
    SPEECH = "speech"  # falou e parou
    TRUNCATED = "truncated"  # falou até a duração máxima
    NO_SPEECH = "no_speech"  # não falou (ou só houve ruído curto)


@dataclass(frozen=True)
class Segment:
    end: End
    listened_s: float  # áudio desde o começo da escuta
    silence_s: float  # silêncio no fim dela
    voiced_s: float  # quanto dela teve voz


class Segmenter:
    def __init__(
        self,
        speech_level: float,
        *,
        end_silence_s: float,
        min_speech_s: float,
        max_s: float,
        speech_timeout_s: float,
        ignore_s: float = 0.0,
    ) -> None:
        """
        Args:
            speech_level: volume acima do qual o chunk tem voz.
            end_silence_s: silêncio que encerra a fala.
            min_speech_s: voz mínima para valer como fala; menos que isso,
                seguida de silêncio, é ruído e é esquecida.
            max_s: duração máxima da escuta; a fala é cortada aí.
            speech_timeout_s: sem fala até aqui, a escuta acaba sem nada.
            ignore_s: começo da escuta que nunca conta como voz (o beep).
        """
        self._speech_level = speech_level
        self._end_silence_s = end_silence_s
        self._min_speech_s = min_speech_s
        self._max_s = max_s
        self._speech_timeout_s = speech_timeout_s
        self._ignore_s = ignore_s
        self._listened_s = 0.0
        self._silence_s = 0.0
        self._voiced_s = 0.0

    def feed(self, chunk: bytes) -> Segment | None:
        """Consome um chunk. Devolve o resultado quando a escuta acaba."""
        seconds = len(chunk) / 2 / SAMPLE_RATE
        self._listened_s += seconds
        if self._listened_s > self._ignore_s and rms(chunk) > self._speech_level:
            self._voiced_s += seconds
            self._silence_s = 0.0
        else:
            self._silence_s += seconds

        speaking = self._voiced_s >= self._min_speech_s
        if speaking and self._silence_s >= self._end_silence_s:
            return self._segment(End.SPEECH)
        if self._listened_s >= self._max_s:
            return self._segment(End.TRUNCATED if speaking else End.NO_SPEECH)
        if not speaking and self._silence_s >= self._end_silence_s:
            self._voiced_s = 0.0  # ruído curto: esquece
            if self._listened_s >= self._speech_timeout_s:
                return self._segment(End.NO_SPEECH)
        return None

    def _segment(self, end: End) -> Segment:
        return Segment(end, self._listened_s, self._silence_s, self._voiced_s)
