"""Wake word com o openWakeWord.

Fica separado do reconhecedor para o modo thin usá-lo sem carregar nada do
Vosk. O openWakeWord é importado só ao criar o motor.
"""


class OpenWakeWordEngine:
    def __init__(self, wake_word: str) -> None:
        """`wake_word` é o nome de um modelo do openWakeWord, ex.: "hey_jarvis"."""
        import numpy as np
        from openwakeword.model import Model

        self._np = np
        self._wake_word = wake_word
        self._model = Model(wakeword_models=[wake_word], inference_framework="onnx")

    def score(self, chunk: bytes) -> float:
        audio = self._np.frombuffer(chunk, dtype=self._np.int16)
        return float(self._model.predict(audio)[self._wake_word])

    def reset(self) -> None:
        self._model.reset()
