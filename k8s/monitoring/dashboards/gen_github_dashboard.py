"""Lab · Terraform CI/CD 대시보드 생성기 (Atlantis 실시간 지표 + GitHub 이력).
사용: python gen_github_dashboard.py lab-github.json
- 실시간: Atlantis /metrics (ServiceMonitor, atlantis_* 만 keep, 실행 카운터는 수집 시 _total 부여) + KSM kube_statefulset_replicas
  Atlantis 는 평소 꺼져 있어(replicas 0) 켜 둔 동안의 이벤트만 쌓인다. Prometheus 보관 7일
- 이력: Grafana GitHub 데이터소스(uid github, 읽기 전용 PAT) — PR·Actions 실행·이슈. Atlantis 를 꺼 둬도 영구 기록
  필드(플러그인 2.10): PR number,title,repository,author_login,created_at,merged_at,open_time(초),labels …
                     실행 name,conclusion,event,head_branch,created_at,html_url / 이슈 title,repo,number,state …
  작성자 이메일·회사 필드는 표시하지 않는다(개인정보)
"""
import sys
from _dashlib import (PROM, Dashboard, base, prom, range_map, stat, state_timeline, table, timeseries, value_map, write)

GH = {"type": "grafana-github-datasource", "uid": "github"}
OWNER = "windevase"
REPOS = ["infra-terraform", "gcp"]
# 저장소별 핵심 워크플로 (파일 이름)
WORKFLOWS = {
    "infra-terraform": ["code-guard.yml", "apply-on-approve.yml", "policy-audit.yml"],
    "gcp": ["ci.yml", "apply-on-approve.yml", "policy-audit.yml"],
}
PR_HIDE = ["url", "additions", "deletions", "state", "author_name", "author_email", "author_company", "closed",
           "is_draft", "locked", "merged", "mergeable", "closed_at", "merged_by_name", "merged_by_login",
           "merged_by_email", "merged_by_company", "updated_at"]
ATL = 'namespace="atlantis"'

_ref = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def gh(query_type, repo, options=None):
    return {"datasource": GH, "refId": next(_ref), "queryType": query_type, "owner": OWNER, "repository": repo,
            "options": options or {}}


def prs(query, time_field):
    """두 저장소의 PR. time_field: 0 closed / 1 created / 2 merged / 3 updated / 4 none(기간 무관)."""
    return [gh("Pull_Requests", r, {"query": query, "timeField": time_field}) for r in REPOS]


def gh_count(title, targets, x, y, w=4, h=4, field="number", calc="count", unit="short", steps=None, desc="",
             decimals=0):
    """GitHub 결과 행을 하나로 합쳐(merge) field 의 count/mean 등을 표시."""
    p = stat(GH, title, targets, x, y, w=w, h=h, unit=unit, steps=steps, desc=desc, decimals=decimals, graph=False)
    p["options"]["reduceOptions"] = {"calcs": [calc], "fields": f"/^{field}$/", "values": False}
    p["transformations"] = [{"id": "merge", "options": {}}]
    return p


d = Dashboard()

# ---------------- 1. 요약 ----------------
d.row("요약 (선택 기간)")
d.add(gh_count("merge 된 PR", prs("is:merged", 2), 0, d.y,
               desc="두 저장소(infra-terraform·gcp)에서 선택 기간에 merge 된 PR 수 — 요청 처리량"))
d.add(gh_count("평균 리드타임", prs("is:merged", 2), 4, d.y, field="open_time", calc="mean", unit="s", decimals=None,
               steps=[("green", None), ("orange", 86400), ("red", 259200)],
               desc="PR 생성 → merge 까지 평균(채팅 보고·승인·apply 포함). 1일 넘으면 주황, 3일 넘으면 빨강"))
d.add(gh_count("열린 PR", prs("is:open", 4), 8, d.y, steps=[("green", None), ("orange", 3)],
               desc="지금 열려 있는 PR(기간 무관). 오래 열려 있으면 apply 실패·승인 대기 확인"))
d.add(gh_count("정책 위반 이슈 (열림)",
               [gh("Issues", r, {"query": "label:policy-violation is:open", "timeField": 0}) for r in REPOS],
               12, d.y, steps=[("green", None), ("red", 1)],
               desc="policy-audit(사후·교차 감사)가 만든 policy-violation 이슈 중 열린 것. 0 이어야 정상"))
