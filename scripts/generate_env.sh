#!/usr/bin/env bash
# Gera config/.env detectando automaticamente:
#   - índices do microfone Anker (ALSA + PyAudio), com retry
#   - IP do PC (cache > default conhecido > scan de rede), com retry
# Roda NA TV BOX, antes de iniciar o client.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/config"
ENV_FILE="$CONFIG_DIR/.env"
IP_CACHE_FILE="$CONFIG_DIR/.last_known_pc_ip"

mkdir -p "$CONFIG_DIR"

TCP_PORT="${TCP_PORT:-9876}"
KNOWN_DEFAULT_IP="192.168.0.111"   # último IP confirmado manualmente — fast path
MAX_RETRIES="${MAX_RETRIES:-5}"
RETRY_DELAY_S="${RETRY_DELAY_S:-3}"

# ── Helper: testa se host:porta responde, rápido -----------------------------
port_is_open() {
    local host="$1" port="$2"
    timeout 2 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null
}

# ── IP do PC ------------------------------------------------------------------
find_pc_ip() {
    # 1) Override explícito do usuário — sempre tem prioridade máxima
    if [ -n "${PC_SERVER_IP:-}" ]; then
        echo "  (usando PC_SERVER_IP definido manualmente)" >&2
        echo "$PC_SERVER_IP"
        return 0
    fi

    # 2) Cache do último IP que funcionou — fast path, evita scan na maioria das vezes
    if [ -f "$IP_CACHE_FILE" ]; then
        local cached
        cached="$(cat "$IP_CACHE_FILE" 2>/dev/null || true)"
        if [ -n "$cached" ] && port_is_open "$cached" "$TCP_PORT"; then
            echo "  (usando IP em cache: $cached)" >&2
            echo "$cached"
            return 0
        fi
    fi

    # 3) IP default conhecido — segundo fast path antes de escanear a rede toda
    if [ "$KNOWN_DEFAULT_IP" != "${cached:-}" ] && port_is_open "$KNOWN_DEFAULT_IP" "$TCP_PORT"; then
        echo "  (usando IP default conhecido: $KNOWN_DEFAULT_IP)" >&2
        echo "$KNOWN_DEFAULT_IP"
        return 0
    fi

    # 4) Scan completo da sub-rede (mais lento, último recurso)
    echo "  Fast paths falharam, escaneando a rede (porta $TCP_PORT)..." >&2

    local cidr
    cidr="$(ip -o -4 addr show scope global | awk '{print $4}' | head -n1)"
    if [ -z "$cidr" ]; then
        echo "ERRO: não consegui determinar a sub-rede local." >&2
        return 1
    fi

    if command -v nmap >/dev/null 2>&1; then
        local found
        found="$(nmap -p "$TCP_PORT" --open -n "$cidr" -oG - 2>/dev/null \
            | awk -v p="$TCP_PORT" '/Ports: .*'"$TCP_PORT"'\/open/{print $2; exit}')"
        if [ -n "$found" ]; then
            echo "$found"
            return 0
        fi
        return 1
    fi

    # Fallback puro em bash, sem nmap
    local base
    base="$(echo "$cidr" | sed -E 's|([0-9]+\.[0-9]+\.[0-9]+)\.[0-9]+/.*|\1|')"
    local tmp_result
    tmp_result="$(mktemp)"
    for i in $(seq 1 254); do
        (
            port_is_open "$base.$i" "$TCP_PORT" && echo "$base.$i" >> "$tmp_result"
        ) &
        if (( i % 40 == 0 )); then wait; fi
    done
    wait
    local found
    found="$(head -n1 "$tmp_result" 2>/dev/null || true)"
    rm -f "$tmp_result"
    if [ -n "$found" ]; then
        echo "$found"
        return 0
    fi
    return 1
}

echo "==> Detectando IP do servidor (PC)..."
PC_SERVER_IP=""
for attempt in $(seq 1 "$MAX_RETRIES"); do
    if PC_SERVER_IP="$(find_pc_ip)"; then
        break
    fi
    echo "  Tentativa $attempt/$MAX_RETRIES falhou. Aguardando rede estabilizar..." >&2
    sleep "$RETRY_DELAY_S"
done

if [ -z "$PC_SERVER_IP" ]; then
    echo "ERRO: nenhum servidor respondendo na porta $TCP_PORT foi encontrado após $MAX_RETRIES tentativas." >&2
    echo "Defina manualmente: PC_SERVER_IP=192.168.0.111 ./run.sh" >&2
    exit 1
fi
echo "  PC_SERVER_IP: $PC_SERVER_IP"
echo "$PC_SERVER_IP" > "$IP_CACHE_FILE"

# ── Microfone Anker (ALSA + PyAudio), com retry -------------------------------
echo "==> Detectando dispositivo Anker..."

ANKER_CARD_INDEX=""
MIC_DEVICE_INDEX=""
for attempt in $(seq 1 "$MAX_RETRIES"); do
    ANKER_CARD_INDEX="$(arecord -l 2>/dev/null | grep -i "anker" | head -n1 | sed -n 's/^card \([0-9]*\):.*/\1/p' || true)"
    if [ -n "$ANKER_CARD_INDEX" ] && MIC_DEVICE_INDEX="$(python3 "$SCRIPT_DIR/find_mic_index.py" 2>/dev/null)"; then
        break
    fi
    echo "  Tentativa $attempt/$MAX_RETRIES falhou. Aguardando dispositivo de áudio estabilizar..." >&2
    ANKER_CARD_INDEX=""
    MIC_DEVICE_INDEX=""
    sleep "$RETRY_DELAY_S"
done

if [ -z "$ANKER_CARD_INDEX" ] || [ -z "$MIC_DEVICE_INDEX" ]; then
    echo "ERRO: microfone Anker não detectado/funcional após $MAX_RETRIES tentativas." >&2
    echo "Rode manualmente para depurar: arecord -l   /   python3 scripts/find_mic_index.py" >&2
    exit 1
fi
echo "  Card ALSA (Anker): $ANKER_CARD_INDEX"
echo "  Índice PyAudio (Anker): $MIC_DEVICE_INDEX"

# ── Valores fixos (sobrescrevíveis via variável de ambiente) ------------------
AUDIO_SAMPLE_RATE="${AUDIO_SAMPLE_RATE:-16000}"
AUDIO_CHANNELS="${AUDIO_CHANNELS:-1}"
AUDIO_CHUNK_MS="${AUDIO_CHUNK_MS:-30}"
BEEP_FILE="${BEEP_FILE:-/home/minipc/go2_refactor/media/beep.wav}"

cat > "$ENV_FILE" <<EOF
#=============================================================
# .env DA TV BOX — gerado automaticamente por scripts/generate_env.sh
# NÃO edite os índices/IP manualmente — eles mudam a cada boot/reconexão.
# Gerado em: $(date '+%Y-%m-%d %H:%M:%S')
#=============================================================
PC_SERVER_IP=$PC_SERVER_IP
TCP_PORT=$TCP_PORT

# Áudio (deve casar com o servidor)
AUDIO_SAMPLE_RATE=$AUDIO_SAMPLE_RATE
AUDIO_CHANNELS=$AUDIO_CHANNELS
AUDIO_CHUNK_MS=$AUDIO_CHUNK_MS

# Microfone Anker — detectado automaticamente a cada execução
MIC_DEVICE_INDEX=$MIC_DEVICE_INDEX
ANKER_CARD_INDEX=$ANKER_CARD_INDEX

# Caminho ABSOLUTO do beep na TV Box
BEEP_FILE=$BEEP_FILE
EOF

echo "==> .env gerado em: $ENV_FILE"
