"""Unit tests for the GitLab client layer (src/gitlab_api.py) and its cache
(src/cache.py): per-request config security rules, cache isolation and
invalidation, redirects, pagination truncation, author resolution and the
parallel fan-out. No network: GitLab calls are faked; the redirect test uses a
local HTTP server on 127.0.0.1."""

import http.client
import http.server
import io
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

import pytest

import src.gitlab_api as gl
from src.cache import TTLCache


@pytest.fixture(autouse=True)
def _reset():
    gl.cache.invalidate()
    gl._base_cache.invalidate()
    gl._ctx.gl = None
    yield
    gl._ctx.gl = None


def _http_error(code, message=""):
    return urllib.error.HTTPError("https://gitlab", code, "err", {},
                                  io.BytesIO(json.dumps({"message": message}).encode()))


# ── resolve_gl_config: who gets the server token ──────────────────────────────

class TestResolveConfig:
    def test_no_headers_uses_server_config(self):
        cfg, err = gl.resolve_gl_config("", "", "")
        assert err is None and cfg["token"] == gl.GITLAB_TOKEN and cfg["server_token"]

    def test_custom_project_without_token_gets_no_token(self):
        cfg, err = gl.resolve_gl_config("", "", "999")
        assert err is None and cfg["token"] == "" and not cfg["server_token"]

    def test_custom_base_without_token_gets_no_token(self, monkeypatch):
        monkeypatch.setattr(gl, "_host_error", lambda h, p: None)
        cfg, err = gl.resolve_gl_config("https://git.example.org", "", "")
        assert err is None and cfg["token"] == "" and cfg["base"] == "https://git.example.org/api/v4"

    def test_own_token_is_used(self):
        cfg, _ = gl.resolve_gl_config("", "glpat-abc", "999")
        assert cfg["token"] == "glpat-abc" and not cfg["server_token"]

    def test_rejects_tokens_with_control_characters(self):
        assert gl.resolve_gl_config("", "abc\r\nX-Evil: 1", "")[1]

    @pytest.mark.parametrize("project", ["80767095#", "1?a=b", "..", "a/..", "g/p/",
                                         "1 2", "grupo/../x"])
    def test_rejects_bad_projects(self, project):
        assert gl.resolve_gl_config("", "", project)[1]

    def test_empty_project_means_default(self):
        cfg, err = gl.resolve_gl_config("", "", "  ")
        assert err is None and cfg["project"] == gl.GITLAB_PROJECT_ID

    @pytest.mark.parametrize("project", ["123", "grupo/projeto", "a.b/c-d/e_f"])
    def test_accepts_ids_and_namespace_paths(self, project):
        assert gl.resolve_gl_config("", "t", project)[1] is None

    def test_url_encoded_namespace_path_still_accepted(self):
        # a forma que a documentação da API do GitLab mostra (e que já funcionava)
        cfg, err = gl.resolve_gl_config("", "t", "grupo%2Fprojeto")
        assert err is None and cfg["project"] == "grupo/projeto"
        gl._ctx.gl = cfg
        assert gl._proj() == "grupo%2Fprojeto"

    def test_encoded_injection_still_rejected(self):
        assert gl.resolve_gl_config("", "t", "80767095%23")[1]

    def test_invalid_default_project_fails_at_startup(self):
        import os
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, "GITLAB_TOKEN": "x", "GROQ_API_KEY": "y",
               "GITLAB_PROJECT_ID": "80767095#"}
        r = subprocess.run([sys.executable, "-c", "import src.gitlab_api"], cwd=root,
                           env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode != 0 and "GITLAB_PROJECT_ID" in r.stderr


class TestBaseValidation:
    @pytest.mark.parametrize("base", [
        "http://gitlab.com", "ftp://gitlab.com", "https://", "gitlab.com",
        "https://user:pw@gitlab.com", "https://gitlab.com/?x=1",
        "https://gitlab.com/#frag", "https://gitlab.com/a/../b",
        "https://127.0.0.1", "https://[::1]", "https://10.1.2.3",
        "https://169.254.169.254", "https://gitlab.com:99999",
    ])
    def test_rejected(self, base):
        assert gl.normalise_base(base)[1]

    def test_hostname_resolving_to_private_address_rejected(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("192.168.1.10", 443))])
        assert "interna" in gl.normalise_base("https://intranet.example")[1]

    def test_public_host_accepted_and_api_suffix_added(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("35.231.145.151", 443))])
        assert gl.normalise_base("https://gitlab.example.com/") == \
            ("https://gitlab.example.com/api/v4", None)
        assert gl.normalise_base("https://gitlab.example.com/gitlab/api/v4")[0] == \
            "https://gitlab.example.com/gitlab/api/v4"

    def test_dns_failure_is_not_cached(self, monkeypatch):
        def down(*a, **k):
            raise OSError("temporary DNS failure")
        monkeypatch.setattr(gl.socket, "getaddrinfo", down)
        assert gl.normalise_base("https://git.example.org")[1]
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("35.231.145.151", 443))])
        assert gl.normalise_base("https://git.example.org") == \
            ("https://git.example.org/api/v4", None)


