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
INTERPRETER_FILE = REPO_ROOT / "config" / "interpreter.json"

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


def _api_commands(data: dict) -> dict[str, Command]:
    return {
        name: Command(
            name=name,
            method=entry["method"],
            endpoint=entry["endpoint"],
            sport_cmd=entry["sport_cmd"],
            params=tuple(entry["params"]),
        )
        for name, entry in data["commands"].items()
    }


def _resolve(
    label: str, target: str | dict, commands: dict[str, Command], problems: list[str]
) -> Command | None:
    """Comando de um alvo: o nome dele, ou `{"command": nome, "args": {...}}`."""
    name, args = (target["command"], target["args"]) if isinstance(target, dict) else (target, {})
    if name not in commands:
        problems.append(f"{label} aponta para comando inexistente {name!r}")
        return None
    command = commands[name]
    if args:
        numeric = all(type(value) in (int, float) for value in args.values())
        if sorted(args) != sorted(command.params) or not numeric:
            problems.append(f"{label}: args devem ser números para {list(command.params)}")
            return None
        return replace(command, args=tuple((p, float(args[p])) for p in command.params))
    if list(command.params) not in PARAMS_WITHOUT_ARGS:
        problems.append(f"{label}: comando {name!r} exige args para {list(command.params)}")
        return None
    return command


def _fail_if(problems: list[str], path: Path) -> None:
    if problems:
        raise CommandsError(f"{path} inválido:\n" + "\n".join(f"  - {p}" for p in problems))


def load_commands(path: Path = COMMANDS_FILE) -> CommandMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    commands = _api_commands(data)

    by_phrase: dict[str, Command] = {}
    for phrase, target in data["phrases"].items():
        key = normalize(phrase)
        command = _resolve(f"frase {phrase!r}", target, commands, problems)
        if command is None:
            continue
        if key in by_phrase:
            problems.append(f"frase {phrase!r} repetida (iguais depois de normalizar)")
        else:
            by_phrase[key] = command

    _fail_if(problems, path)
    return CommandMap(by_phrase=by_phrase, grammar=tuple(p.lower() for p in data["phrases"]))


def load_intents(
    path: Path = INTERPRETER_FILE, commands_path: Path = COMMANDS_FILE
) -> dict[str, Command]:
    """ID do interpretador → comando, da seção `comandos` de `interpreter.json`.

    Toda ação e a parada precisam de um comando. Combinações de verbo e
    direção podem ficar de fora (não existe "girar para frente").
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = _api_commands(json.loads(commands_path.read_text(encoding="utf-8")))
    problems: list[str] = []

    required = {data["parada"]["id"], *data["acoes"]}
    movements = {f"{verb}_{direction}" for verb in data["verbos"] for direction in data["direcoes"]}
    by_intent: dict[str, Command] = {}
    for intent, target in data["comandos"].items():
        if intent not in required | movements:
            problems.append(f"comando {intent!r} não é um ID que o interpretador produz")
            continue
        command = _resolve(f"comando {intent!r}", target, commands, problems)
        if command is not None:
            by_intent[intent] = command
    for intent in sorted(required - set(data["comandos"])):
        problems.append(f"{intent!r} não tem entrada em `comandos`")

    _fail_if(problems, path)
    return by_intent
