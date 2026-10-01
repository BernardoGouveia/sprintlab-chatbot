# 4.1. Levantamento e Análise de Requisitos

Para o levantamento de requisitos foi utilizada a metodologia **MoSCoW**, que classifica
cada requisito em **Must Have** (essencial ao funcionamento base), **Should Have**
(importante mas não crítico), **Could Have** (melhoria desejável) e **Won't Have**
(possível extensão fora do âmbito deste trabalho). A coluna **Esforço** estima a
complexidade de desenvolvimento (S/M/L/XL) e a coluna **Estado** indica o que foi
efetivamente implementado: **I** (*Implemented*), **PI** (*Partially Implemented*) e
**NI** (*Not Implemented*).

> Os requisitos `NI`/`PI` correspondem a decisões deliberadas de âmbito (ver secção
> *Viabilidade — Proposto vs Desenvolvido*): a autenticação individual (login/SSO) foi
> adiada por se tratar de uma demonstração académica, ficando as escritas com o token do
> servidor protegidas por uma **chave de acesso partilhada** (daí FR-22 e NFR-14 em `PI`);
> a sincronização bidirecional e o Kanban/Gantt pertencem à visão alargada do SprintLab,
> fora do âmbito da componente *chatbox* aqui desenvolvida.

## Tabela 1 — Requisitos Funcionais (FR)

| ID | Descrição | MoSCoW | Esforço | Estado |
|----|-----------|--------|:------:|:-----:|
| FR-1 | O sistema deve responder a perguntas sobre o projeto em **linguagem natural** (issues, progresso, contagens, autores) | Must Have | L | I |
| FR-2 | O sistema deve permitir **criar** uma issue através de um formulário estruturado, com confirmação | Must Have | M | I |
| FR-3 | O sistema deve permitir **fechar** uma issue por linguagem natural, mediante cartão de confirmação | Must Have | M | I |
| FR-4 | O sistema deve permitir **atualizar/editar** uma issue, abrindo um formulário pré-preenchido | Must Have | M | I |
| FR-5 | O sistema deve permitir **apagar** uma issue, com cartão de confirmação destrutivo (irreversível) | Should Have | S | I |
| FR-6 | O sistema deve gerar **gráficos** inline: estado, por *assignee*, por *label*, por *milestone*, *burndown*, *cycle-time*, commits e MRs por autor | Must Have | L | I |
| FR-7 | O sistema deve **exportar as issues** para CSV (compatível com Excel) | Should Have | S | I |
| FR-8 | O sistema deve **exportar os commits** para CSV (histórico completo) | Could Have | S | I |
| FR-9 | O sistema deve gerar um **relatório do projeto** determinístico, adaptado ao tipo de projeto (ativo/concluído) | Must Have | L | I |
| FR-10 | O sistema deve **identificar o commit e o autor** que alterou uma dada linha de código (*git blame*) | Should Have | M | I |
| FR-11 | O sistema deve apresentar uma **análise IA** do possível erro, por cima dos factos do *blame* | Could Have | M | I |
| FR-12 | O sistema deve, a pedido, **gerar código e propô-lo como commit** numa *branch* `ai/*` + *Merge Request* (nunca na branch principal) | Could Have | XL | I |
| FR-13 | O sistema deve suportar **vários GitLab** (multi-tenant), cada um como um espaço de trabalho próprio | Must Have | L | I |
| FR-14 | O sistema deve permitir **trocar de modelo LLM** (5 modelos disponíveis) | Should Have | S | I |
| FR-15 | O sistema deve apresentar **sugestões de seguimento** contextuais após cada resposta | Could Have | M | I |
| FR-16 | O sistema deve **detetar issues duplicadas** ao criar uma nova | Could Have | S | I |
| FR-17 | O sistema deve apresentar um **painel de estatísticas** (issues, commits, contribuidores, MRs, milestones) | Should Have | M | I |
| FR-18 | O sistema deve **descrever o projeto** a partir do README, linguagens e estrutura do repositório | Should Have | M | I |
| FR-19 | O sistema deve permitir **cópia de segurança (backup/restauro)** dos espaços de trabalho e conversas | Could Have | S | I |
| FR-20 | O sistema deve poder ser **embebido no Microsoft Teams** (separador/tab) | Must Have | M | I |
| FR-21 | O sistema deve oferecer **temas de aparência** (escuro, claro, preto OLED) | Could Have | S | I |
| FR-22 | O sistema deve **autenticar utilizadores** (login) antes de operações de escrita | Won't Have | M | PI |
| FR-23 | O sistema deve responder a perguntas sobre o **documento/relatório** do projeto via RAG | Won't Have | L | PI |
| FR-24 | O sistema deve fazer **sincronização bidirecional** GitLab↔Teams | Won't Have | XL | NI |

