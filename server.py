"""
SprintLab TFC Chatbox — cloud edition (Groq + GitLab, corre num HF Space).

Ponto de entrada HTTP (stdlib-only, sem dependências externas): serve o
frontend, expõe a API do chatbox e faz o streaming SSE do chat. A lógica
vive em módulos com responsabilidades claras, em src/:

  src/config.py        configuração env-driven (fail-fast) + prompts
  src/gitlab_api.py    cliente GitLab (cache TTL, multi-tenant, fetchers)
  src/analytics.py     estatísticas, contexto do LLM, exports CSV
  src/report.py        relatório do projeto (determinístico)
  src/charts.py        dados Chart.js para os gráficos inline
  src/blame.py         investigação de código (git blame + diff + análise IA)
  src/code_commit.py   commit por IA (plano → confirmação → branch ai/* + MR)
  src/actions.py       escritas no GitLab (validação + executor único)
  src/read_tools.py    ferramentas de leitura do modelo
  src/llm.py           cliente Groq (default llama-3.3-70b-versatile, env-driven)

Pontos-chave: servidor multi-thread com teto de ligações; abort do stream quando
o cliente desliga; rate-limit por IP nos endpoints que consomem Groq e nas
escritas; escritas com o token do servidor exigem a chave APP_ACCESS_KEY;
CSV com BOM utf-8-sig.
"""

from __future__ import annotations

import hmac
import http.client
import ipaddress
import threading
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler

from src.config import (APP_ACCESS_KEY, CACHE_TTL, DOCUMENT_CONTEXT,
                        GITLAB_ALLOW_PRIVATE, GITLAB_PAGE_LIMIT,
                        GITLAB_PROJECT_ID, GROQ_API_KEY, GROQ_MODEL,
                        GROQ_REASONING_EFFORT, GROQ_UA, GROQ_URL,
                        MAX_BODY_BYTES, MAX_CONCURRENT_PER_IP, MAX_CONNECTIONS,
                        PORT, PUBLIC_URL, RATE_LIMIT, READ_RATE_LIMIT,
                        REQUEST_READ_TIMEOUT, REQUEST_TIMEOUT,
                        SPRINTLAB_PROJECT_ID,
                        SUGGESTIONS_MODEL, SYSTEM_PROMPT, TRUSTED_PROXY_HOPS,
                        WRITE_RATE_LIMIT)
from src.gitlab_api import (BusyError, _ck, _ctx, _get_all_commits, _get_blame,
                            _get_commit_diff, _get_commits, _get_contributors,
                            _get_file_history, _get_languages,
                            _get_last_commit_date, _get_repo_tree_top,
                            _gitlab_paginate, _gitlab_request, _gl,
                            _is_own_instance, _proj, begin_operation, cache,
                            contributors_capped, end_operation, get_all_issues,
                            get_all_milestones, get_milestones, get_mr_data,
                            get_project_info, gitlab_error_message,
                            merge_contributors, mr_summary, resolve_gl_config,
                            run_parallel)
from src.analytics import (_issue_stats, commits_to_csv, get_gitlab_context,
                           issue_extras, issues_to_csv, milestones_by_due)
from src.report import build_sprint_report
from src.charts import CHART_HANDLERS
from src.code_commit import execute_commit_plan, generate_commit_plan
from src.blame import (_analyze_code_llm, _blame_owner, _blame_snippet,
                       _diff_excerpt, _path_candidates)
from src.actions import (ISSUE_TOOLS, _action_summary, _words,
                         execute_issue_tool)
from src.read_tools import READ_TOOLS, READ_TOOL_NAMES, execute_read_tool
from src.llm import (DEFAULT_SUGGESTIONS, FALLBACK_CODES, _fallback_models,
                     _groq_complete,
                     _groq_once, _parse_suggestions, _pick_model, _strip_think,
                     _swap_model)

# Re-exports: os testes unitários (e código antigo) importam tudo via
# `server.<nome>` — manter estes nomes aqui preserva essa interface.
from src.gitlab_api import _find_readme  # noqa: F401
from src.analytics import (_clean_readme, _readme_outline,  # noqa: F401
                           _repo_overview_lines, _strip_html_tags)
from src.report import _due_status  # noqa: F401
from src.actions import (_clean_labels, _int_ids, _norm_iid,  # noqa: F401
                         _valid_due_date)
from src.config import GROQ_MODELS  # noqa: F401

log = logging.getLogger("sprintlab")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _groq_stream_open(req, timeout):
    """Open the streaming Groq request (separate seam so tests can feed a fake
    SSE stream through the real parser without patching urllib globally)."""
    return urllib.request.urlopen(req, timeout=timeout)

# Nomes das ferramentas de ESCRITA (as que o modelo pode chamar) — usados para
# separar, na resposta do modelo, o que se executa já (leitura) do que precisa
# de confirmação (escrita).
WRITE_TOOL_NAMES = {t["function"]["name"] for t in ISSUE_TOOLS}
CONFIRMABLE_TOOLS = {"create_issue", "close_issue", "update_issue", "delete_issue"}
MAX_FILE_PATH = 1024   # chars of the file path accepted by /api/analyze-code


# ── Rate limiting (ops, não auth) ─────────────────────────────────────────────
# A quota grátis do Groq é partilhada por todos os utilizadores do Space; um
# único cliente em loop podia esgotá-la e "matar" a demo para os restantes.
# Janela deslizante de 60s por IP, nos endpoints que consomem Groq; um limitador
# à parte para as escritas no GitLab.


from src.rate_limiter import ConcurrencyLimiter, RateLimiter  # src/rate_limiter.py (re-export)


