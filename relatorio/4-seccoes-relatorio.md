# Secções de texto para o Relatório Final — SprintLab Chatbox

> Texto pronto a colar no relatório (português de Portugal). Ajusta nomes,
> percentagens do teu questionário e referências às figuras conforme necessário.

---

## 1. Identificação do Problema

A gestão de projetos de software em equipas que utilizam o **GitLab** depende de um fluxo
de trabalho que vive, na sua maioria, fora das ferramentas de comunicação do dia-a-dia.
Para consultar o estado de um projeto, criar ou fechar uma *issue*, ou perceber quem
alterou determinada linha de código, o utilizador tem de **sair da sua ferramenta de
conversação (Microsoft Teams), abrir o GitLab no navegador, navegar até ao projeto e
executar manualmente a ação**. Este contexto fragmentado origina três problemas:

1. **Perda de eficiência** — cada consulta ou ação implica uma mudança de contexto
   (Teams → navegador → GitLab → de volta), interrompendo o trabalho.
2. **Barreira de conhecimento técnico** — a navegação no GitLab e a interpretação dos seus
   dados (gráficos, estatísticas, histórico de commits) exige familiaridade com a
   plataforma, o que afasta perfis menos técnicos da informação do projeto.
3. **Informação dispersa e por interpretar** — perceber "o que mudou", "quem está
   sobrecarregado" ou "qual o estado real do projeto" exige cruzar manualmente várias
   páginas do GitLab.

No contexto do parceiro **GMV** — empresa do setor aeroespacial que utiliza o GitLab
internamente — estas ineficiências multiplicam-se pelo número de colaboradores e projetos.
A solução proposta neste trabalho, o **SprintLab Chatbox**, ataca diretamente este
problema: traz a gestão e a análise do GitLab **para dentro do Teams, através de linguagem
natural**, eliminando a mudança de contexto e a barreira técnica.

---

## 2. Benchmarking

Existem soluções que tocam partes do problema, mas nenhuma cobre a sua totalidade com a
profundidade da solução proposta:

- **Integração nativa GitLab ↔ Teams** — limita-se a *notificações* (avisa que algo mudou),
  não permite **agir** sobre o GitLab nem **analisar** o projeto.
- **Assistentes de IA genéricos (ex.: ChatGPT)** — sabem falar sobre código, mas **não têm
  acesso ao projeto GitLab real**: não conhecem as issues, os commits nem o estado atual,
  pelo que qualquer número que produzam é uma invenção.
- **Bots de Teams/Slack baseados em comandos** — executam comandos rígidos (`/issue close 5`),
  sem compreensão de linguagem natural nem análise contextual.

A solução proposta distingue-se por **combinar**, num único produto: compreensão de
**linguagem natural (NLP)**, **acesso em tempo real** ao GitLab (issues, commits, *blame*,
MRs), **análise determinística** (gráficos e relatórios cujos números são factos do GitLab,
não gerados por IA), e **ações seguras** (criar/fechar/atualizar issues e até propor commits
de código, sempre sob confirmação humana).

| Funcionalidade | Notif. nativa | IA genérica | Bot de comandos | **SprintLab** |
|---|:--:|:--:|:--:|:--:|
| Linguagem natural | ✗ | ✓ | ✗ | **✓** |
| Acesso ao GitLab real | ✓ | ✗ | ✓ | **✓** |
| Análise determinística (gráficos/relatórios) | ✗ | ✗ | parcial | **✓** |
| Ações sobre issues com confirmação | ✗ | ✗ | ✓ | **✓** |
| Investigação de código (git blame + IA) | ✗ | ✗ | ✗ | **✓** |
| Geração de código → commit/MR sob revisão | ✗ | ✗ | ✗ | **✓** |
| Multi-projeto / multi-GitLab | ✗ | — | parcial | **✓** |
| Embebido no Teams | ✓ | ✗ | ✓ | **✓** |

Acrescente-se um fator de custo: a solução foi desenhada **sem dependências externas**
(apenas a biblioteca padrão de Python) e com inferência num modelo *open-source* (Llama 3.3
70B via Groq, com nível gratuito), o que permite alojamento **gratuito** (Hugging Face
Spaces) — um critério relevante para adoção à escala de uma organização.

