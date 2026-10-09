"""Partida comum aos três papéis: logging, validação da config e log de boot."""

import logging
import subprocess
import sys

from sense.config import REPO_ROOT, Config, ConfigError, load_config

log = logging.getLogger(__name__)

# Código de saída para configuração inválida. O serviço systemd não reinicia
# nesse caso (RestartPreventExitStatus=2): reiniciar não conserta um .env errado.
EXIT_BAD_CONFIG = 2

_RECOGNITION = (
    "vosk_model_path", "stt_mode", "wake_threshold", "stt_min_confidence", "command_timeout_s",
)
_RELEVANT = {
    "edge": ("api_url", "chunk_ms", "mic_name", "cooldown_s", *_RECOGNITION),
    "thin": ("receiver_host", "receiver_port", "chunk_ms", "mic_name"),
    "receiver": ("api_url", "receiver_port", "cooldown_s", *_RECOGNITION),
}


def git_revision() -> str:
    """Branch e commit do código em execução, ex.: "sense-mode-config@1a2b3c4"."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()

    try:
        revision = f"{git('rev-parse', '--abbrev-ref', 'HEAD')}@{git('rev-parse', '--short', 'HEAD')}"
        return revision + ("+modificado" if git("status", "--porcelain") else "")
    except (OSError, subprocess.SubprocessError):
        return "desconhecido"


def start(role: str | None = None) -> Config:
    """Configura o logging, valida a config e loga o que vai rodar."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        config = load_config(role)
    except ConfigError as error:
        log.critical("%s", error)
        sys.exit(EXIT_BAD_CONFIG)

    settings = " ".join(f"{name}={getattr(config, name)}" for name in _RELEVANT[config.role])
    log.info("Sense iniciando | modo=%s | código=%s", config.role, git_revision())
    log.info("Configuração: %s", settings)
    return config
