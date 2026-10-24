#!/usr/bin/env python3
"""Gera o som de "estou ouvindo" da wake word (`beep*.wav`) em `media/`.

Sem dependências além do numpy e sem Piper: roda em qualquer lugar, inclusive na
própria TV Box.

    python scripts/generate_beeps.py             # grava media/beep.wav e beep_2..4.wav
    python scripts/generate_beeps.py --dry-run   # só mostra o que gravaria

Por que um sino curto e não uma frase: o beep toca durante a escuta e o microfone
o capta, então ele entra no áudio enviado ao servidor. Uma palavra ali pode ser
transcrita junto com o comando (um "não" viraria negação e rejeitaria a frase
toda); um sino não vira texto. Por isso também é curto: o silêncio inicial mais o
som precisam acabar antes de `BEEP_IGNORE_S` (0,9 s), e a cauda é desenhada para
cair abaixo do limiar de voz do VAD logo depois do ataque.

Cada variante é um par de notas subindo ("pronto, pode falar"). A TV Box sorteia
uma a cada wake word, sem repetir a anterior (ver `SoundPlayer` em `sense/audio.py`).
"""

import argparse
import re
import tempfile
import wave
from pathlib import Path

import numpy as np

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"
RATE = 24000

# (primeira nota, segunda nota) em Hz. Sempre sobe: soa como "pronto", não "erro".
PAIRS = [
    (659.3, 987.8),   # mi5 → si5, quinta
    (784.0, 1046.5),  # sol5 → dó6, quarta
    (587.3, 880.0),   # ré5 → lá5, quinta
    (523.3, 783.99),  # dó5 → sol5, quinta
]
NOTE_GAP_S = 0.11  # entre o começo de uma nota e o da seguinte
NOTE_DECAY_S = 0.07  # constante de tempo da queda: o som "bate" e some
ATTACK_S = 0.004
SOUND_S = 0.46  # duração do som, sem o silêncio inicial
# Harmônicos (amplitude relativa): dão o timbre de sino em vez de apito puro.
PARTIALS = [(1.0, 1.0), (2.0, 0.35), (3.0, 0.12)]

LEAD_SILENCE_MS = 250.0  # o alto-falante USB do Anker corta o começo sem isto
TARGET_RMS_DB = -15.0  # medido só no trecho com som
PEAK_LIMIT = 0.89  # -1 dBFS
VAD_FLOOR = 150 / 32768  # piso de voz do VAD (`vad_min_rms`); o sino deve cair abaixo


def chime(first_hz: float, second_hz: float) -> np.ndarray:
    """Duas notas de sino, a segunda entrando por cima da cauda da primeira."""
    t = np.arange(int(RATE * SOUND_S)) / RATE
    x = np.zeros_like(t)
    for onset_s, hz in ((0.0, first_hz), (NOTE_GAP_S, second_hz)):
        local = t - onset_s
        active = local >= 0
        envelope = np.where(active, np.exp(-np.clip(local, 0, None) / NOTE_DECAY_S), 0.0)
        envelope *= np.where(active, np.minimum(local / ATTACK_S, 1.0), 0.0)
        note = sum(gain * np.sin(2 * np.pi * hz * k * local) for k, gain in PARTIALS)
        x += envelope * note
    fade = int(0.10 * RATE)  # a cauda se apaga de vez antes do fim do arquivo
    x[-fade:] *= np.linspace(1, 0, fade)
    return x


def finish(x: np.ndarray) -> np.ndarray:
    """Leva o sino ao volume alvo (pelo RMS) com limitador suave e põe o silêncio inicial."""
    x = x * (10 ** (TARGET_RMS_DB / 20) / np.sqrt(np.mean(x**2)))
    x = PEAK_LIMIT * np.tanh(x / PEAK_LIMIT)
    return np.concatenate([np.zeros(int(RATE * LEAD_SILENCE_MS / 1000)), x])


def audible_until_s(y: np.ndarray, window_s: float = 0.02) -> float:
    """Fim da última janela de 20 ms cujo RMS passa do piso de voz do VAD.

    O VAD mede o RMS por chunk, não por amostra, então é essa a conta que importa.
    """
    window = int(RATE * window_s)
    last = 0.0
    for start in range(0, len(y) - window + 1, window):
        if np.sqrt(np.mean(y[start : start + window] ** 2)) > VAD_FLOOR:
            last = (start + window) / RATE
    return last


def filename(index: int) -> str:
    return "beep.wav" if index == 0 else f"beep_{index + 1}.wav"


def write_wav(path: Path, y: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=MEDIA_DIR, help="pasta de saída (padrão: media/ do repo)")
    parser.add_argument("--dry-run", action="store_true", help="só lista o que seria gravado")
    args = parser.parse_args()

    sounds = [finish(chime(*pair)) for pair in PAIRS]
    for i, y in enumerate(sounds):
        rms_db = 20 * np.log10(np.sqrt(np.mean(y[int(RATE * LEAD_SILENCE_MS / 1000):] ** 2)))
        print(
            f"{filename(i):<12} {len(y) / RATE:.2f} s  audível até {audible_until_s(y):.2f} s  "
            f"rms {rms_db:.1f}  pico {20 * np.log10(np.max(np.abs(y))):.1f} dBFS  {PAIRS[i][0]:.0f}→{PAIRS[i][1]:.0f} Hz"
        )
    if args.dry_run:
        return

    # Grava numa pasta temporária e só então troca: uma falha no meio não deixa
    # `media/` com metade dos beeps.
    with tempfile.TemporaryDirectory() as tmp:
        for i, y in enumerate(sounds):
            write_wav(Path(tmp) / filename(i), y)
        args.out.mkdir(parents=True, exist_ok=True)
        for old in args.out.glob("beep*.wav"):
            if re.fullmatch(r"beep(_\d+)?\.wav", old.name):
                old.unlink()
        for i in range(len(sounds)):
            (args.out / filename(i)).write_bytes((Path(tmp) / filename(i)).read_bytes())
    print(f"\nGravado em {args.out}.")


if __name__ == "__main__":
    main()
