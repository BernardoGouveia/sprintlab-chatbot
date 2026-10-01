"""
Investigação de código (git blame + diff): helpers puros para localizar o
commit que alterou uma linha, e a chamada única ao LLM que explica o
possível bug por cima desses factos determinísticos.
"""

from __future__ import annotations

import logging

from src.llm import _groq_complete, _pick_model, _strip_think

log = logging.getLogger("sprintlab")


# ── Code investigation (git blame + commit diff → "who broke this line?") ─────
#
# The facts (which commit last touched a line, who, when, and its diff) come
# straight from GitLab — deterministic, zero LLM involvement. The LLM is called
# ONCE at the end, only to hypothesise about the bug on top of those facts.
# The helpers below are pure (no network) so they are unit-testable.


def _path_candidates(path, cap=8):
    """A pasted path is often absolute (IDE, stack trace). Generate repo-relative
    candidates by stripping leading segments: 'C:/Users/x/src/app.js' →
    ['C:/Users/x/src/app.js', 'Users/x/src/app.js', ..., 'app.js'].

    Capped at `cap` lookups: the full path, then the suffixes starting at a
    conventional repo root (each 'src' segment and up to 3 segments before it —
    single modules like app/src, multi-module layouts like modules/core/src or
    feature/foryou/impl/src, Maven's src/main/java/...), then the SHORTEST suffixes
    (in a deep absolute path the repo-relative part is near the end). Longest
    first. Only those suffixes are built (linear cost), and the input is
    bounded, so a huge pasted "path" can't burn CPU/memory."""
    p = str(path or "").strip()[:MAX_PATH_CHARS].replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    parts = [seg for seg in p.split("/") if seg and seg != "."]
    n = len(parts)
    if not n:
        return []
    if n <= cap:
        starts = range(n)
    else:
        # A home prefix (C:/Users/<name>, /Users/<name>, /home/<name>, WSL's
        # /mnt/c/Users/<name>) is never inside a repo: no src-root lookups in it
        # (a ~/work/src checkout folder must not crowd out the short suffixes).
        k = 0
        if len(parts[0]) == 2 and parts[0].endswith(":"):
            k = 1
        elif parts[0].lower() == "mnt" and len(parts[1]) == 1:
            k = 2
        home = k + 1 if parts[k].lower() in ("users", "home") else 0
        picked = [0]
        for i in range(n - 2, 0, -1):             # conventional repo roots
            if parts[i].lower() == "src":
                for j in (i, i - 1, i - 2, i - 3):   # src, module, parent module(s)
                    if j > home and j not in picked and len(picked) < cap:
                        picked.append(j)
        j = n - 1                                 # then the shortest suffixes
        while len(picked) < cap and j > 0:
            if j not in picked:
                picked.append(j)
            j -= 1
        starts = sorted(picked)                   # longest first
    # suffixes of distinct lengths are always distinct strings
    return ["/".join(parts[i:]) for i in starts]


MAX_PATH_CHARS = 1024


def _blame_owner(blame, range_start, target_line):
    """GitLab blame returns [{commit, lines}] covering consecutive lines from
    `range_start`. Return (commit, line_text) for the entry owning
    `target_line`, or (None, '') if the line falls outside the response."""
    n = range_start
    for entry in blame or []:
        commit = entry.get("commit") or {}
        for txt in entry.get("lines") or []:
            if n == target_line:
                return commit, txt
            n += 1
    return None, ""


def _blame_snippet(blame, range_start, target_line, width=160):
    """Numbered code excerpt for the LLM, '>>' marking the target line and each
    line tagged with the short SHA that last touched it."""
    out = []
    n = range_start
    for entry in blame or []:
        sha = ((entry.get("commit") or {}).get("id") or "")[:7]
        for txt in entry.get("lines") or []:
            mark = ">>" if n == target_line else "  "
            out.append(f"{mark}{n:>5} [{sha}] {txt[:width]}")
            n += 1
    return "\n".join(out)


def _diff_excerpt(diffs, file_path, max_chars=3500):
    """Pick the diff for `file_path` out of a commit's diff list and truncate it
    so one giant commit can't blow the token budget of the analysis call.

    Never substitutes an unrelated file's diff: if the path isn't in the list,
    only a single-file commit (e.g. the file was renamed since) is used, and it is
    labelled with its own path. Otherwise '' → the prompt says 'indisponível'."""
    diffs = [d for d in diffs or [] if isinstance(d, dict)]
    chosen, label = None, ""
    for d in diffs:
        if d.get("new_path") == file_path or d.get("old_path") == file_path:
            chosen = d
            break
    if chosen is None and len(diffs) == 1:
        chosen = diffs[0]
        label = f"(ficheiro neste commit: {chosen.get('new_path') or chosen.get('old_path') or '?'})\n"
    if not chosen:
        return ""
    txt = chosen.get("diff") or ""
    if len(txt) > max_chars:
        txt = txt[:max_chars] + "\n… (diff truncado)"
    return label + txt

def _analyze_code_llm(path, ref, line, line_text, snippet, facts, diff_txt,
                      question, model_req):
    """The single LLM call of the code-investigation feature: explain the
    probable bug ON TOP of the deterministic blame/diff facts. Returns '' on
    any failure — the facts must always survive an LLM outage."""
    gmodel, greff = _pick_model(model_req)
    user = f"""FICHEIRO: {path} (ref: {ref})
LINHA ALVO: {line}
CONTEÚDO DA LINHA ALVO: {line_text.strip()[:200]}

EXCERTO (marcador >> = linha alvo; [sha] = commit que a alterou, via git blame):
{snippet}

ÚLTIMA ALTERAÇÃO DA LINHA ALVO (facto, git blame):
- commit {facts['short_sha']} — {facts['author']} — {facts['date']}
- mensagem: {facts['message']}

DIFF DESSE COMMIT (excerto):
{diff_txt or '(diff indisponível)'}

PERGUNTA DO UTILIZADOR: {question.strip()[:400] or '(nenhuma — análise geral da linha)'}"""
    payload = {
        "model": gmodel,
        "messages": [
            {"role": "system", "content":
                "És um engenheiro de software sénior a fazer análise forense de "
                "código (git blame + diffs). Responde em português de Portugal, "
                "Markdown simples, máximo ~200 palavras, com esta estrutura: "
                "**O que mudou** (resume o que o commit fez nesta zona); "
                "**Possível bug** (analisa se a alteração pode ter introduzido um "
                "erro, em especial na linha alvo — cita linhas concretas); "
                "**Como confirmar** (1-2 passos objetivos). Baseia-te APENAS no "
                "código e diff fornecidos. Se não houver indício de bug, di-lo "
                "claramente — nunca inventes."},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 700,
    }
    if greff:
        payload["reasoning_effort"] = greff
    try:
        resp = _groq_complete(payload)
        return _strip_think(
            (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
        )
    except Exception as e:
        log.warning("code analysis LLM failed: %s", e)
        return ""