---

## 3. Viabilidade e Pertinência — *Proposto vs Desenvolvido*

A pertinência da solução foi sustentada por um inquérito a potenciais utilizadores, cujos
resultados (Anexo 1) confirmam o interesse: **85%** consideraram que uma ferramenta deste
tipo melhoraria a eficiência, **90%** manifestaram interesse na automação de tarefas de
gestão, e **70%** consideraram a visualização do estado do projeto (Kanban/Gantt)
essencial. *(Ajustar às percentagens reais do teu questionário.)*

### Proposto inicialmente

A visão inicial do **SprintLab** descrevia um produto alargado: um *middleware* (Express.js)
com **sincronização bidirecional** de issues entre GitLab e Teams, painéis **Kanban e
Gantt**, persistência em **PostgreSQL**, e um motor de **IA conversacional**.

### O que foi desenvolvido

No âmbito deste trabalho foi desenvolvida e validada a **componente de IA conversacional** —
o **SprintLab Chatbox** — que constitui o núcleo diferenciador da visão. Esta componente
foi levada além do proposto, incluindo funcionalidades não inicialmente previstas:

- **Análise determinística** (relatório do projeto, painel de estatísticas, 9 tipos de
  gráfico) — números garantidamente corretos, sem alucinação;
- **Investigação de código** (*git blame* + análise IA do erro);
- **Geração de código por IA** com fluxo seguro (*branch* + *Merge Request*);
- **Arquitetura multi-tenant** — funciona com qualquer GitLab acessível por `https://`
  (instâncias em redes internas apenas como instância predefinida do servidor), validada
  contra **quatro instâncias reais** (o projeto SprintLab, três GitLabs internos da GMV e um
  espelho do repositório *open-source* AIR, com 2318 commits).

As componentes de *middleware*, sincronização bidirecional e Kanban/Gantt **mantêm-se como
trabalho futuro** (ver Conclusão), por excederem o âmbito temporal desta fase. Esta
delimitação não compromete a viabilidade: a componente desenvolvida é **operacional,
testada e implantada**, e a arquitetura foi pensada para integrar as restantes.

> **Decisões de âmbito conscientes:** a proteção das escritas e o endurecimento de
> segurança foram implementados: as escritas no GitLab feitas com o token do servidor
> exigem uma **chave de acesso partilhada** (sem ela ficam desativadas), o token do
> servidor nunca é enviado para outra instância ou projeto, e as instâncias GitLab
> personalizadas estão protegidas contra SSRF (apenas `https://` em endereços públicos).
> Foi deliberadamente adiada a **autenticação individual/SSO** (identificação de cada
> utilizador, idealmente através do Teams), por se tratar de uma demonstração académica
> num ambiente controlado, não de um produto em produção: a chave partilhada controla
> quem pode escrever, mas não identifica cada pessoa.

---

## 8. Resultados

A solução desenvolvida está **operacional e implantada** (Hugging Face Space, em formato
Docker), acessível por URL estável e embebida no Microsoft Teams. Todas as funcionalidades
Must/Should Have (Tabela 1) foram implementadas (taxa de 100%).

Os resultados foram validados de três formas complementares:

1. **Validação automática** — uma suíte de **506 testes** (unitários sobre a lógica
   pura e de **integração** sobre o servidor HTTP real), executados sem acesso à rede em
   cerca de meio minuto, complementada por **49 casos** de *routing* das intenções do
   *frontend*. Cobrem a sanitização de input, o determinismo dos números, todos os
   *endpoints*, o isolamento multi-tenant, as guardas de segurança das escritas (incluindo
   a chave de acesso e a proteção SSRF) e os limites de carga do servidor.
2. **Validação funcional em cenários reais** — a solução foi exercitada contra **quatro
   GitLabs distintos**, de tipos diferentes:
   - O projeto **SprintLab**, com issues e milestones reais;
   - **Três GitLabs internos da GMV** (parceiro industrial);
   - Um espelho do repositório **AIR** (*open-source*, 2318 commits, 35 autores), que
     validou as funcionalidades de análise de histórico e *blame* sobre um codebase real.