d.add(gh_count("감시 해제 라벨 PR", prs("is:merged label:allow-destroy,allow-force-destroy,allow-open-ingress,"
                                        "allow-iam-wildcard,allow-admin-policy,allow-entry-removal", 2),
               16, d.y, steps=[("green", None), ("orange", 1)],
               desc="감시 차단을 해제 라벨(allow-*)로 풀고 merge 된 PR 수 — 삭제·공개 인바운드 등 위험 변경"))
d.add(stat(PROM, "Atlantis", [prom(f'max(kube_statefulset_replicas{{{ATL}, statefulset="atlantis"}})')],
           20, d.y, w=4, h=4, graph=False, decimals=0,
           mappings=range_map([(0, 0, "꺼짐", "#808080"), (1, 100, "켜짐", "green")]),
           desc="평소 꺼 둠(replicas 0). 처리할 때만 C:\\gcp\\tools\\atlantis.ps1 on, 매일 20:00 자동 종료"))
d.y += 4

# ---------------- 1-2. 사후 감사 · Atlantis 가동 ----------------
# 감사 결과는 Infinity(GitHub REST)로 이벤트별 마지막 실행만 — push = merge 직후 사후 감사, schedule = 매일 09:17 교차 감사
INF = {"type": "yesoreyeram-infinity-datasource", "uid": "github-rest"}
AUDIT_MAP = value_map({"success": ("성공", "green"), "failure": ("실패", "red"), "cancelled": ("취소", "orange"),
                       "": ("진행 중", "blue")})


def audit_stat(title, repo, event, x, desc):
    q = {"datasource": INF, "refId": "A", "type": "json", "source": "url", "parser": "backend", "format": "table",
         "url": f"https://api.github.com/repos/{OWNER}/{repo}/actions/workflows/policy-audit.yml/runs"
                + ("?event=push&per_page=1" if event == "push" else "?per_page=20"),
         "url_options": {"method": "GET", "data": ""}, "root_selector": "workflow_runs",
         "columns": [{"selector": "conclusion", "text": "결과", "type": "string"},
                     {"selector": "created_at", "text": "시각", "type": "timestamp"},
                     {"selector": "event", "text": "이벤트", "type": "string"}]}
    if event != "push":  # 교차 감사 = push 가 아닌 실행(매일 schedule + 수동 workflow_dispatch) 중 최신
        q["filterExpression"] = "이벤트 != 'push'"
    p = stat(INF, title, [q], x, d.y, w=4, h=4, graph=False, mappings=AUDIT_MAP, desc=desc)
    p["options"]["reduceOptions"] = {"calcs": ["firstNotNull"], "fields": "/^(결과|시각)$/", "values": False}
    p["options"]["textMode"] = "value"
    p["fieldConfig"]["overrides"] = [{"matcher": {"id": "byName", "options": "시각"},
                                      "properties": [{"id": "unit", "value": "dateTimeFromNow"},
                                                     {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}]}]
    return p


d.row("사후 감사 · Atlantis 가동")
d.add(audit_stat("사후 감사 — infra-terraform", "infra-terraform", "push", 0,
                 "main merge 직후 감사(보호 경로 변경의 채팅 승인 기록·force-push). 실패면 policy-violation 이슈 확인"))
d.add(audit_stat("사후 감사 — gcp", "gcp", "push", 4, "main merge 직후 감사"))
d.add(audit_stat("교차 감사 — infra-terraform", "infra-terraform", "schedule", 8,
                 "매일 09:17 KST 상대 저장소(gcp) 감사 — 감사 파일 동일·승인 기록·직접 push. 하루 넘게 지났으면 실행 누락"))
d.add(audit_stat("교차 감사 — gcp", "gcp", "schedule", 12, "매일 09:17 KST 상대 저장소(infra-terraform) 감사"))
# Atlantis 가동 시간·비용 (최근 7일, 5분 해상도) — 켜 둔 채 잊었는지
UP = 'max(kube_statefulset_replicas{namespace="atlantis", statefulset="atlantis"})'
UP_H = f'(sum_over_time(({UP} > bool 0)[7d:5m]) * 5 / 60) or vector(0)'
p = stat(PROM, "Atlantis 가동 — 최근 7일", [prom(UP_H)], 16, d.y, w=4, h=4, unit="h", decimals=1, graph=False,
         steps=[("green", None), ("orange", 40), ("red", 80)],
         desc="평소 꺼 둠(필요할 때 켜고 20:00 자동 종료). 40시간 넘으면 주황 — 켜 둔 채 잊었는지 확인")
