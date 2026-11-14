"""Lab · Argo CD (GitOps) 대시보드 생성기.
사용: python gen_argocd_dashboard.py lab-argocd.json
필요 지표(keep 목록, k8s/argocd/values.yaml):
  컨트롤러  argocd_app_info, argocd_app_sync_total, argocd_app_sync_duration_seconds_total,
            argocd_app_reconcile_*, argocd_app_k8s_request_total, argocd_cluster_*(api_resource_objects,
            api_resources, cache_age_seconds, events_total, connection_status)
  repo-server argocd_git_request_total, argocd_git_request_duration_seconds_*, argocd_repo_pending_request_total
  공통      cAdvisor·KSM (메모리·CPU·재시작·OOM)
Argo CD는 평일 10~20시만 가동 → 그 밖의 시간은 패널이 비는 것이 정상
"""
import sys
from _dashlib import (GITOPS_BAD, MEM_RATIO, MEM_STEPS, OOM_24H, OOM_DESC, PROM, Dashboard, base, prom,
                      stat, state_timeline, table, timeseries, value_map, write)

NS = 'namespace="argocd"'
OFF = "꺼짐 (오프아워)"
d = Dashboard()

# ---------------- 1. 개요 ----------------
d.row("개요 — Argo CD는 평일 10~20시만 가동")
d.add(stat(PROM, "관리 앱", [prom('count(max by (name) (argocd_app_info))')], 0, d.y, w=3, decimals=0, graph=False,
           no_value=OFF, desc="App of Apps(root) + 컴포넌트별 앱"))
d.add(stat(PROM, "Synced", [prom('count(max by (name) (argocd_app_info{sync_status="Synced"})) or vector(0)')],
           3, d.y, w=3, decimals=0, graph=False, desc="Git과 클러스터가 일치하는 앱 수"))
d.add(stat(PROM, "Healthy", [prom('count(max by (name) (argocd_app_info{health_status="Healthy"})) or vector(0)')],
           6, d.y, w=3, decimals=0, graph=False, desc="리소스 상태가 정상인 앱 수"))
d.add(stat(PROM, "비정상 앱", [prom(GITOPS_BAD)], 9, d.y, w=3, decimals=0, graph=False,
           steps=[("green", None), ("red", 1)], mappings=value_map({"0": ("없음", "green")}), no_value=OFF,
           desc="OutOfSync이거나 Healthy가 아닌 앱 수"))
d.add(stat(PROM, "동기화 (24h)", [prom('sum(increase(argocd_app_sync_total[24h])) or vector(0)')],
           12, d.y, w=4, decimals=0, graph=False, desc="Git push 후 자동 동기화 + 수동 동기화 횟수"))
d.add(stat(PROM, "동기화 실패 (24h)", [prom('sum(increase(argocd_app_sync_total{phase=~"Failed|Error"}[24h])) or vector(0)')],
           16, d.y, w=4, decimals=0, graph=False, steps=[("green", None), ("red", 1)]))
d.add(stat(PROM, "클러스터 연결", [prom('max(argocd_cluster_connection_status)')], 20, d.y, w=4, graph=False,
           steps=[("red", None), ("green", 1)], mappings=value_map({"1": ("정상", "green"), "0": ("끊김", "red")}),
           no_value=OFF, desc="in-cluster 연결 (감시 범위: argocd, monitoring 네임스페이스 + 클러스터 범위 리소스)"))
d.y += 4

# ---------------- 2. 앱 상태 ----------------
d.row("앱 상태")
d.add(table(PROM, "앱별 현재 상태", [prom('max by (name, sync_status, health_status) (argocd_app_info)', ref="A", instant=True, fmt="table")],
            0, d.y, 9, 9, renames={"name": "앱", "sync_status": "동기화", "health_status": "상태"}, exclude=["Value"],
            mappings={"동기화": value_map({"Synced": ("Synced", "green"), "OutOfSync": ("OutOfSync", "orange"), "Unknown": ("Unknown", "red")}),
                      "상태": value_map({"Healthy": ("Healthy", "green"), "Progressing": ("Progressing", "blue"),
                                        "Degraded": ("Degraded", "red"), "Missing": ("Missing", "orange"),
                                        "Suspended": ("Suspended", "gray"), "Unknown": ("Unknown", "red")})},
            sort=[{"displayName": "앱", "desc": False}]))
d.add(state_timeline(PROM, "앱별 정상 여부 (Synced + Healthy)", [
    prom('max by (name) (argocd_app_info{sync_status="Synced", health_status="Healthy"}) '
         'or (max by (name) (argocd_app_info) * 0)', "{{name}}")],
    9, d.y, w=15, h=9,
    mappings=value_map({"1": ("정상", "green"), "0": ("비정상", "red")}),
    desc="빈 구간 = Argo CD가 꺼진 오프아워 (상태 판단 안 함)"))
d.y += 9

# ---------------- 3. 동기화 ----------------
d.row("동기화")
d.add(timeseries(PROM, "앱별 동기화 횟수 (1시간 단위)", [
    prom('sum by (name) (increase(argocd_app_sync_total[1h]))', "{{name}}")],
    0, d.y, unit="short", minv=0, bars=True, stack=True, decimals=0))
d.add(timeseries(PROM, "동기화 결과 (1시간 단위)", [
    prom('sum by (phase) (increase(argocd_app_sync_total[1h]))', "{{phase}}")],
    8, d.y, unit="short", minv=0, bars=True, stack=True, decimals=0,
    desc="Failed/Error가 보이면 앱별 현재 상태 확인"))
