"""Tests for the read-tools layer (read_tools.py) — a capability que substituiu
as respostas "não tenho acesso" (procurar commits, commits por autor, código).

Cobre também os bugs que a revisão adversarial confirmou: falha-da-API ≠ vazio,
e a flag de truncamento do scan por autor. Rede toda monkeypatched → offline."""

import src.gitlab_api as gitlab_api
import src.read_tools as rt
# ── _fmt_commit (puro) ────────────────────────────────────────────────────────

class TestFmtCommit:
    def test_fields(self):
        c = {"id": "abcdef123456", "short_id": "abcdef12", "author_name": "Ana",
             "authored_date": "2026-06-01T10:00:00Z", "title": "Corrige X"}
        assert rt._fmt_commit(c) == {
            "sha": "abcdef12", "autor": "Ana", "data": "2026-06-01", "titulo": "Corrige X"}

    def test_title_from_message_first_line(self):
        c = {"id": "x", "message": "Linha 1 do commit\nresto do corpo"}
        assert rt._fmt_commit(c)["titulo"] == "Linha 1 do commit"

    def test_missing_fields_safe(self):
        f = rt._fmt_commit({})
        assert f["autor"] == "?" and f["sha"] == "" and f["titulo"] == ""


# ── search_commits ────────────────────────────────────────────────────────────

class TestSearchCommits:
    def test_returns_matches(self, monkeypatch):
        monkeypatch.setattr(rt, "_search_commits", lambda q: [
            {"short_id": "a1b2c3d4", "author_name": "Ana",
             "authored_date": "2026-06-01T00:00:00Z", "title": "spacewire fix"}])
        r = rt.execute_read_tool("search_commits", {"query": "spacewire"})
        assert r["query"] == "spacewire"
        assert r["commits"][0]["sha"] == "a1b2c3d4" and r["commits"][0]["titulo"] == "spacewire fix"

    def test_empty_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(rt, "_search_commits", lambda q: [])
        r = rt.execute_read_tool("search_commits", {"query": "nada"})
        assert r["commits"] == [] and "erro" not in r

    def test_api_failure_reports_error_not_empty(self, monkeypatch):
        # None = FALHA da API → o modelo NÃO deve dizer "não há commits"
        monkeypatch.setattr(rt, "_search_commits", lambda q: None)
        r = rt.execute_read_tool("search_commits", {"query": "x"})
        assert "erro" in r and "commits" not in r

    def test_missing_query(self):
        assert "erro" in rt.execute_read_tool("search_commits", {})

    def test_never_raises(self, monkeypatch):
        def boom(q):
            raise RuntimeError("falha de rede")
        monkeypatch.setattr(rt, "_search_commits", boom)
        assert "erro" in rt.execute_read_tool("search_commits", {"query": "x"})


# ── commits_by_author ─────────────────────────────────────────────────────────

class TestCommitsByAuthor:
    def test_total_and_list(self, monkeypatch):
        monkeypatch.setattr(rt, "_commits_by_author", lambda a, offset=0: {
            "total": 298, "offset": offset, "parcial": False, "commits": [
                {"short_id": "z9", "author_name": "Daniel Silveira",
                 "authored_date": "2024-01-18T00:00:00Z", "title": "último"},
                {"short_id": "y8", "author_name": "Daniel Silveira",
                 "authored_date": "2024-01-10T00:00:00Z", "title": "anterior"}]})
        r = rt.execute_read_tool("commits_by_author", {"author": "Daniel Silveira"})
        assert r["autor"] == "Daniel Silveira"
        assert r["total"] == 298              # total autoritativo (contribuidores)
        assert r["mostrados"] == 2 and r["offset"] == 0
        assert r["commits"][0]["sha"] == "z9"   # newest-first
        assert "ultimo" not in r and "recentes" not in r   # forma nova

    def test_offset_pagination(self, monkeypatch):
        captured = {}
        def fake(a, offset=0):
            captured["offset"] = offset
            return {"total": 298, "offset": offset, "parcial": False, "commits": []}
        monkeypatch.setattr(rt, "_commits_by_author", fake)
        r = rt.execute_read_tool("commits_by_author", {"author": "X", "offset": 15})
        assert captured["offset"] == 15
        assert r["offset"] == 15 and "nota" in r   # "não há mais ... a partir daqui"

    def test_none_found(self, monkeypatch):
        monkeypatch.setattr(rt, "_commits_by_author",
                            lambda a, offset=0: {"total": 0, "offset": 0, "parcial": False, "commits": []})
        r = rt.execute_read_tool("commits_by_author", {"author": "Ninguém"})
        assert r["total"] == 0 and r["commits"] == []

    def test_partial_flag_adds_note(self, monkeypatch):
        monkeypatch.setattr(rt, "_commits_by_author", lambda a, offset=0: {
            "total": 5000, "offset": 0, "parcial": True,
            "commits": [{"short_id": "x", "author_name": "X", "authored_date": "2024", "title": "t"}]})
        assert "nota" in rt.execute_read_tool("commits_by_author", {"author": "X"})

    def test_partial_scan_is_not_reported_as_the_end(self, monkeypatch):
        # página vazia MAS histórico truncado → não afirmar "não há mais commits"
        monkeypatch.setattr(rt, "_commits_by_author", lambda a, offset=0: {
            "total": 298, "offset": offset, "parcial": True, "commits": []})
        r = rt.execute_read_tool("commits_by_author", {"author": "X", "offset": 45})
        assert "pode haver mais" in r["nota"] and "não há mais" not in r["nota"]

    def test_fetch_failure_is_an_error_not_zero_commits(self, monkeypatch):
        monkeypatch.setattr(rt, "_commits_by_author", lambda a, offset=0: {
            "total": 298, "offset": 0, "parcial": False, "commits": [],
            "erro": "não consegui obter a lista de commits do GitLab"})
        r = rt.execute_read_tool("commits_by_author", {"author": "Daniel"})
        assert "erro" in r and "commits" not in r

    def test_ambiguous_author_lists_candidates(self, monkeypatch):
        monkeypatch.setattr(rt, "_commits_by_author", lambda a, offset=0: {
            "total": None, "commits": [], "offset": 0, "parcial": False,
            "candidatos": ["Ana Silva", "Ana Costa"]})
        r = rt.execute_read_tool("commits_by_author", {"author": "Ana"})
        assert r["candidatos"] == ["Ana Silva", "Ana Costa"] and "erro" in r

    def test_missing_author(self):
        assert "erro" in rt.execute_read_tool("commits_by_author", {})


