"""
Cliente GitLab: cache TTL thread-safe, contexto multi-tenant por pedido
(thread-local), request/paginação e todos os fetchers (issues, milestones,
projeto, commits, contribuidores, MRs, linguagens, árvore, README, blame).
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, timedelta

from src.config import (CACHE_TTL, GITLAB_ALLOW_PRIVATE, GITLAB_BASE,
                        GITLAB_FANOUT_TIMEOUT, GITLAB_PAGE_LIMIT,
                        GITLAB_PROJECT_ID, GITLAB_TOKEN, GITLAB_WORKERS,
                        GITLAB_WORKERS_PER_CLIENT)
from src.threading_server import SocketWatchdog

log = logging.getLogger("sprintlab")


# ── Thread-safe TTL cache (classe em src/cache.py) ───────────────────────────
from src.cache import TTLCache  # noqa: E402  (re-export: gitlab_api.TTLCache)


class _MeteredCache(TTLCache):
    """TTLCache whose entries weigh what producing them cost: the estimated
    in-memory size of every GitLab response parsed meanwhile (see _cost) — so
    dicts, lists and README strings all count toward max_bytes, not just the
    paginated lists."""

    def get_or_set(self, key, producer, weigh=None, wait=None):
        spent = [0]

        def metered():
            start = getattr(_ctx, "cost", 0)
            try:
                return producer()
            finally:
                spent[0] = getattr(_ctx, "cost", 0) - start

        # Waiting for someone else's fetch of the same key is bounded by the
        # CALLER's time budget (a fan-out worker must not outlive its deadline
        # because a slower request owns the fetch).
        op = getattr(_ctx, "op_deadline", None)
        if wait is None and op is not None:
            wait = op - time.monotonic()
        return super().get_or_set(
            key, metered,
            weigh=weigh or (lambda v: max(spent[0], self._weight(v))), wait=wait)


cache = _MeteredCache(CACHE_TTL)

# ── Per-request GitLab config ─────────────────────────────────────────────────
# Each user can point at their own GitLab via the settings panel: the frontend
# sends X-GL-Base / X-GL-Token / X-GL-Project headers, resolved (and validated)
# by resolve_gl_config() into a thread-local at the start of every request.
# The server's own GITLAB_TOKEN is ONLY used for the server's own instance and
# project (no X-GL-Base / X-GL-Project) — never lent to a caller-chosen target.
_ctx = threading.local()

_PROJECT_RE = re.compile(r"^(?:\d+|[\w.-]+(?:/[\w.-]+)+)$")
_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,512}$")   # printable ASCII, no spaces


def normalise_project(project) -> str:
    """Project id / namespace path as typed: 'grupo%2Fprojeto' (the form the
    GitLab API docs show) and 'grupo/projeto' are the same project."""
    return urllib.parse.unquote(str(project or "").strip()).strip()


def valid_project(project) -> bool:
    p = str(project or "")
    return (bool(_PROJECT_RE.fullmatch(p))
            and not any(s in (".", "..") for s in p.split("/")))


_DEFAULT_PROJECT = normalise_project(GITLAB_PROJECT_ID)
if not valid_project(_DEFAULT_PROJECT):
    raise SystemExit(
        f"GITLAB_PROJECT_ID must be a numeric project id or a namespace path "
        f"like group/project (got {GITLAB_PROJECT_ID!r}).")


def _default_ctx():
    return {"base": GITLAB_BASE, "token": GITLAB_TOKEN,
            "project": _DEFAULT_PROJECT, "server_token": True}


def _gl():
    return getattr(_ctx, "gl", None) or _default_ctx()


def _proj():
    """Project id/path URL-encoded for use inside an API path ('grp/proj' →
    'grp%2Fproj'), so no character in it can change the request target."""
    return urllib.parse.quote(str(_gl()["project"]), safe="")


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4COMPAT = ipaddress.ip_network("::/96")


def _ip_allowed(ip) -> bool:
    """Public unicast only — including the IPv4 address hidden inside IPv6 forms
    (IPv4-mapped/compatible, 6to4, Teredo, NAT64), which could route to an
    internal IPv4 host."""
    if not ip.is_global or ip.is_multicast:
        return False
    if ip.version == 6:
        v4 = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if v4 is None and (ip in _NAT64 or ip in _V4COMPAT):
            v4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if v4 is not None and (not v4.is_global or v4.is_multicast):
            return False
    return True


def _public_addresses(host, port):
    """(addresses, error): every address `host` resolves to, if ALL are public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return [], "Não consegui resolver o endereço da instância GitLab."
    addrs = []
    for family, _, _, _, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        except ValueError:
            return [], "Endereço da instância GitLab inválido."
        if not _ip_allowed(ip):
            return [], "Endereço da instância GitLab não permitido (rede interna)."
        addrs.append((family, sockaddr))
    if not addrs:
        return [], "Não consegui resolver o endereço da instância GitLab."
    return addrs, None


def _host_error(host, port):
    """None if every address `host` resolves to is public; else an error message."""
    return _public_addresses(host, port)[1]


def _check_base(raw):
    s = (raw or "").strip()
    try:
        u = urllib.parse.urlsplit(s)
        port = u.port
    except ValueError:
        return None, "URL da instância GitLab inválido."
    scheme = u.scheme.lower()
    if scheme != "https" and not (GITLAB_ALLOW_PRIVATE and scheme == "http"):
        return None, "O URL da instância GitLab tem de começar por https://."
    if (not u.hostname or u.username or u.password or u.query or u.fragment
            or port == 0):
        return None, "URL da instância GitLab inválido."
    path = u.path.rstrip("/")
    if any(seg in (".", "..") for seg in path.split("/")):
        return None, "URL da instância GitLab inválido."
    if not GITLAB_ALLOW_PRIVATE:
        err = _host_error(u.hostname, port or 443)
        if err:
            return None, err
    if not path.endswith("/api/v4"):
        path += "/api/v4"
    return f"{scheme}://{u.netloc}{path}", None