class TestDnsRebinding:
    """A custom host validated as public must not be reachable if its DNS later
    answers with an internal address: the connection re-checks and connects to
    exactly the address it validated."""

    def test_connection_refuses_internal_address_at_connect_time(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
        with pytest.raises(urllib.error.URLError):
            gl._PublicOnlyHTTPSConnection._connect_public(("rebind.example", 443), 1)

    def test_connects_to_the_validated_ip(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("35.231.145.151", 443))])
        seen = []
        monkeypatch.setattr(gl.socket, "create_connection",
                            lambda addr, *a, **k: seen.append(addr) or "sock")
        assert gl._PublicOnlyHTTPSConnection._connect_public(("git.example.org", 443), 1) == "sock"
        assert seen == [("35.231.145.151", 443)]

    def test_custom_instances_use_the_checked_opener(self):
        assert gl._opener_for({"base": gl.GITLAB_BASE}) is gl._opener
        assert gl._opener_for({"base": "https://git.example.org/api/v4"}) is gl._public_opener


# ── URL building ──────────────────────────────────────────────────────────────

class TestProjectEncoding:
    def test_namespace_is_encoded(self):
        gl._ctx.gl = {"base": "b", "token": "t", "project": "grp/sub/proj", "server_token": False}
        assert gl._proj() == "grp%2Fsub%2Fproj"

    def test_request_rejects_endpoints_that_could_retarget(self):
        for ep in ("/projects/1#", "/projects/1?x", "projects/1", "/p\r\n"):
            with pytest.raises(ValueError):
                gl._gitlab_request("GET", ep)


# ── cache keys / invalidation ─────────────────────────────────────────────────

class TestCacheKeys:
    def _ctx(self, token):
        gl._ctx.gl = {"base": "https://g/api/v4", "token": token, "project": "1",
                      "server_token": False}

    def test_different_tokens_do_not_share_entries(self):
        self._ctx("tok-A")
        gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["privado"])
        self._ctx("tok-B")
        assert gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["outro"]) == ["outro"]

    def test_write_invalidates_every_tokens_view_of_the_project(self, monkeypatch):
        monkeypatch.setattr(gl, "_gitlab_request", lambda *a, **k: {})
        for tok in ("tok-A", "tok-B"):
            self._ctx(tok)
            gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["velho"])
        gl.gitlab_request("PUT", "/projects/1/issues/5", body={"state_event": "close"})
        self._ctx("tok-A")
        assert gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["novo"]) == ["novo"]

    def test_read_during_write_is_not_cached(self, monkeypatch):
        # leitura que começa DURANTE a escrita não pode re-guardar dados velhos
        self._ctx("tok")
        writing = threading.Event()
        release = threading.Event()

        def slow_write(*a, **k):
            writing.set()
            release.wait(5)
            return {}
        monkeypatch.setattr(gl, "_gitlab_request", slow_write)
        t = threading.Thread(target=lambda: (setattr(gl._ctx, "gl", {
            "base": "https://g/api/v4", "token": "tok", "project": "1",
            "server_token": False}), gl.gitlab_request("PUT", "/projects/1/issues/5",
                                                       body={"a": 1})))
        t.start()
        assert writing.wait(5)
        assert gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["antes"]) == ["antes"]
        release.set()
        t.join(5)
        assert gl.cache.get_or_set(gl._ck("issues:all"), lambda: ["depois"]) == ["depois"]


