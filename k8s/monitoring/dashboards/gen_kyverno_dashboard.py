"""Lab · Kyverno 정책 결과 대시보드 생성기.
사용: python gen_kyverno_dashboard.py lab-kyverno.json
데이터: kube-state-metrics customResourceState 가 PolicyReport(wgpolicyk8s.io)를 지표로 만든 kyverno_policyreport_result
  (설정은 k8s/monitoring/kps/values.yaml). 결과 1건 = 시계열 1개, 값 = 마지막 평가 시각(epoch 초)
  레이블: namespace, scope_kind, scope_name, policy, rule, result(pass·fail·warn·error·skip), severity, category, message
보고서는 Kyverno 가 꺼진 오프아워에도 남아 있어 24h KSM 이 계속 노출한다 → 밤에는 "마지막 스캔 시점" 기준 결과.
KSM 교체 순간 시계열이 겹칠 수 있으므로 집계 전에 max by (결과 식별 레이블) 로 중복 제거.
"""
import sys
from _dashlib import PROM, Dashboard, base, prom, stat, table, text, timeseries, value_map, write

KEY = "namespace, scope_kind, scope_name, policy, rule, result"
R = "kyverno_policyreport_result"


def uniq(sel=""):
    """결과 1건당 시계열 1개 (KSM 중복 제거)."""
    return f"max by ({KEY}) ({R}{sel})"


def cnt(sel="", by=""):
    grp = f" by ({by})" if by else ""
    return f"count{grp} ({uniq(sel)})"


def res(r):
    """result 레이블 선택자. 정규식이면 =~"""
    op = "=~" if "|" in r else "="
    return f'{{result{op}"{r}"}}'


BAD = res("fail|warn|error")
FAIL_STEPS = [("green", None), ("red", 1)]

d = Dashboard()

INTRO = """\
**정책 = 코드** (`k8s/kyverno/policies/`): ephemeral-storage 요청 필수(비용) · 이미지 digest/버전 고정 · restricted 보안 설정 · Spot 노드(비용) · lab-api 이미지 cosign 서명.
**PR 단계**에서 CI(Kyverno CLI)가 매니페스트·차트 렌더링 결과를 검사해 위반이면 merge 차단 → **클러스터**에서는 Audit 모드로 실행 중인 리소스를 다시 검사해 정책 보고서로 남긴다(차단 없음).
Kyverno 는 평일 10~20시만 가동 — 그 밖의 시간에는 마지막 스캔 결과를 보여 준다."""
d.add(text("", INTRO, 0, d.y, w=24, h=4))
d.y += 4

# ---------------- 1. 요약 ----------------
d.row("요약 (클러스터 정책 보고서)")
d.add(stat(PROM, "위반 (fail)", [prom(f'{cnt(res("fail"))} or vector(0)')],
           0, d.y, w=4, decimals=0, graph=False, steps=FAIL_STEPS, desc="Audit 모드라 차단되지 않고 기록만 된다. 0 이 정상"))
d.add(stat(PROM, "경고·오류", [prom(f'{cnt(res("warn|error"))} or vector(0)')],
           4, d.y, w=3, decimals=0, graph=False, steps=[("green", None), ("orange", 1)],
           desc="error = 정책 평가 자체가 실패 (예: CEL 식이 필드 없음을 처리 못 함)"))
d.add(stat(PROM, "통과 (pass)", [prom(f'{cnt(res("pass"))} or vector(0)')],
           7, d.y, w=3, decimals=0, graph=False, color_mode="none"))
d.add(stat(PROM, "검사한 리소스", [prom(f'count(count by (namespace, scope_kind, scope_name) ({uniq()}))')],
           10, d.y, w=4, decimals=0, graph=False, color_mode="none",
           desc="정책 보고서 1개 = 리소스 1개 (Pod·ReplicaSet·Deployment·StatefulSet·Job·CronJob·Rollout …)"))
d.add(stat(PROM, "정책 수", [prom(f'count(count by (policy) ({uniq()}))')], 14, d.y, w=3, decimals=0, graph=False,
           color_mode="none"))
d.add(stat(PROM, "마지막 스캔 경과", [prom(f'time() - max({R})')], 17, d.y, w=4, unit="s", decimals=1, graph=False,
           steps=[("green", None), ("orange", 86400), ("red", 3 * 86400)],
           desc="가장 최근 정책 평가 이후 지난 시간. 평일 밤·주말에는 Kyverno 가 꺼져 길어지는 게 정상"))
