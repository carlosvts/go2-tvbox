# Go2 Voice Control — TV Box

Cliente de captura de áudio que roda na TV box (Amlogic S9xx, Debian 11/Armbian). Captura o microfone Anker e envia o áudio via TCP para o servidor de inferência.

Repositório do servidor (pipeline de IA + controle do robô): [unitreego2](https://github.com/carlosvts/unitreego2).

## Como funciona

```
[Microfone Anker] → client_armbian.py → TCP → [Servidor: inference]
```

O servidor faz todo o processamento (wake word, transcrição, classificação); esta TV box só captura e envia áudio.

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

## Estrutura

```
.
├── run.sh                       # Ponto de entrada — regenera .env e roda o client
├── scripts/
│   ├── generate_env.sh          # Detecta IP do servidor + índices do mic Anker
│   └── find_mic_index.py        # Localiza o índice PyAudio do Anker
├── src/
│   └── client_armbian.py        # Captura e envio de áudio
├── config/
│   └── .env                     # Gerado automaticamente (não editar manualmente)
└── media/
    └── beep.wav                 # Som de confirmação de wake word
```

## `.env` (gerado automaticamente — não edite manualmente)

| Variável | Descrição |
|---|---|
| `PC_SERVER_IP` | IP do servidor — detectado automaticamente (cache → IP conhecido → scan de rede) |
| `TCP_PORT` | Porta do servidor (padrão `9876`) |
| `AUDIO_SAMPLE_RATE`, `AUDIO_CHANNELS`, `AUDIO_CHUNK_MS` | Formato de áudio — deve casar com o servidor |
| `MIC_DEVICE_INDEX` | Índice PyAudio do microfone Anker — detectado automaticamente |
| `ANKER_CARD_INDEX` | Índice ALSA do Anker (usado para tocar o beep de confirmação) |
| `BEEP_FILE` | Caminho absoluto do som de confirmação |

## Uso

```bash
./run.sh
```

Isso executa, nesta ordem:
1. **`scripts/generate_env.sh`** — detecta automaticamente o IP do servidor (com cache e retry) e os índices do microfone Anker (com retry, testando abertura real de stream), regravando `config/.env`.
2. **`src/client_armbian.py`** — inicia a captura e envio de áudio.

### Overrides manuais (se a detecção automática falhar)

```bash
PC_SERVER_IP=192.168.0.111 ./run.sh   # força o IP do servidor
MAX_RETRIES=10 ./run.sh                # mais tentativas antes de desistir
RETRY_DELAY_S=5 ./run.sh               # espera maior entre tentativas
```

## Problemas conhecidos

Ver o registro completo de bugs e soluções no repositório do servidor: [PROBLEMAS_E_SOLUCOES.md](https://github.com/carlosvts/unitreego2/blob/main/PROBLEMAS_E_SOLUCOES.md) — cobre, entre outros: detecção de mic após reboot, detecção de IP, firewall bloqueando a porta do servidor.

## Autores

- Samuel Frizzone Cardoso
- Carlos Vinícius Teixeira de Souza
- Hugo Prado Lima

NEURON — Núcleo de Estudos de Robótica Interativa da UFLA