# Validated bases — short TTL, bounded size. Only SUCCESSES are cached (a
# transient DNS failure must not break a workspace for minutes). The address
# check is repeated when connecting (see _PublicOnlyHTTPSConnection), so a
# cached verdict can't be abused by DNS rebinding.
_base_cache = TTLCache(300, max_entries=128)


class _InvalidBase(ValueError):
    pass


def normalise_base(raw):
    """(api_base_url, error) for a caller-supplied GitLab instance URL."""
    def _produce():
        base, err = _check_base(raw)
        if err:
            raise _InvalidBase(err)
        return base
    try:
        return _base_cache.get_or_set(f"base:{raw}", _produce), None
    except _InvalidBase as e:
        return None, str(e)


def resolve_gl_config(base_h="", token_h="", project_h=""):
    """Per-request GitLab config from the X-GL-* headers → (cfg, error).

    Security rules:
      - custom instances must be https:// on public addresses (no SSRF into
        internal networks / cloud metadata);
      - the project must be a numeric id or a namespace path (no characters that
        could retarget the API path, e.g. '#', '?');
      - the server's GITLAB_TOKEN is used only when the caller names neither an
        instance nor a project; otherwise the caller's own token (or none)."""
    base_h = (base_h or "").strip()
    token_h = (token_h or "").strip()
    project_h = (project_h or "").strip()
    base = GITLAB_BASE
    if base_h:
        base, err = normalise_base(base_h)
        if err:
            return None, err
    project = normalise_project(project_h) if project_h else _DEFAULT_PROJECT
    if not valid_project(project):
        return None, "Project ID inválido (usa o número do projeto ou grupo/projeto)."
    if token_h:
        if not _TOKEN_RE.match(token_h):
            return None, "Token GitLab inválido."
        return {"base": base, "token": token_h, "project": project,
                "server_token": False}, None
    if base_h or project_h:
        return {"base": base, "token": "", "project": project,
                "server_token": False}, None
    return _default_ctx(), None


def _tok_fp(token):
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else "anon"


def _ck(name: str) -> str:
    """Cache key namespaced by instance + project + token fingerprint: data read
    with one token is never served to a request carrying a different token."""
    g = _gl()
    return f"{g['base']}#{g['project']}:{name}#{_tok_fp(g['token'])}"


def _ck_prefix(name: str) -> str:
    """Prefix matching `name` for this instance+project under ANY token (a write
    by one user must refresh everyone's view of that project)."""
    g = _gl()
    return f"{g['base']}#{g['project']}:{name}"


def _run_with_ctx(cfg, op_deadline, fn, *args):
    """Run fn in a worker thread carrying the caller's GitLab config and time
    budget (thread-local does not propagate into worker threads)."""
    _ctx.gl, _ctx.op_deadline = cfg, op_deadline
    try:
        return fn(*args)
    finally:
        _ctx.op_deadline = None


class BusyError(RuntimeError):
    """No GitLab worker free — the SERVER is busy; GitLab itself may be fine."""


class _ClientSlots:
    """At most `quota` worker slots held at once per client key ((IP, pool)):
    one client can't take a whole pool. acquire() waits, bounded, for one of
    the client's own slots to free up."""

    def __init__(self, quota):
        self.quota = max(1, quota)
        self._held = {}
        self._cond = threading.Condition()

    def acquire(self, key, timeout):
        end = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while self._held.get(key, 0) >= self.quota:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            self._held[key] = self._held.get(key, 0) + 1
            return True

    def release(self, key):
        with self._cond:
            n = self._held.get(key, 0) - 1
            if n > 0:
                self._held[key] = n
            else:
                self._held.pop(key, None)
            self._cond.notify_all()


