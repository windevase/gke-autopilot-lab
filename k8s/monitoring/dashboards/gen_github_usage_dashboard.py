"""Lab · GitHub 사용량 대시보드 생성기 (GitHub Free 무료 한도 대비 사용량).
사용: python gen_github_usage_dashboard.py lab-github-usage.json
- 데이터소스: Infinity(uid github-rest) → api.github.com, 감시용 PAT(Plan 읽기 권한 포함)
  · /users/{user}/settings/billing/usage/summary : 이번 달(UTC) SKU 별 사용량 — grossQuantity·netAmount
  · /users/{user}/settings/billing/usage         : 이번 달 저장소·SKU 별 사용량
  · /repos/{repo}/actions/cache/usage, /rate_limit
- 무료 한도(GitHub Free 개인, private 저장소): Actions 2,000분/월(Linux 1배·Windows 2배·macOS 10배 차감),
  저장 공간 500MB — GitHub 은 월 GB-시간 합계로 차감(0.5GB × 한 달 ≈ 365 GB-시간). public 저장소는 차감 없음
- Infinity 계산식(expr)에는 날짜 함수가 없다 → 현재 일·시는 Grafana 시간 변수 ${__to:date:D}/${__to:date:H}(UTC)로 넣고,
  한 달 길이는 평균 730.5시간으로 근사(월말 예상은 '대략')
"""
import sys
from _dashlib import Dashboard, base, gauge, stat, table, write

INF = {"type": "yesoreyeram-infinity-datasource", "uid": "github-rest"}
API = "https://api.github.com"
USER = "windevase"
REPOS = ["infra-terraform", "gcp"]
FREE_MIN = 2000
FREE_GB = 0.5
MIN = "suffix: 분"  # 분을 시간·일로 바꾸지 않고 그대로
SUMMARY = f"{API}/users/{USER}/settings/billing/usage/summary"
USAGE = f"{API}/users/{USER}/settings/billing/usage"
# 이번 달 경과 시간(시간 단위, UTC) — 대시보드를 그릴 때 Grafana 가 채움
ELAPSED_H = "((${__to:date:D} - 1) * 24 + ${__to:date:H} + 1)"
MONTH_H = 730.5
# 무료 한도 차감 기준(Linux 환산) 분
LIN = {"selector": "sku == 'actions_windows' ? grossQuantity * 2 : (sku == 'actions_macos' ? grossQuantity * 10 : "
                   "(sku == 'actions_linux' ? grossQuantity : 0))", "text": "lin", "type": "number"}
GBH = {"selector": "sku == 'actions_storage' ? grossQuantity : 0", "text": "gbh", "type": "number"}

_ref = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def inf(url, root="", computed=None, summarize=None):
    q = {"datasource": INF, "refId": next(_ref), "type": "json", "source": "url", "parser": "backend",
         "format": "table", "url": url, "url_options": {"method": "GET", "data": ""}, "root_selector": root}
    if computed:
        q["computed_columns"] = computed
    if summarize:
        q["summarizeExpression"] = summarize
    return q


def summary_stat(panel):
    """Infinity 합계식 결과(summary 필드)의 값을 표시."""
    panel["options"]["reduceOptions"] = {"calcs": ["lastNotNull"], "fields": "/^summary$/", "values": False}
    return panel


d = Dashboard()

# ---------------- 1. 이번 달 ----------------
d.row("Actions 무료 한도 — 이번 달 (UTC 기준, GitHub Free 2,000분)")
p = gauge(INF, "Actions 사용 — 이번 달 / 월 2,000분 (Linux 환산)", [inf(SUMMARY, "usageItems", [LIN], "sum(lin)")], 0, d.y, w=6, h=6,
          unit=MIN, maxv=FREE_MIN, steps=[("green", None), ("orange", FREE_MIN * 0.7), ("red", FREE_MIN * 0.9)],
          desc="이번 달 Actions 실행 분(private 저장소). Linux 1배·Windows 2배·macOS 10배로 무료 2,000분에서 차감")