d.add(stat(PROM, "Kyverno", [prom('sum(kube_deployment_status_replicas_available{namespace="kyverno"})')], 21, d.y, w=3,
           decimals=0, graph=False, steps=[("green", None)],
           mappings=value_map({"0": ("꺼짐 (오프아워)", "gray")}),
           desc="admission·background·reports 컨트롤러 가동 파드 수 (평일 10~20시 3)"))
d.y += 4

# ---------------- 2. 정책별 ----------------
d.row("정책별 결과")
RESULTS = [("pass", "통과"), ("fail", "위반"), ("warn", "경고"), ("error", "오류"), ("skip", "건너뜀")]
d.add(table(PROM, "정책별 결과 수",
            [prom(cnt(res(r), "policy"), ref=chr(65 + i), instant=True,
                  fmt="table") for i, (r, _) in enumerate(RESULTS)],
            0, d.y, w=12, h=8,
            renames={"policy": "정책", **{f"Value #{chr(65 + i)}": n for i, (_, n) in enumerate(RESULTS)}},
            mappings={"위반": [{"type": "range", "options": {"from": 1, "to": 1e9, "result": {"color": "red", "index": 0}}}]},
            sort=[{"displayName": "위반", "desc": True}], desc="빈 칸 = 0건"))
d.add(timeseries(PROM, "결과 추이", [prom(cnt(by="result"), "{{result}}")], 12, d.y, w=12, h=8, stack=True, minv=0,
                 desc="결과 건수. 오프아워에는 마지막 보고서가 그대로 유지된다(평평한 구간)"))
d.y += 8

# ---------------- 3. 위반 목록 ----------------
d.row("위반·경고 목록")
d.add(table(PROM, "위반·경고 (fail·warn·error)",
            [prom(f'max by ({KEY}, severity, message) ({R}{BAD})', instant=True, fmt="table")],
            0, d.y, w=24, h=8,
            renames={"namespace": "네임스페이스", "scope_kind": "종류", "scope_name": "리소스", "policy": "정책",
                     "rule": "규칙", "result": "결과", "severity": "심각도", "message": "메시지", "Value": "평가 시각"},
            units={"평가 시각": "dateTimeAsLocal"},
            mappings={"결과": value_map({"fail": ("위반", "red"), "warn": ("경고", "orange"), "error": ("오류", "purple")})},
            desc="비어 있으면 정상. 고친 뒤 다음 background 스캔(1시간 주기)에 사라진다"))
d.y += 8

# ---------------- 4. 범위 ----------------
d.row("검사 범위")
d.add(table(PROM, "네임스페이스별",
            [prom(f'count by (namespace) (count by (namespace, scope_kind, scope_name) ({uniq()}))', ref="A", instant=True,
                  fmt="table"),
             prom(cnt("", "namespace"), ref="B", instant=True, fmt="table"),
             prom(cnt(BAD, "namespace"), ref="C", instant=True, fmt="table")],
            0, d.y, w=12, h=8,
            renames={"namespace": "네임스페이스", "Value #A": "리소스", "Value #B": "결과", "Value #C": "위반·경고"},
            sort=[{"displayName": "리소스", "desc": True}]))
d.add(table(PROM, "리소스 종류별",
            [prom(f'count by (scope_kind) (count by (namespace, scope_kind, scope_name) ({uniq()}))', ref="A", instant=True,
                  fmt="table"),
             prom(cnt("", "scope_kind"), ref="B", instant=True, fmt="table")],
            12, d.y, w=12, h=8, renames={"scope_kind": "종류", "Value #A": "리소스", "Value #B": "결과"},
            sort=[{"displayName": "리소스", "desc": True}],
            desc="Pod 를 만드는 상위 객체(ReplicaSet·Deployment 등)도 각각 검사된다"))
d.y += 8

write(base("lab-kyverno", "Lab · Kyverno 정책 결과",
           "Kyverno 정책 보고서(Audit) — 정책별 통과·위반, 위반 목록, 검사 범위. 데이터: KSM customResourceState(PolicyReport)",
           ["lab", "kyverno", "policy"], d.panels, time_from="now-7d", refresh="5m"), sys.argv[1])
