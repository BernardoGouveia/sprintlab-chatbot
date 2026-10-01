# Anexo — Guia de Instalação e Execução

O código-fonte está disponível no repositório do projeto, acompanhado do ficheiro `README.md`.
A aplicação foi concebida para correr **sem dependências externas** (apenas a biblioteca padrão
de Python), o que torna a instalação trivial.

## Estrutura do código-fonte

| Ficheiro | Responsabilidade |
|---|---|
| `server.py` | Ponto de entrada HTTP: rotas, *streaming* SSE do chat, *rate-limit*, chave de acesso das escritas |
| `src/config.py` | Configuração por variáveis de ambiente (*fail-fast*) + *prompts* |
| `src/gitlab_api.py` | Cliente GitLab (validação multi-tenant, cache TTL, paginação, *fetchers*) |
| `src/cache.py`, `src/rate_limiter.py`, `src/threading_server.py` | Cache TTL, limitador por IP, servidor multi-thread |
| `src/analytics.py` | Estatísticas, contexto do LLM, exportações CSV |
| `src/report.py` | Relatório do projeto (determinístico) |
| `src/charts.py` | Dados dos gráficos (Chart.js) |
| `src/blame.py` | Investigação de código (git blame + diff + análise IA) |
| `src/code_commit.py` | Commit por IA: plano → confirmação → branch `ai/*` + commit + Merge Request |
| `src/actions.py` | Escritas no GitLab (validação + executor único) |
| `src/read_tools.py` | Ferramentas de leitura do modelo (commits, código, issues) |
| `src/llm.py` | Cliente Groq (modelos, *parsing*) |
| `chatbox.html`, `style.css`, `app.js` | Interface (3 temas, *workspaces*, *cards*) |
| `tests/` | 506 testes automatizados (unitários + integração HTTP) e 49 casos de *routing* do frontend (apenas desenvolvimento) |
| `Dockerfile` | Imagem de contentor para alojamento (copia `server.py`, a pasta `src/` e o frontend) |

## Pré-requisitos

- Python 3.11 ou superior (para execução local) **ou** Docker (para contentor).
- Uma **chave Groq** (gratuita, sem cartão): https://console.groq.com/keys
- Um **token de acesso GitLab** com *scope* `api`.
- Uma **chave de acesso** (`APP_ACCESS_KEY`): uma frase/chave longa e aleatória, escolhida
  por quem administra o servidor e partilhada apenas com quem pode alterar o GitLab.
- (Opcional, apenas para os testes de *routing* do frontend) Node.js.

## Opção A — Executar localmente

```powershell
# definir os segredos (PowerShell)
$env:GROQ_API_KEY   = "gsk_..."
$env:GITLAB_TOKEN   = "glpat-..."
$env:APP_ACCESS_KEY = "uma-chave-local"   # necessária para testar as escritas
$env:PORT           = "8080"

python server.py
# abrir  http://localhost:8080
```

## Opção B — Executar com Docker

```bash
docker build -t sprintlab-chatbox .
docker run -p 7860:7860 \
  -e GROQ_API_KEY="gsk_..." \
  -e GITLAB_TOKEN="glpat-..." \
  -e APP_ACCESS_KEY="..." \
  sprintlab-chatbox
# abrir  http://localhost:7860
```

## Opção C — Implantar num Hugging Face Space (gratuito)

1. Criar um *Space* do tipo **Docker** (visibilidade *Public*).
2. Enviar `server.py`, **a pasta `src/` inteira** (incluindo `src/__init__.py`),
   `chatbox.html`, `style.css`, `app.js`, `Dockerfile`, `README.md` e `requirements.txt`
   (as pastas `tests/` e `relatorio/` não são necessárias).
   O `Dockerfile` e a pasta `src/` têm de ser enviados **juntos** (o `Dockerfile` copia a
   pasta `src/`). Se o *Space* tinha uma versão anterior, com os módulos `.py` na raiz
   (`config.py`, `gitlab_api.py`, …), **apagar esses ficheiros antigos** — na raiz fica
   apenas o `server.py`.
3. Em *Settings → Variables and secrets*, definir os *secrets* `GROQ_API_KEY`,
   `GITLAB_TOKEN` e `APP_ACCESS_KEY`.
4. O *Space* faz *build* e arranca automaticamente; quando ficar *Running*, fica acessível
   no URL público `https://<utilizador>-<space>.hf.space`.

## Chave de acesso às escritas

As escritas no GitLab que usam o token do servidor (`GITLAB_TOKEN`) — criar, fechar,
editar e apagar *issues* e os commits por IA — exigem a chave `APP_ACCESS_KEY`. Cada
utilizador introdu-la uma vez em ⚙️ **Definições**; fica guardada apenas nesse navegador
(não é incluída nos *backups*) e é enviada no *header* `X-App-Key`. Se o *secret* não
estiver definido, essas escritas ficam desativadas — ler, conversar, gráficos e
relatórios continuam a funcionar. Um *workspace* adicionado com o token GitLab do
próprio utilizador não precisa da chave (é o GitLab que autoriza). Trata-se de uma chave
partilhada, não de autenticação individual.

## Variáveis de ambiente

| Variável | Obrigatória | Default | Descrição |
|---|:--:|---|---|
| `GROQ_API_KEY` | sim | — | Chave da Groq |
| `GITLAB_TOKEN` | sim | — | Token GitLab (*scope* `api`); só é usado com `GITLAB_BASE` + `GITLAB_PROJECT_ID` |
| `APP_ACCESS_KEY` | recomendada | vazio | Chave exigida para as escritas com o `GITLAB_TOKEN` (vazia = essas escritas desativadas) |
| `GITLAB_BASE` | não | `https://gitlab.com/api/v4` | API do GitLab por defeito (*self-managed*: `https://<host>/api/v4`) |
| `GITLAB_PROJECT_ID` | não | `80767095` | ID do projeto GitLab por defeito |
| `GROQ_MODEL` | não | `llama-3.3-70b-versatile` | Modelo de linguagem por defeito (usado quando o utilizador não escolhe outro) |
| `CACHE_TTL` | não | `45` | Segundos de validade da cache |
| `GITLAB_PAGE_LIMIT` | não | `5` | Máx. de páginas (×100 itens) por lista; acima disso as listas são marcadas como parciais e os totais como aproximados |
| `RATE_LIMIT` | não | `20` | Pedidos/min por IP nos endpoints de IA (0 = desligado) |
| `WRITE_RATE_LIMIT` | não | `30` | Escritas/min por IP (0 = desligado) |
| `READ_RATE_LIMIT` | não | `120` | Leituras do GitLab/min por IP (0 = desligado) |
| `README_MAX` | não | `1500` | Caracteres do excerto do README no contexto |

A lista completa (limites de ligações, *timeouts*, etc.) está no `README.md`.

## Executar os testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest              # 506 testes, sem rede, cerca de meio minuto
node tests/route_harness.mjs  # 49 casos de routing do frontend (requer Node.js)
```
