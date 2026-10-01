"""Tests for _issue_stats — the shared aggregation behind both the LLM context
and the /gitlab/stats panel. `today` is injected so 'overdue' is deterministic."""

from datetime import date

import server

TODAY = date(2026, 6, 11)


def _issues():
    return [
        {"state": "closed", "iid": 1, "title": "a"},
        {"state": "closed", "iid": 2, "title": "b"},
        # opened + due in the past + has assignee -> overdue, not no_assignee
        {"state": "opened", "iid": 3, "title": "c",
         "due_date": "2026-06-01", "assignee": {"name": "X"}},
        # opened + due in the future + has assignee -> neither
        {"state": "opened", "iid": 4, "title": "d",
         "due_date": "2026-12-01", "assignee": {"name": "Y"}},
        # opened + no due + no assignee -> no_assignee
        {"state": "opened", "iid": 5, "title": "e"},
    ]


class TestIssueStats:
    def test_open_closed_counts(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert len(s["opened"]) == 3
        assert len(s["closed"]) == 2

    def test_progress_is_closed_over_total(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert s["progress"] == 40.0  # 2 / 5

    def test_overdue_uses_injected_today(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert [i["iid"] for i in s["overdue"]] == [3]

    def test_no_assignee_only_counts_open(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert s["no_assignee"] == 1

    def test_malformed_due_date_is_skipped(self):
        issues = _issues() + [{"state": "opened", "iid": 6, "due_date": "garbage"}]
        s = server._issue_stats(issues, today=TODAY)  # must not raise
        assert all(i["iid"] != 6 for i in s["overdue"])

    def test_empty_list(self):
        s = server._issue_stats([], today=TODAY)
        assert s["progress"] == 0
        assert s["opened"] == [] and s["closed"] == [] and s["overdue"] == []
        assert s["no_assignee"] == 0

    def test_complete_list_is_exact(self):
        s = server._issue_stats(_issues(), today=TODAY)
        assert s["exact"] is True and s["total"] == 5 and s["open_count"] == 3


class TestTruncatedIssueStats:
    """A list cut by GITLAB_PAGE_LIMIT must never be presented as exact."""

    def _truncated(self):
        from src.gitlab_api import PagedList
        rows = PagedList(_issues())
        rows.truncated = True
        return rows

    def test_flagged_as_lower_bound_without_counts(self):
        s = server._issue_stats(self._truncated(), today=TODAY)
        assert s["exact"] is False and s["truncated"] is True

    def test_exact_counts_override_list_lengths(self):
        s = server._issue_stats(self._truncated(), today=TODAY,
                                counts={"all": 750, "opened": 300, "closed": 450})
        assert (s["total"], s["open_count"], s["closed_count"]) == (750, 300, 450)
        assert s["progress"] == 60.0 and s["exact"] is True

    def test_separate_open_list_feeds_overdue(self):
        opened = [{"state": "opened", "iid": 900, "due_date": "2026-01-01"}]
        s = server._issue_stats(self._truncated(), today=TODAY, opened=opened)
        assert [i["iid"] for i in s["overdue"]] == [900] and s["open_complete"] is True
