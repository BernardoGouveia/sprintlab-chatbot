"""
Commit por IA (Feature GMV #1): o utilizador pede código em linguagem natural,
o LLM gera um PLANO (ficheiros + mensagem de commit), o utilizador confirma num
cartão com pré-visualização, e só então o servidor escreve no GitLab.

Desenho de segurança:
  - Geração e execução são passos separados — nada é escrito sem confirmação.
    A execução (POST /api/confirm-commit) exige a chave de acesso quando usa o
    token do servidor (ver server.py) — o plano vem do browser, não é confiado.
  - As escritas vão SEMPRE para uma branch nova `ai/<slug>` + Merge Request;
    a branch principal nunca é tocada. A revisão humana acontece no MR.
  - O commit leva `[skip ci]`: nenhum pipeline corre código gerado por IA antes
    dessa revisão (o revisor corre o pipeline no MR quando quiser).
  - Os caminhos/conteúdos do plano são validados no servidor (re-validados na
    execução); ficheiros de CI/automação (.gitlab-ci.yml, .gitlab/, .git/) são
    recusados.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse

from src.gitlab_api import (_get_languages, _get_repo_tree_top, _gitlab_request,
                            _proj, get_project_info, gitlab_error_detail,
                            gitlab_error_message, gitlab_request)
from src.llm import _groq_complete, _parse_json_obj, _pick_model

log = logging.getLogger("sprintlab")

MAX_FILES = 5
MAX_CONTENT = 200_000   # chars por ficheiro — folga grande, evita payloads absurdos
SKIP_CI = "[skip ci]"


# ── Helpers puros (unit-testáveis) ────────────────────────────────────────────


def _slugify(text, max_len=32):
    """Nome seguro para branch: minúsculas, só [a-z0-9-], sem '-' repetidos."""
    out, prev = [], "-"
    for ch in str(text or "").lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
            prev = ch
        elif prev != "-":
            out.append("-")
            prev = "-"
    s = "".join(out).strip("-")[:max_len].strip("-")
    return s or "alteracao"


def _forbidden_path(path):
    """Motivo pelo qual um caminho não pode ser escrito, ou None."""
    segs = path.split("/")
    low = [s.lower() for s in segs]
    if ".git" in low:
        return "Não é permitido escrever em .git/."
    if low[0] == ".gitlab" or low[-1] in (".gitlab-ci.yml", ".gitlab-ci.yaml"):
        return ("Não é permitido alterar a configuração de CI/CD do GitLab "
                f"(«{path[:60]}»).")
    return None


def _validate_files(raw):
    """Sanitiza a lista de ficheiros de um plano. Devolve (files, erro)."""
    if not isinstance(raw, list) or not raw:
        return None, "O plano não tem ficheiros."
    if len(raw) > MAX_FILES:
        return None, f"Máximo {MAX_FILES} ficheiros por commit."
    files = []
    for f in raw:
        if not isinstance(f, dict):
            return None, "Formato de ficheiro inválido."
        path = str(f.get("path") or "").strip().replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        content = f.get("content")
        if not path or len(path) > 200 or path.endswith("/"):
            return None, f"Caminho inválido: «{path[:60]}»."
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
            return None, "Caminho inválido (caracteres de controlo)."
        segs = path.split("/")
        if path.startswith("/") or ".." in segs or "" in segs or "." in segs:
            return None, f"Caminho não permitido: «{path[:60]}»."
        why = _forbidden_path(path)
        if why:
            return None, why
        if not isinstance(content, str) or not content.strip():
            return None, f"Conteúdo vazio em «{path}»."
        if len(content) > MAX_CONTENT:
            return None, f"Conteúdo demasiado grande em «{path}»."
        files.append({"path": path, "content": content})
    paths = [f["path"].lower() for f in files]
    if len(set(paths)) != len(paths):
        return None, "Ficheiros duplicados no plano."
    return files, None


def plan_from_llm_text(text):
    """Resposta do modelo → plano validado. Devolve (plan, erro). Puro."""
    obj = _parse_json_obj(text)
    if not obj:
        return None, "O modelo não devolveu um plano JSON válido — tenta de novo."
    files, err = _validate_files(obj.get("files"))
    if err:
        return None, err
    msg = (str(obj.get("commit_message") or "").strip()[:120]
           or "Alterações via SprintLab Chatbox")
    slug = _slugify(obj.get("branch_slug") or msg)
    summary = str(obj.get("summary") or "").strip()[:400]
    return {"branch": f"ai/{slug}", "commit_message": msg,
            "summary": summary, "files": files}, None


# ── Passo 1: gerar o plano (1 chamada Groq; NADA é escrito aqui) ──────────────


def generate_commit_plan(request_text, model_req=None):
    """Pede ao LLM um plano de commit para o pedido do utilizador, com um
    contexto mínimo do repositório (linguagens/estrutura) para o código sair
    na linguagem certa e no sítio certo. Devolve (plan, erro)."""
    try:
        info = get_project_info()
    except Exception:
        info = {}
    ref = (info or {}).get("default_branch") or "main"
    pname = (info or {}).get("name_with_namespace") or "?"
    try:
        langs = ", ".join(list((_get_languages() or {}).keys())[:4]) or "?"
    except Exception:
        langs = "?"
    try:
        tree = _get_repo_tree_top(ref) or []
        struct = ", ".join(str(e.get("name")) for e in tree[:12]) or "?"
    except Exception:
        struct = "?"

    gmodel, greff = _pick_model(model_req)
    payload = {
        "model": gmodel,
        "messages": [
            {"role": "system", "content":
                "És um engenheiro de software sénior. Geras código para ser "
                "commitado num repositório GitLab. Responde APENAS com um objeto "
                "JSON válido — sem cercas de código, sem texto antes ou depois:\n"
                '{"branch_slug": "kebab-curto", '
                '"commit_message": "imperativo, máx 72 chars", '
                '"summary": "1-2 frases em português de Portugal sobre o que criaste", '
                '"files": [{"path": "caminho/relativo.ext", "content": "conteúdo COMPLETO do ficheiro"}]}\n'
                "Regras: 1 a 3 ficheiros; código completo, funcional e comentado "
                "(comentários em português de Portugal); segue a linguagem e a "
                "estrutura do projeto salvo pedido em contrário; caminhos "
                "relativos, sem '..'; nunca alteres .gitlab-ci.yml nem .gitlab/."},
            {"role": "user", "content":
                f"PROJETO: {pname} | branch principal: {ref}\n"
                f"LINGUAGENS: {langs}\n"
                f"ESTRUTURA (topo): {struct}\n\n"
                f"PEDIDO DO UTILIZADOR:\n{request_text}"},
        ],
        "temperature": 0.2,
        # 4096 cabe no limite de tokens/minuto dos modelos mais pequenos do Groq
        # (prompt + max_tokens contam); se não chegar, finish_reason="length" avisa.
        "max_tokens": 4096,
    }
    if greff:
        payload["reasoning_effort"] = greff
    try:
        resp = _groq_complete(payload)
        choice = (resp.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
    except Exception as e:
        log.warning("commit plan LLM failed: %s", e)
        return None, "Não consegui contactar o modelo — tenta de novo."
    if choice.get("finish_reason") == "length":
        return None, ("O pedido gera código demais para um só commit — pede menos "
                      "ficheiros ou divide-o em partes.")
    return plan_from_llm_text(text)


# ── Passo 2: executar o plano confirmado (branch ai/* + commit + MR) ──────────


def _file_exists(path, ref):
    """O commit API exige 'create' vs 'update' conforme o ficheiro exista.
    404 → False; qualquer outro erro propaga (não se adivinha)."""
    try:
        _gitlab_request(
            "GET",
            f"/projects/{_proj()}/repository/files/"
            f"{urllib.parse.quote(path, safe='')}",
            params={"ref": ref},
        )
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def _delete_branch(proj, branch):
    """Limpeza best-effort de uma branch ai/* que ficou sem commit."""
    try:
        gitlab_request("DELETE", f"/projects/{proj}/repository/branches/"
                                 f"{urllib.parse.quote(branch, safe='')}",
                       invalidate=())
        return True
    except Exception as e:
        log.warning("could not delete orphan branch %s: %s", branch, e)
        return False


def _fail_after_branch(proj, branch, msg):
    removed = _delete_branch(proj, branch)
    note = ("" if removed else
            f" A branch {branch} ficou criada sem alterações — podes apagá-la no GitLab.")
    return {"ok": False, "error": msg + note}


def execute_commit_plan(plan):
    """Cria branch ai/* + commit + Merge Request. Corre APENAS depois da
    confirmação explícita do utilizador no cartão de pré-visualização."""
    if not isinstance(plan, dict):
        return {"ok": False, "error": "Plano inválido."}
    files, err = _validate_files(plan.get("files"))
    if err:
        return {"ok": False, "error": err}
    msg = (str(plan.get("commit_message") or "").strip()[:120]
           or "Alterações via SprintLab Chatbox")
    raw_branch = str(plan.get("branch") or "").removeprefix("ai/")
    base_branch = f"ai/{_slugify(raw_branch or msg)}"
    summary = str(plan.get("summary") or "").strip()[:400]

    try:
        info = get_project_info()
    except Exception as e:
        return {"ok": False, "error": f"Não consegui ler o projeto: {gitlab_error_message(e)}"}
    ref = (info or {}).get("default_branch") or "main"
    proj = _proj()

    # 1) Branch nova a partir da principal (sufixo -2..-5 se o nome já existir).
    branch = None
    for i in range(1, 6):
        cand = base_branch if i == 1 else f"{base_branch}-{i}"
        try:
            gitlab_request("POST", f"/projects/{proj}/repository/branches",
                           body={"branch": cand, "ref": ref}, invalidate=())
            branch = cand
            break
        except urllib.error.HTTPError as e:
            detail = gitlab_error_detail(e)
            if e.code == 400 and "already exists" in detail.lower():
                continue   # nome ocupado → tenta o sufixo seguinte
            return {"ok": False, "error": "Falha ao criar a branch: "
                                          + gitlab_error_message(e, detail)}
        except Exception as e:
            return {"ok": False, "error": "Falha ao criar a branch: "
                                          + gitlab_error_message(e)}
    if not branch:
        return {"ok": False, "error": "Já existem demasiadas branches ai/* com este nome."}

    # 2) Um único commit com todas as ações (create/update por ficheiro), decidido
    #    contra a branch NOVA — o snapshot a que o commit se aplica.
    actions = []
    for f in files:
        try:
            exists = _file_exists(f["path"], branch)
        except Exception as e:
            return _fail_after_branch(
                proj, branch,
                f"Não consegui verificar «{f['path']}»: {gitlab_error_message(e)}")
        actions.append({"action": "update" if exists else "create",
                        "file_path": f["path"], "content": f["content"]})
    try:
        c = gitlab_request("POST", f"/projects/{proj}/repository/commits",
                           body={"branch": branch,
                                 "commit_message": f"{msg}\n\n{SKIP_CI}",
                                 "actions": actions},
                           invalidate=())
    except Exception as e:
        return _fail_after_branch(proj, branch,
                                  "Falha ao criar o commit: " + gitlab_error_message(e))
    if not isinstance(c, dict):
        c = {}

    # 3) Merge Request — o coração do desenho: revisão humana antes do merge.
    mr_url, mr_iid, mr_error = "", None, ""
    try:
        mr = gitlab_request(
            "POST", f"/projects/{proj}/merge_requests",
            body={"source_branch": branch, "target_branch": ref, "title": msg,
                  "description": ("Gerado por IA via **SprintLab Chatbox**.\n\n"
                                  f"{summary}\n\n"
                                  "⚠️ Código gerado por IA — rever antes do merge. "
                                  "O commit tem `[skip ci]`: corre o pipeline "
                                  "manualmente depois de rever."),
                  "remove_source_branch": True},
            invalidate=("mrs:",))
        mr = mr if isinstance(mr, dict) else {}
        mr_url, mr_iid = mr.get("web_url") or "", mr.get("iid")
    except Exception as e:
        log.warning("MR creation failed: %s", e)  # o commit já existe — segue
        mr_error = gitlab_error_message(e)

    return {"ok": True, "branch": branch, "target": ref,
            "commit_sha": str(c.get("short_id") or c.get("id") or "")[:8],
            "commit_url": c.get("web_url") or "",
            "mr_url": mr_url, "mr_iid": mr_iid, "mr_error": mr_error,
            "files": [f["path"] for f in files]}
