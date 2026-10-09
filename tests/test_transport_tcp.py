"""Transporte TCP em loopback, com sockets de verdade."""

import socket
import threading
import time
from collections import Counter

import pytest

from sense.transport.tcp import HEADER, RECONNECT_DELAY_S, TcpReceiver, TcpSender

CHUNK_MS = 30
CHUNK_BYTES = 960


def chunk(n: int) -> bytes:
    """Chunk reconhecível: todos os bytes iguais a n % 256."""
    return bytes([n % 256]) * CHUNK_BYTES


def drain(receiver, into):
    """Corpo da thread de leitura; termina quando o teste fecha o receptor."""
    try:
        into.extend(receiver.chunks())
    except OSError:
        pass


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "tempo esgotado"
        time.sleep(0.01)


@pytest.fixture
def receiver():
    """Receptor numa porta livre, lendo em segundo plano para `received`."""
    counters = Counter()
    receiver = TcpReceiver(0, counters)
    receiver.counters = counters
    receiver.received = []
    threading.Thread(target=drain, args=(receiver, receiver.received), daemon=True).start()
    yield receiver
    receiver.close()


def raw_client(port, handshake=b"SENSE1 16000 30\n"):
    sock = socket.create_connection(("127.0.0.1", port))
    sock.sendall(handshake)
    return sock


def test_chunks_chegam_inteiros_e_em_ordem(receiver):
    counters = Counter()
    sender = TcpSender("127.0.0.1", receiver.port, CHUNK_MS, counters)
    for n in range(50):
        sender.send(chunk(n))
        time.sleep(0.001)
    wait_for(lambda: receiver.counters["chunks_received"] == 50)
    assert receiver.received == [chunk(n) for n in range(50)]
    assert counters == {"chunks_sent": 50}
    assert receiver.counters["gaps"] == 0


def test_beep_volta_pelo_canal_reverso(receiver):
    beeps = []
    sender = TcpSender(
        "127.0.0.1", receiver.port, CHUNK_MS, Counter(), on_beep=lambda: beeps.append(1)
    )
    sender.send(chunk(0))
    wait_for(lambda: receiver.counters["chunks_received"] == 1)
    receiver.send_beep()
    time.sleep(0.05)
    sender.send(chunk(1))
    assert beeps == [1]


def test_receptor_conta_lacunas_pelo_seq(receiver):
    client = raw_client(receiver.port)
    for seq in (0, 1, 5, 6):
        client.sendall(HEADER.pack(seq) + chunk(seq))
    wait_for(lambda: receiver.counters["chunks_received"] == 4)
    assert receiver.counters["gaps"] == 1
    assert receiver.counters["chunks_missing"] == 3


def test_handshake_errado_e_recusado_e_o_receptor_segue_vivo(receiver):
    bad = raw_client(receiver.port, b"OUTRO 16000 30\n")
    wait_for(lambda: bad.recv(1) == b"")  # o receptor fechou
    good = raw_client(receiver.port)
    good.sendall(HEADER.pack(0) + chunk(0))
    wait_for(lambda: receiver.counters["chunks_received"] == 1)


def test_sem_vazao_descarta_em_vez_de_esperar_e_nao_quebra_o_framing():
    # Servidor que aceita mas demora a ler: simula rede ou PC sem dar vazão.
    server = socket.create_server(("127.0.0.1", 0))
    server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
    counters = Counter()
    sender = TcpSender("127.0.0.1", server.getsockname()[1], CHUNK_MS, counters)

    start = time.monotonic()
    for n in range(3000):  # 90 s de áudio de uma vez
        sender.send(chunk(n))
    assert time.monotonic() - start < 2.0  # nunca bloqueou
    assert counters["chunks_dropped_late"] > 2000
    assert counters["chunks_sent"] + counters["chunks_dropped_late"] == 3000

    # Agora o servidor lê tudo: cada quadro que chegou está inteiro.
    conn, _ = server.accept()
    stream = conn.makefile("rb")
    assert stream.readline() == b"SENSE1 16000 30\n"
    sender.send(chunk(3000))  # empurra o resto de um quadro parcial, se houver
    sender.close()
    seqs = []
    while len(frame := stream.read(HEADER.size + CHUNK_BYTES)) == HEADER.size + CHUNK_BYTES:
        (seq,) = HEADER.unpack_from(frame)
        assert frame[HEADER.size :] == chunk(seq)
        seqs.append(seq)
    assert seqs == sorted(seqs) and len(seqs) >= counters["chunks_sent"] - 1


def test_receptor_fora_do_ar_descarta_e_reconecta_depois():
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    # Reserva uma porta e fecha: nada escuta nela.
    probe = socket.create_server(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    clock, counters = Clock(), Counter()
    sender = TcpSender("127.0.0.1", port, CHUNK_MS, counters, clock=clock)
    sender.send(chunk(0))
    sender.send(chunk(1))
    assert counters == {"chunks_dropped_offline": 2}

    receiver_counters = Counter()
    receiver = TcpReceiver(port, receiver_counters)
    threading.Thread(target=drain, args=(receiver, []), daemon=True).start()
    sender.send(chunk(2))  # ainda dentro do intervalo entre tentativas
    assert counters["chunks_dropped_offline"] == 3
    clock.now = RECONNECT_DELAY_S
    sender.send(chunk(3))
    wait_for(lambda: receiver_counters["chunks_received"] == 1)
    receiver.close()
