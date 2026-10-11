import pytest

from sense.config import ConfigError, load_config

API = "http://192.168.0.10:8000"


def test_so_a_url_da_api_e_obrigatoria():
    config = load_config(env={"GO2_API_URL": API + "/"})
    assert config.api_url == API  # sem a barra final
    assert config.chunk_ms == 30
    assert config.server_url is None  # fallback desligado
    assert config.stop_failsafe is True
    assert config.wake_vad_threshold == 0.0  # VAD da wake word desligado
    assert config.require_obstacle_avoidance is True
    assert (config.stt_min_confidence, config.max_local_utterance_s) == (0.7, 3.0)
    assert (config.fallback_timeout_s, config.cancel_timeout_s) == (5.0, 0.5)
    assert config.audio_buffer_s == 10.0


def test_fallback_configurado():
    config = load_config(
        env={
            "GO2_API_URL": API,
            "SERVER_URL": "http://192.168.0.10:9000/",
            "EDGE_ID": "tvbox-sala",
            "STOP_FAILSAFE": "0",
            "WAKE_VAD_THRESHOLD": "0.5",
            "FALLBACK_OK_STATUSES": "OK, feito",
        }
    )
    assert config.server_url == "http://192.168.0.10:9000"
    assert config.edge_id == "tvbox-sala"
    assert config.stop_failsafe is False
    assert config.wake_vad_threshold == 0.5
    assert config.fallback_ok_statuses == {"ok", "feito"}


@pytest.mark.parametrize(
    ("env", "mensagem"),
    [
        ({}, "GO2_API_URL não definido"),
        ({"GO2_API_URL": "192.168.0.10:8000"}, "GO2_API_URL=.* inválido"),
        ({"GO2_API_URL": API, "SERVER_URL": "servidor"}, "SERVER_URL=.* inválido"),
        ({"GO2_API_URL": API, "AUDIO_CHUNK_MS": "100"}, "AUDIO_CHUNK_MS=100 fora"),
        ({"GO2_API_URL": API, "WAKE_THRESHOLD": "alto"}, "WAKE_THRESHOLD='alto' não é"),
        ({"GO2_API_URL": API, "STOP_FAILSAFE": "talvez"}, "STOP_FAILSAFE='talvez' inválido"),
        ({"GO2_API_URL": API, "AUDIO_BUFFER_S": "5"}, "AUDIO_BUFFER_S tem de ser"),
        ({"GO2_API_URL": API, "MAX_UTTERANCE_S": "2"}, "MAX_UTTERANCE_S não pode"),
    ],
)
def test_config_invalida_falha_com_mensagem_clara(env, mensagem):
    with pytest.raises(ConfigError, match=mensagem):
        load_config(env=env)


def test_lista_todos_os_problemas_de_uma_vez():
    with pytest.raises(ConfigError) as error:
        load_config(env={"WAKE_THRESHOLD": "2", "COOLDOWN_S": "-1"})
    assert str(error.value).count("\n  - ") == 3
