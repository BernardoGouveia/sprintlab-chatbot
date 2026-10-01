# Secções 5 a 9 — Relatório Final (SprintLab Chatbox)

> Continuação de `4-seccoes-relatorio.md`. Texto em português de Portugal, pronto a colar.

---

## 5. Solução Proposta

### 5.1. Introdução

A solução desenvolvida — **SprintLab Chatbox** — é um assistente conversacional que traz a
gestão e a análise de projetos GitLab para dentro do Microsoft Teams, através de linguagem
natural. O sistema está organizado em duas classes de operação: **consulta** (perguntas
sobre o projeto, gráficos, relatórios, investigação de código) e **ação** (criar, fechar,
atualizar ou apagar issues e gerar código), sendo que toda a ação passa obrigatoriamente
por uma confirmação explícita do utilizador.

A solução está **operacional e implantada**, acessível por URL estável e embebida no Teams.

- **Versão operacional:** Hugging Face Space (Docker) — `https://<utilizador>-<space>.hf.space`
- **Código-fonte:** repositório GitLab/GitHub, com instruções de instalação no `README.md`

### 5.2. Arquitetura

A arquitetura (Figura X) segue um modelo de três camadas. No cliente, o **Microsoft Teams**
embebe a interface através de um *iframe* (autorizado por uma política `Content-Security-Policy`
com `frame-ancestors`). No servidor, um processo Python (`server.py`) — assente unicamente na
**biblioteca padrão** (`http.server`, `urllib`) — serve o *frontend*, expõe a API do chatbox
e faz de intermediário (*proxy*) para dois serviços externos: a **Groq** (inferência do
modelo de linguagem) e a **API REST do GitLab** (dados do projeto).

Quatro decisões de arquitetura sustentam a solução:

- **Multi-tenant por pedido** — a configuração do GitLab (instância, token, projeto) é lida
  de cabeçalhos HTTP (`X-GL-Base / X-GL-Token / X-GL-Project`) a cada pedido e isolada por
  *thread*, permitindo que cada utilizador ligue o seu próprio GitLab sem reiniciar o
  servidor. A cache é segmentada por instância, projeto e impressão digital (*hash*) do token,
  impedindo a mistura de dados entre projetos e entre tokens com permissões diferentes.
- **Concorrência com limites** — o servidor é multi-*thread* (`ThreadingMixIn` da biblioteca
  padrão), pelo que um pedido lento (uma chamada ao LLM) não bloqueia os restantes
  utilizadores; a concorrência é, contudo, limitada (número máximo de ligações simultâneas,
  com resposta 503 imediata quando esgotado, e de pedidos simultâneos por cliente, com 429),
  para que um único cliente não esgote os recursos do servidor.
- **Cache TTL** — as chamadas ao GitLab são memorizadas durante 45 segundos (configurável),
  com um mecanismo *thread-safe* que garante coerência mesmo perante escritas concorrentes;
  a cache tem tamanho limitado e pedidos simultâneos à mesma chave partilham uma única
  chamada ao GitLab (*single-flight*).
- **Camada de segurança** — o token do GitLab do servidor só é usado na instância e no
  projeto do próprio servidor, nunca sendo enviado para outra instância ou projeto indicados
  nos cabeçalhos `X-GL-*`; as escritas feitas com esse token exigem uma **chave de acesso
  partilhada** (`APP_ACCESS_KEY`); e as instâncias personalizadas são validadas contra
  SSRF (apenas `https://` em endereços públicos, com nova verificação do DNS no momento da
  ligação e sem seguir redirecionamentos).

O código está **modularizado**: o `server.py` (ponto de entrada HTTP) e um pacote `src/`
com treze módulos de responsabilidade única (`config`, `gitlab_api`, `cache`,
`rate_limiter`, `threading_server`, `analytics`, `report`, `charts`, `blame`,
`code_commit`, `actions`, `read_tools`, `llm`), o que facilita a manutenção e a
testabilidade.

### 5.3. Tecnologias e ferramentas utilizadas

