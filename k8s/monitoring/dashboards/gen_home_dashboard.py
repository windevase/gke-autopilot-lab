"""Lab · 홈 (요약) 대시보드 생성기 — 포트폴리오 첫 화면.
사용: python gen_home_dashboard.py lab-home.json
4개 대시보드의 핵심 숫자 + 바로가기. 상세 계산은 각 대시보드와 동일한 쿼리 사용
"""
import sys
from _dashlib import (BILL_CRED as CRED, BILL_ELAPSED as ELAPSED, BILL_FREE as FREE, BILL_MONTH as MONTH,
                      BILL_T as T, BQ, GITOPS_BAD, MEM_RATIO, MEM_STEPS, OOM_24H, OOM_DESC, PROM, bargauge, value_map, TUNNEL_5XX, TUNNEL_RPM, TUNNEL_5XX_DESC, Dashboard, base, bq, gauge, prom,
                      stat, text, write)

NS = 'namespace="monitoring"'
KRW = "currencyKRW"
d = Dashboard()

INTRO = """\
### GKE Autopilot 모니터링 랩
월 **$10 크레딧 안에서** 운영하는 GKE Autopilot(Spot Pod) + kube-prometheus-stack 실습 환경입니다.
Terraform(GCP·Cloudflare·AWS) · Helm · Cloudflare Tunnel · 예산 초과 자동 차단 · GitHub Actions CI · Argo CD · **Atlantis(채팅 승인 terraform CI/CD)**

| 대시보드 | 내용 |
|---|---|
| [💰 실제 청구액](/d/lab-billing) | BigQuery 결제 데이터: 사용액·크레딧·월말 예상, 추정 vs 실제 |
| [📊 비용 & 용량](/d/lab-cost-capacity) | 파드 requests 기반 비용 추정, rightsizing |
| [⚙️ 클러스터 성능](/d/lab-cluster-performance) | 노드·워크로드·PV·API 서버·Prometheus |
| [🛠️ 운영 & 외부 접속](/d/lab-ops) | 가동 타임라인, CronJob, Spot 노드 교체, Cloudflare Tunnel |
| [🚀 Argo CD (GitOps)](/d/lab-argocd) | 앱 Sync/Health, 동기화 이력·소요 시간, 컨트롤러 캐시, Git, 리소스 |
| [🏗️ Terraform CI/CD](/d/lab-github) | Atlantis 실시간(plan·apply·가동 시간) + PR·Actions·사후/교차 감사·정책 위반 이력 |
| [🐙 GitHub 사용량](/d/lab-github-usage) | GitHub Free 무료 한도 대비 Actions 분·저장 공간·청구 예정·API 한도 |
"""
d.add(text("", INTRO, 0, d.y, w=24, h=10))
d.y += 10

# ---------------- 비용 ----------------
d.row("비용 (이번 달)")
d.add(stat(BQ, "순 청구액", [bq(f"SELECT IFNULL(ROUND(SUM(cost) + SUM({CRED})), 0) AS value FROM {T} WHERE {MONTH}")],
           0, d.y, w=5, unit=KRW, decimals=0, graph=False, steps=[("green", None), ("orange", 1), ("red", 5000)],
           desc="크레딧 차감 후 실제 결제될 금액 (결제 데이터는 1~2일 지연)"))
d.add(gauge(BQ, "예산 대비 (₩$budget_krw)", [bq(
    f"SELECT IFNULL(SAFE_DIVIDE(SUM(cost) + SUM({FREE}), $budget_krw), 0) AS value FROM {T} WHERE {MONTH}")],
    5, d.y, w=5, steps=[("green", None), ("orange", 0.5), ("red", 0.9)],
    desc="100%면 budget-guard가 워크로드를 자동으로 0으로 내림"))
d.add(stat(BQ, "월말 예상 (할인 전, USD)", [bq(
    f"SELECT IFNULL(ROUND(SAFE_DIVIDE(SUM(cost / currency_conversion_rate), {ELAPSED}), 2), 0) AS value FROM {T} WHERE {MONTH}")],
    10, d.y, w=5, unit="currencyUSD", decimals=2, graph=False, steps=[("green", None), ("orange", 7), ("red", 10)],
    desc="크레딧 차감 전 전체 사용액의 월 환산. $10 크레딧과 비교"))
