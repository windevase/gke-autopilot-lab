"""Generate Grafana dashboard JSON: Lab cluster performance (GKE Autopilot, no node-exporter)."""
import json, sys

DS = {"type": "prometheus", "uid": "prometheus"}
# 공개 대시보드는 템플릿 변수를 치환하지 않음 → 네임스페이스 고정 (2026-11-20, 이전엔 네임스페이스 선택 변수)
NS = 'namespace="monitoring"'
panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def tgt(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t


def row(title, y):
    panels.append({"type": "row", "title": title, "id": nid(), "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})


def stat(title, expr, x, y, w=3, h=4, unit="short", thresholds=None, desc="", decimals=None, graph=True):
    # graph=False: 추이선 없이 instant 쿼리 (24h 창·히스토그램처럼 무거운 식은 range로 돌리면 지점마다 재계산)
    steps = thresholds or [{"color": "green", "value": None}]
    p = {"type": "stat", "title": title, "id": nid(), "datasource": DS, "description": desc,
         "gridPos": {"h": h, "w": w, "x": x, "y": y},
         "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps},
                                      "color": {"mode": "thresholds"}}, "overrides": []},
         "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                     "colorMode": "background", "graphMode": "area" if graph else "none",
                     "textMode": "value", "justifyMode": "center"},
         "targets": [tgt(expr, instant=not graph)]}
    if decimals is not None:
        p["fieldConfig"]["defaults"]["decimals"] = decimals
    panels.append(p)


def ts(title, targets, x, y, w=8, h=8, unit="short", desc="", stack=False, overrides=None, fill=10, minv=None):
    custom = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": fill, "showPoints": "never",
              # 15분보다 긴 공백(오프아워에 꺼진 파드 등)은 끊어서 표시. 짧은 공백(재시작, 7일 범위 step)은 연결
              "spanNulls": 15 * 60 * 1000, "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    d = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if minv is not None:
        d["min"] = minv
    panels.append({"type": "timeseries", "title": title, "id": nid(), "datasource": DS, "description": desc,
                   "gridPos": {"h": h, "w": w, "x": x, "y": y},
                   "fieldConfig": {"defaults": d, "overrides": overrides or []},
                   "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max", "lastNotNull"]},
                               "tooltip": {"mode": "multi", "sort": "desc"}},
                   "targets": targets})


def bargauge(title, expr, legend, x, y, w=6, h=8, unit="percentunit", maxv=1, desc="", steps=None):
    steps = steps or [{"color": "green", "value": None}, {"color": "orange", "value": 0.7}, {"color": "red", "value": 0.9}]
    panels.append({"type": "bargauge", "title": title, "id": nid(), "datasource": DS, "description": desc,
                   "gridPos": {"h": h, "w": w, "x": x, "y": y},
                   "fieldConfig": {"defaults": {"unit": unit, "min": 0, "max": maxv,
                                                "thresholds": {"mode": "absolute", "steps": steps},
                                                "color": {"mode": "thresholds"}}, "overrides": []},
                   "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                               "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
                   "targets": [tgt(expr, legend, instant=True)]})


def table(title, targets, x, y, w, h, renames, units=None, mappings=None, desc="", sort=None):
    overrides = []
    for field, unit in (units or {}).items():
        overrides.append({"matcher": {"id": "byName", "options": field},
                          "properties": [{"id": "unit", "value": unit}]})
    for field, mp in (mappings or {}).items():
        overrides.append({"matcher": {"id": "byName", "options": field},
                          "properties": [{"id": "mappings", "value": mp},
                                         {"id": "custom.cellOptions", "value": {"type": "color-background"}}]})
    excl = {"Time": True, "__name__": True}
    panels.append({"type": "table", "title": title, "id": nid(), "datasource": DS, "description": desc,
                   "gridPos": {"h": h, "w": w, "x": x, "y": y},
                   "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides},
                   "options": {"showHeader": True, "sortBy": sort or []},
                   "targets": targets,
                   "transformations": [
                       {"id": "merge", "options": {}},
                       {"id": "organize", "options": {"excludeByName": excl, "renameByName": renames}}]})