# Process-wide caps on GitLab worker threads. The server's own instance has its
# own share, so caller-chosen instances (however slow) can never starve it.
_OWN_WORKERS = max(1, GITLAB_WORKERS * 2 // 3)
_own_workers = threading.BoundedSemaphore(_OWN_WORKERS)
_custom_workers = threading.BoundedSemaphore(max(1, GITLAB_WORKERS - _OWN_WORKERS))
_client_workers = _ClientSlots(GITLAB_WORKERS_PER_CLIENT)
# s a section may queue for a worker: up to the fan-out deadline, the most the
# handler waits anyway (bursts wait, not fail)
SLOT_WAIT = GITLAB_FANOUT_TIMEOUT
FANOUT_GRACE = 10    # s a worker may run past the answer (its result fills the cache)


def _pool_for(cfg):
    return _own_workers if _is_own_instance(cfg) else _custom_workers


def run_parallel(tasks, timeout=None):
    """Run {name: (fn, *args)} concurrently with the current request's GitLab
    config and a TOTAL deadline. Returns {name: value | Exception} — a section that
    failed or didn't finish in time comes back as an exception, never raises; one
    that got no worker comes back as BusyError.

    Workers are short-lived and capped: per process (own instance and caller-
    chosen instances have separate shares) and per client. A worker may finish
    up to FANOUT_GRACE after the answer — so a slow section still fills the cache
    for the next request — but never later, whatever the GitLab does."""
    cfg = _gl()
    timeout = GITLAB_FANOUT_TIMEOUT if timeout is None else timeout
    fan_deadline = time.monotonic() + timeout
    worker_deadline = fan_deadline + FANOUT_GRACE
    op = getattr(_ctx, "op_deadline", None)
    if op is not None:
        worker_deadline = min(worker_deadline, op)
    pool = _pool_for(cfg)
    # per-client quota kept PER POOL: a client's (or its NAT's) slow caller-chosen
    # instance can't use up the slots its reads on the server's own GitLab need
    client = (getattr(_ctx, "client", None) or "-", pool is _own_workers)

    def _task(spec):
        try:
            return _run_with_ctx(cfg, worker_deadline, spec[0], *spec[1:])
        finally:
            pool.release()
            _client_workers.release(client)

    def _wait_left():
        return max(0.0, min(SLOT_WAIT, fan_deadline - time.monotonic()))

    out, futs = {}, {}
    ex = ThreadPoolExecutor(max_workers=len(tasks) or 1, thread_name_prefix="gitlab")
    try:
        for name, spec in tasks.items():
            if not _client_workers.acquire(client, _wait_left()):
                out[name] = BusyError("servidor ocupado — secção não carregada")
                continue
            if not pool.acquire(timeout=_wait_left()):
                _client_workers.release(client)
                out[name] = BusyError("servidor ocupado — secção não carregada")
                continue
            try:
                futs[name] = ex.submit(_task, spec)
            except BaseException:
                pool.release()
                _client_workers.release(client)
                raise
        done, _ = wait(list(futs.values()),
                       timeout=max(0.0, fan_deadline - time.monotonic()))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        for f in futs.values():
            if f.cancelled():             # never ran → its finally never released
                pool.release()
                _client_workers.release(client)
    for name, f in futs.items():
        if f in done:
            try:
                out[name] = f.result()
            except Exception as e:
                out[name] = e
        else:
            out[name] = TimeoutError(f"GitLab não respondeu a tempo ({name})")
    return {name: out[name] for name in tasks}


# ── Time budget of one request ────────────────────────────────────────────────
# Every GitLab call has its own total deadline (below), and every call made while
# serving one client request shares this budget too: a paginated export or a
# multi-step flow can't hold a connection slot for pages x per-call deadline.
GITLAB_OP_TIMEOUT = 180    # s, server's own instance
CUSTOM_OP_TIMEOUT = 60     # s, caller-chosen instance


def begin_operation(seconds=None):
    """Start the GitLab time budget of the current request (the server calls it
    once the request's config is known; a long flow may start a new one per
    step, e.g. the chat's tool calls after the model has answered)."""
    if seconds is None:
        seconds = GITLAB_OP_TIMEOUT if _is_own_instance(_gl()) else CUSTOM_OP_TIMEOUT
    _ctx.op_deadline = time.monotonic() + seconds


def end_operation():
    _ctx.op_deadline = None


def _op_expired():
    op = getattr(_ctx, "op_deadline", None)
    return op is not None and time.monotonic() >= op


# ── GitLab client ─────────────────────────────────────────────────────────────

GITLAB_SOCKET_TIMEOUT = 30        # s per socket operation
# TOTAL time for one request (connect + TLS + headers + body), enforced by a
# watchdog that shuts the socket down: a server trickling bytes can't pin a
# worker thread. Caller-chosen instances get less time and fewer bytes.
GITLAB_TOTAL_TIMEOUT = 60
CUSTOM_TOTAL_TIMEOUT = 30
MAX_RESPONSE_BYTES = 20 * 1024 * 1024          # one response, on the wire
CUSTOM_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024               # what is read of an error answer
# Parsed JSON can take far more memory than its wire size ('[{},{},...]' ~24x),
# so sizes are ESTIMATED as wire bytes + VALUE_COST per JSON value (see _cost),
# and a caller-chosen instance can't send more than CUSTOM_MAX_JSON_VALUES in one
# answer (real GitLab pages of 100 items hold a few thousand to ~20k).
VALUE_COST = 64
CUSTOM_MAX_JSON_VALUES = 250_000
MAX_PAGINATED_BYTES = 120 * 1024 * 1024        # all pages of one list (estimated)
CUSTOM_MAX_PAGINATED_BYTES = 40 * 1024 * 1024


def _is_own_instance(gl):
    return gl["base"] == GITLAB_BASE


_gitlab_watchdog = SocketWatchdog()


def _arm(sock):
    """Bind a fresh socket to the current request's total deadline.

    The watchdog holds a DUPLICATE of the descriptor: HTTPSConnection wraps the
    socket in an SSLSocket, which detach()es the original object (fileno -1) —
    shutting that down would do nothing. shutdown() on the duplicate cuts the
    shared connection, so the TLS handshake, headers and body are all bounded."""
    deadline = getattr(_ctx, "deadline", None)
    if deadline is not None:
        try:
            handle = sock.dup()
        except OSError:
            handle = sock        # no spare descriptor: better than nothing (http)
        _gitlab_watchdog.arm(handle, max(0.0, deadline - time.monotonic()))
        _ctx.sockets.append((handle, handle is not sock))
    return sock


def _connect_timeout(timeout):
    """The per-operation socket timeout, but never past the request's deadline
    (the TCP connect happens before the socket can be armed)."""
    deadline = getattr(_ctx, "deadline", None)
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("GitLab demorou demasiado a responder")
    if timeout is None or timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
        return left
    return min(timeout, left)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would turn a POST/PUT into a GET (a write
    that silently doesn't happen) and re-send PRIVATE-TOKEN to the new host.
    A 3xx surfaces as HTTPError instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self._open_socket

    def _open_socket(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                     source_address=None):
        return _arm(socket.create_connection(address, _connect_timeout(timeout),
                                             source_address))


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """The TCP socket is armed (through a duplicate, see _arm) BEFORE the TLS
    handshake, so a slow handshake is also bounded by the total deadline."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self._open_socket

    def _open_socket(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                     source_address=None):
        return _arm(socket.create_connection(address, _connect_timeout(timeout),
                                             source_address))


class _PublicOnlyHTTPSConnection(_DeadlineHTTPSConnection):
    """HTTPS connection for CALLER-SUPPLIED instances: resolves the host, refuses
    if any address isn't public, and connects to exactly the address it checked
    (TLS/SNI still use the hostname). A DNS answer that changes between the check
    and the connection (rebinding) can't reach an internal service."""

    def _open_socket(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                     source_address=None):
        return _arm(self._connect_public(address, timeout, source_address))

    @staticmethod
    def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                        source_address=None):
        host, port = address
        addrs, err = _public_addresses(host, port)
        if err:
            raise urllib.error.URLError(err)
        last = None
        for family, sockaddr in addrs[:8]:    # the host controls its DNS answers
            try:
                return socket.create_connection((sockaddr[0], port),
                                                _connect_timeout(timeout), source_address)
            except TimeoutError:
                raise
            except OSError as e:
                last = e
        raise last


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_DeadlineHTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_DeadlineHTTPSConnection, req, context=self._context)


