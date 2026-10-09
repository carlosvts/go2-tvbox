"""Modo edge: a TV Box captura, reconhece e faz o POST na go2-api."""

import logging

from sense.audio import Microphone, play_beep
from sense.commands import load_commands
from sense.config import Config
from sense.dispatcher import Dispatcher
from sense.recognizer import build_recognizer
from sense.stats import Stats

log = logging.getLogger(__name__)

# Áudio acumulado no microfone acima disto é jogado fora em vez de processado
# atrasado. Acontece depois de um POST lento ou se a CPU não acompanhar.
MAX_BACKLOG_S = 0.2


def run(config: Config) -> None:
    stats = Stats()
    # Os modelos carregam antes de o microfone abrir, para ele não acumular
    # áudio enquanto isso.
    recognizer = build_recognizer(
        config, load_commands(), stats.counters, on_wake=lambda: play_beep(mic.card)
    )
    dispatcher = Dispatcher(config.api_url, config.cooldown_s, stats.counters)
    mic = Microphone(config.mic_name, config.chunk_ms)

    while True:
        command = recognizer.feed(mic.read())
        if command is not None:
            dispatcher.dispatch(command)
        dropped = mic.drop_backlog(MAX_BACKLOG_S)
        if dropped:
            log.info(
                "Atraso: %d chunks (%.2fs de áudio) jogados fora; o processamento "
                "não acompanhou o microfone.",
                dropped, dropped * config.chunk_ms / 1000,
            )
        stats.counters["chunks_dropped_late"] += dropped
        stats.maybe_log()
