"""Toca os sons de retorno no Anker sem ligar o pipeline, para testar as falas.

    uv run python -m sense.say processing confirmed
    uv run python -m sense.say --all                  # beep e as quatro falas, uma de cada vez
    uv run python -m sense.say confirmed --repeat 6   # confere o sorteio de variantes
    uv run python -m sense.say rejected --card 1      # sem detectar o card pelo nome

Usa o mesmo `SoundPlayer` do serviço, então o que se ouve aqui é o que o
serviço toca. Não abre o microfone: dá para rodar com o `go2-sense` ligado.
"""

import argparse
import logging
import sys
import time

from sense.audio import SoundPlayer, alsa_card

SPOKEN = ("processing", "confirmed", "rejected", "unconfirmed")


def find_card(mic_name: str) -> int | None:
    """Card ALSA do dispositivo cujo nome tem `mic_name`, sem abrir nenhum stream."""
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        for index in range(pa.get_device_count()):
            name = pa.get_device_info_by_index(index)["name"]
            card = alsa_card(name)
            if mic_name.lower() in name.lower() and card is not None:
                return card
    finally:
        pa.terminate()
    return None


def main(argv: list[str] | None = None, player: SoundPlayer | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="sons a tocar, ex.: processing confirmed")
    parser.add_argument("--all", action="store_true", help="toca beep e as quatro falas")
    parser.add_argument("--repeat", type=int, default=1, help="quantas vezes tocar cada som")
    parser.add_argument("--card", type=int, help="número do card ALSA (padrão: detecta pelo nome do mic)")
    parser.add_argument("--mic", default="anker", help="parte do nome do dispositivo, para achar o card")
    parser.add_argument("--gap-s", type=float, default=0.5, help="pausa entre um som e o próximo")
    args = parser.parse_args(argv)

    names = (["beep", *SPOKEN] if args.all else []) + args.names
    if not names:
        parser.error("diga quais sons tocar, ou use --all")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    card = args.card if args.card is not None else find_card(args.mic)
    if card is None:
        print(f"Não achei um dispositivo com {args.mic!r} no nome. Use --card N (veja `aplay -l`).", file=sys.stderr)
        return 1
    player = player or SoundPlayer()
    for name in names:
        for _ in range(args.repeat):
            player.play(card, name)
            player.wait_idle()  # um de cada vez: a fila do serviço descartaria os excedentes
            time.sleep(args.gap_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
