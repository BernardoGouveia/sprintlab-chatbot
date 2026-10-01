"""
Configuração central (env-driven, fail-fast) + prompts estáticos.
Tudo o que é ajustável por variável de ambiente vive aqui.
"""

from __future__ import annotations

import logging
import os
import urllib.parse

# ── Config (env-driven, fail fast) ────────────────────────────────────────────

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
if not GITLAB_TOKEN:
    raise SystemExit(
        "GITLAB_TOKEN is not set. Set it as a Hugging Face Space Secret "
        "(Settings -> Variables and secrets), or export it when running locally."
    )

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise SystemExit(
        "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys "
        "and set it as a Hugging Face Space Secret."
    )

def _env_int(name, default, minimum=None, maximum=None):
    """Integer env var; a malformed value stops the server with a clear message
    (fail-fast, like the missing Secrets above) instead of a bare traceback."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} must be an integer (got {raw!r}).")
    if minimum is not None and value < minimum:
        raise SystemExit(f"{name} must be >= {minimum} (got {value}).")
    if maximum is not None and value > maximum:
        raise SystemExit(f"{name} must be <= {maximum} (got {value}).")
    return value


GITLAB_PROJECT_ID = os.environ.get("GITLAB_PROJECT_ID", "80767095")
GITLAB_BASE = os.environ.get("GITLAB_BASE", "https://gitlab.com/api/v4").strip().rstrip("/")
try:
    _base = urllib.parse.urlsplit(GITLAB_BASE)
    _base_port = _base.port          # ValueError on 'abc' / > 65535
except ValueError:                   # also a malformed IPv6 literal
    _base = _base_port = None
if (_base is None or _base.scheme.lower() not in ("https", "http") or not _base.hostname
        or _base.query or _base.fragment or _base.username or _base.password
        or _base_port == 0):
    raise SystemExit(
        f"GITLAB_BASE must be the GitLab API URL, e.g. https://gitlab.com/api/v4 "
        f"(got {GITLAB_BASE!r}).")
if not _base.path.rstrip("/").endswith("/api/v4"):
    # the instance URL was given (as the custom-workspace form accepts): same
    # normalisation as for X-GL-Base
    GITLAB_BASE += "/api/v4"
PORT = _env_int("PORT", 7860, minimum=1, maximum=65535)  # HF Spaces routes to app_port (7860)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # informational only
CACHE_TTL = _env_int("CACHE_TTL", 45, minimum=0)
GITLAB_PAGE_LIMIT = _env_int("GITLAB_PAGE_LIMIT", 5, minimum=1)
# Prazo total (s) de cada leque de pedidos paralelos ao GitLab (stats, relatório,
# contexto do chat). Passado o prazo, responde-se com as secções que chegaram.
GITLAB_FANOUT_TIMEOUT = _env_int("GITLAB_FANOUT_TIMEOUT", 25, minimum=1)
# Threads de trabalho para esses pedidos paralelos, no processo inteiro (2/3
# reservados para o GitLab do servidor, 1/3 para instâncias personalizadas) e
# quantas um mesmo cliente (IP) pode ocupar ao mesmo tempo.
GITLAB_WORKERS = _env_int("GITLAB_WORKERS", 96, minimum=3)
GITLAB_WORKERS_PER_CLIENT = _env_int("GITLAB_WORKERS_PER_CLIENT", 24, minimum=1)
# Instâncias GitLab personalizadas (X-GL-Base) só em https e em endereços
# públicos. "1" permite http:// e redes internas — só para desenvolvimento local.
GITLAB_ALLOW_PRIVATE = os.environ.get("GITLAB_ALLOW_PRIVATE", "") == "1"

# ── Acesso às escritas ────────────────────────────────────────────────────────
# Escritas no GitLab (issues, commits por IA) que usam o GITLAB_TOKEN do servidor
# exigem esta chave (header X-App-Key, introduzida nas ⚙️ Definições). Vazio =
# escritas com o token do servidor DESLIGADAS. Quem usa o seu próprio token
# (workspace personalizado) não precisa da chave.
APP_ACCESS_KEY = os.environ.get("APP_ACCESS_KEY", "").strip()
# Pedidos de escrita por minuto, por IP (0 = desligado).
WRITE_RATE_LIMIT = _env_int("WRITE_RATE_LIMIT", 30, minimum=0)
# Leituras /gitlab/* por minuto, por IP (0 = desligado) — cada uma pode gerar
# vários pedidos ao GitLab com o token do servidor.
READ_RATE_LIMIT = _env_int("READ_RATE_LIMIT", 120, minimum=0)

# ── Servidor HTTP ─────────────────────────────────────────────────────────────
# Quantos proxies de confiança acrescentam o IP ao X-Forwarded-For (contados a
# partir da direita, ignorando endereços internos). 1 = um proxy (HF Spaces).
TRUSTED_PROXY_HOPS = _env_int("TRUSTED_PROXY_HOPS", 1, minimum=1)
MAX_BODY_BYTES = _env_int("MAX_BODY_BYTES", 5 * 1024 * 1024, minimum=1024)
MAX_CONNECTIONS = _env_int("MAX_CONNECTIONS", 64, minimum=1)
# Pedidos dinâmicos em simultâneo por IP de cliente: um cliente sozinho não ocupa
# as MAX_CONNECTIONS ligações. Os ficheiros estáticos não contam. Atrás de um NAT
# (escola/empresa) todos partilham o mesmo IP — aumentar para demos grandes.
MAX_CONCURRENT_PER_IP = _env_int("MAX_CONCURRENT_PER_IP", 16, minimum=1)
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 60, minimum=1)  # s por operação de socket
# Prazo TOTAL (s) para o cliente enviar o pedido completo (linha, cabeçalhos e
# corpo): um cliente que envia um byte de cada vez não prende a ligação.
REQUEST_READ_TIMEOUT = _env_int("REQUEST_READ_TIMEOUT", 20, minimum=1)

# Groq inference (OpenAI-compatible API). Model + reasoning are env-configurable
# so you can switch to e.g. llama-3.3-70b-versatile without touching code.
GROQ_URL = os.environ.get(
    "GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"
)
# Default: Llama 3.3 70B — the most reliable tool-calling model on Groq (no
# reasoning quirks). Prefer Qwen? Set GROQ_MODEL="qwen/qwen3-32b" AND
# GROQ_REASONING_EFFORT="none".
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Empty for non-reasoning models (Llama). For Qwen3, set "none" to skip <think>.
GROQ_REASONING_EFFORT = os.environ.get("GROQ_REASONING_EFFORT", "")
# Cloudflare (in front of api.groq.com) blocks the default "Python-urllib"
# User-Agent with "error code: 1010". Send a normal browser UA so the request
# reaches the Groq API instead of being bounced at the edge.
GROQ_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(threadName)-12s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sprintlab")

# ── Static context (kept inline; full RAG is a separate task) ─────────────────

DOCUMENT_CONTEXT = """=== RELATÓRIO INTERCALAR TFC — SprintLab ===
Autor: Bernardo Gouveia | Orientador: Daniel Silveira | LEI | Universidade Lusófona | 2025/2026