class TestTTLCache:
    def test_single_flight_for_concurrent_misses(self):
        c = TTLCache(60)
        calls, gate = [], threading.Event()

        def producer():
            calls.append(1)
            gate.wait(5)
            return 42

        results = []
        threads = [threading.Thread(target=lambda: results.append(c.get_or_set("k", producer)))
                   for _ in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        gate.set()
        for t in threads:
            t.join(5)
        assert results == [42] * 8 and len(calls) == 1

    def test_errors_are_not_cached(self):
        c = TTLCache(60)
        with pytest.raises(RuntimeError):
            c.get_or_set("k", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        assert c.get_or_set("k", lambda: 1) == 1

    def test_unrelated_invalidation_does_not_block_caching(self):
        c = TTLCache(60)
        calls = []

        def producer():
            calls.append(1)
            c.invalidate("outro-projeto:")   # concurrent write elsewhere
            return "v"
        c.get_or_set("proj:issues", producer)
        c.get_or_set("proj:issues", producer)
        assert len(calls) == 1

    def test_matching_invalidation_during_fetch_is_not_cached(self):
        c = TTLCache(60)
        calls = []

        def producer():
            calls.append(1)
            c.invalidate("proj:")
            return "v"
        c.get_or_set("proj:issues", producer)
        c.get_or_set("proj:issues", lambda: calls.append(2) or "v2")
        assert calls == [1, 2]

    def test_bounded_size_evicts_least_recently_used(self):
        c = TTLCache(60, max_entries=3)
        for k in "abcd":
            c.get_or_set(k, lambda k=k: k)
        assert len(c) == 3
        assert c.get_or_set("a", lambda: "novo") == "novo"   # 'a' was evicted

    def test_expired_entries_are_purged(self):
        c = TTLCache(0, max_entries=100)
        for i in range(10):
            c.get_or_set(str(i), lambda: 1)
        assert len(c) <= 1


# ── HTTP layer ────────────────────────────────────────────────────────────────

class TestRedirects:
    def test_redirect_is_refused_not_followed(self, monkeypatch):
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                hits.append(("POST", self.path, self.headers.get("PRIVATE-TOKEN")))
                # Read the body first: closing a socket with unread data makes
                # Windows send RST, and the client would see a reset, not the 302.
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()

            def do_GET(self):
                hits.append(("GET", self.path, self.headers.get("PRIVATE-TOKEN")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"[]")

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            gl._ctx.gl = {"base": f"http://127.0.0.1:{srv.server_address[1]}/api/v4",
                          "token": "segredo", "project": "1", "server_token": False}
            with pytest.raises(urllib.error.HTTPError) as e:
                gl._gitlab_request("POST", "/projects/1/repository/branches", body={"a": 1})
            assert e.value.code == 302
            assert "redireciona" in gl.gitlab_error_message(e.value)
            assert hits == [("POST", "/api/v4/projects/1/repository/branches", "segredo")]
        finally:
            srv.shutdown()


class TestPagination:
    def _pages(self, monkeypatch, n_pages, per_page=100):
        def fake(method, endpoint, body=None, params=None, return_headers=False):
            page = params["page"]
            batch = [{"i": (page - 1) * per_page + j} for j in range(per_page)]
            headers = {"X-Next-Page": str(page + 1) if page < n_pages else "",
                       "X-Total": str(n_pages * per_page)}
            return batch, headers
        monkeypatch.setattr(gl, "_gitlab_request", fake)

    def test_truncation_is_flagged(self, monkeypatch):
        self._pages(monkeypatch, n_pages=8)
        rows = gl._gitlab_paginate("/x", page_limit=5)
        assert len(rows) == 500 and rows.truncated is True and rows.total == 800

    def test_complete_list_not_flagged(self, monkeypatch):
        self._pages(monkeypatch, n_pages=3)
        rows = gl._gitlab_paginate("/x", page_limit=5)
        assert len(rows) == 300 and rows.truncated is False


class TestMilestones:
    def test_includes_group_milestones(self, monkeypatch):
        seen = []

        def fake(method, endpoint, body=None, params=None, return_headers=False):
            seen.append(params)
            return [{"id": 1, "title": "Sprint 3"}], {}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        gl._ctx.gl = {"base": "b", "token": "t", "project": "1", "server_token": False}
        assert gl.get_milestones()[0]["title"] == "Sprint 3"
        assert seen[0]["include_ancestors"] == "true" and seen[0]["state"] == "active"

    def test_older_gitlab_falls_back_to_parent_milestones_param(self, monkeypatch):
        seen = []

        def fake(method, endpoint, body=None, params=None, return_headers=False):
            seen.append(dict(params))
            if "include_ancestors" in params:
                raise _http_error(400, "include_ancestors is invalid")
            return [], {}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        gl._ctx.gl = {"base": "b", "token": "t", "project": "1", "server_token": False}
        gl.get_all_milestones()
        assert seen[1].get("include_parent_milestones") == "true"


# ── contributors / author resolution ──────────────────────────────────────────

CONTRIBS = [
    {"name": "Mariana Costa", "email": "mariana@x.pt", "commits": 400},
    {"name": "Ana Silva", "email": "ana@work.pt", "commits": 50},
    {"name": "Ana Silva", "email": "ana@gmail.com", "commits": 30},
    {"name": "Bruno", "email": "bruno@x.pt", "commits": 5},
]


class TestContributors:
    def test_merge_same_person(self):
        merged = gl.merge_contributors(CONTRIBS)
        ana = next(m for m in merged if m["name"] == "Ana Silva")
        assert ana["commits"] == 80 and len(merged) == 3

    def test_capped_flag(self):
        assert gl.contributors_capped([{"commits": 2000}]) is True
        assert gl.contributors_capped([{"commits": 1999}]) is False

    def test_resolve_does_not_match_substring_of_another_name(self):
        rows, cands = gl._resolve_author("Ana", CONTRIBS)
        assert {r["email"] for r in rows} == {"ana@work.pt", "ana@gmail.com"} and not cands

    def test_ambiguous_query_returns_candidates(self):
        rows, cands = gl._resolve_author("Silva", CONTRIBS + [
            {"name": "Rui Silva", "email": "rui@x.pt", "commits": 9}])
        assert rows == [] and set(cands) == {"Ana Silva", "Rui Silva"}

    def test_commits_by_author_total_and_list_use_the_same_identity(self, monkeypatch):
        monkeypatch.setattr(gl, "_get_contributors", lambda: CONTRIBS)
        seen = {}

        def fake(method, endpoint, body=None, params=None, return_headers=False):
            seen.update(params)
            return [
                {"id": "1", "author_name": "Ana Silva", "author_email": "ana@work.pt"},
                {"id": "2", "author_name": "Ana Silva Santos", "author_email": "as@x.pt"},
                {"id": "3", "author_name": "Ana Silva", "author_email": "ana@gmail.com"},
            ], {}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        gl._ctx.gl = {"base": "b", "token": "t", "project": "1", "server_token": False}
        r = gl._commits_by_author("ana")
        assert r["total"] == 80 and r["autores"] == ["Ana Silva"]
        assert [c["id"] for c in r["commits"]] == ["1", "3"]
        assert seen["author"] == "Ana Silva"      # filtro do lado do GitLab

    def test_fetch_failure_is_reported_not_zero(self, monkeypatch):
        monkeypatch.setattr(gl, "_get_contributors", lambda: CONTRIBS)

        def boom(*a, **k):
            raise TimeoutError("slow")
        monkeypatch.setattr(gl, "_gitlab_request", boom)
        gl._ctx.gl = {"base": "b", "token": "t", "project": "1", "server_token": False}
        r = gl._commits_by_author("Bruno")
        assert "erro" in r and r["commits"] == []

    def test_regex_characters_in_names_are_escaped(self):
        assert gl._author_pattern("J. Doe [bot]*") == r"J\. Doe \[bot\]\*"


# ── run_parallel ──────────────────────────────────────────────────────────────

class TestRunParallel:
    def test_results_errors_and_deadline(self):
        gl._ctx.gl = {"base": "b", "token": "t", "project": "9", "server_token": False}

        def boom():
            raise ValueError("x")

        started = time.monotonic()
        r = gl.run_parallel({
            "ctx": (lambda: gl._gl()["project"],),
            "err": (boom,),
            "slow": (time.sleep, 3),
        }, timeout=0.5)
        assert r["ctx"] == "9"                     # config propagated to workers
        assert isinstance(r["err"], ValueError)
        assert isinstance(r["slow"], TimeoutError)
        assert time.monotonic() - started < 2.5    # didn't wait for the slow task


class TestErrorMessages:
    def test_gitlab_reason_is_included(self):
        msg = gl.gitlab_error_message(_http_error(400, "Title is too long (maximum is 255 characters)"))
        assert "HTTP 400" in msg and "too long" in msg

    def test_known_codes(self):
        assert "Token" in gl.gitlab_error_message(_http_error(401))
        assert "não respondeu" in gl.gitlab_error_message(TimeoutError())
        assert "ligar" in gl.gitlab_error_message(urllib.error.URLError("x"))


# ── Final-review fixes: SSRF, project ids, deadlines, byte budgets, MR counts ──

class TestEmbeddedIPv4:
    @pytest.mark.parametrize("addr", [
        "::ffff:127.0.0.1",          # IPv4-mapped
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",           # NAT64 → 127.0.0.1
        "64:ff9b::a9fe:a9fe",        # NAT64 → 169.254.169.254 (cloud metadata)
        "2002:7f00:1::",             # 6to4 → 127.0.0.1
        "2002:c0a8:101::",           # 6to4 → 192.168.1.1
        "224.0.0.1", "ff02::1",      # multicast
        "0.0.0.0", "::",
    ])
    def test_internal_targets_hidden_in_ipv6_are_refused(self, addr):
        import ipaddress
        assert not gl._ip_allowed(ipaddress.ip_address(addr))

    @pytest.mark.parametrize("addr", ["35.231.145.151", "2606:4700::6810:84e5"])
    def test_public_addresses_allowed(self, addr):
        import ipaddress
        assert gl._ip_allowed(ipaddress.ip_address(addr))

    def test_hostname_resolving_to_mapped_loopback_rejected(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(23, 1, 6, "", ("::ffff:127.0.0.1", 443, 0, 0))])
        assert "interna" in gl.normalise_base("https://sneaky.example")[1]


class TestProjectAndBaseEdgeCases:
    def test_valid_project_uses_full_match(self):
        # re.match + "$" would accept a trailing newline
        assert not gl.valid_project("80767095\n")
        assert not gl.valid_project("grupo/projeto\n")

    def test_port_zero_is_rejected(self, monkeypatch):
        monkeypatch.setattr(gl.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("35.231.145.151", 443))])
        assert gl.normalise_base("https://gitlab.example.com")[1] is None
        assert gl.normalise_base("https://gitlab.example.com:0")[1]


