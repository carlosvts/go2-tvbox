#!/usr/bin/env python3
"""Gera as frases de `config/commands.json` a partir de `config/phrases.json`.

Uso:
    python scripts/generate_commands.py          # reescreve o commands.json
    python scripts/generate_commands.py --check  # só confere; sai com 1 se mudaria

`phrases.json` tem listas curtas; o produto delas vira a gramática:

    [prefixo] frase de ação [sufixo]
    [prefixo] verbo ligação direção [sufixo]

A seção `commands` do `commands.json` (cópia de `GET /capabilities`) não é
tocada. "[unk]" não entra no arquivo: quem o acrescenta à gramática do Vosk é
`sense/recognizer.py`.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PHRASES_FILE = REPO_ROOT / "config" / "phrases.json"
COMMANDS_FILE = REPO_ROOT / "config" / "commands.json"


def generate(source: dict) -> tuple[dict, list[str]]:
    """Devolve (`phrases`, `stop_words`) como vão para o `commands.json`."""

    def variants(core: str) -> list[str]:
        return [
            " ".join(part for part in (prefix, core, suffix) if part)
            for prefix in source["prefixos"]
            for suffix in source["sufixos"]
        ]

    phrases: dict = {}

    def add(core: str, target: str | dict) -> None:
        for phrase in variants(core):
            if phrase in phrases and phrases[phrase] != target:
                raise ValueError(f"frase {phrase!r} gerada para dois comandos diferentes")
            phrases[phrase] = target

    stop = source["parada"]
    for word in stop["palavras"]:
        add(word, stop["comando"])
    for phrase, command in source["acoes"].items():
        add(phrase, command)

    moves = source["movimentos"]
    for verb, directions in moves["verbos"].items():
        for direction, args in directions.items():
            target = {"command": moves["comando"], "args": {**moves["padrao"], **args}}
            for link in source["direcoes"][direction]:
                add(f"{verb} {link} {direction}", target)
    return phrases, list(stop["palavras"])


def render(phrases: dict, stop_words: list[str], commands: dict) -> str:
    """O `commands.json` com uma entrada por linha, para o diff ficar legível."""

    def block(entries: dict) -> str:
        lines = [
            f"    {json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in entries.items()
        ]
        return "{\n" + ",\n".join(lines) + "\n  }"

    return (
        "{\n"
        f'  "stop_words": {json.dumps(stop_words, ensure_ascii=False)},\n'
        f'  "phrases": {block(phrases)},\n'
        f'  "commands": {block(commands)}\n'
        "}\n"
    )


def main() -> int:
    source = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))
    current = COMMANDS_FILE.read_text(encoding="utf-8")
    phrases, stop_words = generate(source)
    new = render(phrases, stop_words, json.loads(current)["commands"])
    if "--check" in sys.argv:
        if new != current:
            print(f"ERRO: {COMMANDS_FILE} está desatualizado; rode o script sem --check.")
            return 1
        print(f"OK: {len(phrases)} frases conferem com {PHRASES_FILE.name}.")
        return 0
    COMMANDS_FILE.write_text(new, encoding="utf-8")
    print(f"{COMMANDS_FILE}: {len(phrases)} frases geradas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
