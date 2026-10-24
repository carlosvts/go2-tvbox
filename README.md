# Go2 Sense — TV Box

Cliente de voz do Go2. Roda na TV Box: captura o microfone Anker, espera a wake word ("hey jarvis") e manda o áudio do comando falado ao servidor de inferência ([go2-server](https://github.com/carlosvts/go2-server)), que transcreve, interpreta e leva o comando até a [go2-api](https://github.com/carlosvts/go2-api).

```
[Anker] → TV Box: wake word + gravação do comando ── áudio ──HTTP──▶ go2-server ──HTTP──▶ go2-api ──▶ Go2
              ▲                                                         │
              └──────────── resposta: só som de retorno e log ◀─────────┘
```

A TV Box não reconhece comando nenhum, não tem fila e não executa nada do que o servidor responde: a resposta vira só um som de retorno. A única chamada direta à go2-api é a [parada manual](#parada-manual).

> Até outubro de 2026 havia também um modo `edge`, com reconhecimento local por gramática (Vosk). Ele foi removido; o que sobrou é o antigo modo `thin`. Está no histórico do git, se precisar.

## Índice

- [Como rodar](#como-rodar)
  - [Como serviço na TV Box](#como-serviço-na-tv-box)
  - [O que esperar no log](#o-que-esperar-no-log)
- [Como funciona](#como-funciona)
- [Sons de retorno](#sons-de-retorno)
- [Parada manual](#parada-manual)
- [Onde ajustar cada parâmetro](#onde-ajustar-cada-parâmetro)
- [Diagnóstico](#diagnóstico)
- [Estrutura](#estrutura)
- [Testes](#testes)
- [Especificações da TV Box](#especificações-da-tv-box)
- [Autores](#autores)

## Como rodar

O go2-server precisa estar no ar e alcançável pela rede (`curl http://<IP_DO_SERVIDOR>:9000/health` responde 200). Requer Python 3.10; o [uv](https://docs.astral.sh/uv/) baixa um se o sistema não tiver.

Na TV Box, dentro do repo:

1. Instale as dependências de sistema (o PyAudio compila do fonte em aarch64):

   ```bash
   sudo apt install portaudio19-dev gcc python3-dev
   ```

2. Instale o projeto e baixe o modelo da wake word (confira `df -h` antes):

   ```bash
   uv sync --no-dev --extra capture --extra recognition
   uv run python scripts/download_models.py
   ```

3. Crie o `.env`:

   ```bash
   cp .env.example .env
   ```

   e deixe nele o endereço do servidor e, para a parada manual, o da go2-api:

   ```
   SERVER_URL=http://<IP_DO_SERVIDOR>:9000
   GO2_API_URL=http://<IP_DA_API>:8000
   ```

4. Rode:

   ```bash
   uv run python -m sense
   ```

   O log deve mostrar `Wake word pronta`, `Servidor de inferência no ar` e `Microfone aberto`. Diga "hey jarvis", espere o beep e diga o comando.

**Vindo de uma instalação com o modo edge:** apague `SENSE_MODE` do `.env` (com `edge` o Sense não sobe; `thin` ainda é aceito). As outras chaves do edge são ignoradas. `uv sync` tira o Vosk do ambiente, e o modelo dele pode ser apagado para liberar ~50 MB: `rm -rf models/vosk-model-small-pt-0.3`.

### Como serviço na TV Box

As instruções de instalação do serviço systemd estão no topo de `deploy/go2-sense.service`; ajuste ali o usuário e o caminho do repo. Depois:

```bash
sudo systemctl restart go2-sense        # aplica uma mudança no .env
journalctl --namespace=go2-sense -f     # acompanha os logs
```

Os logs do serviço ficam num journal próprio, limitado a 20 MB.

### O que esperar no log

No boot, o log mostra a branch e o commit em execução e a configuração em vigor:

```
Sense iniciando | código=main@1a2b3c4
Configuração: server_url=http://192.168.0.10:9000 api_url=http://192.168.0.10:8000 chunk_ms=30 ...
```

Configuração inválida (`SERVER_URL` faltando, número fora da faixa) derruba o processo na partida, com todos os problemas listados. Como serviço, ele para sem ficar reiniciando.

## Como funciona

1. **Ocioso:** o microfone alimenta um buffer circular e a wake word. Nenhum áudio sai da TV Box.
2. **Wake word** (`WAKE_WORD`, limiar `WAKE_THRESHOLD`): toca o beep (`WAKE_SOUND`) e começa a escuta. Outra wake word só vale depois de `WAKE_COOLDOWN_S`.
3. **Escuta:** o fim da fala é detectado por volume. Os primeiros `BEEP_IGNORE_S` não contam como fala (é o beep saindo do alto-falante). A escuta acaba com `VAD_END_SILENCE_S` de silêncio depois da fala, em `MAX_UTTERANCE_S` (a fala é cortada), ou em `VAD_SPEECH_TIMEOUT_S` sem fala nenhuma (descartada, com o som de recusa).
4. **Envio:** `POST {SERVER_URL}/v1/utterance` com o WAV e `meta`. O áudio começa `PRE_ROLL_S` antes da detecção, então traz o final da wake word e o beep. Uma wake word dita enquanto a resposta não chega é ignorada, com log: cancelar o envio arriscaria um comando duplicado, porque o servidor pode já tê-lo executado.
5. **Resposta:** vira só som e log, e a TV Box volta ao ocioso.

```
POST {SERVER_URL}/v1/utterance      multipart/form-data
  audio   WAV mono 16 kHz PCM16
  meta    {"utterance_id": uuid, "edge_id", "reason": "wake_word",
           "local_hypothesis": null, "local_confidence": null}
```

Só um comando fica em voo por vez. Da resposta são lidos `status`, `transcript` e `reject_reason`.

A parada falada passa pelo servidor e exige a wake word antes. Para os testes, tenha a [parada manual](#parada-manual) à mão.

Cada interação gera uma linha de log em JSON, e o encerramento (`systemctl stop` ou Ctrl+C) loga um resumo:

```
Interação: {"utterance_id": "...", "wake_at": ..., "speech_end_at": ..., "audio_s": 2.1, "sent_at": ..., "reply_at": ..., "reply_latency_s": 0.40, "result": "enqueued", "outcome": "confirmed", "transcript": "Senta!", "error": null, "reject_reason": null}
Encerrando. Resumo: wake words=12 | sem fala depois (possível falso disparo)=2 | enqueued=8 rejected=2 no_speech=2 | latência fim da fala → resposta: média 0.40s, p95 0.48s (10 respostas)
```

`result` é o `status` do servidor, ou `no_speech` (wake word sem fala) ou `unconfirmed` (sem resposta legível); `outcome` é o retorno que tocou. `speech_end_at` é quando a escuta fechou, `VAD_END_SILENCE_S` depois da última palavra, e é dele que conta o `reply_latency_s`.

Para montar um conjunto de avaliação, `SAVE_UTTERANCES_DIR=utterances` salva cada WAV enviado, com a data e o `utterance_id` no nome. São cerca de 32 KB por segundo de áudio; o disco da TV Box é pequeno. Para ouvir no Anker: `aplay -D plughw:1,0 utterances/<arquivo>.wav`.

## Sons de retorno

Ficam em `media/`. Cada som pode ter variantes (`<nome>_2.wav`, `<nome>_3.wav`...): uma é sorteada a cada vez, sem repetir a anterior. Os sons tocam em fila, um de cada vez.

| Som | Quando |
|---|---|
| `beep` | wake word detectada (sino curto, `scripts/generate_beeps.py`) |
| `processing` | o comando foi enviado ao servidor |
| `confirmed` | o `status` da resposta está em `FALLBACK_OK_STATUSES` |
| `rejected` | o servidor recusou, ou a wake word veio sem fala depois |
| `blocked` | o servidor entendeu, mas o comando está desligado no catálogo dele (`reject_reason: command_disabled`). Sem os WAVs de `blocked`, toca `rejected` |
| `unconfirmed` | timeout, erro de rede ou resposta ilegível; o servidor pode ter executado mesmo assim |

As falas (todos menos o `beep`) são geradas no PC com o Piper, por `scripts/generate_voice.py`, com 250 ms de silêncio no início (o alto-falante USB do Anker corta o começo do som sem isso). Para testar sem ligar o resto:

```bash
uv run python -m sense.say --all            # beep e todas as falas
uv run python -m sense.say blocked --repeat 3
```

## Parada manual

Não depende de voz nem do servidor:

```bash
uv run python -m sense.stop
```

Chama `POST /commands/stop` na go2-api (`GO2_API_URL`) e, em paralelo, avisa o servidor com `POST /v1/cancel {"reason": "stop"}`, sem esperar por ele. Sai com código 0 se a go2-api aceitou a parada, 1 se não aceitou e 2 se falta configuração.

## Onde ajustar cada parâmetro

**No `.env`** (edite e reinicie; detalhes e faixas em `.env.example`):

| Parâmetro | Variável | Padrão |
|---|---|---|
| Endereço do servidor de inferência | `SERVER_URL` | obrigatório |
| Endereço da go2-api (só para a parada manual) | `GO2_API_URL` | — |
| Identificação desta TV Box para o servidor | `EDGE_ID` | nome da máquina |
| Timeout da resposta e do aviso de cancelamento | `FALLBACK_TIMEOUT_S`, `CANCEL_TIMEOUT_S` | 5.0, 0.5 |
| Valores de `status` que contam como confirmado | `FALLBACK_OK_STATUSES` | `enqueued,stopped` |
| Nome do microfone | `MIC_NAME` | `anker` |
| Tamanho do chunk de áudio | `AUDIO_CHUNK_MS` | 30 |
| Tamanho do buffer circular de áudio | `AUDIO_BUFFER_S` | 10.0 |
| Qual wake word (modelo do openWakeWord) | `WAKE_WORD` | `hey_jarvis` |
| Sensibilidade da wake word | `WAKE_THRESHOLD` | 0.85 |
| VAD do Silero na wake word: nota mínima de voz (0 = desligado; 0.5 para ligar) | `WAKE_VAD_THRESHOLD` | 0 |
| Intervalo mínimo entre wake words | `WAKE_COOLDOWN_S` | 2.0 |
| Beep na wake word, e quanto do começo da escuta ele ocupa | `WAKE_SOUND`, `BEEP_IGNORE_S` | 1, 0.9 |
| Áudio de antes da detecção que entra no envio | `PRE_ROLL_S` | 0.3 |
| Silêncio de fim de fala, fala mínima e espera por fala | `VAD_END_SILENCE_S`, `VAD_MIN_SPEECH_S`, `VAD_SPEECH_TIMEOUT_S` | 0.7, 0.2, 5.0 |
| Limiar de voz (múltiplo do ruído de fundo e piso) | `VAD_SPEECH_RATIO`, `VAD_MIN_RMS` | 3.0, 150 |
| Duração máxima do comando | `MAX_UTTERANCE_S` | 8.0 |
| `reason` do envio e pasta para salvar os WAVs | `UTTERANCE_REASON`, `SAVE_UTTERANCES_DIR` | `wake_word`, — |

**Constantes no código** (edite o arquivo e reinicie):

| Parâmetro | Constante | Arquivo | Valor |
|---|---|---|---|
| Acúmulo de áudio tolerado | `MAX_BACKLOG_S` | `sense/thin.py` | 0.2 s |
| Silêncio mantido no fim do áudio enviado | `TAIL_MARGIN_S` | `sense/thin.py` | 0.3 s |
| `reject_reason` que tocam `blocked` | `BLOCKED_REASONS` | `sense/fallback_client.py` | `command_disabled` |
| Timeout do POST da parada manual | `POST_TIMEOUT_S` | `sense/stop.py` | 2.0 s |
| Intervalo do log de diagnóstico | `STATS_INTERVAL_S` | `sense/stats.py` | 60 s |
| Taxa de captura do microfone | `CAPTURE_RATE` | `sense/audio.py` | 48000 Hz |
| Sons e o substituto de cada um quando falta WAV | `MEDIA_DIR`, `SOUND_FALLBACKS` | `sense/audio.py` | `media/`, `blocked → rejected` |
| Tamanho máximo dos logs do serviço | `SystemMaxUse` | `deploy/journald@go2-sense.conf` | 20 MB |

## Diagnóstico

A cada 60 s o log traz uma linha com CPU, pico de RAM e os contadores acumulados:

```
Diagnóstico: cpu=62% ram_pico=203MB | chunks_dropped_late=0 fallback_confirmed=24 fallback_rejected=26 fallback_sent=50 wake_detections=50
```

| Contador | Significa |
|---|---|
| `wake_detections` | wake words detectadas |
| `wake_without_speech` | wake words sem fala depois (possível falso disparo) |
| `wake_ignored_sending` | wake words ignoradas por haver um envio em curso |
| `chunks_dropped_late` | áudio descartado porque o processamento não acompanhou o microfone |
| `fallback_sent` | comandos enviados ao servidor |
| `fallback_confirmed`, `fallback_rejected`, `fallback_blocked`, `fallback_unconfirmed` | resposta aceita, recusada, recusada por comando desligado, ou sem resposta legível |
| `fallback_busy_discards` | comandos descartados por já haver um em voo |

## Estrutura

```
.
├── .env.example               # toda a configuração, comentada
├── media/                     # beeps (generate_beeps.py) e falas de retorno (generate_voice.py)
├── sense/
│   ├── __main__.py            # entrada: python -m sense
│   ├── boot.py                # logging, validação e log de boot
│   ├── config.py              # leitura e validação do .env
│   ├── thin.py                # o laço: wake word, gravação do comando, envio e retorno
│   ├── wake.py                # wake word (openWakeWord) e o VAD opcional dela
│   ├── vad.py                 # fim de fala por volume
│   ├── ring_buffer.py         # últimos segundos de áudio, para o pré-roll
│   ├── audio.py               # microfone e tocador de sons
│   ├── fallback_client.py     # POST /v1/utterance e /v1/cancel no servidor de inferência
│   ├── stats.py               # contadores e log de diagnóstico
│   ├── say.py                 # toca os sons sem ligar o resto: python -m sense.say
│   └── stop.py                # parada manual: python -m sense.stop
├── scripts/
│   ├── generate_voice.py      # falas de retorno com o Piper (roda no PC)
│   ├── generate_beeps.py      # beeps da wake word
│   ├── add_lead_silence.py    # põe o silêncio inicial em falas já geradas
│   ├── generate_sounds.py     # tons simples antigos (sobrescreve as falas de mesmo nome)
│   └── download_models.py     # baixa o modelo hey_jarvis
├── deploy/                    # serviço systemd e limites do journal
├── docs/checklist-laboratorio.md
└── tests/
```

## Testes

```bash
uv sync --extra recognition
uv run pytest
```

O teste do openWakeWord real usa fala sintética (`espeak-ng` + `sox`) e é pulado se faltarem esses programas ou o modelo. O servidor de inferência e a go2-api são trocados por servidores HTTP falsos (`tests/conftest.py`). Nenhum teste usa microfone.

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

NEURON — Núcleo de Estudos de Robótica Interativa da UFLA
