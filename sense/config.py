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

SAMPLE_RATE = 16000  # exigido pelo openWakeWord e pelo servidor


# Valores de `status` do servidor que contam como "confirmado": os do go2-server
# (frase aceita na fila, ou parada executada).
DEFAULT_OK_STATUSES = "enqueued,stopped"


class ConfigError(Exception):
    """Configuração inválida. A mensagem lista todos os problemas."""


@dataclass(frozen=True)
class Config:
    # Servidor de inferência: recebe o áudio de cada comando.
    server_url: str
    # go2-api: só a parada manual (`python -m sense.stop`) fala com ela.
    api_url: str | None
    chunk_ms: int
    mic_name: str
    wake_word: str  # nome do modelo do openWakeWord
    wake_threshold: float
    wake_vad_threshold: float  # 0 = VAD do Silero desligado
    edge_id: str
    fallback_timeout_s: float
    cancel_timeout_s: float
    fallback_ok_statuses: frozenset[str]
    max_utterance_s: float
    audio_buffer_s: float
    wake_cooldown_s: float
    wake_sound: bool
    beep_ignore_s: float
    pre_roll_s: float
    vad_end_silence_s: float
    vad_min_speech_s: float
    vad_speech_timeout_s: float
    vad_speech_ratio: float
    vad_min_rms: float
    utterance_reason: str
    save_utterances_dir: Path | None


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

    # O modo edge foi removido; só sobrou o thin. Um SENSE_MODE=thin antigo no
    # .env não atrapalha, mas qualquer outro valor é um .env de outra época.
    mode = get("SENSE_MODE")
    if mode and mode != "thin":
        problems.append(
            f"SENSE_MODE={mode!r}: o Sense só tem o modo thin agora; apague a linha do .env"
        )

    save_dir = Path(get("SAVE_UTTERANCES_DIR")) if get("SAVE_UTTERANCES_DIR") else None
    if save_dir is not None and not save_dir.is_absolute():
        save_dir = REPO_ROOT / save_dir

    config = Config(
        server_url=url("SERVER_URL", "http://192.168.0.10:9000", required=True) or "",
        api_url=url("GO2_API_URL", "http://192.168.0.10:8000", required=False),
        chunk_ms=int(number("AUDIO_CHUNK_MS", 30, 20, 40)),
        mic_name=get("MIC_NAME") or "anker",
        wake_word=get("WAKE_WORD") or "hey_jarvis",
        wake_threshold=number("WAKE_THRESHOLD", 0.85, 0, 1),
        wake_vad_threshold=number("WAKE_VAD_THRESHOLD", 0.0, 0, 1),
        edge_id=get("EDGE_ID") or socket.gethostname(),
        fallback_timeout_s=number("FALLBACK_TIMEOUT_S", 5.0, 0.5, 60),
        cancel_timeout_s=number("CANCEL_TIMEOUT_S", 0.5, 0.1, 5),
        fallback_ok_statuses=frozenset(
            status.strip().lower()
            for status in (get("FALLBACK_OK_STATUSES") or DEFAULT_OK_STATUSES).split(",")
            if status.strip()
        ),
        max_utterance_s=number("MAX_UTTERANCE_S", 8.0, 1, 30),
        audio_buffer_s=number("AUDIO_BUFFER_S", 10.0, 2, 60),
        wake_cooldown_s=number("WAKE_COOLDOWN_S", 2.0, 0, 30),
        wake_sound=flag("WAKE_SOUND", True),
        beep_ignore_s=number("BEEP_IGNORE_S", 0.9, 0, 3),
        pre_roll_s=number("PRE_ROLL_S", 0.3, 0, 2),
        vad_end_silence_s=number("VAD_END_SILENCE_S", 0.7, 0.2, 3),
        vad_min_speech_s=number("VAD_MIN_SPEECH_S", 0.2, 0.03, 2),
        vad_speech_timeout_s=number("VAD_SPEECH_TIMEOUT_S", 5.0, 1, 30),
        vad_speech_ratio=number("VAD_SPEECH_RATIO", 3.0, 1, 20),
        vad_min_rms=number("VAD_MIN_RMS", 150.0, 0, 10000),
        utterance_reason=get("UTTERANCE_REASON") or "wake_word",
        save_utterances_dir=save_dir,
    )
    if config.audio_buffer_s < config.max_utterance_s + 1:
        problems.append("AUDIO_BUFFER_S tem de ser pelo menos MAX_UTTERANCE_S + 1")
    if problems:
        raise ConfigError("Configuração inválida:\n" + "\n".join(f"  - {p}" for p in problems))
    return config