d.add(timeseries(PROM, "평균 동기화 소요 시간 (앱별, 1시간)", [
    prom('sum by (name) (increase(argocd_app_sync_duration_seconds_total[1h])) '
         '/ sum by (name) (increase(argocd_app_sync_total[1h]))', "{{name}}")],
    16, d.y, unit="s", minv=0, fill=0, desc="kps(Helm 차트 렌더링 + Server-Side Apply)가 가장 오래 걸림"))
d.y += 8

# ---------------- 4. Reconcile · 클러스터 캐시 ----------------
d.row("Reconcile · 클러스터 캐시 (컨트롤러 메모리와 직결)")
d.add(timeseries(PROM, "Reconcile 소요 시간", [
    prom('histogram_quantile(0.5, sum by (le) (rate(argocd_app_reconcile_bucket[5m])))', "p50", "A"),
    prom('histogram_quantile(0.95, sum by (le) (rate(argocd_app_reconcile_bucket[5m])))', "p95", "B")],
    0, d.y, unit="s", minv=0, fill=0, desc="앱 상태 비교(Git 렌더링 결과 vs 클러스터) 한 번에 걸린 시간"))
d.add(timeseries(PROM, "캐시된 객체 수 / API 리소스 종류", [
    prom('max(argocd_cluster_api_resource_objects)', "캐시 객체", "A"),
    prom('max(argocd_cluster_api_resources)', "리소스 종류", "B")],
    8, d.y, unit="short", minv=0, fill=0,
    overrides=[{"matcher": {"id": "byName", "options": "리소스 종류"},
                "properties": [{"id": "custom.axisPlacement", "value": "right"}]}],
    desc="컨트롤러가 메모리에 들고 있는 리소스. 감시 범위 제한·CRD 제외 전에는 클러스터 전체를 캐시해 512Mi에서도 OOM"))
d.add(timeseries(PROM, "클러스터 이벤트 처리 (분당, 종류별)", [
    prom('topk(8, sum by (kind) (rate(argocd_cluster_events_total[5m]))) * 60', "{{kind}}")],
    16, d.y, unit="short", minv=0, fill=0, desc="watch로 받은 리소스 변경 이벤트. 많을수록 컨트롤러 CPU·메모리 사용"))
d.y += 8

# ---------------- 5. Git · repo-server ----------------
d.row("Git · repo-server")
d.add(timeseries(PROM, "Git 요청 (분당)", [
    prom('sum by (request_type) (rate(argocd_git_request_total[5m])) * 60', "{{request_type}}")],
    0, d.y, unit="short", minv=0, fill=0, desc="ls-remote = 새 커밋 확인(약 3분 주기), fetch = 변경 시 내려받기"))
d.add(timeseries(PROM, "Git 요청 소요 시간 p95", [
    prom('histogram_quantile(0.95, sum by (le, request_type) (rate(argocd_git_request_duration_seconds_bucket[5m])))', "{{request_type}}")],
    8, d.y, unit="s", minv=0, fill=0, desc="GitHub(deploy key, SSH)까지 왕복 시간"))
d.add(timeseries(PROM, "앱별 Kubernetes API 요청 (분당)", [
    prom('topk(8, sum by (name) (rate(argocd_app_k8s_request_total[5m]))) * 60', "{{name}}")],
    16, d.y, unit="short", minv=0, fill=0, desc="동기화·상태 확인 중 컨트롤러가 API 서버로 보낸 요청"))
d.y += 8

# ---------------- 6. 리소스 ----------------
d.row("리소스 (Spot, 평일 주간만)")
d.add(timeseries(PROM, "메모리 사용 / limit", [
    prom('max by (container) (' + MEM_RATIO.format(ns="argocd") + ')', "{{container}}")],
    0, d.y, unit="percentunit", minv=0, fill=0,
    overrides=[{"matcher": {"id": "byFrameRefID", "options": "A"},
                "properties": [{"id": "thresholds", "value": {"mode": "absolute", "steps": [
                    {"color": "green", "value": None}, {"color": "orange", "value": 0.8}, {"color": "red", "value": 0.9}]}},
                               {"id": "custom.thresholdsStyle", "value": {"mode": "line+area"}}]}],
    desc="limit = requests. 90% 넘으면 OOMKilled 직전"))
d.add(timeseries(PROM, "CPU 사용 (컨테이너별)", [
    prom(f'sum by (container) (rate(container_cpu_usage_seconds_total{{{NS}, container!=""}}[5m]))', "{{container}}")],
    8, d.y, unit="cores", minv=0, fill=0, desc="requests: controller 100m, 나머지 50m (Autopilot 파드 최소)"))
d.add(stat(PROM, "재시작 (24h)", [prom(f'sum(max by (pod, container) (increase(kube_pod_container_status_restarts_total{{{NS}}}[24h]))) or vector(0)')],
           16, d.y, w=4, h=8, decimals=0, graph=False, steps=[("green", None), ("red", 1)]))
d.add(stat(PROM, "OOM (24h)", [prom(OOM_24H.format(sel=', ' + NS))], 20, d.y, w=4, h=8, decimals=0, graph=False,
           steps=[("green", None), ("red", 1)], desc=OOM_DESC))
d.y += 8

dash = base("lab-argocd", "Lab · Argo CD (GitOps)",
            "App of Apps 상태, 동기화 이력·소요 시간, 컨트롤러 캐시·reconcile, Git·repo-server, 리소스",
            ["lab", "gitops", "argocd"], d.panels, time_from="now-24h")
write(dash, sys.argv[1])
