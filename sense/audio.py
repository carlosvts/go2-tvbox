"""Microfone e sons da TV Box (Anker PowerConf via ALSA).

`pyaudio` é importado só ao abrir o microfone, para os testes rodarem num PC
sem PyAudio instalado.
"""

import logging
import queue
import random
import re
import subprocess
import threading
from pathlib import Path

import numpy as np

from sense.config import REPO_ROOT, SAMPLE_RATE

log = logging.getLogger(__name__)

CAPTURE_RATE = 48000  # taxa nativa do Anker
DOWNSAMPLE_RATIO = CAPTURE_RATE // SAMPLE_RATE  # 3
MEDIA_DIR = REPO_ROOT / "media"
# Sons de `media/`: `beep` (wake word, scripts/generate_beeps.py) e as falas de
# retorno `processing`, `confirmed`, `rejected`, `blocked` e `unconfirmed`
# (scripts/generate_voice.py).

# Som que toca no lugar de outro enquanto este não tiver WAV em `media/`: as
# falas de `blocked` são geradas no PC, e até lá "não entendi" é melhor que nada.
SOUND_FALLBACKS = {"blocked": "rejected"}


class MicrophoneError(Exception):
    """Nenhum microfone com o nome pedido abriu."""


def downsample(raw_48k: bytes) -> bytes:
    """48 kHz → 16 kHz por decimação (1 de cada 3 amostras)."""
    return np.frombuffer(raw_48k, dtype=np.int16)[::DOWNSAMPLE_RATIO].tobytes()


def alsa_card(device_name: str) -> int | None:
    """Número do card ALSA a partir do nome PyAudio, ex.: "... (hw:1,0)" → 1."""
    match = re.search(r"hw:(\d+),", device_name)
    return int(match.group(1)) if match else None


class Microphone:
    """Stream de captura que entrega chunks de `chunk_ms` em 16 kHz mono s16le."""

    def __init__(self, mic_name: str, chunk_ms: int) -> None:
        import pyaudio

        self._frames_48k = CAPTURE_RATE * chunk_ms // 1000
        self._pa = pyaudio.PyAudio()

        names = [
            self._pa.get_device_info_by_index(i)["name"]
            for i in range(self._pa.get_device_count())
        ]
        # Abre de verdade em vez de confiar em maxInputChannels, que pode vir 0
        # de forma espúria logo após o boot (visto no cliente antigo).
        for index, name in enumerate(names):
            if mic_name.lower() not in name.lower():
                continue
            try:
                self._stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=CAPTURE_RATE,
                    input=True,
                    input_device_index=index,
                    frames_per_buffer=self._frames_48k,
                )
            except OSError as error:
                log.warning("Dispositivo [%d] %s não abriu: %s", index, name, error)
                continue
            self.card = alsa_card(name)
            log.info(
                "Microfone aberto: [%d] %s | %d Hz → %d Hz | chunk de %d ms | card ALSA: %s",
                index, name, CAPTURE_RATE, SAMPLE_RATE, chunk_ms, self.card,
            )
            return

        self._pa.terminate()
        raise MicrophoneError(
            f"Nenhum microfone com {mic_name!r} no nome abriu. Dispositivos: {names}"
        )

    def read(self) -> bytes:
        """Bloqueia até ter um chunk. Levanta OSError se o microfone cair."""
        return downsample(self._stream.read(self._frames_48k, exception_on_overflow=False))

    def drop_backlog(self, max_backlog_s: float) -> int:
        """Joga fora o áudio acumulado se passar de `max_backlog_s`.

        Devolve quantos chunks descartou (0 se o acúmulo estava dentro do limite).
        """
        pending = self._stream.get_read_available() // self._frames_48k
        if pending * self._frames_48k / CAPTURE_RATE <= max_backlog_s:
            return 0
        for _ in range(pending):
            self._stream.read(self._frames_48k, exception_on_overflow=False)
        return pending

    def close(self) -> None:
        self._stream.close()
        self._pa.terminate()


class SoundPlayer:
    """Toca `media/<nome>.wav` pelo alto-falante do Anker, um som de cada vez.

    Os sons vêm de threads diferentes (o laço do microfone e a thread do envio
    ao servidor) e o `aplay` em `plughw:` abre o dispositivo sozinho: dois ao
    mesmo tempo disputam o alto-falante e um deles falha em silêncio. Aqui eles
    entram numa fila e tocam em sequência, numa thread à parte, então
    `play()` nunca bloqueia o laço do microfone.

    Um som pode ter variantes, `<nome>_2.wav`, `<nome>_3.wav`...: uma delas é
    sorteada a cada vez, sem repetir a anterior, para a fala não soar robótica.
    """

    MAX_PENDING = 2  # se acumular mais que isto, o mais antigo é descartado
    TIMEOUT_S = 15.0  # um `aplay` que passe disto é morto

    def __init__(self, media_dir: Path = MEDIA_DIR, runner=subprocess.run, rng=None) -> None:
        self._media_dir = media_dir
        self._runner = runner
        self._rng = rng or random.Random()
        self._queue: queue.Queue[tuple[int, Path]] = queue.Queue(maxsize=self.MAX_PENDING)
        self._variants: dict[str, list[Path]] = {}
        self._last: dict[str, Path] = {}
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def variants(self, name: str) -> list[Path]:
        """`<nome>.wav` e `<nome>_<número>.wav`, em ordem.

        Sem nenhum, as do som de `SOUND_FALLBACKS`, se houver.
        """
        if name not in self._variants:
            self._variants[name] = self._find(name)
            fallback = SOUND_FALLBACKS.get(name)
            if not self._variants[name] and fallback:
                self._variants[name] = self._find(fallback)
                log.warning(
                    "Som %r não encontrado em %s; tocando %r no lugar.",
                    name, self._media_dir, fallback,
                )
            elif not self._variants[name]:
                log.warning("Som %r não encontrado em %s.", name, self._media_dir)
        return self._variants[name]

    def _find(self, name: str) -> list[Path]:
        pattern = re.compile(rf"^{re.escape(name)}(_\d+)?\.wav$")
        return sorted(
            path for path in self._media_dir.glob(f"{name}*.wav") if pattern.match(path.name)
        )

    def play(self, card: int | None, name: str) -> None:
        if card is None:
            return
        options = self.variants(name)
        if not options:
            return
        with self._lock:
            if len(options) > 1:
                options = [path for path in options if path != self._last.get(name)]
            path = self._rng.choice(options)
            self._last[name] = path
            self._start()
            try:
                self._queue.put_nowait((card, path))
            except queue.Full:
                try:  # descarta o mais antigo que ainda não começou
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    pass
                self._queue.put_nowait((card, path))

    def wait_idle(self) -> None:
        """Espera tudo o que está na fila terminar de tocar (para os testes)."""
        self._queue.join()

    def _start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, name="sound-player", daemon=True)
            self._thread.start()

    def _work(self) -> None:
        while True:
            card, path = self._queue.get()
            try:
                log.info("Tocando %s", path.name)
                self._runner(
                    ["aplay", "-D", f"plughw:{card},0", "--quiet", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.TIMEOUT_S,
                )
            except (OSError, subprocess.SubprocessError) as error:
                log.warning("Som %r não tocou: %s", path.name, error)
            finally:
                self._queue.task_done()


_player = SoundPlayer()


def play_sound(card: int | None, name: str) -> None:
    """Toca `media/<name>.wav` (ou uma variante) no Anker, sem esperar terminar."""
    _player.play(card, name)
