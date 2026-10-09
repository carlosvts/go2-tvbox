import pytest

from sense.config import RECEIVER, ConfigError, load_config

API = "http://192.168.0.10:8000"


def test_edge_valido():
    config = load_config(env={"SENSE_MODE": "edge", "GO2_API_URL": API + "/"})
    assert config.role == "edge"
    assert config.api_url == API  # sem a barra final
    assert config.receiver_host is None
    assert config.chunk_ms == 30


def test_thin_valido_nao_exige_url_da_api():
    config = load_config(env={"SENSE_MODE": "thin", "RECEIVER_HOST": "192.168.0.10"})
    assert config.role == "thin"
    assert config.api_url is None
    assert config.receiver_host == "192.168.0.10"
    assert config.receiver_port == 9876


def test_receiver_exige_url_mas_nao_sense_mode():
    config = load_config(RECEIVER, env={"GO2_API_URL": API})
    assert config.role == RECEIVER
    with pytest.raises(ConfigError, match="GO2_API_URL não definido"):
        load_config(RECEIVER, env={})


@pytest.mark.parametrize(
    ("env", "mensagem"),
    [
        ({}, "SENSE_MODE não definido"),
        ({"SENSE_MODE": "cloud"}, "SENSE_MODE='cloud' inválido"),
        ({"SENSE_MODE": "edge"}, "GO2_API_URL não definido"),
        ({"SENSE_MODE": "edge", "GO2_API_URL": "192.168.0.10:8000"}, "GO2_API_URL=.* inválido"),
        ({"SENSE_MODE": "thin"}, "RECEIVER_HOST não definido"),
        ({"SENSE_MODE": "edge", "GO2_API_URL": API, "AUDIO_CHUNK_MS": "100"}, "AUDIO_CHUNK_MS=100 fora"),
        ({"SENSE_MODE": "edge", "GO2_API_URL": API, "WAKE_THRESHOLD": "alto"}, "WAKE_THRESHOLD='alto' não é"),
    ],
)
def test_config_invalida_falha_com_mensagem_clara(env, mensagem):
    with pytest.raises(ConfigError, match=mensagem):
        load_config(env=env)


def test_lista_todos_os_problemas_de_uma_vez():
    with pytest.raises(ConfigError) as erro:
        load_config(env={"SENSE_MODE": "thin", "RECEIVER_PORT": "0"})
    assert "RECEIVER_HOST" in str(erro.value)
    assert "RECEIVER_PORT" in str(erro.value)
