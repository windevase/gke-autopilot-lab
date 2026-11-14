"""example.com 존 WAF: atlantis 호스트 예외 + GitHub 웹훅 전용 규칙 (멱등). GitHub hooks IP 가 바뀌면 다시 실행.
사용: python -I tools/waf-atlantis.py (dry-run) / --apply (적용). 2026-11-13 최초 적용. 토큰은 secret/cloudflare_api 9번째 줄(출력 안 함)."""
import json, re, sys, urllib.request

HOST = "atlantis.example.com"
NEW_DESC = "Atlantis GitHub webhooks only"
APPLY = "--apply" in sys.argv

token = open(r"C:\gcp\secret\cloudflare_api", encoding="utf-8").read().splitlines()[8].strip()
zone = re.search(r'cloudflare_zone_id\s*=\s*"([^"]+)"', open(r"C:\gcp\terraform.tfvars", encoding="utf-8").read()).group(1)


def api(method, path, body=None):
    req = urllib.request.Request(
        "https://api.cloudflare.com/client/v4" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        d = json.load(r)
    if not d.get("success"):
        raise SystemExit(f"API 실패: {d.get('errors')}")
    return d["result"]


def gh_hook_ips():
    with urllib.request.urlopen("https://api.github.com/meta") as r:
        return json.load(r)["hooks"]


rs = api("GET", f"/zones/{zone}/rulesets/phases/http_request_firewall_custom/entrypoint")
rules = {r.get("description"): r for r in rs["rules"]}
kr = rules["Allow Korea Only"]
ips = " ".join(gh_hook_ips())

plan = []
excl = f'http.host ne "{HOST}"'
if excl not in kr["expression"]:
    plan.append(("PATCH", f"/zones/{zone}/rulesets/{rs['id']}/rules/{kr['id']}",
                 {"action": kr["action"], "description": kr["description"], "enabled": kr.get("enabled", True),
                  "expression": f'({kr["expression"]}) and {excl}'}))
new_expr = (f'(http.host eq "{HOST}" and (not ip.src in {{{ips}}} '
            f'or http.request.uri.path ne "/events" or http.request.method ne "POST"))')
if NEW_DESC in rules:
    r = rules[NEW_DESC]
    if r["expression"] != new_expr:
        plan.append(("PATCH", f"/zones/{zone}/rulesets/{rs['id']}/rules/{r['id']}",
                     {"action": "block", "description": NEW_DESC, "enabled": True, "expression": new_expr}))
else:
    plan.append(("POST", f"/zones/{zone}/rulesets/{rs['id']}/rules",
                 {"action": "block", "description": NEW_DESC, "enabled": True, "expression": new_expr}))

if not plan:
    print("변경 없음 (이미 적용됨)")
for m, p, b in plan:
    print(f"[{m}] {b['description']}\n    {b['expression']}")
if APPLY:
    for m, p, b in plan:
        api(m, p, b)
    after = api("GET", f"/zones/{zone}/rulesets/phases/http_request_firewall_custom/entrypoint")
    print("\n적용 후 규칙:")
    for r in after["rules"]:
        print(f"- {r.get('description')} | {r['action']} | enabled={r.get('enabled')}")
