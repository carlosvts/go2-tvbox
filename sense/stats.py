"""Contadores de diagnóstico, impressos no log a cada `STATS_INTERVAL_S`.

Os módulos incrementam `stats.counters["nome"]`; o laço principal chama
`maybe_log()` a cada chunk. A linha traz também CPU e pico de RAM do processo,
para as medições de laboratório saírem direto do log.
"""

import logging
import resource
import time
from collections import Counter
from collections.abc import Callable

log = logging.getLogger(__name__)

STATS_INTERVAL_S = 60.0


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class Stats:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.counters: Counter = Counter()
        self._clock = clock
        self._last_log = clock()
        self._last_cpu = _cpu_seconds()

    def maybe_log(self) -> None:
        """Loga os contadores acumulados se já passou o intervalo."""
        now = self._clock()
        elapsed = now - self._last_log
        if elapsed < STATS_INTERVAL_S:
            return
        cpu = _cpu_seconds()
        # ru_maxrss vem em KB no Linux e é o pico desde o início do processo.
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        counters = " ".join(f"{name}={value}" for name, value in sorted(self.counters.items()))
        log.info(
            "Diagnóstico: cpu=%.0f%% ram_pico=%.0fMB | %s",
            100 * (cpu - self._last_cpu) / elapsed, peak_rss_mb, counters or "sem eventos",
        )
        self._last_log = now
        self._last_cpu = cpu
