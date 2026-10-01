"""
Ferramentas de LEITURA para o function calling do modelo.

Ao contrário das ferramentas de escrita (actions.py, que propõem um cartão de
confirmação), estas executam-se de imediato e o resultado é devolvido ao modelo
para uma 2.ª passagem — porque são só leitura, não alteram nada.

Resolvem a classe de perguntas que antes dava "não tenho acesso":
  - "qual o commit que alterou o spacewire?"   → search_commits
  - "último commit do Daniel Silveira?"         → commits_by_author
  - "onde está X no código?"                    → search_code
  - "quem está atribuído à issue 12?"           → get_issue
"""

from __future__ import annotations

import logging
import urllib.error

from src.gitlab_api import (_commits_by_author, _search_blobs, _search_commits,
                            get_all_issues, get_issue)

log = logging.getLogger("sprintlab")

# A Search API devolve no máximo esta quantidade por pedido (per_page).
_SEARCH_PAGE = 100

# Definições no formato OpenAI/Groq (juntam-se às ISSUE_TOOLS no pedido de chat).
READ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_commits",
            "description": (
                "Procura commits cujo TÍTULO ou MENSAGEM contêm um termo (nome de "
                "módulo, funcionalidade, ficheiro, bug). Usa para «qual commit "
                "alterou/mexeu no X», «commits sobre X», «commits que mencionam X»."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Termo a procurar (ex.: spacewire)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commits_by_author",
            "description": (
                "Commits de um autor pelo nome: total e a lista dos mais recentes. "
                "Usa para «commits do X», «último commit do X», «quantos commits fez "
                "o X». Para «MAIS commits do X», chama outra vez com offset (ex.: "
                "offset=15, depois offset=30) para a página seguinte."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "author": {"type": "string", "description": "Nome do autor (ex.: Daniel Silveira)"},
                    "offset": {"type": "integer",
                               "description": "Saltar os N commits mais recentes (paginação; 0 por omissão)"},
                },
                "required": ["author"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Procura FICHEIROS cujo conteúdo contém um termo (ex.: «onde está "
                "definido X», «que ficheiros usam X»). Usa quando search_commits "
                "não chega para localizar algo no código."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Termo a procurar no código"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_issue",
            "description": (
                "Detalhes de UMA issue pelo número (iid): título, estado, assignees, "
                "labels, milestone, data limite. Usa para qualquer issue que não "
                "esteja na lista do contexto (só lá estão as abertas mais recentes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "iid": {"type": "integer", "description": "Número da issue (ex.: 12)"},
                },
                "required": ["iid"],
            },
        },
    },
]

READ_TOOL_NAMES = {t["function"]["name"] for t in READ_TOOLS}


def _fmt_commit(c):
    """Resumo compacto de um commit para o modelo (e para o utilizador)."""
    sha = c.get("id") or ""
    title = c.get("title") or (c.get("message") or "").split("\n", 1)[0]
    return {
        "sha": (c.get("short_id") or sha)[:10],
        "autor": c.get("author_name") or "?",
        "data": (c.get("authored_date") or c.get("created_at") or "")[:10],
        "titulo": (title or "").strip()[:120],
    }


def _fmt_issue(i):
    assignees = [a.get("name") for a in (i.get("assignees") or []) if isinstance(a, dict)]
    if not assignees and isinstance(i.get("assignee"), dict):
        assignees = [i["assignee"].get("name")]
    return {
        "iid": i.get("iid"),
        "titulo": (i.get("title") or "")[:200],
        "estado": "aberta" if i.get("state") == "opened" else "fechada",
        "assignees": [a for a in assignees if a] or ["ninguém"],
        "labels": list(i.get("labels") or []),
        "milestone": (i.get("milestone") or {}).get("title") or None,
        "data_limite": i.get("due_date") or None,
        "criada_em": (i.get("created_at") or "")[:10],
        "fechada_em": (i.get("closed_at") or "")[:10] or None,
    }


