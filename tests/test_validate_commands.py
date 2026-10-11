from scripts.validate_commands import find_problems

DAMP = {"method": "POST", "endpoint": "/commands/posture", "sport_cmd": "Damp", "params": ["cmd"]}
API = [{"name": "damp", **DAMP}, {"name": "sit", **DAMP, "sport_cmd": "Sit"}]


def test_arquivo_que_confere_nao_tem_problemas():
    data = {"phrases": {"desligar motores": "damp"}, "commands": {"damp": DAMP}}
    assert find_problems(data, API) == []


def test_comando_que_a_api_nao_tem_e_orfao():
    data = {"phrases": {}, "commands": {"front_flip": DAMP}}
    assert find_problems(data, API) == ["comando 'front_flip' não existe na API (órfão)"]


def test_campo_divergente_e_apontado():
    data = {"phrases": {}, "commands": {"damp": {**DAMP, "endpoint": "/commands/gesture"}}}
    (problem,) = find_problems(data, API)
    assert "endpoint" in problem and "/commands/gesture" in problem


def test_frase_com_args_e_conferida_pelo_nome_do_comando():
    data = {
        "phrases": {"anda": {"command": "damp", "args": {}}, "voa": {"command": "fly", "args": {}}},
        "commands": {"damp": DAMP},
    }
    (problem,) = find_problems(data, API)
    assert "'voa'" in problem and "órfã" in problem


def test_frase_sem_comando_e_orfa():
    data = {"phrases": {"senta": "sit"}, "commands": {"damp": DAMP}}
    (problem,) = find_problems(data, API)
    assert "'senta'" in problem and "órfã" in problem