- **Python (biblioteca padrão)** — linguagem do servidor; sem dependências externas, o que
  permite alojamento gratuito e arranque imediato.
- **Groq — Llama 3.3 70B** — inferência do modelo de linguagem através de uma API compatível
  com OpenAI, com suporte a *function calling* (a base das ações sobre o GitLab) e a
  *streaming* de respostas. Modelo *open-source*, com nível de utilização gratuito.
- **API REST do GitLab (v4)** — fonte de todos os dados e destino de todas as ações:
  issues, milestones, commits, *blame*, *merge requests*, contribuidores e estatísticas.
- **Hugging Face Spaces (Docker)** — alojamento gratuito da aplicação web, com URL público
  e estável (essencial para o embeber no Teams sem reconfigurações).
- **Chart.js** — biblioteca de visualização para os gráficos inline.
- **Microsoft Teams** — plataforma-alvo; integração via *manifest* e separador estático.
- **pytest** — ferramenta de testes (apenas em desenvolvimento).

### 5.4. Implementação

As funcionalidades centrais foram implementadas da seguinte forma:

- **Conversação com ações (NLP + function calling)** — o pedido do utilizador é enviado ao
  modelo com o contexto do GitLab em tempo real e um conjunto de *ferramentas* declaradas.
  Quando o modelo decide agir, **o servidor não executa** — devolve uma *proposta* que o
  *frontend* materializa num cartão de confirmação. A execução só ocorre num segundo pedido,
  após aprovação (ver diagrama de sequência, Figura X). Este desenho elimina ações
  acidentais ou inventadas pelo modelo.
- **Análise determinística** — o relatório do projeto, o painel de estatísticas e os nove
  tipos de gráfico são calculados **em código** a partir dos dados do GitLab; o modelo de
  linguagem nunca conta nem produz números. O relatório adapta-se ao tipo de projeto
  (gerido por issues vs. repositório de código concluído).
- **Investigação de código** — usando a *Blame API* do GitLab, o sistema identifica, de
  forma determinística, o commit e o autor que alteraram uma dada linha; sobre esses factos,
  faz uma única chamada ao LLM para *hipotetizar* a causa de um erro, sempre rotulada como
  análise de IA.
- **Geração de código por IA** — o modelo gera um *plano* (ficheiros + mensagem de commit)
  que é validado no servidor (rejeição de caminhos perigosos — `../`, absolutos, `.git/`,
  `.gitlab-ci.yml` e `.gitlab/` —, limites de tamanho) e
  apresentado ao utilizador; após confirmação, é criada uma *branch* `ai/*`, o commit e um
  *Merge Request* — a *branch* principal nunca é alterada. O commit leva `[skip ci]`, pelo
  que nenhum *pipeline* executa código gerado por IA antes da revisão humana (o revisor
  corre-o a partir do *Merge Request*).
- **Segurança** — as escritas no GitLab feitas com o token do servidor exigem a chave de
  acesso partilhada (`APP_ACCESS_KEY`): o utilizador introdu-la uma única vez em
  ⚙️ Definições, fica guardada apenas nesse navegador (não é incluída nos *backups*) e é
  enviada no cabeçalho `X-App-Key`, sendo comparada no servidor em tempo constante. Se o
  *secret* não estiver definido, essas escritas ficam desativadas (a leitura, a conversa,
  os gráficos e os relatórios continuam a funcionar); um utilizador que adicione um espaço
  de trabalho com o seu próprio token não precisa da chave, porque é o próprio GitLab que
  autoriza. Os identificadores de projeto são validados e codificados no URL, e os
  cartões de confirmação ficam associados ao projeto ativo no momento em que foram
  criados. No *frontend*, todo o HTML dinâmico é escapado e a página aplica uma
  *Content-Security-Policy* (*scripts* apenas do próprio servidor e do jsDelivr, sem
  *scripts inline*) e *Subresource Integrity* nos ficheiros de CDN (Chart.js, Font
  Awesome), mantendo a autorização de `frame-ancestors` para o Teams.
