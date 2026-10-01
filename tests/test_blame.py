"""Tests for the code-investigation helpers (git blame feature).

The deterministic layer — which commit owns a line, path resolution, diff
truncation — must be exact: it's presented to the user as fact."""

import pytest

import server


# ── _path_candidates: pasted paths are often absolute (IDE / stack trace) ─────

class TestPathCandidates:
    def test_relative_path_kept_first(self):
        assert server._path_candidates("src/app.js")[0] == "src/app.js"

    def test_strips_leading_segments_down_to_basename(self):
        out = server._path_candidates("C:/Users/x/proj/src/app.js")
        assert out[0] == "C:/Users/x/proj/src/app.js"
        assert "src/app.js" in out
        assert out[-1] == "app.js"

    def test_backslashes_normalised(self):
        out = server._path_candidates("src\\sub\\file.py")
        assert out[0] == "src/sub/file.py"
        assert out[-1] == "file.py"

    def test_dot_slash_and_leading_slash(self):
        assert server._path_candidates("./src/a.py")[0] == "src/a.py"
        assert server._path_candidates("/src/a.py")[0] == "src/a.py"

    def test_no_duplicates(self):
        out = server._path_candidates("a/a.py")
        assert len(out) == len(set(out))

    def test_caps_at_8(self):
        deep = "/".join(["d"] * 20) + "/f.py"
        assert len(server._path_candidates(deep)) <= 8

    def test_cap_keeps_repo_relative_suffix_of_deep_paths(self):
        # workspace de CI/OneDrive com muitos segmentos antes da raiz do repo
        deep = "/var/lib/jenkins/workspace/org/team/job/repo/src/main/java/App.java"
        out = server._path_candidates(deep)
        assert len(out) <= 8
        assert out[0] == deep.lstrip("/")
        assert "src/main/java/App.java" in out and out[-1] == "App.java"

    def test_empty(self):
        assert server._path_candidates("") == []
        assert server._path_candidates(None) == []

    @pytest.mark.parametrize("pasted, repo_path", [
        # IntelliJ on Windows (the chat drops "C:"), Maven layout
        ("\\Users\\aluno\\IdeaProjects\\lp2-projeto\\src\\main\\java\\pt\\ulusofona"
         "\\lp2\\greatprogrammingjourney\\GameManager.java",
         "src/main/java/pt/ulusofona/lp2/greatprogrammingjourney/GameManager.java"),
        ("/Users/joana/IdeaProjects/lp2-projeto/src/main/java/pt/ulusofona/lp2/"
         "greatprogrammingjourney/GameManager.java",
         "src/main/java/pt/ulusofona/lp2/greatprogrammingjourney/GameManager.java"),
        # Android module in a CI workspace
        ("/home/runner/work/app/app/app/src/main/java/com/example/app/ui/MainActivity.kt",
         "app/src/main/java/com/example/app/ui/MainActivity.kt"),
        # multi-module layouts: repo root 2-3 levels above src
        ("/Users/aluno/AndroidStudioProjects/nowinandroid/feature/foryou/src/main/kotlin/"
         "com/google/samples/apps/nowinandroid/feature/foryou/ForYouScreen.kt",
         "feature/foryou/src/main/kotlin/com/google/samples/apps/nowinandroid/feature/"
         "foryou/ForYouScreen.kt"),
        ("\\Users\\aluno\\IdeaProjects\\repo\\modules\\core\\src\\main\\java"
         "\\pt\\x\\A.java",
         "modules/core/src/main/java/pt/x/A.java"),
        ("\\Users\\aluno\\AndroidStudioProjects\\nowinandroid\\feature\\foryou\\impl"
         "\\src\\main\\kotlin\\com\\google\\nia\\ForYouScreen.kt",
         "feature/foryou/impl/src/main/kotlin/com/google/nia/ForYouScreen.kt"),
        ("C:/src/tfc/modules/core/src/main/java/pt/x/A.java",       # extra 'src' in the prefix
         "modules/core/src/main/java/pt/x/A.java"),
    ])
    def test_deep_repo_paths_from_ides_and_ci_are_tried(self, pasted, repo_path):
        out = server._path_candidates(pasted)
        assert len(out) <= 8 and len(out) == len(set(out)) and repo_path in out

    @pytest.mark.parametrize("pasted, repo_path", [
        ("C:\\Users\\joao\\src\\tfc\\backend\\app\\api\\routes\\users.py",
         "backend/app/api/routes/users.py"),
        ("/Users/ana/work/src/tfc/backend/app/api/routes/users.py",
         "backend/app/api/routes/users.py"),
        ("/home/u/projects/src/tfc/backend/app/api/routes/users.py",
         "backend/app/api/routes/users.py"),
        ("/home/u/go/src/github.com/org/repo/pkg/server/handler/x/y.go",
         "pkg/server/handler/x/y.go"),
        ("/home/u/catkin_ws/src/robot_nav/scripts/planning/global/astar/planner.py",
         "scripts/planning/global/astar/planner.py"),
        ("/mnt/c/Users/joao/src/tfc/backend/app/api/routes/users.py",
         "backend/app/api/routes/users.py"),
    ])
    def test_a_src_dir_outside_the_repo_does_not_crowd_out_short_suffixes(self, pasted, repo_path):
        out = server._path_candidates(pasted)
        assert repo_path in out and len(out) <= 8 and len(out) == len(set(out))

    def test_huge_input_is_cheap_and_bounded(self):
        import time
        huge = "a/" * 500_000 + "f.py"               # ~1 MB "path"
        started = time.monotonic()
        out = server._path_candidates(huge)
        assert time.monotonic() - started < 0.5      # linear, not quadratic
        assert len(out) <= 8 and all(len(c) <= 1024 for c in out)


