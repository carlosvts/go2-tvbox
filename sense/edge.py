"""O laço da TV Box: captura, reconhece e executa a decisão.

Parada e comando local vão direto à go2-api, como sempre; antes de um
movimento, o desvio de obstáculo do robô tem de estar ligado. O que a gramática
não resolve vai, em áudio, para o servidor de inferência (se houver um
configurado), que é quem interpreta e executa: aqui não há fila e nada do que o
servidor responde vira comando.
"""

import logging
from collections.abc import Callable

from sense.audio import Microphone, play_sound
from sense.commands import load_commands
from sense.config import Config
from sense.dispatcher import Dispatcher
from sense.fallback_client import OUTCOME_SOUNDS, FallbackClient, Utterance
from sense.obstacle_guard import ObstacleGuard
from sense.recognizer import Decision, Kind, build_recognizer
from sense.stats import Stats

log = logging.getLogger(__name__)

# Áudio acumulado no microfone acima disto é jogado fora em vez de processado
# atrasado. Acontece depois de um POST lento ou se a CPU não acompanhar.
MAX_BACKLOG_S = 0.2

# Nome, no commands.json, do comando que faz o robô andar ou girar.
MOVE_COMMAND = "move"

class Executor:
    """Executa uma decisão do reconhecedor."""

    def __init__(
        self,
        dispatcher: Dispatcher,
        fallback: FallbackClient | None,
        feedback: Callable[[str], None],
        guard: ObstacleGuard | None = None,
    ) -> None:
        """Com `guard`, movimento só sai com o desvio de obstáculo ligado."""
        self._dispatcher = dispatcher
        self._fallback = fallback
        self._feedback = feedback
        self._guard = guard

    def handle(self, decision: Decision) -> None:
        if decision.kind is Kind.STOP:
            # O aviso ao servidor sai em paralelo e não segura a parada local.
            if self._fallback is not None:
                self._fallback.cancel("stop")
            self._dispatcher.dispatch(decision.command, cooldown=False)
        elif decision.kind is Kind.LOCAL:
            if self._blocked(decision):
                return
            # Um comando local mais novo invalida o que estiver na fila do servidor.
            if self._dispatcher.dispatch(decision.command) and self._fallback is not None:
                self._fallback.cancel("local_command")
        elif self._fallback is not None:
            utterance = Utterance(
                decision.audio, decision.reason, decision.hypothesis, decision.confidence
            )
            sent = self._fallback.submit(utterance)
            # Retorno na hora, para a pessoa não repetir o comando.
            self._feedback("processing" if sent else "rejected")


    def _blocked(self, decision: Decision) -> bool:
        """Movimento sem o desvio de obstáculo confirmado: não sai."""
        if self._guard is None or decision.command.name != MOVE_COMMAND:
            return False
        if self._guard.ensure_on():
            return False
        log.warning("Descartado: %r, sem o desvio de obstáculo ligado.", decision.hypothesis)
        self._feedback("rejected")
        return True


def run(config: Config) -> None:
    stats = Stats()
    # Os modelos carregam antes de o microfone abrir, para ele não acumular
    # áudio enquanto isso.
    recognizer = build_recognizer(
        config, load_commands(), stats.counters, on_wake=lambda: play("beep")
    )
    fallback = None
    if config.server_url is not None:
        fallback = FallbackClient(
            config.server_url,
            config.edge_id,
            config.fallback_timeout_s,
            config.cancel_timeout_s,
            config.fallback_ok_statuses,
            stats.counters,
            on_outcome=lambda outcome: play(OUTCOME_SOUNDS[outcome]),
        )
    else:
        log.info("SERVER_URL não definido: fallback desligado, só a gramática local.")
    dispatcher = Dispatcher(config.api_url, config.cooldown_s, stats.counters)
    guard = None
    if config.require_obstacle_avoidance:
        guard = ObstacleGuard(config.api_url, stats.counters)
    else:
        log.warning("REQUIRE_OBSTACLE_AVOIDANCE=0: movimentos saem sem conferir o desvio.")
    executor = Executor(dispatcher, fallback, lambda name: play(name), guard)
    mic = Microphone(config.mic_name, config.chunk_ms)

    def play(name: str) -> None:
        play_sound(mic.card, name)

    while True:
        decision = recognizer.feed(mic.read())
        if decision is not None:
            executor.handle(decision)
        stats.counters["chunks_dropped_late"] += mic.drop_backlog(MAX_BACKLOG_S)
        stats.maybe_log()