DESCRIÇÃO: O SprintLab é um middleware e plugin para Microsoft Teams que integra o GitLab com sincronização bidirecional de issues, Kanban, Gantt e IA conversacional.
PROBLEMA: Falta de integração entre GitLab e Microsoft Teams gera processos fragmentados, duplicação de tarefas e perda de eficiência. Parceiro: GMV.
SOLUÇÃO: Middleware Express.js + plugin Teams + chatbox IA com Llama 3.3 70B via Groq (inferência rápida, modelo open-source, com function calling para ações no GitLab).
TECNOLOGIAS: Express.js (middleware), Microsoft Teams API (plugin), GitLab API (webhooks/issues), Llama 3.3 70B via Groq (IA), Hugging Face Spaces (hosting), PostgreSQL (configurações), Docker.
BENCHMARKING: SprintLab é o único com Chatbox IA + NLP + Sincronização bidirecional GitLab↔Teams + Relatórios automáticos IA.
VIABILIDADE: 85% melhoraria eficiência, 70% Kanban+Gantt essenciais, 90% interesse em automação. Redução de 40% em tarefas administrativas. Modelo SaaS.
FUNCIONALIDADES IA: (1) Chatbox NLP — criar/fechar/atualizar issues, queries analíticas, exportar CSV. (2) Relatórios automáticos de sprint. (3) Motor IA↔GitLab↔Teams.
GLOSSÁRIO: LEI=Licenciatura Eng. Informática, TFC=Trabalho Final de Curso, SaaS=Software as a Service, NLP=Natural Language Processing."""

SYSTEM_PROMPT = """És um assistente de IA para projetos GitLab. O projeto atual é indicado nos «DADOS GITLAB» abaixo (nome, contagens, issues e — na secção REPOSITÓRIO — descrição, linguagens, estrutura e excerto do README). Não assumas que é sempre o mesmo — adapta a tua resposta ao projeto em contexto.
Responde SEMPRE em português de Portugal. Sê direto, claro e conciso.
Usa os dados fornecidos. Não inventes informação. NÃO arredondes contagens (commits, issues, autores) — usa o número exato do contexto. O total de commits do projeto é a linha «Total de commits no repositório».
REGRA ABSOLUTA SOBRE SIGLAS E SIGNIFICADOS: só podes dar o significado/expansão de uma sigla, nome ou termo se a expansão estiver ESCRITA LITERALMENTE no contexto (README/descrição) acima. Se NÃO estiver, a ÚNICA resposta válida é «O README não indica o que significa a sigla X.» — e páras aí, sem acrescentar palpites. É TERMINANTEMENTE PROIBIDO usar «é razoável concluir», «provavelmente significa», «com base em contextos semelhantes», «parece ser» ou «refere-se a» para inventar uma expansão — isso é alucinação. EXEMPLO CONCRETO: se te perguntarem «o que significa AIR» e o README só disser «AIR» (sem a expansão escrita), respondes «O README não indica o que significa AIR» — NUNCA inventes «Avionics Integration Runtime» nem qualquer outra expansão.
ESTA REGRA APLICA-SE TAMBÉM QUANDO ESTÁS APENAS A DESCREVER O PROJETO (ex.: «fala-me do projeto», «o que é isto»): NUNCA escrevas uma sigla seguida da expansão entre parênteses — como «AIR (Avionics Integration Runtime)» — se essa expansão não aparecer LITERALMENTE no contexto. Escreve só a sigla («AIR»), sem parênteses explicativos. E NÃO copies o padrão de outras siglas que POR ACASO estejam expandidas no README (ex.: «RTEMS (Real-Time Executive...)») para siglas que NÃO estão — cada expansão tem de estar escrita no texto, caso a caso. Na dúvida, não expandas.
QUANDO O UTILIZADOR TE QUESTIONA («isto está certo?», «onde viste isso?», «de onde tiraste?»): NÃO confirmes automaticamente a tua resposta anterior. RE-EXAMINA o contexto. Se afirmaste algo que NÃO está escrito literalmente no contexto, ADMITE o erro e corrige — nunca defendas uma invenção tua.
Tens SEMPRE em contexto os dados atuais do GitLab (totais, progresso, issues abertas, assignees, atrasos, milestones) — usa-os para responder com precisão. As issues SÃO as tarefas do projeto. NUNCA digas que não tens acesso às issues/tarefas nem mandes o utilizador ir ao GitLab: tens os dados aqui.

