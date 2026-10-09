import json

import pytest

pytest.importorskip("rapidfuzz")

from sense.commands import (  # noqa: E402
    COMMANDS_FILE,
    INTERPRETER_FILE,
    CommandsError,
    load_intents,
)
from sense.interpreter import Interpreter, interpretar  # noqa: E402

DATA = json.loads(INTERPRETER_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("texto", "comando"),
    [
        ("por favor anda para frente agora", "andar_frente"),
        ("anda para frente", "andar_frente"),
        ("vira pra esquerda", "girar_esquerda"),
        ("gira para a direita", "girar_direita"),
        ("caminha pra trás", "andar_tras"),
        ("vai para a direita", "andar_direita"),
        ("andar para esquerda", "andar_esquerda"),
        ("anda para a direta", "andar_direita"),  # erro de transcrição
        ("robô senta aí", "sentar"),
        ("pode levantar", "levantar"),
        ("deita", "deitar"),
        ("cumprimente", "acenar"),
        ("desligar os motores", "desligar_motores"),
        ("para", "parar"),
        ("pare agora", "parar"),
        ("pode parar", "parar"),
        ("para para para", "parar"),
        ("não para", "parar"),  # a negação não segura a parada
        ("vai parar", "parar"),  # verbo sem direção não impede a parada
    ],
)
def test_frases_que_viram_comando(texto, comando):
    assert interpretar(texto) == comando


@pytest.mark.parametrize(
    "texto",
    [
        "não anda para frente",
        "nunca senta",
        "qual é o seu nome",
        "para frente",  # direção sem verbo
        "anda",  # verbo sem direção
        "anda para frente e vira à direita",
        "anda para frente e para trás",
        "senta e levanta",
        "vai para a cozinha",  # "para" no meio de uma frase longa
        "motores",  # só metade de "desligar motores"
        "desligar",
        "",
    ],
)
def test_frases_que_nao_viram_comando(texto):
    assert interpretar(texto) is None


@pytest.mark.parametrize("direcao", ["frente", "trás", "direita", "esquerda"])
@pytest.mark.parametrize("verbo", ["anda", "andar", "vira", "gira", "caminha"])
@pytest.mark.parametrize("prep", ["para", "pra", "para a"])
def test_o_para_de_um_movimento_nunca_vira_parada(verbo, prep, direcao):
    assert interpretar(f"{verbo} {prep} {direcao}") != "parar"


def test_resultado_traz_nota_motivo_e_marca_a_parada():
    interpreter = Interpreter.load()
    stop = interpreter.interpret("pare")
    assert (stop.id, stop.is_stop, stop.score) == ("parar", True, 100.0)
    typo = interpreter.interpret("anda para a direta")
    assert 80 <= typo.score < 100 and not typo.is_stop
    assert interpreter.interpret("para frente").reason == "direção 'frente' sem verbo"


def test_frase_com_dois_movimentos_vai_para_o_log(caplog):
    caplog.set_level("INFO")
    assert interpretar("anda para frente e vira à direita") is None
    assert "mais de um verbo ou direção (andar, girar, direita, frente)" in caplog.text


def test_palavra_curta_so_vale_identica():
    # "rei" está a uma letra de "ré", mas com 2 letras não há fuzzy.
    assert interpretar("anda de rei") is None
    assert interpretar("anda de ré") == "andar_tras"


def test_palavra_de_um_vocabulario_nao_entra_em_fuzzy_com_outro():
    # "deita" está a 83 de "direita": sem o cuidado, "vai deita" andaria de lado.
    assert interpretar("deita aí") == "deitar"
    assert interpretar("vai deita") == "deitar"


def test_direcao_nova_custa_uma_linha():
    data = json.loads(json.dumps(DATA))
    data["direcoes"]["diagonal"] = ["diagonal"]
    assert Interpreter(data).interpret("anda na diagonal").id == "andar_diagonal"


def test_todo_id_mapeado_vira_um_comando_da_api():
    intents = load_intents()
    assert intents["parar"].endpoint == "/commands/stop"
    assert intents["sentar"].body == {"cmd": "sit"}
    assert intents["girar_esquerda"].body == {"vx": 0.0, "vy": 0.0, "vyaw": 0.5, "duration_s": 1.0}
    assert "girar_frente" not in intents  # combinação sem comando, de propósito


@pytest.mark.parametrize(
    ("mudanca", "mensagem"),
    [
        (lambda d: d["comandos"].pop("sentar"), "'sentar' não tem entrada"),
        (lambda d: d["comandos"].update(voar="sit"), "'voar' não é um ID"),
        (lambda d: d["comandos"].update(sentar="sitt"), "comando inexistente 'sitt'"),
        (lambda d: d["comandos"].update(andar_frente="move"), "exige args"),
    ],
)
def test_interpreter_json_inconsistente_falha(tmp_path, mudanca, mensagem):
    data = json.loads(json.dumps(DATA))
    mudanca(data)
    path = tmp_path / "interpreter.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CommandsError, match=mensagem):
        load_intents(path, COMMANDS_FILE)
