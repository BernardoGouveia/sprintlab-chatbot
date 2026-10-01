"""Tests for the small infrastructure classes: RateLimiter (src/rate_limiter.py)
and ThreadingServer (src/threading_server.py), plus config parsing."""

import http.server
import threading
import urllib.request

import pytest

from src.rate_limiter import RateLimiter
from src.threading_server import ThreadingServer


class TestRateLimiter:
    def test_window_limit(self):
        rl = RateLimiter(2)
        assert rl.allow("a") and rl.allow("a") and not rl.allow("a")
        assert rl.allow("b")                      # other IPs unaffected

    def test_disabled(self):
        rl = RateLimiter(0)
        assert all(rl.allow("a") for _ in range(100))

    def test_tracked_keys_are_capped(self):
        rl = RateLimiter(5, max_keys=100)
        for i in range(1000):
            rl.allow(f"10.0.{i // 250}.{i % 250}")
        assert len(rl._hits) <= 100

    def test_sweep_is_periodic_not_per_request(self, monkeypatch):
        rl = RateLimiter(5, sweep_every=60)
        sweeps = []
        original = rl._sweep
        monkeypatch.setattr(rl, "_sweep", lambda now: (sweeps.append(now), original(now)))
        for i in range(200):
            rl.allow(f"ip{i}")
        assert len(sweeps) == 0                   # no O(n) scan on every call


class TestConcurrencyLimiter:
    def test_per_key_limit_and_release(self):
        from src.rate_limiter import ConcurrencyLimiter
        cl = ConcurrencyLimiter(2)
        assert cl.acquire("a") and cl.acquire("a") and not cl.acquire("a")
        assert cl.acquire("b")
        cl.release("a")
        assert cl.acquire("a")
        for _ in range(3):
            cl.release("a")
        assert "a" not in cl._active            # no leftovers once idle


