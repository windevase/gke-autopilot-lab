"""Lab · 운영 & 외부 접속 대시보드 생성기.
사용: python gen_ops_dashboard.py lab-ops.json
필요 지표(keep 목록): KSM cronjob/job/replicas, kube_node_info, cloudflared PodMonitor 지표
"""
import sys
from _dashlib import (PROM, TUNNEL_5XX, TUNNEL_RPM, TUNNEL_5XX_DESC, Dashboard, base, prom, range_map, stat,
                      state_timeline, table, timeseries, value_map, write)

NS = 'namespace="monitoring"'
d = Dashboard()

# ---------------- 1. 워크로드 가동 상태 ----------------
d.row("워크로드 가동 상태 (오프아워·예산 자동 차단 효과)")
UPDOWN = range_map([(0, 0, "정지", "#808080"), (1, 100, "가동", "green")])
d.add(state_timeline(PROM, "Deployment / StatefulSet 레플리카", [
    prom(f'max by (deployment) (kube_deployment_spec_replicas{{{NS}}})', "{{deployment}}", "A"),
    prom(f'max by (statefulset) (kube_statefulset_replicas{{{NS}}})', "{{statefulset}}", "B"),
], 0, d.y, w=18, h=7, mappings=UPDOWN,
    desc="Grafana·cloudflared는 평일 10~20시만 가동, Operator는 설정 변경 때만. 예산 초과 시 budget-guard가 전부 0으로 내림"))
d.add(stat(PROM, "지금 정지된 워크로드", [
    # 양쪽 다 or vector(0) — 정지된 Deployment가 없으면 count()가 빈 결과라 전체가 No data가 되던 문제 수정
    prom(f'(count(max by (deployment) (kube_deployment_spec_replicas{{{NS}}}) == 0) or vector(0)) '
         f'+ (count(max by (statefulset) (kube_statefulset_replicas{{{NS}}}) == 0) or vector(0))')],
    18, d.y, w=6, h=7, decimals=0, graph=False, desc="Operator(평소 0)와 오프아워 시간대 Grafana·cloudflared 포함"))
d.y += 7

# ---------------- 2. CronJob ----------------
d.row("예약 작업 (CronJob)")
d.add(table(PROM, "CronJob 상태", [
    prom(f'max by (cronjob) (kube_cronjob_spec_suspend{{{NS}}})', ref="A", instant=True, fmt="table"),
    prom(f'time() - max by (cronjob) (kube_cronjob_status_last_successful_time{{{NS}}})', ref="B", instant=True, fmt="table"),
    prom(f'max by (cronjob) (kube_cronjob_next_schedule_time{{{NS}}}) - time()', ref="C", instant=True, fmt="table"),
], 0, d.y, 16, 7,
    renames={"cronjob": "CronJob", "Value #A": "상태", "Value #B": "마지막 성공", "Value #C": "다음 실행까지"},
    units={"마지막 성공": "s", "다음 실행까지": "s"},
    mappings={"상태": value_map({"0": ("활성", "green"), "1": ("정지됨", "orange")})},
    desc="grafana-scale-up이 '정지됨'이면 예산 자동 차단이 동작한 것 (CLAUDE.md 복구 절차)"))
d.add(stat(PROM, "실패한 Job (24h)", [
    prom(f'count(max by (job_name) (max_over_time(kube_job_status_failed{{{NS}}}[24h])) > 0) or vector(0)')],
    16, d.y, w=4, h=7, decimals=0, graph=False, steps=[("green", None), ("red", 1)]))
d.add(stat(PROM, "budget-guard 마지막 성공", [
    prom(f'time() - max(kube_cronjob_status_last_successful_time{{{NS},cronjob="budget-guard"}})')],
    20, d.y, w=4, h=7, unit="s", graph=False,
    steps=[("green", None), ("orange", 5400), ("red", 10800)],
    desc="매시 17분 실행. 1.5시간 넘게 성공이 없으면 주황, 3시간이면 빨강"))
d.y += 7

