"""Tests for the AI-commit feature (code_commit.py) — the pure layer.

The plan validation is the safety net between the LLM's JSON and the GitLab
write: bad paths, oversized content and malformed plans must die HERE."""

import json

import src.code_commit as cc
from src.llm import _parse_json_obj


# ── _slugify: branch-safe names ───────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert cc._slugify("Valida Emails") == "valida-emails"

    def test_collapses_symbols(self):
        assert cc._slugify("a!!b__c  d") == "a-b-c-d"

    def test_only_safe_charset(self):
        out = cc._slugify("ação rápida: criar função!")
        assert out and all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in out)

    def test_caps_length(self):
        assert len(cc._slugify("x" * 100)) <= 32

    def test_empty_falls_back(self):
        assert cc._slugify("") == "alteracao"
        assert cc._slugify("!!!") == "alteracao"


# ── _validate_files: the write guard ──────────────────────────────────────────

def _ok_file(path="src/novo.py", content="print('olá')\n"):
    return {"path": path, "content": content}


class TestValidateFiles:
    def test_accepts_and_normalises(self):
        files, err = cc._validate_files([_ok_file("./src/a.py")])
        assert err is None
        assert files[0]["path"] == "src/a.py"

    def test_rejects_traversal(self):
        _, err = cc._validate_files([_ok_file("../../etc/passwd")])
        assert err and "não permitido" in err

    def test_rejects_absolute(self):
        _, err = cc._validate_files([_ok_file("/etc/passwd")])
        assert err

    def test_rejects_git_dir(self):
        _, err = cc._validate_files([_ok_file(".git/hooks/pre-commit")])
        assert err and ".git" in err

    def test_rejects_empty_content(self):
        _, err = cc._validate_files([_ok_file(content="   ")])
        assert err and "vazio" in err.lower()

    def test_rejects_oversized(self):
        _, err = cc._validate_files([_ok_file(content="x" * (cc.MAX_CONTENT + 1))])
        assert err and "grande" in err

    def test_rejects_too_many(self):
        _, err = cc._validate_files([_ok_file(f"f{i}.py") for i in range(cc.MAX_FILES + 1)])
        assert err and str(cc.MAX_FILES) in err

    def test_rejects_duplicates(self):
        _, err = cc._validate_files([_ok_file("a.py"), _ok_file("a.py")])
        assert err and "duplicado" in err.lower()

    def test_rejects_non_list(self):
        assert cc._validate_files(None)[1]
        assert cc._validate_files([])[1]
        assert cc._validate_files(["str"])[1]

    def test_rejects_ci_configuration(self):
        # um pipeline correria o código antes de alguém rever o MR
        for p in (".gitlab-ci.yml", "sub/.GitLab-CI.yaml", ".gitlab/ci/deploy.yml",
                  ".gitlab/CODEOWNERS"):
            _, err = cc._validate_files([_ok_file(p)])
            assert err, p

    def test_rejects_git_dir_at_any_depth_and_case(self):
        for p in ("sub/.git/config", ".GIT/hooks/post-checkout"):
            _, err = cc._validate_files([_ok_file(p)])
            assert err and ".git" in err, p

    def test_rejects_empty_and_dot_segments_and_control_chars(self):
        for p in ("a//b.py", "a/./b.py", "a\x00b.py"):
            assert cc._validate_files([_ok_file(p)])[1], repr(p)


# ── plan_from_llm_text: model JSON → validated plan ───────────────────────────

PLAN_JSON = json.dumps({
    "branch_slug": "Valida Emails",
    "commit_message": "Adiciona validador de emails",
    "summary": "Criei um validador simples.",
    "files": [{"path": "src/validador.py", "content": "def v(e):\n    return '@' in e\n"}],
})


class TestPlanFromText:
    def test_valid_json(self):
        plan, err = cc.plan_from_llm_text(PLAN_JSON)
        assert err is None
        assert plan["branch"] == "ai/valida-emails"
        assert plan["commit_message"] == "Adiciona validador de emails"
        assert plan["files"][0]["path"] == "src/validador.py"

    def test_tolerates_fences_and_prose(self):
        plan, err = cc.plan_from_llm_text(f"Aqui está:\n```json\n{PLAN_JSON}\n```\nEspero que ajude!")
        assert err is None and plan["branch"] == "ai/valida-emails"

    def test_invalid_json_errors(self):
        plan, err = cc.plan_from_llm_text("não sei fazer isso")
        assert plan is None and "JSON" in err

    def test_bad_files_propagate_error(self):
        bad = json.dumps({"branch_slug": "x", "commit_message": "y",
                          "files": [{"path": "../mau.py", "content": "x"}]})
        plan, err = cc.plan_from_llm_text(bad)
        assert plan is None and err

    def test_missing_message_gets_default(self):
        txt = json.dumps({"files": [{"path": "a.py", "content": "x = 1"}]})
        plan, err = cc.plan_from_llm_text(txt)
        assert err is None
        assert plan["commit_message"]            # default aplicado
        assert plan["branch"].startswith("ai/")

    def test_caps_message_length(self):
        txt = json.dumps({"commit_message": "m" * 500,
                          "files": [{"path": "a.py", "content": "x"}]})
        plan, _ = cc.plan_from_llm_text(txt)
        assert len(plan["commit_message"]) <= 120


# ── _parse_json_obj (llm.py) ──────────────────────────────────────────────────