class TestThreadingServer:
    def test_busy_server_answers_503_instead_of_blocking(self):
        import socket
        import time
        release = threading.Event()

        class Slow(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                release.wait(10)
                self.send_response(200)
                self.end_headers()

        srv = ThreadingServer(("127.0.0.1", 0), Slow, max_connections=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            first = socket.create_connection(("127.0.0.1", port), timeout=5)
            first.sendall(b"GET / HTTP/1.0\r\n\r\n")
            time.sleep(0.3)                           # first request holds the only slot
            started = time.monotonic()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as second:
                second.sendall(b"GET / HTTP/1.0\r\n\r\n")
                data = second.recv(4096)
            assert data.startswith(b"HTTP/1.0 503")
            assert time.monotonic() - started < 2      # immediate, not after the slow one
        finally:
            release.set()
            first.close()
            srv.shutdown()

    def test_connection_slots_are_released(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        srv = ThreadingServer(("127.0.0.1", 0), H, max_connections=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/"
            # more sequential requests than slots: each must free its slot
            for _ in range(5):
                with urllib.request.urlopen(url, timeout=5) as r:
                    assert r.read() == b"ok"
        finally:
            srv.shutdown()


class TestEnvInt:
    def test_invalid_value_exits_with_clear_message(self, monkeypatch):
        from src.config import _env_int
        monkeypatch.setenv("SOME_INT", "abc")
        with pytest.raises(SystemExit) as e:
            _env_int("SOME_INT", 5)
        assert "SOME_INT" in str(e.value)

    def test_default_and_minimum(self, monkeypatch):
        from src.config import _env_int
        monkeypatch.delenv("SOME_INT", raising=False)
        assert _env_int("SOME_INT", 5) == 5
        monkeypatch.setenv("SOME_INT", "-1")
        with pytest.raises(SystemExit):
            _env_int("SOME_INT", 5, minimum=0)


class TestReadDeadline:
    """A client that trickles its request one byte at a time must not keep a
    connection slot forever (the socket timeout only bounds each recv)."""

    def test_trickled_headers_are_cut_at_the_deadline(self):
        import socket
        import time

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()

        srv = ThreadingServer(("127.0.0.1", 0), H, max_connections=1, read_timeout=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            started = time.monotonic()
            closed = False
            try:
                for ch in b"GET / HTTP/1.0\r\nX-Slow: " + b"a" * 40:
                    s.sendall(bytes([ch]))
                    time.sleep(0.2)
            except OSError:
                closed = True
            if not closed:
                s.settimeout(3)
                try:
                    closed = s.recv(1024) == b""
                except OSError:
                    closed = True
            assert closed and time.monotonic() - started < 5
            s.close()
            time.sleep(0.3)
            # the only slot is free again: a normal request is served
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                assert r.status == 200
        finally:
            srv.shutdown()


class TestBusyRejection:
    def _slow_server(self, release):
        class Slow(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                release.wait(10)
                self.send_response(200)
                self.end_headers()

            do_POST = do_GET

        srv = ThreadingServer(("127.0.0.1", 0), Slow, max_connections=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    @pytest.mark.parametrize("n_idle", [10, 40])
    def test_idle_connections_do_not_block_the_accept_loop(self, n_idle):
        """Connections that never send anything cannot stall the accept loop:
        with a few of them the next client still gets its 503 at once; with
        more than the 503 writers can serve, extra connections are closed at
        once (never left hanging)."""
        import socket
        import time
        release = threading.Event()
        srv = self._slow_server(release)
        port = srv.server_address[1]
        idle = []
        try:
            holder = socket.create_connection(("127.0.0.1", port), timeout=5)
            holder.sendall(b"GET / HTTP/1.0\r\n\r\n")
            time.sleep(0.3)
            idle = [socket.create_connection(("127.0.0.1", port), timeout=5)
                    for _ in range(n_idle)]
            started = time.monotonic()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(b"GET / HTTP/1.0\r\n\r\n")
                try:
                    data = s.recv(4096)
                except ConnectionError:
                    data = b""
            assert time.monotonic() - started < 1.5     # not queued behind the idle ones
            if n_idle < ThreadingServer._MAX_REJECTERS:
                assert data.startswith(b"HTTP/1.0 503")
            else:
                assert data in (b"",) or data.startswith(b"HTTP/1.0 503")
        finally:
            release.set()
            for c in idle:
                c.close()
            holder.close()
            srv.shutdown()

    def test_large_post_rejected_while_busy_gets_503_not_reset(self):
        import socket
        import time
        release = threading.Event()
        srv = self._slow_server(release)
        port = srv.server_address[1]
        try:
            holder = socket.create_connection(("127.0.0.1", port), timeout=5)
            holder.sendall(b"GET / HTTP/1.0\r\n\r\n")
            time.sleep(0.3)
            body = b"x" * (1024 * 1024)
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(b"POST / HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
                data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            assert data.startswith(b"HTTP/1.0 503")
        finally:
            release.set()
            holder.close()
            srv.shutdown()

    def test_sequential_requests_never_see_spurious_503(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        srv = ThreadingServer(("127.0.0.1", 0), H, max_connections=1)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/"
            for _ in range(150):
                with urllib.request.urlopen(url, timeout=5) as r:
                    assert r.status == 200
        finally:
            srv.shutdown()


class TestClientDisconnectLogging:
    def test_connection_errors_are_not_logged_as_errors(self, caplog):
        import logging
        srv = ThreadingServer.__new__(ThreadingServer)
        with caplog.at_level(logging.DEBUG, logger="sprintlab"):
            try:
                raise ConnectionResetError("peer reset")
            except ConnectionResetError:
                ThreadingServer.handle_error(srv, None, ("1.2.3.4", 1))
            try:
                raise ValueError("real bug")
            except ValueError:
                ThreadingServer.handle_error(srv, None, ("1.2.3.4", 1))
        levels = [r.levelname for r in caplog.records]
        assert levels.count("ERROR") == 1          # only the real bug


class TestEnvLimits:
    def test_port_above_65535_exits_cleanly(self, monkeypatch):
        from src.config import _env_int
        monkeypatch.setenv("SOME_PORT", "70000")
        with pytest.raises(SystemExit) as e:
            _env_int("SOME_PORT", 7860, minimum=1, maximum=65535)
        assert "<= 65535" in str(e.value)

    @pytest.mark.parametrize("base", ["gitlab.com/api/v4", "ftp://gitlab.com", "https://",
                                      "https://gitlab.com:abc/api/v4",
                                      "https://gitlab.com:99999/api/v4",
                                      "https://gitlab.com:0/api/v4", "https://[::1/api/v4"])
    def test_invalid_gitlab_base_fails_at_startup(self, base):
        import os
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "GITLAB_TOKEN": "x", "GROQ_API_KEY": "y", "GITLAB_BASE": base}
        r = subprocess.run([sys.executable, "-c", "import src.config"], cwd=root,
                           env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode != 0 and "GITLAB_BASE" in r.stderr

    @pytest.mark.parametrize("base, expected", [
        ("https://gitlab.mycompany.com", "https://gitlab.mycompany.com/api/v4"),
        ("https://example.com/gitlab/", "https://example.com/gitlab/api/v4"),
        ("https://gitlab.com/api/v4/", "https://gitlab.com/api/v4"),
    ])
    def test_instance_url_is_normalised_to_the_api_url(self, base, expected):
        import os
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "GITLAB_TOKEN": "x", "GROQ_API_KEY": "y", "GITLAB_BASE": base}
        r = subprocess.run([sys.executable, "-B", "-c",
                            "import src.config as c; print(c.GITLAB_BASE)"], cwd=root,
                           env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.strip() == expected