3. **Revisão sistemática de código e segurança** — na fase final, uma revisão sistemática,
   apoiada por ferramentas de análise assistidas por IA, identificou **77 problemas**
   (3 críticos, 2 altos e os restantes médios ou baixos) — por exemplo, escritas não
   autenticadas com o token do servidor, o envio desse token para servidores indicados
   pelo cliente, um identificador de projeto não codificado no URL e uma cache partilhada
   entre tokens. Todos foram corrigidos e cobertos por novos testes. Seguiram-se **três
   rondas de verificação independente** sobre o código já corrigido, cada uma com
   reprodução de cada problema antes de o aceitar, que encontraram mais **49 problemas**
   (2 altos — por exemplo, um caminho de ficheiro enorme capaz de ocupar o CPU do
   servidor e o prazo total dos pedidos ao GitLab que não se aplicava a ligações HTTPS —
   e os restantes médios ou baixos), sobretudo ligados à resistência a clientes ou a
   instâncias GitLab lentas ou hostis. Também foram todos corrigidos e testados; na
   última ronda já só surgiram problemas de gravidade baixa.

Demonstrou-se que o sistema **diferencia corretamente o tipo de projeto** (gerido por
issues vs. repositório de código), adaptando o relatório em conformidade; que os números
apresentados **coincidem sempre** entre o chat, o painel e o relatório (por serem o mesmo
facto do GitLab); e que as ações de escrita — incluindo a geração de código — **nunca
ocorrem sem confirmação** e **nunca alteram a branch principal**.

Em termos de segurança e robustez, a versão final garante que: as escritas com o token do
servidor exigem a **chave de acesso partilhada** e ficam desativadas se esta não estiver
configurada (a leitura, a conversa, os gráficos e os relatórios continuam disponíveis); o
token do servidor **só é usado na instância e no projeto do próprio servidor**; as
instâncias personalizadas são restritas a `https://` em endereços públicos (proteção SSRF)
e os redirecionamentos HTTP são recusados; a cache é segmentada por instância, projeto e
token; os commits por IA levam `[skip ci]` e não podem alterar a configuração de CI; a
página aplica uma *Content-Security-Policy* e *Subresource Integrity* nos recursos de CDN;
e o servidor impõe limites ao tamanho dos pedidos, às ligações simultâneas, à concorrência
por cliente e à taxa de pedidos por IP, e prazos totais ao envio de cada pedido pelo cliente
e aos pedidos ao GitLab, pelo que uma ligação lenta ou um GitLab lento afetam apenas os seus
próprios pedidos.

---

## (Secção diferenciadora) — As soluções IA não são *workslop*

> *"Workslop"* designa o output de IA que aparenta ser trabalho útil mas que, na prática,
> transfere para o destinatário o esforço escondido de o verificar e corrigir.

A solução foi desenhada, por princípio, contra este fenómeno. A regra de design transversal
é: **a IA nunca entrega um facto que o utilizador tenha de verificar, nem uma ação que tenha
de desfazer.** Concretamente:

- **Os factos vêm do GitLab, não da IA.** O relatório do projeto, o painel de estatísticas
  e os gráficos são **100% determinísticos** — os números são calculados em código a partir
  da API do GitLab; o modelo de linguagem **nunca conta nada**. Não há, portanto, nada para
  o utilizador conferir.
- **Facto e hipótese são visualmente separados.** Na investigação de código, os factos do
  *git blame* (commit, autor, data — verificáveis, com ligação direta ao GitLab) são
  apresentados numa zona distinta da **hipótese** gerada pela IA, claramente rotulada como
  tal. O sistema está instruído a afirmar *"não há indício de erro"* em vez de inventar.
- **As ações são propostas, não impostas.** Toda a escrita (incluindo a geração de código)
  passa por um cartão de confirmação com pré-visualização; o código gerado por IA vai para
  uma *branch* separada e um *Merge Request* — a revisão é **estruturada e explícita**, não
  despejada sobre o utilizador, e a branch principal nunca é tocada sem aprovação humana.

Cada um destes três artefactos exibe, na própria interface, um rodapé de **proveniência**
que torna explícita a fronteira entre o que é facto e o que é interpretação da IA. Desta
forma, o sistema **reduz** o trabalho do utilizador em vez de lhe acrescentar trabalho de
revisão — que é, precisamente, a definição inversa de *workslop*.
