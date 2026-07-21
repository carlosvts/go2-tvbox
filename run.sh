#!/usr/bin/env bash
# Ponto de entrada único do projeto Go2 na TV Box.
# 1) Regenera config/.env (IP do PC + índices do microfone, com retry)
# 2) Roda o client
#
# Overrides opcionais (variáveis de ambiente):
#   PC_SERVER_IP=192.168.0.111 ./run.sh     -> pula toda a detecção de IP
#   MAX_RETRIES=10 ./run.sh                 -> mais tentativas antes de desistir
#   RETRY_DELAY_S=5 ./run.sh                -> espera maior entre tentativas

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Regenerando config/.env..."
bash "$PROJECT_ROOT/scripts/generate_env.sh"

echo "==> Iniciando client_armbian.py..."
exec python3 "$PROJECT_ROOT/src/client_armbian.py" "$@"
