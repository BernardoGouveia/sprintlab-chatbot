"""Tests for the sprint report ('Relatórios automáticos' feature).

The whole point of building the report deterministically server-side is that the
numbers are *exact* — never an LLM guess. These tests lock that contract in."""

from datetime import date

import server

TODAY = date(2026, 6, 11)

PROJECT = {"id": 42, "name_with_namespace": "GMV / air-demo",
           "statistics": {"commit_count": 2318}}
ISSUES = [
    {"state": "closed", "iid": 1, "title": "a"},
    {"state": "closed", "iid": 2, "title": "b"},
    {"state": "opened", "iid": 3, "title": "atrasada",
     "due_date": "2026-06-01", "assignee": {"name": "Ana"}},
    {"state": "opened", "iid": 4, "title": "no prazo",
     "due_date": "2026-12-01", "assignee": {"name": "Rui"}},
    {"state": "opened", "iid": 5, "title": "sem dono"},
]
COMMITS90 = [
    {"author_name": "Ana"}, {"author_name": "Ana"}, {"author_name": "Rui"},
]
CONTRIBS = [
    {"name": "Ana", "commits": 200},
    {"name": "Rui", "commits": 150},
]
MILESTONES = [
    {"title": "Sprint 1", "due_date": "2026-06-01"},   # overdue
    {"title": "Sprint 2", "due_date": "2026-06-15"},   # soon (<=7d)
    {"title": "Sprint 3", "due_date": "2026-09-01"},   # ok
]
MRS = [{"state": "opened"}, {"state": "merged"}, {"state": "opened"}]


def _report(**over):
    kw = dict(project_info=PROJECT, issues=ISSUES, commits90=COMMITS90,
              contribs=CONTRIBS, milestones=MILESTONES, mrs=MRS, today=TODAY)
    kw.update(over)
    return server.build_sprint_report(**kw)


# ── _due_status ───────────────────────────────────────────────────────────────

class TestDueStatus:
    def test_overdue(self):
        assert server._due_status("2026-06-01", TODAY) == "overdue"

    def test_soon_within_7_days(self):
        assert server._due_status("2026-06-15", TODAY) == "soon"
        assert server._due_status("2026-06-18", TODAY) == "soon"  # exactly 7

    def test_ok_when_far(self):
        assert server._due_status("2026-06-19", TODAY) == "ok"   # 8 days

    def test_none_and_bad(self):
        assert server._due_status(None, TODAY) is None
        assert server._due_status("garbage", TODAY) is None


# ── build_sprint_report: numbers ──────────────────────────────────────────────

class TestReportNumbers:
    def test_summary_matches_issue_stats(self):
        d = _report()
        expected = {"open": 3, "closed": 2, "total": 5, "progress": 40.0,
                    "overdue": 1, "no_assignee": 1, "exact": True}
        assert {k: d["summary"][k] for k in expected} == expected

    def test_project_name_and_id(self):
        d = _report()
        assert d["project"] == "GMV / air-demo"
        assert d["project_id"] == 42

    def test_activity_90d(self):
        d = _report()
        assert d["activity90d"]["commits"] == 3
        assert d["activity90d"]["authors"] == 2
        assert d["activity90d"]["top"][0] == {"name": "Ana", "count": 2}

    def test_contributors_all_time(self):
        d = _report()
        assert d["contributors"]["total"] == 350
        assert d["contributors"]["authors"] == 2

    def test_repo_commit_count_is_canonical_total(self):
        # the headline total comes from statistics.commit_count (same as the stats
        # panel) — NOT the per-author contributor sum (350), which differs
        d = _report()
        assert d["repo_commits"] == 2318
        assert "Total no repositório: 2318" in d["markdown"]

    def test_repo_commits_none_when_no_statistics(self):
        d = _report(project_info={"id": 1, "name_with_namespace": "x / y"})
        assert d["repo_commits"] is None
        assert "Total no repositório" not in d["markdown"]

    def test_merge_requests(self):
        d = _report()
        assert d["mrs"]["open"] == 2 and d["mrs"]["total"] == 3

    def test_milestone_statuses(self):
        d = _report()
        statuses = {m["title"]: m["status"] for m in d["milestones"]}
        assert statuses == {"Sprint 1": "overdue", "Sprint 2": "soon", "Sprint 3": "ok"}

    def test_overdue_issues_listed(self):
        d = _report()
        assert [i["iid"] for i in d["overdue_issues"]] == [3]

    def test_generated_at_uses_injected_today(self):
        assert _report()["generated_at"] == "2026-06-11"