class TestParseJsonObj:
    def test_clean_object(self):
        assert _parse_json_obj('{"a": 1}') == {"a": 1}

    def test_with_think_block_and_prose(self):
        assert _parse_json_obj('<think>hmm</think>Claro: {"a": 1} pronto') == {"a": 1}

    def test_non_object_returns_none(self):
        assert _parse_json_obj('[1, 2]') is None
        assert _parse_json_obj("nada") is None
        assert _parse_json_obj("") is None

    def test_prose_with_braces_after_the_json(self):
        txt = '```json\n{"a": 1}\n```\nNota: o objeto de config usa {chave: valor}'
        assert _parse_json_obj(txt) == {"a": 1}

    def test_prose_with_braces_before_the_json(self):
        assert _parse_json_obj('Usa {} para dicionários: {"a": 1}') == {"a": 1}

    def test_think_tags_inside_json_content_are_preserved(self):
        code = "start = t.index('<think>')\nend = t.index('</think>')"
        txt = json.dumps({"files": [{"path": "a.py", "content": code}]})
        assert _parse_json_obj(txt)["files"][0]["content"] == code

    def test_raw_newlines_inside_strings_are_tolerated(self):
        assert _parse_json_obj('{"content": "linha1\nlinha2"}') == {"content": "linha1\nlinha2"}


# ── generate_commit_plan / execute_commit_plan (GitLab + Groq faked) ──────────

class TestGenerateCommitPlan:
    def test_truncated_reply_explains_instead_of_generic_error(self, monkeypatch):
        monkeypatch.setattr(cc, "get_project_info", lambda: {"default_branch": "main"})
        monkeypatch.setattr(cc, "_get_languages", lambda: {})
        monkeypatch.setattr(cc, "_get_repo_tree_top", lambda ref: [])
        monkeypatch.setattr(cc, "_groq_complete", lambda p: {"choices": [
            {"message": {"content": '{"files": [{"path": "a.py", "content": "x'},
             "finish_reason": "length"}]})
        plan, err = cc.generate_commit_plan("cria um CRUD completo")
        assert plan is None and "demais" in err


class _Http:
    @staticmethod
    def error(code, message=""):
        import io
        import urllib.error
        body = json.dumps({"message": message}).encode()
        return urllib.error.HTTPError("https://gitlab", code, "err", {}, io.BytesIO(body))


class TestExecuteCommitPlan:
    PLAN = {"branch": "ai/x", "commit_message": "Msg",
            "files": [{"path": "a.py", "content": "x = 1\n"}]}

    def _fake(self, monkeypatch, on_commit=None, file_status=404, branch_errors=()):
        calls = []
        branch_errors = list(branch_errors)

        def fake_request(method, endpoint, body=None, params=None, invalidate=None):
            calls.append((method, endpoint, body, params))
            if method == "POST" and endpoint.endswith("/repository/branches"):
                if branch_errors:
                    raise branch_errors.pop(0)
                return {"name": body["branch"]}
            if method == "POST" and endpoint.endswith("/repository/commits"):
                if on_commit:
                    raise on_commit
                return {"short_id": "abcdef12", "web_url": "u"}
            if method == "POST" and endpoint.endswith("/merge_requests"):
                return {"iid": 3, "web_url": "mr"}
            if method == "DELETE":
                return {}
            raise AssertionError((method, endpoint))

        def fake_get(method, endpoint, body=None, params=None, return_headers=False):
            calls.append((method, endpoint, body, params))
            if file_status == 200:
                return {"content": ""}
            raise _Http.error(file_status)

        monkeypatch.setattr(cc, "gitlab_request", fake_request)
        monkeypatch.setattr(cc, "_gitlab_request", fake_get)
        monkeypatch.setattr(cc, "get_project_info", lambda: {"default_branch": "main"})
        monkeypatch.setattr(cc, "_proj", lambda: "42")
        return calls

    def test_non_dict_plan(self):
        assert cc.execute_commit_plan("abc")["ok"] is False
        assert cc.execute_commit_plan([1])["ok"] is False

    def test_commit_skips_ci_and_checks_files_on_the_new_branch(self, monkeypatch):
        calls = self._fake(monkeypatch)
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is True
        commit = next(c for c in calls if c[1].endswith("/repository/commits"))
        assert "[skip ci]" in commit[2]["commit_message"]
        file_get = next(c for c in calls if "/repository/files/" in c[1])
        assert file_get[3]["ref"] == "ai/x"          # não a branch principal

    def test_failed_commit_deletes_the_orphan_branch(self, monkeypatch):
        calls = self._fake(monkeypatch, on_commit=_Http.error(400, "A file with this name already exists"))
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is False and "already exists" in r["error"]
        assert any(m == "DELETE" and "/repository/branches/ai%2Fx" in ep for m, ep, *_ in calls)

    def test_timeout_after_branch_is_handled(self, monkeypatch):
        calls = self._fake(monkeypatch, on_commit=TimeoutError("slow"))
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is False and "não respondeu" in r["error"]
        assert any(m == "DELETE" for m, *_ in calls)

    def test_file_check_error_aborts_instead_of_guessing_create(self, monkeypatch):
        calls = self._fake(monkeypatch, file_status=500)
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is False and "verificar" in r["error"]
        assert not any(c[1].endswith("/repository/commits") for c in calls)

    def test_only_existing_branch_errors_try_the_next_suffix(self, monkeypatch):
        calls = self._fake(monkeypatch, branch_errors=[_Http.error(400, "Branch already exists")])
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is True and r["branch"] == "ai/x-2"

    def test_other_branch_errors_are_explained(self, monkeypatch):
        self._fake(monkeypatch, branch_errors=[_Http.error(400, "Invalid reference name: main")])
        r = cc.execute_commit_plan(self.PLAN)
        assert r["ok"] is False and "Invalid reference name" in r["error"]
