# Go2 Sense — TV Box

Cliente de voz do Go2. Roda na TV Box: captura o microfone Anker, espera a wake word ("hey jarvis") e leva o comando falado até a [go2-api](https://github.com/carlosvts/go2-api).

Há dois modos, escolhidos por `SENSE_MODE` no `.env`:

```
edge   [Anker] → TV Box: wake word + Vosk (gramática) ──┬── parada / comando local ──HTTP──▶ go2-api
                                                        └── o que a gramática não resolve
                                                              └─ áudio ──HTTP──▶ servidor de inferência ──▶ go2-api

thin   [Anker] → TV Box: só a wake word ── áudio do comando ──HTTP──▶ servidor de inferência ──▶ go2-api
```

| | edge (padrão) | thin |
|---|---|---|
| Na TV Box | wake word, Vosk com gramática, POST na go2-api | só wake word e gravação do comando |
| Quem interpreta | a gramática local; o servidor só no fallback | sempre o servidor |
| Servidor de inferência | opcional (`SERVER_URL`) | obrigatório |
| Carrega o Vosk | sim | não |

Nos dois modos, quem interpreta, enfileira e executa as frases enviadas é o servidor: a TV Box não tem fila e não executa nada do que ele responde.

## Índice

- [Como rodar](#como-rodar)
  - [Como serviço na TV Box](#como-serviço-na-tv-box)
  - [O que esperar no log](#o-que-esperar-no-log)
- [Comandos de voz](#comandos-de-voz)
- [Modo thin](#modo-thin)
- [Parada manual](#parada-manual)
- [Fallback para o servidor de inferência](#fallback-para-o-servidor-de-inferência)
- [Onde ajustar cada parâmetro](#onde-ajustar-cada-parâmetro)
- [Diagnóstico](#diagnóstico)
- [Estrutura](#estrutura)
- [Testes](#testes)
- [Especificações da TV Box](#especificações-da-tv-box)
- [Autores](#autores)

## Como rodar

A go2-api precisa estar no ar e alcançável pela rede (`curl http://<IP_DA_API>:8000/status` responde 200). Requer Python 3.10; o [uv](https://docs.astral.sh/uv/) baixa um se o sistema não tiver.

Na TV Box, dentro do repo:

1. Instale as dependências de sistema (o PyAudio compila do fonte em aarch64):

   ```bash
   sudo apt install portaudio19-dev gcc python3-dev
   ```

2. Instale o projeto e baixe os modelos (cerca de 300 MB + 60 MB; confira `df -h` antes):

   ```bash
   uv sync --no-dev --extra capture --extra recognition
   uv run python scripts/download_models.py
   ```

3. Crie o `.env`:

   ```bash
   cp .env.example .env
   ```

   e deixe nele o endereço da go2-api e, se houver, o do servidor de inferência:

   ```
   GO2_API_URL=http://<IP_DA_API>:8000
   SERVER_URL=http://<IP_DO_SERVIDOR>:<PORTA>
   ```

   Sem `SERVER_URL` o fallback fica desligado e só a gramática local funciona. Para o modo thin, acrescente `SENSE_MODE=thin` (aí o `SERVER_URL` é obrigatório).

4. Confira o mapa de comandos contra a API (tem de imprimir `OK`):

   ```bash
   uv run python scripts/validate_commands.py
   ```

5. Rode:

   ```bash
   uv run python -m sense
   ```

   O log deve mostrar `modo=edge` (ou `modo=thin`), `Reconhecedor pronto` (no thin, `Wake word pronta`) e `Microfone aberto`. Diga "hey jarvis", espere o beep e diga uma frase.

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
Sense iniciando | modo=edge | código=sense-mode-config@1a2b3c4
Configuração: api_url=http://192.168.0.10:8000 chunk_ms=30 mic_name=anker ...
```

Configuração inválida (`GO2_API_URL` faltando, número fora da faixa) derruba o processo na partida, com todos os problemas listados. Como serviço, ele para sem ficar reiniciando.

## Comandos de voz

Diga "hey jarvis", espere o beep e diga a frase.

| Frase | Comando | O que faz |
|---|---|---|
| "levanta" | `stand_up` | levanta |
| "senta" | `sit` | senta |
| "deita" | `stand_down` | deita de forma controlada |
| "para", "pare", "parar" ou "stop" | `stop` | para de andar, continua de pé |
| "cumprimentar" ou "cumprimente" | `hello` | acena |
| "alonga" | `stretch` | alonga |
| "coração" | `finger_heart` | gesto de coração |
| "desligar motores" | `damp` | **tira a força dos motores: de pé, o robô cai** |
| "andar para frente" | `move` | anda para frente a 0,3 m/s por 1 s |
| "andar para trás" | `move` | anda para trás a 0,5 m/s por 1 s |
| "virar para a direita" | `move` | gira para a direita a 0,5 rad/s por 1 s |
| "virar para a esquerda" | `move` | gira para a esquerda a 0,5 rad/s por 1 s |
| "andar para a direita" | `move` | anda de lado para a direita a 0,5 m/s por 1 s |
| "andar para a esquerda" | `move` | anda de lado para a esquerda a 0,5 m/s por 1 s |

As frases com direita e esquerda valem com ou sem o "a" ("virar para direita").

### Desvio de obstáculo antes de andar

Antes de cada movimento (andar ou virar), o Sense pergunta à go2-api se o desvio de obstáculo do robô está ligado (`GET /safety/obstacle-avoidance`). Se não estiver, manda ligar (`PUT`) e pergunta de novo. Sem a confirmação de `enabled: true`, o movimento **não sai**: toca o som de recusa e o log mostra `Descartado: ... sem o desvio de obstáculo ligado`. Postura, gesto e parada não passam por essa conferência.

Isso exige uma go2-api com esse endpoint. Duas ressalvas:

- A go2-api ainda não comprovou que o desvio ligado filtra o `move` dela; o Sense garante o desvio ligado, não que o robô desvie.
- Os movimentos que o servidor de inferência executa (fallback) não passam por aqui: quem tem de conferir é o servidor.

Para desligar a conferência, `REQUIRE_OBSTACLE_AVOIDANCE=0` no `.env`.

### Editar as frases

As frases são geradas: edite `config/phrases.json` e rode o gerador, que reescreve `phrases` e `stop_words` em `config/commands.json`.

```bash
uv run python scripts/generate_commands.py
```

O `phrases.json` tem listas curtas, e a gramática é o produto delas:

| Lista | O que é |
|---|---|
| `prefixos`, `sufixos` | texto opcional antes e depois de toda frase (ex.: `["", "robô"]` dobra as frases) |
| `parada` | as palavras de parada e o comando delas |
| `acoes` | frase → comando de postura ou gesto |
| `direcoes` | cada direção e as ligações aceitas antes dela ("para", "para a") |
| `movimentos` | por verbo e direção, só os valores que diferem do `padrao` do `move` |

No referencial do robô, `vy` e `vyaw` positivos são para a esquerda. As palavras precisam existir no vocabulário do modelo Vosk; palavra desconhecida é ignorada por ele.

A seção `commands` do `commands.json` é a cópia das entradas de `GET /capabilities` usadas, e o gerador não mexe nela. Depois de editar, valide contra a API:

```bash
uv run python scripts/validate_commands.py http://192.168.0.10:8000
```

O script falha (código 1) se algum comando do arquivo não existir na API ou divergir dela.

## Modo thin

Com `SENSE_MODE=thin` a TV Box não reconhece comando nenhum. Trocar de modo é editar o `.env` e reiniciar o serviço.

1. **Ocioso:** o microfone alimenta um buffer circular e a wake word. Nenhum áudio sai da TV Box.
2. **Wake word** (`WAKE_WORD`, limiar `WAKE_THRESHOLD`): toca o beep (`WAKE_SOUND`) e começa a escuta. Outra wake word só vale depois de `WAKE_COOLDOWN_S`.
3. **Escuta:** o fim da fala é detectado por volume. A escuta acaba com `VAD_END_SILENCE_S` de silêncio depois da fala, em `MAX_UTTERANCE_S` (a fala é cortada), ou em `VAD_SPEECH_TIMEOUT_S` sem fala nenhuma (descartada, com o som de recusa).
4. **Envio:** `POST {SERVER_URL}/v1/utterance` com o WAV e `meta` (`reason: "wake_word"`, `local_hypothesis` e `local_confidence` nulos). O áudio começa `PRE_ROLL_S` antes da detecção, então traz o final da wake word e o beep. Uma wake word dita enquanto a resposta não chega é ignorada, com log: cancelar o envio arriscaria um comando duplicado, porque o servidor pode já tê-lo executado.
5. A resposta vira só som (`confirmed`, `rejected`, `unconfirmed`) e log, e a TV Box volta ao ocioso.

A parada falada, no thin, passa pelo servidor e exige a wake word antes. Para os testes, tenha a [parada manual](#parada-manual) à mão.

### Comparar edge e thin

No thin, cada interação gera uma linha de log em JSON, e o encerramento do serviço (`systemctl stop` ou Ctrl+C) loga um resumo:

```
Interação: {"utterance_id": "...", "wake_at": ..., "speech_end_at": ..., "audio_s": 2.1, "sent_at": ..., "reply_at": ..., "reply_latency_s": 0.84, "result": "ok", "transcript": "..."}
Encerrando. Resumo: wake words=12 | sem fala depois (possível falso disparo)=2 | ok=8 rejected=2 no_speech=2 | latência fim da fala → resposta: média 0.91s, p95 1.40s (10 respostas)
```

`result` é o `status` do servidor, ou `no_speech` (wake word sem fala) ou `unconfirmed` (sem resposta legível). No edge, a comparação sai dos contadores da linha `Diagnóstico` e das linhas `Comando:`, `Parada:` e `Fallback`.

Para montar um conjunto de avaliação, `SAVE_UTTERANCES_DIR=utterances` salva cada WAV enviado, com a data e o `utterance_id` no nome. São cerca de 32 KB por segundo de áudio; o disco da TV Box é pequeno.

## Parada manual

Vale nos dois modos e não depende de voz nem do servidor:

```bash
uv run python -m sense.stop
```

Chama `POST /commands/stop` na go2-api e, em paralelo, avisa o servidor com `POST /v1/cancel {"reason": "stop"}`, sem esperar por ele. Sai com código 0 se a go2-api aceitou a parada.

## Fallback para o servidor de inferência

Esta seção vale para o modo edge.


Depois de cada escuta, a decisão segue esta ordem:

1. **Parada:** a frase tem uma palavra de parada. Vai direto à go2-api (`POST /commands/stop`) e, em paralelo, avisa o servidor com `POST /v1/cancel {"reason": "stop"}`. Uma frase de movimento inteira ("andar para frente") não conta: o "para" dela é preposição.
2. **Fallback**, com o motivo:
   - `unk`: a frase tem `[unk]`, não é uma frase do mapa, ou houve voz sem nenhuma palavra reconhecida;
   - `low_conf`: alguma palavra abaixo de `STT_MIN_CONFIDENCE`;
   - `too_long`: fala mais longa que `MAX_LOCAL_UTTERANCE_S`.
3. **Comando local:** o POST de sempre na go2-api. Se a API aceitar, avisa o servidor com `POST /v1/cancel {"reason": "local_command"}`, para a fila dele não retomar depois de um comando mais novo.

A parada nunca depende do servidor: o aviso sai numa thread à parte, com timeout de `CANCEL_TIMEOUT_S`, sem nova tentativa. Com `STOP_FAILSAFE=1` (padrão), uma palavra de parada vale mesmo com confiança baixa ou no meio de `[unk]`; com `0`, a parada duvidosa vai ao fallback.

No fallback, a TV Box espera a pessoa parar de falar e envia o áudio da escuta inteira, com 0,3 s de folga antes e depois:

```
POST {SERVER_URL}/v1/utterance      multipart/form-data
  audio   WAV mono 16 kHz PCM16
  meta    {"utterance_id": uuid, "edge_id", "reason", "local_hypothesis", "local_confidence"}
```

Só uma frase fica em voo por vez; outra que caia no fallback nesse meio tempo é descartada. Da resposta são lidos apenas `status` e `transcript`, para o retorno sonoro:

| Som (`media/`) | Quando |
|---|---|
| `processing.wav` | a frase foi enviada ao servidor |
| `confirmed.wav` | o `status` da resposta está em `FALLBACK_OK_STATUSES` |
| `rejected.wav` | o servidor respondeu outro `status`, ou a frase foi descartada por já haver uma em voo |
| `unconfirmed.wav` | timeout, erro de rede ou resposta ilegível; o servidor pode ter executado mesmo assim |

Sem `SERVER_URL`, o que cairia no fallback é descartado com uma linha de log, e a fala longa de uma frase conhecida é executada normalmente.

## Onde ajustar cada parâmetro

**No `.env`** (edite e reinicie; detalhes e faixas em `.env.example`):

| Parâmetro | Variável | Padrão |
|---|---|---|
| Modo | `SENSE_MODE` | `edge` |
| Endereço da go2-api | `GO2_API_URL` | — |
| Endereço do servidor de inferência (no edge, vazio = sem fallback; obrigatório no thin) | `SERVER_URL` | — |
| Identificação desta TV Box para o servidor | `EDGE_ID` | nome da máquina |
| Timeout do fallback e do aviso de cancelamento | `FALLBACK_TIMEOUT_S`, `CANCEL_TIMEOUT_S` | 5.0, 0.5 |
| Valores de `status` que contam como confirmado | `FALLBACK_OK_STATUSES` | `ok,accepted,queued,executed,stopped` |
| Fala mais longa que isto vai ao fallback | `MAX_LOCAL_UTTERANCE_S` | 3.0 |
| Duração máxima de uma escuta (edge) ou do comando (thin) | `MAX_UTTERANCE_S` | 8.0 |
| Tamanho do buffer circular de áudio | `AUDIO_BUFFER_S` | 10.0 |
| Parada vale mesmo com confiança baixa | `STOP_FAILSAFE` | 1 |
| Movimento só sai com o desvio de obstáculo ligado | `REQUIRE_OBSTACLE_AVOIDANCE` | 1 |
| Nome do microfone | `MIC_NAME` | `anker` |
| Tamanho do chunk de áudio | `AUDIO_CHUNK_MS` | 30 |
| Qual wake word (modelo do openWakeWord) | `WAKE_WORD` | `hey_jarvis` |
| Sensibilidade da wake word | `WAKE_THRESHOLD` | 0.85 |
| Thin: intervalo mínimo entre wake words | `WAKE_COOLDOWN_S` | 2.0 |
| Thin: beep na wake word, e quanto do começo da escuta ele ocupa | `WAKE_SOUND`, `BEEP_IGNORE_S` | 1, 0.6 |
| Thin: áudio de antes da detecção que entra no envio | `PRE_ROLL_S` | 0.3 |
| Thin: silêncio de fim de fala, fala mínima e espera por fala | `VAD_END_SILENCE_S`, `VAD_MIN_SPEECH_S`, `VAD_SPEECH_TIMEOUT_S` | 0.7, 0.2, 5.0 |
| Thin: limiar de voz (múltiplo do ruído de fundo e piso) | `VAD_SPEECH_RATIO`, `VAD_MIN_RMS` | 3.0, 150 |
| Thin: `reason` do envio e pasta para salvar os WAVs | `UTTERANCE_REASON`, `SAVE_UTTERANCES_DIR` | `wake_word`, — |
| Confiança mínima do STT | `STT_MIN_CONFIDENCE` | 0.7 |
| Tempo de escuta após a wake word; com fala em curso, estende até `MAX_UTTERANCE_S` | `COMMAND_TIMEOUT_S` | 2.5 |
| Cooldown de comando repetido | `COOLDOWN_S` | 2.0 |
| Pasta do modelo Vosk | `VOSK_MODEL_PATH` | `models/vosk-model-small-pt-0.3` |

**No `config/phrases.json`:** as frases (depois rode `scripts/generate_commands.py`).

**Constantes no código** (edite o arquivo e reinicie):

| Parâmetro | Constante | Arquivo | Valor |
|---|---|---|---|
| Áudio descartado logo após a wake word (eco e beep) | `POST_WAKE_DISCARD_S` | `sense/recognizer.py` | 0.9 s |
| Tempo em que a wake word fica ignorada após uma escuta | `REARM_S` | `sense/recognizer.py` | 0.5 s |
| Timeout do POST na API | `POST_TIMEOUT_S` | `sense/dispatcher.py` | 2.0 s |
| Acúmulo de áudio tolerado | `MAX_BACKLOG_S` | `sense/edge.py` | 0.2 s |
| Intervalo do log de diagnóstico | `STATS_INTERVAL_S` | `sense/stats.py` | 60 s |
| Taxa de captura do microfone | `CAPTURE_RATE` | `sense/audio.py` | 48000 Hz |
| Folga do recorte e detecção do fim da fala no fallback | `CLIP_MARGIN_S`, `TAIL_SILENCE_S`, `SPEECH_RATIO`, `MIN_SPEECH_RMS` | `sense/recognizer.py` | 0,3 s, 0,6 s, 3×, 150 |
| Sons | `MEDIA_DIR` | `sense/audio.py` | `media/` |
| Tamanho máximo dos logs do serviço | `SystemMaxUse` | `deploy/journald@go2-sense.conf` | 20 MB |

**Sem ajuste hoje:** o Vosk só fecha a frase depois de cerca de 1 s de silêncio. Esse tempo é interno a ele: a versão usada não o expõe no Python e o modelo `small-pt-0.3` não tem arquivo de configuração para isso.

## Diagnóstico

A cada 60 s o log traz uma linha com CPU, pico de RAM e os contadores acumulados:

```
Diagnóstico: cpu=23% ram_pico=310MB | commands_recognized=3 commands_sent=3 no_match=1
```

| Contador | Onde | Significa |
|---|---|---|
| `wake_detections` | reconhecimento | wake words detectadas |
| `commands_recognized` | reconhecimento | frases que viraram comando |
| `stops_recognized` | reconhecimento | paradas reconhecidas |
| `no_match` | reconhecimento | o STT ouviu algo que não é uma frase do mapa (fallback `unk`, se houver servidor) |
| `low_confidence` | reconhecimento | frase certa, confiança abaixo do mínimo (fallback `low_conf`) |
| `too_long` | reconhecimento | frase certa, fala longa demais (fallback `too_long`) |
| `listen_timeouts` | reconhecimento | wake word sem nenhuma fala depois |
| `commands_sent` | POST | a API respondeu 202 |
| `cooldown_discards` | POST | comando repetido dentro do cooldown |
| `api_rejections` | POST | a API respondeu erro (422, 503...) |
| `api_unreachable` | POST | a API não respondeu |
| `obstacle_avoidance_switched_on`, `obstacle_avoidance_unconfirmed` | POST | vezes em que o Sense ligou o desvio de obstáculo, e movimentos barrados por falta de confirmação |
| `wake_without_speech`, `wake_ignored_sending` | thin | wake words sem fala depois, e ignoradas por haver um envio em curso |
| `chunks_dropped_late` | captura | áudio descartado porque o processamento não acompanhou o microfone |
| `fallback_sent` | fallback | frases enviadas ao servidor |
| `fallback_confirmed`, `fallback_rejected`, `fallback_unconfirmed` | fallback | resposta aceita, recusada, ou sem resposta legível |
| `fallback_busy_discards` | fallback | frases descartadas por já haver uma em voo |
| `cancels_sent`, `cancel_failures` | fallback | avisos de cancelamento entregues e perdidos |

API fora do ar ou erro no POST: o comando é descartado, com uma linha de log dizendo o motivo. Não há fila nem nova tentativa na TV Box.

## Estrutura

```
.
├── .env.example               # toda a configuração, comentada
├── config/phrases.json        # listas de onde as frases são geradas
├── config/commands.json       # frase → comando (gerado) e comandos da API
├── media/                     # beep da wake word e sons de retorno do fallback
├── sense/
│   ├── __main__.py            # entrada: python -m sense (escolhe o modo)
│   ├── boot.py                # logging, validação e log de boot
│   ├── config.py              # leitura e validação do .env
│   ├── edge.py                # modo edge: o laço e a execução de cada decisão
│   ├── thin.py                # modo thin: wake word, gravação do comando e envio
│   ├── stop.py                # parada manual: python -m sense.stop
│   ├── wake.py                # wake word (openWakeWord)
│   ├── vad.py                 # fim de fala por volume (thin)
│   ├── audio.py               # microfone e sons
│   ├── ring_buffer.py         # últimos segundos de áudio, para o recorte do fallback
│   ├── recognizer.py          # wake word + STT + decisão (parada, local ou fallback)
│   ├── commands.py            # carrega o mapa de comandos
│   ├── dispatcher.py          # cooldown + POST na go2-api
│   ├── obstacle_guard.py      # liga e confere o desvio de obstáculo antes de um movimento
│   ├── fallback_client.py     # POST /v1/utterance e /v1/cancel no servidor de inferência
│   └── stats.py               # contadores e log de diagnóstico
├── scripts/
│   ├── generate_commands.py   # phrases.json → frases do commands.json
│   ├── validate_commands.py   # confere commands.json contra GET /capabilities
│   ├── generate_sounds.py     # gera os sons de retorno do fallback
│   └── download_models.py     # baixa hey_jarvis e o modelo Vosk
├── deploy/                    # serviço systemd e limites do journal
├── docs/checklist-laboratorio.md
└── tests/
```

## Testes

```bash
uv sync --extra recognition
uv run pytest
```

Os testes com openWakeWord e Vosk reais usam fala sintética (`espeak-ng` + `sox`) e são pulados se faltarem esses programas ou os modelos. O servidor de inferência e a go2-api são trocados por servidores HTTP falsos. Nenhum teste usa microfone.

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
