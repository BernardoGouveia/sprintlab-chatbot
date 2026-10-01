# Anexo — Descrição da Suíte de Testes Automatizados

A solução é coberta por **506 testes automatizados** (*pytest*), executados sem acesso à
rede (o GitLab e o modelo de linguagem são substituídos por *fakes*), em cerca de meio minuto, e
complementados por **49 casos** de *routing* do *frontend*
(`node tests/route_harness.mjs`). Os testes *pytest* organizam-se em **dois níveis**:

- **Testes unitários** — verificam a lógica pura (funções sem efeitos de rede): sanitização de
  input, agregações, geração de relatório, investigação de código, segurança da geração de
  commits, regras de segurança do cliente GitLab e da cache, e limites de carga.
- **Testes de integração** — arrancam um **servidor HTTP real** numa *thread* e exercitam todas
  as rotas de ponta a ponta; apenas as funções de rede de mais baixo nível (pedidos ao GitLab e
  chamadas à Groq) são simuladas — o *parser* SSE real é exercitado com um *stream* simulado —,
  pelo que todo o restante código (rotas, cache, agregação) é o de produção.

## Distribuição dos testes

| Ficheiro | Foco |
|---|---|
| `test_helpers.py` | Sanitização de input e *helpers* base |
| `test_sprint_report.py` | Geração do relatório do projeto |
| `test_endpoints.py` | **Integração HTTP** (todas as rotas) |
| `test_gitlab_api.py` | Cliente GitLab: segurança multi-tenant, cache e correção dos dados |
| `test_code_commit.py` | Segurança da geração de código por IA |
| `test_repo_overview.py` | Visão geral do repositório (README/linguagens) |
| `test_blame.py` | Investigação de código (*git blame*) |
| `test_read_tools.py` | Ferramentas de leitura (procurar commits / por autor / código / issue) |
| `test_csv.py` | Exportações CSV |
| `test_llm.py` | Cliente do modelo de linguagem (*fallback* de modelo) |
| `test_stats.py` | Agregação de estatísticas de issues |
| `test_infra.py` | Limites de carga e configuração |
| `tests/route_harness.mjs` | **49 casos** de *routing* do *frontend* (Node.js) |
| **Total** | **506** testes *pytest* (+ 49 casos de *routing*) |

## O que cada grupo valida

**`test_helpers.py` — sanitização de input.**
Garante que o input do modelo e do utilizador é normalizado/rejeitado antes de chegar ao
GitLab: normalização de números de issue (`#5` → `5`), remoção de *labels* inventadas
(`label1`), validação de datas (`AAAA-MM-DD`), tokenização sem expressões regulares, remoção
de blocos `<think>` dos modelos de raciocínio, *parsing* tolerante das sugestões, e seleção do
modelo de linguagem permitido.

**`test_sprint_report.py` — relatório determinístico.**
Confirma que os números do relatório correspondem exatamente aos dados do GitLab; que os
*destaques* são gerados por regras; que o relatório **se adapta ao tipo de projeto** (ativo vs.
concluído, com ou sem issues); que o total canónico de commits é usado; e que o relatório
tolera secções em falta (resiliência). Verifica ainda que o estado do projeto (por exemplo,
«vazio») só é atribuído com evidência positiva (uma falha do GitLab não passa por projeto
vazio), que listas cortadas pelo limite de paginação são assinaladas como aproximadas
(usando as contagens exatas quando disponíveis) e que a mesma pessoa com vários e-mails conta
uma só vez.

**`test_endpoints.py` — integração HTTP.**
Exercita, contra um servidor real: páginas estáticas, estatísticas, relatório, exportações CSV,
gráficos, tratamento de erros (404), **isolamento multi-tenant** (o cabeçalho `X-GL-Project`
muda o projeto-alvo), execução de escritas confirmadas, *endpoints* de IA, o fluxo completo de
**commit por IA** (branch + commit + *Merge Request*) e o *rate-limit*. Cobre também a chave de
acesso das escritas (`X-App-Key`), a proteção SSRF (o token do servidor nunca é enviado a outra
instância), o isolamento da cache por token, a *Content-Security-Policy*, os limites
(413/429, entregues sem reinício da ligação), o erro 500 em JSON perante falhas inesperadas,
as páginas estáticas sempre servidas (mesmo com o limite por cliente esgotado), o limite do
caminho de ficheiro na investigação de código e o *parser* SSE real.

