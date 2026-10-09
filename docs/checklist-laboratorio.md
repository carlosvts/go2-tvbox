# Checklist de laboratório

Objetivo: rodar o Sense em modo edge e em modo thin na TV Box real, medir RAM, CPU e latência por 10+ minutos em cada um, e confirmar nos logs qual modo cada rodada usou.

Em todos os comandos, `LOGS` é:

```bash
journalctl --namespace=go2-sense --since "<hora de início da rodada>"
```

## Segurança

- [ ] Robô **deitado ou suspenso** nas primeiras rodadas. "desligar motores" manda `damp`: de pé, o robô cai.
- [ ] Combinado quem para o robô e por qual meio (controle físico ou `curl -X POST <API>/commands/stop` pronto num terminal).

## 0. Preparação (uma vez)

- [ ] go2-api no ar, na mesma rede: `curl <API>/status` responde 200.
- [ ] Na TV Box: `python3 --version` e `df -h /` anotados (antes de instalar).
- [ ] `sudo apt install portaudio19-dev gcc python3-dev`
- [ ] `uv sync --no-dev --extra capture --extra recognition` terminou sem erro.
- [ ] `uv run python scripts/download_models.py` terminou sem erro.
- [ ] `df -h /` anotado de novo (quanto a instalação consumiu).
- [ ] `cp .env.example .env`, com `GO2_API_URL` e `RECEIVER_HOST` reais.
- [ ] `uv run python scripts/validate_commands.py` imprime `OK` (anote a versão da API).
- [ ] Serviço instalado conforme o topo de `deploy/go2-sense.service` (ajustar `User` e caminhos).

## 1. Rodada edge

- [ ] `.env` com `SENSE_MODE=edge`; `sudo systemctl restart go2-sense`; anote a hora.
- [ ] `LOGS | grep "Sense iniciando"` mostra `modo=edge` e o commit esperado.
- [ ] O log mostra `Microfone aberto: ...` com `card ALSA:` diferente de `None`.
- [ ] O log mostra `Reconhecedor pronto: ...`.
- [ ] "hey jarvis": toca o beep e o log mostra `Wake word detectada`.
- [ ] "hey jarvis" + cada uma das 8 frases, 3 vezes cada. Para cada frase, anote quantas viraram `Enviado: ...`.
- [ ] O robô executou o que a frase pede.
- [ ] Frase fora do mapa ("hey jarvis, que horas são"): log `Sem comando: ...`, nenhum POST.
- [ ] Mesma frase duas vezes em menos de 2 s: a segunda aparece como `Descartado: ... (cooldown ...)`.
- [ ] Deixe rodando **10+ minutos**, dando um comando a cada 1 ou 2 minutos e com conversa normal por perto.

Medições da rodada:

| Medida | Como obter | Valor |
|---|---|---|
| CPU (média e pior minuto) | `LOGS \| grep Diagnóstico` (campo `cpu=`; 100% = 1 núcleo) | |
| RAM pico | mesma linha, campo `ram_pico=` | |
| RAM atual | `systemctl status go2-sense` (linha `Memory:`) | |
| RAM livre do sistema | `free -h` | |
| Latência wake word → POST | diferença de horário entre `Wake word detectada` e `Enviado:` do mesmo comando | |
| Tempo de escuta | campo `(... s de escuta)` da linha `Comando:` | |
| Duração do POST | campo `(202, ... ms)` da linha `Enviado:` | |
| Falsos disparos da wake word | `wake_detections` menos as vezes em que alguém disse "hey jarvis" | |
| Áudio descartado | `chunks_dropped_late` na última linha `Diagnóstico` | |

A latência **da fala** até o POST é a latência wake word → POST menos o tempo que a pessoa levou para falar a frase; para isolar, cronometre ou grave um vídeo de uma rodada.

## 2. Troca para thin (só `.env` + restart)

- [ ] No PC: `uv sync --no-dev --extra recognition`, `uv run python scripts/download_models.py`, porta TCP 9876 liberada no firewall.
- [ ] No PC: `GO2_API_URL=<API> uv run python -m sense.receiver` mostra `modo=receiver` e `Reconhecedor pronto`.
- [ ] Na TV Box: **só** trocar `SENSE_MODE=thin` no `.env` e `sudo systemctl restart go2-sense`. Nenhum `git checkout`, nenhum `uv sync`. Anote a hora.
- [ ] `LOGS | grep "Sense iniciando"` mostra `modo=thin` e **o mesmo commit** da rodada edge.
- [ ] Log da TV Box: `Conectado ao receptor ...`. Log do PC: `TV Box conectada: ...`.

