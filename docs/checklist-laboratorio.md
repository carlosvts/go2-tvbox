# Checklist de laboratório

Objetivo: rodar o Sense na TV Box real com o go2-server e a go2-api, e medir RAM, CPU e latência por 10+ minutos.

Em todos os comandos, `LOGS` é:

```bash
journalctl --namespace=go2-sense --since "<hora de início da rodada>"
```

## Segurança

- [ ] Robô **deitado ou suspenso** nas primeiras rodadas. "desligar os motores" manda `damp`: de pé, o robô cai.
- [ ] Combinado quem para o robô e por qual meio (controle físico ou `uv run python -m sense.stop` pronto num terminal).

## 0. Preparação (uma vez)

- [ ] go2-server no ar, na mesma rede: `curl <SERVIDOR>/health` responde 200, e o log dele mostra `Whisper ... em cuda`.
- [ ] go2-api no ar: `curl <API>/status` responde 200.
- [ ] Na TV Box: `df -h /` anotado antes de instalar.
- [ ] `sudo apt install portaudio19-dev gcc python3-dev`
- [ ] `uv sync --no-dev --extra capture --extra recognition` e `uv run python scripts/download_models.py` sem erro.
- [ ] `cp .env.example .env`, com `SERVER_URL` e `GO2_API_URL` reais.
- [ ] Serviço instalado conforme o topo de `deploy/go2-sense.service` (ajustar `User` e caminhos).

## 1. Rodada de voz

- [ ] `sudo systemctl restart go2-sense`; anote a hora.
- [ ] O log mostra `Sense iniciando` com o commit esperado, `Wake word pronta`, `Servidor de inferência no ar` e `Microfone aberto: ...` com `card ALSA:` diferente de `None`.
- [ ] `uv run python -m sense.say --all` toca o beep e todas as falas no Anker, sem cortar o começo.
- [ ] "hey jarvis", pausa, comando: beep, "processando" e o som do resultado. O log traz `Comando gravado: X.Xs de áudio` e uma linha `Interação: {...}`.
- [ ] Cada comando do catálogo do servidor, 3 vezes. O robô executou o que a frase pede.
- [ ] **Parada falada**: "para", "pare" e "stop" durante um movimento. O robô para; o servidor responde `stopped`.
- [ ] `uv run python -m sense.stop` para o robô, com o servidor no ar e fora do ar.
- [ ] Comando desligado no servidor ("dançar"): toca `blocked` ("esse eu ainda não posso fazer"), não "não entendi".
- [ ] Frase fora do catálogo ("que horas são"): toca `rejected`.
- [ ] "hey jarvis" e silêncio: som de recusa depois de ~5 s, `result: no_speech`, nada enviado.
- [ ] "hey jarvis" de novo antes de a resposta chegar: `Wake word ignorada: ainda esperando a resposta`.
- [ ] Derrube o servidor e diga um comando: `unconfirmed`, e a TV Box volta a ouvir. Suba o servidor de novo: volta a funcionar sem reiniciar a TV Box.
- [ ] Wake word a 1, 2 e 3 m, 20 vezes cada: quantas dispararam.
- [ ] Com TV ou música ao fundo, e com outra pessoa falando.
- [ ] Deixe rodando **1 hora** sem chamar, com ruído normal da sala: `wake_detections` deve ficar em 0.
- [ ] Com o robô andando: anote `wake_without_speech`, escutas truncadas e o nível de ruído (motores e lidar no microfone).
- [ ] Pare o serviço e anote a linha `Encerrando. Resumo: ...`.

Medições da rodada:

| Medida | Como obter | Valor |
|---|---|---|
| Contagem por resultado | resumo no encerramento | |
| Latência VAD fecha → resposta (média e p95) | resumo no encerramento | |
| Wake words sem fala (possíveis falsos disparos) | resumo no encerramento | |
| CPU e pico de RAM | linha `Diagnóstico` | |
| Áudio descartado | `chunks_dropped_late` | |

## 2. Fechamento

- [ ] `journalctl --namespace=go2-sense --disk-usage` está abaixo de 20 MB.
- [ ] Teste de configuração inválida: `SERVER_URL=xyz` no `.env` e restart. O log lista o problema e `systemctl status go2-sense` mostra o serviço parado (sem ficar reiniciando). Volte o valor correto.
- [ ] Reinicie a TV Box: o serviço sobe sozinho.
- [ ] Desconecte o Anker por 10 s e reconecte: o serviço reinicia sozinho e volta a ouvir.

## Já validado (24/10/2026, sem robô e sem go2-api)

Detalhes em `claude/resultados-teste-thin-gpu.md` no projeto Go2.

- Instalação na TV Box (aarch64, Debian 11), microfone do Anker no card 1, `aplay` no mesmo card.
- Decimação 48 → 16 kHz sem filtro: o Anker já corta acima de ~7,5 kHz, então não há aliasing.
- 50 interações com o go2-server (Whisper medium na RTX 3050): resposta 0,40 s em média depois de o VAD fechar; 0 wakes sem fala; CPU ~62%, RAM ~200 MB estável.
- O beep não entra no transcript e não é tomado como fala com `BEEP_IGNORE_S=0.9`.
- WAVs enviados limpos, sem corte no começo nem no fim.

## Ainda não validado

1. **Robô e go2-api.** Nenhum comando chegou a um Go2 por este caminho. Parada falada nunca testada.
2. **Ruído do Go2** (motores, lidar) no microfone e no limiar do VAD.
3. **Serviço systemd.** A unit não foi instalada: usuário `minipc`, caminho `/home/minipc/go2-tvbox`, grupo `audio`, `LogNamespace` no Debian 11 e a ordem de boot em relação ao USB do Anker.
4. **Wake word a distância e falsos disparos em horas de ruído.**
5. **Uma escuta que durou 8 s** (o VAD não fechou) em 50: causa não identificada.
6. **Falas de `blocked`**: ainda não geradas; até lá toca `rejected` no lugar.
