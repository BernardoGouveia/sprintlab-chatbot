# Testes automatizados

Testes em três níveis, **sem rede** (o GitLab e o Groq nunca são chamados):

1. **Unitários** — lógica pura dos módulos (sanitização, agregações, CSV,
   relatório, blame, repo overview, cliente GitLab, cache, limitador).
2. **Integração** (`test_endpoints.py`) — um servidor HTTP **real** arranca numa
   thread e todas as rotas são exercitadas ponta-a-ponta; só as funções de rede
   (`_gitlab_request`, `_groq_complete`/`_groq_once` e a abertura do stream do
   Groq) são substituídas por fakes — o parser SSE real corre sobre um stream falso.
3. **Routing do frontend** (`route_harness.mjs`) — extrai as funções de deteção de
   intenção REAIS do `app.js` e verifica para onde cada frase é encaminhada.

## Correr

```bash
python -m pip install -r requirements-dev.txt
python -m pytest              # a partir da pasta chatbox-cloud/ (ou de tests/)
node tests/route_harness.mjs  # requer Node.js
```

## O que é coberto

| Ficheiro | Foco |
|---|---|
| `test_helpers.py`      | `_norm_iid`, `_clean_labels`, `_valid_due_date`, `_int_ids`, `_words`, `_strip_think`, `_parse_suggestions`, `_pick_model`, `_action_summary` |
| `test_stats.py`        | `_issue_stats` — abertas/fechadas, progresso, em atraso, sem assignee, listas truncadas |
| `test_sprint_report.py`| `build_sprint_report` + `_due_status` — números exatos, destaques, markdown, estado só com provas, secções indisponíveis |
| `test_csv.py`          | `issues_to_csv` / `commits_to_csv` — BOM para Excel, cabeçalhos, linhas, injeção de fórmulas |
| `test_blame.py`        | investigação de código — `_path_candidates`, `_blame_owner`, `_blame_snippet`, `_diff_excerpt` |
| `test_repo_overview.py`| visão geral do repo — limpeza do README, índice, linhas do contexto |
| `test_code_commit.py`  | commit por IA — validação de caminhos (CI, `.git`), parsing do plano, execução (limpeza de branches, `[skip ci]`) |
| `test_llm.py`          | cliente Groq — fallback de modelos em 429/404 |
| `test_read_tools.py`   | ferramentas de leitura — pesquisa de commits/código, commits por autor, `get_issue` |
| `test_gitlab_api.py`   | regras multi-tenant (token do servidor, URLs, project id), cache (isolamento por token, invalidação, single-flight), redirects, paginação, autores |
| `test_infra.py`        | `RateLimiter`, `ThreadingServer`, parsing de variáveis de ambiente |
| `test_endpoints.py`    | **integração HTTP**: estáticos/CSP, stats, report, exports, charts, multi-tenant (X-GL-*), autorização das escritas (`X-App-Key`), confirm-action/commit, SSE do chat, robustez, rate-limit |

As datas são injetadas (`today=...`) para os testes de "em atraso" serem
determinísticos. `conftest.py` fixa `GITLAB_TOKEN`/`GROQ_API_KEY` falsos e limpa
as restantes variáveis de configuração antes de importar o `server` — um
`GITLAB_PROJECT_ID` exportado na shell não altera os testes.