# ── _blame_owner: which commit owns the target line ───────────────────────────

BLAME = [
    {"commit": {"id": "aaa111", "author_name": "Ana"}, "lines": ["x=1", "y=2"]},
    {"commit": {"id": "bbb222", "author_name": "Rui"}, "lines": ["z=3"]},
]


class TestBlameOwner:
    def test_first_entry_lines(self):
        c, txt = server._blame_owner(BLAME, 10, 10)
        assert c["id"] == "aaa111" and txt == "x=1"
        c, txt = server._blame_owner(BLAME, 10, 11)
        assert c["id"] == "aaa111" and txt == "y=2"

    def test_second_entry_line(self):
        c, txt = server._blame_owner(BLAME, 10, 12)
        assert c["id"] == "bbb222" and txt == "z=3"

    def test_line_beyond_range_returns_none(self):
        c, txt = server._blame_owner(BLAME, 10, 13)
        assert c is None and txt == ""

    def test_empty_blame(self):
        assert server._blame_owner([], 1, 1) == (None, "")
        assert server._blame_owner(None, 1, 1) == (None, "")


# ── _blame_snippet: numbered excerpt for the LLM ──────────────────────────────

class TestBlameSnippet:
    def test_marks_target_line(self):
        snip = server._blame_snippet(BLAME, 10, 11)
        lines = snip.splitlines()
        assert lines[1].startswith(">>")
        assert lines[0].startswith("  ") and lines[2].startswith("  ")

    def test_numbers_and_sha_tags(self):
        snip = server._blame_snippet(BLAME, 10, 11)
        assert "10" in snip and "12" in snip
        assert "[aaa111" in snip and "[bbb222" in snip

    def test_truncates_long_lines(self):
        blame = [{"commit": {"id": "c"}, "lines": ["A" * 500]}]
        snip = server._blame_snippet(blame, 1, 1, width=100)
        assert len(snip.splitlines()[0]) < 150

    def test_empty(self):
        assert server._blame_snippet([], 1, 1) == ""


# ── _diff_excerpt: pick the right file's diff, truncated ──────────────────────

DIFFS = [
    {"old_path": "other.py", "new_path": "other.py", "diff": "@@ other @@"},
    {"old_path": "src/app.js", "new_path": "src/app.js", "diff": "@@ certo @@"},
]


class TestDiffExcerpt:
    def test_picks_matching_file(self):
        assert server._diff_excerpt(DIFFS, "src/app.js") == "@@ certo @@"

    def test_matches_old_path_on_rename(self):
        diffs = [{"old_path": "velho.py", "new_path": "novo.py", "diff": "@@ r @@"}]
        assert server._diff_excerpt(diffs, "velho.py") == "@@ r @@"

    def test_never_uses_an_unrelated_files_diff(self):
        # antes caía no 1.º ficheiro do commit e a IA "explicava" o diff errado
        assert server._diff_excerpt(DIFFS, "inexistente.py") == ""

    def test_single_file_commit_is_used_and_labelled(self):
        diffs = [{"old_path": "antigo/nome.py", "new_path": "antigo/nome.py", "diff": "@@ x @@"}]
        out = server._diff_excerpt(diffs, "novo/nome.py")
        assert out.endswith("@@ x @@") and "antigo/nome.py" in out

    def test_truncates_giant_diff(self):
        diffs = [{"new_path": "a.py", "old_path": "a.py", "diff": "x" * 10000}]
        out = server._diff_excerpt(diffs, "a.py", max_chars=100)
        assert len(out) < 200 and "diff truncado" in out

    def test_empty(self):
        assert server._diff_excerpt([], "a.py") == ""
        assert server._diff_excerpt(None, "a.py") == ""