RED_IF_POS = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
CPU_M = 'sum by ({by}) (rate(container_cpu_usage_seconds_total{{container!=""{sel}}}[5m])) * 1000'
MEM = 'sum by ({by}) (container_memory_working_set_bytes{{container!=""{sel}}})'
RUNNING = 'max by (namespace, pod) (kube_pod_status_phase{phase="Running"}) == 1'
REQ = ('sum by ({by}) (max by (namespace, pod, container, node) (kube_pod_container_resource_requests{{resource="{res}"}})'
       ' * on (namespace, pod) group_left () (' + RUNNING.replace('{', '{{').replace('}', '}}') + '))')

y = 0
# ---------------- 1. 개요 ----------------
row("클러스터 개요", y); y += 1
stat("노드", 'count(max by (node) (kube_node_info))', 0, y, desc="Autopilot이 자동으로 늘리고 줄이는 노드 수 (노드 자체는 과금 안 됨)")
stat("실행 중 파드", 'sum(' + RUNNING + ')', 3, y, desc="전체 네임스페이스 (시스템 파드 포함)")
stat("Pending 파드", 'sum(max by (namespace, pod) (kube_pod_status_phase{phase="Pending"})) or vector(0)', 6, y,
     thresholds=RED_IF_POS, desc="스케줄 대기. Autopilot은 노드 프로비저닝 중 잠깐 Pending일 수 있음")
stat("재시작 (24h)", 'sum(max by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total[24h]))) or vector(0)', 9, y,
     thresholds=RED_IF_POS, decimals=0, graph=False)
# Autopilot에선 cAdvisor container_oom_events_total이 항상 0 → KSM 종료 사유의 24시간 이력 (파드 교체 후에도 집계)
stat("OOM (24h)", 'count(max by (namespace, pod, container) (max_over_time('
     'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}[24h])) == 1) or vector(0)', 12, y,
     thresholds=RED_IF_POS, decimals=0, desc="24시간 안에 OOMKilled로 종료된 컨테이너 수 (KSM 종료 사유 기준)", graph=False)
stat("API 5xx 비율", 'sum(lab:apiserver_request:rate5m{code=~"5.."}) / sum(lab:apiserver_request:rate5m) or vector(0)', 15, y,
     unit="percentunit", decimals=2,
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.01}, {"color": "red", "value": 0.05}])
stat("API p99 지연", 'histogram_quantile(0.99, sum by (le) (lab:apiserver_request_sli_duration_seconds_bucket:rate5m))', 18, y,
     unit="s", decimals=2, desc="버킷을 줄여 둬서(0.05/0.1/1/5s…) 근사값", graph=False,
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 5}])
stat("수집 대상 정상", 'sum(up) / count(up)', 21, y, unit="percentunit", decimals=0,
     thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.9}, {"color": "green", "value": 1}])
y += 4

# ---------------- 2. 노드 ----------------
row("노드", y); y += 1
ts("노드별 CPU 사용", [tgt(CPU_M.format(by="node", sel=""), "{{node}}")], 0, y, unit="suffix:m",
   desc="노드 위 모든 컨테이너 사용량 합 (node-exporter가 없어 OS 오버헤드 제외)", minv=0)
ts("노드별 메모리 사용 (working set)", [tgt(MEM.format(by="node", sel=""), "{{node}}")], 8, y, unit="bytes", minv=0)
bargauge("CPU requests / 할당 가능", REQ.format(by="node", res="cpu") + ' / max by (node) (kube_node_status_allocatable{resource="cpu"})',
         "{{node}}", 16, y, w=4, desc="스케줄링 관점의 노드 점유율")
bargauge("메모리 requests / 할당 가능", REQ.format(by="node", res="memory") + ' / max by (node) (kube_node_status_allocatable{resource="memory"})',
         "{{node}}", 20, y, w=4)
