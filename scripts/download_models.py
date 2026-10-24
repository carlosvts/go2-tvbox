#!/usr/bin/env python3
"""Baixa o modelo da wake word (só onde o extra `recognition` está instalado).

- openWakeWord: hey_jarvis e os modelos de features, para dentro do pacote.

Uso: python scripts/download_models.py
"""

import openwakeword.utils

openwakeword.utils.download_models(model_names=["hey_jarvis"])
print("openWakeWord: hey_jarvis ok")
