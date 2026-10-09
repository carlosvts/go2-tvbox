import json

import pytest

from scripts.generate_commands import COMMANDS_FILE, PHRASES_FILE, generate, render

SOURCE = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))


def test_commands_json_versionado_esta_em_dia():
    current = COMMANDS_FILE.read_text(encoding="utf-8")
    phrases, stop_words = generate(SOURCE)
    assert render(phrases, stop_words, json.loads(current)["commands"]) == current


def test_movimento_e_verbo_ligacao_direcao_com_os_valores_padrao():
    phrases, stop_words = generate(SOURCE)
    assert stop_words == ["para", "pare", "parar", "stop"]
    assert phrases["pare"] == "stop" and phrases["senta"] == "sit"
    assert phrases["andar para frente"]["args"] == {
        "vx": 0.3, "vy": 0.0, "vyaw": 0.0, "duration_s": 1.0,
    }
    assert phrases["virar para a esquerda"] == phrases["virar para esquerda"]
    assert phrases["virar para esquerda"]["args"]["vyaw"] == 0.5
    assert "virar para frente" not in phrases and "andar para a frente" not in phrases


def test_prefixos_e_sufixos_multiplicam_as_frases():
    source = {**SOURCE, "prefixos": ["", "robô"], "sufixos": ["", "agora"]}
    phrases, _ = generate(source)
    assert len(phrases) == 4 * len(generate(SOURCE)[0])
    assert phrases["robô senta agora"] == "sit"
    assert phrases["robô andar para frente"] == phrases["andar para frente"]


def test_frase_gerada_para_dois_comandos_e_erro():
    source = {**SOURCE, "acoes": {**SOURCE["acoes"], "para": "sit"}}
    with pytest.raises(ValueError, match="'para' gerada para dois comandos"):
        generate(source)