y += 8
table("노드 목록", [
    tgt('max by (node, kubelet_version) (kube_node_info)', ref="A", instant=True, fmt="table"),
    tgt('time() - max by (node) (kube_node_created)', ref="B", instant=True, fmt="table"),
    tgt('max by (node) (kube_node_status_allocatable{resource="cpu"})', ref="C", instant=True, fmt="table"),
    tgt('max by (node) (kube_node_status_allocatable{resource="memory"})', ref="D", instant=True, fmt="table"),
    tgt('count by (node) (max by (namespace, pod, node) (kube_pod_info) * on (namespace, pod) group_left () (' + RUNNING + '))',
        ref="E", instant=True, fmt="table"),
], 0, y, 24, 6,
    renames={"node": "노드", "kubelet_version": "kubelet", "Value #A": "_", "Value #B": "수명",
             "Value #C": "할당 CPU (core)", "Value #D": "할당 메모리", "Value #E": "실행 파드"},
    units={"수명": "s", "할당 메모리": "bytes"},
    desc="수명이 짧으면 Spot 회수나 Autopilot 재배치로 교체된 노드")
panels[-1]["transformations"][1]["options"]["excludeByName"]["Value #A"] = True
y += 6

# ---------------- 3. 워크로드 ----------------
row("워크로드 (monitoring)", y); y += 1
sel = ', ' + NS
ts("파드별 CPU 사용", [tgt(CPU_M.format(by="pod", sel=sel), "{{pod}}")], 0, y, w=12, unit="suffix:m", minv=0)
ts("파드별 메모리 사용 (working set)", [tgt(MEM.format(by="pod", sel=sel), "{{pod}}")], 12, y, w=12, unit="bytes", minv=0)
y += 8
ts("CPU 스로틀링 비율", [tgt(
    'sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{container!="", ' + NS + '}[5m]))'
    ' / sum by (pod) (rate(container_cpu_cfs_periods_total{container!="", ' + NS + '}[5m]))', "{{pod}}")],
   0, y, unit="percentunit", minv=0, desc="CPU limit에 걸린 비율. 높으면 응답 지연 원인")
ts("네트워크 (수신 + / 송신 −)", [
    tgt('sum by (pod) (rate(container_network_receive_bytes_total{' + NS + '}[5m]))', "{{pod}} 수신", "A"),
    tgt('-sum by (pod) (rate(container_network_transmit_bytes_total{' + NS + '}[5m]))', "{{pod}} 송신", "B")],
   8, y, unit="Bps")
ts("디스크 I/O (읽기 + / 쓰기 −)", [
    tgt('sum by (pod) (rate(container_fs_reads_bytes_total{container!="", ' + NS + '}[5m]))', "{{pod}} 읽기", "A"),
    tgt('-sum by (pod) (rate(container_fs_writes_bytes_total{container!="", ' + NS + '}[5m]))', "{{pod}} 쓰기", "B")],
   16, y, unit="Bps")
y += 8
table("파드 상태", [
    tgt('max by (namespace, pod, node) (kube_pod_info{' + NS + '}) * on (namespace, pod) group_left () (' + RUNNING + ')',
        ref="A", instant=True, fmt="table"),
    tgt('time() - max by (namespace, pod) (kube_pod_start_time{' + NS + '})', ref="B", instant=True, fmt="table"),
    tgt('sum by (namespace, pod) (max by (namespace, pod, container) (kube_pod_container_status_restarts_total{' + NS + '}))',
        ref="C", instant=True, fmt="table"),
    tgt(CPU_M.format(by="namespace, pod", sel=sel), ref="D", instant=True, fmt="table"),
    tgt(MEM.format(by="namespace, pod", sel=sel), ref="E", instant=True, fmt="table"),
], 0, y, 24, 7,
    renames={"namespace": "네임스페이스", "pod": "파드", "node": "노드", "Value #B": "수명", "Value #C": "재시작",
             "Value #D": "CPU (m)", "Value #E": "메모리"},
    units={"수명": "s", "메모리": "bytes"},
    desc="Running 파드만 표시")