- **Robustez** — cada secção de dados é obtida em paralelo, com um prazo total por pedido
  (um GitLab lento afeta apenas os seus próprios pedidos), e tolerada de forma
  independente; a falha de uma (ex.: um repositório sem *merge requests*) não compromete as
  restantes e é assinalada como indisponível, em vez de ser apresentada como zero ou como
  "projeto vazio". As listas cortadas pelo limite de paginação são assinaladas e os
  respetivos totais apresentados como aproximados ou mínimos. O servidor impõe ainda
  limites ao tamanho do corpo dos pedidos, às ligações simultâneas, à concorrência por
  cliente e à taxa de pedidos por IP (por omissão, 20/min nos *endpoints* que usam o LLM,
  30/min nas escritas e 120/min nas leituras do GitLab), além de *timeouts* de *socket* e
  de prazos totais — para o cliente enviar o pedido e para cada pedido ao GitLab —, de modo
  que uma ligação lenta não prende indefinidamente os recursos do servidor.

### 5.5. Abrangência

Este trabalho mobiliza e integra conteúdos de várias unidades curriculares do curso:
**Inteligência Artificial / Aprendizagem Automática** (uso de modelos de linguagem,
*function calling*, engenharia de *prompts*); **Engenharia de Software** (levantamento de
requisitos, arquitetura modular, testes automatizados); **Redes de Computadores e Sistemas
Distribuídos** (protocolo HTTP, APIs REST, *streaming*, concorrência, cache); **Interação
Humano-Máquina** (desenho da interface, *mockups*, acessibilidade, temas); **Bases de Dados**
(modelação das entidades do GitLab e agregação de dados); e princípios de **Segurança de
Software** (validação de input, ações sob confirmação, análise de vulnerabilidades).

---

## 6. Plano de Testes e Avaliação

A avaliação da solução combinou **validação automática** (uma suíte de testes que corre sem
intervenção humana) com **validação funcional** (cenários reais executados manualmente). A
abordagem automática é deliberada: garante que a lógica crítica permanece correta a cada
alteração, sem depender de testes manuais repetidos.

### 6.1. Testes automatizados

Foram desenvolvidos **506 testes** (*pytest*), executados sem acesso à rede (o GitLab
e o modelo de linguagem são substituídos por *fakes*), em cerca de meio minuto. Organizam-se em
dois níveis:

- **Unitários** — sobre a lógica pura (funções sem efeitos de rede): sanitização de input,
  agregação de estatísticas, geração do relatório, *blame*, visão geral do repositório.
- **Integração** — um **servidor HTTP real** é arrancado numa *thread* e todas as rotas são
  exercitadas de ponta a ponta; apenas as funções de rede de mais baixo nível (pedidos ao
  GitLab e chamadas à Groq) são simuladas — o *parser* SSE real é exercitado com um
  *stream* simulado —, pelo que todo o restante código (rotas, cache, agregação) é o de
  produção.

A estes somam-se **49 casos** de *routing* que verificam a deteção de intenções do
*frontend* (`node tests/route_harness.mjs`), usando as funções reais do `app.js`.

