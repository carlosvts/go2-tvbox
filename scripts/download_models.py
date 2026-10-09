#!/usr/bin/env python3
"""Baixa os modelos do reconhecimento (só onde o extra `recognition` está instalado).

- openWakeWord: hey_jarvis e os modelos de features, para dentro do pacote.
- Vosk: vosk-model-small-pt-0.3 (~50 MB em disco), para `models/`.

Uso: python scripts/download_models.py
"""

import io
import urllib.request
import zipfile
from pathlib import Path

import openwakeword.utils

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
VOSK_MODEL = "vosk-model-small-pt-0.3"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL}.zip"

openwakeword.utils.download_models(model_names=["hey_jarvis"])
print("openWakeWord: hey_jarvis ok")

if (MODELS_DIR / VOSK_MODEL).is_dir():
    print(f"Vosk: {VOSK_MODEL} já existe")
else:
    with urllib.request.urlopen(VOSK_URL, timeout=60) as response:
        zipfile.ZipFile(io.BytesIO(response.read())).extractall(MODELS_DIR)
    print(f"Vosk: {VOSK_MODEL} baixado em {MODELS_DIR}")
