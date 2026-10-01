"""
Construtores de dados Chart.js (gráficos inline no chat) + registo
CHART_HANDLERS usado pela rota /gitlab/chart/<nome>.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, timedelta

from src.gitlab_api import (_get_commits, _get_contributors,
                            _get_merge_requests, contributors_capped,
                            get_all_issues, get_issue_counts, get_project_info,
                            get_recently_closed_issues, merge_contributors)

log = logging.getLogger("sprintlab")


def _plus(rows):
    """'+' when a list was cut by the page limit (the count is a minimum)."""
    return "+" if getattr(rows, "truncated", False) else ""


# ── Chart data builders (Chart.js-compatible JSON) ────────────────────────────

_PALETTE = [
    "#8B88F8", "#4caf50", "#ff9800", "#f44336", "#03a9f4",
    "#e91e63", "#ffeb3b", "#9c27b0", "#00bcd4", "#cddc39",
    "#795548", "#607d8b",
]


def _colors(n):
    return [_PALETTE[i % len(_PALETTE)] for i in range(n)]


def _no_data_chart(title):
    return {
        "type": "doughnut",
        "title": title,
        "labels": ["Sem dados"],
        "datasets": [{"data": [1], "backgroundColor": ["#444"]}],
    }

def chart_state_pie(**_):
    issues = get_all_issues("all")
    if not issues:
        return _no_data_chart("Issues por estado")
    counts = get_issue_counts() if getattr(issues, "truncated", False) else None
    if counts:   # the list was cut: use GitLab's exact counts (same as the panel)
        opened, closed, total, plus = counts["opened"], counts["closed"], counts["all"], ""
    else:
        opened = sum(1 for i in issues if i.get("state") == "opened")
        closed = sum(1 for i in issues if i.get("state") == "closed")
        total, plus = len(issues), _plus(issues)
    return {
        "type": "doughnut",
        "title": f"Issues por estado (total {total}{plus})",
        "labels": ["Abertas", "Fechadas"],
        "datasets": [{
            "data": [opened, closed],
            "backgroundColor": ["#ff9800", "#4caf50"],
        }],
    }


def chart_by_assignee(**_):
    opened = [i for i in get_all_issues("all") if i.get("state") == "opened"]
    if not opened:
        return _no_data_chart("Issues abertas por assignee")
    counter = Counter()
    for i in opened:
        name = i["assignee"]["name"] if i.get("assignee") else "Sem assignee"
        counter[name] += 1
    items = counter.most_common()
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues abertas por assignee",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_by_label(**_):
    issues = get_all_issues("all")
    counter = Counter()
    for i in issues:
        for lab in (i.get("labels") or []):
            counter[lab] += 1
    if not counter:
        return _no_data_chart("Issues por label")
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues por label (top 10)",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_by_milestone(**_):
    issues = get_all_issues("all")
    if not issues:
        return _no_data_chart("Issues por milestone")
    counter = Counter()
    for i in issues:
        ms = (i.get("milestone") or {}).get("title") or "Sem milestone"
        counter[ms] += 1
    items = counter.most_common()
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": "Issues por milestone",
        "labels": labels,
        "datasets": [{
            "label": "Issues",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_burndown(days=14, **_):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(days, 90))
    today = date.today()
    start = today - timedelta(days=days - 1)
    # Issues closed in the window were necessarily UPDATED in it: ask GitLab for
    # exactly those (a capped "all closed issues" list, newest-created first,
    # silently missed old issues closed recently).
    rows = get_recently_closed_issues(since=start)
    closed = [i for i in rows if i.get("closed_at")]
    bucket = {start + timedelta(days=i): 0 for i in range(days)}
    for i in closed:
        try:
            d = date.fromisoformat(i["closed_at"][:10])
            if d in bucket:
                bucket[d] += 1
        except (ValueError, KeyError):
            continue
    labels = [d.strftime("%d/%m") for d in bucket]
    data = [bucket[d] for d in bucket]
    total = sum(data)
    return {
        "type": "line",
        "title": f"Burndown — {total}{_plus(rows)} issues fechadas nos últimos {days} dias",
        "labels": labels,
        "datasets": [{
            "label": "Fechadas/dia",
            "data": data,
            "borderColor": "#8B88F8",
            "backgroundColor": "rgba(139,136,248,0.2)",
            "fill": True,
            "tension": 0.3,
        }],
    }


def chart_cycle_time(**_):
    # Most recently closed first, so the sample reflects the CURRENT pace even
    # when the page limit caps how many closed issues are read.
    rows = get_recently_closed_issues()
    closed = [i for i in rows if i.get("closed_at") and i.get("created_at")]
    if not closed:
        return _no_data_chart("Tempo de ciclo")
    buckets = {"0-1d": 0, "2-3d": 0, "4-7d": 0, "8-14d": 0, "15-30d": 0, ">30d": 0}
    total_days = 0
    n = 0
    for i in closed:
        try:
            opened_at = date.fromisoformat(i["created_at"][:10])
            closed_at = date.fromisoformat(i["closed_at"][:10])
            d = (closed_at - opened_at).days
        except (ValueError, KeyError):
            continue
        total_days += d
        n += 1
        if d <= 1:    buckets["0-1d"] += 1
        elif d <= 3:  buckets["2-3d"] += 1
        elif d <= 7:  buckets["4-7d"] += 1
        elif d <= 14: buckets["8-14d"] += 1
        elif d <= 30: buckets["15-30d"] += 1
        else:         buckets[">30d"] += 1
    avg = round(total_days / n, 1) if n else 0
    sample = " — últimas fechadas" if getattr(rows, "truncated", False) else ""
    return {
        "type": "bar",
        "title": f"Tempo de ciclo — média {avg} dias (n={n}{sample})",
        "labels": list(buckets.keys()),
        "datasets": [{
            "label": "Issues",
            "data": list(buckets.values()),
            "backgroundColor": _colors(len(buckets)),
        }],
    }


def chart_contributors_mr(**_):
    try:
        mrs = _get_merge_requests()
    except Exception as e:
        log.warning("MR fetch failed: %s", e)
        return _no_data_chart("Top MRs por autor")
    if not mrs:
        return _no_data_chart("Top MRs por autor")
    counter = Counter()
    for mr in mrs:
        name = (mr.get("author") or {}).get("name") or "Desconhecido"
        counter[name] += 1
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": f"Top MRs por autor (total {len(mrs)}{_plus(mrs)})",
        "labels": labels,
        "datasets": [{
            "label": "Merge Requests",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


def chart_contributors_all(**_):
    """Top contributors of ALL TIME (GitLab contributors API) — the default for
    'top commits', so dormant/mirrored repos show their real history."""
    try:
        contribs = _get_contributors()
    except Exception as e:
        log.warning("contributors fetch failed: %s", e)
        return _no_data_chart("Top contribuidores (commits)")
    if not contribs:
        return _no_data_chart("Top contribuidores (commits)")
    capped = contributors_capped(contribs)
    more = _plus(contribs)
    raw_sum = sum(int(c.get("commits") or 0) for c in contribs if isinstance(c, dict))
    contribs = merge_contributors(contribs)   # same person, several e-mails → one bar
    top = contribs[:10]
    # Total CANÓNICO do repositório (statistics.commit_count) — o MESMO número que
    # o relatório e o painel mostram. A soma por autor da API de contributors
    # exclui merge commits (ex.: 2000 vs 2318), o que contradizia o relatório.
    try:
        info = get_project_info()
        commit_count = (info.get("statistics") or {}).get("commit_count")
    except Exception as e:
        log.warning("project info for chart failed: %s", e)
        commit_count = None
    total = commit_count if commit_count is not None else raw_sum
    scope = " · barras: só os commits mais recentes (limite do GitLab)" if capped else ""
    return {
        "type": "bar",
        "title": (f"Top contribuidores — {total} commits no repo · "
                  f"{len(contribs)}{more} autores{scope}"),
        "labels": [c["name"] for c in top],
        "datasets": [{
            "label": "Commits",
            "data": [c["commits"] for c in top],
            "backgroundColor": _colors(len(top)),
        }],
    }


def chart_contributors_commits(days=90, **_):
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 90
    days = max(1, min(days, 365))
    try:
        commits = _get_commits(days)
    except Exception as e:
        log.warning("commits fetch failed: %s", e)
        return _no_data_chart("Top commits por autor")
    if not commits:
        return _no_data_chart(f"Top commits — últimos {days} dias (0 commits)")
    counter = Counter()
    for c in commits:
        name = c.get("author_name") or "Desconhecido"
        counter[name] += 1
    items = counter.most_common(10)
    labels = [k for k, _ in items]
    data = [v for _, v in items]
    return {
        "type": "bar",
        "title": (f"Top commits por autor — últimos {days} dias "
                  f"(total {len(commits)}{_plus(commits)})"),
        "labels": labels,
        "datasets": [{
            "label": "Commits",
            "data": data,
            "backgroundColor": _colors(len(labels)),
        }],
    }


CHART_HANDLERS = {
    "state-pie": chart_state_pie,
    "by-assignee": chart_by_assignee,
    "by-label": chart_by_label,
    "by-milestone": chart_by_milestone,
    "burndown": chart_burndown,
    "cycle-time": chart_cycle_time,
    "contributors-mr": chart_contributors_mr,
    "contributors-commits": chart_contributors_commits,
    "contributors-all": chart_contributors_all,
}