d.add(stat(PROM, "추정 월 비용 (USD)", [prom(
    "(avg_over_time(sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource=\"cpu\",namespace!~\"kube-system|gke-.*\"})"
    " * on(namespace,pod) group_left() max by (namespace,pod) (kube_pod_status_phase{phase=\"Running\"} == 1))[7d:10m]) * $cpu_rate"
    " + avg_over_time(sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource=\"memory\",namespace!~\"kube-system|gke-.*\"})"
    " * on(namespace,pod) group_left() max by (namespace,pod) (kube_pod_status_phase{phase=\"Running\"} == 1))[7d:10m]) / 2^30 * $mem_rate) * 730")],
    15, d.y, w=5, unit="currencyUSD", decimals=2, graph=False, steps=[("green", None), ("orange", 7), ("red", 10)],
    desc="파드 requests × Spot 단가, 최근 7일 평균 (비용 & 용량 대시보드와 동일)"))
d.add(stat(PROM, "수집 시계열", [prom('sum(scrape_samples_post_metric_relabeling)')], 20, d.y, w=4, decimals=0, graph=True,
           desc="keep 리스트로 대시보드가 쓰는 지표만 수집 (약 5.2만 → 4~5천)"))
d.y += 4

# ---------------- 클러스터 ----------------
d.row("클러스터")
d.add(stat(PROM, "노드", [prom('count(max by (node) (kube_node_info))')], 0, d.y, w=4, decimals=0,
           desc="Autopilot이 관리 (노드 자체는 과금 안 됨)"))
d.add(stat(PROM, "모니터링 파드 Running", [prom(
    f'count(max by (pod) (kube_pod_status_phase{{{NS},phase="Running"}}) == 1)')],
    4, d.y, w=4, decimals=0, desc="monitoring 네임스페이스 (CronJob 파드가 실행 중인 몇 초 동안은 +1)"))
# 24시간 창 집계는 추이선 없이 instant (range면 지점마다 24시간치를 다시 계산)
d.add(stat(PROM, "재시작 (24h)", [prom(f'sum(max by (pod, container) (increase(kube_pod_container_status_restarts_total{{{NS}}}[24h]))) or vector(0)')],
           8, d.y, w=4, decimals=0, graph=False, steps=[("green", None), ("red", 1)]))
d.add(stat(PROM, "OOM (24h)", [prom(OOM_24H.format(sel=', ' + NS))],
           12, d.y, w=4, decimals=0, graph=False, steps=[("green", None), ("red", 1)], desc=OOM_DESC))
d.add(stat(PROM, "수집 대상 정상", [prom('sum(up) / count(up)')], 16, d.y, w=4, unit="percentunit", decimals=0,
           steps=[("red", None), ("orange", 0.9), ("green", 1)]))
d.add(stat(PROM, "API 5xx 비율", [prom('(sum(lab:apiserver_request:rate5m{code=~"5.."}) / sum(lab:apiserver_request:rate5m)) or vector(0)')],
           20, d.y, w=4, unit="percentunit", decimals=2, steps=[("green", None), ("orange", 0.01), ("red", 0.05)]))
d.y += 4

# ---------------- 운영 · 외부 접속 ----------------
d.row("운영 · 외부 접속")
d.add(stat(PROM, "터널 연결", [prom('max(cloudflared_tunnel_ha_connections)')], 0, d.y, w=4, decimals=0,
           steps=[("red", None), ("orange", 1), ("green", 4)], desc="정상 4. 평일 20시 이후·주말엔 cloudflared가 꺼져 데이터 없음"))
d.add(stat(PROM, "요청 (분당)", [prom(TUNNEL_RPM)], 4, d.y, w=4, decimals=1))
d.add(stat(PROM, "서버 오류율 (5xx)", [prom(TUNNEL_5XX)],
           8, d.y, w=4, unit="percentunit", decimals=2, steps=[("green", None), ("orange", 0.01), ("red", 0.05)],
           desc=TUNNEL_5XX_DESC))