class TestErrorMessageWithoutToken:
    def test_401_without_token_asks_for_a_token(self):
        gl._ctx.gl = {"base": "https://git.example.org/api/v4", "token": "",
                      "project": "1", "server_token": False}
        assert "precisa de um token" in gl.gitlab_error_message(_http_error(401))

    def test_401_with_token_says_it_is_invalid(self):
        gl._ctx.gl = {"base": "https://git.example.org/api/v4", "token": "glpat-x",
                      "project": "1", "server_token": False}
        assert "inválido" in gl.gitlab_error_message(_http_error(401))


def _local_server(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _custom_instance(srv):
    gl._ctx.gl = {"base": f"http://127.0.0.1:{srv.server_address[1]}/api/v4",
                  "token": "", "project": "1", "server_token": False}


class TestRequestDeadlines:
    """The socket timeout only bounds each read: an instance trickling one byte
    at a time must still be cut at the TOTAL deadline, and a huge answer must be
    refused, not buffered."""

    def test_trickling_body_is_cut_at_the_total_deadline(self, monkeypatch):
        stop = threading.Event()

        class Trickle(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

        srv = _local_server(Trickle)
        monkeypatch.setattr(gl, "CUSTOM_TOTAL_TIMEOUT", 1)
        try:
            _custom_instance(srv)
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                gl._gitlab_request("GET", "/projects/1")
            assert time.monotonic() - started < 4
        finally:
            stop.set()
            srv.shutdown()

    def test_slow_headers_are_cut_at_the_total_deadline(self, monkeypatch):
        stop = threading.Event()

        class SlowHeaders(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    while not stop.is_set():
                        self.wfile.write(b"X-Pad: a\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

        srv = _local_server(SlowHeaders)
        monkeypatch.setattr(gl, "CUSTOM_TOTAL_TIMEOUT", 1)
        try:
            _custom_instance(srv)
            started = time.monotonic()
            with pytest.raises((TimeoutError, OSError)):
                gl._gitlab_request("GET", "/projects/1")
            assert time.monotonic() - started < 4
        finally:
            stop.set()
            srv.shutdown()

    def test_oversized_response_is_refused(self, monkeypatch):
        class Big(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = b"[" + b"1," * 60000 + b"1]"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        srv = _local_server(Big)
        monkeypatch.setattr(gl, "CUSTOM_MAX_RESPONSE_BYTES", 50_000)
        try:
            _custom_instance(srv)
            with pytest.raises(ValueError, match="demasiado grande"):
                gl._gitlab_request("GET", "/projects/1")
        finally:
            srv.shutdown()

    def test_sockets_are_disarmed_after_a_normal_request(self):
        class Ok(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"[]")

        srv = _local_server(Ok)
        try:
            _custom_instance(srv)
            assert gl._gitlab_request("GET", "/projects/1") == []
            assert not gl._gitlab_watchdog._items          # nothing left armed
        finally:
            srv.shutdown()


CUSTOM = {"base": "https://git.example.org/api/v4", "token": "t", "project": "9",
          "server_token": False}


class TestWorkerCap:
    def test_sections_beyond_the_pool_are_reported_busy(self, monkeypatch):
        monkeypatch.setattr(gl, "_custom_workers", threading.BoundedSemaphore(2))
        monkeypatch.setattr(gl, "SLOT_WAIT", 0.2)
        gl._ctx.gl = dict(CUSTOM)
        r = gl.run_parallel({"a": (time.sleep, 0.6), "b": (time.sleep, 0.6),
                             "c": (lambda: 3,)}, timeout=2)
        assert r["a"] is None and r["b"] is None
        assert isinstance(r["c"], gl.BusyError)
        assert gl.gitlab_error_message(r["c"]) == "Servidor ocupado — tenta daqui a pouco."

    def test_bursts_wait_for_a_slot_instead_of_failing(self, monkeypatch):
        monkeypatch.setattr(gl, "_custom_workers", threading.BoundedSemaphore(1))
        monkeypatch.setattr(gl, "SLOT_WAIT", 3)
        gl._ctx.gl = dict(CUSTOM)
        r = gl.run_parallel({"a": (time.sleep, 0.3), "b": (lambda: 2,)}, timeout=3)
        assert r == {"a": None, "b": 2}

    def test_caller_instances_cannot_starve_the_servers_own(self, monkeypatch):
        custom = threading.BoundedSemaphore(1)
        monkeypatch.setattr(gl, "_custom_workers", custom)
        monkeypatch.setattr(gl, "SLOT_WAIT", 0.1)
        assert custom.acquire(blocking=False)          # custom share exhausted
        try:
            gl._ctx.gl = dict(CUSTOM)
            assert isinstance(gl.run_parallel({"x": (lambda: 1,)})["x"], gl.BusyError)
            gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9",
                          "server_token": True}
            assert gl.run_parallel({"x": (lambda: 1,)}) == {"x": 1}
        finally:
            custom.release()

    def test_one_client_cannot_take_the_whole_pool(self, monkeypatch):
        slots = gl._ClientSlots(2)
        monkeypatch.setattr(gl, "_client_workers", slots)
        monkeypatch.setattr(gl, "SLOT_WAIT", 0.1)
        key = ("1.1.1.1", False)                       # (client, own-instance pool?)
        assert slots.acquire(key, 0) and slots.acquire(key, 0)
        try:
            gl._ctx.gl = dict(CUSTOM)
            gl._ctx.client = "1.1.1.1"
            assert isinstance(gl.run_parallel({"x": (lambda: 1,)})["x"], gl.BusyError)
            gl._ctx.client = "2.2.2.2"                 # another client is unaffected
            assert gl.run_parallel({"x": (lambda: 1,)}) == {"x": 1}
        finally:
            slots.release(key)
            slots.release(key)
            gl._ctx.client = None
        assert not slots._held

    def test_slow_caller_instance_does_not_starve_the_same_clients_own_reads(self, monkeypatch):
        slots = gl._ClientSlots(2)
        monkeypatch.setattr(gl, "_client_workers", slots)
        monkeypatch.setattr(gl, "SLOT_WAIT", 0.3)
        hung = threading.Event()
        gl._ctx.client = "1.1.1.1"
        try:
            gl._ctx.gl = dict(CUSTOM)                  # a hung caller instance…
            r = gl.run_parallel({"a": (hung.wait, 5), "b": (hung.wait, 5)}, timeout=0.1)
            assert all(isinstance(v, TimeoutError) for v in r.values())
            gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9",
                          "server_token": True}       # …the same client's own reads
            assert gl.run_parallel({"x": (lambda: 1,)}) == {"x": 1}
        finally:
            hung.set()
            gl._ctx.client = None
        for _ in range(50):
            if not slots._held:
                break
            time.sleep(0.05)
        assert not slots._held

    def test_sections_may_wait_for_a_worker_until_the_fanout_deadline(self):
        assert gl.SLOT_WAIT >= gl.GITLAB_FANOUT_TIMEOUT

    def test_joining_another_requests_fetch_respects_the_workers_budget(self, monkeypatch):
        """A fan-out worker that joins a slow fetch owned by another request
        (single-flight) must still end at its own deadline and free its slots."""
        clients = gl._ClientSlots(4)
        monkeypatch.setattr(gl, "_client_workers", clients)
        monkeypatch.setattr(gl, "FANOUT_GRACE", 0.2)

        def slow_gitlab(method, endpoint, body=None, params=None, return_headers=False):
            op = getattr(gl._ctx, "op_deadline", None)
            if op is not None and time.monotonic() >= op:
                raise TimeoutError("budget")           # the real budget rule
            time.sleep(3)
            return [], {}
        monkeypatch.setattr(gl, "_gitlab_request", slow_gitlab)
        cfg = {"base": gl.GITLAB_BASE, "token": "t", "project": "9", "server_token": True}

        def owner():                                   # e.g. a CSV export, 180 s budget
            gl._ctx.gl = cfg
            gl.begin_operation()
            gl.get_all_issues("all")

        t = threading.Thread(target=owner, daemon=True)
        t.start()
        time.sleep(0.2)                                # the owner's fetch is in flight
        gl._ctx.gl = dict(cfg)
        gl._ctx.client = "8.8.8.8"
        try:
            started = time.monotonic()
            r = gl.run_parallel({"issues": (gl.get_all_issues, "all")}, timeout=0.3)
            assert isinstance(r["issues"], TimeoutError)
            while clients._held and time.monotonic() - started < 2.5:
                time.sleep(0.05)
            assert not clients._held                   # released long before the owner ends
        finally:
            gl._ctx.client = None
            t.join(5)


    def test_slots_are_returned_even_after_a_timeout(self, monkeypatch):
        pool = threading.BoundedSemaphore(2)
        clients = gl._ClientSlots(2)
        monkeypatch.setattr(gl, "_custom_workers", pool)
        monkeypatch.setattr(gl, "_client_workers", clients)
        gl._ctx.gl = dict(CUSTOM)
        gl.run_parallel({"slow": (time.sleep, 0.5), "ok": (lambda: 1,)}, timeout=0.1)
        time.sleep(0.8)                                # the slow one finishes
        assert pool.acquire(blocking=False) and pool.acquire(blocking=False)
        assert not clients._held

    def test_workers_get_a_bounded_time_budget(self, monkeypatch):
        monkeypatch.setattr(gl, "FANOUT_GRACE", 0.5)
        gl._ctx.gl = dict(CUSTOM)
        r = gl.run_parallel({"left": (lambda: gl._ctx.op_deadline - time.monotonic(),)},
                            timeout=1)
        assert 0 < r["left"] <= 1.5


class TestOperationDeadline:
    def test_request_after_the_budget_fails_without_network(self):
        gl._ctx.gl = dict(CUSTOM)
        gl.begin_operation(0.05)
        try:
            time.sleep(0.1)
            with pytest.raises(TimeoutError):
                gl._gitlab_request("GET", "/projects/9")
        finally:
            gl.end_operation()

    @staticmethod
    def _slow_pages(delay):
        """Real HTTP server: 100 items per page, always another page, `delay` s
        per page — the budget runs out DURING a page, as in production."""
        class Pages(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                time.sleep(delay)
                body = json.dumps([{"i": j} for j in range(100)]).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("X-Next-Page", "2")
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass
        return _local_server(Pages)

    def test_budget_ending_mid_page_keeps_the_pages_already_read(self):
        srv = self._slow_pages(0.3)
        try:
            _custom_instance(srv)
            gl.begin_operation(1.0)
            try:
                rows = gl._gitlab_paginate("/projects/1/repository/commits", page_limit=50)
            finally:
                gl.end_operation()
            assert rows.truncated is True and 100 <= len(rows) <= 400
        finally:
            srv.shutdown()

    def test_first_page_timing_out_is_still_an_error(self):
        srv = self._slow_pages(1.0)
        try:
            _custom_instance(srv)
            gl.begin_operation(0.3)
            try:
                with pytest.raises(TimeoutError):
                    gl._gitlab_paginate("/projects/1/repository/commits")
            finally:
                gl.end_operation()
        finally:
            srv.shutdown()


class TestByteBudgets:
    def test_pagination_stops_at_the_byte_budget(self, monkeypatch):
        def fake(method, endpoint, body=None, params=None, return_headers=False):
            gl._ctx.last_cost = 1000
            return [{"i": j} for j in range(100)], {"X-Next-Page": "x", "X-Total": "10000"}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        monkeypatch.setattr(gl, "MAX_PAGINATED_BYTES", 2500)
        gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9", "server_token": True}
        rows = gl._gitlab_paginate("/x", page_limit=50)
        assert rows.truncated is True and len(rows) == 300 and rows.nbytes == 3000

    def test_cache_evicts_by_bytes(self):
        c = TTLCache(60, max_entries=100, max_bytes=1000)

        def big(n):
            v = gl.PagedList([1])
            v.nbytes = n
            return v
        for k in "abcd":
            c.get_or_set(k, lambda: big(240))
        assert len(c) == 4 and c._bytes == 960
        c.get_or_set("e", lambda: big(240))            # over budget → LRU out
        assert len(c) == 4 and c._bytes <= 1000 and "a" not in c._data
        c.get_or_set("huge", lambda: big(600))         # > budget/4 → not cached
        assert c._bytes <= 1000 and "huge" not in c._data

    def test_explicit_weights_are_used_and_released(self):
        c = TTLCache(60, max_entries=100, max_bytes=1000)
        c.get_or_set("a", lambda: {"x": 1}, weigh=lambda v: 200)
        assert c._bytes == 200
        c.invalidate("a")
        assert c._bytes == 0 and not c._weights

    def test_pages_never_hold_more_than_per_page_items(self, monkeypatch):
        monkeypatch.setattr(gl, "_gitlab_request",
                            lambda *a, **k: ([{"i": j} for j in range(5000)], {}))
        gl._ctx.gl = dict(CUSTOM)
        assert len(gl._gitlab_paginate("/x")) == 100


class TestMrCounts:
    def test_mr_data_falls_back_to_the_list_inside_the_same_call(self, monkeypatch):
        def fake(method, endpoint, body=None, params=None, return_headers=False):
            if params.get("per_page") == 1:
                return [], {}                           # no X-Total (> 10 000 rows)
            return [{"state": "opened"}, {"state": "merged"}], {}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9", "server_token": True}
        counts, mrs = gl.get_mr_data()
        assert counts is None and gl.mr_summary(counts, mrs)["total"] == 2

    def test_counts_come_from_x_total(self, monkeypatch):
        seen = []

        def fake(method, endpoint, body=None, params=None, return_headers=False):
            seen.append(params)
            return [], {"X-Total": "7" if params["state"] == "opened" else "250"}
        monkeypatch.setattr(gl, "_gitlab_request", fake)
        gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9", "server_token": True}
        assert gl.get_mr_counts() == {"opened": 7, "all": 250}
        assert all(p["per_page"] == 1 for p in seen)   # never downloads the list
        assert gl.mr_summary(gl.get_mr_counts()) == {"open": 7, "total": 250,
                                                     "complete": True}

    def test_missing_x_total_means_fallback(self, monkeypatch):
        monkeypatch.setattr(gl, "_gitlab_request", lambda *a, **k: ([], {}))
        gl._ctx.gl = {"base": gl.GITLAB_BASE, "token": "t", "project": "9", "server_token": True}
        assert gl.get_mr_counts() is None

    def test_summary_from_a_truncated_list_uses_the_total(self):
        mrs = gl.PagedList([{"state": "opened"}, {"state": "merged"}])
        mrs.truncated, mrs.total = True, 900
        assert gl.mr_summary(None, mrs) == {"open": 1, "total": 900, "complete": False}
        assert gl.mr_summary(None, [{"state": "opened"}]) == {"open": 1, "total": 1,
                                                              "complete": True}


# ── Hostile instances: HTTPS deadline, error bodies, JSON structure, weights ───

HERE = os.path.dirname(os.path.abspath(__file__))
CERT = os.path.join(HERE, "tls_test_cert.pem")   # self-signed, 127.0.0.1/localhost
KEY = os.path.join(HERE, "tls_test_key.pem")


def _tls_server(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def trusted_https(monkeypatch):
    """The caller-instance path (_PublicOnlyHTTPSConnection) against loopback:
    only DNS/SSRF resolution and the trusted CA are test stand-ins."""
    ctx = ssl.create_default_context(cafile=CERT)
    monkeypatch.setattr(gl, "_public_opener", urllib.request.build_opener(
        gl._NoRedirect, gl._HTTPHandler, gl._PublicOnlyHTTPSHandler(context=ctx)))
    monkeypatch.setattr(gl, "_public_addresses",
                        lambda host, port: ([(socket.AF_INET, ("127.0.0.1", port))], None))
    monkeypatch.setattr(gl, "CUSTOM_TOTAL_TIMEOUT", 1)

    def use(port):
        gl._ctx.gl = {"base": f"https://localhost:{port}/api/v4", "token": "",
                      "project": "1", "server_token": False}
    return use


class TestHttpsDeadlines:
    """The TLS wrap detaches the socket the watchdog was given: without the
    duplicate descriptor these calls ran for as long as the host liked."""

    def _timed(self):
        started = time.monotonic()
        with pytest.raises((TimeoutError, OSError)):
            gl._gitlab_request("GET", "/projects/1")
        return time.monotonic() - started

    def test_trickled_headers_over_tls_are_cut(self, trusted_https):
        stop = threading.Event()

        class Trickle(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                    while not stop.is_set():
                        self.wfile.write(b"X-Pad: a\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

        srv = _tls_server(Trickle)
        try:
            trusted_https(srv.server_address[1])
            assert self._timed() < 4
            assert not gl._gitlab_watchdog._items
        finally:
            stop.set()
            srv.shutdown()

    def test_endless_100_continue_over_tls_is_cut(self, trusted_https):
        stop = threading.Event()

        class Continue(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    while not stop.is_set():
                        self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

        srv = _tls_server(Continue)
        try:
            trusted_https(srv.server_address[1])
            assert self._timed() < 4
        finally:
            stop.set()
            srv.shutdown()

    def test_trickled_handshake_is_cut(self, trusted_https):
        stop = threading.Event()
        lsock = socket.socket()
        lsock.bind(("127.0.0.1", 0))
        lsock.listen()

        def serve():
            conn, _ = lsock.accept()
            try:
                conn.sendall(b"\x16\x03\x03\x40\x00")   # a 16 KB handshake record…
                while not stop.is_set():
                    conn.sendall(b"\x00")                # …sent one byte at a time
                    time.sleep(0.1)
            except OSError:
                pass
            finally:
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        try:
            trusted_https(lsock.getsockname()[1])
            assert self._timed() < 4
        finally:
            stop.set()
            lsock.close()

    def test_normal_tls_request_works_and_leaves_nothing_armed(self, trusted_https):
        class Ok(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"[]")

        srv = _tls_server(Ok)
        try:
            trusted_https(srv.server_address[1])
            assert gl._gitlab_request("GET", "/projects/1") == []
            assert not gl._gitlab_watchdog._items
        finally:
            srv.shutdown()


class TestErrorBodies:
    def _error_server(self, body_writer):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    body_writer(self)
                except OSError:
                    pass

        return _local_server(H)

    def test_trickled_error_body_is_cut_and_the_message_is_immediate(self, monkeypatch):
        stop = threading.Event()

        def trickle(h):
            h.send_response(404)
            h.send_header("Content-Length", "100000000")
            h.end_headers()
            while not stop.is_set():
                h.wfile.write(b"x")
                h.wfile.flush()
                time.sleep(0.1)

        srv = self._error_server(trickle)
        monkeypatch.setattr(gl, "CUSTOM_TOTAL_TIMEOUT", 1)
        try:
            _custom_instance(srv)
            started = time.monotonic()
            with pytest.raises(urllib.error.HTTPError) as e:
                gl._gitlab_request("GET", "/projects/1")
            msg = gl.gitlab_error_message(e.value)
            assert time.monotonic() - started < 4 and "Não encontrado" in msg
            assert len(e.value.gl_body) <= gl.MAX_ERROR_BODY_BYTES
        finally:
            stop.set()
            srv.shutdown()

    def test_huge_error_body_is_not_buffered(self):
        def flood(h):
            h.send_response(500)
            h.end_headers()                           # no length: read to EOF
            chunk = b"x" * 65536
            for _ in range(160):                      # 10 MB
                h.wfile.write(chunk)

        srv = self._error_server(flood)
        try:
            _custom_instance(srv)
            with pytest.raises(urllib.error.HTTPError) as e:
                gl._gitlab_request("GET", "/projects/1")
            assert len(e.value.gl_body) <= gl.MAX_ERROR_BODY_BYTES
            assert "HTTP 500" in gl.gitlab_error_message(e.value)
        finally:
            srv.shutdown()

    def test_gitlab_reason_is_still_shown(self):
        def reason(h):
            body = json.dumps({"message": "Title is too long"}).encode()
            h.send_response(400)
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)

        srv = self._error_server(reason)
        try:
            _custom_instance(srv)
            with pytest.raises(urllib.error.HTTPError) as e:
                gl._gitlab_request("GET", "/projects/1")
            assert "Title is too long" in gl.gitlab_error_message(e.value)
        finally:
            srv.shutdown()


class TestParsedSize:
    def _json_server(self, payload: bytes):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

        return _local_server(H)

    def test_pathological_json_from_a_caller_instance_is_refused(self):
        srv = self._json_server(b"[" + b"{}," * 300_000 + b"{}]")   # ~0.9 MB, 300k objects
        try:
            _custom_instance(srv)
            with pytest.raises(ValueError, match="complexa"):
                gl._gitlab_request("GET", "/projects/1")
        finally:
            srv.shutdown()

    def test_every_cached_value_is_weighed(self):
        payload = json.dumps({"name": "x", "pad": ["abc"] * 20000}).encode()
        srv = self._json_server(payload)
        try:
            _custom_instance(srv)
            gl.get_project_info()
            key = next(k for k in gl.cache._weights if "project:info" in k)
            assert gl.cache._weights[key] >= len(payload) + 20000 * gl.VALUE_COST
        finally:
            srv.shutdown()


class TestBodyEndOfFile:
    """read1() reports EOF as a normal end of body: a connection cut by the
    watchdog (Linux) or dropped mid-body must never become a 'complete' answer."""

    def _raw_server(self, script):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    script(self)
                except OSError:
                    pass
        return _local_server(H)

    def test_dropped_before_content_length_is_an_error_not_an_empty_answer(self):
        def drop(h):
            h.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                          b"Content-Length: 5000\r\nConnection: close\r\n\r\n[{\"a\": 1}")
            h.wfile.flush()
        srv = self._raw_server(drop)
        try:
            _custom_instance(srv)
            with pytest.raises((http.client.HTTPException, OSError)):
                gl._gitlab_request("GET", "/projects/1")
        finally:
            srv.shutdown()

    def test_end_of_body_after_the_deadline_is_a_timeout(self, monkeypatch):
        # emulate Linux, where the watchdog's cut makes the blocked read return EOF
        monkeypatch.setattr(gl._gitlab_watchdog, "arm", lambda sock, seconds: None)
        monkeypatch.setattr(gl, "CUSTOM_TOTAL_TIMEOUT", 1)

        def late_eof(h):
            time.sleep(0.3)
            h.wfile.write(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
                          b"[{\"a\": 1}")
            h.wfile.flush()
            time.sleep(0.9)                            # EOF arrives after the deadline
        srv = self._raw_server(late_eof)
        try:
            _custom_instance(srv)
            with pytest.raises(TimeoutError):
                gl._gitlab_request("GET", "/projects/1")
        finally:
            srv.shutdown()

    def test_complete_bodies_are_unaffected(self):
        def ok(h):
            body = b'[{"a": 1}]'
            h.wfile.write(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                          b"Content-Length: %d\r\n\r\n" % len(body) + body)
        srv = self._raw_server(ok)
        try:
            _custom_instance(srv)
            assert gl._gitlab_request("GET", "/projects/1") == [{"a": 1}]
        finally:
            srv.shutdown()