PROCURAR INFORMAÇÃO (ferramentas de leitura): quando a resposta NÃO está no contexto acima, NÃO digas «não tenho acesso» nem mandes o utilizador usar o git/GitLab — usa as ferramentas para a ir buscar:
- get_issue(iid): detalhes de UMA issue (estado, assignees, labels, milestone, data) — o contexto só lista as issues abertas mais recentes; para qualquer outra issue, chama get_issue em vez de adivinhar.
- search_commits(query): «qual commit alterou X», «commits sobre/que mencionam X».
- commits_by_author(author): «commits do X», «último commit do X», «quantos commits fez o X».
- search_code(query): «onde está X no código», «que ficheiros usam X».
A linha «Atividade recente (últimos 90 dias)» é só uma janela recente: se estiver a 0 ou não tiver o autor, isso NÃO significa que o repositório ou o autor não tenham commits. Para «último commit de X» ou histórico por autor, chama SEMPRE commits_by_author — NUNCA respondas «não há commits do X» a partir da janela de 90 dias.
Depois de receberes os resultados, responde com os dados concretos (SHA curto, autor, data, título). Se a pesquisa não devolver nada, di-lo honestamente — não inventes commits.
NÃO narres intenções: NUNCA respondas «vou buscar...», «deixa-me procurar...» ou «o utilizador pediu...» — ou CHAMAS a ferramenta e respondes já com os dados, ou dizes que não encontraste. Não descrevas o que vais fazer.
Ao listar commits de um autor, mostra o TOTAL e a LISTA dos mais recentes (não só o último — exceto se pedirem mesmo «o último commit»). Para «mais commits do X», chama commits_by_author de novo com offset (ex.: offset=15) para a página seguinte.
Quando te perguntarem «fala-me do projeto» / «o que é isto» / «a estrutura», descreve o projeto a partir da secção REPOSITÓRIO (o que faz, linguagens, organização) — NÃO respondas só com a contagem de issues. Mesmo que o README esteja em inglês, responde em português de Portugal (ex.: «ficheiro» não «arquivo», «gestão» não «gerenciamento»). Baseia o propósito do projeto no que está escrito no README/descrição — se não for claro o que o projeto faz, di-lo em vez de adivinhar.

