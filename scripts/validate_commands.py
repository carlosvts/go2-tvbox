#!/usr/bin/env python3
"""Confere `config/commands.json` contra `GET /capabilities` da go2-api.

Uso:
    python scripts/validate_commands.py [URL_DA_API]

Sem argumento, usa GO2_API_URL (do ambiente ou do `.env`). Sai com 0 se todo
comando do arquivo existe na API com os mesmos campos; com 1 se houver entrada
órfã ou divergente, ou se a API não responder.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("method", "endpoint", "sport_cmd", "params")


def find_problems(data: dict, capabilities: list[dict]) -> list[str]:
    """Lista as entradas de `data` que não batem com `capabilities`."""
    by_name = {capability["name"]: capability for capability in capabilities}
    problems: list[str] = []
    for name, entry in data["commands"].items():
        capability = by_name.get(name)
        if capability is None:
            problems.append(f"comando {name!r} não existe na API (órfão)")
            continue
        for field in FIELDS:
            if entry[field] != capability[field]:
                problems.append(
                    f"comando {name!r}: {field} é {entry[field]!r} no arquivo "
                    f"e {capability[field]!r} na API"
                )
    for phrase, name in data["phrases"].items():
        if name not in data["commands"]:
            problems.append(f"frase {phrase!r} aponta para comando inexistente {name!r} (órfã)")
    return problems


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    api_url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GO2_API_URL", "")).rstrip("/")
    if not api_url:
        print("ERRO: passe a URL da API como argumento ou defina GO2_API_URL.", file=sys.stderr)
        return 1

    commands_file = REPO_ROOT / "config" / "commands.json"
    data = json.loads(commands_file.read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(f"{api_url}/capabilities", timeout=5) as response:
            capabilities = json.load(response)
    except OSError as error:
        print(f"ERRO: não consegui ler {api_url}/capabilities: {error}", file=sys.stderr)
        return 1

    problems = find_problems(data, capabilities["commands"])
    if problems:
        print(f"ERRO: {commands_file} diverge da API (versão {capabilities['version']}):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"OK: {len(data['phrases'])} frases e {len(data['commands'])} comandos conferem "
        f"com {api_url} (versão {capabilities['version']})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