# ---------------- 3. 노드 ----------------
d.row("노드 (Autopilot · Spot 교체 이력)")
d.add(state_timeline(PROM, "노드 수명", [prom('max by (node) (kube_node_info)', "{{node}}")],
                     0, d.y, w=18, h=7, mappings=range_map([(1, 1, "존재", "blue")]),
                     desc="막대가 끊기고 새 노드가 생기면 Spot 회수 또는 Autopilot 재배치로 교체된 것"))
d.add(stat(PROM, "기간 내 사용된 노드 수", [prom('count(max by (node) (max_over_time(kube_node_info[$__range])))', instant=True)],
           18, d.y, w=6, h=7, decimals=0, graph=False, desc="대시보드 기간 동안 한 번이라도 존재한 노드 수"))
d.y += 7

# ---------------- 4. 외부 접속 ----------------
d.row("외부 접속 (Cloudflare Tunnel)")
CF_OFF = "평일 20시 이후·주말에는 cloudflared가 꺼져 있어 데이터 없음이 정상"
d.add(stat(PROM, "터널 연결", [prom('max(cloudflared_tunnel_ha_connections)')], 0, d.y, w=4, h=4, decimals=0,
           steps=[("red", None), ("orange", 1), ("green", 4)], desc="cloudflared↔Cloudflare 엣지 연결 수 (정상 4). " + CF_OFF))
d.add(stat(PROM, "요청 (분당)", [prom(TUNNEL_RPM)], 4, d.y, w=4, h=4, decimals=1))
d.add(stat(PROM, "서버 오류율 (5xx)", [prom(TUNNEL_5XX)],
           8, d.y, w=4, h=4, unit="percentunit", decimals=2,
           steps=[("green", None), ("orange", 0.01), ("red", 0.05)], desc=TUNNEL_5XX_DESC))
d.add(stat(PROM, "평균 RTT (엣지)", [prom('avg(quic_client_smoothed_rtt)')], 12, d.y, w=4, h=4, unit="ms", decimals=0,
           steps=[("green", None), ("orange", 150), ("red", 300)], desc="cloudflared↔Cloudflare 엣지 QUIC 왕복 시간"))
d.add(table(PROM, "연결된 엣지 위치", [
    prom('max by (connection_id, edge_location) (cloudflared_tunnel_server_locations)', ref="A", instant=True, fmt="table")],
    16, d.y, 8, 4, renames={"connection_id": "연결", "edge_location": "엣지(공항코드)"}, exclude=["Value"], merge=False))
d.y += 4
d.add(timeseries(PROM, "응답 코드별 요청률", [
    prom('sum by (status_code) (lab:cloudflared_tunnel_response_by_code:rate5m)', "{{status_code}}")],
    0, d.y, unit="reqps", minv=0, stack=True, desc=CF_OFF))
# (예전 "오리진 연결 지연"은 cloudflared_proxy_connect_latency가 TCP/WARP 프록시 전용이라 HTTP 터널에선 항상 0 → 교체)
d.add(timeseries(PROM, "동시 처리 요청 (터널)", [
    prom('sum(max_over_time(cloudflared_tunnel_concurrent_requests_per_tunnel[5m]))', "동시 요청 (5분 최대)")],
    8, d.y, unit="short", minv=0, fill=0, decimals=0,
    desc="터널이 동시에 처리 중인 요청 수 (5분 구간 최대값). 대시보드를 여는 순간 패널 수만큼 올라감"))
d.add(timeseries(PROM, "터널 트래픽 (송신 + / 수신 −)", [
    prom('sum(lab:quic_client_sent_bytes:rate5m)', "송신 (cloudflared→엣지)", "A"),
    prom('-sum(lab:quic_client_receive_bytes:rate5m)', "수신 (엣지→cloudflared)", "B")],
    16, d.y, unit="Bps", desc="GCP 송신 트래픽(월 1GB 무료)의 대부분이 이 송신 값"))
d.y += 8

# GitOps(Argo CD) 패널은 전용 대시보드 lab-argocd로 분리 (gen_argocd_dashboard.py)

dash = base("lab-ops", "Lab · 운영 & 외부 접속",
            "워크로드 가동 상태(오프아워·kill switch), CronJob, Spot 노드 교체, Cloudflare Tunnel 트래픽",
            ["lab", "ops", "cloudflare"], d.panels, time_from="now-24h")
write(dash, sys.argv[1])
