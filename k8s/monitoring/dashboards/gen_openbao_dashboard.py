"""Lab · OpenBao (임시 자격 발급) 대시보드 생성기.
사용: python gen_openbao_dashboard.py lab-openbao.json
필요 지표(keep 목록, k8s/openbao/values.yaml ServiceMonitor):
  상태      vault_core_unsealed, vault_core_active, vault_autopilot_healthy, vault_raft_peers
  인증·발급 vault_token_creation_total(인증 방식별 로그인 → 토큰), vault_secret_lease_creation_total(엔진별 발급),
            vault_expire_num_leases(살아 있는 자격), vault_expire_revoke_count(만료·폐기)
  요청      vault_route_*_count(경로별), vault_core_handle_request_{sum,count}, vault_core_handle_login_request_{sum,count}
  감사      vault_audit_log_request_count, vault_audit_log_{request,response}_failure_total
  공통      cAdvisor·KSM (메모리·CPU·재시작), up
OpenBao 는 필요할 때만 가동(tools/atlantis.ps1 on/off, 20:00 자동 종료) → 꺼진 시간은 패널이 비는 것이 정상.
카운터는 파드가 켜질 때마다 0부터 다시 쌓임 → increase()/rate() 로만 본다.
"""
import sys
from _dashlib import (MEM_RATIO, PROM, Dashboard, base, prom, stat, state_timeline, table, timeseries,
                      value_map, write)

NS = 'namespace="openbao"'
OFF = "꺼짐 (필요할 때만 가동)"
UNSEALED = 'max(vault_core_unsealed{cluster!=""})'
# AppRole 로그인 시도(경로 호출) - 성공(토큰 발급) = 실패 추정
APPROLE_TRY = 'sum(increase(vault_route_update_auth_approle__count[{w}]))'
APPROLE_OK = 'sum(increase(vault_token_creation_total{{auth_method="approle"}}[{w}]))'
d = Dashboard()

# ---------------- 1. 개요 ----------------
d.row("개요 — OpenBao 는 필요할 때만 가동 (꺼진 시간은 비어 있음)")
d.add(stat(PROM, "상태", [prom(UNSEALED)], 0, d.y, w=4, graph=False, no_value=OFF,
           steps=[("red", None), ("green", 1)], mappings=value_map({"1": ("열림 (unsealed)", "green"), "0": ("잠김 (sealed)", "red")}),
           desc="static seal 로 기동 시 자동 unseal. 잠김이면 unseal 키 Secret 확인"))
d.add(stat(PROM, "가동 시간 (7일)", [prom(f'sum_over_time((max(up{{{NS}}}) > bool 0)[7d:5m]) * 5 * 60')],
           4, d.y, w=4, unit="s", decimals=1, graph=False, desc="켜 둔 시간 합계 (5분 단위). 비용은 켜 둔 시간만 발생"))
d.add(stat(PROM, "살아 있는 자격 (lease)", [prom('max(vault_expire_num_leases)')], 8, d.y, w=4, decimals=0, graph=False,
           no_value=OFF, desc="아직 만료되지 않은 발급 자격·토큰 수. 만료되면 OpenBao 가 자동 정리"))
d.add(stat(PROM, "로그인 (24h)", [prom('sum(increase(vault_token_creation_total[24h])) or vector(0)')], 12, d.y, w=3,
           decimals=0, graph=False, desc="성공한 로그인 수 (AppRole·OIDC 합계)"))
d.add(stat(PROM, "AWS 자격 발급 (24h)", [prom('sum(increase(vault_secret_lease_creation_total{secret_engine="aws"}[24h])) or vector(0)')],
           15, d.y, w=3, decimals=0, graph=False, desc="STS 임시 자격 발급 수. 클라이언트 캐시(만료 5분 전까지 재사용) 덕에 aws 명령 수보다 훨씬 적음"))
d.add(stat(PROM, "AppRole 로그인 실패 (24h, 추정)", [prom(
    f'clamp_min({APPROLE_TRY.format(w="24h")} - ({APPROLE_OK.format(w="24h")} or vector(0)), 0) or vector(0)')],
    18, d.y, w=3, decimals=0, graph=False, steps=[("green", None), ("orange", 1), ("red", 10)],
    desc="AppRole 로그인 호출 수 - 성공 수. 잘못된 secret_id·허용 IP 밖 요청 등. 늘면 감사 로그(파드 stdout) 확인"))
d.add(stat(PROM, "감사 로그 실패 (24h)", [prom(
    'sum(increase(vault_audit_log_request_failure_total[24h]) + increase(vault_audit_log_response_failure_total[24h])) or vector(0)')],
    21, d.y, w=3, decimals=0, graph=False, steps=[("green", None), ("red", 1)],
    mappings=value_map({"0": ("없음", "green")}), desc="감사 장치에 기록하지 못한 요청. 0 이 아니면 즉시 확인(감사 실패 시 요청도 거부됨)"))
d.y += 4

# ---------------- 2. 가동 ----------------
d.row("가동")
d.add(state_timeline(PROM, "가동·봉인 상태", [
    prom(f'max(up{{{NS}}})', "수집(가동)", "A"),
    prom(UNSEALED, "unsealed", "B")],
    0, d.y, w=24, h=5,
    mappings=value_map({"1": ("정상", "green"), "0": ("이상", "red")}),
    desc="빈 구간 = OpenBao 가 꺼져 있던 시간 (필요할 때만 가동)"))
d.y += 5

