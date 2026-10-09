"""Buffer circular com os últimos segundos de áudio (PCM 16 kHz mono s16le).

É alimentado o tempo todo; quando uma frase termina, `clip` recorta o trecho
dela para o fallback. Posições são dadas em segundos atrás do áudio mais novo.
"""

from collections import deque

from sense.config import SAMPLE_RATE

BYTES_PER_SECOND = SAMPLE_RATE * 2


class RingBuffer:
    def __init__(self, seconds: float) -> None:
        self._max_bytes = int(seconds * BYTES_PER_SECOND)
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, chunk: bytes) -> None:
        self._chunks.append(chunk)
        self._size += len(chunk)
        # Sempre sobra pelo menos `seconds`: só sai o chunk que não faz falta.
        while self._size - len(self._chunks[0]) >= self._max_bytes:
            self._size -= len(self._chunks.popleft())

    def clip(self, start_ago_s: float, end_ago_s: float = 0.0) -> bytes:
        """Áudio de `start_ago_s` até `end_ago_s` segundos atrás.

        O que cair fora do que o buffer ainda guarda é cortado em silêncio.
        """
        audio = b"".join(self._chunks)
        start = len(audio) - _even(start_ago_s * BYTES_PER_SECOND)
        end = len(audio) - _even(max(end_ago_s, 0.0) * BYTES_PER_SECOND)
        return audio[max(start, 0) : max(end, 0)]


def _even(n_bytes: float) -> int:
    """Arredonda para um número inteiro de amostras (2 bytes cada)."""
    return int(n_bytes) // 2 * 2
