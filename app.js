// ============================================================
// SprintLab TFC Chatbox — logica do frontend
// Separada de chatbox.html (organizacao HTML / CSS / JS).
// Servida por server.py em  GET /app.js  (carregada com <script defer>).
// Sem handlers inline no HTML: a CSP da página só permite scripts deste ficheiro.
// ============================================================

    // BASE_URL is derived from the page itself — no more hardcoded ngrok URL.
    const BASE_URL = window.location.origin;
    const HISTORY_CAP = 20;

    const history = [];
    let loading = false;
    let activeController = null; // AbortController for in-flight stream

    // Erro com uma mensagem do servidor que vale a pena mostrar ao utilizador.
    class ServerError extends Error {
      constructor(message, data) { super(message); this.name = "ServerError"; this.data = data || {}; }
    }

    // ── Utils ─────────────────────────────────────────────────────────────────
    function resize(el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 100) + "px";
    }
    function handleKey(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    }
    function useSuggestion(el) {
      document.getElementById("input").value = el.textContent;
      send();
    }
    function removeWelcome() {
      const w = document.getElementById("welcome");
      if (w) w.remove();
    }
    function escHtml(s) {
      return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
              .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
    }
    // Só permite http(s) num href — bloqueia javascript:/data: (web_url vem de um
    // GitLab arbitrário que o utilizador liga, logo é dado não-confiável).
    function safeUrl(u) {
      // Campo em falta (null/undefined) → "" e NÃO um link. Sem este guarda,
      // new URL(undefined, BASE) resolvia para "<base>/undefined" (um http
      // válido!) e mostrava "Abrir no GitLab" a apontar para /undefined.
      if (typeof u !== "string" || !u.trim()) return "";
      try {
        const url = new URL(u, BASE_URL);
        return (url.protocol === "https:" || url.protocol === "http:") ? url.href : "";
      } catch (e) { return ""; }
    }
    // Mensagem de erro de uma resposta HTTP falhada (JSON {error} do servidor).
    async function responseError(res) {
      let data = {};
      try { data = await res.json(); } catch (e) { /* corpo não-JSON */ }
      return new ServerError(
        (data && typeof data.error === "string" && data.error) || `Erro do servidor (HTTP ${res.status}).`,
        data);
    }

    // Render markdown SAFELY: escape first, then apply formatting on the
    // already-escaped text — LINE BY LINE, so inline rules (** * `) never span
    // lines and swallow list markers; ``` fences become code blocks.
    function renderInline(text) {
      return escHtml(text).split(/(`[^`]+`)/g).map((part, i) => {
        if (i % 2 === 1) return `<code>${part.slice(1, -1)}</code>`;
        return part
          .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
          .replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?![*\w])/g, "$1<em>$2</em>");
      }).join("");
    }
    function renderMd(text) {
      const lines = String(text ?? "").replace(/\r\n/g, "\n").split("\n");
      let html = "", list = null, code = null;
      const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
      const flushCode = () => { html += `<pre class="md-code"><code>${escHtml(code.join("\n"))}</code></pre>`; code = null; };
      for (const line of lines) {
        const t = line.trim();
        if (code !== null) {
          if (t.startsWith("```")) flushCode(); else code.push(line);
          continue;
        }
        if (t.startsWith("```")) { closeList(); code = []; continue; }
        if (!t) continue;   // blank lines don't split a list
        let m;
        if ((m = t.match(/^[-*+]\s+(.*)$/))) {
          if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
          html += `<li>${renderInline(m[1])}</li>`;
        } else if ((m = t.match(/^\d+[.)]\s+(.*)$/))) {
          if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
          html += `<li>${renderInline(m[1])}</li>`;
        } else if ((m = t.match(/^#{1,6}\s+(.*)$/))) {
          closeList(); html += `<p><strong>${renderInline(m[1])}</strong></p>`;
        } else {
          closeList(); html += `<p>${renderInline(t)}</p>`;
        }
      }
      if (code !== null) flushCode();
      closeList();
      return html;
    }
    function setStatus(thinking) {
      const dot = document.getElementById("status-dot");
      const label = document.getElementById("model-label");
      dot.className = "status-dot" + (thinking ? " thinking" : "");
      label.textContent = thinking ? "a pensar..." : modelLabel();
    }
    let scrollPending = false;
    function scrollBottom() {
      if (scrollPending) return;
      scrollPending = true;
      requestAnimationFrame(() => {
        scrollPending = false;
        const m = document.getElementById("messages");
        m.scrollTop = m.scrollHeight;
      });
    }

    // ── Diálogos na página ────────────────────────────────────────────────────
    // window.confirm/alert não funcionam no novo cliente do Teams (a chamada é
    // ignorada) — por isso apagar/backup/repor usam este diálogo próprio.
    function uiDialog({ title, message, okText = "OK", cancelText = null, danger = false }) {
      return new Promise((resolve) => {
        const ov = document.getElementById("dialog-overlay");
        const ok = document.getElementById("dialog-ok");
        const cancel = document.getElementById("dialog-cancel");
        document.getElementById("dialog-title").textContent = title || "";
        document.getElementById("dialog-message").textContent = message || "";
        ok.textContent = okText;
        ok.classList.toggle("danger", !!danger);
        cancel.hidden = !cancelText;
        if (cancelText) cancel.textContent = cancelText;
        const prevFocus = document.activeElement;
        const onKey = (e) => {
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
        };
        function done(value) {
          ov.hidden = true;
          ok.onclick = cancel.onclick = ov.onclick = null;
          document.removeEventListener("keydown", onKey, true);
          if (prevFocus && typeof prevFocus.focus === "function") prevFocus.focus();
          resolve(value);
        }
        ok.onclick = () => done(true);
        cancel.onclick = () => done(false);
        ov.onclick = (e) => { if (e.target === ov) done(false); };
        document.addEventListener("keydown", onKey, true);
        ov.hidden = false;
        (cancelText ? cancel : ok).focus();   // destructive dialogs default to "Cancelar"
      });
    }
    function uiConfirm(message, opts = {}) {
      return uiDialog({ title: opts.title || "Confirmar", message,
                        okText: opts.okText || "Continuar", cancelText: "Cancelar",
                        danger: !!opts.danger });
    }
    function uiAlert(message, title = "Aviso") {
      return uiDialog({ title, message, okText: "OK" });
    }

    // ── Static (non-stream) message ───────────────────────────────────────────
    function addMsg(role, text, badge) {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = `msg ${role}`;
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = role === "user" ? "Tu" : "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      if (badge) {
        const b = document.createElement("div");
        b.className = "action-badge";
        b.textContent = badge;
        bub.appendChild(b);
      }
      const content = document.createElement("div");
      content.className = "bubble-content";
      if (role === "user") {
        content.textContent = text; // pre-wrap CSS preserves newlines
      } else {
        content.innerHTML = renderMd(text);
      }
      bub.appendChild(content);
      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return content;
    }

    // ── Streaming bubble (perf-critical path) ─────────────────────────────────
    // While streaming we append plain text to a <span> and only re-parse the
    // markdown when streaming completes. This avoids the O(n²) re-parse-on-
    // every-token cost the previous version had. Bubbles are referenced by
    // element (never by a shared id), so an error only ever touches its own.
    function createStreamBubble() {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";

      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "AI";

      const bub = document.createElement("div");
      bub.className = "bubble";

      const typing = document.createElement("div");
      typing.className = "typing";
      typing.innerHTML = `<div class="dot"></div><div class="dot"></div><div class="dot"></div>`;
      bub.appendChild(typing);

      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return bub;
    }

    function ensureStreamContent(bub) {
      let raw = bub.querySelector(".stream-raw");
      if (raw) return raw;
      const typing = bub.querySelector(".typing");
      if (typing) typing.remove();
      raw = document.createElement("span");
      raw.className = "stream-raw";
      const cursor = document.createElement("span");
      cursor.className = "cursor";
      bub.appendChild(raw);
      bub.appendChild(cursor);
      return raw;
    }

    function appendStreamChunk(bub, chunk) {
      const raw = ensureStreamContent(bub);
      // textContent append is cheap; pre-wrap CSS keeps newlines.
      raw.appendChild(document.createTextNode(chunk));
      scrollBottom();
    }

    function finalizeStreamBubble(bub, fullText) {
      const cursor = bub.querySelector(".cursor");
      if (cursor) cursor.remove();
      const typing = bub.querySelector(".typing");
      if (typing) typing.remove();
      const raw = bub.querySelector(".stream-raw");
      if (!raw) {
        bub.innerHTML = renderMd(fullText);
        return;
      }
      // One markdown re-parse at the end, no more.
      raw.outerHTML = `<div class="bubble-content">${renderMd(fullText)}</div>`;
    }

    function showError(msg) {
      const msgs = document.getElementById("messages");
      const e = document.createElement("div");
      e.className = "error-msg";
      e.textContent = msg;
      msgs.appendChild(e);
      scrollBottom();
    }
    function showInfo(msg) {
      const msgs = document.getElementById("messages");
      const e = document.createElement("div");
      e.className = "info-msg";
      e.textContent = msg;
      msgs.appendChild(e);
      scrollBottom();
    }

    // ── Intent detection ──────────────────────────────────────────────────────
    function normaliseQuotes(s) {
      // Collapse smart quotes / pt-PT angle quotes into ASCII so the regex below
      // doesn't miss "criar issue 'X'" written with curly quotes.
      return s.replace(/[‘’‚‛‹›]/g, "'")
              .replace(/[“”„«»]/g, "\"");
    }
    // Texto entre aspas (ex.: o título de uma issue) não conta para a intenção:
    // "cria uma issue 'relatório final'" é criar, não pedir o relatório.
    function stripQuoted(s) {
      return s.replace(/"[^"]*"|'[^']*'/g, " ");
    }
    function detectChartIntent(t) {
      // Trigger word must be present — otherwise plain Q&A goes to the chat model.
      const hasTrigger = /\b(?:gr[áa]fico|chart|visualiza[r]?|mostra(?:r|me)?|pie|doughnut|barras?|burndown)\b/i.test(t);
      if (!hasTrigger) return null;
      // Palavra de gráfico FORTE (exclui "mostra"/"visualiza", que sozinhos são
      // ambíguos): só estas autorizam os gráficos por categoria e o fallback.
      const strong = /\b(?:gr[áa]fico|chart|pie|doughnut|barras?|burndown)\b/i.test(t);

      const daysMatch = t.match(/(\d+)\s*dias?/i);
      const params = daysMatch ? { days: daysMatch[1] } : {};
      // "mostra as issues atribuídas ao João" é uma LISTA (chat), não um gráfico.
      const specific = strong || !!daysMatch;

      if (/burndown|fechadas?.*dia|progresso (?:ao longo|temporal|di[áa]rio)/i.test(t))
        return { chart: "burndown", params };
      if (/cycle ?time|tempo (?:de |para )?ciclo|tempo para fechar|tempo de fecho/i.test(t))
        return { chart: "cycle-time", params: {} };
      if (/assignee|atribu[ií]d|quem tem/i.test(t))
        return specific ? { chart: "by-assignee", params: {} } : null;
      if (/label|etiqueta/i.test(t))
        return specific ? { chart: "by-label", params: {} } : null;
      if (/milestone|\bsprint\b(?!lab)/i.test(t))
        return specific ? { chart: "by-milestone", params: {} } : null;
      if (/merge request|\bmr\b|pull request/i.test(t))
        return specific ? { chart: "contributors-mr", params: {} } : null;
      if (/commit/i.test(t)) {
        // "gráfico de commits"/"...N dias" → gráfico; mas "mostra os commits do X"
        // (sem palavra forte) NÃO é gráfico → vai ao chat (commits_by_author).
        if (!specific) return null;
        return daysMatch ? { chart: "contributors-commits", params }
                         : { chart: "contributors-all", params: {} };
      }
      if (/estado|aberta?s? vs|opened vs|status/i.test(t))
        return specific ? { chart: "state-pie", params: {} } : null;
      // Sem especificador: só faz gráfico se houver palavra FORTE ("gráfico/pie/
      // barras"). "mostra-me um exemplo de issue" → null → chat (não um pie).
      return strong ? { chart: "state-pie", params: {} } : null;
    }

    // ── Investigação de código (blame): "quem alterou X?", "analisa o erro em
    // Y linha N", ou um stack trace colado. Devolve {file, line} | {help} | null.
    function detectBlameIntent(raw) {
      // 1) stack traces colados → ficheiro+linha extraídos diretamente
      let m = raw.match(/File "([^"]+)", line (\d+)/);                    // Python
      if (m) return { file: m[1], line: Number(m[2]) };
      m = raw.match(/\bat [^\n(]*\(?([\w./\\-]+\.[A-Za-z]\w{0,7}):(\d+)(?::\d+)?\)?/); // JS
      if (m) return { file: m[1], line: Number(m[2]) };

      // 2) frase natural: palavras-chave de blame/erro + um token de ficheiro
      const kw =
        /\b(?:quem|qual|que)\b[^.?!\n]*\b(?:alterou|alteraram|mudou|mudaram|mexeu|mexeram|modificou|modificaram|escreveu|introduziu)\b/i.test(raw)
        || /\bblame\b/i.test(raw)
        || (/\b(?:analisa|investiga|descobre|encontra|explica|origem|veio)\b/i.test(raw)
            && /\b(?:erro|bug|problema|falha)\b/i.test(raw));
      if (!kw) return null;

      const noUrl = raw.replace(/https?:\/\/\S+/g, " ");  // não confundir URLs com ficheiros
      const f = noUrl.match(/([\w\-./\\]*[\w\-]\.[A-Za-z]\w{0,7})(?::(\d+))?/);
      if (!f) {
        // pediu blame mas sem ficheiro → bolha de ajuda (0 tokens)
        return /\b(?:ficheiro|linha|c[óo]digo|file)\b/i.test(raw) ? { help: true } : null;
      }
      let line = f[2] ? Number(f[2]) : null;
      if (!line) {
        const l = raw.match(/\b(?:linha|line)\s*#?(\d+)/i);
        if (l) line = Number(l[1]);
      }
      return { file: f[1], line };
    }

    function detectIntent(text) {
      const raw = normaliseQuotes(text);
      const plain = stripQuoted(raw);          // sem títulos entre aspas
      const t = plain.toLowerCase();

      // Blame antes dos charts: "mostra quem alterou o app.js" tem trigger de
      // chart ("mostra") mas o token de ficheiro torna-o investigação de código.
      // (Usa o texto original: os stack traces Python trazem aspas.)
      const blame = detectBlameIntent(raw);
      if (blame) {
        return blame.help ? { type: "blame_help" }
                          : { type: "blame_analysis", file: blame.file, line: blame.line };
      }

      // Commit por IA — exige o VERBO ("faz commit", "commita"), por isso não
      // dispara em "gráfico de commits", "exporta commits" ou "quantos commits".
      if (/\bfa(?:z|ça|zer)\s+(?:o\s+|um\s+)?commit\b/i.test(plain)
          || /\bcommita(?:r)?\b/i.test(plain)) {
        return { type: "code_commit" };
      }

      // Ações explícitas sobre UMA issue (com número) ANTES do relatório e dos
      // gráficos: "fecha a issue 4 do relatório" é fechar, não um relatório.
      // Apagar exige o verbo a atuar diretamente sobre a issue: "remove o
      // assignee da issue 5" é uma edição (vai ao modelo → formulário), nunca
      // um cartão de apagar definitivo.
      const deleteMatch = plain.match(/\b(?:apaga|elimina|remove|delete)r?\s+(?:a\s+|o\s+)?issue\s+#?(\d+)\b/i);
      if (deleteMatch) return { type: "delete_action", iid: deleteMatch[1] };

      const closeMatch = plain.match(/\bfecha[r]?\s+(?:a\s+)?issue\s+#?(\d+)\b/i)
                      || plain.match(/\bclose\s+issue\s+#?(\d+)\b/i);
      if (closeMatch) return { type: "close_issue", iid: closeMatch[1] };

      // Edit/update intent → open the pre-filled edit form for that issue.
      const editMatch = plain.match(/\b(?:edita[r]?|atualiz[ae][r]?|altera[r]?|modifica[r]?|muda[r]?)\b[^?]*\bissue\s+#?(\d+)\b/i);
      if (editMatch) return { type: "edit_form", iid: editMatch[1] };

      // Create intent → open the structured form (the model never auto-creates).
      // Broad detection; a title is only pre-filled when clearly given.
      if (/\b(cria|criar)\b[^?]*\b(issue|tarefa)\b/i.test(t)
          || /\bnov[ao]\s+issue\b/i.test(t)) {
        let title = "";
        const q = raw.match(/['"]([^'"]+)['"]/);
        if (q) {
          title = q[1].trim();
        } else {
          const m = raw.match(/\b(?:t[ií]tulo|chamad[oa])\s+(.+)$/i);
          if (m) title = m[1].trim();
        }
        return { type: "create_form", title };
      }

      // Relatório do projeto ANTES dos gráficos: "mostra-me um relatório da
      // sprint" tem "mostra" (gatilho de gráfico) mas a intenção é o relatório.
      // Regex larga: "resumo … sprint/projeto" tolera palavras pelo meio.
      if (/relat[óo]rio|resumo\b[^?.!\n]*\b(?:sprint|projeto|projecto)|sprint\s+report/i.test(t))
        return { type: "sprint_report" };

      const chart = detectChartIntent(t);
      if (chart) return { type: "chart", ...chart };

      if (/export[ae][r]?|download|csv|transfere|descarrega|ficheiro de (?:issues|commits)/i.test(t)) {
        if (/commit/i.test(t)) return { type: "export_commits" };
        const state = /fecha[ds]|closed/i.test(t) ? "closed"
                    : /abert[as]|opened/i.test(t) ? "opened"
                    : "all";
        return { type: "export_issues", state };
      }

      return { type: "chat" };
    }

    // ── GitLab actions ────────────────────────────────────────────────────────
    // All issue writes (create/close/update/delete) go through the confirmation
    // card -> POST /api/confirm-action. Only export stays a direct GET download.
    const headers = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

    // Contexto GitLab de um cartão/formulário, FIXADO quando é criado. Se o
    // projeto ativo mudar (ou for editado) entretanto, o clique é recusado em
    // vez de apagar/editar/commitar noutro projeto.
    function glKey() {
      return JSON.stringify([headers["X-GL-Base"] || "", headers["X-GL-Token"] || "",
                             headers["X-GL-Project"] || ""]);
    }
    function snapshotCtx() {
      return { wsId: workspaces.active, key: glKey(), headers: { ...headers } };
    }
    function ctxStillValid(ctx) {
      return !!ctx && ctx.wsId === workspaces.active && ctx.key === glKey();
    }
    const STALE_CTX_MSG = "O projeto ativo mudou desde que isto foi aberto — repete o pedido.";
    // Headers de uma ESCRITA: os do contexto fixado + a chave de acesso (se houver).
    function writeHeaders(ctx) {
      const h = { ...ctx.headers };
      if (settings.accessKey) h["X-App-Key"] = settings.accessKey;
      return h;
    }
    // Erro de uma ação; se faltar a chave de acesso, oferece abrir as definições.
    function actionErrorHtml(r, fallback) {
      let h = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ` +
              `${escHtml((r && r.error) || fallback)}</div>`;
      if (r && r.code === "access_key")
        h += `<button type="button" class="confirm-link-btn" data-open-settings>` +
             `<i class="fas fa-key"></i> Introduzir a chave de acesso</button>`;
      return h;
    }

    // ── Definições globais (modelo + tema + chave) — guardadas no browser ──────
    const SETTINGS_KEY = "sprintlab_settings";
    // "auto" = segue a hora local do utilizador: Claro de dia, Preto (OLED) à noite
    const THEMES = ["auto", "light", "black"];
    const DAY_START = 7, DAY_END = 19;      // "de dia" = 07:00–18:59 (hora local)
    function resolveTheme(t) {
      if (t !== "auto") return t;
      const h = new Date().getHours();
      return (h >= DAY_START && h < DAY_END) ? "light" : "black";
    }
    const MODEL_LABELS = {
      "llama-3.3-70b-versatile": "Llama 3.3 70B",
      "llama-3.1-8b-instant": "Llama 3.1 8B",
      "qwen/qwen3-32b": "Qwen3 32B",
      "openai/gpt-oss-120b": "GPT-OSS 120B",
      "moonshotai/kimi-k2-instruct-0905": "Kimi K2",
    };
    const str = (v) => (typeof v === "string" ? v : "");
    const settings = (() => {
      let s = {};
      try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch (e) {}
      if (!s || typeof s !== "object") s = {};
      // Versões antigas gravavam SEMPRE "llama-3.3-70b-versatile" (não havia a
      // opção "Predefinido do servidor"); sem o campo accessKey, não foi escolha.
      if (!("accessKey" in s) && s.model === "llama-3.3-70b-versatile") s.model = "";
      return {
        // gl* mantidos só para migração p/ workspaces (ver loadWorkspaces)
        glBase: str(s.glBase), glToken: str(s.glToken), glProject: str(s.glProject),
        // "" = modelo predefinido do servidor (GROQ_MODEL)
        model: MODEL_LABELS[s.model] ? s.model : "",
        // sem escolha guardada (ou o antigo "Escuro") → automático pela hora local
        theme: THEMES.includes(s.theme) ? s.theme : "auto",
        accessKey: str(s.accessKey),
      };
    })();
    // O que o servidor diz de si (modelo por omissão, escritas ligadas?).
    const serverConfig = { default_model: "", writes: "key", allow_private_gitlab: false };
    function modelLabel() {
      const m = settings.model || serverConfig.default_model;
      return MODEL_LABELS[m] || m || "Modelo do servidor";
    }
    // `model` só vai no pedido se o utilizador escolheu um (senão o servidor usa
    // o GROQ_MODEL configurado).
    function modelParam() { return settings.model || undefined; }
    function saveSettingsToStorage() {
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(
          { model: settings.model, theme: settings.theme, accessKey: settings.accessKey }));
      } catch (e) { /* storage cheio — ignora */ }
    }
    function applyTheme() {
      document.documentElement.dataset.theme = resolveTheme(settings.theme);
      refreshChartsTheme();
    }
    // Em automático, troca sozinho à hora certa (verifica a cada minuto; não mexe
    // durante a pré-visualização nas Definições).
    setInterval(() => {
      if (settings.theme !== "auto") return;
      const ov = document.getElementById("settings-overlay");
      if (ov && !ov.hidden) return;
      if (document.documentElement.dataset.theme !== resolveTheme("auto")) applyTheme();
    }, 60000);
    function applyModelBadge() {
      const el = document.getElementById("model-label");
      if (el && !loading) el.textContent = modelLabel();
    }

    // ── Workspaces: cada GitLab é um "chat" próprio na sidebar esquerda ───────
    // O workspace `default` usa as credenciais do servidor (Secrets) — sem token
    // no browser. Os personalizados guardam base/token/project em localStorage.
    const WS_KEY = "sprintlab_workspaces";
    const HIST_PREFIX = "sprintlab_hist_";

    // Valida/normaliza a estrutura (localStorage ou backup importado): entradas
    // inválidas são descartadas em vez de partirem a app no arranque.
    function normaliseWorkspaces(w) {
      const list = [];
      const seen = new Set();
      for (const x of (w && Array.isArray(w.list) ? w.list : [])) {
        if (!x || typeof x !== "object") continue;
        const id = str(x.id);
        if (!id || seen.has(id)) continue;
        seen.add(id);
        if (x.builtin) {
          list.push({ id, name: str(x.name) || "SprintLab", builtin: true });
        } else {
          list.push({ id, name: str(x.name) || "GitLab", glBase: str(x.glBase),
                      glToken: str(x.glToken), glProject: str(x.glProject) });
        }
      }
      return { active: str(w && w.active) || "default", list };
    }
    function validMsg(m) {
      return !!m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string";
    }

    const workspaces = loadWorkspaces();

    function loadWorkspaces() {
      let raw = null;
      try { raw = JSON.parse(localStorage.getItem(WS_KEY)); } catch (e) {}
      const w = normaliseWorkspaces(raw);
      if (!w.list.some(x => x.builtin)) {
        w.list.unshift({ id: "default", name: "SprintLab", builtin: true });
      }
      // migração: config GitLab antiga (painel ⚙️ de antes) vira um workspace
      if (settings.glToken || settings.glProject) {
        if (!w.list.some(x => x.id === "ws_migrated")) {
          w.list.push({
            id: "ws_migrated", name: "O meu GitLab",
            glBase: settings.glBase, glToken: settings.glToken, glProject: settings.glProject,
          });
          w.active = "ws_migrated";
        }
        // PERSISTE o workspace migrado ANTES de limpar a config antiga, senão as
        // credenciais perdiam-se (o `w` aqui ainda está em TDZ p/ saveWorkspaces).
        try { localStorage.setItem(WS_KEY, JSON.stringify(w)); } catch (e) {}
        settings.glBase = settings.glToken = settings.glProject = "";
        saveSettingsToStorage();
      }
      if (!w.list.some(x => x.id === w.active)) w.active = w.list[0].id;
      return w;
    }
    function saveWorkspaces() {
      try { localStorage.setItem(WS_KEY, JSON.stringify(workspaces)); } catch (e) { /* quota */ }
    }
    function activeWs() { return workspaces.list.find(x => x.id === workspaces.active) || workspaces.list[0]; }

    function applyGitlabHeaders() {
      // mutam o objeto `headers` partilhado → todos os fetch usam o ws ativo
      const ws = activeWs();
      if (!ws || ws.builtin) {
        delete headers["X-GL-Base"]; delete headers["X-GL-Token"]; delete headers["X-GL-Project"];
        return;
      }
      if (ws.glBase)    headers["X-GL-Base"] = ws.glBase;       else delete headers["X-GL-Base"];
      if (ws.glToken)   headers["X-GL-Token"] = ws.glToken;     else delete headers["X-GL-Token"];
      if (ws.glProject) headers["X-GL-Project"] = ws.glProject; else delete headers["X-GL-Project"];
    }

    // Histórico por workspace (persistido no browser)
    function loadStoredHistory(wsId) {
      let saved = [];
      try { saved = JSON.parse(localStorage.getItem(HIST_PREFIX + wsId)); } catch (e) {}
      return Array.isArray(saved) ? saved.filter(validMsg).slice(-HISTORY_CAP) : [];
    }
    function persistHistory() {
      try {
        localStorage.setItem(HIST_PREFIX + workspaces.active,
          JSON.stringify(history.slice(-HISTORY_CAP)));
      } catch (e) { /* storage cheio — ignora */ }
    }
    // Acrescenta uma nota ao histórico do workspace ONDE a ação começou — mesmo
    // que o utilizador já tenha mudado de projeto quando ela termina.
    function appendToHistory(wsId, msg) {
      if (wsId === workspaces.active) {
        history.push(msg);
        persistHistory();
        return;
      }
      const other = loadStoredHistory(wsId);
      other.push(msg);
      try {
        localStorage.setItem(HIST_PREFIX + wsId, JSON.stringify(other.slice(-HISTORY_CAP)));
      } catch (e) { /* storage cheio — ignora */ }
    }
    function restoreHistory() {
      const saved = loadStoredHistory(workspaces.active);
      history.splice(0, history.length, ...saved);
      destroyAllCharts();                 // os canvases vão desaparecer do DOM
      const msgs = document.getElementById("messages");
      if (!history.length) {
        msgs.innerHTML = WELCOME_HTML;   // ecrã de boas-vindas do projeto
        return;
      }
      msgs.innerHTML = "";
      for (const m of history) addMsg(m.role === "user" ? "user" : "ai", m.content);
      scrollBottom();
    }

    function updateHeaderForWs() {
      const ws = activeWs();
      document.getElementById("ws-title").textContent = ws.name || "GitLab";
      const host = ws.builtin ? "GitLab predefinido"
        : `${(ws.glBase || "gitlab.com").replace(/^https?:\/\//, "")} · #${ws.glProject}`;
      document.getElementById("ws-sub").textContent = host;
    }

    function renderWorkspaceList() {
      const box = document.getElementById("ws-list");
      box.innerHTML = "";
      for (const ws of workspaces.list) {
        const item = document.createElement("div");
        const isActive = ws.id === workspaces.active;
        item.className = "ws-item" + (isActive ? " active" : "");
        // Acessível por teclado (Tab + Enter/Espaço), não só por clique.
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        if (isActive) item.setAttribute("aria-current", "true");
        const activate = () => { switchWorkspace(ws.id); closeSidebar("sidebar-left"); };
        item.onclick = activate;
        item.onkeydown = (e) => {
          if (e.target === item && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); activate(); }
        };

        const ico = document.createElement("div");
        ico.className = "ws-ico";
        ico.innerHTML = ws.builtin ? '<i class="fas fa-rocket"></i>' : '<i class="fab fa-gitlab"></i>';

        const meta = document.createElement("div");
        meta.className = "ws-meta";
        const nm = document.createElement("div");
        nm.className = "ws-name"; nm.textContent = ws.name || "GitLab";
        const host = document.createElement("div");
        host.className = "ws-host";
        host.textContent = ws.builtin ? "Predefinição"
          : (ws.glBase || "gitlab.com").replace(/^https?:\/\//, "");
        meta.appendChild(nm); meta.appendChild(host);

        item.appendChild(ico); item.appendChild(meta);
        if (!ws.builtin) {
          const edit = document.createElement("button");
          edit.type = "button"; edit.className = "ws-edit";
          edit.innerHTML = '<i class="fas fa-pen"></i>';
          edit.setAttribute("aria-label", "Editar " + (ws.name || "GitLab"));
          edit.onclick = (e) => { e.stopPropagation(); openWsEditor(ws.id); };
          edit.onkeydown = (e) => e.stopPropagation();
          item.appendChild(edit);
        }
        box.appendChild(item);
      }
    }

    function switchWorkspace(id) {
      if (loading) return;                  // não trocar a meio de uma resposta
      if (id === workspaces.active) return;
      persistHistory();
      workspaces.active = id;
      saveWorkspaces();
      applyGitlabHeaders();
      renderWorkspaceList();
      updateHeaderForWs();
      clearFollowups();
      restoreHistory();
      loadStats();
    }

    // ── Editor de workspace (adicionar / editar um GitLab) ────────────────────
    let wsEditing = null;        // id do ws em edição, ou null = novo
    let wsTestName = "";         // nome do projeto devolvido pelo último teste

    function openWsEditor(id) {
      wsEditing = id || null;
      const ws = id ? workspaces.list.find(x => x.id === id) : null;
      document.getElementById("ws-editor-title").textContent = ws ? "Editar GitLab" : "Adicionar GitLab";
      document.getElementById("ws-name").value = ws?.name || "";
      document.getElementById("ws-base").value = ws?.glBase || "";
      document.getElementById("ws-token").value = ws?.glToken || "";
      document.getElementById("ws-project").value = ws?.glProject || "";
      document.getElementById("ws-test-result").textContent = "";
      document.getElementById("ws-delete").hidden = !ws;
      wsTestName = "";
      document.getElementById("ws-overlay").hidden = false;
      document.getElementById("ws-name").focus();
    }
    function closeWsEditor() { document.getElementById("ws-overlay").hidden = true; }

    function wsFormError(msg) {
      const r = document.getElementById("ws-test-result");
      r.textContent = "✗ " + msg;
      r.className = "settings-test-result err";
    }
    // Validação local (o servidor valida outra vez): https e sem credenciais no URL
    // (http:// só se o servidor o permitir — GITLAB_ALLOW_PRIVATE, dev local).
    function wsBaseError(base) {
      if (!base) return "";
      try {
        const u = new URL(base);
        const httpOk = serverConfig.allow_private_gitlab && u.protocol === "http:";
        if (u.protocol !== "https:" && !httpOk) return "O URL da instância tem de começar por https://.";
        if (u.username || u.password) return "O URL não pode conter credenciais.";
      } catch (e) { return "URL da instância inválido."; }
      return "";
    }

    async function wsTest() {
      const r = document.getElementById("ws-test-result");
      const base = document.getElementById("ws-base").value.trim();
      const token = document.getElementById("ws-token").value.trim();
      const project = document.getElementById("ws-project").value.trim();
      // Sem token, o teste nunca usa o token do servidor — pede-o logo.
      if (!token) return wsFormError("Indica o token de acesso para testar.");
      if (!project) return wsFormError("Indica o Project ID para testar.");
      const baseErr = wsBaseError(base);
      if (baseErr) return wsFormError(baseErr);
      r.textContent = "A testar…"; r.className = "settings-test-result";
      const h = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true",
                  "X-GL-Token": token, "X-GL-Project": project };
      if (base) h["X-GL-Base"] = base;
      try {
        const res = await fetch(`${BASE_URL}/gitlab/test`, { headers: h });
        const d = await res.json();
        if (d.ok) {
          wsTestName = d.name || "";
          r.textContent = `✓ ${d.name} · ${d.open_issues ?? "?"} issues abertas`;
          r.className = "settings-test-result ok";
        } else { wsFormError(d.error || "Não foi possível ligar."); }
      } catch (e) { wsFormError("Erro de ligação ao servidor"); }
    }

    function wsSave() {
      const name = document.getElementById("ws-name").value.trim();
      const base = document.getElementById("ws-base").value.trim();
      const token = document.getElementById("ws-token").value.trim();
      const project = document.getElementById("ws-project").value.trim();
      if (!token || !project) return wsFormError("Token e Project ID são obrigatórios.");
      const baseErr = wsBaseError(base);
      if (baseErr) return wsFormError(baseErr);
      // nome final: o que escreveste > último teste > parte final do namespace
      const finalName = name || (wsTestName ? wsTestName.split("/").pop().trim() : "") || "GitLab";
      if (wsEditing) {
        const ws = workspaces.list.find(x => x.id === wsEditing);
        if (ws) Object.assign(ws, { name: finalName, glBase: base, glToken: token, glProject: project });
        saveWorkspaces();
        if (wsEditing === workspaces.active) {
          // O projeto ativo mudou: re-desenha a conversa (os cartões pendentes do
          // projeto anterior desaparecem) e recarrega as estatísticas.
          persistHistory();
          applyGitlabHeaders(); updateHeaderForWs(); clearFollowups();
          if (!loading) restoreHistory();
          loadStats();
        }
        renderWorkspaceList();
        closeWsEditor();
      } else {
        const id = "ws_" + Math.random().toString(36).slice(2, 10);
        workspaces.list.push({ id, name: finalName, glBase: base, glToken: token, glProject: project });
        saveWorkspaces();
        renderWorkspaceList();   // mostra-o já na lista (mesmo se não puder trocar agora)
        closeWsEditor();
        if (!loading) switchWorkspace(id);   // entra no projeto novo (se não houver resposta a decorrer)
      }
    }

    async function wsDelete() {
      if (!wsEditing) return;
      const id = wsEditing;
      const ws = workspaces.list.find(x => x.id === id);
      // Não apagar o workspace ATIVO a meio de uma resposta (corromperia o histórico).
      if (loading && id === workspaces.active) {
        return wsFormError("Espera a resposta terminar antes de apagar este projeto.");
      }
      if (!(await uiConfirm(`Apagar o projeto «${ws?.name || "GitLab"}» da lista? (não apaga nada no GitLab)`,
                            { okText: "Apagar", danger: true }))) return;
      if (loading && id === workspaces.active) return;
      workspaces.list = workspaces.list.filter(x => x.id !== id);
      try { localStorage.removeItem(HIST_PREFIX + id); } catch (e) {}
      if (workspaces.active === id) {
        workspaces.active = workspaces.list[0].id;
        saveWorkspaces();
        applyGitlabHeaders(); updateHeaderForWs(); restoreHistory(); loadStats();
      } else {
        saveWorkspaces();
      }
      renderWorkspaceList();
      closeWsEditor();
    }

    // ── Painel de estatísticas (sidebar direita): issues + commits + MRs ──────
    let statsSeq = 0;
    async function loadStats() {
      const seq = ++statsSeq;                 // só a chamada mais recente pode renderizar
      const wsAtStart = workspaces.active;
      const body = document.getElementById("stats-body");
      body.innerHTML = '<div class="stats-empty">A carregar…</div>';
      try {
        const res = await fetch(`${BASE_URL}/gitlab/stats`, { headers });
        if (!res.ok) throw await responseError(res);
        const data = await res.json();
        if (seq !== statsSeq || wsAtStart !== workspaces.active) return;  // resposta obsoleta
        renderStats(data);
      } catch (e) {
        if (seq !== statsSeq) return;
        const detail = e instanceof ServerError ? ` ${e.message}` : "";
        body.innerHTML = `<div class="stats-empty">Não foi possível carregar as estatísticas.${escHtml(detail)}</div>`;
      }
    }
    function statRow(label, value, ellip) {
      return `<div class="stat-row"><span class="sr-label${ellip ? " ellip" : ""}">${escHtml(String(label))}</span>` +
             `<span class="sr-value">${escHtml(String(value))}</span></div>`;
    }
    function renderStats(d) {
      const body = document.getElementById("stats-body");
      const SECTION = { info: "projeto", issues: "issues", commits: "commits",
                        contributors: "contribuidores", milestones: "milestones", mrs: "merge requests" };
      let html = "";
      if (d.unavailable && d.unavailable.length) {
        html += `<div class="stats-note"><i class="fas fa-triangle-exclamation"></i> Indisponível: ` +
                `${escHtml(d.unavailable.map(k => SECTION[k] || k).join(", "))}</div>`;
      }
      if (d.project) {
        html += `<div class="stats-sec sec-proj"><h4><i class="fas fa-folder"></i> Projeto</h4>` +
                statRow("Nome", (d.project.name || "?").split("/").pop().trim()) +
                (d.project.commit_count != null ? statRow("Commits (total)", d.project.commit_count) : "") +
                (safeUrl(d.project.web_url) ? `<a class="stats-proj-link" href="${escHtml(safeUrl(d.project.web_url))}" target="_blank" rel="noopener noreferrer">Abrir no GitLab ↗</a>` : "") +
                `</div>`;
      }
      if (d.issues) {
        // Contagens a partir de uma lista truncada são aproximadas/mínimos.
        const approx = d.issues.exact === false ? "~" : "";
        const atLeast = d.issues.open_complete === false ? "≥ " : "";
        html += `<div class="stats-sec sec-issues"><h4><i class="fas fa-circle-dot"></i> Issues</h4>` +
                `<div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, Number(d.issues.progress) || 0)}%"></div></div>` +
                statRow("Progresso", approx + d.issues.progress + "%") +
                statRow("Abertas", approx + d.issues.open) +
                statRow("Fechadas", approx + d.issues.closed) +
                statRow("Em atraso", atLeast + d.issues.overdue) +
                statRow("Sem assignee", atLeast + d.issues.no_assignee) +
                (approx ? `<div class="stats-hint">Lista de issues limitada pelo servidor — valores aproximados.</div>` : "") +
                `</div>`;
      }
      if (d.contributors && d.contributors.top && d.contributors.top.length) {
        html += `<div class="stats-sec sec-contrib"><h4><i class="fas fa-users"></i> Contribuidores</h4>` +
                statRow("Autores", (d.contributors.complete === false ? "≥ " : "") + d.contributors.authors);
        for (const t of d.contributors.top) {
          html += statRow(t.name, t.commits, true);
        }
        if (d.contributors.capped)
          html += `<div class="stats-hint">Contagens dos commits mais recentes (limite do GitLab).</div>`;
        html += `</div>`;
      }
      if (d.commits && d.commits.last90d > 0) {
        const plus = d.commits.complete === false ? "+" : "";
        html += `<div class="stats-sec sec-activity"><h4><i class="fas fa-code-commit"></i> Atividade (90 dias)</h4>` +
                statRow("Commits", d.commits.last90d + plus) +
                statRow("Autores", d.commits.authors + plus);
        for (const t of (d.commits.top || []).slice(0, 3)) {
          html += statRow(t.name, t.count, true);
        }
        html += `</div>`;
      }
      if (d.mrs && d.mrs.total > 0) {
        const plus = d.mrs.complete === false ? "+" : "";
        html += `<div class="stats-sec sec-mrs"><h4><i class="fas fa-code-pull-request"></i> Merge Requests</h4>` +
                statRow("Abertos", d.mrs.open + plus) +
                statRow("Total", d.mrs.total + plus) +
                `</div>`;
      }
      if (d.milestones && d.milestones.length) {
        html += `<div class="stats-sec sec-ms"><h4><i class="fas fa-flag"></i> Milestones</h4>`;
        for (const m of d.milestones) {
          html += statRow(m.title, m.due_date || "—", true);
        }
        html += `</div>`;
      }
      body.innerHTML = html || '<div class="stats-empty">Sem dados para mostrar.</div>';
    }

    // ── Definições globais (gear): modelo + tema + chave de acesso ────────────
    function refreshModelDesc() {
      const sel = document.getElementById("set-model");
      const opt = sel.options[sel.selectedIndex];
      let raw = opt?.dataset.desc || "";
      if (opt && opt.value === "" && serverConfig.default_model) {
        raw += ` Atualmente: ${MODEL_LABELS[serverConfig.default_model] || serverConfig.default_model}.`;
      }
      const esc = escHtml(raw);
      document.getElementById("set-model-desc").innerHTML =
        esc.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    }
    function refreshAccessHint() {
      const hint = document.getElementById("set-access-hint");
      hint.textContent = serverConfig.writes === "disabled"
        ? "⚠️ Este servidor não tem alterações ativas no GitLab predefinido (falta o secret APP_ACCESS_KEY). Os projetos com o teu próprio token funcionam."
        : "Fica guardada só neste browser. Os projetos com o teu próprio token não precisam dela.";
    }
    function openSettings() {
      document.getElementById("set-model").value = settings.model;
      document.getElementById("set-theme").value = settings.theme;
      document.getElementById("set-access-key").value = settings.accessKey;
      refreshModelDesc();
      refreshAccessHint();
      document.getElementById("settings-overlay").hidden = false;
      document.getElementById("set-model").focus();
    }
    function closeSettings() {
      applyTheme();   // reverte pré-visualização de tema não guardada
      document.getElementById("settings-overlay").hidden = true;
    }
    function saveSettings() {
      settings.model = document.getElementById("set-model").value;
      settings.theme = document.getElementById("set-theme").value;
      settings.accessKey = document.getElementById("set-access-key").value.trim();
      saveSettingsToStorage();
      applyTheme(); applyModelBadge();
      document.getElementById("settings-overlay").hidden = true;
    }

    // ── Sidebars em modo overlay (ecrãs estreitos / tab do Teams) ─────────────
    function closeSidebar(id) { document.getElementById(id).classList.remove("open"); }
    function toggleSidebar(id) { document.getElementById(id).classList.toggle("open"); }

    // ── Cópia de segurança (workspaces + conversas + definições) ──────────────
    // Tudo vive no localStorage — trocar de browser/máquina perdia os dados.
    // O backup é um JSON local: o utilizador guarda e repõe onde quiser.
    async function backupExport() {
      const hasTokens = workspaces.list.some(w => w.glToken);
      if (hasTokens && !(await uiConfirm(
        "O ficheiro de backup inclui os TOKENS dos teus GitLabs (em claro).\n" +
        "Guarda-o num local seguro. Continuar?", { okText: "Exportar" }))) return;
      const data = {
        app: "sprintlab-chatbox", version: 2,
        exported_at: new Date().toISOString(),
        // a chave de acesso NÃO vai no backup (volta a introduzi-la no outro browser)
        settings: { model: settings.model, theme: settings.theme },
        workspaces: { active: workspaces.active, list: workspaces.list },
        histories: {},
      };
      persistHistory();
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith(HIST_PREFIX)) {
            try { data.histories[k] = JSON.parse(localStorage.getItem(k)); }
            catch (e) { /* histórico corrompido — não trava o backup */ }
          }
        }
      } catch (e) {
        // armazenamento do browser bloqueado: exporta pelo menos a conversa atual
        data.histories[HIST_PREFIX + workspaces.active] = history.slice(-HISTORY_CAP);
      }
      const blob = new Blob([JSON.stringify(data, null, 2)],
                            { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "sprintlab-backup.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }

    function backupRestore(file) {
      const reader = new FileReader();
      reader.onload = async () => {
        let data = null;
        try { data = JSON.parse(reader.result); } catch (e) {}
        const ws = normaliseWorkspaces(data && data.workspaces);
        if (!data || typeof data !== "object" || data.app !== "sprintlab-chatbox" || !ws.list.length) {
          await uiAlert("Ficheiro de backup inválido.");
          return;
        }
        if (!(await uiConfirm("Repor o backup substitui os GitLabs e conversas atuais. Continuar?",
                              { okText: "Repor", danger: true })))
          return;
        // Só entra o que é válido — um backup editado/antigo/malicioso não pode
        // deixar a app (incluindo o botão Repor) partida no arranque seguinte.
        if (!ws.list.some(x => x.builtin)) ws.list.unshift({ id: "default", name: "SprintLab", builtin: true });
        if (!ws.list.some(x => x.id === ws.active)) ws.active = ws.list[0].id;
        const st = data.settings && typeof data.settings === "object" ? data.settings : {};
        // backups < v2 (UI antiga) gravavam SEMPRE Llama 3.3 — não foi escolha
        // do utilizador: passa a seguir o modelo predefinido do servidor
        const legacy = !(Number(data.version) >= 2);
        const model = (legacy && st.model === "llama-3.3-70b-versatile") ? "" : st.model;
        const cleanSettings = {
          model: MODEL_LABELS[model] ? model : "",
          theme: THEMES.includes(st.theme) ? st.theme : settings.theme,
          accessKey: settings.accessKey,
        };
        const histories = {};
        const rawHist = data.histories && typeof data.histories === "object" ? data.histories : {};
        for (const [k, v] of Object.entries(rawHist)) {
          if (k.startsWith(HIST_PREFIX) && Array.isArray(v)) histories[k] = v.filter(validMsg).slice(-HISTORY_CAP);
        }
        try {
          localStorage.setItem(WS_KEY, JSON.stringify(ws));
          localStorage.setItem(SETTINGS_KEY, JSON.stringify(cleanSettings));
          // limpa históricos atuais e repõe os do backup
          for (let i = localStorage.length - 1; i >= 0; i--) {
            const k = localStorage.key(i);
            if (k && k.startsWith(HIST_PREFIX)) localStorage.removeItem(k);
          }
          for (const [k, v] of Object.entries(histories)) localStorage.setItem(k, JSON.stringify(v));
        } catch (e) {
          await uiAlert("Não foi possível repor o backup (armazenamento cheio?).");
          return;
        }
        location.reload();   // re-arranca com o estado reposto
      };
      reader.readAsText(file);
    }

    // Download via fetch (NÃO <a href>) para os headers X-GL-* irem com o pedido —
    // senão o servidor cai nas Secrets (SprintLab) e ignora o workspace ativo.
    // Devolve o nº de linhas se a exportação foi limitada pelo servidor.
    async function downloadCsv(url, filename) {
      const res = await fetch(url, { headers });
      if (!res.ok) throw await responseError(res);
      const truncated = res.headers.get("X-Export-Truncated");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      return truncated;
    }
    function exportDone(kind, truncated) {
      if (truncated)
        showInfo(`A exportação de ${kind} foi limitada às ${truncated} linhas mais recentes (limite de páginas do servidor).`);
    }
    function exportIssues(state) {
      downloadCsv(`${BASE_URL}/gitlab/export?state=${encodeURIComponent(state)}`,
                  "gitlab_issues.csv")
        .then(tr => exportDone("issues", tr))
        .catch(e => { showError(e instanceof ServerError ? e.message : "Não foi possível exportar as issues."); console.error(e); });
    }
    function exportCommits() {
      downloadCsv(`${BASE_URL}/gitlab/export?type=commits`, "gitlab_commits.csv")
        .then(tr => exportDone("commits", tr))
        .catch(e => { showError(e instanceof ServerError ? e.message : "Não foi possível exportar os commits."); console.error(e); });
    }

    // ── Relatório automático de sprint ────────────────────────────────────────
    // Card determinístico a partir de /gitlab/report (números exatos, sem o
    // modelo a "contar"). Botões para exportar .md e copiar.
    function addAiCard(html) {
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      bub.innerHTML = html;
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();
      return bub;
    }

    function repKpi(label, value, cls) {
      return `<div class="rep-kpi ${cls || ""}"><div class="rep-kpi-v">${escHtml(String(value))}</div>` +
             `<div class="rep-kpi-l">${escHtml(label)}</div></div>`;
    }

    function sprintReportHtml(d) {
      const s = d.summary || {}, a = d.activity90d || { top: [] };
      const c = d.contributors || { top: [] }, mrs = d.mrs || {};
      const hasIssues = d.has_issues, hasRecent = d.has_recent_activity;
      const badge = { ativo: ["Ativo", "st-active"], fase_final: ["Fase final", "st-final"],
                      concluido: ["Concluído", "st-done"],
                      vazio: ["Vazio", "st-empty"] }[d.status];

      let h = `<div class="report-card">`;
      h += `<div class="rep-head"><i class="fas fa-file-lines"></i>` +
           `<div><div class="rep-title">Relatório do projeto` +
           (badge ? ` <span class="rep-status ${badge[1]}">${escHtml(badge[0])}</span>` : "") +
           `</div>` +
           `<div class="rep-sub">${escHtml(d.project || "?")} · ${escHtml(d.generated_at || "")}</div></div></div>`;

      // Sobre — o que o projeto é (descrição, linguagens, estrutura)
      if (d.about && d.about.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-circle-info sec-i-blue"></i> Sobre</h5><ul class="rep-list">`;
        for (const x of d.about) h += `<li>${escHtml(x)}</li>`;
        h += `</ul></div>`;
      }

      if (d.highlights && d.highlights.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-star sec-i-amber"></i> Destaques</h5><ul class="rep-list">`;
        for (const x of d.highlights) h += `<li>${escHtml(x)}</li>`;
        h += `</ul></div>`;
      }

      // Issues — progresso/KPIs só quando o projeto as usa (0% ≠ "fase inicial")
      if (hasIssues) {
        const approx = s.exact === false ? "~" : "";
        h += `<div class="rep-progress"><div class="progress-track">` +
             `<div class="progress-fill" style="width:${Math.min(100, Number(s.progress) || 0)}%"></div></div>` +
             `<span class="rep-pct">${escHtml(approx + String(s.progress || 0))}%</span></div>`;
        h += `<div class="rep-kpis">` +
             repKpi("Abertas", approx + (s.open || 0), "k-blue") +
             repKpi("Fechadas", approx + (s.closed || 0), "k-green") +
             repKpi("Em atraso", (s.open_complete === false ? "≥" : "") + (s.overdue || 0), "k-amber") + `</div>`;
        if (d.overdue_issues && d.overdue_issues.length) {
          h += `<div class="rep-sec"><h5><i class="fas fa-triangle-exclamation sec-i-amber"></i> Issues em atraso</h5><ul class="rep-list">`;
          for (const i of d.overdue_issues)
            h += `<li>#${escHtml(String(i.iid))} ${escHtml(i.title)} <span class="rep-due">(${escHtml(i.due_date || "")})</span></li>`;
          h += `</ul></div>`;
        }
      } else if (s.known === false) {
        h += `<div class="rep-note"><i class="fas fa-circle-info"></i> Issues indisponíveis (erro ao ler do GitLab).</div>`;
      } else {
        h += `<div class="rep-note"><i class="fas fa-circle-info"></i> Este projeto não usa issues do GitLab.</div>`;
      }

      // Commits (sempre); Atividade (90d) só em projetos ativos
      const repoTot = (d.repo_commits != null)
        ? `${escHtml(String(d.repo_commits))} commits no repo · ` : "";
      let commitsSec = `<div class="rep-sec"><h5><i class="fas fa-users sec-i-blue"></i> Commits</h5>` +
        `<div class="rep-line">${repoTot}${escHtml(String(c.authors || 0) + (c.complete === false ? "+" : ""))} autores <span class="rep-dim">(por autor${c.capped ? ", commits mais recentes" : ""})</span></div>`;
      for (const t of (c.top || []).slice(0, 3))
        commitsSec += `<div class="rep-row"><span>${escHtml(t.name)}</span><b>${escHtml(String(t.commits))}</b></div>`;
      commitsSec += `</div>`;
      if (hasRecent) {
        const plus = a.complete === false ? "+" : "";
        let actSec = `<div class="rep-sec"><h5><i class="fas fa-code-commit sec-i-green"></i> Atividade (90d)</h5>` +
          `<div class="rep-line">${escHtml(String(a.commits || 0) + plus)} commits · ${escHtml(String(a.authors || 0) + plus)} autores</div>`;
        for (const t of (a.top || []).slice(0, 3))
          actSec += `<div class="rep-row"><span>${escHtml(t.name)}</span><b>${escHtml(String(t.count))}</b></div>`;
        actSec += `</div>`;
        h += `<div class="rep-cols">${commitsSec}${actSec}</div>`;
      } else {
        h += commitsSec;   // largura total — sem atividade recente a omitir
      }

      if (mrs.total) {
        const plus = mrs.complete === false ? "+" : "";
        h += `<div class="rep-sec"><h5><i class="fas fa-code-pull-request sec-i-teal"></i> Merge Requests</h5>` +
             `<div class="rep-line">${escHtml(String(mrs.open || 0) + plus)} abertos · ${escHtml(String(mrs.total) + plus)} no total</div></div>`;
      }
      if (d.milestones && d.milestones.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-flag sec-i-pink"></i> Milestones</h5><ul class="rep-list">`;
        for (const m of d.milestones) {
          const tag = m.status === "overdue" ? ' <span class="rep-tag late">atrasado</span>'
                    : m.status === "soon" ? ' <span class="rep-tag soon">em breve</span>' : "";
          h += `<li>${escHtml(m.title || "?")} <span class="rep-due">(${escHtml(m.due_date || "—")})</span>${tag}</li>`;
        }
        if (d.milestones_more > 0)
          h += `<li class="rep-dim">… e mais ${escHtml(String(d.milestones_more))} milestone(s)</li>`;
        h += `</ul></div>`;
      }

      // Proveniência (anti-"workslop"): o relatório não tem nada para rever —
      // todos os valores vêm do GitLab, calculados em código, sem a IA a contar.
      h += `<div class="prov-foot"><i class="fas fa-shield-halved"></i> ` +
           `Relatório 100% determinístico — todos os valores vêm diretamente do GitLab. ` +
           `Nenhum número é gerado por IA.</div>`;
      h += `<div class="rep-actions">` +
           `<button type="button" class="rep-btn" data-rep="md"><i class="fas fa-download"></i> Exportar .md</button>` +
           `<button type="button" class="rep-btn ghost" data-rep="copy"><i class="fas fa-copy"></i> Copiar</button></div>`;
      return h + `</div>`;
    }

    function wireReportActions(bub, d) {
      const md = bub.querySelector('[data-rep="md"]');
      const cp = bub.querySelector('[data-rep="copy"]');
      if (md) md.onclick = () => {
        const blob = new Blob([d.markdown || ""], { type: "text/markdown;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "relatorio-projeto.md";
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      };
      if (cp) cp.onclick = async () => {
        try { await navigator.clipboard.writeText(d.markdown || ""); cp.innerHTML = '<i class="fas fa-check"></i> Copiado'; }
        catch (e) { cp.innerHTML = "Falhou"; }
        setTimeout(() => { cp.innerHTML = '<i class="fas fa-copy"></i> Copiar'; }, 1800);
      };
    }

    async function renderSprintReport() {
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A gerar o relatório do projeto…</div>');
      try {
        const res = await fetch(`${BASE_URL}/gitlab/report`, { headers });
        if (!res.ok) throw await responseError(res);
        const d = await res.json();
        bub.innerHTML = sprintReportHtml(d);
        wireReportActions(bub, d);
        scrollBottom();
        return true;
      } catch (e) {
        const msg = e instanceof ServerError ? e.message : "Não foi possível gerar o relatório.";
        bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(msg)}</div>`;
        console.error(e);
        return false;
      }
    }

    // ── Investigação de código (blame + diff + análise IA) ────────────────────
    function blameShaHtml(shortSha, url, small) {
      const u = safeUrl(url);
      const cls = "bl-sha" + (small ? " sm" : "");
      return u ? `<a class="${cls}" href="${escHtml(u)}" target="_blank" rel="noopener noreferrer">${escHtml(shortSha)}</a>`
               : `<span class="${cls}">${escHtml(shortSha)}</span>`;
    }

    function blameCardHtml(d) {
      let h = `<div class="blame-card">`;
      h += `<div class="rep-head"><i class="fas fa-magnifying-glass bl-ico"></i>` +
           `<div><div class="rep-title">Investigação de código</div>` +
           `<div class="rep-sub">${escHtml(d.file)}${d.line ? ":" + escHtml(String(d.line)) : ""} · ref ${escHtml(d.ref || "?")}</div></div></div>`;

      if (d.note) h += `<div class="bl-note"><i class="fas fa-circle-info"></i> ${escHtml(d.note)}</div>`;

      if (d.facts) {
        const f = d.facts;
        h += `<div class="bl-facts">` +
             `<div class="bl-facts-t"><i class="fas fa-fingerprint"></i> Última alteração desta linha (git blame)</div>` +
             `<div class="bl-fact-row">${blameShaHtml(f.short_sha, f.commit_url)}` +
             `<span><i class="fas fa-user"></i> ${escHtml(f.author)}</span>` +
             `<span><i class="fas fa-calendar"></i> ${escHtml(f.date)}</span></div>` +
             `<div class="bl-msg">“${escHtml(f.message)}”</div>`;
        if (d.line_text)
          h += `<pre class="bl-line"><code>${escHtml(String(d.line))} | ${escHtml(d.line_text)}</code></pre>`;
        h += `</div>`;
      }

      if (d.analysis) {
        h += `<div class="rep-sec"><h5><i class="fas fa-wand-magic-sparkles sec-i-amber"></i> Análise da IA</h5>` +
             `<div class="bl-analysis">${renderMd(d.analysis)}</div></div>`;
      } else if (d.line && d.facts) {
        h += `<div class="bl-note"><i class="fas fa-circle-info"></i> Análise IA indisponível neste momento — os factos acima vêm diretamente do GitLab.</div>`;
      }

      if (d.authors && d.authors.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-users sec-i-blue"></i> Quem mais alterou este ficheiro</h5>`;
        for (const a of d.authors)
          h += `<div class="rep-row"><span>${escHtml(a.name)}</span><b>${escHtml(String(a.count))}</b></div>`;
        h += `</div>`;
      }

      if (d.history && d.history.length) {
        h += `<div class="rep-sec"><h5><i class="fas fa-clock-rotate-left sec-i-teal"></i> Últimos commits neste ficheiro</h5><div class="bl-hist">`;
        for (const c of d.history) {
          h += `<div class="bl-hist-row">${blameShaHtml(c.short_sha, c.commit_url, true)}` +
               `<span class="bl-hist-title">${escHtml(c.title)}</span>` +
               `<span class="bl-hist-meta">${escHtml(c.author)} · ${escHtml(c.date)}</span></div>`;
        }
        h += `</div></div>`;
      }

      h += `<div class="prov-foot"><i class="fas fa-shield-halved"></i> Factos (commit, autor, data) via git blame do GitLab — determinísticos. A análise do erro é uma hipótese gerada por IA.</div>`;
      return h + `</div>`;
    }

    async function renderBlameAnalysis(intent, originalText) {
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A investigar… (git blame + diff' + (intent.line ? " + análise IA" : "") + ')</div>');
      try {
        const res = await fetch(`${BASE_URL}/api/analyze-code`, {
          method: "POST",
          headers,   // leva os X-GL-* do workspace ativo (multi-tenant)
          body: JSON.stringify({
            file: intent.file, line: intent.line,
            question: originalText, model: modelParam(),
          }),
        });
        if (!res.ok) throw await responseError(res);
        const d = await res.json();
        if (!d.ok) {
          bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(d.error || "Não foi possível investigar.")}</div>`;
          return;
        }
        bub.innerHTML = blameCardHtml(d);
        scrollBottom();
      } catch (e) {
        const msg = e instanceof ServerError ? e.message : "Erro ao investigar o código.";
        bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(msg)}</div>`;
        console.error(e);
      }
    }

    // ── Commit por IA (gera plano → confirmação → branch ai/* + commit + MR) ──
    function commitPreviewHtml(p) {
      let h = `<div class="commit-card">`;
      h += `<div class="rep-head"><i class="fas fa-code-commit"></i><div>` +
           `<div class="rep-title">Commit por IA — pré-visualização</div>` +
           `<div class="rep-sub">branch <code>${escHtml(p.branch)}</code> · com Merge Request para revisão</div></div></div>`;
      if (p.summary) h += `<div class="cc-summary">${escHtml(p.summary)}</div>`;
      h += `<div class="cc-msg"><i class="fas fa-message"></i> ${escHtml(p.commit_message)}</div>`;
      for (const f of p.files || []) {
        h += `<div class="cc-file"><div class="cc-path"><i class="fas fa-file-code"></i> ${escHtml(f.path)}</div>` +
             `<pre class="cc-code"><code>${escHtml(f.content)}</code></pre></div>`;
      }
      h += `<div class="cc-note"><i class="fas fa-shield-halved"></i> Nada foi escrito ainda. ` +
           `Ao confirmar é criada a branch <b>${escHtml(p.branch)}</b> + commit + Merge Request — ` +
           `a branch principal não é alterada e nenhum pipeline corre antes da revisão.</div>`;
      h += `<div class="rep-actions">` +
           `<button type="button" class="rep-btn cc-approve"><i class="fas fa-check"></i> Criar commit + MR</button>` +
           `<button type="button" class="rep-btn ghost cc-cancel">Cancelar</button></div>`;
      return h + `</div>`;
    }

    function commitResultHtml(r) {
      const cu = safeUrl(r.commit_url), mu = safeUrl(r.mr_url);
      let h = `<div class="commit-card">` +
        `<div class="confirm-msg ok"><i class="fas fa-circle-check"></i> Commit criado com sucesso!</div>` +
        `<div class="cc-result">` +
        `<div class="rep-row"><span>Branch</span><b>${escHtml(r.branch || "")}</b></div>` +
        `<div class="rep-row"><span>Commit</span><b>${escHtml(r.commit_sha || "")}</b></div>` +
        `<div class="rep-row"><span>Ficheiros</span><b>${escHtml((r.files || []).join(", "))}</b></div>` +
        `</div><div class="rep-actions">`;
      if (mu) h += `<a class="rep-btn" href="${escHtml(mu)}" target="_blank" rel="noopener noreferrer"><i class="fas fa-code-pull-request"></i> Abrir o Merge Request</a>`;
      if (cu) h += `<a class="rep-btn ghost" href="${escHtml(cu)}" target="_blank" rel="noopener noreferrer">Ver o commit</a>`;
      h += `</div>`;
      if (!mu) h += `<div class="cc-note"><i class="fas fa-circle-info"></i> O commit foi criado, mas o Merge Request falhou${r.mr_error ? ` (${escHtml(r.mr_error)})` : ""} — podes abri-lo manualmente no GitLab.</div>`;
      h += `<div class="cc-note"><i class="fas fa-circle-info"></i> O commit tem <code>[skip ci]</code>: corre o pipeline no MR depois de rever o código.</div>`;
      return h + `</div>`;
    }

    // Escritas com o token do servidor desligadas (sem APP_ACCESS_KEY) e este
    // projeto usa esse token (sem X-GL-*): avisar logo, sem gastar o modelo.
    function writesDisabledFor(ctx) {
      const h = ctx.headers;
      return serverConfig.writes === "disabled"
        && !h["X-GL-Token"] && !h["X-GL-Base"] && !h["X-GL-Project"];
    }
    const WRITES_DISABLED_MSG = "As alterações no GitLab predefinido estão desativadas neste " +
      "servidor (falta o secret APP_ACCESS_KEY). Ler, conversar, gráficos e relatórios " +
      "continuam a funcionar; num projeto com o teu próprio token podes alterar o GitLab.";

    async function renderCommitFlow(requestText) {
      const ctx = snapshotCtx();
      if (writesDisabledFor(ctx)) {
        addAiCard(`<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(WRITES_DISABLED_MSG)}</div>`);
        return;
      }
      const bub = addAiCard('<div class="confirm-msg"><i class="fas fa-spinner fa-spin"></i> A gerar o código e o plano de commit…</div>');
      let plan = null;
      try {
        const res = await fetch(`${BASE_URL}/api/generate-commit`, {
          method: "POST",
          headers: ctx.headers,   // leva os X-GL-* do workspace ativo (multi-tenant)
          body: JSON.stringify({ request: requestText, model: modelParam() }),
        });
        if (!res.ok) throw await responseError(res);
        const d = await res.json();
        if (!d.ok) {
          bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(d.error || "Não consegui gerar o plano.")}</div>`;
          return;
        }
        plan = d.plan;
      } catch (e) {
        const msg = e instanceof ServerError ? e.message : "Erro ao contactar o servidor.";
        bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(msg)}</div>`;
        console.error(e);
        return;
      }

      bub.innerHTML = commitPreviewHtml(plan);
      scrollBottom();
      const ok = bub.querySelector(".cc-approve");
      const no = bub.querySelector(".cc-cancel");
      no.onclick = () => {
        bub.innerHTML = '<div class="confirm-msg">Commit cancelado — nada foi escrito no GitLab.</div>';
      };
      ok.onclick = async () => {
        if (!ctxStillValid(ctx)) {
          bub.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(STALE_CTX_MSG)}</div>`;
          return;
        }
        ok.disabled = no.disabled = true;
        ok.innerHTML = '<i class="fas fa-spinner fa-spin"></i> A criar branch + commit + MR…';
        try {
          const res = await fetch(`${BASE_URL}/api/confirm-commit`, {
            method: "POST", headers: writeHeaders(ctx), body: JSON.stringify({ plan }),
          });
          let r = {};
          try { r = await res.json(); } catch (e) { r = { error: `Erro do servidor (HTTP ${res.status}).` }; }
          if (r.ok) {
            bub.innerHTML = commitResultHtml(r);
            appendToHistory(ctx.wsId, { role: "assistant", content: `[Commit criado na branch ${r.branch}]` });
          } else if (r.code === "access_key") {
            // sem chave: mantém a pré-visualização para tentar outra vez
            ok.disabled = no.disabled = false;
            ok.innerHTML = '<i class="fas fa-check"></i> Criar commit + MR';
            bub.querySelector(".cc-auth")?.remove();
            const note = document.createElement("div");
            note.className = "cc-auth";
            note.innerHTML = actionErrorHtml(r, "");
            bub.querySelector(".commit-card").appendChild(note);
          } else {
            bub.innerHTML = actionErrorHtml(r, "Não foi possível criar o commit.");
          }
        } catch (e) {
          bub.innerHTML = '<div class="confirm-msg err">Erro ao executar o commit.</div>';
          console.error(e);
        }
        scrollBottom();
      };
    }

    // ── Streaming chat ────────────────────────────────────────────────────────
    async function streamChat(turn) {
      const bub = createStreamBubble();
      turn.bubble = bub;
      const ctx = snapshotCtx();
      let fullText = "";
      let pendingAction = null;

      activeController = new AbortController();
      setSendButton("loading");   // só o streaming pode ser cancelado

      try {
        const res = await fetch(`${BASE_URL}/api/chat`, {
          method: "POST",
          headers: ctx.headers,
          body: JSON.stringify({ model: modelParam(), messages: [...history] }),
          signal: activeController.signal,
        });
        if (!res.ok) throw await responseError(res);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const handleLine = (line) => {
          if (!line.startsWith("data: ")) return;
          let chunk;
          try { chunk = JSON.parse(line.slice(6)); } catch (e) { return; /* ignore malformed line */ }
          if (chunk.action) pendingAction = chunk.action;
          if (chunk.reset) {
            // 2.ª passagem (read tools): descartar o texto da 1.ª passagem
            // para a resposta final não vir concatenada com o "rascunho".
            fullText = "";
            const raw = bub.querySelector(".stream-raw");
            if (raw) raw.textContent = "";
          }
          if (chunk.content) {
            fullText += chunk.content;
            appendStreamChunk(bub, chunk.content);
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();
          for (const line of lines) handleLine(line.replace(/\r$/, ""));
        }
        buffer += decoder.decode();
        if (buffer) handleLine(buffer.replace(/\r$/, ""));

        if (pendingAction) {
          if (fullText.trim()) {
            // The model said something before proposing the action — keep it
            // (render it) and show the card/form in a new bubble.
            finalizeStreamBubble(bub, fullText);
            showActionCard(pendingAction, ctx);
          } else if (pendingAction.tool === "update_issue" && pendingAction.args && pendingAction.args.iid) {
            // Updates open the PRE-FILLED form (ignore any fields the model invented).
            bub.closest(".msg")?.remove();
            await openEditForm(pendingAction.args.iid, ctx);
          } else {
            renderConfirmCard(bub, pendingAction, ctx);
          }
          return { action: pendingAction };
        }
        finalizeStreamBubble(bub, fullText);
      } catch (e) {
        if (e.name === "AbortError") {
          // Cancelado (antes ou durante a resposta): a pergunta fica na conversa.
          const text = (fullText ? fullText + "\n\n" : "") + "_(cancelado)_";
          finalizeStreamBubble(bub, text);
          return { text };
        }
        throw e;
      } finally {
        activeController = null;
        setSendButton("busy");
      }

      return { text: fullText.trim() || "Sem resposta." };
    }

    // ── Confirmation card for write actions (created only after you approve) ──
    // Used both by the model path (streamChat) and by explicit commands (showActionCard).
    function showActionCard(action, ctx) {
      ctx = ctx || snapshotCtx();
      // Updates go through the pre-filled edit form (never the model's invented
      // fields); only close/delete use a plain confirmation card.
      if (action.tool === "update_issue" && action.args && action.args.iid) {
        openEditForm(action.args.iid, ctx);
        return;
      }
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      renderConfirmCard(bub, action, ctx);
    }

    function renderConfirmCard(bub, action, ctx) {
      ctx = ctx || snapshotCtx();
      const typing = bub.querySelector(".typing"); if (typing) typing.remove();
      const cursor = bub.querySelector(".cursor"); if (cursor) cursor.remove();
      const raw = bub.querySelector(".stream-raw"); if (raw) raw.remove();

      const danger = action.tool === "delete_issue";
      const card = document.createElement("div");
      card.className = "confirm-card" + (danger ? " danger" : "");
      bub.appendChild(card);

      const icon = danger ? "fa-triangle-exclamation" : "fa-circle-question";
      const build = (summaryText) => {
        card.innerHTML =
          `<div class="confirm-summary"><i class="fas ${icon}"></i> ${escHtml(summaryText)}</div>`;
        const btns = document.createElement("div");
        btns.className = "confirm-btns";
        const yes = document.createElement("button");
        yes.type = "button";
        yes.className = "confirm-yes" + (danger ? " danger" : "");
        yes.textContent = danger ? "Apagar" : "Confirmar";
        yes.onclick = () => doConfirm(card, action, ctx);
        const no = document.createElement("button");
        no.type = "button"; no.className = "confirm-no"; no.textContent = "Cancelar";
        no.onclick = () => { card.innerHTML = '<div class="confirm-msg">Ação cancelada.</div>'; };
        btns.appendChild(yes); btns.appendChild(no);
        card.appendChild(btns);
        scrollBottom();
      };

      // For issue actions, VERIFY the issue exists and show its title first —
      // so a hallucinated/wrong number can't be confirmed blindly (esp. delete).
      const iid = action.args && action.args.iid;
      if (["close_issue", "update_issue", "delete_issue"].includes(action.tool) && iid) {
        card.innerHTML = '<div class="confirm-msg">A verificar a issue…</div>';
        fetch(`${BASE_URL}/gitlab/issue/${encodeURIComponent(String(iid))}`, { headers: ctx.headers })
          .then(async r => {
            if (r.ok) return r.json();
            if (r.status === 404) throw null;            // só um 404 quer dizer "não existe"
            throw await responseError(r);                // 503 ocupado, 502 GitLab, 429 limites…
          })
          .then(d => build(`${action.summary} — «${d.title}»`))
          .catch((e) => {
            const msg = e === null ? `Não encontrei a issue #${String(iid)}. Confirma o número.`
                      : e instanceof ServerError ? e.message
                      : "Não foi possível verificar a issue — tenta de novo.";
            card.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(msg)}</div>`;
            scrollBottom();
          });
      } else {
        build(action.summary || "Confirmar esta ação?");
      }
      scrollBottom();
    }

    async function doConfirm(card, action, ctx) {
      if (!ctxStillValid(ctx)) {
        card.innerHTML = `<div class="confirm-msg err"><i class="fas fa-triangle-exclamation"></i> ${escHtml(STALE_CTX_MSG)}</div>`;
        return;
      }
      const btns = card.querySelector(".confirm-btns");
      const btnsHtml = btns ? btns.innerHTML : "";
      if (btns) btns.innerHTML = '<span class="confirm-msg">A executar…</span>';
      try {
        const res = await fetch(`${BASE_URL}/api/confirm-action`, {
          method: "POST", headers: writeHeaders(ctx),
          body: JSON.stringify({ tool: action.tool, args: action.args }),
        });
        let r = {};
        try { r = await res.json(); } catch (e) { r = { error: `Erro do servidor (HTTP ${res.status}).` }; }
        if (r.ok) {
          const verb = r.action === "create" ? "criada"
                     : r.action === "close" ? "fechada"
                     : r.action === "delete" ? "apagada"
                     : "atualizada";
          let html = `<div class="confirm-msg ok"><i class="fas fa-check"></i> Issue #${escHtml(String(r.iid ?? action.args?.iid ?? "?"))} ${verb} com sucesso.</div>`;
          if (safeUrl(r.web_url)) html += `<a class="confirm-link" href="${escHtml(safeUrl(r.web_url))}" target="_blank" rel="noopener noreferrer">Abrir no GitLab</a>`;
          card.innerHTML = html;
          if (ctxStillValid(ctx)) loadStats();   // a escrita mudou os números → refresca o painel
        } else if (r.code === "access_key" && btns) {
          // Falta a chave: mantém o cartão para confirmar outra vez depois de a introduzir.
          btns.innerHTML = btnsHtml;
          btns.querySelector(".confirm-yes").onclick = () => doConfirm(card, action, ctx);
          btns.querySelector(".confirm-no").onclick = () => { card.innerHTML = '<div class="confirm-msg">Ação cancelada.</div>'; };
          card.querySelector(".cc-auth")?.remove();
          const note = document.createElement("div");
          note.className = "cc-auth";
          note.innerHTML = actionErrorHtml(r, "");
          card.appendChild(note);
        } else {
          card.innerHTML = actionErrorHtml(r, "Não foi possível executar.");
        }
      } catch (e) {
        card.innerHTML = '<div class="confirm-msg err">Erro ao executar a ação.</div>';
        console.error(e);
      }
      scrollBottom();
    }

    // ── Issue form — create OR edit (edit pre-fills every field) ─────────────
    function renderIssueForm(opts, ctx) {
      ctx = ctx || snapshotCtx();
      const isEdit = (opts.mode || "create") === "edit";
      removeWelcome();
      const msgs = document.getElementById("messages");
      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar"; av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble issue-form";
      bub.innerHTML = `
        <div class="cf-head">${isEdit ? "Editar issue #" + escHtml(String(opts.iid)) : "Nova issue"}</div>
        ${isEdit ? "" : `<div class="cf-templates">
          <span class="cf-tlabel">Template:</span>
          <button type="button" class="cf-tmpl active" data-tmpl="">Vazio</button>
          <button type="button" class="cf-tmpl" data-tmpl="bug">Bug</button>
          <button type="button" class="cf-tmpl" data-tmpl="task">Tarefa</button>
        </div>`}
        <div class="cf-field">
          <span class="cf-tlabel">Título *</span>
          <input type="text" class="cf-input cf-titlein" placeholder="Título da issue" aria-label="Título" />
          <div class="cf-dup"></div>
        </div>
        <div class="cf-field">
          <span class="cf-tlabel">Descrição
            <button type="button" class="cf-ai"><i class="fas fa-wand-magic-sparkles"></i> Gerar com IA</button>
          </span>
          <textarea class="cf-input cf-desc" rows="4" placeholder="Descrição (opcional)" aria-label="Descrição"></textarea>
        </div>
        <div class="cf-field">
          <span class="cf-tlabel">Labels</span>
          <div class="cf-labels"><span class="cf-muted">a carregar…</span></div>
          <input type="text" class="cf-input cf-customlabel" placeholder="escrever label e Enter (opcional)" aria-label="Nova label" />
        </div>
        <div class="cf-row">
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Milestone</span>
            <select class="cf-input cf-milestone" aria-label="Milestone"><option value="">Nenhuma</option></select>
          </div>
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Data limite</span>
            <input type="date" class="cf-input cf-due" aria-label="Data limite" />
          </div>
        </div>
        <div class="cf-row">
          <div class="cf-field cf-half">
            <span class="cf-tlabel">Assignee</span>
            <select class="cf-input cf-assignee" aria-label="Assignee"><option value="">Ninguém</option></select>
            <div class="cf-muted cf-assignee-note" hidden></div>
          </div>
          <div class="cf-field cf-half cf-conf-field">
            <label class="cf-checkrow"><input type="checkbox" class="cf-confidential" /> Confidencial</label>
          </div>
        </div>
        <div class="cf-actions">
          <button type="button" class="cf-create">${isEdit ? "Guardar alterações" : "Criar issue"}</button>
          <button type="button" class="cf-cancel">Cancelar</button>
          <span class="cf-status" role="status"></span>
        </div>`;
      wrap.appendChild(av); wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();

      const selectedLabels = new Set(opts.labels || []);
      let currentTemplate = "";
      const $ = (sel) => bub.querySelector(sel);
      const titleIn = $(".cf-titlein"), descIn = $(".cf-desc"), dupBox = $(".cf-dup");
      const labelsBox = $(".cf-labels"), customLabel = $(".cf-customlabel");
      const msSelect = $(".cf-milestone"), dueIn = $(".cf-due"), statusEl = $(".cf-status");
      const asSelect = $(".cf-assignee"), confIn = $(".cf-confidential");

      titleIn.value = opts.title || "";
      descIn.value = opts.description || "";
      dueIn.value = opts.dueDate || "";
      confIn.checked = !!opts.confidential;
      titleIn.focus();

      // Garante que o valor atual existe como <option> antes de o selecionar —
      // senão um valor sem option faz o <select> cair para "" e, ao guardar,
      // desassociava a milestone/assignee em silêncio.
      const ensureOption = (sel, value, label) => {
        const v = String(value);
        if (![...sel.options].some(o => o.value === v)) {
          const opt = document.createElement("option");
          opt.value = v; opt.textContent = label;
          sel.appendChild(opt);
        }
      };
      // Valores atuais selecionados JÁ (não só quando as listas chegarem): guardar
      // antes de as listas carregarem não pode apagar a milestone/assignee.
      const origMilestone = opts.milestoneId ? String(opts.milestoneId) : "";
      const assigneeIds = (opts.assigneeIds || []).map(String);
      const assigneeNames = opts.assigneeNames || [];
      const origAssignee = assigneeIds[0] || "";
      if (origMilestone) {
        ensureOption(msSelect, origMilestone, opts.milestoneTitle || `#${origMilestone}`);
        msSelect.value = origMilestone;
      }
      if (origAssignee) {
        ensureOption(asSelect, origAssignee, assigneeNames[0] || `#${origAssignee}`);
        asSelect.value = origAssignee;
      }
      if (assigneeIds.length > 1) {
        const note = $(".cf-assignee-note");
        note.hidden = false;
        note.textContent = `Tem ${assigneeIds.length} assignees (${assigneeNames.join(", ")}) — só mudam se escolheres outro.`;
      }
      const showStatus = (message, isErr, r) => {
        statusEl.className = "cf-status" + (isErr ? " err" : "");
        statusEl.textContent = message;
        if (r && r.code === "access_key") {
          const b = document.createElement("button");
          b.type = "button"; b.className = "confirm-link-btn"; b.dataset.openSettings = "";
          b.innerHTML = '<i class="fas fa-key"></i> Introduzir a chave';
          statusEl.appendChild(document.createTextNode(" "));
          statusEl.appendChild(b);
        }
      };

      // Templates pre-fill the description scaffold
      bub.querySelectorAll(".cf-tmpl").forEach(b => {
        b.onclick = () => {
          currentTemplate = b.dataset.tmpl;
          bub.querySelectorAll(".cf-tmpl").forEach(x => x.classList.remove("active"));
          b.classList.add("active");
          if (currentTemplate === "bug")
            descIn.value = "**O que acontece:**\n\n**Passos para reproduzir:**\n1. \n2. \n\n**Resultado esperado:**\n";
          else if (currentTemplate === "task")
            descIn.value = "**Objetivo:**\n\n**Critérios de aceitação:**\n- [ ] ";
        };
      });

      // AI-generated description from the title
      $(".cf-ai").onclick = async () => {
        const title = titleIn.value.trim();
        if (!title) { titleIn.focus(); return; }
        const btn = $(".cf-ai"); const old = btn.innerHTML;
        btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> A gerar…';
        try {
          const res = await fetch(`${BASE_URL}/api/generate-description`, {
            method: "POST", headers: ctx.headers,
            body: JSON.stringify({ title, template: currentTemplate, model: modelParam() }),
          });
          const d = await res.json();
          if (d.description) { descIn.value = d.description; showStatus("", false); }
          else showStatus(d.error || "Não foi possível gerar a descrição.", true);
        } catch (e) {
          showStatus("Não foi possível gerar a descrição.", true);
        }
        btn.disabled = false; btn.innerHTML = old;
      };

      // Duplicate-title warning (debounced) — only when creating a new issue
      let dupTimer = null;
      if (!isEdit) titleIn.oninput = () => {
        clearTimeout(dupTimer);
        const title = titleIn.value.trim();
        if (title.length < 3) { dupBox.innerHTML = ""; return; }
        dupTimer = setTimeout(async () => {
          try {
            const res = await fetch(`${BASE_URL}/gitlab/duplicate?title=${encodeURIComponent(title)}`, { headers: ctx.headers });
            const d = await res.json();
            if (d.matches && d.matches.length)
              dupBox.innerHTML = `<i class="fas fa-triangle-exclamation"></i> Parecido com: ` +
                d.matches.map(m => `#${escHtml(String(m.iid))} ${escHtml(m.title)}`).join(" · ");
            else dupBox.innerHTML = "";
          } catch (e) { dupBox.innerHTML = ""; }
        }, 450);
      };

      // Load real project labels as toggle chips, pre-selecting the issue's current ones
      const addLabelChip = (name, on) => {
        const chip = document.createElement("button");
        chip.type = "button"; chip.className = "cf-labelchip" + (on ? " on" : ""); chip.textContent = name;
        chip.setAttribute("aria-pressed", on ? "true" : "false");
        chip.onclick = () => {
          if (selectedLabels.has(name)) { selectedLabels.delete(name); chip.classList.remove("on"); chip.setAttribute("aria-pressed", "false"); }
          else { selectedLabels.add(name); chip.classList.add("on"); chip.setAttribute("aria-pressed", "true"); }
        };
        labelsBox.appendChild(chip);
      };
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/labels`, { headers: ctx.headers });
          const data = await res.json().catch(() => ({}));
          const failed = !res.ok || !!data.error;          // servidor ocupado / erro do GitLab
          const labels = (res.ok && data.labels) || [];
          labelsBox.innerHTML = "";
          const seen = new Set();
          labels.slice(0, 40).forEach(l => { seen.add(l.name); addLabelChip(l.name, selectedLabels.has(l.name)); });
          (opts.labels || []).forEach(n => { if (!seen.has(n)) addLabelChip(n, true); });
          if (!labelsBox.children.length)
            labelsBox.innerHTML = failed ? '<span class="cf-muted">erro ao carregar labels</span>'
                                         : '<span class="cf-muted">sem labels no projeto</span>';
        } catch (e) { labelsBox.innerHTML = '<span class="cf-muted">erro ao carregar labels</span>'; }
      })();

      // Custom label on Enter
      customLabel.onkeydown = (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const v = customLabel.value.trim();
        if (!v || selectedLabels.has(v)) { customLabel.value = ""; return; }
        selectedLabels.add(v); customLabel.value = "";
        const chip = document.createElement("button");
        chip.type = "button"; chip.className = "cf-labelchip on"; chip.textContent = v;
        chip.onclick = () => { selectedLabels.delete(v); chip.remove(); };
        labelsBox.appendChild(chip);
      };

      // Load milestones (the current one is already selected above)
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/milestones`, { headers: ctx.headers });
          const keep = msSelect.value;
          (await res.json()).milestones?.forEach(m => {
            if ([...msSelect.options].some(o => o.value === String(m.id))) return;
            const opt = document.createElement("option");
            opt.value = m.id; opt.textContent = m.title;
            msSelect.appendChild(opt);
          });
          msSelect.value = keep;
        } catch (e) { /* ignore */ }
      })();

      // Load project members for the assignee dropdown (current already selected)
      (async () => {
        try {
          const res = await fetch(`${BASE_URL}/gitlab/members`, { headers: ctx.headers });
          const keep = asSelect.value;
          (await res.json()).members?.forEach(m => {
            if ([...asSelect.options].some(o => o.value === String(m.id))) return;
            const opt = document.createElement("option");
            opt.value = m.id; opt.textContent = m.name;
            asSelect.appendChild(opt);
          });
          asSelect.value = keep;
        } catch (e) { /* ignore */ }
      })();

      $(".cf-cancel").onclick = () => { bub.innerHTML = `<div class="confirm-msg">${isEdit ? "Edição" : "Criação"} cancelada.</div>`; };

      $(".cf-create").onclick = async () => {
        const title = titleIn.value.trim();
        if (!title) { showStatus("Indica um título.", true); titleIn.focus(); return; }
        if (!ctxStillValid(ctx)) { showStatus(STALE_CTX_MSG, true); return; }
        const custom = customLabel.value.trim();
        if (custom) selectedLabels.add(custom);
        const args = {
          title,
          description: descIn.value,
          labels: Array.from(selectedLabels).join(","),
          due_date: dueIn.value || "",
          confidential: confIn.checked,
        };
        // Na edição, milestone/assignees só vão se o utilizador os MUDOU: o PUT do
        // GitLab substitui a lista inteira de assignees.
        if (!isEdit || msSelect.value !== origMilestone) args.milestone_id = msSelect.value || "";
        if (!isEdit || asSelect.value !== origAssignee)
          args.assignee_ids = asSelect.value ? [Number(asSelect.value)] : [];
        if (isEdit) args.iid = opts.iid;
        const btn = $(".cf-create"); btn.disabled = true;
        showStatus(isEdit ? "A guardar…" : "A criar…", false);
        try {
          const res = await fetch(`${BASE_URL}/api/confirm-action`, {
            method: "POST", headers: writeHeaders(ctx),
            body: JSON.stringify({ tool: isEdit ? "update_issue" : "create_issue", args }),
          });
          let r = {};
          try { r = await res.json(); } catch (e) { r = { error: `Erro do servidor (HTTP ${res.status}).` }; }
          if (r.ok) {
            const verb = isEdit ? "atualizada" : "criada";
            let html = `<div class="confirm-msg ok"><i class="fas fa-check"></i> Issue #${escHtml(String(r.iid ?? opts.iid ?? "?"))} ${verb} com sucesso.</div>`;
            if (safeUrl(r.web_url)) html += `<a class="confirm-link" href="${escHtml(safeUrl(r.web_url))}" target="_blank" rel="noopener noreferrer">Abrir no GitLab</a>`;
            bub.innerHTML = html;
            if (ctxStillValid(ctx)) loadStats();   // refresca o painel de estatísticas
          } else {
            btn.disabled = false;
            showStatus(r.error || "Não foi possível guardar.", true, r);
          }
        } catch (e) {
          btn.disabled = false;
          showStatus("Erro ao guardar.", true);
        }
        scrollBottom();
      };
    }

    function renderCreateForm(prefillTitle) {
      renderIssueForm({ mode: "create", title: prefillTitle || "" });
    }
    async function openEditForm(iid, ctx) {
      ctx = ctx || snapshotCtx();
      removeWelcome();
      try {
        const res = await fetch(`${BASE_URL}/gitlab/issue/${encodeURIComponent(String(iid))}`, { headers: ctx.headers });
        if (!res.ok) throw await responseError(res);
        const d = await res.json();
        if (!ctxStillValid(ctx)) return;   // mudou de projeto enquanto carregava
        const ids = Array.isArray(d.assignee_ids) ? d.assignee_ids
                  : (d.assignee_id ? [d.assignee_id] : []);
        const names = Array.isArray(d.assignee_names) ? d.assignee_names
                    : (d.assignee_name ? [d.assignee_name] : []);
        renderIssueForm({
          mode: "edit", iid: d.iid, title: d.title, description: d.description,
          labels: d.labels || [], milestoneId: d.milestone_id, milestoneTitle: d.milestone_title,
          dueDate: d.due_date, assigneeIds: ids, assigneeNames: names,
          confidential: d.confidential,
        }, ctx);
      } catch (e) {
        showError(`Não consegui carregar a issue #${iid}.`);
      }
    }

    // ── Chart rendering (inline in a chat bubble) ─────────────────────────────
    // As cores vêm do TEMA atual (variáveis CSS) e são reaplicadas quando o tema
    // muda. Os gráficos são destruídos quando a conversa é re-desenhada.
    const liveCharts = new Set();
    function chartColors() {
      const cs = getComputedStyle(document.documentElement);
      const v = (name, fallback) => (cs.getPropertyValue(name) || "").trim() || fallback;
      return { text: v("--text", "#f0f0f0"), muted: v("--muted", "#aaa"),
               border: v("--border", "#3a3b3d"), card: v("--bg-card", "#2e2f31"),
               accent: v("--purple", "#8B88F8") };
    }
    function applyChartTheme(chart) {
      const c = chartColors();
      const o = chart.options;
      if (o.plugins && o.plugins.legend && o.plugins.legend.labels) o.plugins.legend.labels.color = c.text;
      if (o.plugins && o.plugins.tooltip) {
        Object.assign(o.plugins.tooltip, { backgroundColor: c.card, borderColor: c.accent,
                                           titleColor: c.text, bodyColor: c.text });
      }
      for (const axis of Object.values(o.scales || {})) {
        if (axis.ticks) axis.ticks.color = c.muted;
        if (axis.grid) axis.grid.color = c.border;
      }
    }
    function refreshChartsTheme() {
      for (const chart of [...liveCharts]) {
        if (!chart.canvas || !chart.canvas.isConnected) { chart.destroy(); liveCharts.delete(chart); continue; }
        applyChartTheme(chart);
        chart.update("none");
      }
    }
    function destroyAllCharts() {
      for (const chart of liveCharts) chart.destroy();
      liveCharts.clear();
    }

    async function renderChartIntent(chartName, params = {}) {
      removeWelcome();
      const msgs = document.getElementById("messages");

      const wrap = document.createElement("div");
      wrap.className = "msg ai";
      const av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "AI";
      const bub = document.createElement("div");
      bub.className = "bubble";

      const typing = document.createElement("div");
      typing.className = "typing";
      typing.innerHTML = `<div class="dot"></div><div class="dot"></div><div class="dot"></div>`;
      bub.appendChild(typing);
      wrap.appendChild(av);
      wrap.appendChild(bub);
      msgs.appendChild(wrap);
      scrollBottom();

      try {
        const qs = new URLSearchParams(params).toString();
        const url = `${BASE_URL}/gitlab/chart/${encodeURIComponent(chartName)}${qs ? "?" + qs : ""}`;
        const res = await fetch(url, { headers });
        if (!res.ok) throw await responseError(res);
        const config = await res.json();
        if (typeof Chart === "undefined") throw new Error("a biblioteca de gráficos não carregou");

        typing.remove();

        const title = document.createElement("div");
        title.className = "chart-title";
        title.textContent = config.title;
        bub.appendChild(title);

        const container = document.createElement("div");
        container.className = "chart-container";
        const canvas = document.createElement("canvas");
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", config.title || "Gráfico");
        container.appendChild(canvas);
        bub.appendChild(container);

        const c = chartColors();
        const isCircular = config.type === "pie" || config.type === "doughnut";
        const chart = new Chart(canvas, {
          type: config.type,
          data: { labels: config.labels, datasets: config.datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                display: isCircular,
                position: "bottom",
                labels: { color: c.text, font: { size: 10 }, padding: 8, boxWidth: 12 },
              },
              tooltip: {
                backgroundColor: c.card,
                borderColor: c.accent,
                borderWidth: 1,
                titleColor: c.text,
                bodyColor: c.text,
              },
            },
            scales: isCircular ? {} : {
              x: { ticks: { color: c.muted, font: { size: 10 } }, grid: { color: c.border } },
              y: {
                ticks: { color: c.muted, font: { size: 10 }, precision: 0 },
                grid: { color: c.border },
                beginAtZero: true,
              },
            },
          },
        });
        liveCharts.add(chart);

        return config.title;
      } catch (e) {
        typing.remove();
        const err = document.createElement("div");
        err.className = "chart-error";
        err.textContent = `Não consegui gerar o gráfico: ${e.message}`;
        bub.appendChild(err);
        console.error(e);
        return null;
      } finally {
        scrollBottom();
      }
    }

    // ── Send button: Enviar · A processar (desativado) · Cancelar (streaming) ──
    function setSendButton(state) {
      const btn = document.getElementById("send-btn");
      btn.classList.remove("cancel");
      if (state === "loading") {
        btn.disabled = false;
        btn.classList.add("cancel");
        btn.innerHTML = '<i class="fas fa-stop"></i>';
        btn.setAttribute("aria-label", "Cancelar");
        btn.onclick = cancelStream;
      } else if (state === "busy") {
        // pedidos que não são streaming (gráficos, relatório, commit…) não se
        // cancelam — o botão fica ocupado em vez de um "parar" que nada faz
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
        btn.setAttribute("aria-label", "A processar");
        btn.onclick = null;
      } else {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-paper-plane"></i>';
        btn.setAttribute("aria-label", "Enviar");
        btn.onclick = send;
      }
    }
    function cancelStream() {
      if (activeController) activeController.abort();
    }

    // ── Contextual follow-up chips (rendered above the input after a reply) ───
    async function updateFollowups() {
      const wsAtStart = workspaces.active;
      try {
        const res = await fetch(`${BASE_URL}/api/suggestions`, {
          method: "POST",
          headers,
          body: JSON.stringify({ messages: history.slice(-6), model: modelParam() }),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (wsAtStart !== workspaces.active || loading) return;
        renderFollowups(data.suggestions || []);
      } catch (e) {
        /* silent — follow-up chips are a nice-to-have, never block the chat */
      }
    }
    function renderFollowups(items) {
      const bar = document.getElementById("followups");
      bar.innerHTML = "";
      for (const s of (items || []).slice(0, 5)) {
        if (!s) continue;
        const b = document.createElement("button");
        b.type = "button";
        b.className = "chip";
        b.textContent = s;
        b.onclick = () => { document.getElementById("input").value = s; send(); };
        bar.appendChild(b);
      }
    }
    function clearFollowups() {
      document.getElementById("followups").innerHTML = "";
    }

    // ── Main send ─────────────────────────────────────────────────────────────
    async function send() {
      if (loading) return;
      const input = document.getElementById("input");
      const text = input.value.trim();
      if (!text) return;

      input.value = "";
      input.style.height = "auto";
      loading = true;
      setStatus(true);
      setSendButton("busy");
      clearFollowups();

      addMsg("user", text);
      history.push({ role: "user", content: text });

      const turn = { bubble: null };
      let qaReply = false;  // only fetch follow-up chips after a real Q&A turn
      try {
        const intent = detectIntent(text);

        if (intent.type === "chart") {
          const title = await renderChartIntent(intent.chart, intent.params || {});
          history.push({
            role: "assistant",
            content: title ? `[Gráfico apresentado: ${title}]` : "[Erro ao gerar gráfico]",
          });
          qaReply = true;   // mostra chips de seguimento depois de um gráfico

        } else if (intent.type === "create_form") {
          renderCreateForm(intent.title || "");
          history.push({ role: "assistant",
            content: "[Formulário de criação de issue aberto]" });

        } else if (intent.type === "delete_action") {
          showActionCard({
            tool: "delete_issue",
            args: { iid: Number(intent.iid) },
            summary: `Apagar DEFINITIVAMENTE a issue #${intent.iid} — irreversível`,
          });
          history.push({ role: "assistant",
            content: `[Confirmação de apagar a issue #${intent.iid}]` });

        } else if (intent.type === "close_issue") {
          showActionCard({
            tool: "close_issue",
            args: { iid: Number(intent.iid) },
            summary: `Fechar a issue #${intent.iid}`,
          });
          history.push({ role: "assistant",
            content: `[Confirmação de fechar a issue #${intent.iid}]` });

        } else if (intent.type === "edit_form") {
          await openEditForm(intent.iid);
          history.push({ role: "assistant",
            content: `[Formulário de edição da issue #${intent.iid} aberto]` });

        } else if (intent.type === "export_issues") {
          exportIssues(intent.state);
          const label = intent.state === "all" ? "todas"
                      : intent.state === "opened" ? "abertas" : "fechadas";
          const streamBub = createStreamBubble();
          const reply = `A exportar issues (${label}) para CSV...\n\nO ficheiro \`gitlab_issues.csv\` vai ser descarregado automaticamente.`;
          finalizeStreamBubble(streamBub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;   // chips de seguimento depois de exportar

        } else if (intent.type === "export_commits") {
          exportCommits();
          const streamBub = createStreamBubble();
          const reply = "A exportar commits para CSV...\n\nO ficheiro `gitlab_commits.csv` vai ser descarregado automaticamente (história completa do projeto).";
          finalizeStreamBubble(streamBub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;   // chips de seguimento depois de exportar

        } else if (intent.type === "sprint_report") {
          const ok = await renderSprintReport();
          history.push({ role: "assistant",
            content: ok ? "[Relatório do projeto gerado]" : "[Erro ao gerar o relatório]" });
          qaReply = ok;   // chips de seguimento depois do relatório

        } else if (intent.type === "code_commit") {
          await renderCommitFlow(text);
          history.push({ role: "assistant",
            content: "[Proposta de commit por IA apresentada para confirmação]" });

        } else if (intent.type === "blame_analysis") {
          await renderBlameAnalysis(intent, text);
          history.push({ role: "assistant",
            content: `[Investigação de código: ${intent.file}${intent.line ? ":" + intent.line : ""}]` });
          qaReply = true;

        } else if (intent.type === "blame_help") {
          const bub = createStreamBubble();
          const reply = "Para investigar código diz-me o ficheiro (e a linha, se quiseres a análise do erro):\n\n" +
            "- `quem alterou o src/app.js?` — histórico e autores do ficheiro\n" +
            "- `analisa o erro em server.py linha 120` — blame + diff + análise IA\n" +
            "- ou cola um *stack trace* — eu extraio o ficheiro e a linha.";
          finalizeStreamBubble(bub, reply);
          history.push({ role: "assistant", content: reply });
          qaReply = true;

        } else {
          const result = await streamChat(turn);
          if (result.action) {
            history.push({
              role: "assistant",
              content: `[Proposta de ação a aguardar confirmação: ${result.action.summary}]`,
            });
          } else {
            history.push({ role: "assistant", content: result.text });
            qaReply = true;   // plain answer → contextual chips make sense
          }
        }
      } catch (err) {
        // Só mexe na bolha DESTE turno: a meio do streaming fecha-a com uma nota;
        // se ainda só tinha os pontos, remove-a. Conteúdo já finalizado fica.
        const bub = turn.bubble;
        const rawEl = bub && bub.querySelector(".stream-raw");
        if (rawEl) {
          finalizeStreamBubble(bub, rawEl.textContent + "\n\n_(resposta interrompida)_");
        } else if (bub && !bub.querySelector(".bubble-content, .confirm-card")) {
          bub.closest(".msg")?.remove();
        }
        showError(err instanceof ServerError ? err.message
                  : "Não foi possível obter resposta. Verifica a ligação e tenta novamente.");
        console.error(err);
        history.pop();
      }

      // Token-budget-aware history trim: keep the most recent CAP messages.
      if (history.length > HISTORY_CAP) {
        history.splice(0, history.length - HISTORY_CAP);
      }
      activeController = null;
      loading = false;
      setSendButton("idle");
      setStatus(false);
      document.getElementById("input").focus();
      persistHistory();                // guarda a conversa deste workspace
      if (qaReply) updateFollowups();  // chips only after a Q&A (saves a Groq call/turn)
    }

    // ── Init + wiring (corre no load; DOM pronto por causa do defer) ──────────
    // Os handlers são ligados ANTES do primeiro render: mesmo que algo corra mal a
    // desenhar o estado guardado, os botões (incl. Repor backup) funcionam.
    const WELCOME_HTML = document.getElementById("welcome").outerHTML;

    document.getElementById("gear-btn").onclick = openSettings;
    document.getElementById("settings-close").onclick = closeSettings;
    document.getElementById("set-cancel").onclick = closeSettings;
    document.getElementById("set-save").onclick = saveSettings;
    document.getElementById("set-theme").onchange = (e) => {
      document.documentElement.dataset.theme = resolveTheme(e.target.value);   // pré-visualização
      refreshChartsTheme();
    };
    document.getElementById("set-model").onchange = refreshModelDesc;
    document.getElementById("settings-overlay").onclick = (e) => {
      if (e.target.id === "settings-overlay") closeSettings();
    };

    document.getElementById("ws-add").onclick = () => openWsEditor(null);
    document.getElementById("ws-close").onclick = closeWsEditor;
    document.getElementById("ws-cancel").onclick = closeWsEditor;
    document.getElementById("ws-save").onclick = wsSave;
    document.getElementById("ws-test").onclick = wsTest;
    document.getElementById("ws-delete").onclick = wsDelete;
    document.getElementById("ws-overlay").onclick = (e) => {
      if (e.target.id === "ws-overlay") closeWsEditor();
    };

    document.getElementById("ws-backup").onclick = backupExport;
    document.getElementById("ws-restore").onclick = () =>
      document.getElementById("ws-restore-file").click();
    document.getElementById("ws-restore-file").onchange = (e) => {
      if (e.target.files && e.target.files[0]) backupRestore(e.target.files[0]);
      e.target.value = "";   // permite re-importar o mesmo ficheiro
    };

    document.getElementById("stats-refresh").onclick = loadStats;
    document.getElementById("toggle-left").onclick = (e) => { e.stopPropagation(); toggleSidebar("sidebar-left"); };
    document.getElementById("toggle-right").onclick = (e) => { e.stopPropagation(); toggleSidebar("sidebar-right"); };
    document.getElementById("close-left").onclick = () => closeSidebar("sidebar-left");
    document.getElementById("close-right").onclick = () => closeSidebar("sidebar-right");
    // Em modo overlay: clicar fora da sidebar fecha-a.
    document.addEventListener("click", (e) => {
      for (const id of ["sidebar-left", "sidebar-right"]) {
        const sb = document.getElementById(id);
        if (sb.classList.contains("open") && !sb.contains(e.target)) sb.classList.remove("open");
      }
    });
    // Escape fecha o que estiver aberto (modais primeiro, depois as sidebars).
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!document.getElementById("ws-overlay").hidden) return closeWsEditor();
      if (!document.getElementById("settings-overlay").hidden) return closeSettings();
      closeSidebar("sidebar-left");
      closeSidebar("sidebar-right");
    });

    const inputEl = document.getElementById("input");
    inputEl.addEventListener("keydown", handleKey);
    inputEl.addEventListener("input", () => resize(inputEl));
    // Sugestões do ecrã de boas-vindas + botões "introduzir a chave" nos cartões.
    document.getElementById("messages").addEventListener("click", (e) => {
      const chip = e.target.closest(".welcome .chip");
      if (chip) return useSuggestion(chip);
      if (e.target.closest("[data-open-settings]")) openSettings();
    });

    applyTheme(); applyModelBadge(); applyGitlabHeaders(); setSendButton("idle");
    renderWorkspaceList(); updateHeaderForWs(); restoreHistory(); loadStats();

    fetch(`${BASE_URL}/api/config`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d) return;
        Object.assign(serverConfig, d);
        applyModelBadge();
      })
      .catch(() => { /* servidor antigo sem /api/config — mantém os defaults */ });
