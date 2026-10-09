#!/usr/bin/env python3
"""Gera os sons de retorno do fallback em `media/` (tons simples, sem dependências).

Uso: python scripts/generate_sounds.py

Todos duram menos que os 0,9 s de áudio descartados após a wake word.
"""

import math
import struct
import wave
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
RATE = 24000
VOLUME = 0.35
FADE_S = 0.01

# nome → sequência de (frequência em Hz, duração em s); 0 Hz = pausa.
SOUNDS = {
    "processing": [(660, 0.09), (0, 0.04), (880, 0.09)],  # sobe: "entendi, processando"
    "confirmed": [(880, 0.09), (0, 0.04), (1320, 0.16)],  # sobe mais: confirmado
    "rejected": [(330, 0.30)],  # grave: "não entendi"
    "unconfirmed": [(550, 0.12), (0, 0.06), (550, 0.12)],  # dois iguais: não confirmado
}


def tone(frequency: float, duration_s: float) -> list[int]:
    count = int(RATE * duration_s)
    fade = int(RATE * FADE_S)
    samples = []
    for i in range(count):
        envelope = min(1.0, i / fade, (count - i) / fade) if frequency else 0.0
        value = VOLUME * envelope * math.sin(2 * math.pi * frequency * i / RATE)
        samples.append(int(value * 32767))
    return samples


for name, notes in SOUNDS.items():
    samples = [sample for frequency, duration_s in notes for sample in tone(frequency, duration_s)]
    with wave.open(str(MEDIA_DIR / f"{name}.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    print(f"{name}.wav: {len(samples) / RATE:.2f} s")
