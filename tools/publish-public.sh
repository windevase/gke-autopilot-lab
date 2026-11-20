#!/usr/bin/env bash
# 공개용 사본 만들기 — 이 저장소(private, 운영용)의 정리된 스냅샷을 public 포트폴리오 저장소로 내보낸다
#   tools/publish-public.sh            로컬에 공개본을 만들고 검사만 (push 안 함). 결과 폴더 경로를 출력
#   tools/publish-public.sh --push     검사 통과 시 공개 저장소에 스냅샷 커밋 1개로 push
# 환경 변수: PUBLIC_REPO (기본 windevase/gke-autopilot-lab), REDACT (기본 secret/public-redact.tsv)
#
# 왜 별도 저장소인가: 과거 커밋(변수화 전 main.tf 등)과 PR 댓글(Atlantis plan)에 계정 식별 정보가 남아 있어
#   이 저장소를 그대로 공개할 수 없다. 기록을 다시 쓰면 커밋 SHA 기반 승인·사후 감사가 깨진다
#   → 운영은 여기서, 공개는 "검사를 통과한 스냅샷"만. 공개 저장소 기록에는 처음부터 치환된 내용만 있다
#
# 처리:
#   1. HEAD(= origin/main, 작업 트리 깨끗해야 함)를 git archive 로 추출 — 추적 파일만 (secret/·tfvars 는 원래 제외)
#   2. 공개본에서 동작할 수 없는 것 제거: private 토큰이 필요한 워크플로(apply-on-approve, policy-audit),
#      ci.yml 의 사본 동기화 job, Dependabot 설정(공개 저장소에 PR 을 만들지 않도록), 로컬 작업 환경 설정(.claude/)
#   3. 치환: REDACT 규칙 + tfvars 의 계정·존·결제 ID 자동 추가. 도메인은 숨김, GitHub 사용자명은 공개(사용자 결정)
#      예외: README.md 의 Grafana 공개 대시보드 링크(https://grafana.<도메인>/public-dashboards/<토큰>)는 그대로 둔다
#      (2026-11-20 사용자 결정 — 포트폴리오에서 바로 볼 수 있게. 다른 위치의 도메인은 계속 치환·검사)
#   4. 검사: 치환 대상 원래 값 잔존 0, 비밀값 패턴(PRIVATE KEY·ghp_·glsa_·GOCSPX-·cfat_ 등) 0,
#      개인 메일 주소(gmail·naver 등) 0. 하나라도 걸리면 중단
set -euo pipefail
export PYTHONIOENCODING=utf-8

PUBLIC_REPO=${PUBLIC_REPO:-windevase/gke-autopilot-lab}
REDACT=${REDACT:-secret/public-redact.tsv}
PUSH=0; [ "${1:-}" = "--push" ] && PUSH=1
root=$(git rev-parse --show-toplevel); cd "$root"

[ -f "$REDACT" ] || { echo "치환 규칙 없음: $REDACT"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "작업 트리가 깨끗하지 않음 — 커밋된 내용만 공개한다"; exit 1; }
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "HEAD 가 origin/main 과 다름 — merge 된 내용만 공개한다"; exit 1; }
sha=$(git rev-parse --short=7 HEAD)

out=$(mktemp -d)/public
mkdir -p "$out"
git archive HEAD | tar -x -C "$out"

# 2. 공개본에서 동작할 수 없는 것
rm -f "$out/.github/workflows/apply-on-approve.yml" "$out/.github/workflows/policy-audit.yml" "$out/.github/dependabot.yml"
# 로컬 작업 환경 설정(Claude Code 권한 규칙)은 공개하지 않는다 — README 의 경로 언급도 함께 정리(설명은 유지)
rm -rf "$out/.claude"
sed -i -e '/^├─ \.claude\/settings\.json /d' -e 's#Claude Code 권한 설정(`\.claude/settings\.json`)#Claude Code 권한 설정#' "$out/README.md"
python -I - "$out/.github/workflows/ci.yml" <<'PY'
import sys, re
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
# 사본 동기화 job (private 원본 저장소 토큰 필요) 제거: "  sync:" 부터 다음 최상위 job 직전까지
s2 = re.sub(r"\n  sync:\n.*?(?=\n  [a-z][\w-]*:\n)", "\n", s, flags=re.S)
assert s2 != s, "ci.yml 에서 sync job 을 찾지 못함"
open(p, "w", encoding="utf-8", newline="").write(s2)
PY

