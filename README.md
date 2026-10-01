---
title: SprintLab TFC Chatbox
emoji: 🤖
colorFrom: purple
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# SprintLab TFC Chatbox — Cloud edition (Groq + Hugging Face Spaces)

Assistente IA do TFC **SprintLab** (Bernardo Gouveia, LEI, Universidade Lusófona).
Esta versão corre **inteiramente na cloud, grátis**: o `server.py` num Hugging Face
Docker Space e a inferência no **Groq** (`llama-3.3-70b-versatile`, com function
calling para criar/fechar/atualizar issues por linguagem natural). Sem Ollama local, sem
tunnel, sem portátil — URL público e estável (`https://<user>-<space>.hf.space`).

## Arquitetura

```
Microsoft Teams (tab)
        ↓  (iframe, URL estável *.hf.space)
  Hugging Face Space  →  server.py (proxy + GitLab + charts)
        ↓                      ↓
   Groq API              GitLab API
 (llama-3.3-70b)        (issues, MRs, commits)
```

## Estrutura do código

| Ficheiro | Responsabilidade |
|---|---|
| `server.py` | Entrypoint HTTP: rotas, streaming SSE do chat, rate-limit, chave de acesso das escritas |
| `src/config.py` | Configuração env-driven (fail-fast) + prompts |
| `src/gitlab_api.py` | Cliente GitLab: validação multi-tenant, cache TTL, paginação, fetchers |
| `src/cache.py` / `src/rate_limiter.py` / `src/threading_server.py` | Cache TTL, limitador por IP, servidor multi-thread |
| `src/analytics.py` | Estatísticas, contexto do LLM, exports CSV |
| `src/report.py` | Relatório do projeto (determinístico) |
| `src/charts.py` | Dados Chart.js dos gráficos inline |
| `src/blame.py` | Investigação de código (git blame + diff + análise IA) |
| `src/code_commit.py` | Commit por IA: plano → confirmação → branch `ai/*` + commit + MR |
| `src/actions.py` | Escritas no GitLab (validação + executor único) |
| `src/read_tools.py` | Ferramentas de leitura do modelo (commits, código, issues) |
| `src/llm.py` | Cliente Groq (modelos, parsing) |
| `chatbox.html` / `style.css` / `app.js` | Frontend (3 temas, workspaces, cards) |
| `tests/` | Testes unitários + integração HTTP (`python -m pytest`) e routing do frontend (`node tests/route_harness.mjs`) |

## Deploy (uma vez)

1. **Cria uma chave Groq** (grátis, sem cartão): https://console.groq.com/keys
2. **Cria um Space**: huggingface.co → New → Space → **Docker** (Blank), visibilidade *Public*.
3. **Envia estes ficheiros** para o Space (git push ou upload):
   `Dockerfile`, `.dockerignore`, `server.py`, **a pasta `src/` inteira**
   (incluindo `src/__init__.py`, mas **sem** `src/__pycache__/`), `chatbox.html`,
   `style.css`, `app.js`, `README.md`, `requirements.txt`. O `Dockerfile` novo e a
   pasta `src/` têm de ir **no mesmo envio**. (As pastas `tests/`, `relatorio/` e
   `_backup_*` não são precisas; os módulos antigos da raiz do Space — `config.py`,
   `gitlab_api.py`, … — podem ser apagados.)
