"""Configuração do Sense: lida do `.env` e validada antes de qualquer coisa subir.

Um erro aqui derruba o processo na partida, com todos os problemas listados de
uma vez, em vez de aparecer só no primeiro comando de voz.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Valores aceitos em SENSE_MODE (o que a TV Box faz).
MODES = ("edge", "thin")
# Papel do PC no modo thin. Não é um SENSE_MODE: quem o escolhe é o comando
# `python -m sense.receiver`.
RECEIVER = "receiver"

SAMPLE_RATE = 16000  # exigido pelo openWakeWord e pelo Vosk


class ConfigError(Exception):
    """Configuração inválida. A mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Config:
    role: str  # "edge", "thin" ou "receiver"
    api_url: str | None  # None no thin: quem fala com a API é o receptor
    receiver_host: str | None  # só no thin
    receiver_port: int
    chunk_ms: int
    mic_name: str
    vosk_model_path: Path
    wake_threshold: float
    stt_min_confidence: float
    command_timeout_s: float
    cooldown_s: float


def load_config(role: str | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Monta a configuração do papel `role`.

    Sem `role`, o papel é o valor de SENSE_MODE (caso da TV Box). Sem `env`,
    lê o `.env` da raiz do repo e depois o ambiente do processo.
    """
    if env is None:
        load_dotenv(REPO_ROOT / ".env")
        env = os.environ

    problems: list[str] = []

    def get(name: str) -> str:
        return env.get(name, "").strip()

    def number(name: str, default: float, low: float, high: float) -> float:
        raw = get(name)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            problems.append(f"{name}={raw!r} não é um número")
            return default
        if not low <= value <= high:
            problems.append(f"{name}={raw} fora da faixa {low:g} a {high:g}")
        return value

    if role is None:
        role = get("SENSE_MODE")
        if not role:
            problems.append(f"SENSE_MODE não definido (use um de: {', '.join(MODES)})")
        elif role not in MODES:
            problems.append(f"SENSE_MODE={role!r} inválido (use um de: {', '.join(MODES)})")

    api_url = None
    if role in ("edge", RECEIVER):
        api_url = get("GO2_API_URL").rstrip("/")
        parsed = urlparse(api_url)
        if not api_url:
            problems.append(f"GO2_API_URL não definido (obrigatório no papel {role})")
        elif parsed.scheme not in ("http", "https") or not parsed.netloc:
            problems.append(
                f"GO2_API_URL={api_url!r} inválido (esperado algo como http://192.168.0.10:8000)"
            )

    receiver_host = None
    if role == "thin":
        receiver_host = get("RECEIVER_HOST")
        if not receiver_host:
            problems.append("RECEIVER_HOST não definido (obrigatório no modo thin)")

    model_path = Path(get("VOSK_MODEL_PATH") or "models/vosk-model-small-pt-0.3")
    if not model_path.is_absolute():
        model_path = REPO_ROOT / model_path

    config = Config(
        role=role,
        api_url=api_url,
        receiver_host=receiver_host,
        receiver_port=int(number("RECEIVER_PORT", 9876, 1, 65535)),
        chunk_ms=int(number("AUDIO_CHUNK_MS", 30, 20, 40)),
        mic_name=get("MIC_NAME") or "anker",
        vosk_model_path=model_path,
        wake_threshold=number("WAKE_THRESHOLD", 0.85, 0, 1),
        stt_min_confidence=number("STT_MIN_CONFIDENCE", 0.7, 0, 1),
        command_timeout_s=number("COMMAND_TIMEOUT_S", 4.0, 1, 15),
        cooldown_s=number("COOLDOWN_S", 2.0, 0, 60),
    )
    if problems:
        raise ConfigError("Configuração inválida:\n" + "\n".join(f"  - {p}" for p in problems))
    return config
