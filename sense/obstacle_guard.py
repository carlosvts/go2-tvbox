"""Garante o desvio de obstáculo do robô ligado antes de um movimento.

Antes de cada `move`, pergunta à go2-api (`GET /safety/obstacle-avoidance`). Se
o desvio não estiver ligado, manda ligar (`PUT`) e pergunta de novo. Sem a
confirmação de que está ligado, o movimento não sai.

Limite conhecido: a go2-api ainda não comprovou que o desvio ligado filtra o
`move` dela. Este módulo garante o desvio ligado, não que o robô desvie.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable

log = logging.getLogger(__name__)

ENDPOINT = "/safety/obstacle-avoidance"
# O GET espera a resposta do robô; a API desiste em GO2_REQUEST_TIMEOUT_S.
TIMEOUT_S = 4.0
# O PUT não espera o robô: depois dele, a confirmação é perguntada algumas
# vezes, com este intervalo.
CONFIRM_TRIES = 3
CONFIRM_WAIT_S = 0.3


class ObstacleGuard:
    def __init__(
        self,
        api_url: str,
        counters: Counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = api_url + ENDPOINT
        self._counters = counters
        self._sleep = sleep

    def ensure_on(self) -> bool:
        """True só com a confirmação da API de que o desvio está ligado."""
        state = self._read()
        if state is True:
            return True
        log.info(
            "Desvio de obstáculo %s: mandando ligar.",
            "desligado" if state is False else "em estado desconhecido",
        )
        if self._switch_on():
            for _ in range(CONFIRM_TRIES):
                self._sleep(CONFIRM_WAIT_S)
                if self._read() is True:
                    log.info("Desvio de obstáculo ligado.")
                    self._counters["obstacle_avoidance_switched_on"] += 1
                    return True
        log.warning("Desvio de obstáculo não confirmado como ligado.")
        self._counters["obstacle_avoidance_unconfirmed"] += 1
        return False

    def _read(self) -> bool | None:
        """`enabled` informado pela API; None se ela não soube ou não respondeu."""
        try:
            with urllib.request.urlopen(self._url, timeout=TIMEOUT_S) as response:
                answer = json.load(response)
        except (OSError, ValueError) as error:  # inclui HTTPError e timeout
            log.warning("Desvio de obstáculo: a leitura falhou (%s).", error)
            return None
        enabled = answer.get("enabled") if isinstance(answer, dict) else None
        if not isinstance(enabled, bool):
            # A API não reconheceu a resposta do robô: `raw` mostra o que veio.
            log.warning("Desvio de obstáculo: estado não reconhecido, resposta %r.", answer)
            return None
        return enabled

    def _switch_on(self) -> bool:
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"enabled": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S):
                return True
        except OSError as error:
            log.warning("Desvio de obstáculo: o pedido para ligar falhou (%s).", error)
            return False