| # | Categoria de teste | Objetivo | Resultado |
|---|---|---|---|
| 1 | Sanitização de input | Garantir que números de issue, datas e *labels* inválidos são rejeitados/normalizados | ✅ Passou |
| 2 | Determinismo dos números | Confirmar que o total de commits/issues vem do GitLab e é coerente entre chat, painel e relatório | ✅ Passou |
| 3 | Geração do relatório | Verificar a adaptação ao tipo de projeto (ativo vs. concluído) e a ausência de progresso enganador | ✅ Passou |
| 4 | Investigação de código | Confirmar a correta atribuição do commit/autor a cada linha (*blame*) e a truncagem de *diffs* | ✅ Passou |
| 5 | Segurança do commit por IA | Rejeitar caminhos perigosos (`../`, `.git/`, absolutos, `.gitlab-ci.yml`, `.gitlab/`) e planos malformados | ✅ Passou |
| 6 | Endpoints HTTP | Exercitar estáticos, estatísticas, relatório, exportações, gráficos e tratamento de erros | ✅ Passou |
| 7 | Isolamento multi-tenant | Confirmar que o cabeçalho `X-GL-Project` muda o projeto-alvo, que o *default* é usado sem cabeçalho e que o token do servidor nunca é enviado para outra instância ou projeto | ✅ Passou |
| 8 | Escritas com confirmação | Verificar que `/api/confirm-action` rejeita ferramentas inválidas e executa as válidas | ✅ Passou |
| 9 | Fluxo do commit por IA | Validar a criação de *branch* + commit (com `[skip ci]`) + *Merge Request* contra um GitLab simulado | ✅ Passou |
| 10 | *Rate-limit* | Confirmar o bloqueio após o limite e a degradação graciosa | ✅ Passou |
| 11 | Autorização das escritas | Confirmar que as escritas com o token do servidor exigem a chave de acesso correta (`X-App-Key`), ficam desativadas sem o *secret* e que um espaço de trabalho com token próprio não precisa da chave | ✅ Passou |
| 12 | SSRF / validação de instâncias | Rejeitar instâncias `http://` ou que resolvam para endereços internos (também no momento da ligação), recusar redirecionamentos e validar/codificar o identificador do projeto | ✅ Passou |
| 13 | Isolamento da cache por token | Confirmar que tokens diferentes não partilham entradas da cache e que uma escrita invalida a vista do projeto para todos os tokens | ✅ Passou |
| 14 | Limites e robustez | Verificar o limite do corpo do pedido (413), o 503 com o servidor cheio, o limite de concorrência por cliente (429), os limites de leituras/escritas por IP, os prazos totais (clientes lentos e pedidos ao GitLab), os limites de bytes e o reporte de falhas como indisponíveis | ✅ Passou |
| 15 | *Parser* SSE real | Exercitar o *parser* de *streaming* real com um *stream* simulado (texto, *tool calls* fragmentadas, erros a meio) | ✅ Passou |
| 16 | *Routing* do *frontend* | Verificar para onde cada frase é encaminhada pela deteção de intenções do `app.js` | ✅ Passou |

### 6.2. Validação funcional em cenários reais

A solução foi exercitada manualmente contra **quatro instâncias GitLab distintas**,
cobrindo tipos de projeto diferentes (gestão por issues e milestones vs. repositórios só de
código):

| Cenário | Objetivo | Resultado |
|---|---|---|
| Projeto SprintLab | Gestão de issues e milestones reais (criar, fechar, atualizar, gráficos) | ✅ Funcional |
| 3 GitLabs internos da GMV | Validar a ligação a instâncias corporativas distintas (parceiro industrial) | ✅ Funcional |
| Espelho do repositório AIR (2318 commits) | Validar análise de histórico, contribuidores e *blame* sobre um codebase real | ✅ Funcional |
| Projeto Java importado (lp2-jogo) | Validar a deteção de tipo de projeto (código, sem issues) e o relatório adaptativo | ✅ Funcional |

Durante a validação foram identificadas e corrigidas melhorias, como a discrepância entre o
total de commits do painel e o do chat (resolvida fixando a *contagem canónica* do GitLab) e
a inadequação do relatório a projetos sem issues (resolvida com um relatório adaptativo).

---

## 7. Método e Planeamento

O projeto seguiu uma metodologia **iterativa e incremental**: a cada fase, uma funcionalidade
era especificada, implementada, testada e validada com dados reais antes de avançar. O
calendário (Figura X / Tabela X, em anexo) organizou o trabalho em fases de levantamento,
arquitetura, implementação do núcleo conversacional, funcionalidades de análise e, por fim,
as funcionalidades avançadas (investigação de código e geração de commits).

**Desvios face ao planeamento inicial** (e respetiva justificação):

- **Mudança de infraestrutura de inferência** — a abordagem inicial assentava num modelo
  local (Ollama) exposto por um túnel (ngrok). Optou-se por migrar para a **Groq** (inferência
  alojada, gratuita) servida a partir de um **Hugging Face Space**, eliminando a dependência
  da máquina do autor e obtendo um URL público e estável — requisito para a integração com o
  Teams. Esta mudança melhorou a fiabilidade e a viabilidade da solução.
