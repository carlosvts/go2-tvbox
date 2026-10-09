"""Receptor do modo thin: roda no PC com `python -m sense.receiver`.

Recebe o áudio da TV Box e faz o resto: o mesmo reconhecedor e o mesmo
dispatcher do modo edge, só que alimentados pela rede em vez do microfone.
"""

from sense import boot
from sense.commands import load_commands
from sense.config import RECEIVER, Config
from sense.dispatcher import Dispatcher
from sense.recognizer import build_recognizer
from sense.stats import Stats
from sense.transport.tcp import TcpReceiver


def run(config: Config) -> None:
    stats = Stats()
    # A porta só abre depois de os modelos carregarem: antes disso a TV Box
    # ficaria conectada a um receptor que ainda não lê.
    recognizer = build_recognizer(
        config, load_commands(), stats.counters, on_wake=lambda: receiver.send_beep()
    )
    dispatcher = Dispatcher(config.api_url, config.cooldown_s, stats.counters)
    receiver = TcpReceiver(config.receiver_port, stats.counters)

    for chunk in receiver.chunks():
        command = recognizer.feed(chunk)
        if command is not None:
            dispatcher.dispatch(command)
        stats.maybe_log()


if __name__ == "__main__":
    run(boot.start(RECEIVER))
