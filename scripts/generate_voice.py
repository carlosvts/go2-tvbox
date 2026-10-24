#!/usr/bin/env python3
"""Gera as falas de retorno da TV Box em `media/` com o Piper (voz pt-BR).

Roda no PC, não na TV Box: a TV Box só toca os WAVs prontos com o `aplay`, sem
síntese em tempo de execução (zero CPU a mais, uns 0,5 MB de disco).

    pip install piper-tts numpy
    python -m piper.download_voices pt_BR-faber-medium --download-dir voices
    python scripts/generate_voice.py --voice voices/pt_BR-faber-medium.onnx
    scp media/*.wav minipc@aml-s9xx-box:~/go2-tvbox/media/

Cada som pode ter várias frases. A primeira vira `<nome>.wav` e as outras
`<nome>_2.wav`, `<nome>_3.wav`...; a TV Box sorteia uma a cada vez, sem repetir
a anterior (ver `SoundPlayer` em `sense/audio.py`).

O que cada som significa no código (para escolher frases que não mintam):
  processing   o áudio acabou de ser enviado ao servidor; ainda não há resposta.
  confirmed    o servidor ACEITOU o comando (status enqueued/stopped...). O robô
               ainda não o executou, então nada de "pronto" ou "feito".
  rejected     o servidor recusou, ou a wake word veio sem fala depois. A TV Box
               volta a esperar a wake word, então a frase manda chamar de novo.
  unconfirmed  nenhuma resposta do servidor (timeout ou erro de rede).

O `beep` da wake word NÃO é gerado aqui, de propósito: ele toca durante a escuta
e entra no áudio enviado ao servidor. Uma frase ali seria transcrita junto com o
comando (um "não" viraria negação e rejeitaria a frase toda).
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

MEDIA_DIR = Path(__file__).resolve().parent.parent / "media"

PHRASES: dict[str, list[str]] = {
    "processing": ["Um instante.", "Hum, deixa eu ver.", "Só um segundo."],
    "confirmed": ["Entendi!", "Certo!", "Beleza!", "Pode deixar!"],
    "rejected": [
        "Hum, não entendi. Me chama de novo.",
        "Desculpa, não peguei essa. Me chama de novo.",
    ],
    "unconfirmed": [
        "Não tive resposta do servidor.",
        "Hum, não consegui confirmar. Tenta de novo.",
    ],
}

# Acima disto a frase passa a atrapalhar: `processing` não pode ficar mais
# longa que a espera típica pela resposta, senão `confirmed` entra atrasado.
MAX_DURATION_S = {"processing": 1.3, "confirmed": 1.0, "rejected": 2.8, "unconfirmed": 2.8}

TRIM_DB = -40.0  # silêncio = abaixo disto em relação ao pico
LEAD_PAD_S, TAIL_PAD_S = 0.04, 0.08
FADE_IN_S, FADE_OUT_S = 0.01, 0.03
PEAK_LIMIT = 0.89  # -1 dBFS


def synthesize(voice: Path, text: str, out: Path, length_scale: float) -> None:
    """Chama o Piper (linha de comando); o texto vai pela entrada padrão."""
    command = [
        sys.executable, "-m", "piper",
        "-m", str(voice),
        "-f", str(out),
        "--length-scale", str(length_scale),
        "--sentence-silence", "0.1",
    ]
    result = subprocess.run(command, input=text, text=True, capture_output=True)
    if result.returncode != 0 or not out.exists():
        raise SystemExit(f"Piper falhou em {text!r}:\n{result.stderr.strip()[-600:]}")


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path)) as wav:
        if wav.getsampwidth() != 2:
            raise SystemExit(f"{path}: esperado PCM de 16 bits")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        if wav.getnchannels() > 1:
            data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
        return wav.getframerate(), data.astype(np.float64) / 32768.0


def postprocess(x: np.ndarray, rate: int, target_rms_db: float) -> np.ndarray:
    """Tira o silêncio das pontas, aplica fade e deixa a fala no volume pedido.

    O volume é pelo RMS (o que o ouvido percebe), com um limitador suave no pico
    para não estourar o alto-falante.
    """
    if not len(x) or np.max(np.abs(x)) == 0:
        raise SystemExit("O Piper devolveu áudio vazio.")
    voiced = np.flatnonzero(np.abs(x) > np.max(np.abs(x)) * 10 ** (TRIM_DB / 20))
    start = max(voiced[0] - int(LEAD_PAD_S * rate), 0)
    end = min(voiced[-1] + int(TAIL_PAD_S * rate), len(x))
    y = x[start:end].copy()

    fade_in, fade_out = int(FADE_IN_S * rate), int(FADE_OUT_S * rate)
    y[:fade_in] *= np.linspace(0, 1, fade_in)
    y[-fade_out:] *= np.linspace(1, 0, fade_out)

    rms = np.sqrt(np.mean(y**2))
    y = y * (10 ** (target_rms_db / 20) / rms)
    return PEAK_LIMIT * np.tanh(y / PEAK_LIMIT)  # linear nos sons baixos, achata só os picos


def with_lead(y: np.ndarray, rate: int, lead_ms: float) -> np.ndarray:
    """Acrescenta `lead_ms` de silêncio no começo."""
    return np.concatenate([np.zeros(int(rate * lead_ms / 1000)), y]) if lead_ms > 0 else y


def write_wav(path: Path, rate: int, y: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


def filename(name: str, index: int) -> str:
    return f"{name}.wav" if index == 0 else f"{name}_{index + 1}.wav"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", type=Path, help="modelo .onnx do Piper (ex.: voices/pt_BR-faber-medium.onnx)")
    parser.add_argument("--out", type=Path, default=MEDIA_DIR, help="pasta de saída (padrão: media/ do repo)")
    parser.add_argument("--only", nargs="+", choices=sorted(PHRASES), help="gera só estes sons")
    parser.add_argument("--target-rms-db", type=float, default=-16.0, help="volume médio da fala (dBFS); maior = mais alto")
    parser.add_argument("--length-scale", type=float, default=1.0, help="velocidade do Piper (menor = mais rápido)")
    parser.add_argument(
        "--lead-silence-ms",
        type=float,
        default=250.0,
        help="silêncio extra no início de cada arquivo; o alto-falante USB do Anker corta o começo "
        "do som sem isso (use 0 para desligar)",
    )
    parser.add_argument("--dry-run", action="store_true", help="só lista o que seria gerado")
    parser.add_argument(
        "--preview",
        metavar="TEXTO",
        help="sintetiza só esta frase e grava preview_raw.wav (saída crua do Piper) e "
        "preview.wav (depois do pós-processamento) na pasta atual, para comparar de ouvido",
    )
    args = parser.parse_args()

    names = args.only or list(PHRASES)
    if args.dry_run:
        for name in names:
            for i, text in enumerate(PHRASES[name]):
                print(f"{filename(name, i):<18} {text}")
        return
    if args.voice is None or not args.voice.exists():
        raise SystemExit(
            "Informe --voice com o .onnx do Piper. Para baixar a voz:\n"
            "  python -m piper.download_voices pt_BR-faber-medium --download-dir voices"
        )

    if args.preview:
        raw = Path("preview_raw.wav")
        synthesize(args.voice, args.preview, raw, args.length_scale)
        rate, x = read_wav(raw)
        y = postprocess(x, rate, args.target_rms_db)
        write_wav(Path("preview.wav"), rate, with_lead(y, rate, args.lead_silence_ms))
        print(f"{args.preview!r}: {len(x) / rate:.2f} s crua a {rate} Hz. Ouça preview_raw.wav e preview.wav.")
        return

    # Tudo é gerado numa pasta temporária e só então copiado: se o Piper falhar
    # no meio, os sons que já estão em media/ continuam como estavam.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        report = []
        for name in names:
            for i, text in enumerate(PHRASES[name]):
                raw = tmp_dir / "raw.wav"
                synthesize(args.voice, text, raw, args.length_scale)
                rate, x = read_wav(raw)
                y = with_lead(postprocess(x, rate, args.target_rms_db), rate, args.lead_silence_ms)
                write_wav(tmp_dir / filename(name, i), rate, y)
                duration = len(y) / rate
                warn = "  <- LONGA para este som" if duration > MAX_DURATION_S[name] else ""
                rms_db = 20 * np.log10(np.sqrt(np.mean(y**2)))
                peak_db = 20 * np.log10(np.max(np.abs(y)))
                report.append(f"{filename(name, i):<18} {duration:>4.2f} s  rms {rms_db:>6.1f}  pico {peak_db:>5.1f} dBFS  {text}{warn}")
        args.out.mkdir(parents=True, exist_ok=True)
        for name in names:
            for old in args.out.glob(f"{name}*.wav"):
                if re.fullmatch(rf"{re.escape(name)}(_\d+)?\.wav", old.name):
                    old.unlink()
            for i in range(len(PHRASES[name])):
                shutil.copy(tmp_dir / filename(name, i), args.out / filename(name, i))
    print("\n".join(report))
    print(f"\nGravado em {args.out}. O beep da wake word não foi tocado.")


if __name__ == "__main__":
    main()
