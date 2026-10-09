"""Modo thin: a TV Box só captura e envia o áudio ao receptor no PC.

Não importa nada do reconhecimento: roda sem openWakeWord e sem Vosk.
"""

from sense.audio import Microphone, play_beep
from sense.config import Config
from sense.stats import Stats
from sense.transport.tcp import TcpSender


def run(config: Config) -> None:
    stats = Stats()
    mic = Microphone(config.mic_name, config.chunk_ms)
    sender = TcpSender(
        config.receiver_host,
        config.receiver_port,
        config.chunk_ms,
        stats.counters,
        on_beep=lambda: play_beep(mic.card),
    )
    while True:
        sender.send(mic.read())
        stats.maybe_log()