AÇÕES NO GITLAB:
- CRIAR issues NÃO é contigo: existe um formulário próprio. Se o utilizador quiser criar, diz-lhe para escrever «criar issue». NUNCA finjas criar nem inventes uma issue.
- ATUALIZAR/EDITAR: chama update_issue APENAS com o `iid` (o número). NÃO preenchas título/descrição/data — o utilizador edita tudo num FORMULÁRIO pré-preenchido que abre a seguir. Se ele pediu para atualizar e depois disser só um número, chama update_issue com esse iid. NUNCA inventes os campos.
- FECHAR (close_issue) e APAGAR (delete_issue): chama com o `iid`; o utilizador confirma num cartão. delete_issue só se ele pedir mesmo para APAGAR/ELIMINAR (é irreversível).
- NUNCA inventes números, títulos, descrições nem datas. Se faltar o número da issue, PERGUNTA.
- "Como faço X?" → responde com instruções, sem chamar ferramentas."""

# Project ID where the SprintLab knowledge (above) actually applies. For any
# other project (custom GitLab via the settings panel), DOCUMENT_CONTEXT is
# replaced by a lightweight per-project header built at request time.
SPRINTLAB_PROJECT_ID = "80767095"

# Orçamento de caracteres do excerto do README injetado no contexto do LLM.
README_MAX = _env_int("README_MAX", 1500, minimum=0)

# Models offered in the UI switcher → reasoning_effort to use for each.
# Empty string = not a reasoning model (Llama/GPT-OSS/Kimi).
# "none" = reasoning model (Qwen3) com o passo <think> desligado.
GROQ_MODELS = {
    "llama-3.3-70b-versatile": "",
    "llama-3.1-8b-instant": "",
    "qwen/qwen3-32b": "none",
    "openai/gpt-oss-120b": "",
    "moonshotai/kimi-k2-instruct-0905": "",
}

# Modelo das tarefas secundárias (sugestões de seguimento): rápido e com a maior
# quota grátis, para não gastar o "balde" do modelo principal.
SUGGESTIONS_MODEL = "llama-3.1-8b-instant"

# Pedidos por minuto, por IP, nos endpoints que consomem Groq (0 = desligado).
# Protege a QUOTA grátis partilhada (ops) — não é autenticação.
RATE_LIMIT = _env_int("RATE_LIMIT", 20, minimum=0)
