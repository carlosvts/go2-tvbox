"""Parada manual: `python -m sense.stop`.

Chama a parada da go2-api direto, sem passar por voz nem pelo servidor de
inferência, e avisa o servidor (`POST /v1/cancel`) em paralelo, sem esperar por
ele. É a garantia para os testes, principalmente no modo thin, em que a parada
falada depende da rede. Sai com 0 se a go2-api aceitou a parada.
"""

import logging
import sys
import threading
from collections import Counter

from sense.commands import load_commands
from sense.config import Config, ConfigError, load_config
from sense.dispatcher import Dispatcher
from sense.fallback_client import FallbackClient

log = logging.getLogger(__name__)


def stop(config: Config) -> bool:
    """Para o robô. True se a go2-api aceitou."""
    counters: Counter = Counter()
    notice = None
    if config.server_url is not None:
        client = FallbackClient(
            config.server_url,
            config.edge_id,
            config.fallback_timeout_s,
            config.cancel_timeout_s,
            config.fallback_ok_statuses,
            counters,
        )
        # Sai antes do POST de parada e não o segura.
        notice = threading.Thread(target=client.send_cancel, args=("stop",), daemon=True)
        notice.start()
    stopped = Dispatcher(config.api_url, 0.0, counters).dispatch(
        load_commands().stop, cooldown=False
    )
    if notice is not None:
        # Só para o processo não acabar com o aviso a meio caminho.
        notice.join(config.cancel_timeout_s + 0.2)
    return stopped


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    try:
        config = load_config()
    except ConfigError as error:
        log.critical("%s", error)
        return 2
    return 0 if stop(config) else 1


if __name__ == "__main__":
    sys.exit(main())