d.add(summary_stat(p))
d.add(summary_stat(stat(INF, "남은 분 — 이번 달 (매월 1일 UTC 초기화)", [inf(SUMMARY, "usageItems", [LIN], f"{FREE_MIN} - sum(lin)")], 6, d.y,
                        w=5, h=6, unit=MIN, decimals=0, graph=False,
                        steps=[("red", None), ("orange", 200), ("green", 600)],
                        desc="무료 2,000분 − 이번 달 사용(Linux 환산). 매월 1일(UTC) 초기화")))
d.add(summary_stat(stat(INF, "월말 예상 — 이번 달 (대략, 한도 월 2,000분)",
                        [inf(SUMMARY, "usageItems", [LIN], f"sum(lin) / {ELAPSED_H} * {MONTH_H}")], 11, d.y,
                        w=5, h=6, unit=MIN, decimals=0, graph=False,
                        steps=[("green", None), ("orange", FREE_MIN * 0.9), ("red", FREE_MIN)],
                        desc="지금까지 사용 속도로 한 달(평균 730.5시간)을 채웠을 때. 2,000분 넘으면 빨강")))
d.add(summary_stat(stat(INF, "청구 예정 금액 — 이번 달", [inf(SUMMARY, "usageItems", None, "sum(netAmount)")], 16, d.y,
                        w=4, h=6, unit="currencyUSD", decimals=2, graph=False,
                        steps=[("green", None), ("red", 0.01)],
                        desc="무료 차감 뒤 실제 청구될 금액(netAmount). 결제 수단·예산이 없으면 한도 초과 시 Actions 가 멈춘다")))
FREE_GBH = round(FREE_GB * MONTH_H, 1)
p = gauge(INF, f"저장 공간 — 이번 달 / 월 {FREE_GBH:.0f} GB-시간", [inf(SUMMARY, "usageItems", [GBH], "sum(gbh)")], 20, d.y,
          w=4, h=6, unit="suffix: GB-h", maxv=FREE_GBH, steps=[("green", None), ("orange", FREE_GBH * 0.7),
                                                             ("red", FREE_GBH * 0.9)],
          desc=f"Actions·Packages 저장 공간을 시간 단위로 누적한 값. 무료 500MB × 한 달 ≈ {FREE_GBH} GB-시간")
d.add(summary_stat(p))
d.y += 6
d.add(summary_stat(stat(INF, f"저장 공간 월말 예상 — 이번 달 (대략, 한도 월 {FREE_GBH:.0f} GB-시간)",
                        [inf(SUMMARY, "usageItems", [GBH], f"sum(gbh) / {ELAPSED_H} * {MONTH_H}")], 0, d.y,
                        w=6, h=4, unit="suffix: GB-h", decimals=0, graph=False,
                        steps=[("green", None), ("orange", FREE_GBH * 0.9), ("red", FREE_GBH)],
                        desc=f"지금 저장량이 월말까지 유지될 때. {FREE_GBH} GB-시간 넘으면 빨강 — 오래된 아티팩트 정리")))
d.add(summary_stat(stat(INF, "평균 저장량 — 이번 달 (무료 500MB)",
                        [inf(SUMMARY, "usageItems", [GBH], f"sum(gbh) / {ELAPSED_H}")], 6, d.y,
                        w=6, h=4, unit="decgbytes", decimals=2, graph=False,
                        desc="GB-시간 ÷ 이번 달 경과 시간. 순간 저장량이 아니라 평균")))
d.add(summary_stat(stat(INF, "Actions 일평균 사용 — 이번 달 (권장 하루 약 66분 이하)",
                        [inf(SUMMARY, "usageItems", [LIN], f"sum(lin) / {ELAPSED_H} * 24")], 12, d.y,
                        w=6, h=4, unit=MIN, decimals=0, graph=False,
                        desc=f"Linux 환산 분 ÷ 경과 일. 한 달 무료분을 고르게 쓰면 하루 약 {FREE_MIN * 24 / MONTH_H:.0f}분")))
