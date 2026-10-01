"""
Relatório do projeto ("Relatórios automáticos") — 100% determinístico:
números calculados em código a partir de dados já obtidos do GitLab; o LLM
nunca conta nada. Funções puras → unit-testáveis.

O relatório ADAPTA-SE ao tipo de projeto:
  - "ativo"      → há commits nos últimos 90 dias: lidera com a atividade recente.
  - "fase_final" → 0 commits em 90 dias mas último commit há menos de 2 anos:
                   praticamente concluído / em fase final (não "encerrado").
  - "concluido"  → sem commits há ≥ 2 anos (critério do orientador): terminado.
  - "vazio"      → PROVAS de que não há commits nem issues (nunca por falta de
                   dados: uma secção que falhou não conta como "zero").
A secção "Atividade (90 dias)" é omitida quando não há commits recentes.
Projetos só de código (0 issues) mostram "Sem issues registadas" em vez de uma
percentagem de progresso enganadora (0% não significa "fase inicial").
Secções que não foi possível obter do GitLab são assinaladas como tal.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from src.analytics import _issue_stats, milestones_by_due
from src.gitlab_api import contributors_capped, merge_contributors, mr_summary

REPORT_MILESTONES = 15   # listed in the card / markdown (counts use all of them)


def _due_status(due, today):
    """Classify a YYYY-MM-DD due date relative to `today`:
    'overdue' (past), 'soon' (≤7 days), 'ok' (further out), or None (no/bad date)."""
    if not due:
        return None
    try:
        d = date.fromisoformat(str(due))
    except (ValueError, TypeError):
        return None
    delta = (d - today).days
    if delta < 0:
        return "overdue"
    if delta <= 7:
        return "soon"
    return "ok"


def _about_lines(project_info, languages, tree):
    """Compact 'what is this project' block para o relatório (descrição,
    linguagens, estrutura de topo). Pure → testável."""
    pi = project_info or {}
    lines = []
    desc = (pi.get("description") or "").strip()
    if desc:
        lines.append(desc[:220])
    if languages:
        top = sorted(languages.items(), key=lambda x: -x[1])[:4]
        lines.append("Linguagens: " + ", ".join(f"{k} {float(v):.0f}%" for k, v in top))
    if tree:
        dirs = [e["name"] for e in tree if e.get("type") == "tree"][:8]
        if dirs:
            lines.append("Estrutura: " + ", ".join(dirs))
    topics = pi.get("topics") or pi.get("tag_list") or []
    if topics:
        lines.append("Tópicos: " + ", ".join(str(t) for t in topics[:8]))
    return lines


# Sem commits há ≥ 2 anos = concluído; entre 90 dias e 2 anos = fase final.
# (Critério do orientador: "concluído" exige ~2 anos parado; 90 dias é só "fase
# final / praticamente concluído", não um projeto encerrado.)
_DONE_DAYS = 730  # 2 anos


def _humanize_age(days):
    """Idade legível para o texto do destaque: '45 dias' | '8 meses' | '3 anos'."""
    if days is None:
        return ""
    if days < 60:
        return f"{days} dias"
    if days < _DONE_DAYS:
        return f"{round(days / 30)} meses"
    return f"{round(days / 365)} anos"


def _project_status(summary, activity, repo_commits, last_commit_age_days=None,
                    contributors_known_empty=False, issues_known=True):
    """'ativo' | 'fase_final' | 'concluido' | 'vazio'.
    - ativo      → commits nos últimos 90 dias (ou último commit há < 90 dias,
                   mesmo que a lista dos 90 dias não tenha sido obtida).
    - concluido  → sem commits há ≥ 2 anos (critério do orientador).
    - fase_final → parado entre 90 dias e 2 anos (ou idade desconhecida): NÃO se
                   afirma "concluído" sem 2 anos de prova — fica "fase final".
    - vazio      → só com provas: 0 issues (lidas com sucesso), nenhum commit
                   recente nem último commit, e commit_count == 0 ou a lista de
                   contribuidores lida e vazia."""
    has_issues = summary["total"] > 0
    has_recent = activity["commits"] > 0
    no_commits = (repo_commits == 0
                  or (repo_commits is None and contributors_known_empty))
    if (no_commits and issues_known and not has_issues and not has_recent
            and last_commit_age_days is None):
        return "vazio"
    if has_recent or (last_commit_age_days is not None and last_commit_age_days < 90):
        return "ativo"
    if last_commit_age_days is not None and last_commit_age_days >= _DONE_DAYS:
        return "concluido"
    return "fase_final"


def _report_highlights(summary, activity, contributors, mrs, milestones,
                       repo_commits, status, last_commit_age_days=None,
                       unavailable=None):
    """Insights por regras, adaptados ao tipo de projeto. 100% determinístico."""
    unavailable = list(unavailable or [])
    if status == "vazio":
        return ["📭 Repositório sem commits nem issues registadas"]

    has_issues = summary["total"] > 0
    has_recent = activity["commits"] > 0
    h = []
    if unavailable:
        h.append("⚠️ Dados parciais — não foi possível obter do GitLab: "
                 + ", ".join(unavailable))
    age = _humanize_age(last_commit_age_days)
    age_sfx = f" (último commit há ~{age})" if age else ""
    plus = "" if activity.get("complete", True) else "+"

    # 1) Atividade / estado do projeto
    if has_recent:
        top = activity["top"][0]["name"] if activity["top"] else "?"
        h.append(f"🔥 {activity['commits']}{plus} commits (90 dias) por "
                 f"{activity['authors']}{plus} autores — mais ativo: {top}")
    elif activity.get("unknown"):
        if status == "ativo":
            h.append(f"🔥 Projeto ativo{age_sfx}")
        else:
            h.append(f"🟢 Atividade recente indisponível{age_sfx}")
    elif status == "ativo":
        h.append(f"🔥 Projeto ativo{age_sfx}")
    elif status == "concluido":
        h.append(f"✅ Projeto concluído — {repo_commits} commits no total, "
                 f"sem atividade há mais de 2 anos{age_sfx}")
    elif status == "fase_final" and repo_commits:
        h.append(f"🟢 Projeto em fase final — {repo_commits} commits no total, "
                 f"sem atividade nos últimos 90 dias{age_sfx}")
    else:
        h.append(f"🟢 Sem atividade nos últimos 90 dias{age_sfx}")

    # 2) Issues — só quando o projeto as usa (0 issues ≠ "fase inicial")
    if has_issues:
        p = summary["progress"]
        approx = "" if summary.get("exact", True) else "~"
        if p >= 80:
            h.append(f"✅ {approx}{p}% das issues fechadas")
        elif p >= 50:
            h.append(f"🔹 A meio — {approx}{p}% das issues fechadas")
        else:
            h.append(f"🔸 {approx}{p}% das issues fechadas")
        if not summary.get("exact", True):
            h.append(f"ℹ️ Lista de issues limitada pelo GitLab ({summary['listed']} lidas) — "
                     "contagens aproximadas")
        more = "" if summary.get("open_complete", True) else "+"
        if summary["overdue"]:
            h.append(f"⚠️ {summary['overdue']}{more} issue(s) em atraso a precisar de atenção")
        elif more:
            h.append("ℹ️ Sem issues em atraso entre as issues abertas lidas")
        else:
            h.append("✅ Sem issues em atraso")
    elif summary.get("known", True):
        h.append("📋 Sem issues registadas no GitLab")

    # 3) Autor principal — relevante sobretudo em repos de código: sem atividade
    #    recente, o "mais ativo" acima não aparece, por isso nomeamo-lo aqui.
    if not has_recent and contributors["top"]:
        tc = contributors["top"][0]
        scope = " nos últimos ~2000 commits" if contributors.get("capped") else ""
        h.append(f"👤 Autor principal: {tc['name']} ({tc['commits']} commits{scope})")

    # 4) MRs / milestones
    if mrs["open"]:
        plus = "" if mrs.get("complete", True) else "+"
        h.append(f"🔀 {mrs['open']}{plus} merge request(s) por rever")
    late = sum(1 for m in milestones if m["status"] == "overdue")
    soon = sum(1 for m in milestones if m["status"] == "soon")
    if late:
        h.append(f"📅 {late} milestone(s) com prazo ultrapassado")
    elif soon:
        h.append(f"📅 {soon} milestone(s) com prazo nos próximos 7 dias")
    return h


def _sprint_report_md(d):
    """Render the report dict as exportable Markdown (.md download). Adaptativo:
    omite o progresso quando não há issues e a atividade quando o projeto está
    concluído (sem commits recentes)."""
    s = d["summary"]
    L = [f"# Relatório do projeto — {d['project']}",
         f"_Gerado em {d['generated_at']}_", ""]

    if d.get("about"):
        L.append("## Sobre")
        L += [f"- {x}" for x in d["about"]]
        L.append("")

    L.append("## Destaques")
    L += [f"- {x}" for x in d["highlights"]]

    L += ["", "## Issues"]
    if d.get("has_issues"):
        approx = "" if s.get("exact", True) else " (aproximado — lista limitada)"
        L += [f"- Progresso: **{s['progress']}%** ({s['closed']}/{s['total']} fechadas){approx}",
              f"- Abertas: {s['open']} · Fechadas: {s['closed']} · Total: {s['total']}",
              f"- Em atraso: {s['overdue']}{'' if s.get('open_complete', True) else '+'}"]
        if d["overdue_issues"]:
            L += ["", "### Issues em atraso"]
            L += [f"- #{i['iid']} {i['title']} (prazo: {i['due_date']})"
                  for i in d["overdue_issues"]]
    elif s.get("known", True):
        L.append("- Este projeto não usa issues do GitLab.")
    else:
        L.append("- Issues indisponíveis (erro ao ler do GitLab).")

    # Atividade (90 dias) — apenas em projetos ativos; um repo concluído não
    # precisa de uma secção a dizer "0 commits".
    if d.get("has_recent_activity"):
        a = d["activity90d"]
        plus = "" if a.get("complete", True) else "+"
        L += ["", "## Atividade (90 dias)",
              f"- {a['commits']}{plus} commits por {a['authors']}{plus} autores"]
        L += [f"- {t['name']}: {t['count']} commits" for t in a["top"]]

    c = d["contributors"]
    L += ["", "## Commits"]
    if d.get("repo_commits") is not None:
        L.append(f"- Total no repositório: {d['repo_commits']}")
    scope = " — contagens dos últimos ~2000 commits sem merges" if c.get("capped") else ""
    more = "" if c.get("complete", True) else "+"
    L.append(f"- Autores: {c['authors']}{more} (commits por autor abaixo{scope})")
    L += [f"- {t['name']}: {t['commits']} commits" for t in c["top"]]

    if d["mrs"]["total"]:
        plus = "" if d["mrs"].get("complete", True) else "+"
        L += ["", "## Merge Requests",
              f"- Abertos: {d['mrs']['open']}{plus} · Total: {d['mrs']['total']}{plus}"]
    if d["milestones"]:
        L += ["", "## Milestones"]
        tag = {"overdue": " ⚠️ atrasado", "soon": " ⏳ em breve"}
        L += [f"- {m['title']} (prazo: {m['due_date'] or '—'}){tag.get(m['status'], '')}"
              for m in d["milestones"]]
        if d.get("milestones_more"):
            L.append(f"- … e mais {d['milestones_more']} milestone(s)")
    return "\n".join(L)


_SECTION_NAMES = {"info": "projeto", "issues": "issues", "commits": "commits (90 dias)",
                  "contributors": "contribuidores", "milestones": "milestones",
                  "mrs": "merge requests", "languages": "linguagens",
                  "last_commit": "último commit"}


def build_sprint_report(project_info, issues, commits90, contribs,
                        milestones, mrs=None, today=None,
                        languages=None, tree=None, last_commit_date=None,
                        unavailable=None, issue_counts=None, opened_issues=None,
                        mr_counts=None):
    """Assemble the project report from pre-fetched GitLab data.

    Pure: takes data, returns a dict with a structured view + an exportable
    `markdown` string. `today` is injectable for deterministic tests. Tolerates
    any section being None (a failed fetch) — a None section is treated as
    UNKNOWN, never as "zero"; `unavailable` names the sections that failed so the
    report says so. `languages`/`tree` alimentam a secção "Sobre" (opcionais).
    `issue_counts`/`opened_issues`: exact counts / open list when `issues` was
    truncated by the page limit."""
    today = today or date.today()
    unavailable = [_SECTION_NAMES.get(u, u) for u in (unavailable or [])]
    issues_known = issues is not None
    commits_known = commits90 is not None
    contribs_known = contribs is not None
    issues = issues if issues is not None else []
    commits90 = commits90 if commits90 is not None else []
    raw_contribs = contribs if contribs is not None else []
    contribs = merge_contributors(raw_contribs)
    milestones = milestones or []
    mrs = mrs or []

    st = _issue_stats(issues, today=today, counts=issue_counts, opened=opened_issues)
    summary = {
        "open": st["open_count"], "closed": st["closed_count"],
        "total": st["total"], "progress": st["progress"],
        "overdue": len(st["overdue"]), "no_assignee": st["no_assignee"],
        "exact": st["exact"], "open_complete": st["open_complete"],
        "listed": len(issues), "known": issues_known,
    }

    pname = "?"
    pid = None
    repo_commits = None  # canonical total (statistics.commit_count) — same as the panel
    if project_info:
        pname = (project_info.get("name_with_namespace")
                 or project_info.get("name") or "?")
        pid = project_info.get("id")
        repo_commits = (project_info.get("statistics") or {}).get("commit_count")

    by90 = Counter((c.get("author_name") or "?") for c in commits90)
    activity = {
        "commits": len(commits90), "authors": len(by90),
        "top": [{"name": n, "count": c} for n, c in by90.most_common(5)],
        "complete": not getattr(commits90, "truncated", False),
        "unknown": not commits_known,
    }

    contributors = {
        "total": sum(c["commits"] for c in contribs),
        "authors": len(contribs),
        "top": [{"name": c["name"], "commits": c["commits"]} for c in contribs[:5]],
        "capped": contributors_capped(raw_contribs),
        "complete": not getattr(raw_contribs, "truncated", False),
    }

    mr_data = mr_summary(mr_counts, mrs)

    ms_all = [{"title": m.get("title"), "due_date": m.get("due_date"),
               "status": _due_status(m.get("due_date"), today)}
              for m in milestones_by_due(milestones)]
    ms_data = ms_all[:REPORT_MILESTONES]
    ms_more = len(ms_all) - len(ms_data)

    overdue_issues = [{"iid": i.get("iid"), "title": i.get("title", ""),
                       "due_date": i.get("due_date")} for i in st["overdue"][:10]]

    # Idade do último commit → distingue "fase final" (meses) de "concluído" (anos).
    last_commit_age_days = None
    if last_commit_date:
        try:
            last_commit_age_days = max(0, (
                today - date.fromisoformat(str(last_commit_date)[:10])).days)
        except (ValueError, TypeError):
            last_commit_age_days = None
    status = _project_status(
        summary, activity, repo_commits, last_commit_age_days,
        contributors_known_empty=contribs_known and not raw_contribs,
        issues_known=issues_known)

    data = {
        "generated_at": today.isoformat(),
        "project": pname,
        "project_id": pid,
        "repo_commits": repo_commits,
        "status": status,                       # ativo | fase_final | concluido | vazio
        "last_commit_date": last_commit_date,
        "last_commit_age_days": last_commit_age_days,
        "about": _about_lines(project_info, languages, tree),
        "has_issues": summary["total"] > 0,
        "has_recent_activity": activity["commits"] > 0,
        "summary": summary,
        "unavailable": unavailable,
        "highlights": _report_highlights(summary, activity, contributors,
                                         mr_data, ms_all, repo_commits, status,
                                         last_commit_age_days, unavailable),
        "activity90d": activity,
        "contributors": contributors,
        "mrs": mr_data,
        "milestones": ms_data,
        "milestones_more": ms_more,
        "overdue_issues": overdue_issues,
    }
    data["markdown"] = _sprint_report_md(data)
    return data
