"""Parada manual: `python -m sense.stop`.

Chama `POST /commands/stop` da go2-api direto, sem passar por voz nem pelo
servidor de inferência, e avisa o servidor (`POST /v1/cancel`) em paralelo, sem
esperar por ele, para a fila dele não retomar. É a garantia para os testes: a
parada falada depende da rede e do servidor. Sai com 0 se a go2-api aceitou a
parada, 1 se não aceitou e 2 se a configuração não permite tentar.
"""

import logging
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter

from sense.config import Config, ConfigError, load_config
from sense.fallback_client import FallbackClient

log = logging.getLogger(__name__)

STOP_ENDPOINT = "/commands/stop"
POST_TIMEOUT_S = 2.0  # uma tentativa só: uma parada que chega tarde já não ajuda


def stop_robot(api_url: str) -> bool:
    """`POST /commands/stop`. True só se a go2-api respondeu 202."""
    # Igual ao que o Dispatcher antigo mandava: POST sem corpo.
    request = urllib.request.Request(
        api_url + STOP_ENDPOINT,
        data=None,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=POST_TIMEOUT_S) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        # A API respondeu, mas recusou: 503 = API viva, sem conexão com o robô.
        detail = error.read().decode(errors="replace")[:200]
        log.error("Parada recusada pela go2-api (HTTP %d): %s", error.code, detail)
        return False
    except OSError as error:  # conexão recusada, DNS, timeout
        log.error("Parada não enviada: go2-api fora do ar (%s).", error)
        return False
    if status != 202:
        log.error("Parada com resposta inesperada da go2-api (HTTP %d).", status)
        return False
    log.info("Parada aceita pela go2-api.")
    return True


def stop(config: Config) -> bool:
    """Para o robô. True se a go2-api aceitou."""
    if config.api_url is None:
        log.error("GO2_API_URL não definido no .env: sem ele a parada manual não sabe aonde ir.")
        return False
    client = FallbackClient(
        config.server_url,
        config.edge_id,
        config.fallback_timeout_s,
        config.cancel_timeout_s,
        config.fallback_ok_statuses,
        Counter(),
    )
    # Sai antes do POST de parada e não o segura.
    notice = threading.Thread(target=client.send_cancel, args=("stop",), daemon=True)
    notice.start()
    stopped = stop_robot(config.api_url)
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
    if config.api_url is None:
        log.critical("GO2_API_URL não definido no .env: a parada manual precisa da go2-api.")
        return 2
    return 0 if stop(config) else 1


if __name__ == "__main__":
    sys.exit(main())
