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


def test_stop_nao_tem_corpo():
    stop = load_commands().lookup("para agora")
    assert (stop.endpoint, stop.body) == ("/commands/stop", None)


def test_lookup_ignora_acento_caixa_e_pontuacao():
    commands = load_commands()
    assert commands.lookup("Fica de PE!") is commands.lookup("fica de pé")
    assert normalize("  Faz   coração. ") == "faz coracao"


def test_frase_fora_do_mapa_devolve_none():
    assert load_commands().lookup("faz um mortal") is None


def test_nenhum_comando_com_parametros_numericos():
    names = {command.name for command in load_commands().by_phrase.values()}
    assert not names & {"move", "speed"}


@pytest.mark.parametrize(
    ("data", "mensagem"),
    [
        ({"phrases": {"senta": "sitt"}, "commands": {"sit": CMD}}, "comando inexistente 'sitt'"),
        ({"phrases": {"senta": "sit", "Senta!": "sit"}, "commands": {"sit": CMD}}, "repetida"),
        (
            {"phrases": {}, "commands": {"move": {**CMD, "params": ["vx", "vy"]}}},
            "params .* não suportados",
        ),
    ],
)
def test_arquivo_inconsistente_falha(tmp_path, data, mensagem):
    with pytest.raises(CommandsError, match=mensagem):
        load_commands(write(tmp_path, data))