d.add(stat(PROM, "budget-guard 마지막 성공", [prom(f'time() - max(kube_cronjob_status_last_successful_time{{{NS},cronjob="budget-guard"}})')],
           12, d.y, w=4, unit="s", graph=False, steps=[("green", None), ("orange", 5400), ("red", 10800)],
           desc="예산 초과 감시 (매시간). 1.5시간 넘게 성공이 없으면 주황"))
d.add(stat(PROM, "자동 기동 CronJob", [prom(f'max(kube_cronjob_spec_suspend{{{NS},cronjob="grafana-scale-up"}})')],
           16, d.y, w=4, graph=False, steps=[("green", None), ("red", 1)],
           mappings=[{"type": "value", "options": {"0": {"text": "정상", "color": "green"}, "1": {"text": "차단됨", "color": "red"}}}],
           desc="'차단됨'이면 예산 100% 초과로 budget-guard가 자동 차단한 상태"))
d.add(stat(PROM, "기간 내 노드 교체", [prom('count(max by (node) (max_over_time(kube_node_info[$__range]))) - count(max by (node) (kube_node_info))', instant=True)],
           20, d.y, w=4, decimals=0, graph=False, desc="대시보드 기간 동안 사라진 노드 수 (Spot 회수·Autopilot 재배치)"))
d.y += 4

d.row("GitOps · 안정성")
d.add(stat(PROM, "GitOps (Argo CD)", [prom(GITOPS_BAD)], 0, d.y, w=4, h=6, decimals=0, graph=False,
           steps=[("green", None), ("red", 1)], mappings=value_map({"0": ("정상", "green")}),
           no_value="꺼짐 (오프아워)",
           desc="Git과 다르거나(OutOfSync) 비정상인 앱 수. Argo CD는 평일 10~20시만 가동. 자세히: Argo CD (GitOps) 대시보드"))
d.add(stat(PROM, "kill switch 자가 점검", [prom(f'time() - max(kube_cronjob_status_last_successful_time{{{NS},cronjob="budget-guard-selftest"}})')],
           4, d.y, w=4, h=6, unit="s", graph=False, steps=[("green", None), ("orange", 33 * 86400), ("red", 40 * 86400)],
           no_value="기록 없음",
           desc="마지막으로 성공한 월간 자가 점검(Pub/Sub 인증 + 차단 대상 RBAC dry-run)으로부터 경과 시간"))
d.add(stat(PROM, "메모리 사용률 최고", [prom('max(' + MEM_RATIO.format(ns="monitoring|argocd") + ')')],
           8, d.y, w=4, h=6, unit="percentunit", decimals=0, graph=False, steps=MEM_STEPS,
           desc="컨테이너 메모리 사용 / limit 중 최댓값. 90% 넘으면 OOMKilled 직전 (오른쪽 상위 5개 참고)"))
d.add(bargauge(PROM, "메모리 사용률 상위 5 (사용 / limit)",
               [prom('topk(5, ' + MEM_RATIO.format(ns="monitoring|argocd") + ')', "{{namespace}} · {{container}}")],
               12, d.y, w=12, h=6, steps=MEM_STEPS,
               desc="limit에 가까운 컨테이너. 지속적으로 주황·빨강이면 requests/limit 상향 검토"))
d.y += 6

templating = [
    {"name": "budget_krw", "type": "constant", "hide": 2, "query": "15000"},
    {"name": "cpu_rate", "type": "constant", "hide": 2, "query": "0.0133"},
    {"name": "mem_rate", "type": "constant", "hide": 2, "query": "0.0014767"},
]
dash = base("lab-home", "Lab · 홈", "GKE Autopilot 모니터링 랩 요약 — 비용·클러스터·운영 핵심 지표와 대시보드 바로가기",
            ["lab", "home"], d.panels, templating=templating, time_from="now-24h", refresh="5m")
dash["links"] = [{"title": "Lab 대시보드", "type": "dashboards", "tags": ["lab"], "asDropdown": True,
                  "includeVars": False, "keepTime": True, "icon": "external link", "targetBlank": False}]
write(dash, sys.argv[1])
