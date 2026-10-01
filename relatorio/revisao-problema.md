# 1. Identificação do Problema (versão revista)

> Reescrita segundo a orientação do professor: focar a **correlação issue↔commit** e a
> **segurança** dos dados de repositórios privados. Substitui o texto atual da secção 1.

Numa equipa que usa o GitLab, acompanhar o ciclo de vida de uma funcionalidade ou de um *bug*
obriga a saltar entre o GitLab, o terminal e a ferramenta de comunicação da equipa para juntar
o contexto de uma alteração: *Qual foi o commit que resolveu o Issue #12? Porque é que esta
alteração foi feita? Qual é o histórico de discussões associado a este bug?* A informação que
responde a estas perguntas existe — está dispersa entre as *issues*, os *commits* e as *merge
requests* — mas correlacioná-la manualmente é lento e exige familiaridade técnica com a
plataforma, o que afasta os perfis menos técnicos da informação do projeto.

A este problema de **fragmentação e de correlação** acresce uma preocupação de **segurança e
propriedade intelectual**. O envio de excertos de código, *logs* e dados de repositórios
privados para serviços de IA na nuvem de uso geral (como o ChatGPT comercial) pode violar as
políticas de propriedade intelectual e de proteção de dados de uma organização — uma
preocupação particularmente relevante no contexto do parceiro **GMV**, do setor aeroespacial.

O problema resume-se, assim, à necessidade de uma ferramenta de conversação que: (1) traga a
gestão e a análise do GitLab para o fluxo de trabalho da equipa (o Microsoft Teams), em
linguagem natural; (2) consiga **correlacionar as discussões das issues com as alterações reais
de código** (os commits) — respondendo a "quem alterou esta linha e porquê"; e (3) seja
**segura e implantável de acordo com as políticas da organização**, minimizando a exposição de
dados sensíveis a serviços externos.

A solução desenvolvida neste trabalho — o **SprintLab Chatbox** — responde a estas três
necessidades. Como se detalha na Solução Proposta (Capítulo 5), a sua arquitetura é
**independente do fornecedor de inferência** (usa uma interface compatível com a OpenAI),
podendo correr com um modelo **local/on-premise** em contextos sensíveis; e o seu desenho
**determinístico** garante que os factos (números, histórico, *blame*) são obtidos diretamente
do GitLab, e não gerados nem inferidos pelo modelo de linguagem — reduzindo o que é efetivamente
enviado para a IA.

---

> **Nota de coerência (para teres em conta na defesa):** o **protótipo demonstrado** usa o Groq
> (inferência na nuvem, nível gratuito) por acessibilidade e custo zero. A frase "arquitetura
> independente do fornecedor… pode correr com um modelo local" é **verdadeira** — a interface é
> compatível com a OpenAI, pelo que basta apontar para um modelo local (ex.: Ollama, que foi a
> infraestrutura do protótipo inicial). Se um jurado perguntar "mas o Groq não é seguro?", a
> resposta honesta é: *"o demo usa Groq por custo; a arquitetura suporta um modelo on-premise
> sem alterações de código, e os números nunca são enviados para a IA — são calculados a partir
> do GitLab."* Mantém esta coerência entre o problema e a solução.
