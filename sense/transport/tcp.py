"""Transporte de áudio do modo thin: TV Box (TcpSender) → PC (TcpReceiver).

Protocolo, numa conexão TCP só:

    TV Box → PC   "SENSE1 <taxa> <chunk_ms>\\n"         uma vez, ao conectar
    TV Box → PC   [seq: uint32 big-endian][PCM s16le]   um quadro por chunk
    PC → TV Box   0x01                                  "toque o beep"

Áudio atrasado é descartado, nunca enfileirado: o socket de envio é não
bloqueante e tem buffer pequeno, então quando a rede (ou o PC) não dá vazão o
chunk novo é jogado fora. `seq` conta todo chunk capturado, enviado ou não, e é
por ele que o receptor enxerga as lacunas.

Este módulo é a única parte que sabe que o transporte é TCP. Para trocar, basta
outro par com `send(chunk)` de um lado e `chunks()` / `send_beep()` do outro.
"""

import logging
import socket
import struct
import time
from collections import Counter
from collections.abc import Callable, Iterator

from sense.config import SAMPLE_RATE

log = logging.getLogger(__name__)

MAGIC = "SENSE1"
HEADER = struct.Struct("!I")
BEEP = b"\x01"

# Buffers de socket pequenos (envio na TV Box, recepção no PC): limitam quanto
# áudio pode ficar parado no caminho antes de o descarte começar.
SOCKET_BUFFER_BYTES = 8192
RECONNECT_DELAY_S = 3.0
CONNECT_TIMEOUT_S = 1.0
# Sem receber nada por este tempo, o receptor considera a TV Box desconectada.
IDLE_TIMEOUT_S = 5.0
# Atraso de chegada a partir do qual o receptor conta o chunk como atrasado.
LATE_THRESHOLD_S = 0.2


class TcpSender:
    def __init__(
        self,
        host: str,
        port: int,
        chunk_ms: int,
        counters: Counter,
        on_beep: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._address = (host, port)
        self._handshake = f"{MAGIC} {SAMPLE_RATE} {chunk_ms}\n".encode()
        self._counters = counters
        self._on_beep = on_beep
        self._clock = clock
        self._sock: socket.socket | None = None
        self._seq = 0
        self._pending = b""  # resto de um quadro que o socket aceitou só em parte
        self._next_connect = 0.0
        self._connected_before = False

    def send(self, chunk: bytes) -> None:
        """Envia o chunk ou o descarta. Nunca espera a rede."""
        frame = HEADER.pack(self._seq) + chunk
        self._seq = (self._seq + 1) & 0xFFFFFFFF

        if self._sock is None and not self._connect():
            self._counters["chunks_dropped_offline"] += 1
            return
        try:
            self._poll_beep()
            # Um quadro começado tem de terminar, senão o receptor perde o
            # alinhamento. Enquanto ele não sai, os chunks novos são descartados.
            if self._pending:
                self._pending = self._pending[self._sock.send(self._pending) :]
            if self._pending:
                self._counters["chunks_dropped_late"] += 1
                return
            self._pending = frame[self._sock.send(frame) :]
            self._counters["chunks_sent"] += 1
        except BlockingIOError:
            # Buffer de envio cheio: a rede não está dando vazão.
            self._counters["chunks_dropped_late"] += 1
        except OSError as error:
            log.warning("Conexão com o receptor perdida: %s", error)
            self.close()

    def _connect(self) -> bool:
        now = self._clock()
        if now < self._next_connect:
            return False
        self._next_connect = now + RECONNECT_DELAY_S
        try:
            sock = socket.create_connection(self._address, timeout=CONNECT_TIMEOUT_S)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_BYTES)
            sock.sendall(self._handshake)
            sock.setblocking(False)
        except OSError as error:
            log.warning(
                "Receptor %s:%d inacessível (%s). Nova tentativa em %.0fs.",
                *self._address, error, RECONNECT_DELAY_S,
            )
            return False
        self._sock = sock
        self._pending = b""
        if self._connected_before:
            self._counters["tcp_reconnects"] += 1
        self._connected_before = True
        log.info("Conectado ao receptor %s:%d.", *self._address)
        return True

    def _poll_beep(self) -> None:
        try:
            data = self._sock.recv(64)
        except BlockingIOError:
            return
        if not data:
            raise ConnectionResetError("o receptor fechou a conexão")
        if BEEP in data:
            self._on_beep()

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class TcpReceiver:
    """Aceita uma TV Box por vez e entrega os chunks dela, em ordem."""

    def __init__(
        self,
        port: int,
        counters: Counter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counters = counters
        self._clock = clock
        self._conn: socket.socket | None = None
        self._server = socket.create_server(("0.0.0.0", port))
        # Herdado pelas conexões aceitas.
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_BYTES)
        self.port = self._server.getsockname()[1]

    def chunks(self) -> Iterator[bytes]:
        """Gera chunks para sempre, atravessando desconexões e reconexões."""
        while True:
            conn, (host, port) = self._server.accept()
            log.info("TV Box conectada: %s:%d", host, port)
            self._counters["tcp_connections"] += 1
            self._conn = conn
            try:
                yield from self._serve(conn)
                log.warning("TV Box desconectou.")
            except (OSError, ValueError) as error:
                log.warning("Conexão com a TV Box encerrada: %s", error)
            finally:
                self._conn = None
                conn.close()

    def _serve(self, conn: socket.socket) -> Iterator[bytes]:
        conn.settimeout(IDLE_TIMEOUT_S)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # O `with` importa: enquanto o arquivo existir, `conn.close()` não fecha o socket.
        with conn.makefile("rb") as stream:
            magic, rate, chunk_ms = stream.readline(64).decode(errors="replace").split()
            if magic != MAGIC or int(rate) != SAMPLE_RATE:
                raise ValueError(f"handshake incompatível: {magic} {rate} {chunk_ms}")
            chunk_s = int(chunk_ms) / 1000
            frame_bytes = HEADER.size + SAMPLE_RATE * int(chunk_ms) // 1000 * 2

            expected = None
            earliest = float("inf")  # menor (chegada − instante nominal) já visto
            while len(frame := stream.read(frame_bytes)) == frame_bytes:
                (seq,) = HEADER.unpack_from(frame)
                if expected is not None and seq != expected:
                    self._counters["gaps"] += 1
                    self._counters["chunks_missing"] += (seq - expected) & 0xFFFFFFFF
                expected = (seq + 1) & 0xFFFFFFFF

                # Atraso relativo ao chunk mais pontual da conexão: não precisa de
                # relógios sincronizados entre a TV Box e o PC.
                offset = self._clock() - seq * chunk_s
                earliest = min(earliest, offset)
                if offset - earliest > LATE_THRESHOLD_S:
                    self._counters["chunks_late"] += 1

                self._counters["chunks_received"] += 1
                yield frame[HEADER.size :]

    def send_beep(self) -> None:
        """Pede à TV Box que toque o beep. Sem conexão, não faz nada."""
        if self._conn is not None:
            try:
                self._conn.send(BEEP)
            except OSError:
                pass

    def close(self) -> None:
        self._server.close()