panels[-1]["transformations"][1]["options"]["excludeByName"]["Value #A"] = True
panels[-1]["transformations"].append({"id": "filterByValue", "options": {
    "type": "exclude", "match": "any",
    "filters": [{"fieldName": "파드", "config": {"id": "isNull", "options": {}}}]}})
y += 7

# ---------------- 4. 스토리지 ----------------
row("스토리지 (PV)", y); y += 1
bargauge("PV 사용률",
         'max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes) / max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_capacity_bytes)',
         "{{persistentvolumeclaim}}", 0, y, w=8, h=7)
ts("PV 사용량 추이", [tgt('max by (persistentvolumeclaim) (kubelet_volume_stats_used_bytes)', "{{persistentvolumeclaim}}")],
   8, y, w=16, h=7, unit="bytes", minv=0)
y += 7

# ---------------- 5. 컨트롤 플레인 ----------------
row("컨트롤 플레인 (API 서버)", y); y += 1
ts("요청률 (verb별)", [tgt('sum by (verb) (lab:apiserver_request:rate5m)', "{{verb}}")], 0, y, unit="reqps", minv=0)
ts("요청률 (응답 코드별)", [tgt('sum by (code) (lab:apiserver_request:rate5m)', "{{code}}")], 8, y, unit="reqps", minv=0)
ts("요청 지연 (WATCH 제외, 근사)", [
    tgt('histogram_quantile(0.50, sum by (le) (lab:apiserver_request_sli_duration_seconds_bucket:rate5m))', "p50", "A"),
    tgt('histogram_quantile(0.90, sum by (le) (lab:apiserver_request_sli_duration_seconds_bucket:rate5m))', "p90", "B"),
    tgt('histogram_quantile(0.99, sum by (le) (lab:apiserver_request_sli_duration_seconds_bucket:rate5m))', "p99", "C")],
   16, y, unit="s", minv=0, fill=0, desc="le 버킷을 줄여 둬서 계단 모양 근사값")
y += 8

# ---------------- 6. 모니터링 스택 ----------------
row("모니터링 스택 (Prometheus)", y); y += 1
ts("활성 시계열 & 수집 속도", [
    # 레플리카 2대 → 레플리카 1대 기준(max)으로 표시
    tgt('max(prometheus_tsdb_head_series)', "활성 시계열", "A"),
    tgt('max(rate(prometheus_tsdb_head_samples_appended_total[5m]))', "샘플/초", "B")],
   0, y, unit="short", minv=0,
   overrides=[{"matcher": {"id": "byName", "options": "샘플/초"},
               "properties": [{"id": "custom.axisPlacement", "value": "right"}]}])
ts("수집 소요 시간 (job별)", [tgt('max by (job) (scrape_duration_seconds)', "{{job}}")], 8, y, unit="s", minv=0, fill=0)
table("수집 대상 상태", [tgt('max by (job, instance) (up)', ref="A", instant=True, fmt="table")], 16, y, 8, 8,
      renames={"job": "job", "instance": "instance", "Value": "상태"},
      mappings={"상태": [{"type": "value", "options": {
          "1": {"text": "UP", "color": "green", "index": 0},
          "0": {"text": "DOWN", "color": "red", "index": 1}}}]},
      sort=[{"displayName": "상태", "desc": False}])
y += 8

dashboard = {
    "uid": "lab-cluster-performance",
    "title": "Lab · 클러스터 성능",
    "description": "GKE Autopilot 클러스터 성능 (node-exporter 없이 kubelet/cAdvisor/KSM/apiserver 지표)",
    "tags": ["lab", "cluster", "performance"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "graphTooltip": 1,
    "refresh": "1m",
    "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": []},
    "annotations": {"list": []},
    "panels": panels,
}

out = sys.argv[1]
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"wrote {out}: {len(panels)} panels")