d.add(p)
# 비 Spot 250m/1Gi (Autopilot 일반 파드: vCPU $0.0445/h, 메모리 $0.0049225/GiB-h) ≈ $0.0160/h + PVC 2Gi 는 상시(월 약 $0.08, 별도)
p = stat(PROM, "Atlantis 비용 — 최근 7일(대략)", [prom(f'({UP_H}) * (0.25 * 0.0445 + 1 * 0.0049225)')], 20, d.y,
         w=4, h=4, unit="currencyUSD", decimals=2, graph=False,
         desc="가동 시간 × 파드 requests 단가(비 Spot 250m/1Gi ≈ $0.016/h). PVC 2Gi(월 약 $0.08)는 제외")
d.add(p)
d.y += 4

# ---------------- 2. Atlantis 실시간 ----------------
d.row("Atlantis 실시간 (최근 24시간 — 켜 둔 동안만, Prometheus)")
RT_Y = d.y - 1
d.add(state_timeline(PROM, "가동 구간", [
    prom(f'max(kube_statefulset_replicas{{{ATL}, statefulset="atlantis"}})', "atlantis")], 0, d.y, w=24, h=4,
    mappings=range_map([(0, 0, "꺼짐", "#808080"), (1, 100, "켜짐", "green")]),
    desc="atlantis.ps1 on/off, 20:00 오프아워 종료"))
d.y += 4
# increase() 는 지표 이름을 지우므로 이름 정규식으로 묶으면 시계열이 겹친다(422) → 지표별 쿼리
d.add(timeseries(PROM, "프로젝트 실행 결과 (5분)", [
    prom(f'sum(increase(atlantis_project_execution_{r}_total[5m]))', r, ref)
    for r, ref in (("success", "A"), ("error", "B"), ("failure", "C"))], 0, d.y, w=12, h=8, bars=True, stack=True, minv=0, decimals=0,
    desc="plan·apply 프로젝트 실행. success / error(실행 오류) / failure(plan·apply 실패·감시 차단)",
    overrides=[{"matcher": {"id": "byRegexp", "options": ".*error.*|.*failure.*"},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}]))
d.add(timeseries(PROM, "명령별 실행 (5분)", [
    # 명령·결과를 라벨로 옮긴 뒤(label_replace) 서브쿼리로 increase — 이름이 지워져도 시계열이 구분됨
    prom('sum by (cmd, result) (increase(label_replace(label_replace('
         '{__name__=~"atlantis_cmd_.+_execution_(success|error|failure)_total"}, '
         '"cmd", "$1", "__name__", "atlantis_cmd_(.+)_execution_.*"), '
         '"result", "$1", "__name__", ".*_execution_(.+)_total")[5m:1m]))',
         "{{cmd}} {{result}}")], 12, d.y, w=12, h=8, bars=True, stack=True, minv=0, decimals=0,
    desc="autoplan·comment plan·comment apply 등 명령 단위. 첫 실행 뒤에 지표가 생긴다"))
d.y += 8
d.add(stat(PROM, "실행 성공 (24시간)", [prom('sum(increase(atlantis_project_execution_success_total[$__range])) or vector(0)')],
           0, d.y, w=6, h=6, graph=False, decimals=0))
d.add(stat(PROM, "실행 실패·오류 (24시간)", [prom(
    '(sum(increase(atlantis_project_execution_error_total[$__range])) or vector(0)) + '
    '(sum(increase(atlantis_project_execution_failure_total[$__range])) or vector(0))')],
    6, d.y, w=6, h=6, graph=False, decimals=0, steps=[("green", None), ("red", 1)]))
d.add(timeseries(PROM, "Atlantis 런타임", [
    prom('max(atlantis_scheduled_runtime_memory_heap_alloc)', "heap"),
    prom('max(atlantis_scheduled_runtime_cpu_goroutines)', "goroutines", "B")], 12, d.y, w=12, h=6, minv=0,
    overrides=[{"matcher": {"id": "byName", "options": "heap"}, "properties": [{"id": "unit", "value": "bytes"}]}]))
d.y += 6
for p in d.panels:
    if p.get("gridPos", {}).get("y", 0) > RT_Y and p["type"] != "row" and p.get("datasource") is PROM:
        p["timeFrom"] = "24h"  # 패널만 최근 24시간 (대시보드 기본 30일은 GitHub 이력용)