**`test_gitlab_api.py` — cliente GitLab e cache.**
Valida as regras de configuração multi-tenant (o token do servidor nunca é emprestado a outra
instância ou projeto), a proteção SSRF (apenas `https://` em endereços públicos, com o DNS
reverificado no momento da ligação), a recusa de redireccionamentos, a validação e codificação
do *project id*, a cache segmentada por token (com *single-flight* e tamanho limitado), a
sinalização de listas truncadas, os *milestones* de grupo, a fusão da mesma pessoa com vários
e-mails, a pesquisa de autor sem misturar pessoas («Ana» ≠ «Mariana»), o reporte de falhas em
vez de zeros, o prazo total das leituras paralelas e de cada pedido (servidores que enviam a
resposta byte a byte), os limites de bytes por resposta, por lista paginada e na cache, e as
contagens exatas de *merge requests* (cabeçalho `X-Total`).

**`test_code_commit.py` — segurança da geração de código.**
A camada de validação entre o JSON do modelo e a escrita no GitLab: rejeita caminhos perigosos
(`../`, `.git/` em qualquer nível, absolutos), a configuração de CI (`.gitlab-ci.yml`,
`.gitlab/`), conteúdos vazios ou demasiado grandes, planos malformados e nomes de *branch*
inválidos; valida a extração do plano a partir da resposta do modelo e confirma que o commit
leva `[skip ci]`.

**`test_repo_overview.py` — contexto do projeto.**
Valida a limpeza do README (remoção de *badges*, HTML, definições de ligação), a extração do
índice (ignorando blocos de código) e a construção do bloco "o que é este projeto" (descrição,
linguagens, estrutura).

**`test_blame.py` — investigação de código.**
Valida a resolução de caminhos absolutos para caminhos do repositório, a atribuição correta do
commit/autor a cada linha (a partir da resposta do *git blame*), a construção do excerto
numerado e a truncagem de *diffs* grandes.

**`test_read_tools.py` — ferramentas de leitura.**
Valida a pesquisa de commits e de código, os commits por autor (total e paginação, com
candidatos quando o nome é ambíguo) e a consulta de qualquer issue (`get_issue`); uma falha do
GitLab é reportada como erro e não como resultado vazio.

**`test_csv.py` — exportações.**
Confirma o BOM `utf-8-sig` (para o Excel abrir os acentos), os cabeçalhos e as linhas, tanto
para issues como para commits, e a neutralização de fórmulas em texto vindo do GitLab.

**`test_llm.py` — cliente do modelo de linguagem.**
Valida a troca automática de modelo quando a Groq responde 429, 404 ou 413 e a remoção do
parâmetro `reasoning_effort` quando o modelo de recurso não o suporta.

**`test_stats.py` — estatísticas.**
Valida a agregação partilhada (abertas/fechadas, progresso, em atraso com data injetada,
sem responsável) e a sinalização de listas truncadas como valores mínimos.

**`test_infra.py` — limites de carga.**
Valida o limitador de pedidos por IP, o limite de pedidos simultâneos por cliente, a resposta
imediata 503 quando o servidor está cheio (também com ligações inativas e pedidos grandes), o
prazo total de leitura do pedido (clientes lentos) e a validação da configuração no arranque.

**`tests/route_harness.mjs` — *routing* do *frontend*.**
Verifica a deteção de intenções do *frontend* (para onde cada frase é encaminhada), usando as
funções reais do `app.js`.

## Como executar

```bash
python -m pip install -r requirements-dev.txt
python -m pytest              # 506 testes, sem rede, cerca de meio minuto
node tests/route_harness.mjs  # 49 casos de routing do frontend
```

> Os testes não acedem à rede: o ficheiro `conftest.py` injeta credenciais falsas e os testes de
> integração substituem apenas as funções de rede de mais baixo nível, garantindo execução
> determinística e reprodutível.