# ---------------- 3. 인증·발급 ----------------
d.row("인증 · 자격 발급")
d.add(timeseries(PROM, "로그인 (인증 방식별, 1시간 단위)", [
    prom('sum by (auth_method) (increase(vault_token_creation_total[1h]))', "{{auth_method}}")],
    0, d.y, unit="short", minv=0, bars=True, stack=True, decimals=0,
    desc="approle = 역할 없는 서버(IP 고정), oidc = 담당자 브라우저 로그인"))
d.add(timeseries(PROM, "자격 발급 (엔진·TTL별, 1시간 단위)", [
    prom('sum by (secret_engine, creation_ttl) (increase(vault_secret_lease_creation_total[1h]))', "{{secret_engine}} {{creation_ttl}}")],
    8, d.y, unit="short", minv=0, bars=True, stack=True, decimals=0,
    desc="aws = STS 임시 자격(서버 15분, 관리자 1시간 — 표시 TTL 은 토큰 기준일 수 있음)"))
d.add(timeseries(PROM, "살아 있는 자격 / 만료·폐기", [
    prom('max(vault_expire_num_leases)', "살아 있는 lease", "A"),
    prom('sum(increase(vault_expire_revoke_count[1h]))', "만료·폐기 (1시간)", "B")],
    16, d.y, unit="short", minv=0, fill=0, decimals=0,
    desc="발급 자격은 TTL 이 지나면 자동 만료. 서버용 스크립트는 로그인 토큰을 호출마다 즉시 폐기"))
d.y += 8

# ---------------- 4. 요청 ----------------
d.row("요청")
d.add(table(PROM, "경로별 요청 수 (선택 기간)", [prom(
    # increase() 는 지표 이름을 지우므로 이름 → route 라벨을 먼저 만들고 서브쿼리로 증가량. 내부 주기 작업(rollback_*)은 제외
    'sort_desc(sum by (route) (increase(label_replace({__name__=~"vault_route_.+_count", __name__!~"vault_route_rollback_.+"}, '
    '"route", "$1", "__name__", "vault_route_(.+)_count")[$__range:1m])) > 0)', ref="A", instant=True, fmt="table")],
    0, d.y, 8, 8, renames={"route": "경로 (동작_마운트)", "Value": "요청 수"}, units={"요청 수": "short"},
    desc="update_auth_approle_ = AppRole 로그인, update_aws_ = AWS 자격 발급, update_auth_token_ = 토큰 폐기 등"))
d.add(timeseries(PROM, "평균 처리 시간", [
    prom('sum(rate(vault_core_handle_request_sum[5m])) / sum(rate(vault_core_handle_request_count[5m])) / 1000', "일반 요청", "A"),
    prom('sum(rate(vault_core_handle_login_request_sum[5m])) / sum(rate(vault_core_handle_login_request_count[5m])) / 1000', "로그인", "B")],
    8, d.y, unit="s", minv=0, fill=0,
    desc="AWS 자격 발급은 OpenBao → AWS STS 왕복이 포함돼 일반 요청보다 김"))
d.add(timeseries(PROM, "요청 수 (분당)", [
    prom('sum(rate(vault_core_handle_request_count[5m])) * 60', "일반 요청", "A"),
    prom('sum(rate(vault_core_handle_login_request_count[5m])) * 60', "로그인", "B"),
    prom('sum(rate(vault_audit_log_request_count[5m])) * 60', "감사 기록", "C")],
    16, d.y, unit="short", minv=0, fill=0, desc="모든 요청은 감사 로그(stdout)에 기록 — 감사 기록 수 ≈ 전체 요청 수"))
d.y += 8

# ---------------- 5. 저장소 · 리소스 ----------------
d.row("저장소(Raft) · 리소스 (Spot)")
d.add(stat(PROM, "Raft 상태", [prom('min(vault_autopilot_healthy)')], 0, d.y, w=4, h=8, graph=False, no_value=OFF,
           steps=[("red", None), ("green", 1)], mappings=value_map({"1": ("정상", "green"), "0": ("이상", "red")}),
           desc="통합 저장소(Raft) autopilot 판단. 지금은 1대 구성"))
d.add(stat(PROM, "Raft 노드", [prom('max(vault_raft_peers)')], 4, d.y, w=4, h=8, decimals=0, graph=False, no_value=OFF,
           desc="lab 은 1대(회사 환경은 3대 권장)"))
d.add(timeseries(PROM, "메모리 사용 / limit", [
    prom('max by (container) (' + MEM_RATIO.format(ns="openbao") + ')', "{{container}}")],
    8, d.y, unit="percentunit", minv=0, fill=0,
    overrides=[{"matcher": {"id": "byFrameRefID", "options": "A"},
                "properties": [{"id": "thresholds", "value": {"mode": "absolute", "steps": [
                    {"color": "green", "value": None}, {"color": "orange", "value": 0.8}, {"color": "red", "value": 0.9}]}},
                               {"id": "custom.thresholdsStyle", "value": {"mode": "line+area"}}]}],
    desc="limit 256Mi (= requests). 플러그인(AWS)은 별도 프로세스로 같은 컨테이너 메모리를 씀"))
d.add(timeseries(PROM, "CPU 사용", [
    prom(f'sum by (container) (rate(container_cpu_usage_seconds_total{{{NS}, container!=""}}[5m]))', "{{container}}")],
    16, d.y, unit="cores", minv=0, fill=0, desc="requests 50m (Autopilot 파드 최소)"))
d.y += 8

dash = base("lab-openbao", "Lab · OpenBao (임시 자격 발급)",
            "역할 없는 서버(AppRole)·담당자(OIDC) 로그인, AWS STS 발급, 살아 있는 자격, 경로별 요청·처리 시간, 감사, Raft·리소스",
            ["lab", "security", "openbao"], d.panels, time_from="now-24h")
write(dash, sys.argv[1])
