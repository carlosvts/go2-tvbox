#!/usr/bin/env python3
"""Garante um silêncio no começo das falas de `media/`, sem sintetizar de novo.

Alto-falantes USB como o Anker demoram um instante para "acordar" quando o
`aplay` abre o dispositivo e cortam o começo do som. Este script acrescenta
silêncio no início de cada fala até ela ter pelo menos `--ms` de silêncio antes
da voz. Pode rodar mais de uma vez: só completa o que falta, não empilha.

    uv run python scripts/add_lead_silence.py                 # 250 ms em media/
    uv run python scripts/add_lead_silence.py --ms 400        # mais folga
    uv run python scripts/add_lead_silence.py --dry-run       # só mostra

Pula o `beep*.wav` (o beep da wake word não é fala; scripts/generate_beeps.py já
coloca o silêncio inicial nele). Guarde uma cópia antes se
quiser poder voltar: `cp -r media media.bak`.
"""

import argparse
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
SPOKEN = ("processing", "confirmed", "rejected", "unconfirmed")
SILENCE_LEVEL = 0.01  # abaixo disto (cerca de -40 dBFS) conta como silêncio


def is_spoken(path: Path) -> bool:
    return any(re.fullmatch(rf"{name}(_\d+)?\.wav", path.name) for name in SPOKEN)


def leading_silence_s(x: np.ndarray, rate: int) -> float:
    loud = np.flatnonzero(np.abs(x.astype(np.float64)) / 32768 > SILENCE_LEVEL)
    return (loud[0] if len(loud) else len(x)) / rate


def pad_file(path: Path, target_ms: float, dry_run: bool) -> str:
    with wave.open(str(path)) as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2 or channels != 1:
        return f"{path.name}: pulado (esperado mono PCM de 16 bits)"
    x = np.frombuffer(frames, dtype=np.int16)
    have_s = leading_silence_s(x, rate)
    missing = max(0, int(round((target_ms / 1000 - have_s) * rate)))
    if missing == 0:
        return f"{path.name}: já tem {have_s * 1000:.0f} ms de silêncio, nada a fazer"
    if not dry_run:
        padded = np.concatenate([np.zeros(missing, dtype=np.int16), x])
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        with wave.open(str(tmp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(padded.tobytes())
        tmp_path.replace(path)  # troca de uma vez: nunca fica um arquivo pela metade
    verb = "acrescentaria" if dry_run else "acrescentei"
    return f"{path.name}: tinha {have_s * 1000:.0f} ms, {verb} {missing / rate * 1000:.0f} ms"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=MEDIA_DIR)
    parser.add_argument("--ms", type=float, default=250.0, help="silêncio mínimo antes da voz")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    files = sorted(p for p in args.dir.glob("*.wav") if is_spoken(p))
    if not files:
        raise SystemExit(f"Nenhuma fala (processing/confirmed/rejected/unconfirmed) em {args.dir}")
    for path in files:
        print(pad_file(path, args.ms, args.dry_run))


if __name__ == "__main__":
    main()
