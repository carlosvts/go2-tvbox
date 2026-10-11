import json

import pytest

from sense.commands import CommandsError, load_commands, normalize

CMD = {"method": "POST", "endpoint": "/commands/posture", "sport_cmd": "Sit", "params": ["cmd"]}


def write(tmp_path, data):
    path = tmp_path / "commands.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_arquivo_versionado_carrega():
    commands = load_commands()
    assert len(commands.grammar) == len(commands.by_phrase)


def test_desligar_motores_e_damp():
    damp = load_commands().lookup("desligar motores")
    assert damp.sport_cmd == "Damp"
    assert (damp.method, damp.endpoint, damp.body) == ("POST", "/commands/posture", {"cmd": "damp"})


def test_palavras_de_parada_apontam_para_o_stop():
    commands = load_commands()
    assert commands.stop_words == {"para", "pare", "parar", "stop"}
    assert commands.stop.endpoint == "/commands/stop"
    assert all(commands.lookup(word) == commands.stop for word in commands.stop_words)
    assert commands.has_stop_word("andar PARA frente") and not commands.has_stop_word("levanta")


def test_stop_nao_tem_corpo():
    stop = load_commands().lookup("para")
    assert (stop.endpoint, stop.body) == ("/commands/stop", None)


def test_lookup_ignora_acento_caixa_e_pontuacao():
    commands = load_commands()
    assert commands.lookup("CORACAO!") is commands.lookup("coração")
    assert normalize("  Faz   coração. ") == "faz coracao"


def test_frase_fora_do_mapa_devolve_none():
    assert load_commands().lookup("faz um mortal") is None


def test_frases_de_movimento_levam_os_valores_no_corpo():
    commands = load_commands()
    frente = commands.lookup("andar para frente")
    assert (frente.method, frente.endpoint) == ("POST", "/commands/move")
    assert frente.body == {"vx": 0.3, "vy": 0.0, "vyaw": 0.0, "duration_s": 1.0}
    assert commands.lookup("andar para trás").body["vx"] == -0.5
    assert commands.lookup("virar para a direita").body["vyaw"] == -0.5
    assert commands.lookup("virar para a esquerda").body["vyaw"] == 0.5
    assert commands.lookup("andar para a direita").body["vy"] == -0.5
    assert commands.lookup("andar para a esquerda").body["vy"] == 0.5
    # Com e sem o artigo é a mesma coisa.
    assert commands.lookup("virar para direita") == commands.lookup("virar para a direita")


def test_cumprimentar_e_cumprimente_sao_hello():
    commands = load_commands()
    assert commands.lookup("cumprimentar").name == "hello"
    assert commands.lookup("cumprimente") is commands.lookup("cumprimentar")


@pytest.mark.parametrize(
    ("data", "mensagem"),
    [
        ({"phrases": {"senta": "sitt"}, "commands": {"sit": CMD}}, "comando inexistente 'sitt'"),
        ({"phrases": {"senta": "sit", "Senta!": "sit"}, "commands": {"sit": CMD}}, "repetida"),
        (
            {"stop_words": ["para", "pare"], "phrases": {"para": "sit"}, "commands": {"sit": CMD}},
            "toda palavra de `stop_words`",
        ),
        (
            {"phrases": {"anda": "move"}, "commands": {"move": {**CMD, "params": ["vx", "vy"]}}},
            "exige args",
        ),
        (
            {
                "phrases": {"anda": {"command": "move", "args": {"vx": 1.0}}},
                "commands": {"move": {**CMD, "params": ["vx", "vy"]}},
            },
            "args devem ser números",
        ),
    ],
)
def test_arquivo_inconsistente_falha(tmp_path, data, mensagem):
    with pytest.raises(CommandsError, match=mensagem):
        load_commands(write(tmp_path, data))
