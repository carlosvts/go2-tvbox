# Checklist de laboratório

Objetivo: rodar o Sense na TV Box real, primeiro só com a gramática local e depois com o fallback para o servidor de inferência, e medir RAM, CPU e latência por 10+ minutos em cada rodada.

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
- [ ] `cp .env.example .env`, com `GO2_API_URL` real e sem `SERVER_URL`.
- [ ] `uv run python scripts/validate_commands.py` imprime `OK` (anote a versão da API).
- [ ] Serviço instalado conforme o topo de `deploy/go2-sense.service` (ajustar `User` e caminhos).

## 1. Rodada só com a gramática (sem `SERVER_URL`)

- [ ] `sudo systemctl restart go2-sense`; anote a hora.
- [ ] `LOGS | grep "Sense iniciando"` mostra o commit esperado, e o log traz `SERVER_URL não definido: fallback desligado`.
- [ ] O log mostra `Microfone aberto: ...` com `card ALSA:` diferente de `None`.
- [ ] O log mostra `Reconhecedor pronto: ...`.
- [ ] "hey jarvis": toca o beep e o log mostra `Wake word detectada`.
- [ ] "hey jarvis" + cada uma das frases do `commands.json`, 3 vezes cada. Para cada frase, anote quantas viraram `Enviado: ...`.
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

### Desvio de obstáculo

- [ ] go2-api com `GET /safety/obstacle-avoidance` respondendo `enabled: true` ou `false` (se vier `null`, todo movimento é barrado: anote o `raw` e ajuste a go2-api).
- [ ] Desligue o desvio (`PUT` com `{"enabled": false}`) e diga "andar para frente": o log mostra `Desvio de obstáculo desligado: mandando ligar` e `Desvio de obstáculo ligado`, e só então `Enviado: move`.
- [ ] Com uma caixa a 1 m na frente, "andar para frente": anote se o robô para, desvia ou encosta. É o teste que diz se o desvio vale para o `move` da API.
- [ ] "senta" e "para" funcionam mesmo com a go2-api sem o endpoint (nesse caso só os movimentos são barrados).

## 2. Rodada com o fallback

- [ ] Servidor de inferência no ar. Ponha `SERVER_URL` (e, se quiser, `EDGE_ID`) no `.env` e reinicie; anote a hora.
- [ ] Frase conhecida ("senta"): executa como antes, e o servidor recebe `POST /v1/cancel` com `local_command`.
- [ ] "para": o robô para; o log da TV Box mostra `Parada:` e o servidor recebe `POST /v1/cancel` com `stop`. Nenhum `/v1/utterance` para essa fala.
- [ ] "andar para frente": anda, **não** para. Se parar, o log `Parada: ...` mostra o que o Vosk ouviu.
- [ ] Frase livre ("por favor anda um pouco para frente"): toca o som de "processando", o log mostra `Fallback (...)` e `Fallback: X.Xs de áudio`, e depois toca o som do resultado. Confira no servidor se o áudio recebido tem a frase inteira.
- [ ] Duas frases livres em seguida, a segunda antes de a primeira responder: a segunda é descartada (`Fallback descartado ... já há uma frase em voo`).
- [ ] Derrube o servidor e diga uma frase livre: som de "não confirmado" e log `Fallback ... não confirmado`. "para" e "senta" continuam funcionando; o log traz `Cancel (...) não chegou ao servidor`.
- [ ] Wake word seguida de silêncio, 5 vezes: nenhuma chamada ao servidor (`fallback_sent` não cresce). Se crescer, o ruído do ambiente está passando por voz.

Medições da rodada (mesma tabela da rodada 1, mais):

| Medida | Como obter | Valor |
|---|---|---|
| Frases enviadas / confirmadas / recusadas / sem resposta | `fallback_sent`, `fallback_confirmed`, `fallback_rejected`, `fallback_unconfirmed` | |
| Tempo do fim da fala até o som de resultado | cronômetro ou vídeo | |
| Áudio descartado por atraso | `chunks_dropped_late` | |

