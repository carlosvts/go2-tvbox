"""Microfone e sons da TV Box (Anker PowerConf via ALSA).

`pyaudio` é importado só ao abrir o microfone, para os testes rodarem num PC
sem PyAudio instalado.
"""

import logging
import re
import subprocess

import numpy as np

from sense.config import REPO_ROOT, SAMPLE_RATE

log = logging.getLogger(__name__)

CAPTURE_RATE = 48000  # taxa nativa do Anker
DOWNSAMPLE_RATIO = CAPTURE_RATE // SAMPLE_RATE  # 3
MEDIA_DIR = REPO_ROOT / "media"
# Sons de `media/`: `beep` (wake word) e o retorno do fallback (`processing`,
# `confirmed`, `rejected`, `unconfirmed`, gerados por scripts/generate_sounds.py).


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


def play_sound(card: int | None, name: str) -> None:
    """Toca `media/<name>.wav` pelo alto-falante do Anker, sem esperar terminar."""
    if card is None:
        return
    try:
        subprocess.Popen(
            ["aplay", "-D", f"plughw:{card},0", "--quiet", str(MEDIA_DIR / f"{name}.wav")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        log.warning("Som %r não tocou: %s", name, error)