4. **Define os Secrets** em *Settings → Variables and secrets*:
   - `GROQ_API_KEY` = `gsk_...`
   - `GITLAB_TOKEN` = `glpat-...`
   - `APP_ACCESS_KEY` = uma frase/chave longa e aleatória — **sem ela, criar/fechar/
     editar/apagar issues e os commits por IA ficam desativados** no GitLab
     predefinido (ver [Segurança](#segurança)). Partilha-a só com quem pode alterar o GitLab.
   - (opcional) `GITLAB_PROJECT_ID`, `GROQ_MODEL`, `GROQ_REASONING_EFFORT`
5. O Space faz **build** e arranca sozinho. Quando ficar *Running*, o chatbox está em
   `https://<user>-<space>.hf.space`. Depois de uma atualização, **recarrega** os
   separadores/tabs do Teams que estavam abertos (continuam com a versão antiga da
   página até recarregar).

## Ligar ao Teams

Aponta o `manifest.json` para o URL do Space (host = `<user>-<space>.hf.space`).
Podes reutilizar o `package.ps1` da pasta `chatbox/`:

```powershell
.\teams-package\package.ps1 -NgrokHost "bernardo-sprintlab.hf.space"
```

Como o URL do Space **não muda**, fazes isto **uma única vez** — nunca mais mexes no Teams.

## Variáveis de ambiente

| Variável | Obrigatória | Default | Notas |
|---|---|---|---|
| `GROQ_API_KEY` | sim | — | Chave Groq (`gsk_...`) |
| `GITLAB_TOKEN` | sim | — | Token GitLab (`glpat-...`) — só é usado com `GITLAB_BASE` + `GITLAB_PROJECT_ID` |
| `APP_ACCESS_KEY` | recomendada | `""` (vazio) | Chave exigida (header `X-App-Key`, introduzida em ⚙️ Definições) para as escritas com o `GITLAB_TOKEN`. Vazia = essas escritas desligadas |
| `GITLAB_PROJECT_ID` | não | `80767095` | ID do projeto GitLab |
| `GITLAB_BASE` | não | `https://gitlab.com/api/v4` | API do GitLab predefinido (self-managed: `https://<host>/api/v4`; se indicares só `https://<host>`, o `/api/v4` é acrescentado) |
| `GROQ_MODEL` | não | `llama-3.3-70b-versatile` | Modelo usado quando o utilizador deixa "Predefinido do servidor" nas definições. Alt.: `qwen/qwen3-32b` (+ `GROQ_REASONING_EFFORT=none`) |
| `GROQ_REASONING_EFFORT` | não | `""` (vazio) | Vazio para Llama. Para Qwen3 põe `none` (respostas diretas, sem `<think>`) |
| `GROQ_URL` | não | API do Groq | Endpoint OpenAI-compatible |
| `CACHE_TTL` | não | `45` | Segundos de cache das chamadas GitLab |
| `GITLAB_PAGE_LIMIT` | não | `5` | Máx. páginas (×100 itens). Acima disso as listas são marcadas como parciais e os totais como aproximados |
| `GITLAB_FANOUT_TIMEOUT` | não | `25` | Prazo total (s) dos pedidos paralelos ao GitLab por resposta |
| `GITLAB_WORKERS` | não | `96` | *Threads* para esses pedidos paralelos, no processo inteiro (2/3 reservados ao GitLab predefinido, 1/3 às instâncias personalizadas) |
| `GITLAB_WORKERS_PER_CLIENT` | não | `24` | Quantas dessas *threads* um mesmo IP pode ocupar ao mesmo tempo (acima disso as secções esperam a vez) |
| `GITLAB_ALLOW_PRIVATE` | não | vazio | `1` permite instâncias `http://` e em redes internas (só desenvolvimento local) |
| `RATE_LIMIT` | não | `20` | Pedidos/min por IP nos endpoints que usam o Groq (0 = desligado) |
| `WRITE_RATE_LIMIT` | não | `30` | Escritas/min por IP (0 = desligado) |
| `READ_RATE_LIMIT` | não | `120` | Leituras `/gitlab/*` por minuto, por IP (0 = desligado) |
| `MAX_CONCURRENT_PER_IP` | não | `16` | Pedidos dinâmicos em simultâneo por IP (acima disso: 429). Os ficheiros estáticos não contam |
| `TRUSTED_PROXY_HOPS` | não | `1` | Proxies de confiança que acrescentam ao `X-Forwarded-For` (conta a partir da direita). Se houver um CDN + load balancer à frente, usa `2` |
| `MAX_BODY_BYTES` | não | `5242880` | Tamanho máximo do corpo de um pedido |
| `MAX_CONNECTIONS` | não | `64` | Ligações HTTP em simultâneo (acima disso: 503 imediato) |
| `REQUEST_TIMEOUT` | não | `60` | Timeout (s) por operação de socket com o cliente |
| `REQUEST_READ_TIMEOUT` | não | `20` | Prazo total (s) para o cliente enviar o pedido completo |
| `README_MAX` | não | `1500` | Caracteres do excerto do README no contexto do LLM |
| `LOG_LEVEL` | não | `INFO` | Nível de log |
| `PUBLIC_URL` | não | vazio | Só informativo (aparece no arranque) |

> O `GROQ_REASONING_EFFORT` só se aplica a modelos *reasoning* (ex. Qwen3). Com o
> default Llama deixa-o vazio — senão o Groq rejeita o parâmetro.

> **Demos com muitas pessoas atrás do mesmo IP** (sala de aula, escritório): os
> limites por IP (`RATE_LIMIT`, `READ_RATE_LIMIT`, `MAX_CONCURRENT_PER_IP`) são
> partilhados por todos — cada pergunta no chat gasta 2 do `RATE_LIMIT` (resposta +
> sugestões). Para uma demo com 20+ pessoas, aumenta por exemplo `RATE_LIMIT=120`,
> `MAX_CONCURRENT_PER_IP=48` e `GITLAB_WORKERS_PER_CLIENT=64`.

## Segurança

- **Escritas protegidas.** Com o token do servidor (`GITLAB_TOKEN`), criar/fechar/
  editar/apagar issues e os commits por IA exigem a `APP_ACCESS_KEY` (cada utilizador
  introdu-la uma vez em ⚙️ Definições; fica só no browser dele). Sem o secret, essas
  escritas ficam desligadas — ler, conversar, gráficos e relatórios continuam.
- **Workspaces com token próprio.** Um projeto adicionado com o token do próprio
  utilizador não precisa da chave: é o GitLab que autoriza. O token do servidor
  **nunca** é usado para outro URL de GitLab nem para outro projeto.
- **Instâncias personalizadas** só em `https://` e em endereços públicos (sem acesso
  a redes internas a partir do Space). Uma instância lenta ou hostil não bloqueia o
  servidor: cada pedido ao GitLab tem prazo total (também em HTTPS) e tamanho máximo,
  cada pedido do utilizador tem um orçamento de tempo, e as instâncias personalizadas
  têm uma quota própria de ligações e de *threads* — o GitLab predefinido mantém sempre
  capacidade.
- **Commits por IA** vão sempre para uma branch `ai/*` + Merge Request, com
  `[skip ci]` (nenhum pipeline corre código gerado antes da revisão humana) e sem
  poder alterar `.gitlab-ci.yml` / `.gitlab/`.
- A página usa CSP e Subresource Integrity nos recursos de CDN.

## Correr localmente (dev)

```powershell
$env:GROQ_API_KEY="gsk_..."
$env:GITLAB_TOKEN="glpat-..."
$env:APP_ACCESS_KEY="uma-chave-local"   # para testar as escritas
$env:PORT="8080"
python server.py
# abre http://localhost:8080
```

## Notas

- Limites do free tier Groq são **por modelo** (cada modelo tem a sua quota de
  req/min e tokens/min) — se um modelo atingir o limite, troca de modelo nas
  definições (⚙️) e continuas com quota fresca. O servidor também aplica um
  rate-limit por IP (`RATE_LIMIT`) para um único cliente não esgotar a quota.
- O Space adormece após ~48h sem tráfego; o primeiro pedido a seguir tem um arranque curto.
- Segredos nunca ficam no repositório — só nos Secrets do Space.
- Workspaces e conversas vivem no `localStorage` do browser; usa os botões
  **Backup / Repor** (rodapé da barra lateral) para os levar para outro browser.

## Testes

```bash
python -m pip install -r requirements-dev.txt
python -m pytest              # unitários + integração HTTP (sem rede)
node tests/route_harness.mjs  # routing de intenções do frontend (app.js)
```
