"""Grafana 대시보드 JSON 생성용 공통 함수 (gen_ops_dashboard.py, gen_billing_dashboard.py에서 사용)."""
import json

PROM = {"type": "prometheus", "uid": "prometheus"}
BQ = {"type": "grafana-bigquery-datasource", "uid": "bigquery-billing"}


class Dashboard:
    def __init__(self):
        self.panels = []
        self._id = 0
        self.y = 0

    def nid(self):
        self._id += 1
        return self._id

    def add(self, panel):
        panel["id"] = self.nid()
        self.panels.append(panel)
        return panel

    def row(self, title):
        self.add({"type": "row", "title": title, "collapsed": False, "panels": [],
                  "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}})
        self.y += 1


def prom(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t


def bq(sql, ref="A", table=True):
    """BigQuery 쿼리. 프로젝트는 데이터소스 defaultProject 사용 (코드에 프로젝트 ID 없음)."""
    return {"datasource": BQ, "refId": ref, "rawSql": sql, "rawQuery": True, "editorMode": "code",
            "format": 1 if table else 0, "location": "US"}


# --- BigQuery 결제 내보내기 공통 SQL 조각 (gen_billing_dashboard.py, gen_home_dashboard.py) ---
BILL_T = "`billing_export.gcp_billing_export_v1_*`"
BILL_MONTH = "invoice.month = FORMAT_TIMESTAMP('%Y%m', CURRENT_TIMESTAMP(), 'America/Los_Angeles')"
BILL_CRED = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)"
BILL_FREE = "IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c WHERE c.type = 'FREE_TIER'), 0)"
# 이번 달 경과 비율 (월말 예상치 계산용)
BILL_ELAPSED = ("TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH, 'America/Los_Angeles'), SECOND)"
                " / (86400 * EXTRACT(DAY FROM LAST_DAY(CURRENT_DATE('America/Los_Angeles'))))")


# --- Cloudflare Tunnel 서버 오류율 ---
# cloudflared_tunnel_request_errors는 브라우저가 요청을 취소한 경우(context canceled)까지 세서 부정확
# 501은 Grafana 13의 미구현 quota API(quotas.grafana.app) 응답이라 제외
# cloudflared 카운터는 recording rule(lab:*, values.yaml additionalPrometheusRulesMap)로 미리 rate 계산
# → 이름이 _total로 안 끝나는 카운터에 rate()를 직접 걸 때 뜨는 PromQL info 경고 제거
TUNNEL_5XX = ('(sum(lab:cloudflared_tunnel_response_by_code:rate5m{status_code=~"5..",status_code!="501"})'
              ' / sum(lab:cloudflared_tunnel_response_by_code:rate5m)) or vector(0)')
TUNNEL_RPM = 'sum(lab:cloudflared_tunnel_requests:rate5m) * 60'
# OOM: Autopilot에선 cAdvisor container_oom_events_total이 항상 0 → KSM 종료 사유의 24시간 이력으로 집계
# (OOM 후 파드가 교체되면 현재값에서는 사라지므로 max_over_time 필요)
OOM_24H = ('count(max by (namespace, pod, container) (max_over_time('
           'kube_pod_container_status_last_terminated_reason{{reason="OOMKilled"{sel}}}[24h])) == 1) or vector(0)')
OOM_DESC = "24시간 안에 OOMKilled로 종료된 컨테이너 수 (KSM 종료 사유 기준)"

# 컨테이너 메모리 사용 / limit (OOM 직전 신호). KSM 교체 시 중복 → max by로 정리
MEM_RATIO = ('max by (namespace, pod, container) (container_memory_working_set_bytes{{namespace=~"{ns}", container!=""}})'
             ' / on (namespace, pod, container) max by (namespace, pod, container) '
             '(kube_pod_container_resource_limits{{namespace=~"{ns}", resource="memory"}})')
MEM_STEPS = [("green", None), ("orange", 0.8), ("red", 0.9)]

# Argo CD: Sync가 아니거나 Healthy가 아닌 앱 수. Argo가 꺼진 오프아워에는 결과 없음 → noValue
GITOPS_BAD = ('count(max by (name) (argocd_app_info{sync_status!="Synced"}) '
              'or max by (name) (argocd_app_info{health_status!="Healthy"})) '
              'or (count(max by (name) (argocd_app_info)) * 0)')
TUNNEL_5XX_DESC = ("오리진(Grafana) 5xx 응답 비율. 브라우저 요청 취소(context canceled)와 "
                   "Grafana 미구현 API의 501은 제외")


def text(title, markdown, x, y, w=24, h=4):
    return {"type": "text", "title": title, "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "options": {"mode": "markdown", "content": markdown}}


def thresholds(steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


GREEN = [("green", None)]


def _instant(targets):
    """Prometheus 타깃을 instant 쿼리로 (마지막 값 하나만 필요한 패널 → 시간 범위 전체 계산 생략)."""
    for t in targets:
        if "expr" in t:
            t["instant"], t["range"] = True, False
    return targets


def stat(ds, title, targets, x, y, w=4, h=4, unit="short", steps=None, desc="", decimals=None,
         color_mode="background", graph=True, mappings=None, no_value=None):
    # 추이선(sparkline)이 없으면 instant 쿼리로 (range 쿼리는 ~800개 지점을 전부 계산)
    if not graph:
        _instant(targets)
    d = {"unit": unit, "thresholds": thresholds(steps or GREEN), "color": {"mode": "thresholds"}}
    if decimals is not None:
        d["decimals"] = decimals
    if mappings:
        d["mappings"] = mappings
    if no_value is not None:
        d["noValue"] = no_value  # 결과가 없을 때 표시할 문구 (예: 오프아워에 꺼진 컴포넌트)
    return {"type": "stat", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "area" if graph else "none",
                        "textMode": "value", "justifyMode": "center"},
            "targets": targets}


def gauge(ds, title, targets, x, y, w=4, h=4, unit="percentunit", maxv=1, steps=None, desc=""):
    _instant(targets)  # 게이지는 항상 현재 값 하나
    return {"type": "gauge", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": maxv,
                                         "thresholds": thresholds(steps or GREEN),
                                         "color": {"mode": "thresholds"}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "showThresholdMarkers": True},
            "targets": targets}


# 공백 연결 기준: 15분보다 긴 공백은 끊어서 표시 (오프아워에 꺼진 cloudflared·Grafana 구간을 직선으로 잇지 않도록)
# 짧은 공백(재시작 2~5분, 7일 범위의 10분 step)은 이어서 표시. 값은 ms
SPAN_NULLS_MS = 15 * 60 * 1000


def timeseries(ds, title, targets, x, y, w=8, h=8, unit="short", desc="", stack=False, bars=False,
               fill=10, minv=None, overrides=None, decimals=None):
    # BigQuery(일 단위 지점)는 지점 간격이 24시간이라 항상 연결
    span = True if ds is BQ else SPAN_NULLS_MS
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1, "fillOpacity": 80 if bars else fill,
              "showPoints": "never", "spanNulls": span,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    d = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if minv is not None:
        d["min"] = minv
    if decimals is not None:
        d["decimals"] = decimals
    return {"type": "timeseries", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": d, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "table", "placement": "bottom",
                                   "calcs": ["mean", "max", "lastNotNull"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": targets}


def bargauge(ds, title, targets, x, y, w=8, h=6, unit="percentunit", maxv=1, steps=None, desc=""):
    _instant(targets)  # 현재 값 순위
    return {"type": "bargauge", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": maxv,
                                         "thresholds": thresholds(steps or GREEN),
                                         "color": {"mode": "thresholds"}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "left", "sizing": "auto"},
            "targets": targets}


def state_timeline(ds, title, targets, x, y, w=24, h=8, mappings=None, desc=""):
    return {"type": "state-timeline", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"mappings": mappings or [], "color": {"mode": "thresholds"},
                                         "thresholds": thresholds(GREEN),
                                         "custom": {"fillOpacity": 80, "lineWidth": 0}},
                            "overrides": []},
            "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8,
                        "legend": {"showLegend": False}, "tooltip": {"mode": "single"}},
            "targets": targets}


