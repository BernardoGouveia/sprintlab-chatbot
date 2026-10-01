"""Servidor HTTP multi-thread — classe ThreadingServer (extraída de server.py) —
e SocketWatchdog, que impõe prazos TOTAIS a sockets (o timeout do socket só
limita cada operação: um byte de 50 em 50 s passaria sempre)."""
import json
import logging
import socket
import sys
import threading
import time
from http.server import HTTPServer
from socketserver import ThreadingMixIn

log = logging.getLogger("sprintlab")


class SocketWatchdog:
    """Fecha (shutdown) um socket quando o seu prazo passa. Uma única thread de
    fundo serve todos os sockets; arm()/disarm() são baratos."""

    def __init__(self, tick=0.25):
        self._tick = tick
        self._items = {}                 # id(sock) -> (deadline, sock)
        self._lock = threading.Lock()
        self._thread = None

    def arm(self, sock, seconds):
        with self._lock:
            self._items[id(sock)] = (time.monotonic() + seconds, sock)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="watchdog",
                                                daemon=True)
                self._thread.start()

    def disarm(self, sock):
        with self._lock:
            self._items.pop(id(sock), None)

    def _run(self):
        while True:
            time.sleep(self._tick)
            now = time.monotonic()
            with self._lock:
                expired = [(k, s) for k, (d, s) in self._items.items() if d <= now]
                for k, _ in expired:
                    del self._items[k]
            for _, sock in expired:
                try:
                    sock.shutdown(socket.SHUT_RDWR)   # unblocks any recv/send on it
                except OSError:
                    pass


class ThreadingServer(ThreadingMixIn, HTTPServer):
    """Uma thread por ligação, com um TETO de ligações em simultâneo:
    - acima de `max_connections` a ligação recebe um 503 (Retry-After), escrito
      por um pequeno grupo de threads à parte — o ciclo de accept nunca espera;
    - com `read_timeout`, o cliente tem esse prazo TOTAL para enviar o pedido
      (o handler chama request_read() quando o leu); depois a ligação é fechada;
    - a vaga é libertada ANTES de o cliente ver a ligação fechar.
    O limite por cliente e o timeout por operação de socket ficam no handler."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    _BUSY_BODY = json.dumps({"ok": False, "error": "Servidor ocupado — tenta daqui a pouco."}).encode()
    _MAX_REJECTERS = 16          # threads a responder 503 em simultâneo
    _REJECT_DRAIN_SECONDS = 2.0  # quanto tempo se lê o que o cliente ainda envia
    _REJECT_DRAIN_BYTES = 8 * 1024 * 1024

    def __init__(self, server_address, handler_class, max_connections=64,
                 read_timeout=None):
        self._slots = threading.BoundedSemaphore(max(1, max_connections))
        self._rejecters = threading.BoundedSemaphore(self._MAX_REJECTERS)
        self.read_timeout = read_timeout
        self.watchdog = SocketWatchdog()
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self._reject_busy_async(request)
            return
        if self.read_timeout:
            self.watchdog.arm(request, self.read_timeout)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.watchdog.disarm(request)
            self._slots.release()
            raise

    def request_read(self, request):
        """O handler leu o pedido completo: termina o prazo de leitura."""
        self.watchdog.disarm(request)

    def process_request_thread(self, request, client_address):
        # = ThreadingMixIn.process_request_thread, mas liberta a vaga antes do
        # shutdown: o cliente só vê EOF quando a vaga já está livre.
        try:
            try:
                self.finish_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
        finally:
            self.watchdog.disarm(request)
            self._slots.release()
            self.shutdown_request(request)

    def handle_error(self, request, client_address):
        """Cliente que desligou ou foi lento demais: nota em debug, não traceback."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.debug("client %s went away: %s", client_address[0], exc)
            return
        log.exception("unhandled error serving %s", client_address[0])

    # ---- 503 quando cheio ----------------------------------------------------

    def _reject_busy_async(self, request):
        if not self._rejecters.acquire(blocking=False):
            self.shutdown_request(request)   # sob ataque: fecha já, não espera
            return

        def run():
            try:
                self._reject_busy(request)
            finally:
                self._rejecters.release()

        try:
            threading.Thread(target=run, name="reject-503", daemon=True).start()
        except RuntimeError:
            self._rejecters.release()
            self.shutdown_request(request)

    def _reject_busy(self, request):
        """503 mínimo. Lê o pedido antes de responder e o que o cliente ainda
        envia depois (limitado): fechar um socket com dados por ler faz RST e o
        cliente veria "ligação reiniciada" em vez do 503."""
        try:
            request.settimeout(0.5)
            try:
                request.recv(65536)
            except OSError:
                pass
            request.sendall(
                b"HTTP/1.0 503 Service Unavailable\r\n"
                b"Content-Type: application/json\r\n"
                b"Retry-After: 5\r\n"
                b"Connection: close\r\n"
                b"Content-Length: " + str(len(self._BUSY_BODY)).encode() + b"\r\n\r\n"
                + self._BUSY_BODY)
            request.shutdown(socket.SHUT_WR)       # FIN: o cliente lê a resposta toda
            deadline = time.monotonic() + self._REJECT_DRAIN_SECONDS
            drained = 0
            while drained < self._REJECT_DRAIN_BYTES:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                request.settimeout(left)
                chunk = request.recv(65536)
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass
        finally:
            self.shutdown_request(request)
