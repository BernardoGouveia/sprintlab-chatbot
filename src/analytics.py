"""
Agregações e contexto: estatísticas de issues, visão geral do repositório
(descrição/linguagens/estrutura/README), o contexto GitLab injetado no LLM
em cada turno, e os exports CSV.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections import Counter
from datetime import date

from src.config import CACHE_TTL, README_MAX
from src.gitlab_api import (_get_commits, _get_contributors, _get_languages,
                            _get_readme_text, _get_repo_tree_top, _gl,
                            contributors_capped, get_all_issues,
                            get_issue_counts, get_milestones, get_project_info,
                            gitlab_error_message, merge_contributors,
                            run_parallel)

log = logging.getLogger("sprintlab")


def _strip_html_tags(s):
    """Remove <tag …>, </tag> and <!…> markup without regex. A '<' only starts a
    tag when followed by '/' or '!', or by a letter not glued to a preceding word
    (List<String> is code, not HTML), and closed by a '>' — so 'Python >= 3.10'
    or 'x < 5 ms' survive."""
    out, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        opens = nxt in ("/", "!") or (nxt.isalpha() and not (i > 0 and s[i - 1].isalnum()))
        if ch == "<" and opens:
            end = s.find(">", i + 1)
            if end != -1:
                i = end + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _clean_readme(text):
    """Strip badge/image/HTML/link-def/rule noise so the excerpt budget is spent
    on actual prose (a model shouldn't have to guess what the project is from a
    wall of badges). Pure → unit-testable."""
    if not text:
        return ""
    kept = []
    for raw in str(text).replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if not s:
            kept.append("")
            continue
        low = s.lower()
        if (s.startswith("![") or s.startswith("[![")
                or low.startswith("<!--") or low.startswith("-->")):
            continue  # badge / linked-badge / image-only line or HTML comment
        if s.startswith("[") and "]:" in s and ("http" in low or "mailto" in low):
            continue  # reference-style link definition
        if len(s) >= 3 and set(s) <= {"-", "=", "*", "_", " "}:
            continue  # horizontal rule
        s = _strip_html_tags(s).strip()
        if s:
            kept.append(s)
    # collapse runs of blank lines
    out, blank = [], False
    for ln in kept:
        if ln == "":
            if out and not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^#{1,6}(?:\s|$)")


def _readme_outline(text, limit=10):
    """Markdown heading titles (the README's table of contents) → the project's
    structure at a glance. Skips fenced (``` or ~~~) and indented code blocks, and
    '#' lines that aren't ATX headings (e.g. '#include', '#define')."""
    heads, fence = [], None   # fence = (char, length) of the open code fence
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        m = _FENCE_RE.match(s)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                    and s == m.group(1):
                fence = None
            continue
        if m:
            fence = (m.group(1)[0], len(m.group(1)))
            continue
        if raw.startswith("    ") or raw.startswith("\t"):
            continue   # indented code block
        if not _HEADING_RE.match(s):
            continue
        title = s.lstrip("#").strip().rstrip("#").strip()
        if title and title not in heads and 1 <= len(title) <= 60:
            heads.append(title)
            if len(heads) >= limit:
                break
    return heads


def _repo_overview_lines(info, languages, tree, readme, readme_max=README_MAX):
    """Compact 'what is this project' block. Pure (no network) → unit-testable."""
    info = info or {}
    lines = []
    desc = (info.get("description") or "").strip()
    if desc:
        lines.append(f"Descrição: {desc[:300]}")
    topics = info.get("topics") or info.get("tag_list") or []
    if topics:
        lines.append("Tópicos: " + ", ".join(str(t) for t in topics[:10]))
    if info.get("default_branch"):
        lines.append(f"Branch principal: {info['default_branch']}")
    if languages:
        top = sorted(languages.items(), key=lambda x: -x[1])[:6]
        lines.append("Linguagens: " + ", ".join(f"{k} {float(v):.0f}%" for k, v in top))
    if tree:
        dirs = [e["name"] for e in tree if e.get("type") == "tree"][:15]
        files = [e["name"] for e in tree if e.get("type") == "blob"][:15]
        if dirs:
            lines.append("Pastas (topo): " + ", ".join(dirs))
        if files:
            lines.append("Ficheiros (topo): " + ", ".join(files))
    outline = _readme_outline(readme)
    if outline:
        lines.append("README (secções): " + ", ".join(outline))
    rd = _clean_readme(readme)
    if rd:
        excerpt = rd[:readme_max]
        if len(rd) > readme_max:
            excerpt += " …"
        lines.append("README (excerto):\n" + excerpt)
    return lines


_NOT_FETCHED = object()


def _get_repo_overview(info, languages=_NOT_FETCHED):
    """Assemble the repo-overview lines, each piece independently tolerated so a
    failed fetch (e.g. empty repo) never breaks the whole context. `languages`
    can be passed in when already fetched (None = that fetch failed)."""
    ref = (info or {}).get("default_branch") or "main"
    if languages is _NOT_FETCHED:
        try:
            languages = _get_languages()
        except Exception as e:
            log.warning("languages fetch failed: %s", e)
            languages = {}
    try:
        tree = _get_repo_tree_top(ref)
    except Exception as e:
        log.warning("tree fetch failed: %s", e)
        tree = []
    try:
        readme = _get_readme_text(ref, tree)
    except Exception as e:
        log.warning("readme fetch failed: %s", e)
        readme = ""
    return _repo_overview_lines(info, languages or {}, tree, readme)


def _issue_stats(all_issues, today=None, counts=None, opened=None):
    """Shared aggregation over the issue list (used by the LLM context, the
    /gitlab/stats endpoint and the report — one implementation, no drift).

    `today` is injectable so the overdue calc is deterministic under test.
    When the list was truncated by the page limit (`.truncated`), pass exact
    `counts` ({all, opened, closed}) and/or the separately fetched `opened`
    list; without them the totals are flagged as lower bounds (`exact=False`)."""
    today = today or date.today()
    all_issues = all_issues if all_issues is not None else []
    truncated = bool(getattr(all_issues, "truncated", False))
    opened_list = [i for i in all_issues if i.get("state") == "opened"]
    closed_list = [i for i in all_issues if i.get("state") == "closed"]
    if opened is not None:
        opened_list = list(opened)
    overdue = []
    for i in opened_list:
        d = i.get("due_date")
        if not d:
            continue
        try:
            if date.fromisoformat(d) < today:
                overdue.append(i)
        except (ValueError, TypeError):
            pass  # malformed date in GitLab — skip
    no_assignee = sum(1 for i in opened_list
                      if not (i.get("assignee") or i.get("assignees")))
    total, n_open, n_closed = len(all_issues), len(opened_list), len(closed_list)
    if counts:
        total, n_open, n_closed = counts["all"], counts["opened"], counts["closed"]
    progress = round(n_closed / total * 100, 1) if total else 0
    open_complete = (not truncated) or (
        opened is not None and not getattr(opened, "truncated", False))
    return {"opened": opened_list, "closed": closed_list, "overdue": overdue,
            "no_assignee": no_assignee, "progress": progress,
            "total": total, "open_count": n_open, "closed_count": n_closed,
            "truncated": truncated, "exact": (not truncated) or bool(counts),
            "open_complete": open_complete}


def issue_extras(issues):
    """If `issues` hit the page limit, fetch exact counts and the full list of OPEN
    issues (usually far fewer). Returns (counts, opened) — (None, None) otherwise."""
    if not getattr(issues, "truncated", False):
        return None, None
    r = run_parallel({"counts": (get_issue_counts,), "opened": (get_all_issues, "opened")})
    counts = None if isinstance(r["counts"], Exception) else r["counts"]
    opened = None if isinstance(r["opened"], Exception) else r["opened"]
    return counts, opened


def _at_least(n, exact):
    return str(n) if exact else f"pelo menos {n}"


def milestones_by_due(milestones):
    """Milestones ordered by due date (soonest / most overdue first), undated last —
    the ones worth showing when the list has to be capped."""
    def key(m):
        due = (m or {}).get("due_date") or ""
        return (0, due) if due else (1, str((m or {}).get("title") or ""))
    return sorted((m for m in milestones or [] if isinstance(m, dict)), key=key)


CONTEXT_MILESTONES = 10   # every chat turn carries this list: keep it small


# ── Aggregate context for the LLM ─────────────────────────────────────────────


def get_gitlab_context() -> str:
    # Parallel, per-request threads with a total deadline; every section is
    # tolerated on its own — one failing endpoint never blanks the others.
    r = run_parallel({
        "issues": (get_all_issues, "all"),
        "milestones": (get_milestones,),
        "commits": (_get_commits, 90),
        "contributors": (_get_contributors,),
        "info": (get_project_info,),
        "languages": (_get_languages,),
    })
    failed = {k: v for k, v in r.items() if isinstance(v, Exception)}
    for k, v in failed.items():
        log.warning("gitlab context: %s failed: %s", k, v)
    core = ("issues", "milestones", "commits", "contributors", "info")
    if all(k in failed for k in core):
        return ("\n=== GITLAB: indisponível — "
                f"{gitlab_error_message(failed['info'])} ===\n")

    # ── Issues ──
    if "issues" in failed:
        issues_head = "Issues: indisponíveis (erro ao ler do GitLab)"
        issues_block = "ISSUES ABERTAS:\n  indisponível."
        assignee_block = "RANKING ASSIGNEES:\n  indisponível."
        overdue_block = "EM ATRASO:\n  indisponível."
    else:
        all_issues = r["issues"] or []
        counts, opened_extra = issue_extras(all_issues)
        s = _issue_stats(all_issues, counts=counts, opened=opened_extra)
        opened, overdue = s["opened"], s["overdue"]
        exact, open_exact = s["exact"], s["open_complete"]
        issues_head = (
            f"Total: {_at_least(s['total'], exact)} | "
            f"Abertas: {_at_least(s['open_count'], exact)} | "
            f"Fechadas: {_at_least(s['closed_count'], exact)} | "
            f"Progresso: {s['progress']}%"
            + ("" if exact else " (estimado — lista de issues limitada)")
            + f"\nEm atraso: {_at_least(len(overdue), open_exact)} | "
              f"Sem assignee: {_at_least(s['no_assignee'], open_exact)}")

        by_assignee: dict[str, int] = {}
        for i in opened:
            name = i["assignee"]["name"] if i.get("assignee") else "Sem assignee"
            by_assignee[name] = by_assignee.get(name, 0) + 1

        shown = opened[:10]   # cap the per-turn token cost
        issues_list = "\n".join(
            f"  #{i.get('iid')} {i.get('title', '')} | "
            f"{i['assignee']['name'] if i.get('assignee') else 'Nenhum'} | "
            f"due:{i.get('due_date') or 'N/A'} | "
            f"{','.join((i.get('labels') or [])[:2]) or 'sem label'}"
            for i in shown
        ) or "  Nenhuma issue aberta."
        if len(opened) > len(shown):
            issues_title = (f"ISSUES ABERTAS ({len(shown)} mais recentes de "
                            f"{_at_least(len(opened), open_exact)} — para qualquer "
                            "outra issue usa get_issue):")
        else:
            issues_title = "ISSUES ABERTAS:"
        issues_block = f"{issues_title}\n{issues_list}"

        assignee_block = "RANKING ASSIGNEES:\n" + ("\n".join(
            f"  {n}: {c}" for n, c in sorted(by_assignee.items(), key=lambda x: -x[1])
        ) or "  Nenhum.")

        overdue_block = "EM ATRASO:\n" + ("\n".join(
            f"  #{i.get('iid')} {i.get('title', '')} (due:{i.get('due_date')})"
            for i in overdue[:5]
        ) or "  Nenhuma.")

    # ── Milestones ──
    if "milestones" in failed:
        ms_list = "  indisponível."
    else:
        # Group + project milestones can be many: cap them (tokens/turn).
        ms_all = milestones_by_due(r["milestones"])
        ms_list = "\n".join(
            f"  {m.get('title')} (due:{m.get('due_date') or 'sem data'})"
            for m in ms_all[:CONTEXT_MILESTONES]
        ) or "  Nenhum."
        if len(ms_all) > CONTEXT_MILESTONES:
            ms_list += f"\n  … e mais {len(ms_all) - CONTEXT_MILESTONES} milestone(s) ativas"

    # ── Commits (the chatbot also analyses commits, not just issues) ──
    if "commits" in failed:
        commits_line = "Atividade recente (últimos 90 dias): indisponível"
    else:
        commits90 = r["commits"] or []
        c_by = Counter((c.get("author_name") or "?") for c in commits90)
        top_c = ", ".join(f"{n} ({c})" for n, c in c_by.most_common(5)) or "nenhum"
        if commits90:
            partial = bool(getattr(commits90, "truncated", False))
            commits_line = (f"Atividade recente (últimos 90 dias): "
                            f"{_at_least(len(commits90), not partial)} commits por "
                            f"{_at_least(len(c_by), not partial)} autores. Top: {top_c}")
        else:
            # Janela vazia ≠ repositório sem commits. Sem esta nota, o modelo
            # responde "não há commits do X" em vez de chamar commits_by_author.
            commits_line = ("Atividade recente (últimos 90 dias): 0 commits "
                            "(janela recente vazia — NÃO concluir que não há "
                            "commits; usar commits_by_author p/ histórico/autor)")

    # Per-author commit breakdown ("quem tem mais commits"). GitLab computes it
    # from the latest ~2000 non-merge commits, per e-mail — merged per person
    # here and presented ONLY as a ranking; the total comes from commit_count.
    if "contributors" in failed:
        authors_line = "Autores: indisponível"
    else:
        raw_contribs = r["contributors"] or []
        contribs = merge_contributors(raw_contribs)
        top_all = ", ".join(f"{c['name']} ({c['commits']})" for c in contribs[:5]) or "nenhum"
        scope = (" (contagens dos últimos ~2000 commits sem merges — não são totais "
                 "históricos)" if contributors_capped(raw_contribs) else "")
        n_authors = _at_least(len(contribs), not getattr(raw_contribs, "truncated", False))
        authors_line = f"Autores: {n_authors}. Mais ativos: {top_all}{scope}"

    # Project metadata + repo overview so the model knows WHAT the project is
    # (descrição, linguagens, estrutura, README) — não só a contagem de issues.
    info = {} if "info" in failed else (r["info"] or {})
    pname = info.get("name_with_namespace") or "?"
    # Canonical repo commit total — the SAME number the stats panel shows.
    commit_count = (info.get("statistics") or {}).get("commit_count")
    commits_total_line = ("Total de commits no repositório: "
                          f"{commit_count if commit_count is not None else 'indisponível'}")
    try:
        languages = None if "languages" in failed else r["languages"]
        overview = "\n".join(_get_repo_overview(info, languages=languages))
    except Exception as e:
        log.warning("repo overview failed: %s", e)
        overview = ""
    repo_section = f"\n\nREPOSITÓRIO:\n{overview}" if overview else ""

    return f"""
=== DADOS GITLAB (tempo real, cache {CACHE_TTL}s) ===
Projeto: {pname}  |  ID: {_gl()['project']}
{issues_head}{repo_section}

{issues_block}

{assignee_block}

{overdue_block}

MILESTONES:
{ms_list}

COMMITS:
  {commits_total_line}
  {authors_line}
  {commits_line}"""


# ── CSV ───────────────────────────────────────────────────────────────────────

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Neutralise spreadsheet formulas in untrusted text (issue titles, author
    names…): a cell starting with = + - @ is prefixed with ' so Excel shows it
    as text instead of executing it."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_START) else s


def issues_to_csv(issues) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        ["ID", "Título", "Estado", "Assignee", "Labels",
         "Due Date", "Criada em", "URL"]
    )
    for i in issues:
        w.writerow([
            f"#{i.get('iid')}",
            _csv_safe(i.get("title")),
            _csv_safe(i.get("state")),
            _csv_safe(i["assignee"]["name"] if i.get("assignee") else ""),
            _csv_safe(",".join(i.get("labels") or [])),
            i.get("due_date") or "",
            (i.get("created_at") or "")[:10],
            _csv_safe(i.get("web_url")),
        ])
    # utf-8-sig writes a BOM so Excel-pt opens accents correctly.
    return out.getvalue().encode("utf-8-sig")


def commits_to_csv(commits) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SHA", "Autor", "Email", "Data", "Título"])
    for c in commits:
        w.writerow([
            (c.get("short_id") or c.get("id") or "")[:12],
            _csv_safe(c.get("author_name")),
            _csv_safe(c.get("author_email")),
            (c.get("created_at") or "")[:10],
            _csv_safe(c.get("title")),
        ])
    return out.getvalue().encode("utf-8-sig")
