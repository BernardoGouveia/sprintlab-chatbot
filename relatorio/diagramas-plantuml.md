# Diagramas em PlantUML

Código-fonte PlantUML de cada diagrama. **Como usar:** copia cada bloco (de `@startuml` a
`@enduml`) e cola em:
- **https://www.plantuml.com/plantuml/uml/** (renderiza online, exporta PNG/SVG), ou
- a extensão **PlantUML** no VS Code (pré-visualização local), ou
- **draw.io** (Arrange → Insert → Advanced → PlantUML).

> Vantagem do PlantUML: produz diagramas com o aspeto "UML standard" que os avaliadores
> reconhecem, e são fáceis de editar (é texto). Os ficheiros `.svg` que já tens servem se
> preferires o visual personalizado; estes são a alternativa editável.

---

## 1. Diagrama de Casos de Uso

```plantuml
@startuml casos-de-uso
left to right direction
skinparam packageStyle rectangle
skinparam shadowing false

actor "Utilizador\n(docente / gestor)" as U

rectangle "SprintLab Chatbox" {
  usecase "Consultar o projeto\n(linguagem natural)" as UC1
  usecase "Gerar relatório\ne gráficos" as UC2
  usecase "Gerir issues\n(criar/fechar/atualizar/apagar)" as UC3
  usecase "Investigar código\n(git blame + análise IA)" as UC4
  usecase "Gerar e committar código\n(branch + Merge Request)" as UC5
  usecase "Configurar GitLab\n(multi-tenant)" as UC6
}

actor "GitLab API" as GL
actor "Groq (IA)" as GQ

U --> UC1
U --> UC2
U --> UC3
U --> UC4
U --> UC5
U --> UC6

UC1 ..> GQ
UC4 ..> GQ
UC5 ..> GQ
UC2 ..> GL
UC3 ..> GL
UC6 ..> GL
@enduml
```

---

## 2. Diagrama de Sequência — Ação com confirmação

```plantuml
@startuml sequencia
skinparam shadowing false
actor Utilizador as U
participant "Chatbox (UI)" as C
participant "Servidor" as S
participant "Groq (IA)" as G
participant "GitLab API" as GL

U -> C : «dá a issue #5 como terminada»
C -> S : POST /api/chat (+ contexto)
S -> G : prompt + ferramentas
G --> S : tool_call: close_issue(5)
S --> C : propõe ação (NÃO executa)
C -> U : cartão de confirmação

note over U, GL #FFF8E1 : Ponto de controlo humano —\nnada é escrito no GitLab antes desta aprovação

U -> C : confirma
C -> S : POST /api/confirm-action\n(+ chave de acesso, cabeçalho X-App-Key)
S -> S : valida a chave de acesso\n(comparação em tempo constante)
alt chave válida
  S -> GL : PUT issues/5 (close)
  GL --> S : 200 OK
  S --> C : resultado
  C -> U : «issue #5 fechada»
else chave em falta ou inválida
  S --> C : 403 — nada é escrito no GitLab
  C -> U : erro + «Introduzir a chave de acesso»\n(o cartão mantém-se para confirmar de novo)
end
note right of S
  A chave (secret APP_ACCESS_KEY) só é exigida nas escritas
  com o token do servidor; um workspace com token GitLab
  próprio é autorizado pelo próprio GitLab.
end note
@enduml
```

> Frases simples como «fecha a issue 5» são reconhecidas diretamente pelo *frontend*, que abre
> o mesmo cartão sem passar pelo modelo (sem `POST /api/chat` nem chamada à Groq); o passo de
> confirmação é idêntico.

---

## 3. Diagrama de Atividade — Processo As-Is (atual)

```plantuml
@startuml bpmn-as-is
skinparam shadowing false
title Processo atual — Gerir o GitLab manualmente
start
:Sair do Teams, abrir o navegador;
:Autenticar e navegar no GitLab;
:Localizar o projeto / a issue;
:Consultar ou alterar manualmente;
:Regressar ao Teams para comunicar;
stop
note right
  4 mudanças de contexto
  Processo fragmentado,
  fora do Teams
end note
@enduml
```

---

## 4. Diagrama de Atividade — Processo To-Be (com o SprintLab)

```plantuml
@startuml bpmn-to-be
skinparam shadowing false
title Processo com o SprintLab Chatbox (no Teams)
start
:Escrever em linguagem natural no chatbox;
if (Ação de escrita?) then (não — consulta)
  :Resposta com dados em tempo real do GitLab;
  stop
else (sim — escrita)
  :Cartão de confirmação (pré-visualização);
  if (Confirma?) then (sim)
    :Executa no GitLab\nou cria branch + Merge Request;
    :Resultado apresentado inline;
    stop
  else (não)
    :Cancelado — nada alterado;
    stop
  endif
endif
@enduml
```

---

## 5. Diagrama de Arquitetura (componentes / *deployment*)

```plantuml
@startuml arquitetura
skinparam componentStyle rectangle
skinparam shadowing false

actor "Utilizador" as U

node "Microsoft Teams" as Teams {
  component "Separador (iframe)" as Tab
}

node "Hugging Face Space\n(Docker · porta 7860)" as HF {
  component "Frontend\n(chatbox.html · style.css · app.js)" as FE
  component "server.py\n(HTTP · SSE · chave de acesso nas escritas · limites por IP)\n(X-GL-* validados; token do servidor só na sua instância)" as SRV
  package "src/ (stdlib)" {
    component "config"
    component "gitlab_api"
    component "cache"
    component "rate_limiter"
    component "threading_server"
    component "analytics"
    component "report"
    component "charts"
    component "blame"
    component "code_commit"
    component "actions"
    component "read_tools"
    component "llm"
  }
}

cloud "Groq API\nLlama 3.3 70B" as Groq
cloud "GitLab REST API v4" as GitLab

U --> Tab
Tab --> FE : HTTPS (iframe)
FE --> SRV : HTTP / SSE
SRV --> Groq : HTTPS (inferência)
SRV --> GitLab : HTTPS (dados + ações)

note bottom of HF
  Factos (números, blame, histórico) vêm do GitLab — determinísticos.
  A IA (Groq) só interpreta e propõe.
end note
@enduml
```

---

## 6. (Extra) Modelo de dados — entidades do GitLab usadas

> Para a secção 4.4 (Modelos relevantes). O SprintLab não tem base de dados própria —
> consome as entidades do GitLab. Este diagrama de classes documenta-as.

```plantuml
@startuml modelo-dados
skinparam shadowing false
hide circle
skinparam classAttributeIconSize 0

class Projeto {
  id
  name_with_namespace
  default_branch
  statistics.commit_count
}
class Issue {
  iid
  title
  state
  due_date
  labels
}
class Commit {
  id (sha)
  author_name
  authored_date
  title
}
class MergeRequest {
  iid
  title
  state
  source_branch
}
class Milestone {
  id
  title
  due_date
}
class Utilizador {
  id
  name
}

Projeto "1" -- "*" Issue
Projeto "1" -- "*" Commit
Projeto "1" -- "*" MergeRequest
Projeto "1" -- "*" Milestone
Issue "*" -- "0..1" Utilizador : assignee
Issue "*" -- "0..1" Milestone
Commit "*" -- "1" Utilizador : autor
@enduml
```