- **Funcionalidades acrescentadas além do plano** — em resposta ao acompanhamento do
  orientador e do parceiro (GMV), foram adicionadas a **arquitetura multi-tenant**, o
  **relatório automático**, a **investigação de código (blame)** e a **geração de commits por
  IA**. Apesar de acrescentarem trabalho, mantiveram-se dentro dos objetivos de gestão e
  análise de GitLab definidos no início.
- **Esforço subestimado na fase de testes** — a construção da suíte de testes de integração
  (servidor real + *fakes*) exigiu mais tempo do que o previsto, mas passou a apanhar
  regressões automaticamente a cada alteração, em vez de depender de testes manuais.
- **Revisão final de qualidade e segurança** — na fase final (de 17 de setembro a 1 de
  outubro de 2026), foi realizada uma revisão sistemática do código e da segurança,
  apoiada por ferramentas de análise assistidas por IA, que identificou 77 problemas
  (3 críticos, 2 altos e os restantes médios ou baixos) — por exemplo, escritas não
  autenticadas com o token do servidor, o envio desse token para servidores indicados pelo
  cliente, um identificador de projeto não codificado no URL e uma cache partilhada entre
  tokens. Todos foram corrigidos e cobertos por novos testes; esta fase permitiu
  implementar a proteção das escritas e a proteção SSRF, inicialmente adiadas. Três rondas
  de verificação independente sobre o código já corrigido encontraram mais 49 problemas
  (2 altos, os restantes médios ou baixos), na maioria ligados à robustez perante clientes
  ou instâncias GitLab lentas ou hostis — também corrigidos e testados. A lição: uma
  correção de segurança também precisa de ser verificada, porque duas delas tinham
  introduzido problemas novos.

O esforço foi crescente ao longo do projeto, com maior intensidade nas fases de implementação
das funcionalidades avançadas e de consolidação dos testes.

---

## 9. Conclusão e Trabalhos Futuros

O trabalho cumpriu o objetivo central: mostrar que a gestão e a análise de projetos GitLab
podem ser feitas a partir do Microsoft Teams, em linguagem natural, sem o utilizador ter de
sair do chat — com os números a virem do GitLab e as ações sempre sob confirmação. A solução
está operacional, é validada por 506 testes automatizados (e 49 casos de
*routing* do *frontend*) e foi exercitada contra quatro
instâncias GitLab, incluindo três da GMV (parceiro industrial).

O projeto segue uma regra de design clara:
a IA nunca entrega um facto que o utilizador tenha de verificar (os números são do GitLab),
nem uma ação que tenha de desfazer (toda a escrita é confirmada e as ações irreversíveis, como
apagar uma issue, são assinaladas como tal no cartão de confirmação). Este princípio distingue
a solução de assistentes que geram trabalho de revisão (*workslop*).

**Trabalhos futuros:**

- **RAG sobre documentos** — substituir o contexto estático por recuperação semântica sobre
  o(s) documento(s) do projeto, permitindo responder a perguntas sobre o relatório/wiki.
- **Persistência server-side** — migrar os espaços de trabalho e o histórico do `localStorage`
  para uma base de dados (ex.: PostgreSQL), permitindo acesso multi-dispositivo.
- **Autenticação individual e SSO** — as escritas com o token do servidor estão hoje
  protegidas por uma chave de acesso partilhada; o passo seguinte é a autenticação
  individual de cada utilizador (idealmente SSO do Teams), permitindo permissões e
  rastreabilidade por pessoa em ambientes partilhados.
- **Sincronização bidirecional e Kanban/Gantt** — concretizar a visão alargada do SprintLab,
  com o *middleware* de sincronização GitLab↔Teams e as vistas de planeamento.
- **Notificações em tempo real** — via *webhooks* do GitLab, levando eventos (nova issue, MR)
  proactivamente ao Teams.