# ── highlights (rule-based) ───────────────────────────────────────────────────

class TestHighlights:
    def test_mentions_progress(self):
        hs = " ".join(_report()["highlights"])
        assert "40" in hs and "%" in hs

    def test_flags_overdue(self):
        hs = " ".join(_report()["highlights"])
        assert "atraso" in hs.lower()

    def test_no_overdue_reads_clean(self):
        only_closed = [{"state": "closed", "iid": 1, "title": "x"}]
        hs = " ".join(_report(issues=only_closed)["highlights"])
        assert "Sem issues em atraso" in hs

    def test_dormant_recent_reads_as_final_phase(self):
        # parado há ~8 meses (< 2 anos) → "fase final", NÃO "concluído"
        hs = " ".join(_report(commits90=[],
                              last_commit_date="2025-10-11")["highlights"])
        assert "fase final" in hs.lower() and "sem atividade" in hs.lower()
        assert "concluído" not in hs.lower()

    def test_dormant_old_reads_as_concluded(self):
        # parado há ~3 anos (≥ 2 anos) → "concluído" (critério do orientador)
        hs = " ".join(_report(commits90=[],
                              last_commit_date="2023-06-11")["highlights"])
        assert "concluído" in hs.lower() and "2 anos" in hs.lower()

    def test_code_project_no_misleading_progress(self):
        # 0 issues → "sem issues registadas", NUNCA "fase inicial / 0% concluído"
        hs = " ".join(_report(issues=[], commits90=[])["highlights"])
        assert "Sem issues registadas" in hs
        assert "fase inicial" not in hs.lower()

    def test_dormant_names_main_author(self):
        hs = " ".join(_report(commits90=[])["highlights"])
        assert "Autor principal" in hs and "Ana" in hs


# ── markdown export ───────────────────────────────────────────────────────────

class TestMarkdown:
    def test_has_title_and_project(self):
        md = _report()["markdown"]
        assert "Relatório do projeto" in md
        assert "GMV / air-demo" in md

    def test_lists_overdue_issue(self):
        md = _report()["markdown"]
        assert "#3" in md and "atrasada" in md

    def test_is_a_string(self):
        assert isinstance(_report()["markdown"], str)


# ── resilience: any section may be None when a fetch fails ────────────────────

class TestResilience:
    def test_all_none_does_not_crash(self):
        d = server.build_sprint_report(None, None, None, None, None,
                                       mrs=None, today=TODAY)
        assert d["summary"]["total"] == 0
        assert d["project"] == "?"
        assert d["mrs"]["open"] == 0 and d["mrs"]["total"] == 0
        assert isinstance(d["markdown"], str)
        # dados desconhecidos ≠ "vazio"
        assert d["status"] != "vazio"

    def test_missing_project_info(self):
        d = _report(project_info=None)
        assert d["project"] == "?"
        assert d["project_id"] is None


# ── adaptativo ao tipo de projeto (a melhoria do lp2-jogo / air-demo) ─────────

class TestAdaptive:
    def test_status_active(self):
        assert _report()["status"] == "ativo"           # tem commits recentes

    def test_status_final_phase_when_recently_dormant(self):
        # 0 commits recentes mas último há ~8 meses → "fase_final" (não concluído)
        d = _report(commits90=[], last_commit_date="2025-10-11")
        assert d["status"] == "fase_final"
        assert d["has_recent_activity"] is False
        assert 0 < d["last_commit_age_days"] < 730

    def test_status_concluded_when_2_years_dormant(self):
        # sem commits há ≥ 2 anos → "concluido"
        d = _report(commits90=[], last_commit_date="2023-06-11")
        assert d["status"] == "concluido"
        assert d["last_commit_age_days"] >= 730

    def test_status_final_phase_when_age_unknown(self):
        # sem a data (fetch falhou) → NÃO afirmar "concluído" sem prova → fase_final
        d = _report(commits90=[])
        assert d["status"] == "fase_final"
        assert d["last_commit_age_days"] is None

    def test_status_empty(self):
        d = _report(project_info={"id": 1, "name_with_namespace": "x / y"},
                    issues=[], commits90=[], contribs=[])
        assert d["status"] == "vazio"
        assert "Repositório sem commits" in d["highlights"][0]

    def test_activity_section_dropped_when_concluded(self):
        # a secção "Atividade (90 dias)" não aparece num projeto concluído
        assert "## Atividade (90 dias)" not in _report(commits90=[])["markdown"]
        # mas aparece num projeto ativo
        assert "## Atividade (90 dias)" in _report()["markdown"]

    def test_no_issues_section_is_a_one_liner(self):
        md = _report(issues=[], commits90=[])["markdown"]
        assert "Este projeto não usa issues do GitLab" in md
        assert "Progresso:" not in md   # nada de percentagem enganadora

    def test_about_section_from_languages_and_tree(self):
        md = _report(languages={"Java": 100.0},
                     tree=[{"name": "src", "type": "tree"},
                           {"name": "README.md", "type": "blob"}])["markdown"]
        assert "## Sobre" in md
        assert "Java 100%" in md and "src" in md

    def test_about_uses_description(self):
        pi = dict(PROJECT, description="Jogo de tabuleiro em Java")
        d = _report(project_info=pi)
        assert any("Jogo de tabuleiro" in x for x in d["about"])

    def test_commits_total_survives_in_code_project(self):
        # o lp2-jogo: 0 issues, mas os 153 commits têm de aparecer
        md = _report(issues=[], commits90=[])["markdown"]
        assert "Total no repositório: 2318" in md


