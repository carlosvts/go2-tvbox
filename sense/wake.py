"""Wake word com o openWakeWord.

Fica separado do reconhecedor para o modo thin usá-lo sem carregar nada do
Vosk. O openWakeWord é importado só ao criar o motor.
"""

from collections import deque

from sense.config import SAMPLE_RATE

# Com o VAD ligado, a wake word só vale se houve voz dentro desta janela. A
# janela cobre a wake word inteira: a nota dela sobe só no fim, quando a
# pessoa já pode ter parado de falar.
VAD_WINDOW_S = 1.0


class OpenWakeWordEngine:
    def __init__(self, wake_word: str, vad_threshold: float = 0.0) -> None:
        """
        Args:
            wake_word: nome de um modelo do openWakeWord, ex.: "hey_jarvis".
            vad_threshold: acima de 0, liga o VAD do Silero que vem com o
                openWakeWord: a wake word só pontua se o VAD viu voz, com
                pelo menos esta nota (0 a 1), no último segundo. Filtra
                disparos por ruído que não é voz.
        """
        import numpy as np
        import openwakeword
        from openwakeword.model import Model

        self._np = np
        self._wake_word = wake_word
        self._model = Model(wakeword_models=[wake_word], inference_framework="onnx")
        # O `vad_threshold` do próprio Model olha de 4 a 7 chamadas atrás, o
        # que só funciona com chunks de 80 ms; com os nossos, de 30 ms, ele
        # zera a nota justamente no pico. Por isso o VAD é aplicado aqui, por
        # tempo de áudio.
        self._vad_threshold = vad_threshold
        self._vad = openwakeword.VAD() if vad_threshold > 0 else None
        self._voice: deque[tuple[float, float]] = deque()  # (duração, nota) por chunk
        self._voice_s = 0.0

    def score(self, chunk: bytes) -> float:
        audio = self._np.frombuffer(chunk, dtype=self._np.int16)
        score = float(self._model.predict(audio)[self._wake_word])
        if self._vad is None:
            return score
        return score if self._heard_voice(audio) else 0.0

    def _heard_voice(self, audio: object) -> bool:
        """Atualiza o VAD com o chunk e diz se houve voz na janela recente."""
        seconds = len(audio) / SAMPLE_RATE
        self._voice.append((seconds, float(self._vad.predict(audio, frame_size=len(audio)))))
        self._voice_s += seconds
        while self._voice_s - self._voice[0][0] >= VAD_WINDOW_S:
            self._voice_s -= self._voice.popleft()[0]
        return max(voice for _, voice in self._voice) >= self._vad_threshold

    def reset(self) -> None:
        self._model.reset()
        if self._vad is not None:
            self._vad.reset_states()
            self._voice.clear()
            self._voice_s = 0.0
