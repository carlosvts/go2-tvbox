"""Hashmap frase → comando, lido de `config/commands.json`.

O arquivo tem duas partes: `phrases` (frase em PT-BR → comando) e `commands`
(cópia das entradas de `GET /capabilities` da go2-api que o Sense usa).
`scripts/validate_commands.py` confere a segunda parte contra a API.

Uma frase aponta para o nome do comando ou, se o comando recebe números (caso
do `move`), para `{"command": nome, "args": {campo: valor}}` com valores fixos.
"""

import json
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from sense.config import REPO_ROOT

COMMANDS_FILE = REPO_ROOT / "config" / "commands.json"

# Corpos que o Sense monta sozinho: nenhum, ou só `{"cmd": <nome>}`. Qualquer
# outro comando precisa dos valores em `args`, na frase.
PARAMS_WITHOUT_ARGS = ([], ["cmd"])


class CommandsError(Exception):
    """`commands.json` inconsistente. A mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Command:
    name: str
    method: str
    endpoint: str
    sport_cmd: str
    params: tuple[str, ...]
    args: tuple[tuple[str, float], ...] = ()  # valores fixos, na ordem de `params`

    @property
    def body(self) -> dict[str, str] | dict[str, float] | None:
        """Corpo JSON do pedido, ou None se a rota não recebe corpo."""
        if self.args:
            return dict(self.args)
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
        commands[name] = Command(
            name=name,
            method=entry["method"],
            endpoint=entry["endpoint"],
            sport_cmd=entry["sport_cmd"],
            params=tuple(entry["params"]),
        )

    by_phrase: dict[str, Command] = {}
    for phrase, target in data["phrases"].items():
        key = normalize(phrase)
        name, args = (target["command"], target["args"]) if isinstance(target, dict) else (target, {})
        if name not in commands:
            problems.append(f"frase {phrase!r} aponta para comando inexistente {name!r}")
            continue
        command = commands[name]
        if args:
            numeric = all(type(value) in (int, float) for value in args.values())
            if sorted(args) != sorted(command.params) or not numeric:
                problems.append(
                    f"frase {phrase!r}: args devem ser números para {list(command.params)}"
                )
                continue
            command = replace(command, args=tuple((p, float(args[p])) for p in command.params))
        elif list(command.params) not in PARAMS_WITHOUT_ARGS:
            problems.append(
                f"frase {phrase!r}: comando {name!r} exige args para {list(command.params)}"
            )
            continue
        if key in by_phrase:
            problems.append(f"frase {phrase!r} repetida (iguais depois de normalizar)")
        else:
            by_phrase[key] = command

    if problems:
        raise CommandsError(f"{path} inválido:\n" + "\n".join(f"  - {p}" for p in problems))
    return CommandMap(by_phrase=by_phrase, grammar=tuple(p.lower() for p in data["phrases"]))
