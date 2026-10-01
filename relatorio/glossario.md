# Glossário

| Termo | Significado |
|---|---|
| **API** | *Application Programming Interface* — interface que permite a um programa comunicar com outro de forma estruturada |
| **REST** | *Representational State Transfer* — estilo de arquitetura de APIs sobre HTTP, usado pela API do GitLab |
| **LLM** | *Large Language Model* — modelo de linguagem de grande dimensão (ex.: Llama 3.3 70B) |
| **NLP** | *Natural Language Processing* — processamento de linguagem natural |
| **Function calling** | Capacidade de um LLM de invocar funções/ferramentas definidas pelo programador, em vez de só gerar texto |
| **GitLab** | Plataforma de alojamento de repositórios Git e gestão de projetos de software |
| **Issue** | Tarefa, bug ou pedido registado num projeto GitLab |
| **Commit** | Alteração registada no histórico de um repositório Git |
| **Branch** | Ramo de desenvolvimento paralelo num repositório Git |
| **Merge Request (MR)** | Pedido para integrar (*merge*) uma *branch* noutra, sujeito a revisão antes da aceitação |
| **git blame** | Operação que identifica, linha a linha, qual o commit e o autor que a alteraram pela última vez |
| **Groq** | Fornecedor de inferência de LLMs alojada, com API compatível com a OpenAI e nível de utilização gratuito |
| **Llama 3.3 70B** | Modelo de linguagem *open-source* da Meta, com 70 mil milhões de parâmetros |
| **Hugging Face Space** | Serviço de alojamento gratuito de aplicações de IA, suportando contentores Docker |
| **Docker** | Tecnologia de contentorização que empacota uma aplicação e o seu ambiente de execução |
| **Multi-tenant** | Arquitetura em que uma única instância serve vários clientes/configurações isolados (aqui, vários GitLab) |
| **SSE** | *Server-Sent Events* — mecanismo de envio contínuo de dados do servidor para o cliente (usado no *streaming* das respostas) |
| **CSP** | *Content-Security-Policy* — cabeçalho HTTP de segurança que indica ao browser de onde a página pode carregar recursos; aqui só aceita *scripts* do próprio servidor e do jsDelivr (sem *scripts inline*) e autoriza o embeber da página no Teams (`frame-ancestors`) |
| **SRI** | *Subresource Integrity* — atributo `integrity` com o *hash* esperado de um ficheiro externo (aqui, Chart.js e Font Awesome na CDN); o browser recusa o ficheiro se o conteúdo não corresponder |
| **Token (PAT)** | *Personal Access Token* — credencial de acesso à API do GitLab |
| **Chave de acesso (`APP_ACCESS_KEY`)** | Chave partilhada, configurada como *secret* no servidor e introduzida uma vez nas ⚙️ Definições (enviada no cabeçalho `X-App-Key`), exigida para as escritas no GitLab feitas com o token do servidor; não substitui um login individual |
| **CSV** | *Comma-Separated Values* — formato de ficheiro tabular, aberto pelo Excel |
| **Cache TTL** | Memória temporária com *Time-To-Live* (tempo de validade), para evitar pedidos repetidos |
| **Rate limit** | Limite ao número de pedidos que um cliente (aqui, por IP) pode fazer por unidade de tempo; acima dele os pedidos são recusados até a janela de tempo avançar |
| **RAG** | *Retrieval-Augmented Generation* — técnica que enriquece o LLM com informação recuperada de documentos |
| **Workslop** | *Output* de IA que aparenta ser trabalho útil mas transfere para o destinatário o esforço de o verificar e corrigir |
| **Determinístico** | Que produz sempre o mesmo resultado exato a partir dos mesmos dados (por oposição ao texto gerado por IA) |
| **MoSCoW** | Método de priorização de requisitos: *Must / Should / Could / Won't have* |
| **FR / NFR** | *Functional Requirement* / *Non-Functional Requirement* — requisito funcional / não funcional |
| **XSS / SSRF** | *Cross-Site Scripting* / *Server-Side Request Forgery* — classes de vulnerabilidades de segurança web |
| **TFC** | Trabalho Final de Curso |
| **LEI** | Licenciatura em Engenharia Informática |
| **GMV** | Empresa do setor aeroespacial, parceira industrial do projeto |