# ---------------- 3. GitHub 이력 ----------------
# 표마다 쿼리 하나 — 여러 결과를 합치는 merge·groupBy 변환은 쓰지 않는다
# (2026-11-14: 그 변환을 쓴 패널을 브라우저에서 열 때 탭이 멈춤 — 원인 미확정, 단순 구조로 회피)
d.row("GitHub 이력 (영구 기록 — Atlantis 를 꺼 둬도 남음)")
PR_RENAME = {"number": "#", "title": "제목", "author_login": "작성자", "created_at": "생성", "merged_at": "merge",
             "open_time": "리드타임"}
for k, r in enumerate(REPOS):
    d.add(table(GH, f"merge 된 PR — {r}", [gh("Pull_Requests", r, {"query": "is:merged", "timeField": 2})],
                12 * k, d.y, 12, 9, merge=False, exclude=PR_HIDE + ["repository", "labels"], renames=PR_RENAME,
                units={"리드타임": "s"}, sort=[{"displayName": "merge", "desc": True}],
                desc="채팅 승인 후 merge 된 PR(봇 PR = 요청 처리, 본인 PR = 정책 변경). 리드타임 = 생성 → merge"))
d.y += 9
CONCLUSION = {"success": ("성공", "green"), "failure": ("실패", "red"), "cancelled": ("취소", "#808080"),
              "skipped": ("건너뜀", "#808080")}
conc_map = [{"type": "value", "options": {k: {"text": t, "color": c, "index": i}
                                          for i, (k, (t, c)) in enumerate(CONCLUSION.items())}}]
RUN_HIDE = ["id", "name", "head_sha", "updated_at", "run_started_at", "url", "workflow_id", "run_number", "status"]
RUN_RENAME = {"head_branch": "브랜치", "conclusion": "결과", "event": "이벤트", "created_at": "시작", "html_url": "링크"}
RUN_DESC = {"code-guard.yml": "PR 코드 감시(+회귀 테스트·shellcheck)", "ci.yml": "gcp PR 검사(사본 동기화 포함)",
            "apply-on-approve.yml": "채팅 승인(Approve) → atlantis apply 요청",
            "policy-audit.yml": "main push 사후 감사 + 매일 교차 감사"}
for r, ws in WORKFLOWS.items():
    for k, w in enumerate(ws):
        d.add(table(GH, f"{w.removesuffix('.yml')} — {r}", [gh("Workflow_Runs", r, {"workflow": w})],
                    8 * k, d.y, 8, 7, merge=False, exclude=RUN_HIDE, renames=RUN_RENAME,
                    mappings={"결과": conc_map}, sort=[{"displayName": "시작", "desc": True}],
                    desc=RUN_DESC.get(w, "") + " — 최근 실행"))
    d.y += 7
ISSUE_HIDE = ["author_company", "closed", "updated_at", "labels", "assignees", "milestone", "repo"]
ISSUE_RENAME = {"number": "#", "title": "제목", "author": "작성", "state": "상태", "created_at": "생성", "closed_at": "닫힘"}
for k, r in enumerate(REPOS):
    d.add(table(GH, f"정책 위반 이슈 — {r}", [gh("Issues", r, {"query": "label:policy-violation", "timeField": 0})],
                12 * k, d.y, 12, 5, merge=False, exclude=ISSUE_HIDE, renames=ISSUE_RENAME,
                desc="승인 기록 없는 보호 경로 변경(policy-audit). 비어 있어야 정상"))
d.y += 5
SHOW_TIME = {"matcher": {"id": "byType", "options": "time"},
             "properties": [{"id": "custom.hidden", "value": False}, {"id": "unit", "value": "dateTimeAsLocal"}]}
for p in d.panels:
    if p["type"] == "table" and p.get("datasource") is GH:
        p["fieldConfig"]["overrides"].insert(0, SHOW_TIME)
        # _dashlib.table 의 기본 제외 "Time"(Prometheus 표용)이 time 형식 필드를 전부 걸러 냄 → GitHub 표에서는 뺀다
        # (2026-11-14 패널 검사: 원본엔 created_at·merged_at 있음, organize 적용 후 사라짐)
        for t in p["transformations"]:
            if t["id"] == "organize":
                t["options"]["excludeByName"].pop("Time", None)

dash = base("lab-github", "Lab · Terraform CI/CD",
            "Atlantis 실시간 지표(켜 둔 동안) + GitHub PR·Actions·정책 위반 이력 — infra-terraform·gcp",
            ["lab", "cicd", "atlantis", "github"], d.panels, time_from="now-30d",
            refresh="5m")  # GitHub API 요청 한도(PAT 5,000/시간) — 짧게 두지 않는다
write(dash, sys.argv[1])