class _PublicOnlyHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicOnlyHTTPSConnection, req, context=self._context)


_opener = urllib.request.build_opener(_NoRedirect, _HTTPHandler, _HTTPSHandler)
_public_opener = urllib.request.build_opener(_NoRedirect, _HTTPHandler,
                                             _PublicOnlyHTTPSHandler)


def _opener_for(gl):
    """The server's own instance is trusted (it may be a private self-managed
    GitLab); any other instance only on public addresses."""
    if _is_own_instance(gl) or GITLAB_ALLOW_PRIVATE:
        return _opener
    return _public_opener


def _read_body(r, deadline, max_bytes=MAX_RESPONSE_BYTES):
    chunks, total = [], 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("GitLab demorou demasiado a enviar a resposta")
        chunk = r.read1(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Resposta do GitLab demasiado grande")
        chunks.append(chunk)
    # read1() reports EOF as a normal end of body, even before Content-Length:
    # on Linux the watchdog's shutdown() makes a blocked read return EOF, and a
    # connection dropped mid-body looks the same. Neither is a complete answer.
    left = getattr(r, "length", None)
    if left == 0:                       # Content-Length reached: complete
        return b"".join(chunks)
    if time.monotonic() >= deadline:    # cut by the watchdog
        raise TimeoutError("GitLab demorou demasiado a enviar a resposta")
    if left:                            # dropped before Content-Length
        raise http.client.IncompleteRead(b"".join(chunks), left)
    return b"".join(chunks)


def _cost(raw):
    """(estimated in-memory size once parsed, number of JSON values) of a body.
    Commas inside strings are over-counted — which only makes it stricter."""
    values = raw.count(b",") + raw.count(b"{") + raw.count(b"[")
    return len(raw) + VALUE_COST * values, values


def _gitlab_request(method, endpoint, body=None, params=None, return_headers=False):
    gl = _gl()
    if not str(endpoint).startswith("/") or any(c in endpoint for c in "#?\r\n"):
        raise ValueError(f"endpoint GitLab inválido: {endpoint!r}")
    url = f"{gl['base']}{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if gl["token"]:
        headers["PRIVATE-TOKEN"] = gl["token"]
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    own = _is_own_instance(gl)
    deadline = time.monotonic() + (GITLAB_TOTAL_TIMEOUT if own else CUSTOM_TOTAL_TIMEOUT)
    op = getattr(_ctx, "op_deadline", None)
    if op is not None:
        deadline = min(deadline, op)
    if deadline <= time.monotonic():
        raise TimeoutError("GitLab demorou demasiado a responder")
    _ctx.deadline, _ctx.sockets = deadline, []
    try:
        with _opener_for(gl).open(req, timeout=GITLAB_SOCKET_TIMEOUT) as r:
            raw = _read_body(r, deadline,
                             MAX_RESPONSE_BYTES if own else CUSTOM_MAX_RESPONSE_BYTES)
            cost, values = _cost(raw)
            if not own and values > CUSTOM_MAX_JSON_VALUES:
                raise ValueError("Resposta do GitLab demasiado complexa")
            _ctx.last_cost = cost
            _ctx.cost = getattr(_ctx, "cost", 0) + cost
            payload = json.loads(raw) if raw else {}  # DELETE returns 204 / empty body
            if return_headers:
                # Return the HTTPMessage directly — its .get() is case-insensitive.
                # GitLab sends "x-next-page" (lowercase); converting to dict() would
                # lose the case-insensitive lookup and silently break pagination.
                return payload, r.headers
            return payload
    except urllib.error.HTTPError as e:
        # Read a bounded prefix of the error body NOW, while the watchdog still
        # bounds the socket — never later from a live connection (a hostile
        # instance could trickle or flood it).
        body = b""
        try:
            if e.fp is not None:
                body = _read_body(e, deadline, MAX_ERROR_BODY_BYTES)
        except (ValueError, OSError, http.client.HTTPException):
            body = b""
        finally:
            try:
                e.close()
            except Exception:
                pass
        e.gl_body = body
        raise
    except (OSError, http.client.HTTPException) as e:
        if time.monotonic() >= deadline:   # the watchdog cut the connection
            raise TimeoutError("GitLab demorou demasiado a responder") from e
        raise
    finally:
        for s, dup in _ctx.sockets:
            _gitlab_watchdog.disarm(s)
            if dup:
                try:
                    s.close()          # the duplicate descriptor (see _arm)
                except OSError:
                    pass
        _ctx.deadline, _ctx.sockets = None, []


def gitlab_error_detail(e, limit=300):
    """GitLab's own reason ('message' / 'error') from an HTTPError body, or ''."""
    raw = getattr(e, "gl_body", None)
    if raw is None:              # an HTTPError not raised by _gitlab_request
        try:
            raw = e.read(MAX_ERROR_BODY_BYTES) or b""
        except Exception:
            return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ""   # HTML error page etc. — not useful to show
    if not isinstance(data, dict):
        return ""
    msg = data.get("message") or data.get("error") or ""
    if isinstance(msg, dict):
        msg = "; ".join(
            f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
            for k, v in msg.items())
    elif isinstance(msg, list):
        msg = "; ".join(map(str, msg))
    return str(msg).strip()[:limit]


def gitlab_error_message(e, detail=None):
    """Portuguese, user-facing message for a failed GitLab call (no internals)."""
    if isinstance(e, urllib.error.HTTPError):
        if detail is None:
            detail = gitlab_error_detail(e)
        if e.code == 401 and not _gl().get("token"):
            return ("Este pedido precisa de um token GitLab — edita o projeto (✏️) "
                    "e adiciona um token de acesso.")
        base = {
            401: "Token GitLab inválido ou expirado.",
            403: "Sem permissão no GitLab para esta operação.",
            404: "Não encontrado no GitLab (verifica o projeto e o número).",
            409: "Conflito no GitLab.",
            429: "O GitLab limitou os pedidos — tenta daqui a pouco.",
        }.get(e.code)
        if base is None and 300 <= e.code < 400:
            base = ("O URL da instância GitLab redireciona — usa o endereço final "
                    "(https://…).")
        if base is None:
            base = f"O GitLab recusou o pedido (HTTP {e.code})."
        return f"{base} {detail}".strip() if detail else base
    if isinstance(e, BusyError):
        return "Servidor ocupado — tenta daqui a pouco."
    if isinstance(e, TimeoutError):
        return "O GitLab não respondeu a tempo."
    if isinstance(e, urllib.error.URLError):
        return "Não consegui ligar ao GitLab."
    return "Erro ao comunicar com o GitLab."


class PagedList(list):
    """A list of API items that knows whether the page limit (or the size / time
    budget) cut it short, and roughly how much memory it takes."""
    truncated = False
    total = None   # GitLab's X-Total, when sent
    nbytes = 0     # estimated in-memory size (see _cost)


def _int_header(headers, name):
    try:
        v = headers.get(name)
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def _gitlab_paginate(endpoint, params=None, page_limit=None):
    """Generic paginator. Follows X-Next-Page until exhausted or page_limit hit —
    in which case the result is flagged `.truncated` (never silently partial)."""
    if page_limit is None:
        page_limit = GITLAB_PAGE_LIMIT
    budget = MAX_PAGINATED_BYTES if _is_own_instance(_gl()) else CUSTOM_MAX_PAGINATED_BYTES
    items = PagedList()
    base = dict(params or {})
    base.setdefault("per_page", 100)
    per_page = int(base["per_page"])
    page = 1
    while True:
        _ctx.last_cost = 0
        try:
            batch, headers = _gitlab_request(
                "GET", endpoint, params={**base, "page": page}, return_headers=True
            )
        except TimeoutError:
            # The request's time budget ran out during a later page: keep what
            # was read (flagged partial) instead of throwing it all away. A
            # first page that times out is still an error (dead instance).
            if page > 1 and _op_expired():
                items.truncated = True
                break
            raise
        items.nbytes += getattr(_ctx, "last_cost", 0) or 0
        if page == 1:
            items.total = _int_header(headers, "X-Total")
        if not isinstance(batch, list):
            break
        items.extend(batch[:per_page])     # GitLab never sends more than per_page
        if len(batch) < per_page or not headers.get("X-Next-Page"):
            break
        if page >= page_limit or items.nbytes >= budget or _op_expired():
            items.truncated = True
            break
        page += 1
    return items


_ISSUE_CACHES = ("issues:", "milestones:", "labels:")


def _invalidate_project(names):
    for name in names:
        cache.invalidate(_ck_prefix(name))


def gitlab_request(method, endpoint, body=None, params=None, invalidate=_ISSUE_CACHES):
    """Public entry. Writes invalidate the affected caches BEFORE and AFTER the
    call: a read that starts while the write is in flight must not re-cache the
    old data for a whole TTL."""
    if method == "GET":
        return _gitlab_request(method, endpoint, body, params)
    _invalidate_project(invalidate)
    try:
        return _gitlab_request(method, endpoint, body, params)
    finally:
        _invalidate_project(invalidate)


# ── Issues ────────────────────────────────────────────────────────────────────


def get_all_issues(state: str = "all"):
    return cache.get_or_set(_ck(f"issues:{state}"), lambda: _fetch_all_issues(state))


def _fetch_all_issues(state: str):
    issues = _gitlab_paginate(f"/projects/{_proj()}/issues", {"state": state})
    log.info("gitlab fetched %d issues (state=%s%s)", len(issues), state,
             ", TRUNCATED" if issues.truncated else "")
    return issues


def get_issue_counts():
    """Exact {all, opened, closed} counts (issues_statistics) — used when the issue
    list was truncated by the page limit. None if the instance doesn't provide it."""
    def _fetch():
        r = _gitlab_request("GET", f"/projects/{_proj()}/issues_statistics")
        c = ((r or {}).get("statistics") or {}).get("counts") or {}
        return {"all": int(c["all"]), "opened": int(c["opened"]),
                "closed": int(c["closed"])}
    try:
        return cache.get_or_set(_ck("issues:counts"), _fetch)
    except Exception as e:
        log.warning("issue counts unavailable: %s", e)
        return None


def get_recently_closed_issues(since=None):
    """Closed issues, most recently UPDATED first (closing updates an issue), so
    recently closed old issues are never cut off by the page limit. With `since`
    (a date), only issues updated from that day on."""
    params = {"state": "closed", "order_by": "updated_at", "sort": "desc"}
    key = "issues:closed-recent"
    if since:
        params["updated_after"] = f"{since.isoformat()}T00:00:00Z"
        key += f":{since.isoformat()}"
    return cache.get_or_set(
        _ck(key), lambda: _gitlab_paginate(f"/projects/{_proj()}/issues", params))


def get_issue(iid):
    """One issue by number (fresh, not cached)."""
    return _gitlab_request("GET", f"/projects/{_proj()}/issues/{int(iid)}")


# ── Milestones ────────────────────────────────────────────────────────────────


def _fetch_milestones(state=None):
    """Project milestones INCLUDING the parent groups' milestones (projects in a
    group usually plan sprints with group milestones). `include_ancestors` is the
    current GitLab parameter; older instances only know the deprecated
    `include_parent_milestones` — and reject the unknown one with 400."""
    params = {"state": state} if state else {}
    endpoint = f"/projects/{_proj()}/milestones"
    variants = ({"include_ancestors": "true"}, {"include_parent_milestones": "true"}, {})
    for extra in variants:
        try:
            return _gitlab_paginate(endpoint, {**params, **extra})
        except urllib.error.HTTPError as e:
            if e.code != 400 or not extra:
                raise
    return PagedList()


def get_milestones():
    """Active milestones (project + ancestor groups)."""
    return cache.get_or_set(_ck("milestones:active"), lambda: _fetch_milestones("active"))


def get_all_milestones():
    """All milestones (active + closed) — for the edit form's dropdown."""
    return cache.get_or_set(_ck("milestones:list-all"), lambda: _fetch_milestones(None))


def get_project_info():
    """Project metadata (name, web_url, statistics like commit_count) — cached."""
    return cache.get_or_set(
        _ck("project:info"),
        lambda: _gitlab_request(
            "GET", f"/projects/{_proj()}", params={"statistics": "true"}
        ),
    )


# ── Repo overview: WHAT the project is (not just issue counts) ────────────────
# "fala-me do projeto" needs description + languages + structure + README, not a
# list of issues. Injected once per turn (cached) into the LLM context.


def _get_languages():
    return cache.get_or_set(
        _ck("languages"),
        lambda: _gitlab_request("GET", f"/projects/{_proj()}/languages"),
    )


def _get_repo_tree_top(ref):
    """Top-level files/dirs of the default branch (project structure at a glance)."""
    return cache.get_or_set(
        _ck(f"tree:{ref}"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/tree",
            {"ref": ref, "per_page": 100}, page_limit=1,
        ),
    )


def _find_readme(tree):
    """First root entry that looks like a README file."""
    for e in tree or []:
        if e.get("type") == "blob" and (e.get("name") or "").lower().startswith("readme"):
            return e.get("path") or e.get("name")
    return None


def _get_readme_text(ref, tree):
    """Raw README content (decoded) for the default branch, '' if none. The
    Files API returns base64 JSON, so this goes through the normal JSON path."""
    path = _find_readme(tree)
    if not path:
        return ""

    def _fetch():
        f = _gitlab_request(
            "GET",
            f"/projects/{_proj()}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}",
            params={"ref": ref},
        )
        content = f.get("content") or ""
        if (f.get("encoding") or "").lower() == "base64":
            try:
                return base64.b64decode(content).decode("utf-8", "replace")
            except Exception:
                return ""
        return content

    return cache.get_or_set(_ck(f"readme:{ref}"), _fetch)


def _get_merge_requests():
    return cache.get_or_set(
        _ck("mrs:all"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/merge_requests",
            {"state": "all", "scope": "all"},
        ),
    )


def get_mr_counts():
    """{'opened': n, 'all': n} from GitLab's X-Total header — two 1-item requests
    instead of downloading every MR just to count them. None when the instance
    doesn't send X-Total (e.g. very large projects): callers then fall back to
    the paginated list."""
    def _fetch():
        counts = {}
        for state in ("opened", "all"):
            _, headers = _gitlab_request(
                "GET", f"/projects/{_proj()}/merge_requests",
                params={"state": state, "scope": "all", "per_page": 1},
                return_headers=True)
            n = _int_header(headers, "X-Total")
            if n is None:
                return None
            counts[state] = n
        return counts
    return cache.get_or_set(_ck("mrs:counts"), _fetch)


def get_mr_data():
    """(counts, mrs) for the stats panel / report: GitLab's exact X-Total counts,
    or — when it omits X-Total (above 10 000 rows) — the paginated list. Meant to
    run INSIDE run_parallel, so the fan-out deadline also bounds the fallback."""
    counts = get_mr_counts()
    return counts, (None if counts else _get_merge_requests())


def mr_summary(counts=None, mrs=None):
    """{'open', 'total', 'complete'} for the stats panel / report, from exact
    counts when available, else from the (possibly truncated) list."""
    if counts:
        return {"open": counts["opened"], "total": counts["all"], "complete": True}
    mrs = mrs or []
    total = getattr(mrs, "total", None)
    truncated = bool(getattr(mrs, "truncated", False))
    return {"open": sum(1 for m in mrs if m.get("state") == "opened"),
            "total": total if (truncated and total) else len(mrs),
            "complete": not truncated}


# ── Commits ───────────────────────────────────────────────────────────────────


def _get_commits(days):
    since = (date.today() - timedelta(days=days)).isoformat() + "T00:00:00Z"
    return cache.get_or_set(
        _ck(f"commits:{days}"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/commits",
            {"since": since},
        ),
    )


def _get_last_commit_date():
    """Data (YYYY-MM-DD) do commit mais recente, ou None. Barato: pede 1 commit
    (a API devolve o mais recente primeiro). Serve para distinguir um projeto
    'em fase final' (parado há meses) de um 'concluído' (parado há anos)."""
    try:
        rows = cache.get_or_set(
            _ck("commits:last"),
            lambda: _gitlab_request(
                "GET", f"/projects/{_proj()}/repository/commits",
                params={"per_page": 1},
            ),
        )
    except Exception as e:
        log.warning("last commit date fetch failed: %s", e)
        return None
    if not rows or not isinstance(rows, list):
        return None
    ts = rows[0].get("committed_date") or rows[0].get("created_at") or ""
    return ts[:10] or None


def _get_all_commits(page_limit=50):
    """Full commit history (no date window) for CSV export. A higher page limit
    than the default so big mirrors (e.g. AIR, ~2300 commits) export in full;
    flagged `.truncated` beyond that."""
    return cache.get_or_set(
        _ck("commits:export"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/commits",
            page_limit=page_limit,
        ),
    )


# GitLab's contributors API is computed from (at most) the latest 2000 non-merge
# commits of the default branch, grouped by e-mail — NOT an all-time total.
CONTRIBUTORS_COMMIT_CAP = 2000


def _get_contributors():
    """Commits per author as computed by GitLab (see CONTRIBUTORS_COMMIT_CAP)."""
    return cache.get_or_set(
        _ck("contributors:all"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/contributors",
            {"order_by": "commits", "sort": "desc"},
        ),
    )


def _norm_person(s):
    """Case/accent/space-insensitive form of a person's name."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().split())


def merge_contributors(contribs):
    """GitLab lists one row per e-mail: the same person committing with two
    e-mails appears twice. Merge rows by normalised name, most commits first."""
    merged = {}
    for c in contribs or []:
        if not isinstance(c, dict):
            continue
        key = _norm_person(c.get("name")) or (c.get("email") or "").strip().lower() or "?"
        m = merged.get(key)
        if m is None:
            m = merged[key] = {"name": c.get("name") or "?", "commits": 0,
                               "emails": [], "_best": -1}
        n = int(c.get("commits") or 0)
        m["commits"] += n
        email = (c.get("email") or "").strip().lower()
        if email and email not in m["emails"]:
            m["emails"].append(email)
        if n > m["_best"]:   # display the spelling used on most commits
            m["_best"], m["name"] = n, c.get("name") or m["name"]
    out = sorted(merged.values(), key=lambda x: -x["commits"])
    for m in out:
        m.pop("_best", None)
    return out


def contributors_capped(contribs):
    """True when the contributors data likely hit GitLab's commit cap."""
    return sum(int(c.get("commits") or 0) for c in contribs or []
               if isinstance(c, dict)) >= CONTRIBUTORS_COMMIT_CAP


def _search(scope, query, per_page=100):
    """GitLab project search API (scope = commits | blobs | ...).

    Distingue 'falha' de 'vazio': devolve uma LISTA em sucesso (mesmo vazia) ou
    **None** se a chamada falhou (ex.: 403 em scope=blobs sem Advanced Search).
    Assim quem chama pode dizer 'a pesquisa falhou' em vez de 'não há resultados'
    — honestidade, não invenção. Pede per_page alto (a Search API só dá 20 por
    omissão, o que escondia resultados em históricos grandes)."""
    q = (query or "").strip()
    if not q:
        return []
    try:
        rows = cache.get_or_set(
            _ck(f"search:{scope}:{q.lower()}"),
            lambda: _gitlab_request(
                "GET", f"/projects/{_proj()}/search",
                params={"scope": scope, "search": q, "per_page": per_page},
            ),
        )
        return rows if isinstance(rows, list) else []
    except Exception as e:
        log.warning("search %s '%s' failed: %s", scope, q, e)
        return None  # sentinela de FALHA (≠ [] vazio)


def _search_commits(query, limit=None):
    """Commits cujo título/mensagem contêm o termo (scope=commits). None = falha."""
    rows = _search("commits", query)
    return rows if rows is None or limit is None else rows[:limit]


def _search_blobs(query, limit=None):
    """Ficheiros cujo conteúdo contém o termo (scope=blobs). None = falha."""
    rows = _search("blobs", query)
    return rows if rows is None or limit is None else rows[:limit]


def _resolve_author(query, contribs):
    """Match a free-text author query to ONE person in the contributors rows.

    Exact name/e-mail first, then all query words present in the name, then a
    substring — and a match is only accepted if it names a single person
    (so 'Ana' never silently means 'Mariana'). Returns
    (rows_of_that_person, candidate_names_if_ambiguous)."""
    q = _norm_person(query)
    rows = [c for c in contribs or [] if isinstance(c, dict)]

    def name(c):
        return _norm_person(c.get("name"))

    def email(c):
        return (c.get("email") or "").strip().lower()

    qwords = set(q.split())
    for test in (lambda c: q == name(c) or q == email(c),
                 lambda c: bool(qwords) and qwords <= set(name(c).split()),
                 lambda c: q in name(c) or q in email(c)):
        hits = [c for c in rows if test(c)]
        people = {name(c) or email(c) for c in hits}
        if len(people) == 1:
            person = people.pop()
            same = [c for c in rows if (name(c) or email(c)) == person]
            return same, []
        if len(people) > 1:
            best = merge_contributors(hits)
            return [], [p["name"] for p in best[:6]]
    return [], []


_BRE_SPECIAL = re.compile(r"([.\[\]\\*^$])")


def _author_pattern(name):
    """GitLab passes `author` to `git log --author` (a basic regex)."""
    return _BRE_SPECIAL.sub(r"\\\1", name)


def _identity_matcher(names, emails):
    def match(c):
        return (_norm_person(c.get("author_name")) in names
                or (c.get("author_email") or "").strip().lower() in emails)
    return match


def _query_matcher(query):
    """Fallback when the author isn't in the contributors data: exact e-mail, or
    every word of the query present in the author's name ('Ana' ≠ 'Mariana')."""
    q = _norm_person(query)
    qwords = set(q.split())

    def match(c):
        return ((c.get("author_email") or "").strip().lower() == q
                or (bool(qwords) and qwords <= set(_norm_person(c.get("author_name")).split())))
    return match


def _author_commit_slice(raw_name, match, limit, offset, max_pages=10):
    """Newest-first commits of one person via GitLab's server-side author filter
    (no full-history scan). Returns (commits, partial)."""
    want = offset + limit
    matched, page, partial = [], 1, False
    while len(matched) < want:
        batch, headers = _gitlab_request(
            "GET", f"/projects/{_proj()}/repository/commits",
            params={"author": _author_pattern(raw_name), "per_page": 100, "page": page},
            return_headers=True)
        if not isinstance(batch, list):
            break
        matched.extend(c for c in batch if match(c))
        if len(batch) < 100 or not headers.get("X-Next-Page"):
            break
        if page >= max_pages:
            partial = True
            break
        page += 1
    return matched[offset:offset + limit], partial


def _commits_by_author(name, limit=15, offset=0):
    """Commits de UM autor: TOTAL (API de contribuidores, linhas da mesma pessoa
    somadas) e LISTA newest-first com paginação por `offset`.

    Devolve {"total", "commits", "offset", "parcial", "autores", "total_parcial"} e,
    quando aplicável, "candidatos" (nome ambíguo) ou "erro" (falha do GitLab —
    nunca apresentada como "0 commits")."""
    q = (name or "").strip()
    out = {"total": 0, "commits": [], "offset": offset, "parcial": False,
           "autores": [], "total_parcial": False}
    if not q:
        return out

    try:
        contribs = _get_contributors() or []
    except Exception as e:
        log.warning("contributors (for author) failed: %s", e)
        contribs = None

    rows, candidates = _resolve_author(q, contribs or [])
    if candidates:
        out.update(total=None, candidatos=candidates)
        return out

    if rows:
        person = merge_contributors(rows)[0]
        names = {_norm_person(c.get("name")) for c in rows}
        emails = {(c.get("email") or "").strip().lower() for c in rows} - {""}
        raw_names = sorted({c.get("name") for c in rows if c.get("name")})
        match = _identity_matcher(names, emails)
        identity = "|".join(sorted(names | emails))
        out["total"] = person["commits"]
        out["autores"] = [person["name"]]
        out["total_parcial"] = contributors_capped(contribs)
    else:
        # Not in the contributors data (older than its window, or that call
        # failed): let GitLab filter by the query and keep whole-word matches.
        raw_names, match, identity = [q], _query_matcher(q), f"q:{_norm_person(q)}"
        out["total"] = None

    try:
        if len(raw_names) == 1:
            commits, partial = cache.get_or_set(
                _ck(f"commits:author:{identity}:{offset}:{limit}"),
                lambda: _author_commit_slice(raw_names[0], match, limit, offset))
        else:
            # Same person under several spellings: one filtered query can't cover
            # them all, so filter the (cached) full history instead.
            allc = _get_all_commits()
            mine = [c for c in allc if match(c)]
            commits, partial = mine[offset:offset + limit], bool(allc.truncated)
    except Exception as e:
        log.warning("commits_by_author fetch failed: %s", e)
        out["erro"] = "não consegui obter a lista de commits do GitLab"
        return out

    out["commits"] = commits
    out["parcial"] = partial
    if not rows:
        out["autores"] = sorted({c.get("author_name") or "?" for c in commits})
        if contribs is None:
            out["total_parcial"] = True
        elif not partial and offset == 0 and len(commits) < limit:
            out["total"] = len(commits)
    return out


def _get_file_history(path, n=15):
    """Last commits that touched `path` (newest first). Empty list = path not
    found on the default branch, which doubles as cheap path resolution."""
    return cache.get_or_set(
        _ck(f"fhist:{path}"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/commits",
            {"path": path, "per_page": n}, page_limit=1,
        ),
    )


def _get_blame(path, ref, start, end):
    return cache.get_or_set(
        _ck(f"blame:{ref}:{path}:{start}-{end}"),
        lambda: _gitlab_request(
            "GET",
            f"/projects/{_proj()}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}/blame",
            params={"ref": ref, "range[start]": start, "range[end]": end},
        ),
    )


_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _get_commit_diff(sha):
    """All per-file diffs of a commit (the endpoint is paginated: 20 by default,
    so a large commit's diff for the file we care about could be on page 2+)."""
    sha = str(sha or "")
    if not _SHA_RE.match(sha):
        return []
    return cache.get_or_set(
        _ck(f"cdiff:{sha}"),
        lambda: _gitlab_paginate(
            f"/projects/{_proj()}/repository/commits/{sha}/diff",
            {"per_page": 100}, page_limit=10,
        ),
    )