def _get_issue_tool(args):
    raw = str(args.get("iid") or "").strip().lstrip("#").strip()
    if not raw.isdigit():
        return {"erro": "número de issue em falta ou inválido"}
    iid = int(raw)
    # Primeiro a lista em cache (sem pedido extra), depois o GitLab diretamente.
    try:
        for i in get_all_issues("all") or []:
            if i.get("iid") == iid:
                return _fmt_issue(i)
    except Exception as e:
        log.warning("issue list for get_issue failed: %s", e)
    try:
        return _fmt_issue(get_issue(iid))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"iid": iid, "erro": "essa issue não existe neste projeto"}
        raise


def execute_read_tool(name, args):
    """Executa uma ferramenta de leitura. Nunca lança — devolve sempre um dict
    (com 'erro' em caso de falha) para a 2.ª passagem do modelo."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "search_commits":
            q = str(args.get("query") or "").strip()
            if not q:
                return {"erro": "termo de pesquisa em falta"}
            rows = _search_commits(q)
            if rows is None:   # falha da API ≠ zero resultados
                return {"query": q, "erro": "a pesquisa de commits falhou nesta "
                        "instância do GitLab — não consigo confirmar se existem."}
            out = {"query": q, "mostrados": len(rows[:10]), "total_visto": len(rows),
                   "commits": [_fmt_commit(c) for c in rows[:10]]}
            if len(rows) >= _SEARCH_PAGE:
                out["nota"] = (f"a pesquisa devolve no máximo {_SEARCH_PAGE} resultados "
                               "— pode haver mais commits com este termo.")
            return out

        if name == "commits_by_author":
            a = str(args.get("author") or "").strip()
            if not a:
                return {"erro": "nome do autor em falta"}
            try:
                offset = max(0, int(args.get("offset") or 0))
            except (TypeError, ValueError):
                offset = 0
            r = _commits_by_author(a, offset=offset)
            if r.get("candidatos"):
                return {"autor": a, "erro": "nome ambíguo — corresponde a vários autores",
                        "candidatos": r["candidatos"]}
            if r.get("erro"):   # falha do GitLab ≠ "0 commits"
                return {"autor": a, "erro": r["erro"], "total": r.get("total")}
            commits = r.get("commits") or []
            total = r.get("total")
            out = {"autor": ", ".join(r.get("autores") or []) or a,
                   "total": total if total is not None else "desconhecido",
                   "offset": offset, "mostrados": len(commits),
                   "commits": [_fmt_commit(c) for c in commits]}
            notas = []
            if r.get("parcial"):
                notas.append("a pesquisa não percorreu o histórico todo — pode haver "
                             "mais commits deste autor além destes.")
            elif not commits and offset > 0:
                notas.append("não há mais commits deste autor a partir desse ponto.")
            if r.get("total_parcial"):
                notas.append("o total pode estar subestimado (o GitLab só conta os "
                             "commits mais recentes por autor).")
            if notas:
                out["nota"] = " ".join(notas)
            return out

        if name == "search_code":
            q = str(args.get("query") or "").strip()
            if not q:
                return {"erro": "termo de pesquisa em falta"}
            rows = _search_blobs(q)
            if rows is None:   # falha da API (scope=blobs pode dar 403) ≠ vazio
                return {"query": q, "erro": "a pesquisa de código não está "
                        "disponível nesta instância do GitLab."}
            files, seen = [], set()
            for b in rows:
                p = b.get("path") or b.get("filename") or ""
                if p and p not in seen:
                    seen.add(p)
                    files.append(p)
            out = {"query": q, "ficheiros": files[:15], "total_ficheiros": len(files)}
            if len(rows) >= _SEARCH_PAGE:
                out["nota"] = ("a pesquisa devolve no máximo "
                               f"{_SEARCH_PAGE} excertos — pode haver mais ficheiros.")
            return out

        if name == "get_issue":
            return _get_issue_tool(args)

        return {"erro": f"ferramenta desconhecida: {name}"}
    except Exception as e:
        log.warning("read tool %s failed: %s", name, e)
        return {"erro": "falha ao consultar o GitLab"}
