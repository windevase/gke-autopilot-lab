"""Lab · SLO (lab-api 에러 버짓) 대시보드 생성기.
사용: python gen_slo_dashboard.py lab-slo.json
데이터: Sloth 가 만든 recording 규칙 (원본 slo/lab-api.yaml → k8s/lab-api/slo-rules.yaml)
  slo:sli_error:ratio_rate{5m,30m,1h,2h,6h,1d,3d,1w}, slo:error_budget:ratio, slo:objective:ratio,
  slo:current_burn_rate:ratio, slo:period_burn_rate:ratio, slo:period_error_budget_remaining:ratio
  + lab-api 원본 지표(ServiceMonitor keep): http_requests_total, http_request_duration_seconds_*, lab_api_build_info
파드 템플릿별 패널은 ServiceMonitor podTargetLabels(rollouts-pod-template-hash)로 붙는 레이블 사용.
SLO 기간은 7일(Prometheus 보관 기간). 경보 규칙은 Prometheus 안에서만 평가 — Alertmanager 가 없어 알림은 없고 상태만 표시.
"""
import sys
from _dashlib import PROM, Dashboard, base, gauge, prom, stat, table, timeseries, value_map, write

SVC = 'sloth_service="lab-api"'
SLOS = [("availability", "가용성 (5xx 아닌 비율, 목표 99.5%)"), ("latency", "지연 (300ms 이내 비율, 목표 99%)")]
APP = 'job="lab-api"'
ITEMS = f'{APP}, handler="items"'
BUDGET_STEPS = [("red", None), ("orange", 0.25), ("green", 0.5)]
BURN_STEPS = [("green", None), ("orange", 1), ("red", 6)]


def sel(slo):
    return f'{SVC}, sloth_slo="{slo}"'


d = Dashboard()

# ---------------- 1. 요약 ----------------
for slo, title in SLOS:
    d.row(f"SLO — {title}")
    s = sel(slo)
    d.add(stat(PROM, "SLI (7일)", [prom(f'1 - max(slo:sli_error:ratio_rate1w{{{s}}})')], 0, d.y, w=5, unit="percentunit",
               decimals=3, graph=False, steps=[("red", None), ("green", 0.99 if slo == "latency" else 0.995)],
               desc="기간(7일) 동안 좋은 요청 비율. 목표 이상이면 초록"))
    d.add(stat(PROM, "목표", [prom(f'max(slo:objective:ratio{{{s}}})')], 5, d.y, w=3, unit="percentunit", decimals=1,
               graph=False, color_mode="none"))
    d.add(gauge(PROM, "남은 에러 버짓 (7일)", [prom(f'max(slo:period_error_budget_remaining:ratio{{{s}}})')], 8, d.y, w=5,
                steps=BUDGET_STEPS, desc="7일 동안 허용되는 실패량 중 아직 쓰지 않은 비율. 0 아래면 목표 미달"))
    d.add(stat(PROM, "현재 소진 속도 (5m)", [prom(f'max(slo:current_burn_rate:ratio{{{s}}})')], 13, d.y, w=4, decimals=2,
               graph=True, steps=BURN_STEPS, desc="1 = 기간 끝에 예산을 딱 다 쓰는 속도. 14.4배가 1시간 이어지면 page 경보"))
    d.add(stat(PROM, "기간 평균 소진 속도", [prom(f'max(slo:period_burn_rate:ratio{{{s}}})')], 17, d.y, w=3, decimals=2,
               graph=False, steps=BURN_STEPS))
    d.add(stat(PROM, "경보 (firing)", [prom(f'count(ALERTS{{{s}, alertstate="firing"}}) or vector(0)')], 20, d.y, w=4,
               decimals=0, graph=False, steps=[("green", None), ("red", 1)], mappings=value_map({"0": ("없음", "green")}),
               desc="Sloth 다중 창 burn rate 경보 (page·ticket). 알림 채널은 없음 — 상태만 표시"))
    d.y += 4

# ---------------- 2. 소진 속도 ----------------
d.row("소진 속도 (burn rate) — 다중 창. 경보 기준: page 14.4배(1h·5m) / 6배(6h·30m), ticket 3배(1d·2h) / 1배(3d·6h)")
for i, (slo, title) in enumerate(SLOS):
    s = sel(slo)
    targets = [prom(f'max(slo:sli_error:ratio_rate{w}{{{s}}}) / max(slo:error_budget:ratio{{{s}}})', w, ref=chr(65 + j))
               for j, w in enumerate(["5m", "1h", "6h", "1d"])]
    d.add(timeseries(PROM, f"{title.split(' ')[0]} — 창별 소진 속도", targets, 12 * i, d.y, w=12, h=8, decimals=2, minv=0,
                     desc="오류 비율 ÷ 허용 오류 비율. 짧은 창은 급한 장애, 긴 창은 느린 누수를 잡는다"))
d.y += 8

# ---------------- 3. 에러 버짓 추이 ----------------
d.row("에러 버짓 추이")
d.add(timeseries(PROM, "남은 에러 버짓 (7일 기준)",
                 [prom(f'max(slo:period_error_budget_remaining:ratio{{{sel(slo)}}})', slo, ref=chr(65 + j))
                  for j, (slo, _) in enumerate(SLOS)], 0, d.y, w=12, h=8, unit="percentunit",
                 desc="배포·장애 시점에 꺾이는 곳을 보면 어떤 변경이 예산을 썼는지 알 수 있다"))