def table(ds, title, targets, x, y, w, h, renames=None, units=None, mappings=None, desc="",
          merge=True, exclude=None, sort=None):
    overrides = []
    for field, unit in (units or {}).items():
        overrides.append({"matcher": {"id": "byName", "options": field},
                          "properties": [{"id": "unit", "value": unit}]})
    for field, mp in (mappings or {}).items():
        overrides.append({"matcher": {"id": "byName", "options": field},
                          "properties": [{"id": "mappings", "value": mp},
                                         {"id": "custom.cellOptions", "value": {"type": "color-background"}}]})
    excl = {"Time": True, "__name__": True}
    excl.update({k: True for k in (exclude or [])})
    transformations = []
    if merge:
        transformations.append({"id": "merge", "options": {}})
    transformations.append({"id": "organize", "options": {"excludeByName": excl, "renameByName": renames or {}}})
    return {"type": "table", "title": title, "datasource": ds, "description": desc,
            "gridPos": {"h": h, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides},
            "options": {"showHeader": True, "sortBy": sort or []},
            "targets": targets, "transformations": transformations}


def value_map(pairs):
    """{"0": ("정지", "gray")} → Grafana value mapping."""
    opts = {k: {"text": t, "color": c, "index": i} for i, (k, (t, c)) in enumerate(pairs.items())}
    return [{"type": "value", "options": opts}]


def range_map(ranges):
    """[(from, to, text, color)] → range mappings."""
    return [{"type": "range", "options": {"from": f, "to": t, "result": {"text": txt, "color": c, "index": i}}}
            for i, (f, t, txt, c) in enumerate(ranges)]


def write(dashboard: dict, out: str):
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {out}: {len(dashboard['panels'])} panels")


def base(uid, title, desc, tags, panels, templating=None, time_from="now-6h", refresh="1m"):
    """refresh: 데이터 갱신 주기에 맞춤 (BigQuery는 쿼리당 최소 10MB 과금 → 결제 대시보드는 길게)."""
    return {"uid": uid, "title": title, "description": desc, "tags": tags, "timezone": "browser",
            "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1, "refresh": refresh,
            "time": {"from": time_from, "to": "now"}, "templating": {"list": templating or []},
            "annotations": {"list": []}, "panels": panels}