rate_limiter = RateLimiter(RATE_LIMIT)
write_limiter = RateLimiter(WRITE_RATE_LIMIT)
read_limiter = RateLimiter(READ_RATE_LIMIT)
inflight_limiter = ConcurrencyLimiter(MAX_CONCURRENT_PER_IP)
# Requests that talk to a CALLER-CHOSEN GitLab (X-GL-Base) may hold at most half
# of the connection slots: a slow or hostile instance can't take the whole
# server — the page, /healthz and the server's own GitLab keep capacity.
custom_gitlab_slots = threading.BoundedSemaphore(max(1, MAX_CONNECTIONS // 2))
RATE_MSG = ("Limite de pedidos atingido (proteção da quota grátis do Groq) — "
            "espera um momento e tenta de novo.")
WRITE_RATE_MSG = "Demasiadas escritas seguidas — espera um momento e tenta de novo."
READ_RATE_MSG = "Demasiados pedidos seguidos — espera um momento e tenta de novo."
BUSY_MSG = "Demasiados pedidos em simultâneo — espera que os anteriores terminem."
SERVER_BUSY_MSG = "Servidor ocupado — tenta daqui a pouco."

# Endpoints que fazem chamadas ao Groq — os únicos que vale a pena limitar.
GROQ_ENDPOINTS = {"/api/chat", "/api/suggestions",
                  "/api/generate-description", "/api/analyze-code",
                  "/api/generate-commit"}
# Endpoints que ESCREVEM no GitLab: chave de acesso + limitador próprio.
WRITE_ENDPOINTS = {"/api/confirm-action", "/api/confirm-commit"}
# Endpoints POST que não tocam no GitLab (não validam os headers X-GL-*).
NO_GITLAB_ENDPOINTS = {"/api/suggestions", "/api/generate-description"}
POST_ENDPOINTS = GROQ_ENDPOINTS | WRITE_ENDPOINTS

STATIC_FILES = {
    "/": ("chatbox.html", "text/html; charset=utf-8"),
    "/chatbox.html": ("chatbox.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

FRAME_ANCESTORS = ("frame-ancestors 'self' "
                   "https://teams.microsoft.com "
                   "https://*.teams.microsoft.com "
                   "https://teams.cloud.microsoft "
                   "https://*.cloud.microsoft "
                   "https://*.office.com "
                   "https://outlook.office365.com "
                   "https://*.microsoft365.com "
                   "https://*.microsoft.com "
                   "https://*.skype.com "
                   "https://huggingface.co")   # the Space's own page embeds it

# Política da página: só scripts deste servidor + Chart.js (jsDelivr, com SRI);
# estilos/fontes do Font Awesome (cdnjs). Um HTML injetado não consegue correr
# scripts inline nem enviar dados para outros domínios.
PAGE_CSP = ("default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "font-src 'self' https://cdnjs.cloudflare.com data:; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            + FRAME_ANCESTORS)


# ── HTTP handler ──────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = "SprintLabChatbox/1.2"
    _custom_slot = False     # holds one of custom_gitlab_slots (see _set_ctx)
    _streamed = False        # chat pass 1 already sent a token to the client
    # Timeout por operação de socket: um cliente parado (ex.: Content-Length
    # anunciado sem corpo) não prende a thread para sempre.
    timeout = REQUEST_TIMEOUT

    def log_message(self, fmt, *args):
        # Funnel into the structured logger; default goes to stderr unconditionally.
        log.debug("%s - %s", self.address_string(), fmt % args)

    def handle(self):
        """A client that disconnects mid-request is normal traffic, not an error."""
        try:
            super().handle()
        except ConnectionError as e:
            log.debug("client disconnected: %s", e)
            self.close_connection = True

    def end_headers(self):
        self._headers_flushed = True
        super().end_headers()

    # ---- helpers -------------------------------------------------------------

    def _request_read(self):
        """The whole request has been read: end the server's read deadline."""
        done = getattr(self.server, "request_read", None)
        if done:
            done(self.connection)

    def _drain_body(self, limit=8 * 1024 * 1024, seconds=2.0):
        """Read (and discard) the declared body before answering an error without
        reading it — closing with unread data makes the client see a connection
        reset instead of our 413/429. Bounded in bytes and time."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        left = min(max(0, length), limit)
        deadline = time.monotonic() + seconds
        try:
            while left > 0 and time.monotonic() < deadline:
                self.connection.settimeout(max(0.05, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
        except OSError:
            pass
        finally:
            self.close_connection = True
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass

    def _route(self) -> str:
        """Request path without query string / fragment ('/?inTeams=true' → '/')."""
        return self.path.split("?", 1)[0].split("#", 1)[0] or "/"

    def _query(self) -> dict:
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        return urllib.parse.parse_qs(q.split("#", 1)[0])

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "X-Export-Truncated")
        self.send_header("X-Content-Type-Options", "nosniff")
        # No X-Frame-Options: 'ALLOWALL' is invalid syntax — some browsers
        # treat invalid values as DENY, which blocks the Teams iframe.
        # CSP frame-ancestors is the modern, spec-compliant way.
        self.send_header("Content-Security-Policy", FRAME_ANCESTORS)

    def _json(self, data, status=200, headers=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self._cors()
        self.end_headers()
        self._safe_write(body)

    def _error(self, msg, status=500):
        self._json({"ok": False, "error": str(msg)}, status=status)

    def _not_found(self):
        self.send_response(404)
        self._cors()
        self.end_headers()

    def _safe_write(self, chunk: bytes) -> bool:
        """Return False if the socket is dead — caller should bail out."""
        try:
            self.wfile.write(chunk)
            self.wfile.flush()
            return True
        except OSError:   # BrokenPipe, ConnectionReset, socket timeout
            return False

    def _client_ip(self) -> str:
        """IP do cliente para o rate-limit. Atrás do proxy do HF o IP real vem no
        X-Forwarded-For — mas o CLIENTE controla as entradas à esquerda, por isso
        conta-se a partir da DIREITA (a parte acrescentada pelos proxies de
        confiança), ignorando endereços internos. O header só é considerado se o
        pedido vier de um proxy (endereço não público)."""
        peer = self.client_address[0]
        try:
            if ipaddress.ip_address(peer).is_global:
                return peer   # ligação direta — o X-Forwarded-For é do cliente
        except ValueError:
            return peer
        public = []
        for part in (self.headers.get("X-Forwarded-For") or "").split(","):
            try:
                ip = ipaddress.ip_address(part.strip())
            except ValueError:
                continue
            if ip.is_global:
                public.append(str(ip))
        if not public:
            return peer
        return public[-min(TRUSTED_PROXY_HOPS, len(public))]

    def _rate_limited(self, route):
        """Resposta de limite por endpoint — sempre graciosa, o frontend nunca
        parte: o chat mostra a mensagem, as sugestões caem nos defaults, a
        descrição mostra o aviso e o analyze devolve um erro legível."""
        log.warning("rate limited: %s %s", self._client_ip(), route)
        if route == "/api/chat":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self._cors()
            self.end_headers()
            return self._emit_done(RATE_MSG)
        if route == "/api/suggestions":
            return self._json({"suggestions": DEFAULT_SUGGESTIONS})
        if route == "/api/generate-description":
            return self._json({"description": "", "error": RATE_MSG})
        if route in WRITE_ENDPOINTS:
            return self._json({"ok": False, "error": WRITE_RATE_MSG}, status=429)
        return self._json({"ok": False, "error": RATE_MSG})

    def _set_ctx(self) -> bool:
        """Resolve the per-request GitLab config from the X-GL-* headers (validated;
        see gitlab_api.resolve_gl_config). On a bad config, answers 400 and
        returns False."""
        cfg, err = resolve_gl_config(self.headers.get("X-GL-Base"),
                                     self.headers.get("X-GL-Token"),
                                     self.headers.get("X-GL-Project"))
        if err:
            _ctx.gl = None
            self._json({"ok": False, "error": err, "code": "gitlab_config"}, status=400)
            return False
        if not _is_own_instance(cfg) and not self._custom_slot:
            if not custom_gitlab_slots.acquire(blocking=False):
                _ctx.gl = None
                log.warning("custom-instance slots full (%s)", self._client_ip())
                self._json({"ok": False, "error": SERVER_BUSY_MSG}, status=503,
                           headers={"Retry-After": "5"})
                return False
            self._custom_slot = True
        _ctx.gl = cfg
        _ctx.client = self._client_ip()
        begin_operation()                 # total GitLab time budget of this request
        return True

    def _write_auth_error(self):
        """Writes with the SERVER's GitLab token need the access key; a caller
        using its own token (custom workspace) is authorised by GitLab itself.
        Returns None or (code, message)."""
        if not _gl().get("server_token"):
            return None
        if not APP_ACCESS_KEY:
            return ("writes_disabled",
                    "As alterações no GitLab predefinido estão desativadas neste "
                    "servidor (falta configurar o secret APP_ACCESS_KEY). Ler, "
                    "conversar, gráficos e relatórios continuam a funcionar.")
        supplied = (self.headers.get("X-App-Key") or "").strip()
        if not supplied or not hmac.compare_digest(supplied.encode(),
                                                   APP_ACCESS_KEY.encode()):
            return ("access_key",
                    "Chave de acesso em falta ou inválida — introduz a chave em "
                    "⚙️ Definições para poderes alterar o GitLab (se não vires esse "
                    "campo, recarrega a página).")
        return None

    def _read_body(self):
        """Request body bytes, or None after answering 400/413."""
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw) if raw is not None else 0
        except ValueError:
            length = -1
        if length < 0:
            self._request_read()
            self._error("Content-Length inválido.", status=400)
            return None
        if length > MAX_BODY_BYTES:
            self._drain_body()
            self._request_read()
            self._error("Pedido demasiado grande.", status=413)
            return None
        body = self.rfile.read(length) if length else b""
        self._request_read()
        return body

    @staticmethod
    def _json_obj(body):
        """Parse a JSON object body → dict, or None if malformed / not an object."""
        try:
            data = json.loads(body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # ---- verbs ---------------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _guarded(self, dispatch):
        """Last-resort safety net: an unexpected error becomes a 500 JSON (never an
        empty reply), logged once. Client disconnects are left to handle()."""
        try:
            return dispatch()
        except ConnectionError:
            raise
        except Exception:
            log.exception("unhandled error on %s %s", self.command, self._route())
            if not getattr(self, "_headers_flushed", False):
                self._headers_buffer = []      # drop a half-built response
                self._error("Erro interno do servidor.", 500)

    def _limited(self, dispatch):
        """Run a dynamic request with at most MAX_CONCURRENT_PER_IP in flight per
        client — one client can't tie up every connection slot."""
        ip = self._client_ip()
        if not inflight_limiter.acquire(ip):
            log.warning("too many concurrent requests from %s", ip)
            if self.command == "POST":
                self._drain_body()
            self._request_read()
            return self._json({"ok": False, "error": BUSY_MSG}, status=429)
        self._custom_slot = False
        try:
            return self._guarded(dispatch)
        finally:
            end_operation()
            _ctx.gl = None
            if self._custom_slot:
                self._custom_slot = False
                custom_gitlab_slots.release()
            inflight_limiter.release(ip)

    def do_GET(self):
        self._request_read()                  # a GET has no body: fully read
        route = self._route()
        # Static files, health and config are cheap and never call GitLab/Groq:
        # not counted per IP (many users behind one NAT must still get the page).
        if route in STATIC_FILES or route in ("/healthz", "/api/config"):
            return self._guarded(self._dispatch_get)
        return self._limited(self._dispatch_get)

    def do_POST(self):
        return self._limited(self._dispatch_post)

    def _dispatch_get(self):
        route = self._route()
        static = STATIC_FILES.get(route)
        if static:
            return self._serve_static(*static)
        if route == "/healthz":
            return self._json({"ok": True, "ts": int(time.time())})
        if route == "/api/config":
            return self._handle_config()
        if not route.startswith("/gitlab/"):
            return self._not_found()
        if not read_limiter.allow(self._client_ip()):
            log.warning("read rate limited: %s %s", self._client_ip(), route)
            return self._json({"ok": False, "error": READ_RATE_MSG}, status=429)
        if not self._set_ctx():
            return
        if route == "/gitlab/export":
            return self._handle_export()
        if route.startswith("/gitlab/chart/"):
            return self._handle_chart()
        if route == "/gitlab/labels":
            return self._handle_labels()
        if route == "/gitlab/milestones":
            return self._handle_milestones()
        if route == "/gitlab/members":
            return self._handle_members()
        if route == "/gitlab/duplicate":
            return self._handle_duplicate()
        if route.startswith("/gitlab/issue/"):
            return self._handle_issue_get()
        if route == "/gitlab/test":
            return self._handle_gl_test()
        if route == "/gitlab/stats":
            return self._handle_stats()
        if route == "/gitlab/report":
            return self._handle_report()
        self._not_found()

    def _dispatch_post(self):
        route = self._route()
        if route not in POST_ENDPOINTS:
            self._drain_body()
            self._request_read()
            return self._not_found()
        body = self._read_body()
        if body is None:
            return

        # Quota Groq partilhada: limitar por IP os endpoints que chamam o LLM;
        # e as escritas no GitLab com um limitador à parte.
        if route in GROQ_ENDPOINTS and not rate_limiter.allow(self._client_ip()):
            return self._rate_limited(route)
        if route in WRITE_ENDPOINTS and not write_limiter.allow(self._client_ip()):
            return self._rate_limited(route)

        if route not in NO_GITLAB_ENDPOINTS and not self._set_ctx():
            return
        if route in WRITE_ENDPOINTS:
            refused = self._write_auth_error()
            if refused:
                code, err = refused
                log.warning("write refused (%s, %s) from %s", route, code, self._client_ip())
                return self._json({"ok": False, "error": err, "code": code}, status=403)

        # NOTE: all issue writes go through /api/confirm-action -> execute_issue_tool
        # (the single validated path). The old /gitlab/issues[/.../close|update]
        # routes were unvalidated and are removed.
        if route == "/api/chat":
            return self._handle_chat(body)
        if route == "/api/confirm-action":
            return self._handle_confirm_action(body)
        if route == "/api/generate-description":
            return self._handle_generate_description(body)
        if route == "/api/analyze-code":
            return self._handle_analyze_code(body)
        if route == "/api/generate-commit":
            return self._handle_generate_commit(body)
        if route == "/api/confirm-commit":
            return self._handle_confirm_commit(body)
        if route == "/api/suggestions":
            return self._handle_suggestions(body)
        self._not_found()

    # ---- route impls ---------------------------------------------------------

    def _serve_static(self, fname, ctype):
        try:
            # Resolved next to server.py, not the process CWD.
            with open(os.path.join(_BASE_DIR, fname), "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self.send_response(404)
            self._cors()
            self.end_headers()
            self._safe_write(b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        # Don't let the browser serve a stale chatbox.html/style.css/app.js —
        # the assets change often during development.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         PAGE_CSP if ctype.startswith("text/html") else FRAME_ANCESTORS)
        self.end_headers()
        self._safe_write(content)

    def _handle_config(self):
        """What the UI needs to know about this server: the default model (used
        when the user hasn't picked one) and whether server-token writes need
        the access key or are disabled."""
        self._json({
            "default_model": GROQ_MODEL,
            "models": list(GROQ_MODELS),
            "writes": "key" if APP_ACCESS_KEY else "disabled",
            # http:// e redes internas permitidas (só em desenvolvimento local)
            "allow_private_gitlab": GITLAB_ALLOW_PRIVATE,
        })

    def _handle_export(self):
        params = self._query()
        kind = params.get("type", ["issues"])[0]
        try:
            if kind == "commits":
                rows = _get_all_commits()
                data = commits_to_csv(rows)
                fname = "gitlab_commits.csv"
                log.info("export csv type=commits rows=%d", len(rows))
            else:
                state = params.get("state", ["all"])[0]
                if state not in ("all", "opened", "closed"):
                    state = "all"
                rows = get_all_issues(state)
                data = issues_to_csv(rows)
                fname = "gitlab_issues.csv"
                log.info("export csv state=%s rows=%d", state, len(rows))
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            if getattr(rows, "truncated", False):
                self.send_header("X-Export-Truncated", str(len(rows)))
            self._cors()
            self.end_headers()
            self._safe_write(data)
        except Exception as e:
            log.exception("export failed")
            self._error(gitlab_error_message(e), status=502)

    def _handle_chart(self):
        name = self._route()[len("/gitlab/chart/"):]
        handler = CHART_HANDLERS.get(name)
        if not handler:
            return self._error("gráfico desconhecido", status=404)
        kwargs = {k: v[0] for k, v in self._query().items()}
        try:
            data = handler(**kwargs)
            log.info("chart %s rendered", name)
            self._json(data)
        except Exception as e:
            log.exception("chart %s failed", name)
            self._error(gitlab_error_message(e), status=502)

    def _handle_labels(self):
        try:
            labels = cache.get_or_set(
                _ck("labels:all"),
                lambda: _gitlab_paginate(
                    f"/projects/{_proj()}/labels", {"per_page": 100}
                ),
            )
            out = [{"name": l.get("name"), "color": l.get("color")}
                   for l in labels if l.get("name")]
            self._json({"labels": out})
        except Exception as e:
            log.warning("labels failed: %s", e)
            self._json({"labels": [], "error": gitlab_error_message(e)})

    def _handle_milestones(self):
        # ALL milestones (active + closed, project + parent groups) — so the edit
        # form's dropdown always contains the issue's current milestone and can't
        # fall back to "" (which would silently unassign it on save).
        try:
            ms = get_all_milestones()
            out = [{"id": m.get("id"), "title": m.get("title"),
                    "due_date": m.get("due_date")} for m in ms if m.get("id")]
            self._json({"milestones": out})
        except Exception as e:
            log.warning("milestones failed: %s", e)
            self._json({"milestones": [], "error": gitlab_error_message(e)})

    def _handle_stats(self):
        """Project stats for the right-hand panel: issues + commits + milestones
        + MRs. Each section is fetched in parallel (with a total deadline) and
        tolerated independently — a project with no MRs/commits still gets its
        issue stats. If NOTHING could be read, it's an error, not empty stats."""
        r = run_parallel({
            "info": (get_project_info,),
            "issues": (get_all_issues, "all"),
            "commits": (_get_commits, 90),
            "contributors": (_get_contributors,),
            "milestones": (get_milestones,),
            "mrs": (get_mr_data,),
        })
        failed = [k for k, v in r.items() if isinstance(v, Exception)]
        for k in failed:
            log.warning("stats section failed (%s): %s", k, r[k])
        if len(failed) == len(r):
            return self._all_sections_failed(r["info"], r.values())

        def val(k):
            return None if isinstance(r[k], Exception) else r[k]

        info, issues, commits = val("info"), val("issues"), val("commits")
        contribs, ms, mrs = val("contributors"), val("milestones"), val("mrs")
        out = {"project": None, "issues": None, "commits": None,
               "contributors": None, "milestones": None, "mrs": None,
               "unavailable": failed}
        if info:
            out["project"] = {
                "name": info.get("name_with_namespace") or info.get("name") or "?",
                "web_url": info.get("web_url"),
                "commit_count": (info.get("statistics") or {}).get("commit_count"),
            }
        if issues is not None:
            counts, opened = issue_extras(issues)
            s = _issue_stats(issues, counts=counts, opened=opened)
            out["issues"] = {
                "open": s["open_count"], "closed": s["closed_count"],
                "total": s["total"], "progress": s["progress"],
                "overdue": len(s["overdue"]), "no_assignee": s["no_assignee"],
                "exact": s["exact"], "open_complete": s["open_complete"],
            }
        if commits is not None:
            by = Counter((c.get("author_name") or "?") for c in commits)
            out["commits"] = {
                "last90d": len(commits), "authors": len(by),
                "top": [{"name": n, "count": c} for n, c in by.most_common(5)],
                "complete": not getattr(commits, "truncated", False),
            }
        if contribs is not None:
            merged = merge_contributors(contribs)
            out["contributors"] = {
                "authors": len(merged),
                "total": sum(c["commits"] for c in merged),
                "top": [{"name": c["name"], "commits": c["commits"]} for c in merged[:5]],
                "capped": contributors_capped(contribs),
                "complete": not getattr(contribs, "truncated", False),
            }
        if ms is not None:
            out["milestones"] = [
                {"title": m.get("title"), "due_date": m.get("due_date")}
                for m in milestones_by_due(ms)[:5]
            ]
        if mrs is not None:
            out["mrs"] = mr_summary(*mrs)
        self._json(out)

    def _all_sections_failed(self, main_error, errors):
        """Nothing could be read: 503 when it was only the server being busy
        (retry), else 502 with the GitLab error."""
        if all(isinstance(e, BusyError) for e in errors):
            return self._json({"ok": False, "error": SERVER_BUSY_MSG}, status=503,
                              headers={"Retry-After": "5"})
        return self._json({"ok": False,
                           "error": gitlab_error_message(main_error)},
                          status=502)

    def _handle_report(self):
        """Sprint report ('Relatórios automáticos' feature). Gathers the same
        data as /gitlab/stats — in parallel, each section tolerated — then hands
        it to the pure build_sprint_report(). Returns {..., markdown}. Without the
        project itself there is no report: an error, never a fake 'empty' one."""
        r = run_parallel({
            "info": (get_project_info,),
            "issues": (get_all_issues, "all"),
            "commits": (_get_commits, 90),
            "contributors": (_get_contributors,),
            "milestones": (get_milestones,),
            "mrs": (get_mr_data,),
            "languages": (_get_languages,),          # secção "Sobre"
            "last_commit": (_get_last_commit_date,),  # ativo/fase final/concluído
        })

        def val(k):
            return None if isinstance(r[k], Exception) else r[k]

        for k, v in r.items():
            if isinstance(v, Exception):
                log.warning("report section failed (%s): %s", k, v)
        info = val("info")
        if not info:
            if isinstance(r["info"], BusyError):
                return self._all_sections_failed(r["info"], [r["info"]])
            return self._json({
                "ok": False,
                "error": "Não consegui ler o projeto no GitLab — "
                         + gitlab_error_message(r["info"]
                                                if isinstance(r["info"], Exception)
                                                else ValueError()),
            }, status=502)
        ref = info.get("default_branch") or "main"
        try:
            tree = _get_repo_tree_top(ref)                # estrutura p/ "Sobre"
        except Exception as e:
            log.warning("report tree failed: %s", e)
            tree = None
        issues = val("issues")
        counts, opened = issue_extras(issues) if issues is not None else (None, None)
        unavailable = [k for k in ("issues", "commits", "contributors",
                                   "milestones", "mrs")
                       if isinstance(r[k], Exception)]
        mr_counts, mrs = val("mrs") or (None, None)
        try:
            report = build_sprint_report(
                info, issues, val("commits"), val("contributors"),
                val("milestones"), mrs,
                languages=val("languages"), tree=tree,
                last_commit_date=val("last_commit"),
                unavailable=unavailable, issue_counts=counts, opened_issues=opened,
                mr_counts=mr_counts,
            )
        except Exception:
            log.exception("report build failed")
            return self._error("Não foi possível gerar o relatório.")
        self._json(report)

    def _handle_gl_test(self):
        """Validate the current GitLab config (URL/token/project) for the
        settings panel's 'Test connection' button."""
        try:
            p = _gitlab_request("GET", f"/projects/{_proj()}")
            self._json({
                "ok": True,
                "name": p.get("name_with_namespace") or p.get("name") or "?",
                "open_issues": p.get("open_issues_count"),
            })
        except urllib.error.HTTPError as e:
            msg = {401: "Token inválido ou sem permissão.",
                   404: "Projeto não encontrado (verifica o Project ID, o URL e o token).",
                   }.get(e.code) or gitlab_error_message(e)
            self._json({"ok": False, "error": msg})
        except Exception as e:
            log.warning("gitlab test failed: %s", e)
            self._json({"ok": False, "error": gitlab_error_message(e)})

    def _handle_members(self):
        try:
            members = cache.get_or_set(
                _ck("members:all"),
                lambda: _gitlab_paginate(
                    f"/projects/{_proj()}/members/all", {"per_page": 100}
                ),
            )
            out = [{"id": m.get("id"), "name": m.get("name")}
                   for m in members if m.get("id")]
            self._json({"members": out})
        except Exception as e:
            log.warning("members failed: %s", e)
            self._json({"members": [], "error": gitlab_error_message(e)})

    def _handle_issue_get(self):
        iid = self._route().rstrip("/").split("/")[-1]
        if not iid.isdigit():
            return self._error("Número de issue inválido.", status=400)
        try:
            i = _gitlab_request("GET", f"/projects/{_proj()}/issues/{int(iid)}")
        except urllib.error.HTTPError as e:
            log.warning("issue get failed: %s", e)
            return self._error(gitlab_error_message(e),
                               status=404 if e.code == 404 else 502)
        except Exception as e:
            log.warning("issue get failed: %s", e)
            return self._error(gitlab_error_message(e), status=502)
        assignees = [a for a in (i.get("assignees") or []) if isinstance(a, dict)]
        try:
            iid_out = int(i.get("iid"))
        except (TypeError, ValueError):
            iid_out = int(iid)
        self._json({
            "iid": iid_out,
            "title": i.get("title") or "",
            "description": i.get("description") or "",
            "labels": i.get("labels") or [],
            "milestone_id": (i.get("milestone") or {}).get("id") or "",
            "milestone_title": (i.get("milestone") or {}).get("title") or "",
            "due_date": i.get("due_date") or "",
            "assignee_id": assignees[0].get("id") if assignees else "",
            "assignee_name": assignees[0].get("name") if assignees else "",
            # Todos os assignees: o formulário só pode enviar assignee_ids quando o
            # utilizador os muda — senão apagava os restantes (GitLab Premium).
            "assignee_ids": [a.get("id") for a in assignees if a.get("id") is not None],
            "assignee_names": [a.get("name") or "?" for a in assignees],
            "confidential": bool(i.get("confidential")),
            "web_url": i.get("web_url"),
        })

    def _handle_duplicate(self):
        params = self._query()
        title = (params.get("title", [""])[0]).strip().lower()
        if len(title) < 3:
            return self._json({"matches": []})
        try:
            opened = [i for i in get_all_issues("all") if i.get("state") == "opened"]
            qtoks = {w for w in _words(title) if len(w) >= 3}
            scored = []
            for i in opened:
                t = (i.get("title") or "").strip().lower()
                if not t:
                    continue   # an empty title would "contain" every query
                ttoks = {w for w in _words(t) if len(w) >= 3}
                shared = len(qtoks & ttoks)
                if (qtoks and (title in t or t in title
                               or shared >= max(2, len(qtoks) // 2))):
                    scored.append((shared, i))
            scored.sort(key=lambda x: -x[0])
            out = []
            for _, i in scored[:3]:
                try:
                    out.append({"iid": int(i.get("iid")), "title": i.get("title")})
                except (TypeError, ValueError):
                    continue
            self._json({"matches": out})
        except Exception as e:
            log.warning("duplicate check failed: %s", e)
            self._json({"matches": []})

    def _handle_analyze_code(self, body):
        """Code investigation: which commit last changed a line, by whom — and
        an AI hypothesis of the bug. Facts come from GitLab (blame + diff,
        deterministic); the LLM is called once, at the end, only to explain."""
        data = self._json_obj(body)
        if data is None:
            return self._json({"ok": False, "error": "Pedido inválido."})
        file_raw = str(data.get("file") or "").strip()
        if not file_raw:
            return self._json({"ok": False, "error": "Indica o ficheiro a investigar."})
        if len(file_raw) > MAX_FILE_PATH:
            return self._json({"ok": False, "error": "Caminho do ficheiro demasiado longo."})
        try:
            line = int(data.get("line")) if data.get("line") else None
        except (TypeError, ValueError):
            line = None
        if line is not None and line < 1:
            line = None
        question = str(data.get("question") or "")[:1200]

        try:
            info = get_project_info() or {}
        except Exception:
            info = {}
        ref = info.get("default_branch") or "main"
        proj_url = info.get("web_url") or ""

        # Resolve the repo path: pasted paths are often absolute (IDE/traceback),
        # so try progressively shorter suffixes until one has commit history.
        path, hist = None, []
        for cand in _path_candidates(file_raw):
            try:
                h = _get_file_history(cand)
            except Exception as e:
                log.warning("file history failed for %s: %s", cand, e)
                continue
            if h:
                path, hist = cand, h
                break
        if not path:
            return self._json({
                "ok": False,
                "error": f"Não encontrei «{file_raw[:200]}» no repositório (ref {ref}). "
                         "Usa o caminho relativo à raiz do projeto.",
            })

        def _c(c):
            sha = c.get("id") or ""
            return {
                "short_sha": (c.get("short_id") or sha)[:8],
                "author": c.get("author_name") or "?",
                "date": (c.get("authored_date") or c.get("created_at") or "")[:10],
                "title": (c.get("title") or c.get("message") or "").strip()[:100],
                "commit_url": f"{proj_url}/-/commit/{sha}" if proj_url and sha else "",
            }

        authors = Counter((c.get("author_name") or "?") for c in hist)
        out = {
            "ok": True, "file": path, "line": line, "ref": ref,
            "facts": None, "line_text": "",
            "history": [_c(c) for c in hist[:8]],
            "authors": [{"name": n, "count": k} for n, k in authors.most_common(5)],
            "analysis": "", "note": "",
        }

        if line:
            start, end = max(1, line - 20), line + 20
            blame = None
            try:
                blame = _get_blame(path, ref, start, end)
            except Exception as e:
                log.warning("blame failed for %s:%d: %s", path, line, e)
            if not isinstance(blame, list):
                # the call failed (an EMPTY list is a success: range past the end)
                out["note"] = ("Não consegui obter o git blame desta linha — "
                               "mostro só o histórico do ficheiro.")
            else:
                commit, line_text = _blame_owner(blame, start, line)
                if commit is None:
                    out["note"] = (f"A linha {line} parece estar além do fim do "
                                   f"ficheiro em {ref} — mostro só o histórico.")
                else:
                    sha = commit.get("id") or ""
                    out["facts"] = {
                        "sha": sha, "short_sha": sha[:8],
                        "author": commit.get("author_name") or "?",
                        "date": (commit.get("authored_date") or "")[:10],
                        "message": (commit.get("message") or "").strip()[:200],
                        "commit_url": (f"{proj_url}/-/commit/{sha}"
                                       if proj_url and sha else ""),
                    }
                    out["line_text"] = line_text
                    diff_txt = ""
                    try:
                        diff_txt = _diff_excerpt(_get_commit_diff(sha), path)
                    except Exception as e:
                        log.warning("commit diff failed for %s: %s", sha[:8], e)
                    out["analysis"] = _analyze_code_llm(
                        path, ref, line, line_text, _blame_snippet(blame, start, line),
                        out["facts"], diff_txt, question, data.get("model"),
                    )
        log.info("analyze-code %s:%s facts=%s analysis=%s",
                 path, line, bool(out["facts"]), bool(out["analysis"]))
        self._json(out)

    def _handle_generate_description(self, body):
        data = self._json_obj(body)
        if data is None:
            return self._json({"description": "", "error": "Pedido inválido."})
        try:
            title = str(data.get("title") or "").strip()[:300]
            if not title:
                return self._json({"description": ""})
            template = str(data.get("template") or "").lower()
            hint = {
                "bug": " Estrutura como relatório de bug: o que acontece, passos para "
                       "reproduzir e resultado esperado.",
                "task": " Estrutura como tarefa: objetivo e critérios de aceitação.",
            }.get(template, "")
            prompt = (
                f"Escreve uma descrição curta e clara (2 a 4 frases) em português de "
                f"Portugal para uma issue de GitLab com o título «{title}».{hint} "
                f"Responde apenas com a descrição — sem título, sem aspas, sem cabeçalhos."
            )
            gmodel, greff = _pick_model(data.get("model"))
            payload = {
                "model": gmodel,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 220,
            }
            if greff:
                payload["reasoning_effort"] = greff
            resp = _groq_complete(payload)
            desc = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
            desc = _strip_think(desc)   # remove <think>… de modelos de raciocínio
            if not desc:
                return self._json({"description": "",
                                   "error": "O modelo não devolveu nenhuma descrição."})
            self._json({"description": desc})
        except Exception as e:
            log.warning("generate description failed: %s", e)
            self._json({"description": "",
                        "error": "Não foi possível gerar a descrição — tenta de novo."})

    def _handle_chat(self, body):
        # Validate BEFORE the SSE headers: a malformed request gets a proper 400
        # JSON, never a stream without an HTTP status line.
        data = self._json_obj(body)
        if data is None or not isinstance(data.get("messages", []) or [], list):
            return self._json({"ok": False, "error": "Pedido inválido."}, status=400)
        messages = data.get("messages") or []
        # Só mensagens user/assistant com texto (um cliente não pode injetar
        # mensagens 'system'/'tool'); histórico e tamanho limitados (quota Groq).
        safe_messages = [
            {"role": m["role"], "content": m["content"][:12000]}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ][-30:]
        model, reff = _pick_model(data.get("model"))   # UI model switcher
        last_user = next((m["content"] for m in reversed(safe_messages)
                          if m["role"] == "user"), "")
        log.info("chat: %s  [%s]", last_user[:70], model)

        headers_sent = False
        try:
            # Always give the model the live GitLab data so it can answer about
            # issues / tarefas / sprint / progresso regardless of phrasing.
            # The TTL cache means repeated questions don't re-fetch.
            gitlab_ctx = get_gitlab_context()
            # SprintLab knowledge only applies to SprintLab; for any other
            # project, drop the static doc so the model adapts to that project.
            doc = DOCUMENT_CONTEXT if _gl()["project"] == SPRINTLAB_PROJECT_ID else ""
            system_content = SYSTEM_PROMPT
            if doc:
                system_content += "\n\n" + doc
            system_content += "\n" + gitlab_ctx
            convo = [{"role": "system", "content": system_content}, *safe_messages]

            # Start the SSE response; every outcome from here on is an event.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.end_headers()
            headers_sent = True

            # ── Pass 1: STREAM the answer live; if the model calls a tool
            #    instead, accumulate the call(s) and act on them below. ─────────
            first_payload = {
                "model": model,
                "messages": convo,
                "tools": ISSUE_TOOLS + READ_TOOLS,
                "tool_choice": "auto",
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 1024,
            }
            if reff:
                first_payload["reasoning_effort"] = reff

            self._streamed = False    # set by _stream_pass1 once a token was sent
            try:
                tool_calls = self._stream_with_fallback(first_payload)
            except urllib.error.HTTPError as e:
                return self._emit_done(self._groq_error_msg(e))
            except urllib.error.URLError as e:
                log.warning("groq connection failed: %s", e)
                return self._emit_done("Erro de ligação ao Groq — tenta de novo.")
            except (OSError, http.client.HTTPException) as e:
                # Groq dropped/reset the connection (urllib doesn't wrap errors
                # from getresponse() or from reading the stream). Every write to
                # the CLIENT in the stream goes through _safe_write, so this is
                # never the client going away.
                log.warning("groq connection dropped: %r", e)
                if self._streamed:    # part of the answer is already on screen
                    return self._emit_done(
                        " …_(resposta interrompida — erro de ligação ao Groq)_")
                return self._emit_done("Erro de ligação ao Groq — tenta de novo.")

            if not tool_calls:
                # Plain answer — already streamed token-by-token + done sent.
                log.info("chat done (no tool)")
                return

            # O modelo pode devolver VÁRIOS tool_calls — separá-los por tipo (não
            # assumir tool_calls[0]), senão uma escrita misturada com leituras
            # perdia-se silenciosamente.
            def _tname(tc):
                return (tc.get("function") or {}).get("name", "")
            read_calls = [tc for tc in tool_calls if _tname(tc) in READ_TOOL_NAMES]
            write_calls = [tc for tc in tool_calls if _tname(tc) in WRITE_TOOL_NAMES]

            # ── Só leituras → executar já (não alteram nada) e responder numa
            #    2.ª passagem com os dados. Substitui o antigo "não tenho acesso".
            if read_calls and not write_calls:
                begin_operation()     # fresh GitLab time budget for the tool reads
                return self._run_read_tools(read_calls, convo, model, reff)

            # ── Há uma ação de escrita → propor (uma de cada vez; o utilizador
            #    confirma no cartão; a execução é só em /api/confirm-action).
            if write_calls:
                tc = write_calls[0]
                fn = _tname(tc)
                try:
                    args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                action = {"tool": fn, "args": args, "summary": _action_summary(fn, args)}
                log.info("proposing action for confirmation: %s(%s)", fn, args)
                self._safe_write(
                    f"data: {json.dumps({'action': action, 'done': True}, ensure_ascii=False)}\n\n".encode()
                )
                return

            # Tool(s) não reconhecida(s) — não deixar o pedido pendurado.
            log.warning("tool calls não reconhecidas: %s", [_tname(t) for t in tool_calls])
            self._emit_done("Não consegui processar esse pedido. Reformula, por favor.")

        except ConnectionError:
            log.debug("chat client disconnected")
            self.close_connection = True
        except Exception:
            log.exception("chat handler failed")
            if headers_sent:
                self._emit_done("Ocorreu um erro inesperado — tenta de novo.")
            else:
                self._error("Ocorreu um erro inesperado — tenta de novo.")

    def _run_read_tools(self, tool_calls, convo, model, reff):
        """Executa as ferramentas de leitura pedidas e faz a 2.ª passagem: o
        modelo recebe os resultados e gera a resposta final (em streaming)."""
        # Mensagem do assistente com os tool_calls (formato exigido pela API).
        convo.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tc in tool_calls:
            f = tc.get("function") or {}
            name = f.get("name", "")
            try:
                args = json.loads(f.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            result = execute_read_tool(name, args) if name in READ_TOOL_NAMES else {"erro": "desconhecida"}
            log.info("read tool %s(%s) -> %s itens", name, args,
                     result.get("encontrados", result.get("total", "?")))
            convo.append({"role": "tool", "tool_call_id": tc.get("id"),
                          "content": json.dumps(result, ensure_ascii=False)})
        # Limpa o bubble: se o modelo "pensou em voz alta" antes do tool_call na
        # 1.ª passagem, esse texto já foi para o cliente — descartá-lo antes de a
        # 2.ª passagem escrever a resposta real (evita texto concatenado).
        self._safe_write(b'data: {"reset": true}\n\n')
        # 2.ª passagem SEM ferramentas → o modelo só responde com os dados.
        pass2 = {"model": model, "messages": convo, "stream": True,
                 "temperature": 0.3, "max_tokens": 1024}
        if reff:
            pass2["reasoning_effort"] = reff
        self._streamed = False   # the reset above cleared what pass 1 showed
        try:
            self._stream_with_fallback(pass2)   # sem 'tools' → faz stream + 'done'
        except urllib.error.HTTPError as e:
            self._emit_done(self._groq_error_msg(e))
        except Exception as e:   # URLError, queda de ligação a meio, etc.
            log.warning("pass-2 stream falhou: %s", e)
            self._emit_done(" …_(resposta interrompida)_" if self._streamed
                            else "Erro de ligação ao Groq — tenta de novo.")

    def _stream_with_fallback(self, payload):
        """`_stream_pass1` com FALLBACK em 429/404/413, iterando a cadeia de modelos.
        O 429/404 é lançado antes de qualquer token (no urlopen, ou no chunk de
        erro só quando NADA foi ainda enviado — ver _stream_pass1), por isso uma
        nova tentativa com outro modelo é segura (sem conteúdo duplicado)."""
        try:
            return self._stream_pass1(payload)
        except urllib.error.HTTPError as e:
            if e.code not in FALLBACK_CODES:
                raise
            last = e
            for fb in _fallback_models(payload.get("model")):
                try:
                    log.warning("groq %s em '%s' (stream) → fallback '%s'",
                                last.code, payload.get("model"), fb)
                    return self._stream_pass1(_swap_model(payload, fb))
                except urllib.error.HTTPError as e2:
                    if e2.code not in FALLBACK_CODES:
                        raise
                    last = e2
            raise last  # toda a cadeia falhou

    # ---- chat helpers --------------------------------------------------------

    @staticmethod
    def _groq_error_msg(e) -> str:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        log.warning("groq HTTP %s: %s", getattr(e, "code", "?"), detail)
        if getattr(e, "code", None) == 429:
            return "Limite de pedidos atingido — espera um momento e tenta de novo."
        return f"Erro Groq ({getattr(e, 'code', '?')})."

    def _emit_done(self, text: str):
        """Send a single terminal event (used for errors)."""
        self._safe_write(
            f"data: {json.dumps({'content': text, 'done': True})}\n\n".encode()
        )

    def _stream_pass1(self, payload: dict):
        """Stream pass-1: forward content deltas to the client as they arrive
        (smooth token-by-token typing) while accumulating any tool_call deltas.

        Returns a list of tool_call dicts if the model chose to act (caller then
        executes them and runs pass-2). Returns None for a normal answer — in
        that case the text was already streamed and a 'done' event was sent.
        """
        req = urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "User-Agent": GROQ_UA,
            },
            method="POST",
        )
        tool_acc = {}   # index -> {"id", "name", "args"}
        sent_content = False   # já enviámos algum token ao cliente?
        t0 = time.time()
        with _groq_stream_open(req, timeout=120) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                # O Groq pode responder HTTP 200 e enviar o erro (ex.: quota de
                # tokens esgotada a meio) como um chunk {"error": ...}. Se ainda
                # nada foi enviado, lança 429 para acionar o fallback de modelo;
                # se já houve tokens, termina com nota (retry duplicaria texto).
                if chunk.get("error"):
                    log.warning("groq stream error chunk: %s",
                                str(chunk.get("error"))[:200])
                    if not sent_content and not tool_acc:
                        r.close()
                        raise urllib.error.HTTPError(
                            GROQ_URL, 429, "stream error", {}, None)
                    self._safe_write(
                        f"data: {json.dumps({'content': ' …_(resposta interrompida — limite atingido)_', 'done': True})}\n\n".encode()
                    )
                    return None
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    sent_content = self._streamed = True
                    if not self._safe_write(
                        f"data: {json.dumps({'content': content, 'done': False})}\n\n".encode()
                    ):
                        r.close()
                        return None  # client gone
                for tcd in (delta.get("tool_calls") or []):
                    slot = tool_acc.setdefault(
                        tcd.get("index", 0), {"id": None, "name": "", "args": ""}
                    )
                    if tcd.get("id"):
                        slot["id"] = tcd["id"]
                    fn = tcd.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]

        if tool_acc:
            return [
                {"id": tool_acc[i]["id"], "type": "function",
                 "function": {"name": tool_acc[i]["name"],
                              "arguments": tool_acc[i]["args"]}}
                for i in sorted(tool_acc)
            ]
        # Normal answer fully streamed — finalise.
        self._safe_write(
            f"data: {json.dumps({'content': '', 'done': True, 'duration': int((time.time()-t0)*1000)})}\n\n".encode()
        )
        return None

    def _handle_generate_commit(self, body):
        """Commit por IA (1/2): gera o PLANO (ficheiros + mensagem) com uma
        chamada Groq. NADA é escrito no GitLab neste passo — o utilizador vê
        a pré-visualização e decide."""
        data = self._json_obj(body)
        if data is None:
            return self._json({"ok": False, "error": "Pedido inválido."})
        request_text = str(data.get("request") or "").strip()
        if len(request_text) < 8:
            return self._json({"ok": False,
                               "error": "Descreve o que queres programar e commitar."})
        plan, err = generate_commit_plan(request_text[:2000], data.get("model"))
        if err:
            return self._json({"ok": False, "error": err})
        log.info("commit plan: %s (%d ficheiro(s))", plan["branch"], len(plan["files"]))
        self._json({"ok": True, "plan": plan})

    def _handle_confirm_commit(self, body):
        """Commit por IA (2/2): executa o plano que o utilizador CONFIRMOU —
        branch ai/* + commit + Merge Request. A branch principal nunca é tocada."""
        data = self._json_obj(body)
        if data is None or not isinstance(data.get("plan"), dict):
            return self._json({"ok": False, "error": "Plano inválido."}, status=400)
        try:
            result = execute_commit_plan(data["plan"])
        except Exception:
            log.exception("commit execute failed")
            result = {"ok": False, "error": "Erro inesperado ao executar o commit."}
        log.info("commit execute -> ok=%s branch=%s mr=%s err=%s",
                 result.get("ok"), result.get("branch"),
                 result.get("mr_iid"), result.get("error"))
        self._json(result)

    def _handle_confirm_action(self, body):
        """Execute an action the user explicitly confirmed in the UI."""
        data = self._json_obj(body)
        if data is None:
            return self._error("Pedido inválido.", status=400)
        tool = data.get("tool", "")
        args = data.get("args") or {}
        if not isinstance(tool, str) or tool not in CONFIRMABLE_TOOLS:
            return self._error(f"ação inválida: {str(tool)[:40]}", status=400)
        try:
            result = execute_issue_tool(tool, args)
        except Exception:
            log.exception("confirm action failed")
            return self._error("Não foi possível executar a ação.")
        log.info("confirmed action %s -> ok=%s", tool, result.get("ok"))
        self._json(result)

    def _handle_suggestions(self, body):
        """Return 3 short, context-aware follow-up chips for the UI. Always
        returns 200 with something usable — falls back to defaults on any error
        so the chips never break the page."""
        try:
            data = self._json_obj(body) or {}
            msgs = data.get("messages") if isinstance(data.get("messages"), list) else []
            recent = [m for m in msgs
                      if isinstance(m, dict) and isinstance(m.get("content"), str)
                      and m.get("content")][-4:]
            convo_txt = "\n".join(f"{m.get('role', '?')}: {m['content'][:300]}"
                                  for m in recent)
            prompt = (
                "És o motor de sugestões de um assistente de IA para projetos "
                "GitLab. O assistente sabe: responder sobre issues, commits, "
                "progresso e autores; descrever o projeto (o que é, linguagens, "
                "estrutura); gerar gráficos (por assignee, burndown, commits por "
                "autor); criar o relatório do projeto; e exportar issues/commits "
                "para CSV. Com base na conversa abaixo, sugere exatamente 5 "
                "sugestões curtas de seguimento (máximo 6 palavras cada), em "
                "português de Portugal, VARIADAS entre esses tipos e relevantes "
                "para o que se falou (ex.: «Relatório do projeto», «Burndown 14 "
                "dias», «O que é este projeto?», «Quem tem mais commits?», "
                "«Exporta os commits para CSV»). REGRAS: só pedidos de "
                "leitura/consulta; NUNCA sugiras ações que alterem dados (criar, "
                "editar, fechar, adicionar, apagar). Responde APENAS com um array "
                "JSON de 5 strings.\n\n"
                f"Conversa:\n{convo_txt or '(início)'}"
            )
            # As sugestões usam SEMPRE o modelo secundário (8B, rápido, maior quota).
            # Sem fallback de modelo (_groq_once, não _groq_complete): se falhar, é
            # melhor cair nos DEFAULT_SUGGESTIONS do que gastar o balde do 70B numa
            # tarefa secundária. A página nunca parte (except → defaults).
            payload = {
                "model": SUGGESTIONS_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 150,
            }
            resp = _groq_once(payload)
            text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
            self._json({"suggestions": _parse_suggestions(text) or DEFAULT_SUGGESTIONS})
        except Exception as e:
            log.warning("suggestions failed: %s", e)
            self._json({"suggestions": DEFAULT_SUGGESTIONS})


from src.threading_server import ThreadingServer  # classe em src/threading_server.py (re-export)


# ── Entrypoint ────────────────────────────────────────────────────────────────


def main():
    try:
        srv = ThreadingServer(("", PORT), Handler, max_connections=MAX_CONNECTIONS,
                              read_timeout=REQUEST_READ_TIMEOUT)
    except OSError as e:   # port in use / not allowed
        raise SystemExit(f"Cannot listen on port {PORT}: {e}")
    log.info("=" * 60)
    log.info("SprintLab chatbox listening on http://0.0.0.0:%d", PORT)
    if PUBLIC_URL:
        log.info("Public URL: %s", PUBLIC_URL)
    log.info("GitLab project: %s", GITLAB_PROJECT_ID)
    log.info(
        "Cache TTL: %ds  |  Page limit: %d  |  Max connections: %d",
        CACHE_TTL, GITLAB_PAGE_LIMIT, MAX_CONNECTIONS,
    )
    log.info("Groq default model: %s (reasoning_effort=%s) — used when the UI "
             "doesn't pick one", GROQ_MODEL, GROQ_REASONING_EFFORT or "off")
    log.info("Writes with the server token: %s",
             "require APP_ACCESS_KEY" if APP_ACCESS_KEY
             else "DISABLED (set APP_ACCESS_KEY to enable)")
    log.info("=" * 60)
    srv.serve_forever()


if __name__ == "__main__":
    main()
