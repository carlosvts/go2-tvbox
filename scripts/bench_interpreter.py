#!/usr/bin/env python3
"""Mede o tempo do interpretador: 1000 chamadas sobre frases variadas.

Uso (na TV Box, com o extra `recognition` instalado):
    python scripts/bench_interpreter.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sense.interpreter import Interpreter  # noqa: E402

CALLS = 1000
PHRASES = [
    "por favor anda para frente agora",
    "vira pra esquerda",
    "caminha pra trás",
    "robô senta aí",
    "para",
    "não anda para frente",
    "qual é o seu nome",
    "anda para frente e vira à direita",
    "desligar os motores",
    "hoje o dia está bonito e eu queria saber que horas são agora",
]

interpreter = Interpreter.load()
times_ms = []
for i in range(CALLS):
    start = time.perf_counter()
    interpreter.interpret(PHRASES[i % len(PHRASES)])
    times_ms.append((time.perf_counter() - start) * 1000)

times_ms.sort()
print(
    f"{CALLS} chamadas: média {sum(times_ms) / CALLS:.3f} ms | "
    f"p99 {times_ms[int(CALLS * 0.99)]:.3f} ms | máx {times_ms[-1]:.3f} ms"
)
