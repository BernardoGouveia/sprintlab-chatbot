# Resumo / Abstract

## Resumo

O **SprintLab Chatbox** é um assistente conversacional, baseado em inteligência artificial,
que integra a gestão e a análise de projetos GitLab no Microsoft Teams através de linguagem
natural. O projeto responde ao problema da fragmentação entre as ferramentas de
desenvolvimento (GitLab) e de comunicação (Teams), que obriga os utilizadores a constantes
mudanças de contexto e exige conhecimento técnico para interpretar o estado de um projeto.

A solução permite consultar o projeto, gerar **relatórios e gráficos determinísticos**, gerir
*issues* (criar, fechar, atualizar e apagar), investigar o histórico de código (*git blame*)
e até **gerar código para commit** — sempre sob confirmação humana (as escritas com o token
do servidor exigem ainda uma chave de acesso) e sem alterar a *branch* principal, sendo
qualquer código gerado proposto através de um *Merge Request* para revisão.

Foi desenvolvida exclusivamente com a biblioteca padrão de Python, com inferência num modelo
*open-source* (Llama 3.3 70B, via Groq), e implantada gratuitamente num *Hugging Face Space*
em formato Docker. A solução distingue-se por um princípio de design: os **factos vêm sempre
do GitLab** (números determinísticos, nunca gerados pela IA) e as **ações são sempre
confirmadas** — evitando o *output* de IA que apenas acrescenta trabalho de revisão (*workslop*).

Sendo este o relatório final, todas as fases de desenvolvimento foram concluídas. A solução
foi validada contra **quatro instâncias GitLab reais** (incluindo do parceiro industrial GMV)
e está coberta por **506 testes automatizados**.

**Palavras-chave:** Inteligência Artificial, Processamento de Linguagem Natural, GitLab,
Microsoft Teams, Modelos de Linguagem, DevOps, Multi-tenant.

## Abstract

**SprintLab Chatbox** is an AI-based conversational assistant that integrates GitLab project
management and analysis into Microsoft Teams through natural language. The project addresses
the fragmentation between development tools (GitLab) and communication tools (Teams), which
forces users into constant context switching and demands technical knowledge to interpret a
project's state.

The solution allows users to query the project, generate **deterministic reports and charts**,
manage *issues* (create, close, update and delete), investigate the code history (*git blame*)
and even **generate code to commit** — always under human confirmation (writes using the server's
token also require an access key) and without touching the main branch, with any generated code
proposed through a *Merge Request* for review.

It was built exclusively with the Python standard library, with inference on an *open-source*
model (Llama 3.3 70B, via Groq), and deployed for free on a *Hugging Face Space* as a Docker
image. The solution stands out for a design principle: **facts always come from GitLab**
(deterministic figures, never produced by the AI) and **actions are always confirmed** —
avoiding AI output that merely adds review burden (*workslop*).

As this is the final report, all development phases are complete. The solution was validated
against **four real GitLab instances** (including the industrial partner GMV) and is covered
by **506 automated tests**.

**Keywords:** Artificial Intelligence, Natural Language Processing, GitLab, Microsoft Teams,
Large Language Models, DevOps, Multi-tenant.
