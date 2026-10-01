# Artefactos para o Relatório Final — SprintLab Chatbox

Material de apoio à escrita do relatório final, seguindo a estrutura e o nível de
rigor do template DEISI (relatório de referência: 20/20).

| Ficheiro | Secção do relatório | O que é |
|---|---|---|
| `0-resumo-abstract.md` | Resumo / Abstract | Resumo (PT) + Abstract (EN) + palavras-chave |
| `glossario.md` | Glossário | Definições dos termos técnicos |
| `diagramas-plantuml.md` | 4.2 / 4.3 / 4.4 / 5.2 | **Código PlantUML** de todos os diagramas (alternativa editável aos SVG) |
| `1-requisitos-moscow.md` | 4.1 Levantamento de Requisitos | Tabelas MoSCoW — Requisitos Funcionais (FR) e Não Funcionais (NFR) com estado `I/PI/NI` |
| `2-arquitetura.svg` | 4.5 Estrutura / 5.2 Arquitetura | Diagrama de arquitetura do sistema |
| `3-casos-de-uso.svg` | 4.2 Diagramas de Casos de Uso | Diagrama UML de casos de uso |
| `3-bpmn-as-is.svg` | 4.3 Sequência e BPMN | Processo atual (gerir o GitLab manualmente) |
| `3-bpmn-to-be.svg` | 4.3 Sequência e BPMN | Processo com o SprintLab (integrado e seguro) |
| `3-sequencia.svg` | 4.3 Sequência e BPMN | Diagrama de sequência — ação com confirmação (confirmar→executar) |
| `4-seccoes-relatorio.md` | 1, 2, 3, 8 + diferenciador | Texto pronto: Problema, Benchmarking, Viabilidade (Proposto vs Desenvolvido), Resultados, Anti-workslop |
| `5-9-seccoes-restantes.md` | 5, 6, 7, 9 | Texto pronto: Solução Proposta, Plano de Testes, Método/Planeamento, Conclusão |

## Como usar

- **Markdown** (`.md`): copia o texto/tabelas para o teu editor de relatório (Word/Google Docs/LaTeX).
- **SVG** (`.svg`): abre num navegador para ver; para o relatório, podes:
  - inserir o SVG diretamente (Word moderno e LaTeX aceitam SVG), ou
  - converter para PNG (abrir no navegador → captura, ou usar um conversor online), ou
  - importar para o **draw.io** se quiseres editar/ajustar texto e cores.

## A confirmar / personalizar antes de entregar
- As **percentagens do questionário** na secção de Viabilidade (estão marcadas como *ajustar*).
- Os números de **Figura X** / **Tabela X** conforme a numeração do teu documento.
- Nomes de autor/orientador e o ID DEISI do TFC.

## Cobertura

Estão cobertas todas as secções de texto do template DEISI: Resumo/Abstract, 1·2·3,
4.1 (requisitos), 4.4 (modelo de dados, via PlantUML), 5·6·7·8·9, Glossário, e o
diferenciador anti-workslop. Diagramas: Casos de Uso, BPMN As-Is/To-Be, Sequência,
Arquitetura e Modelo de Dados — em **SVG** (visual personalizado) **e PlantUML** (editável).

**Falta só** (depende de ti): **4.6 Mock ups** (usa capturas da UI real da aplicação),
**Bibliografia** e os **Anexos** (questionário, calendário/Gantt).
