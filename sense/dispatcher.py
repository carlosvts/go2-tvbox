"""Entrega de um comando reconhecido à go2-api.

Uma tentativa por comando, sem fila e sem retry: um comando de voz que chega
atrasado ao robô é pior do que um que não chega. Tudo que é descartado vira
log e contador.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable

from sense.commands import Command

log = logging.getLogger(__name__)

POST_TIMEOUT_S = 2.0


class Dispatcher:
    def __init__(
        self,
        api_url: str,
        cooldown_s: float,
        counters: Counter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_url = api_url
        self._cooldown_s = cooldown_s
        self._counters = counters
        self._clock = clock
        self._last: Command | None = None
        self._last_time = 0.0

    def dispatch(self, command: Command) -> bool:
        """Envia o comando. Devolve True só se a API respondeu 202."""
        now = self._clock()
        if command == self._last and now - self._last_time < self._cooldown_s:
            log.info(
                "Descartado: %s repetido em %.1fs (cooldown de %.1fs).",
                command.name, now - self._last_time, self._cooldown_s,
            )
            self._counters["cooldown_discards"] += 1
            return False

        url = self._api_url + command.endpoint
        body = command.body
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=command.method,
        )
        try:
            with urllib.request.urlopen(request, timeout=POST_TIMEOUT_S) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            # A API respondeu, mas recusou: 422 (pedido inválido) ou 503 (API
            # viva, sem conexão com o robô). O corpo traz o motivo em `detail`.
            detail = error.read().decode(errors="replace")[:200]
            log.warning(
                "Descartado: %s recusado pela API (HTTP %d): %s", command.name, error.code, detail
            )
            self._counters["api_rejections"] += 1
            return False
        except OSError as error:
            # Inclui conexão recusada, DNS e timeout: a API não respondeu.
            log.warning("Descartado: %s não enviado, API fora do ar (%s).", command.name, error)
            self._counters["api_unreachable"] += 1
            return False

        if status != 202:
            log.warning("Descartado: %s teve resposta inesperada (HTTP %d).", command.name, status)
            self._counters["api_rejections"] += 1
            return False

        # O cooldown só conta a partir de um envio aceito: se a API estava fora,
        # repetir a frase é uma nova tentativa, não uma duplicata.
        self._last = command
        self._last_time = now
        log.info(
            "Enviado: %s → %s %s (202, %.0f ms).",
            command.name, command.method, command.endpoint, (self._clock() - now) * 1000,
        )
        self._counters["commands_sent"] += 1
        return True