# README CI 배지·링크: private 저장소(windevase/gcp) → 공개 저장소
sed -i "s#github.com/windevase/gcp/actions#github.com/$PUBLIC_REPO/actions#g" "$out/README.md"

# 3. 치환 + 4. 검사
python -I - "$out" "$REDACT" terraform.tfvars platform/terraform.tfvars <<'PY'
import os, re, sys
out, redact, *tfvars = sys.argv[1:]
rules = []
for line in open(redact, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line or line.startswith("#"):
        continue
    pat, rep = line.split("\t", 1)
    rules.append((pat, rep))
# tfvars 의 계정·존·결제 ID 값 자동 추가 (값이 바뀌어도 빠지지 않게)
for tf in tfvars:
    if not os.path.exists(tf):
        continue
    for m in re.finditer(r'^\s*(\w*(?:account_id|zone_id)|billing_account_id)\s*=\s*"([^"]+)"', open(tf, encoding="utf-8").read(), re.M):
        rules.append((re.escape(m.group(2)), "0" * len(m.group(2)) if m.group(2).isalnum() else "000000-000000-000000"))

SECRET_PATTERNS = [r"-----BEGIN [A-Z ]*PRIVATE KEY", r"\bghp_[A-Za-z0-9]{20,}", r"\bgithub_pat_[A-Za-z0-9_]{20,}",
                   r"\bglsa_[A-Za-z0-9_]{20,}", r"GOCSPX-[A-Za-z0-9_-]{10,}", r"\bcfat_[A-Za-z0-9]{20,}",
                   r"\bAKIA[0-9A-Z]{16}\b", r'"private_key"\s*:']
KEEP = re.compile(r"https://grafana\.[A-Za-z0-9.-]+/public-dashboards/[0-9a-f]{32}")
MAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?:gmail|naver|daum|hanmail|kakao|outlook|hotmail|yahoo)\.[a-z.]+", re.I)

changed, problems = 0, []
for dp, _, files in os.walk(out):
    for fn in files:
        p = os.path.join(dp, fn)
        try:
            s = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue  # 바이너리
        rel = os.path.relpath(p, out)
        keep = []
        def _mask(m):
            keep.append(m.group(0)); return f"@@KEEP{len(keep) - 1}@@"
        t = KEEP.sub(_mask, s) if rel == "README.md" else s
        for pat, rep in rules:
            t = re.sub(pat, rep, t)
        for pat, _ in rules:
            if re.search(pat, t):
                problems.append(f"치환 후 잔존: {rel} ~ /{pat}/")
        for pat in SECRET_PATTERNS:
            if re.search(pat, t):
                problems.append(f"비밀값 패턴: {rel} ~ /{pat}/")
        for m in MAIL.finditer(t):
            problems.append(f"개인 메일: {rel} ~ {m.group(0)}")
        # 검사는 예외 링크를 가린 상태로 끝낸 뒤 되돌린다
        t = re.sub(r"@@KEEP(\d+)@@", lambda m: keep[int(m.group(1))], t)
        if t != s:
            open(p, "w", encoding="utf-8", newline="").write(t)
            changed += 1
print(f"치환 규칙 {len(rules)}개, 치환된 파일 {changed}개")
if problems:
    print("\n".join(problems)); sys.exit(1)
print("검사 통과: 잔존 0, 비밀값 패턴 0, 개인 메일 0")
PY

echo "공개본: $out  (원본 커밋 $sha)"
[ $PUSH = 1 ] || { echo "push 안 함 (--push 로 공개 저장소에 반영)"; exit 0; }

# 공개 저장소에 스냅샷 커밋 1개 (공개 저장소 기록에는 치환된 내용만)
work=$(mktemp -d)
if git ls-remote "https://github.com/$PUBLIC_REPO.git" >/dev/null 2>&1; then
  git clone -q "https://github.com/$PUBLIC_REPO.git" "$work/repo"
else
  echo "공개 저장소 $PUBLIC_REPO 가 없음 — 먼저 만들어야 한다 (사용자 확인 후)"; exit 1
fi
cd "$work/repo"
git rm -rq --ignore-unmatch . >/dev/null
cp -a "$out/." .
git add -A
git -c user.name=windevase -c user.email=79207024+windevase@users.noreply.github.com \
  commit -qm "Publish snapshot of private repo @ $sha" || { echo "변경 없음"; exit 0; }
git push -q origin HEAD:main
echo "공개 저장소 반영: https://github.com/$PUBLIC_REPO ($(git rev-parse --short HEAD))"