> **Notas de honestidade:**
> FR-23 (`PI`): o conhecimento do documento é injetado como contexto estático
> (`DOCUMENT_CONTEXT`), funcional para o SprintLab; o RAG genérico sobre PDFs não foi
> implementado. FR-22 (`PI`): não existe login individual nem SSO; as escritas que usam o
> token GitLab do servidor exigem uma **chave de acesso partilhada** (`APP_ACCESS_KEY`),
> introduzida uma vez nas ⚙️ Definições e guardada apenas nesse browser — sem a chave
> configurada no servidor, essas escritas ficam desligadas; quem adiciona um espaço de
> trabalho com o seu próprio token é autorizado pelo próprio GitLab. FR-24 (`NI`): fora do
> âmbito da componente *chatbox*.

## Tabela 2 — Requisitos Não Funcionais (NFR)

| ID | Área | Descrição | Esforço | Estado |
|----|------|-----------|:------:|:-----:|
| NFR-1 | Fiabilidade | **Determinismo**: todos os números (commits, issues, progresso) vêm do GitLab — a IA nunca os conta nem inventa | M | I |
| NFR-2 | Compatibilidade | **Multi-tenant isolado**: a cache é segmentada por instância+projeto+token (impressão digital do token), sem mistura de dados entre GitLabs nem entre tokens | M | I |
| NFR-3 | Desempenho | A resposta do chat é entregue em **streaming** (SSE), token-a-token | M | I |
| NFR-4 | Disponibilidade | **Degradação graciosa**: cada secção de dados é tolerada de forma independente (uma falha não parte o todo) | M | I |
| NFR-5 | Portabilidade | **Sem dependências externas** (apenas biblioteca padrão de Python) → alojamento gratuito | L | I |
| NFR-6 | Confiança | **Proveniência visível**: o que é facto (GitLab) é distinguido do que é hipótese da IA (anti-*workslop*) | S | I |
| NFR-7 | Segurança | Todas as **escritas exigem confirmação** explícita do utilizador antes de executar | M | I |
| NFR-8 | Segurança | **Proteção contra XSS** (todo o HTML dinâmico escapado, validação de URLs, *Content-Security-Policy* sem *scripts inline* e *Subresource Integrity* nos ficheiros da CDN) e **validação de caminhos** no commit por IA (bloqueia `../`, caminhos absolutos, `.git/`, `.gitlab/` e `.gitlab-ci.yml`; commit com `[skip ci]`) | M | I |
| NFR-9 | Desempenho | **Cache TTL** *thread-safe* (45s) para reduzir chamadas repetidas ao GitLab | M | I |
| NFR-10 | Desempenho | **Concorrência**: servidor multi-thread (um pedido lento não bloqueia os outros) | S | I |
| NFR-11 | Qualidade | **506 testes automatizados** (unitários + integração HTTP, GitLab e Groq simulados) e **49 casos de encaminhamento** do frontend, sem rede, em cerca de meio minuto | L | I |
| NFR-12 | Portabilidade | **Configuração por variáveis de ambiente** + imagem **Docker** | M | I |
| NFR-13 | Disponibilidade | **Rate-limit por IP** nos endpoints que consomem o LLM (proteção da quota gratuita), nas escritas e nas leituras ao GitLab, e **limites de concorrência** (ligações simultâneas → 503 imediato; pedidos simultâneos por cliente → 429) | S | I |
| NFR-14 | Segurança | **Proteção contra SSRF** (instâncias personalizadas só em `https://` e endereços públicos — incluindo endereços IPv4 escondidos em formas IPv6 —, DNS re-verificado na ligação contra *DNS rebinding*, ids de projeto validados e codificados, redirecionamentos recusados); **autenticação individual/SSO** não implementada | L | PI |
| NFR-15 | Segurança | **Autorização das escritas**: as escritas com o token GitLab do servidor exigem a **chave de acesso** (`APP_ACCESS_KEY`, cabeçalho `X-App-Key`, comparação em tempo constante) e ficam desligadas se esta não estiver configurada; o token do servidor nunca é enviado a outra instância ou projeto indicado pelo cliente | M | I |
| NFR-16 | Disponibilidade | **Limites de recursos**: tamanho máximo do corpo do pedido, *timeouts* de *socket*, prazo total para o cliente enviar o pedido (clientes lentos não prendem ligações), prazo total e tamanho máximo de cada resposta do GitLab, prazo total para as leituras paralelas ao GitLab (um GitLab lento só afeta os seus pedidos) e teto de *threads* de trabalho; cache limitada em entradas e em bytes, com *single-flight* | M | I |

---

*Total: 24 requisitos funcionais (21 I, 2 PI, 1 NI) e 16 requisitos não funcionais
(15 I, 1 PI). Taxa de implementação dos requisitos Must/Should Have: 100%.*