# ── estado só com provas (falhas de fetch ≠ "zero") ───────────────────────────

class TestStatusEvidence:
    def test_failed_commit_window_with_recent_last_commit_is_active(self):
        # a lista de 90 dias falhou (None) mas o último commit foi há 2 dias
        d = _report(commits90=None, last_commit_date="2026-06-09")
        assert d["status"] == "ativo"
        hs = " ".join(d["highlights"]).lower()
        assert "sem atividade" not in hs and "fase final" not in hs

    def test_failed_commit_window_is_not_no_activity(self):
        d = _report(commits90=None)
        assert "sem atividade nos últimos 90 dias" not in " ".join(d["highlights"]).lower()

    def test_missing_statistics_with_contributors_is_not_empty(self):
        # token sem Reporter → sem `statistics`, mas há contribuidores e commits
        d = _report(project_info={"id": 1, "name_with_namespace": "x / y"},
                    issues=[], commits90=[], last_commit_date="2024-01-10")
        assert d["status"] != "vazio"

    def test_failed_issues_fetch_is_not_no_issues(self):
        d = _report(issues=None, unavailable=["issues"])
        md = d["markdown"]
        assert "não usa issues" not in md and "indisponíveis" in md
        assert any("Dados parciais" in h for h in d["highlights"])

    def test_future_commit_date_is_clamped(self):
        d = _report(last_commit_date="2026-06-20")
        assert d["last_commit_age_days"] == 0


class TestTruncatedIssues:
    def test_exact_counts_used_when_list_truncated(self):
        from src.gitlab_api import PagedList
        rows = PagedList(ISSUES)
        rows.truncated = True
        d = _report(issues=rows, issue_counts={"all": 750, "opened": 300, "closed": 450})
        assert d["summary"]["total"] == 750 and d["summary"]["progress"] == 60.0
        assert d["summary"]["exact"] is True

    def test_without_counts_the_report_says_approximate(self):
        from src.gitlab_api import PagedList
        rows = PagedList(ISSUES)
        rows.truncated = True
        d = _report(issues=rows)
        assert d["summary"]["exact"] is False
        assert "aproximad" in d["markdown"]


class TestMilestoneCap:
    def test_long_milestone_lists_are_capped_but_counted(self):
        many = [{"title": f"M{i:02d}", "due_date": f"2026-0{1 + i % 9}-15"} for i in range(40)]
        d = _report(milestones=many)
        assert len(d["milestones"]) == 15 and d["milestones_more"] == 25
        assert "… e mais 25 milestone(s)" in d["markdown"]
        # os destaques contam TODAS (não só as 15 mostradas)
        late = sum(1 for m in many if m["due_date"] < "2026-06-11")
        assert any(f"{late} milestone(s) com prazo ultrapassado" in h for h in d["highlights"])


class TestContributorsMerged:
    def test_same_person_two_emails_counts_once(self):
        contribs = [{"name": "Ana", "email": "a@work", "commits": 10},
                    {"name": "Ana", "email": "a@home", "commits": 5},
                    {"name": "Rui", "email": "r@x", "commits": 7}]
        d = _report(contribs=contribs)
        assert d["contributors"]["authors"] == 2
        assert d["contributors"]["top"][0] == {"name": "Ana", "commits": 15}
