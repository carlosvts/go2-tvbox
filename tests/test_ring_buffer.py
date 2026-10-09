from sense.ring_buffer import RingBuffer

SECOND = bytes(32000)  # 1 s de áudio a 16 kHz, 16 bits


def numbered(n):
    """1 s de áudio em que cada amostra vale `n`."""
    return n.to_bytes(2, "little") * 16000


def test_guarda_so_os_ultimos_segundos():
    buffer = RingBuffer(2.0)
    for n in range(5):
        buffer.append(numbered(n))
    assert buffer.clip(10) == numbered(3) + numbered(4)


def test_recorte_e_contado_a_partir_do_audio_mais_novo():
    buffer = RingBuffer(10.0)
    for n in range(5):
        buffer.append(numbered(n))
    assert buffer.clip(2.0, 1.0) == numbered(3)
    assert buffer.clip(1.0) == numbered(4)
    assert len(buffer.clip(1.5, 0.25)) == 40000  # sempre amostras inteiras


def test_recorte_fora_do_que_existe_e_cortado():
    buffer = RingBuffer(10.0)
    buffer.append(SECOND)
    assert buffer.clip(5.0, -1.0) == SECOND  # começa antes e termina "no futuro"
    assert RingBuffer(10.0).clip(1.0) == b""
