"""Configuração do Sense: lida do `.env` e validada antes de qualquer coisa subir.

Um erro aqui derruba o processo na partida, com todos os problemas listados de
uma vez, em vez de aparecer só no primeiro comando de voz.
"""

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

SAMPLE_RATE = 16000  # exigido pelo openWakeWord e pelo Vosk


class ConfigError(Exception):
    """Configuração inválida. A mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Config:
    api_url: str
    chunk_ms: int
    mic_name: str
    vosk_model_path: Path
    wake_threshold: float
    stt_min_confidence: float
    command_timeout_s: float
    cooldown_s: float
    # Movimento só sai com o desvio de obstáculo do robô confirmado como ligado.
    require_obstacle_avoidance: bool
    # Fallback para o servidor de inferência. Sem `server_url`, fica desligado.
    server_url: str | None
    edge_id: str
    fallback_timeout_s: float
    cancel_timeout_s: float
    fallback_ok_statuses: frozenset[str]
    max_local_utterance_s: float
    max_utterance_s: float
    audio_buffer_s: float
    stop_failsafe: bool


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Monta a configuração. Sem `env`, lê o `.env` da raiz do repo e o ambiente."""
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

    def flag(name: str, default: bool) -> bool:
        raw = get(name).lower()
        if not raw:
            return default
        if raw not in ("0", "1", "true", "false"):
            problems.append(f"{name}={raw!r} inválido (use 1 ou 0)")
        return raw in ("1", "true")

    def url(name: str, example: str, required: bool) -> str | None:
        value = get(name).rstrip("/")
        parsed = urlparse(value)
        if not value:
            if required:
                problems.append(f"{name} não definido")
            return None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            problems.append(f"{name}={value!r} inválido (esperado algo como {example})")
        return value

    model_path = Path(get("VOSK_MODEL_PATH") or "models/vosk-model-small-pt-0.3")
    if not model_path.is_absolute():
        model_path = REPO_ROOT / model_path

    config = Config(
        api_url=url("GO2_API_URL", "http://192.168.0.10:8000", required=True) or "",
        chunk_ms=int(number("AUDIO_CHUNK_MS", 30, 20, 40)),
        mic_name=get("MIC_NAME") or "anker",
        vosk_model_path=model_path,
        wake_threshold=number("WAKE_THRESHOLD", 0.85, 0, 1),
        stt_min_confidence=number("STT_MIN_CONFIDENCE", 0.7, 0, 1),
        command_timeout_s=number("COMMAND_TIMEOUT_S", 2.5, 1, 15),
        cooldown_s=number("COOLDOWN_S", 2.0, 0, 60),
        require_obstacle_avoidance=flag("REQUIRE_OBSTACLE_AVOIDANCE", True),
        server_url=url("SERVER_URL", "http://192.168.0.10:9000", required=False),
        edge_id=get("EDGE_ID") or socket.gethostname(),
        fallback_timeout_s=number("FALLBACK_TIMEOUT_S", 5.0, 0.5, 60),
        cancel_timeout_s=number("CANCEL_TIMEOUT_S", 0.5, 0.1, 5),
        fallback_ok_statuses=frozenset(
            status.strip().lower()
            for status in (get("FALLBACK_OK_STATUSES") or "ok,accepted,queued,executed").split(",")
            if status.strip()
        ),
        max_local_utterance_s=number("MAX_LOCAL_UTTERANCE_S", 3.0, 0.5, 15),
        max_utterance_s=number("MAX_UTTERANCE_S", 8.0, 1, 30),
        audio_buffer_s=number("AUDIO_BUFFER_S", 10.0, 2, 60),
        stop_failsafe=flag("STOP_FAILSAFE", True),
    )
    if config.max_utterance_s < config.command_timeout_s:
        problems.append("MAX_UTTERANCE_S não pode ser menor que COMMAND_TIMEOUT_S")
    if config.audio_buffer_s < config.max_utterance_s + 1:
        problems.append("AUDIO_BUFFER_S tem de ser pelo menos MAX_UTTERANCE_S + 1")
    if problems:
        raise ConfigError("Configuração inválida:\n" + "\n".join(f"  - {p}" for p in problems))
    return config
