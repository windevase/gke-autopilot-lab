#!/usr/bin/env python3
"""채팅 승인용 '적용 내용 보고' 초안을 만든다 (CLAUDE.md 2.5).

  python -I tools/pr-report.py <PR 번호> [--repo owner/name] [--allow-closed]

원본: windevase/infra-terraform — windevase/gcp 에 같은 내용 사본(CI 가 동일 여부 확인, 원본을 먼저 고친다)
읽는 것: PR 정보(제목·본문 템플릿 항목·HEAD), HEAD 커밋의 atlantis/plan 상태, 최신 Atlantis "Ran Plan" 댓글
         (terraform plan 원문 + plan-guard 보고: 건수 표·감시 결과·변경 리소스 목록)
출력: 채팅에 붙일 보고 초안 + chat-approve 명령. 데이터 유무(S3 객체 수 등)·이름 근거·기본값 설명은 Claude 가 채운다.
"""
import json
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

KEY_ATTRS = re.compile(
    r'^\s*[~+-]?\s*(name|bucket|instance_type|ami|cidr_blocks|cidr_ipv4|source_ranges|from_port|to_port|protocol|'
    r'role|member|policy_arn|dataset_id|location|force_destroy|public_access_prevention|deletion_protection|'
    r'"Name"|"Environment"|"Request"|"ChangeRequest"|"RequesterTeam")\s+=')


def gh_bin():
    return shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def gh(*args):
    r = subprocess.run([gh_bin(), *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        sys.exit(f"gh 실패: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def origin_repo():
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    return re.sub(r"^(https://github\.com/|git@github\.com:)|\.git$", "", url)


def field(body, label):
    m = re.search(rf"^- {re.escape(label)}:\s*(.+)$", body or "", re.M)
    if not m:
        return "—"
    v = re.sub(r"<!--.*?-->", "", m.group(1)).strip()
    return v or "—"


def main():
    args = sys.argv[1:]
    if not args or not args[0].isdigit():
        sys.exit(__doc__)
    num = args[0]
    repo = args[args.index("--repo") + 1] if "--repo" in args else origin_repo()
    allow_closed = "--allow-closed" in args

    pr = json.loads(gh("pr", "view", num, "-R", repo, "--json", "title,body,url,state,headRefOid,author,mergeStateStatus"))
    if pr["state"] != "OPEN" and not allow_closed:
        sys.exit(f"PR #{num} 상태 {pr['state']} — 보고 대상 아님(--allow-closed 로 강제)")
    sha = pr["headRefOid"]

    statuses = json.loads(gh("api", f"repos/{repo}/commits/{sha}/status"))["statuses"]
    plans = {s["context"]: s["state"] for s in statuses if s["context"].startswith("atlantis/plan")}

    comments = json.loads(gh("api", f"repos/{repo}/issues/{num}/comments", "--paginate"))
    ran = [c for c in comments if "atlantis" in c["user"]["login"] and "Ran Plan" in c["body"]]
    body = ran[-1]["body"] if ran else ""

    projects = re.findall(r"Ran Plan for (?:project: `([^`]+)` )?dir: `([^`]+)`", body)
    counts = re.search(r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", body)
    c, u, r_, d = counts.groups() if counts else ("?", "?", "?", "?")
    findings = [l for l in body.splitlines() if l.startswith("| 🔴") or l.startswith("| ⚪")]
    changed = re.findall(r"^- `([a-z+\-]+)` (\S+)$", body, re.M)

    # plan 원문에서 리소스별 주요 속성
    detail = {}
    cur = None
    for line in body.splitlines():
        m = re.match(r"\s*# (\S+) (will be|must be)", line)
        if m:
            cur = m.group(1)
            detail[cur] = []
            continue
        if cur and KEY_ATTRS.match(line) and len(detail[cur]) < 10:
            item = re.sub(r"\s+", " ", line.strip())
            if item not in detail[cur]:  # tags / tags_all 중복 제거
                detail[cur].append(item)
        if line.startswith("Plan:") or line.startswith("Changes to Outputs"):
            cur = None
    outputs = []
    m = re.search(r"Changes to Outputs:\n(.*?)(?:\n\n|```)", body, re.S)
    if m:
        outputs = [re.sub(r"\s+", " ", l.strip()) for l in m.group(1).splitlines()
                   if l.strip() and not re.match(r"\s*(Releasing|Acquiring|Terraform|Note:)", l)]

    pb = pr["body"]
    blocked = [f for f in findings if f.startswith("| 🔴")]
    allowed = sorted(set(re.findall(r"허용\(`([^`]+)`\)", "\n".join(findings))))
    guard_ok = not blocked
    if blocked:
        guard_txt, guard_short = "🔴 차단 — 해제 라벨이 필요하면 승인 문구에 해제 의사 포함", "🔴→라벨 필요"
    elif allowed:
        guard_txt, guard_short = "⚪ 해제 라벨로 허용: " + ", ".join(allowed), "⚪(" + ",".join(allowed) + ")"
    else:
        guard_txt, guard_short = "✅ 위반 없음", "✅"
    lines = [
        f"## 📋 적용 내용 보고 — {repo} PR #{num}",
        "",
        "**요청**",
        f"- 요청 ID: {field(pb, '요청 ID')} / 기안: {field(pb, '기안번호 / 제목')}",
        f"- 기안부서: {field(pb, '기안부서(RequesterTeam)')} / 결재 상태: {field(pb, '결재 상태')} / 유형: {field(pb, '요청 유형')}",
        f"- PR: {pr['url']} (작성자 {pr['author']['login']}, {pr['mergeStateStatus']})",
        "",
        "**대상**",
        f"- {field(pb, '대상')}  ← 라이브면 ⚠️ 계정 ID·리전 재확인",
        f"- Atlantis 프로젝트: {', '.join(p or d for p, d in projects) or '—'}",
        "",
        "**리소스별 동작**",
    ]
    if changed:
        for act, addr in changed:
            lines.append(f"- `{act}` {addr}")
            for a in detail.get(addr, []):
                lines.append(f"    - {a}")
    else:
        lines.append("- 리소스 변경 없음")
    if outputs:
        lines.append("- 출력값: " + " / ".join(outputs))
    lines += [
        "",
        f"**plan**: 생성 {c} / 변경 {u} / 교체 {r_} / 삭제 {d}  ·  상태: "
        + (", ".join(f"{k}={v}" for k, v in plans.items()) or "없음 ⚠️"),
        "**감시**: " + guard_txt,
    ]
    lines += [f"  {f}" for f in findings]
    if int(d if d.isdigit() else 0) or int(r_ if r_.isdigit() else 0):
        lines.append("**⚠️ 삭제·교체 있음** — 대상 데이터 유무(S3 객체·버전·삭제 마커 수 등)를 조회해 적을 것")
    lines += [
        "**기본값·이름 근거**: (PR 본문 '변경 내용' 참고해 Claude 가 보충)",
        "",
        f"**커밋**: `{sha[:7]}`",
        "",
        "승인 시 (Claude 실행):",
        f'`tools/chat-approve.sh {num} {sha[:7]} "생성 {c}/변경 {u}/삭제 {d}, 감시 {guard_short}"`',
    ]
    if not ran:
        lines.insert(0, "⚠️ Atlantis plan 댓글이 없음 — Atlantis 를 켜고 plan 후 다시 실행")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
