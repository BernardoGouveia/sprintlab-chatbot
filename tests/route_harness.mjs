// Routing harness — extrai as funções de deteção de intenção REAIS do app.js
// (sem DOM) e verifica que cada prompt encaminha para o tipo esperado.
// Corre com:  node tests/route_harness.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "..", "app.js"), "utf8");
const lines = src.split(/\r?\n/);

// As 4 funções de routing vivem em linhas contíguas; fatiamos pelos marcadores.
const start = lines.findIndex((l) => l.includes("function normaliseQuotes"));
const endIdx = lines.findIndex((l, i) => i > start && /^\s{4}function detectIntent/.test(l));
// inclui o corpo de detectIntent até ao seu fecho "    }" na coluna 4
let close = endIdx;
for (let i = endIdx + 1; i < lines.length; i++) {
  if (/^\s{4}\}\s*$/.test(lines[i])) { close = i; break; }
}
const block = lines.slice(start, close + 1).join("\n");

const factory = new Function(block + "\nreturn { detectIntent, detectChartIntent, detectBlameIntent };");
const { detectIntent } = factory();

const cases = [
  // ── Os 5 misroutes confirmados pela revisão adversarial ───────────────
  ["mostra-me os commits do Daniel Silveira", "chat"],
  ["mostra-me um exemplo de issue bem escrita", "chat"],
  ["visualiza como está a correr o sprintlab", "chat"],
  ["faz um resumo do que falta na sprint", "sprint_report"],
  ["mostra-me um relatório da sprint", "sprint_report"],

  // ── Gráficos que DEVEM continuar a ser gráficos ───────────────────────
  ["mostra um gráfico de commits", "chart"],
  ["gráfico de commits dos últimos 30 dias", "chart"],
  ["gráfico por assignee", "chart"],
  ["mostra o burndown", "chart"],
  ["gráfico de estado das issues", "chart"],
  ["gráfico por milestone", "chart"],
  ["gráfico de cycle time", "chart"],
  ["mostra um gráfico", "chart"],
  ["pie das issues", "chart"],
  ["gráfico de merge requests", "chart"],

  // ── Relatório ─────────────────────────────────────────────────────────
  ["faz um relatório do projeto", "sprint_report"],
  ["relatório da sprint", "sprint_report"],
  ["dá-me um resumo do projeto", "sprint_report"],
  ["resumo da sprint atual", "sprint_report"],

  // ── Blame / investigação de código ────────────────────────────────────
  ["quem alterou o server.py?", "blame_analysis"],
  ["quem mexeu no README.md linha 5?", "blame_analysis"],
  ["analisa o erro no app.js", "blame_analysis"],

  // ── Commit por IA ─────────────────────────────────────────────────────
  ["faz commit de uma função soma em utils.py", "code_commit"],
  ["commita um ficheiro hello.txt", "code_commit"],

  // ── Criar / fechar / editar / apagar ──────────────────────────────────
  ["cria uma issue 'corrigir login'", "create_form"],
  ["fecha a issue 8", "close_issue"],
  ["edita a issue 12", "edit_form"],
  ["apaga a issue 3", "delete_action"],

  // ── Exportações ───────────────────────────────────────────────────────
  ["exporta todas as issues para csv", "export_issues"],
  ["exporta todos os commits para csv", "export_commits"],

  // ── Q&A puro → chat ───────────────────────────────────────────────────
  ["fala-me do projeto", "chat"],
  ["quantas issues estão abertas?", "chat"],
  ["quem é o autor com mais commits?", "chat"],
  ["o que é uma boa descrição de issue?", "chat"],

  // ── "relatório" no texto não pode engolir ações sobre issues ──────────
  ["cria uma issue para o relatório final", "create_form"],
  ["cria uma issue 'rever o relatório'", "create_form"],
  ["fecha a issue 4 do relatório", "close_issue"],
  ["edita a issue 7 do relatório", "edit_form"],
  ["apaga a issue 9 do relatório", "delete_action"],

  // ── "remove X da issue N" é uma edição, nunca um apagar definitivo ────
  ["remove o assignee da issue 5", "chat"],
  ["remove a label bug da issue #7", "chat"],
  ["apaga a data limite da issue 3", "chat"],
  ["podes eliminar a issue #12?", "delete_action"],

  // ── "mostra" + categoria sem palavra forte é uma lista (chat) ─────────
  ["mostra as issues atribuídas ao João", "chat"],
  ["mostra as issues com a label bug", "chat"],
  ["mostra os merge requests abertos", "chat"],
  ["mostra o estado da issue 4", "chat"],
  ["mostra um gráfico por label", "chart"],
  ["mostra os commits dos últimos 30 dias", "chart"],
];

let fail = 0;
for (const [prompt, expected] of cases) {
  const got = detectIntent(prompt).type;
  const ok = got === expected;
  if (!ok) fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  [${expected.padEnd(15)}] got=${got.padEnd(15)}  «${prompt}»`);
}
console.log(`\n${cases.length - fail}/${cases.length} passed${fail ? `  (${fail} FAILED)` : ""}`);
process.exit(fail ? 1 : 0);