d.add(table(PROM, "경보 상태 (pending·firing)",
            [prom(f'max by (alertname, sloth_slo, sloth_severity, alertstate) (ALERTS{{{SVC}}})', instant=True, fmt="table")],
            12, d.y, w=12, h=8, renames={"alertname": "경보", "sloth_slo": "SLO", "sloth_severity": "등급", "alertstate": "상태"},
            exclude=["Value"], desc="비어 있으면 정상"))
d.y += 8

# ---------------- 4. 서비스 지표 (RED) ----------------
d.row("서비스 지표 (RED) — lab-api")
d.add(timeseries(PROM, "요청 수 (응답 코드별)", [prom(f'sum by (code) (rate(http_requests_total{{{ITEMS}}}[5m]))', "{{code}}")],
                 0, d.y, w=8, h=8, unit="reqps", stack=True, desc="트래픽 생성기(lab-api-loadgen)가 초당 2건 상시 호출"))
d.add(timeseries(PROM, "오류율 (5xx)", [prom(
    f'(sum(rate(http_requests_total{{{ITEMS}, code=~"5.."}}[5m])) or vector(0)) / sum(rate(http_requests_total{{{ITEMS}}}[5m]))',
    "5xx 비율")], 8, d.y, w=8, h=8, unit="percentunit", minv=0))
d.add(timeseries(PROM, "지연 분위수", [
    prom(f'histogram_quantile({q}, sum by (le) (rate(http_request_duration_seconds_bucket{{{ITEMS}}}[5m])))', f"p{int(q * 100)}",
         ref=chr(65 + j)) for j, q in enumerate([0.5, 0.95, 0.99])], 16, d.y, w=8, h=8, unit="s", minv=0,
    desc="버킷 경계(25·50·100·300·500ms…) 사이는 선형 보간 근사값. SLO 기준선 300ms"))
d.y += 8
d.add(stat(PROM, "가동 파드", [prom(f'sum(up{{{APP}}})')], 0, d.y, w=4, decimals=0, graph=False,
           steps=[("red", None), ("orange", 1), ("green", 2)], desc="2대 운영 (노드 분산)"))
d.add(table(PROM, "실행 중인 버전", [prom('count by (version) (lab_api_build_info)', instant=True, fmt="table")],
            4, d.y, w=8, h=4, renames={"version": "버전 (sha-커밋)", "Value": "파드 수"},
            desc="canary 배포 중에는 두 버전이 함께 보인다"))
d.add(timeseries(PROM, "메모리 (파드별)", [prom(f'max by (pod) (process_resident_memory_bytes{{{APP}}})', "{{pod}}")],
                 12, d.y, w=12, h=4, unit="bytes", minv=0, desc="requests·limit 64Mi"))
d.y += 4

# ---------------- 5. canary (Argo Rollouts) ----------------
HASH = "rollouts_pod_template_hash"
d.row("canary 배포 — 파드 템플릿별 (Argo Rollouts, 분석 기준: page 14.4배)")
d.add(timeseries(PROM, "요청 수 (파드 템플릿별)", [prom(f'sum by ({HASH}) (rate(http_requests_total{{{ITEMS}}}[2m]))', f"{{{{{HASH}}}}}")],
                 0, d.y, w=8, h=8, unit="reqps", stack=True, desc="canary 중에는 stable(2대)·canary(1대) 두 템플릿 — 파드 수 비율로 약 2:1"))
d.add(timeseries(PROM, "가용성 소진 속도 (파드 템플릿별)", [prom(
    f'((sum by ({HASH}) (rate(http_requests_total{{{ITEMS}, code=~"5.."}}[2m])) or sum by ({HASH}) (rate(http_requests_total{{{ITEMS}}}[2m])) * 0)'
    f' / sum by ({HASH}) (rate(http_requests_total{{{ITEMS}}}[2m]))) / 0.005', f"{{{{{HASH}}}}}")],
    8, d.y, w=8, h=8, decimals=1, minv=0, desc="AnalysisTemplate slo-burn-rate 와 같은 계산(2분 창). 14.4 이상이면 canary 중단"))
d.add(timeseries(PROM, "지연 소진 속도 (파드 템플릿별)", [prom(
    f'((sum by ({HASH}) (rate(http_request_duration_seconds_count{{{ITEMS}}}[2m]))'
    f' - sum by ({HASH}) (rate(http_request_duration_seconds_bucket{{{ITEMS}, le="0.3"}}[2m])))'
    f' / sum by ({HASH}) (rate(http_request_duration_seconds_count{{{ITEMS}}}[2m]))) / 0.01', f"{{{{{HASH}}}}}")],
    16, d.y, w=8, h=8, decimals=1, minv=0, desc="300ms 초과 비율 ÷ 0.01. 14.4 이상이면 canary 중단"))
d.y += 8

write(base("lab-slo", "Lab · SLO (lab-api 에러 버짓)",
           "Sloth 로 만든 lab-api SLO(가용성 99.5%·지연 300ms 99%, 7일)의 에러 버짓·다중 창 소진 속도와 서비스 RED 지표",
           ["lab", "slo", "sre"], d.panels, time_from="now-24h", refresh="1m"), sys.argv[1])
