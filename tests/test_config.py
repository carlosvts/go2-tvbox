import pytest

from sense.config import ConfigError, load_config

SERVER = "http://192.168.0.10:9000"
API = "http://192.168.0.10:8000"


def test_so_a_url_do_servidor_e_obrigatoria():
    config = load_config(env={"SERVER_URL": SERVER + "/"})
    assert config.server_url == SERVER  # sem a barra final
    assert config.api_url is None  # só a parada manual usa
    assert config.chunk_ms == 30
    assert config.wake_vad_threshold == 0.0  # VAD da wake word desligado
    assert (config.fallback_timeout_s, config.cancel_timeout_s) == (5.0, 0.5)
    assert config.audio_buffer_s == 10.0
    assert config.beep_ignore_s == 0.9
    assert config.fallback_ok_statuses == {"enqueued", "stopped"}  # os do go2-server


def test_configuracao_completa():
    config = load_config(
        env={
            "SERVER_URL": SERVER,
            "GO2_API_URL": API + "/",
            "EDGE_ID": "tvbox-sala",
            "WAKE_VAD_THRESHOLD": "0.5",
            "FALLBACK_OK_STATUSES": "Enqueued, stopped",
        }
    )
    assert config.api_url == API
    assert config.edge_id == "tvbox-sala"
    assert config.wake_vad_threshold == 0.5
    assert config.fallback_ok_statuses == {"enqueued", "stopped"}


def test_chaves_antigas_do_edge_no_env_sao_ignoradas():
    env = {"SERVER_URL": SERVER, "VOSK_MODEL_PATH": "x", "STOP_FAILSAFE": "talvez", "COOLDOWN_S": "-1"}
    assert load_config(env=env).server_url == SERVER


@pytest.mark.parametrize(
    ("env", "mensagem"),
    [
        ({}, "SERVER_URL não definido"),
        ({"SERVER_URL": "servidor"}, "SERVER_URL=.* inválido"),
        ({"SERVER_URL": SERVER, "GO2_API_URL": "192.168.0.10:8000"}, "GO2_API_URL=.* inválido"),
        ({"SERVER_URL": SERVER, "AUDIO_CHUNK_MS": "100"}, "AUDIO_CHUNK_MS=100 fora"),
        ({"SERVER_URL": SERVER, "WAKE_THRESHOLD": "alto"}, "WAKE_THRESHOLD='alto' não é"),
        ({"SERVER_URL": SERVER, "WAKE_SOUND": "talvez"}, "WAKE_SOUND='talvez' inválido"),
        ({"SERVER_URL": SERVER, "AUDIO_BUFFER_S": "5"}, "AUDIO_BUFFER_S tem de ser"),
        ({"SERVER_URL": SERVER, "SENSE_MODE": "edge"}, "SENSE_MODE='edge'"),
    ],
)
def test_config_invalida_falha_com_mensagem_clara(env, mensagem):
    with pytest.raises(ConfigError, match=mensagem):
        load_config(env=env)


def test_lista_todos_os_problemas_de_uma_vez():
    with pytest.raises(ConfigError) as error:
        load_config(env={"WAKE_THRESHOLD": "2", "PRE_ROLL_S": "-1"})
    assert str(error.value).count("\n  - ") == 3
