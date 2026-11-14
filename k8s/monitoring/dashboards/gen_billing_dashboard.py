"""Lab · 실제 청구액 대시보드 생성기 (BigQuery 결제 내보내기).
사용: python gen_billing_dashboard.py lab-billing.json
- 테이블: billing_export.gcp_billing_export_v1_* (프로젝트는 Grafana 데이터소스 defaultProject)
- 청구월(invoice.month)은 태평양 시간 기준. 결제 데이터는 1~2일 늦게 적재됨
"""
import sys
from _dashlib import (BILL_CRED as CRED, BILL_ELAPSED as ELAPSED, BILL_FREE as FREE, BILL_MONTH as MONTH,
                      BILL_T as T, BQ, PROM, Dashboard, base, bq, gauge, prom, stat, table, timeseries, write)

RANGE = "usage_start_time >= TIMESTAMP_MILLIS($__from) AND usage_start_time < TIMESTAMP_MILLIS($__to)"
DAY = "TIMESTAMP_TRUNC(usage_start_time, DAY, 'America/Los_Angeles')"
WATCH = ("(sku.description LIKE 'Network Intelligence Center%' "
         "OR sku.description LIKE '%Private Service Connect Partner%')")
KRW = "currencyKRW"
LAG = "결제 데이터는 1~2일 늦게 들어오므로 최근 하루 이틀은 적게 보임"

d = Dashboard()

# ---------------- 1. 이번 달 요약 ----------------
d.row("이번 달 요약 (청구월, 태평양 시간 기준)")
d.add(stat(BQ, "사용액 (할인 전)", [bq(f"SELECT IFNULL(ROUND(SUM(cost)), 0) AS value FROM {T} WHERE {MONTH}")],
           0, d.y, unit=KRW, decimals=0, graph=False, desc=LAG))
d.add(stat(BQ, "크레딧·할인", [bq(f"SELECT IFNULL(ROUND(SUM({CRED})), 0) AS value FROM {T} WHERE {MONTH}")],
           4, d.y, unit=KRW, decimals=0, graph=False, desc="무료 티어, 프로모션(Developer Program) 크레딧, 할인 등 (음수)"))
d.add(stat(BQ, "순 청구액", [bq(f"SELECT IFNULL(ROUND(SUM(cost) + SUM({CRED})), 0) AS value FROM {T} WHERE {MONTH}")],
           8, d.y, unit=KRW, decimals=0, graph=False, steps=[("green", None), ("orange", 1), ("red", 5000)],
           desc="실제로 결제될 금액. 크레딧 안에서 운영 중이면 0에 가까움"))
d.add(gauge(BQ, "예산 대비 (₩$budget_krw)", [bq(
    f"SELECT IFNULL(SAFE_DIVIDE(SUM(cost) + SUM({FREE}), $budget_krw), 0) AS value FROM {T} WHERE {MONTH}")],
    12, d.y, steps=[("green", None), ("orange", 0.5), ("red", 0.9)],
    desc="예산과 같은 기준(무료 티어만 차감, 프로모션 크레딧 차감 전). 100%면 budget-guard가 워크로드를 0으로 내림"))
d.add(stat(BQ, "월말 예상 (순 청구액)", [bq(
    f"SELECT IFNULL(ROUND(SAFE_DIVIDE(SUM(cost) + SUM({CRED}), {ELAPSED})), 0) AS value FROM {T} WHERE {MONTH}")],
    16, d.y, unit=KRW, decimals=0, graph=False, steps=[("green", None), ("orange", 1), ("red", 5000)],
    desc="지금까지 순 청구액 ÷ 이번 달 경과 비율. " + LAG + " (예상치도 약간 낮게 나옴)"))
d.add(stat(BQ, "확인 중인 SKU 순액", [bq(
    f"SELECT IFNULL(ROUND(SUM(cost) + SUM({CRED})), 0) AS value FROM {T} WHERE {MONTH} AND {WATCH}")],
    20, d.y, unit=KRW, decimals=0, graph=False, steps=[("green", None), ("orange", 1), ("red", 3000)],
    desc="Network Intelligence Center 3종(100% 할인 대상) + PSC Partner Select 엔드포인트. 할인이 붙으면 0이어야 정상"))
d.y += 4

# ---------------- 2. 추정 vs 실제 ----------------
d.row("추정 vs 실제 (USD, 월 환산)")
EST = ("(avg_over_time(sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource=\"cpu\",namespace!~\"kube-system|gke-.*\"})"
       " * on(namespace,pod) group_left() max by (namespace,pod) (kube_pod_status_phase{phase=\"Running\"} == 1))[7d:10m]) * $cpu_rate"
       " + avg_over_time(sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource=\"memory\",namespace!~\"kube-system|gke-.*\"})"
       " * on(namespace,pod) group_left() max by (namespace,pod) (kube_pod_status_phase{phase=\"Running\"} == 1))[7d:10m]) / 2^30 * $mem_rate) * 730")
d.add(stat(PROM, "추정: 파드 requests 기반 (최근 7일)", [prom(EST)], 0, d.y, w=6, unit="currencyUSD", decimals=2, graph=False,
           desc="비용 & 용량 대시보드와 같은 계산 (PV 제외). Spot 단가 변수 cpu_rate/mem_rate"))