## 4. Fechamento

- [ ] `journalctl --namespace=go2-sense | grep "Sense iniciando"` lista as partidas, com horário e commit.
- [ ] `journalctl --namespace=go2-sense --disk-usage` está abaixo de 20 MB.
- [ ] `df -h /` final anotado.
- [ ] Teste de configuração inválida: `GO2_API_URL=xyz` no `.env` e restart. O log lista o problema e `systemctl status go2-sense` mostra o serviço parado (sem ficar reiniciando). Volte o valor correto.
- [ ] Teste de API fora do ar: pare a go2-api e dê um comando. Log `Descartado: ... API fora do ar`; ao religar a API, o comando antigo **não** é enviado.
- [ ] Teste de microfone: desconecte o Anker por 10 s e reconecte. O serviço reinicia sozinho e volta a ouvir.

## Não validado sem hardware

Tudo abaixo foi escrito sem poder ser executado; se algo falhar no laboratório, é aqui que se deve olhar primeiro.

1. **Instalação em aarch64 / Debian 11.** O lock foi resolvido e instalado só em x86_64. Em especial: compilação do PyAudio, wheels de `onnxruntime` e `tflite-runtime`, e o espaço em disco consumido (313 MB medidos no PC).
2. **`sense/audio.py` inteiro.** A classe `Microphone` nunca abriu um dispositivo real: a busca pelo nome, a abertura a 48 kHz mono, o `drop_backlog` e o número do card ALSA tirado do nome (`hw:N,0`). Se o nome vier em outro formato, o beep fica mudo e o log mostra `card ALSA: None`.
3. **Beep.** `aplay` no card do Anker, e se o eco dele atrapalha o reconhecimento (os 0,9 s de descarte vêm do servidor antigo).
4. **Acurácia com voz real.** Wake word e frases só foram testadas com voz sintética do `espeak-ng`. Com ela, entre as frases curtas só "alonga" e "desligar motores" saíram certas; "deita" saiu com confiança abaixo do limiar e "levanta", "senta", "para" e "coração" não foram reconhecidas. Os limiares `WAKE_THRESHOLD` e `STT_MIN_CONFIDENCE` são pontos de partida, não valores calibrados.
5. **CPU e RAM na TV Box.** Não sei se openWakeWord + Vosk rodam em tempo real nos 4 núcleos ARM com 1,8 GB. Se não rodarem, `chunks_dropped_late` cresce.
6. **Decimação 48 kHz → 16 kHz sem filtro.** Mantida igual à do cliente antigo; pode custar acurácia.
7. **Servidor de inferência real.** O cliente só falou com um servidor falso. Os valores de `status` que contam como confirmado (`FALLBACK_OK_STATUSES`) são um palpite, e o formato exato que o servidor espera no multipart e no `/v1/cancel` não foi conferido contra ele.
8. **Serviço systemd.** A unit não foi instalada em lugar nenhum: usuário `minipc`, caminho `/home/minipc/go2-tvbox`, grupo `audio`, `LogNamespace` no systemd do Debian 11 e a ordem de boot em relação ao USB do Anker.
9. **Robô.** Os POSTs foram testados contra uma API falsa e o `validate_commands.py` contra a go2-api real sem robô. Nenhum comando chegou a um Go2.
10. **Fim de fala por volume.** No fallback, a escuta só termina depois de 0,6 s sem voz, e "voz" é volume acima de 3× o ruído de fundo. Com microfone e ruído reais isso pode cortar cedo ou esticar até `MAX_UTTERANCE_S`; os números estão no topo de `sense/recognizer.py`.
11. **Sons de retorno.** São tons gerados por `scripts/generate_sounds.py`; nunca tocaram no Anker, e não sei se o volume está bom nem se o som de "processando" atrapalha a próxima wake word.