# ── search_code ───────────────────────────────────────────────────────────────

class TestSearchCode:
    def test_dedups_paths(self, monkeypatch):
        monkeypatch.setattr(rt, "_search_blobs", lambda q: [
            {"path": "src/spacewire.c"}, {"path": "src/spacewire.c"}, {"path": "docs/sw.md"}])
        r = rt.execute_read_tool("search_code", {"query": "spacewire"})
        assert r["ficheiros"] == ["src/spacewire.c", "docs/sw.md"]

    def test_api_failure_reports_error(self, monkeypatch):
        monkeypatch.setattr(rt, "_search_blobs", lambda q: None)
        r = rt.execute_read_tool("search_code", {"query": "x"})
        assert "erro" in r and "ficheiros" not in r

    def test_dedup_happens_before_the_display_cap(self, monkeypatch):
        # 20 excertos do mesmo ficheiro não podem esconder os outros 30 ficheiros
        rows = [{"path": "big.c"}] * 20 + [{"path": f"f{i}.c"} for i in range(30)]
        monkeypatch.setattr(rt, "_search_blobs", lambda q: rows)
        r = rt.execute_read_tool("search_code", {"query": "x"})
        assert r["total_ficheiros"] == 31 and len(r["ficheiros"]) == 15


class TestSearchCommitTotals:
    def test_total_counts_all_rows_not_the_display_cap(self, monkeypatch):
        rows = [{"short_id": f"s{i}", "title": "t"} for i in range(100)]
        monkeypatch.setattr(rt, "_search_commits", lambda q: rows)
        r = rt.execute_read_tool("search_commits", {"query": "x"})
        assert r["mostrados"] == 10 and r["total_visto"] == 100 and "nota" in r


class TestGetIssue:
    def test_from_cached_list(self, monkeypatch):
        monkeypatch.setattr(rt, "get_all_issues", lambda state="all": [
            {"iid": 12, "title": "Login", "state": "opened",
             "assignees": [{"name": "Ana"}, {"name": "Rui"}], "labels": ["bug"],
             "milestone": {"title": "Sprint 3"}, "due_date": "2026-07-01"}])
        r = rt.execute_read_tool("get_issue", {"iid": 12})
        assert r["assignees"] == ["Ana", "Rui"] and r["milestone"] == "Sprint 3"
        assert r["estado"] == "aberta"

    def test_falls_back_to_gitlab(self, monkeypatch):
        monkeypatch.setattr(rt, "get_all_issues", lambda state="all": [])
        monkeypatch.setattr(rt, "get_issue", lambda iid: {
            "iid": iid, "title": "Antiga", "state": "closed", "assignees": []})
        r = rt.execute_read_tool("get_issue", {"iid": "#300"})
        assert r["titulo"] == "Antiga" and r["assignees"] == ["ninguém"]

    def test_invalid_number(self):
        assert "erro" in rt.execute_read_tool("get_issue", {"iid": "abc"})


class TestMisc:
    def test_unknown_tool(self):
        assert "erro" in rt.execute_read_tool("inexistente", {})

    def test_names_match_definitions(self):
        assert rt.READ_TOOL_NAMES == {"search_commits", "commits_by_author",
                                      "search_code", "get_issue"}
        assert len(rt.READ_TOOLS) == 4


# ── _search sentinel: falha (None) vs vazio ([]) ──────────────────────────────

class TestSearchSentinel:
    def test_empty_query_is_empty_list(self):
        assert gitlab_api._search("commits", "") == []

    def test_failure_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("403")
        monkeypatch.setattr(gitlab_api, "_gitlab_request", boom)
        gitlab_api.cache.invalidate()
        assert gitlab_api._search("blobs", "spacewire") is None
