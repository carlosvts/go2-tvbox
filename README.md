# Go2 Sense — TV Box

Cliente de voz do Go2. Captura o microfone Anker, reconhece um comando falado ("hey jarvis" + frase em PT-BR) e faz o POST na [go2-api](https://github.com/carlosvts/go2-api).

Roda em dois modos, escolhidos por `SENSE_MODE` no `.env`:

```
edge   [Anker] → TV Box: captura + reconhecimento ──HTTP POST──▶ go2-api

thin   [Anker] → TV Box: captura ──TCP──▶ PC: reconhecimento ──HTTP POST──▶ go2-api
```

| | edge | thin |
|---|---|---|
| Na TV Box | captura, wake word, STT, POST | só captura e envio de áudio |
| No PC | nada | `python -m sense.receiver` |
| Dependências na TV Box | extras `capture` e `recognition` + modelos | extra `capture` |

O reconhecimento é o mesmo componente nos dois modos (`sense/recognizer.py`); só muda de onde vem o áudio.

## Instalação

Requer Python 3.10 ou 3.11 (o [uv](https://docs.astral.sh/uv/) baixa um se o sistema não tiver).

**TV Box** (o PyAudio compila do fonte em aarch64):

```bash
sudo apt install portaudio19-dev gcc python3-dev
uv sync --no-dev --extra capture                       # só thin
uv sync --no-dev --extra capture --extra recognition   # edge, ou para alternar entre os dois
uv run python scripts/download_models.py               # só se instalou `recognition`
cp .env.example .env                                   # e ajuste
```

Quem instala os dois extras troca de modo só editando o `.env` e reiniciando. O extra `recognition` ocupa cerca de 300 MB, mais 60 MB de modelos: confira `df -h` antes.

**PC receptor** (modo thin; não precisa de PyAudio):

```bash
uv sync --no-dev --extra recognition
uv run python scripts/download_models.py
GO2_API_URL=http://localhost:8000 uv run python -m sense.receiver
```

O receptor lê `GO2_API_URL` e, opcionalmente, `RECEIVER_PORT` e os parâmetros de reconhecimento, do `.env` ou do ambiente. Libere a porta TCP 9876 no firewall do PC.

## Uso

À mão, na TV Box:

```bash
uv run python -m sense
```

Como serviço (as instruções de instalação estão no topo de `deploy/go2-sense.service`):

```bash
sudo systemctl restart go2-sense
journalctl --namespace=go2-sense -f
```

Os logs do serviço ficam num journal próprio, limitado a 20 MB.

No boot, o log mostra o modo, a branch e o commit em execução, e a configuração em vigor:

```
Sense iniciando | modo=edge | código=sense-mode-config@1a2b3c4
Configuração: api_url=http://192.168.0.10:8000 chunk_ms=30 mic_name=anker ...
```

Configuração inválida derruba o processo na partida (código de saída 2, sem reinício automático) com todos os problemas listados.

## Comandos de voz

Diga "hey jarvis", espere o beep e diga a frase.

| Frase | Comando | O que faz |
|---|---|---|
| "fica de pé" | `stand_up` | levanta |
| "pode sentar" | `sit` | senta |
| "deita no chão" | `stand_down` | deita de forma controlada |
| "para agora" | `stop` | para de andar, continua de pé |
| "dá um oi" | `hello` | acena |
| "faz alongamento" | `stretch` | alonga |
| "faz coração" | `finger_heart` | gesto de coração |
| "desligar motores" | `damp` | **tira a força dos motores: de pé, o robô cai** |

O mapa fica em `config/commands.json`: `phrases` (frase → comando) e `commands` (cópia das entradas de `GET /capabilities` usadas). Depois de editar, valide contra a API:

```bash
uv run python scripts/validate_commands.py http://192.168.0.10:8000
```

O script falha (código 1) se algum comando do arquivo não existir na API ou divergir dela. As palavras das frases precisam existir no vocabulário do modelo Vosk; palavra desconhecida é ignorada por ele.

## Onde ajustar cada parâmetro

**No `.env`** (edite e reinicie; detalhes e faixas em `.env.example`):

| Parâmetro | Variável | Padrão |
|---|---|---|
| Modo | `SENSE_MODE` | — |
| Endereço da go2-api | `GO2_API_URL` | — |
| Endereço e porta do receptor | `RECEIVER_HOST`, `RECEIVER_PORT` | —, 9876 |
| Nome do microfone | `MIC_NAME` | `anker` |
| Tamanho do chunk de áudio | `AUDIO_CHUNK_MS` | 30 |
| Sensibilidade da wake word | `WAKE_THRESHOLD` | 0.85 |
| Confiança mínima do STT | `STT_MIN_CONFIDENCE` | 0.7 |
| Tempo máximo de escuta após a wake word | `COMMAND_TIMEOUT_S` | 4.0 |
| Cooldown de comando repetido | `COOLDOWN_S` | 2.0 |
| Pasta do modelo Vosk | `VOSK_MODEL_PATH` | `models/vosk-model-small-pt-0.3` |

**No `config/commands.json`:** as frases e os comandos.

**Constantes no código** (edite o arquivo e reinicie):

| Parâmetro | Constante | Arquivo | Valor |
|---|---|---|---|
| Áudio descartado logo após a wake word (eco e beep) | `POST_WAKE_DISCARD_S` | `sense/recognizer.py` | 0.9 s |
| Tempo em que a wake word fica ignorada após uma escuta | `REARM_S` | `sense/recognizer.py` | 1.5 s |
| Qual wake word | `WAKE_WORD` | `sense/recognizer.py` | `hey_jarvis` |
| Timeout do POST na API | `POST_TIMEOUT_S` | `sense/dispatcher.py` | 2.0 s |
| Acúmulo de áudio tolerado no modo edge | `MAX_BACKLOG_S` | `sense/edge.py` | 0.2 s |
| Buffers de socket do modo thin | `SOCKET_BUFFER_BYTES` | `sense/transport/tcp.py` | 8192 |
| Intervalo entre tentativas de reconexão | `RECONNECT_DELAY_S` | `sense/transport/tcp.py` | 3.0 s |
| Timeout de conexão ao receptor | `CONNECT_TIMEOUT_S` | `sense/transport/tcp.py` | 1.0 s |
| Silêncio até o receptor dar a TV Box por desconectada | `IDLE_TIMEOUT_S` | `sense/transport/tcp.py` | 5.0 s |
| Atraso a partir do qual um chunk conta como atrasado | `LATE_THRESHOLD_S` | `sense/transport/tcp.py` | 0.2 s |
| Intervalo do log de diagnóstico | `STATS_INTERVAL_S` | `sense/stats.py` | 60 s |
| Taxa de captura do microfone | `CAPTURE_RATE` | `sense/audio.py` | 48000 Hz |
| Arquivo do beep | `BEEP_FILE` | `sense/audio.py` | `media/beep.wav` |
| Tamanho máximo dos logs do serviço | `SystemMaxUse` | `deploy/journald@go2-sense.conf` | 20 MB |

**Sem ajuste hoje:** o Vosk só fecha a frase depois de cerca de 1 s de silêncio. Esse tempo é interno a ele: a versão usada não o expõe no Python e o modelo `small-pt-0.3` não tem arquivo de configuração para isso.

## Diagnóstico

A cada 60 s o log traz uma linha com CPU, pico de RAM e os contadores acumulados:

```
Diagnóstico: cpu=23% ram_pico=310MB | chunks_sent=2000 commands_sent=3 no_match=1
```

| Contador | Onde | Significa |
|---|---|---|
| `wake_detections` | reconhecimento | wake words detectadas |
| `commands_recognized` | reconhecimento | frases que viraram comando |
| `no_match` | reconhecimento | o STT ouviu algo que não é uma frase do mapa |
| `low_confidence` | reconhecimento | frase certa, confiança abaixo do mínimo |
| `listen_timeouts` | reconhecimento | wake word sem nenhuma frase reconhecida depois |
| `commands_sent` | POST | a API respondeu 202 |
| `cooldown_discards` | POST | comando repetido dentro do cooldown |
| `api_rejections` | POST | a API respondeu erro (422, 503...) |
| `api_unreachable` | POST | a API não respondeu |
| `chunks_dropped_late` | TV Box | áudio descartado por atraso (rede sem vazão no thin, acúmulo no edge) |
| `chunks_sent`, `chunks_dropped_offline`, `tcp_reconnects` | TV Box, thin | enviados, descartados sem conexão, reconexões |
| `chunks_received`, `chunks_late`, `gaps`, `chunks_missing`, `tcp_connections` | PC, thin | recebidos, atrasados, lacunas, chunks faltando, conexões aceitas |

Comando fora da gramática, confiança baixa, API fora do ar ou erro no POST: o comando é descartado, com uma linha de log dizendo o motivo. Não há fila nem nova tentativa.

## Estrutura

```
.
├── .env.example               # os dois modos documentados
├── config/commands.json       # frase → comando
├── sense/
│   ├── __main__.py            # entrada da TV Box: python -m sense
│   ├── receiver.py            # entrada do PC (thin): python -m sense.receiver
│   ├── boot.py                # logging, validação e log de boot
│   ├── config.py              # leitura e validação do .env
│   ├── edge.py / thin.py      # o laço de cada modo
│   ├── audio.py               # microfone e beep
│   ├── recognizer.py          # wake word + STT + lookup (único nos dois modos)
│   ├── commands.py            # carrega o mapa de comandos
│   ├── dispatcher.py          # cooldown + POST na go2-api
│   ├── stats.py               # contadores e log de diagnóstico
│   └── transport/tcp.py       # transporte do thin (a única parte que sabe que é TCP)
├── scripts/
│   ├── validate_commands.py   # confere commands.json contra GET /capabilities
│   └── download_models.py     # baixa hey_jarvis e o modelo Vosk
├── deploy/                    # serviço systemd e limites do journal
├── docs/checklist-laboratorio.md
├── tests/
└── legacy/                    # cliente antigo (servidor unitreego2). Não é usado.
```

`legacy/` guarda o cliente da arquitetura anterior (áudio cru por TCP para o servidor `unitreego2`, sem go2-api). Nada do pacote `sense` depende dele.

## Testes

```bash
uv sync --extra recognition
uv run pytest
```

Os testes com openWakeWord e Vosk reais usam fala sintética (`espeak-ng` + `sox`) e são pulados se faltarem esses programas ou os modelos. Nenhum teste usa microfone.

O que só o hardware valida está em [docs/checklist-laboratorio.md](docs/checklist-laboratorio.md).

## Especificações da TV Box

| Item | Valor |
|---|---|
| Host | `minipc@aml-s9xx-box` |
| Arquitetura | aarch64 (ARM64, Amlogic S9xx) |
| CPU | ARMv8 Processor rev 0 (4 cores) |
| SO | Debian GNU/Linux 11 (bullseye) |
| RAM total | 1.8 GiB (~173 MiB livre, ~1.1 GiB "available" com cache) |
| Swap | 912 MiB (40 MiB em uso) |
| Disco raiz (`/dev/mmcblk1p2`) | 13G total, 90% em uso, ~1.3G livre |
| `/boot` | 477M, 28% em uso |

> **Atenção:** disco quase cheio e RAM limitada. Evite instalar dependências desnecessárias — antes de qualquer instalação nova, confira espaço com `df -h` e `free -h`.

## Autores

- Samuel Frizzone Cardoso
- Carlos Vinícius Teixeira de Souza
- Hugo Prado Lima

NEURON — Núcleo de Estudos de Robótica Interativa da UFLA