d.add(summary_stat(stat(INF, "남은 비율 — 이번 달 (월 2,000분 기준)",
                        [inf(SUMMARY, "usageItems", [LIN], f"1 - sum(lin) / {FREE_MIN}")], 18, d.y,
                        w=6, h=4, unit="percentunit", decimals=0, graph=False,
                        steps=[("red", None), ("orange", 0.1), ("green", 0.3)],
                        desc="무료 2,000분 중 아직 쓰지 않은 비율")))
d.y += 4

# ---------------- 2. 내역 ----------------
d.row("내역 — 이번 달 (UTC)")
d.add(table(INF, "SKU 별 — 이번 달", [inf(SUMMARY, "usageItems")], 0, d.y, 12, 6, merge=False,
            exclude=["product", "netQuantity", "pricePerUnit"],
            renames={"sku": "SKU", "unitType": "단위", "grossQuantity": "사용량", "discountQuantity": "무료 차감",
                     "grossAmount": "정가($)", "discountAmount": "할인($)", "netAmount": "청구($)"},
            units={"정가($)": "currencyUSD", "할인($)": "currencyUSD", "청구($)": "currencyUSD"},
            desc="GitHub 결제 요약(이번 달). 사용량 단위: 분 / GB-시간"))
d.add(table(INF, "저장소 별 — 이번 달", [inf(USAGE, "usageItems")], 12, d.y, 12, 6, merge=False,
            exclude=["date", "product", "pricePerUnit", "discountAmount"],
            renames={"repositoryName": "저장소", "sku": "SKU", "quantity": "사용량", "unitType": "단위",
                     "grossAmount": "정가($)", "netAmount": "청구($)"},
            units={"정가($)": "currencyUSD", "청구($)": "currencyUSD"},
            sort=[{"displayName": "정가($)", "desc": True}],
            desc="이번 달 저장소·SKU 별 (계정의 모든 private 저장소 포함)"))
d.y += 6

# ---------------- 3. 기타 ----------------
d.row("캐시(현재 보관량) · API 한도(시간당)")
for k, r in enumerate(REPOS):
    p = stat(INF, f"Actions 캐시 — {r} (현재 / 저장소당 10GB)", [inf(f"{API}/repos/{USER}/{r}/actions/cache/usage")], 6 * k, d.y,
             w=6, h=4, unit="bytes", graph=False, desc="저장소당 무료 10GB (넘으면 오래된 캐시부터 삭제)")
    p["options"]["reduceOptions"] = {"calcs": ["lastNotNull"], "fields": "/^active_caches_size_in_bytes$/",
                                     "values": False}
    d.add(p)
p = gauge(INF, "API 요청 한도 사용 — 시간당 5,000회 (감시용 PAT)", [inf(f"{API}/rate_limit", "resources.core", None,
                                                  "sum(used) / sum(limit)")], 12, d.y, w=6, h=4,
          steps=[("green", None), ("orange", 0.7), ("red", 0.9)],
          desc="시간당 5,000회. Grafana GitHub 패널·감시 스크립트·cred-check 가 같은 PAT 를 쓴다")
d.add(summary_stat(p))
p = stat(INF, "API 남은 요청 — 이번 시간 (매시 초기화)", [inf(f"{API}/rate_limit", "resources.core")], 18, d.y, w=6, h=4, decimals=0,
         unit="locale", graph=False, steps=[("red", None), ("orange", 500), ("green", 1500)], desc="이번 시간 창에서 남은 요청 수")
p["options"]["reduceOptions"] = {"calcs": ["lastNotNull"], "fields": "/^remaining$/", "values": False}
d.add(p)
d.y += 4

dash = base("lab-github-usage", "Lab · GitHub 사용량",
            "GitHub Free 무료 한도 대비 사용량 — Actions 분(Linux 환산)·저장 공간·청구 예정·캐시·API 한도",
            ["lab", "github", "cost"], d.panels, time_from="now-1h",
            refresh="30m")  # 결제 데이터는 몇 시간 단위로 갱신 + API 한도 절약
write(dash, sys.argv[1])