## 3. Rodada thin

- [ ] Repita os itens de voz da rodada edge (wake word, 8 frases × 3, frase fora do mapa, cooldown). O beep toca na TV Box; `Wake word detectada` e `Enviado:` aparecem no log **do PC**.
- [ ] Deixe rodando **10+ minutos**, do mesmo jeito.
- [ ] Desligue o receptor por 20 s e religue: a TV Box loga `Conexão com o receptor perdida`, depois `Conectado ao receptor`, e `tcp_reconnects` sobe.

Medições da rodada (mesma tabela, com estas diferenças):

| Medida | Como obter | Valor |
|---|---|---|
| CPU e RAM da TV Box | `LOGS \| grep Diagnóstico` na TV Box | |
| CPU e RAM do receptor | linha `Diagnóstico` no terminal do PC | |
| Latência wake word → POST | horários de `Wake word detectada` e `Enviado:` no log do PC | |
| Áudio descartado por atraso | `chunks_dropped_late` (TV Box) | |
| Áudio atrasado na chegada | `chunks_late` (PC) | |
| Lacunas | `gaps` e `chunks_missing` (PC) | |
| Reconexões | `tcp_reconnects` (TV Box) | |

## 4. Fechamento

- [ ] `journalctl --namespace=go2-sense | grep "Sense iniciando"` lista as duas partidas, com horário, `modo=edge` / `modo=thin` e commit: é o registro de qual modo cada rodada usou.
- [ ] `journalctl --namespace=go2-sense --disk-usage` está abaixo de 20 MB.
- [ ] `df -h /` final anotado.
- [ ] Teste de configuração inválida: `SENSE_MODE=xyz` no `.env` e restart. O log lista o problema e `systemctl status go2-sense` mostra o serviço parado (sem ficar reiniciando). Volte o valor correto.
- [ ] Teste de API fora do ar: pare a go2-api e dê um comando. Log `Descartado: ... API fora do ar`; ao religar a API, o comando antigo **não** é enviado.
- [ ] Teste de microfone: desconecte o Anker por 10 s e reconecte. O serviço reinicia sozinho e volta a ouvir.

## Não validado sem hardware

Tudo abaixo foi escrito sem poder ser executado; se algo falhar no laboratório, é aqui que se deve olhar primeiro.

1. **Instalação em aarch64 / Debian 11.** O lock foi resolvido e instalado só em x86_64. Em especial: compilação do PyAudio, wheels de `onnxruntime` e `tflite-runtime`, e o espaço em disco consumido (313 MB medidos no PC).
2. **`sense/audio.py` inteiro.** A classe `Microphone` nunca abriu um dispositivo real: a busca pelo nome, a abertura a 48 kHz mono, o `drop_backlog` e o número do card ALSA tirado do nome (`hw:N,0`). Se o nome vier em outro formato, o beep fica mudo e o log mostra `card ALSA: None`.
3. **Beep.** `aplay` no card do Anker, e se o eco dele atrapalha o reconhecimento (os 0,9 s de descarte vêm do servidor antigo).
4. **Acurácia com voz real.** Wake word e frases só foram testadas com voz sintética do `espeak-ng`. Com ela, só "oi", "alonga" e "desligar motores" saíram certas; "deita" saiu com confiança abaixo do limiar e "levanta", "senta", "para" e "coração" não foram reconhecidas. Os limiares `WAKE_THRESHOLD` e `STT_MIN_CONFIDENCE` são pontos de partida, não valores calibrados.
5. **CPU e RAM na TV Box.** Não sei se openWakeWord + Vosk rodam em tempo real nos 4 núcleos ARM com 1,8 GB. Se não rodarem, `chunks_dropped_late` cresce no modo edge.
6. **Decimação 48 kHz → 16 kHz sem filtro.** Mantida igual à do cliente antigo; pode custar acurácia.
7. **Rede real.** O transporte foi testado em loopback. Wi-Fi com perda, o tamanho efetivo dos buffers de 8 KB e os contadores de atraso em rede de verdade não foram exercitados.
8. **Serviço systemd.** A unit não foi instalada em lugar nenhum: usuário `minipc`, caminho `/home/minipc/go2-tvbox`, grupo `audio`, `LogNamespace` no systemd do Debian 11 e a ordem de boot em relação ao USB do Anker.
9. **Robô.** Os POSTs foram testados contra uma API falsa e o `validate_commands.py` contra a go2-api real sem robô. Nenhum comando chegou a um Go2.
