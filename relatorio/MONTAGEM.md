# 🗂️ Mapa de Montagem Final do Relatório

Guia para montar o documento final a partir dos ficheiros desta pasta (`sfv/`).
Monta por esta ordem (estrutura do template DEISI). Legenda do estado:
**✅ pronto** · **✍️ preencher** (precisa de dados teus) · **📸 capturar** (precisa de imagem da app) · **⚙️ gerar** (renderizar diagrama).

---

## Ordem do documento

| # | Secção | Ficheiro fonte | Estado |
|---|--------|----------------|:------:|
| 1 | **Capa** | `0-capa-e-listas.md` (parte 1) | ✍️ nomes/data |
| 2 | **Direitos de Cópia** | `0-capa-e-listas.md` (parte 2) | ✍️ nomes |
| 3 | **Resumo** | `0-resumo-abstract.md` | ✅ |
| 4 | **Abstract** | `0-resumo-abstract.md` | ✅ |
| 5 | **Índice** | *(gerado automaticamente pelo Word/Docs)* | ⚙️ auto |
| 6 | **Lista de Figuras** | `0-capa-e-listas.md` | ✅ (confirmar nºs) |
| 7 | **Lista de Tabelas** | `0-capa-e-listas.md` | ✅ (confirmar nºs) |
| 8 | **1. Identificação do Problema** | `4-seccoes-relatorio.md` § 1 | ✅ |
| 9 | **2. Benchmarking** | `4-seccoes-relatorio.md` § 2 (Tabela 3) | ✅ |
| 10 | **3. Viabilidade e Pertinência** | `4-seccoes-relatorio.md` § 3 | ✍️ % do questionário |
| 11 | **4. Engenharia** | *(secções abaixo)* | — |
| 11.1 | **4.1 Levantamento de Requisitos** | `1-requisitos-moscow.md` (Tabelas 1 e 2) | ✅ |
| 11.2 | **4.2 Diagramas de Casos de Uso** (Figura 1) | `3-casos-de-uso.svg` *(ou PlantUML)* | ⚙️ inserir |
| 11.3 | **4.3 Sequência e BPMN** (Figuras 2, 3, 4) | `3-bpmn-as-is.svg`, `3-bpmn-to-be.svg`, `3-sequencia.svg` | ⚙️ inserir |
| 11.4 | **4.4 Modelos relevantes** (Figura 5) | `diagramas-plantuml.md` § 6 (modelo de dados) | ⚙️ gerar |
| 11.5 | **4.5 Estrutura / Arquitetura** (Figura 6) | `2-arquitetura.svg` | ⚙️ inserir |
| 11.6 | **4.6 Mock ups** (Figuras 7–12) | *capturas da UI real* | 📸 capturar |
| 12 | **5. Solução Proposta** (5.1–5.5) | `5-9-seccoes-restantes.md` § 5 | ✅ |
| 13 | **6. Plano de Testes e Avaliação** (Tabelas 4, 5) | `5-9-seccoes-restantes.md` § 6 | ✅ |
| 14 | **7. Método e Planeamento** (Gantt) | `5-9-seccoes-restantes.md` § 7 + `anexo-gantt.md` (figura) | ✍️ datas |
| 15 | **8. Resultados** | `4-seccoes-relatorio.md` § 8 | ✅ |
| 16 | **8.1 (As soluções IA não são *workslop*)** | `4-seccoes-relatorio.md` (secção diferenciadora) | ✅ |
| 17 | **9. Conclusão e Trabalhos Futuros** | `5-9-seccoes-restantes.md` § 9 | ✅ |
| 18 | **Bibliografia** | `bibliografia.md` | ✍️ datas de acesso |
| 19 | **Anexo 1 — Questionário** | `anexo-questionario.md` | ✍️ resultados |
| 20 | **Anexo 2 — Guia de Instalação** | `anexo-instalacao.md` | ✅ |
| 21 | **Anexo 3 — Calendário (Gantt)** | `anexo-gantt.md` (Tabela + figura) | ✍️ datas |
| 22 | **Glossário** | `glossario.md` | ✅ |

> A secção **8.1 anti-workslop** é o teu diferenciador — coloca-a dentro dos Resultados (ou
> mesmo antes da Conclusão). É o argumento que responde diretamente ao desafio do professor.

---

## Inserir os diagramas (figuras)

Tens cada diagrama em **dois formatos**, escolhe um:
- **SVG** (`.svg`) — visual com cores; Word moderno e LaTeX aceitam SVG direto. Ou abre no
  navegador e exporta PNG.
- **PlantUML** (`diagramas-plantuml.md` + `anexo-gantt.md`) — cola em
  https://www.plantuml.com/plantuml/uml/ → exporta PNG/SVG. Aspeto "UML standard".

| Figura | SVG | PlantUML |
|---|---|---|
| 1 — Casos de Uso | `3-casos-de-uso.svg` | § 1 |
| 2 — BPMN As-Is | `3-bpmn-as-is.svg` | § 3 |
| 3 — BPMN To-Be | `3-bpmn-to-be.svg` | § 4 |
| 4 — Sequência | `3-sequencia.svg` | § 2 |
| 5 — Modelo de Dados | — | § 6 |
| 6 — Arquitetura | `2-arquitetura.svg` | § 5 |
| (Gantt) | — | `anexo-gantt.md` |

---

## Antes de entregar — checklist

- [ ] **Deploy da nova versão** (`server.py` + pasta `src/` + frontend) e *secret*
      `APP_ACCESS_KEY` definido **antes** de tirares as capturas e gravares o vídeo
- [ ] **✍️ Percentagens** do questionário (secção 3) — depois de aplicares o `anexo-questionario.md`
- [ ] **✍️ Datas** do Gantt (secção 7 + Anexo 3) — ajustar às reais
- [ ] **📸 Capturas** da UI (Figuras 7–12): ecrã principal, card do relatório, card do blame,
      fluxo de commit por IA + Merge Request, painel de estatísticas, os 3 temas
- [ ] **📸 Capturas tiradas da nova versão** (não reutilizar capturas da versão anterior) —
      esconder a **chave de acesso** (⚙️ Definições)
- [ ] **✍️ Nomes** (autor, orientador) e **data** na capa e direitos de cópia
- [ ] **⚙️ Índice** automático gerado e atualizado
- [ ] Numeração de **Figura X / Tabela X** coerente em todo o documento
- [ ] **Bibliografia** — confirmar datas de acesso e acrescentar fontes usadas
- [ ] Esconder **tokens/segredos** em qualquer captura de ecrã
- [ ] Exportar para **PDF** com o índice clicável

---

## Como montar (prático)

1. Cria o documento no Word/Google Docs com o estilo de títulos (Título 1/2/3) para o
   **Índice** e as **Listas** serem automáticos.
2. Cola o texto dos `.md` secção a secção (as tabelas markdown coladas no Word convertem-se
   em tabela com *Colar > Manter formatação* ou via um conversor markdown→docx).
3. Insere as figuras (SVG/PNG) nos sítios indicados, com legenda "Figura X — ...".
4. Gera o Índice, a Lista de Figuras e a Lista de Tabelas (referências automáticas).
5. Revê a numeração e exporta para PDF.

> **Dica Cowork:** se vais montar no Cowork, sobe esta pasta `sfv/` inteira — tens o texto
> (`.md`), os diagramas (`.svg`) e este mapa. Segue a coluna "#" da tabela de cima como
> ordem das páginas.
