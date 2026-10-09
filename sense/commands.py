"""Hashmap frase → comando, lido de `config/commands.json`.

O arquivo tem duas partes: `phrases` (frase em PT-BR → nome do comando) e
`commands` (cópia das entradas de `GET /capabilities` da go2-api que o Sense
usa). `scripts/validate_commands.py` confere a segunda parte contra a API.
"""

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sense.config import REPO_ROOT

COMMANDS_FILE = REPO_ROOT / "config" / "commands.json"

# Corpos que o Sense sabe montar: nenhum, ou só `{"cmd": <nome>}`. Comandos com
# parâmetros numéricos (`move`, `speed`) ficam de fora de propósito.
SUPPORTED_PARAMS = ([], ["cmd"])


class CommandsError(Exception):
    """`commands.json` inconsistente. A mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Command:
    name: str
    method: str
    endpoint: str
    sport_cmd: str
    params: tuple[str, ...]

    @property
    def body(self) -> dict[str, str] | None:
        """Corpo JSON do pedido, ou None se a rota não recebe corpo."""
        return {"cmd": self.name} if self.params else None


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação, espaços simples."""
    nfd = unicodedata.normalize("NFD", text.lower())
    s = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return " ".join(s.split())


@dataclass(frozen=True)
class CommandMap:
    by_phrase: dict[str, Command]  # chave: frase normalizada
    grammar: tuple[str, ...]  # frases como escritas no arquivo, para o STT

    def lookup(self, text: str) -> Command | None:
        return self.by_phrase.get(normalize(text))


def load_commands(path: Path = COMMANDS_FILE) -> CommandMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    commands: dict[str, Command] = {}
    for name, entry in data["commands"].items():
        if entry["params"] not in SUPPORTED_PARAMS:
            problems.append(f"comando {name!r}: params {entry['params']} não suportados")
        commands[name] = Command(
            name=name,
            method=entry["method"],
            endpoint=entry["endpoint"],
            sport_cmd=entry["sport_cmd"],
            params=tuple(entry["params"]),
        )

    by_phrase: dict[str, Command] = {}
    for phrase, name in data["phrases"].items():
        key = normalize(phrase)
        if name not in commands:
            problems.append(f"frase {phrase!r} aponta para comando inexistente {name!r}")
        elif key in by_phrase:
            problems.append(f"frase {phrase!r} repetida (iguais depois de normalizar)")
        else:
            by_phrase[key] = commands[name]

    if problems:
        raise CommandsError(f"{path} inválido:\n" + "\n".join(f"  - {p}" for p in problems))
    return CommandMap(by_phrase=by_phrase, grammar=tuple(p.lower() for p in data["phrases"]))