d.add(stat(BQ, "실제: Kubernetes Engine 월말 예상", [bq(
    f"SELECT IFNULL(ROUND(SAFE_DIVIDE(SUM(cost / currency_conversion_rate), {ELAPSED}), 2), 0) AS value "
    f"FROM {T} WHERE {MONTH} AND service.description = 'Kubernetes Engine'")],
    6, d.y, w=6, unit="currencyUSD", decimals=2, graph=False,
    desc="Kubernetes Engine 서비스의 할인 전 사용액 (파드 요금 + 클러스터 관리비 포함 — 관리비는 무료 티어 크레딧으로 상쇄)"))
d.add(stat(BQ, "실제: 전체 월말 예상 (할인 전)", [bq(
    f"SELECT IFNULL(ROUND(SAFE_DIVIDE(SUM(cost / currency_conversion_rate), {ELAPSED}), 2), 0) AS value FROM {T} WHERE {MONTH}")],
    12, d.y, w=6, unit="currencyUSD", decimals=2, graph=False, desc="모든 서비스 합계 (크레딧 차감 전)"))
d.add(stat(BQ, "실제: 전체 월말 예상 (순액)", [bq(
    f"SELECT IFNULL(ROUND(SAFE_DIVIDE(SUM((cost + {CRED}) / currency_conversion_rate), {ELAPSED}), 2), 0) AS value FROM {T} WHERE {MONTH}")],
    18, d.y, w=6, unit="currencyUSD", decimals=2, graph=False, steps=[("green", None), ("orange", 1), ("red", 10)],
    desc="크레딧 차감 후 실제 결제 예상. $10 크레딧 안이면 0 근처"))
d.y += 4

# ---------------- 3. 추이 ----------------
d.row("추이")
d.add(timeseries(BQ, "일별 사용액 (서비스별, 할인 전)", [bq(
    f"SELECT {DAY} AS time, service.description AS service, ROUND(SUM(cost), 1) AS cost "
    f"FROM {T} WHERE {RANGE} GROUP BY 1, 2 ORDER BY 1", table=False)],
    0, d.y, w=14, h=8, unit=KRW, bars=True, stack=True, minv=0, desc=LAG))
d.add(timeseries(BQ, "이번 달 누적 (사용액 vs 순 청구액)", [bq(
    f"SELECT day AS time, SUM(gross) OVER (ORDER BY day) AS `누적 사용액`, SUM(net) OVER (ORDER BY day) AS `누적 순 청구액` "
    f"FROM (SELECT {DAY} AS day, SUM(cost) AS gross, SUM(cost) + SUM({CRED}) AS net FROM {T} WHERE {MONTH} GROUP BY 1) ORDER BY 1",
    table=False)],
    14, d.y, w=10, h=8, unit=KRW, minv=0, desc="두 선의 차이가 크레딧·할인으로 상쇄된 금액"))
d.y += 8

# ---------------- 4. 상세 ----------------
d.row("상세 (이번 달)")
# BigQuery 열 이름은 영문(특수문자 제한) → 한글 표시는 Grafana renames
d.add(table(BQ, "SKU별 비용", [bq(
    f"SELECT service.description AS service, sku.description AS sku, "
    f"ROUND(SUM(usage.amount_in_pricing_units), 3) AS usage_amount, ANY_VALUE(usage.pricing_unit) AS unit, "
    f"ROUND(SUM(cost), 1) AS gross_krw, ROUND(SUM({CRED}), 1) AS credits_krw, ROUND(SUM(cost) + SUM({CRED}), 1) AS net_krw "
    f"FROM {T} WHERE {MONTH} GROUP BY 1, 2 HAVING ABS(SUM(cost)) > 0 OR ABS(SUM({CRED})) > 0 ORDER BY gross_krw DESC")],
    0, d.y, 16, 10, merge=False,
    renames={"service": "서비스", "sku": "SKU", "usage_amount": "사용량", "unit": "단위",
             "gross_krw": "사용액", "credits_krw": "크레딧·할인", "net_krw": "순액"},
    units={"사용액": KRW, "크레딧·할인": KRW, "순액": KRW},
    desc="순액이 0보다 크면 크레딧으로 상쇄되지 않은 항목"))
d.add(table(BQ, "크레딧·할인 종류별", [bq(
    f"SELECT c.type AS type, c.name AS name, ROUND(SUM(c.amount), 1) AS amount "
    f"FROM {T}, UNNEST(credits) AS c WHERE {MONTH} GROUP BY 1, 2 ORDER BY amount")],
    16, d.y, 8, 10, merge=False, renames={"type": "종류", "name": "이름", "amount": "금액"}, units={"금액": KRW},
    desc="FREE_TIER(무료 티어), PROMOTION(Developer Program 크레딧), DISCOUNT 등"))
d.y += 10

templating = [
    {"name": "budget_krw", "type": "constant", "hide": 2, "query": "15000", "label": "월 예산(원)"},
    {"name": "cpu_rate", "type": "constant", "hide": 2, "query": "0.0133"},
    {"name": "mem_rate", "type": "constant", "hide": 2, "query": "0.0014767"},
]
dash = base("lab-billing", "Lab · 실제 청구액",
            "BigQuery 결제 내보내기 기반 실제 비용·크레딧·월말 예상, 파드 requests 추정치와 비교",
            ["lab", "cost", "billing"], d.panels, templating=templating, time_from="now-30d",
            refresh="1h")  # 결제 데이터는 하루 몇 번만 갱신 + BigQuery 쿼리당 최소 10MB 과금
write(dash, sys.argv[1])
