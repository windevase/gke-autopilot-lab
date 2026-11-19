#!/usr/bin/env bash
# 채팅 승인을 GitHub PR Approve 로 기록한다 (CLAUDE.md 2.5). 손으로 gh pr review 를 쓰지 말고 이 스크립트만 쓴다.
#   tools/chat-approve.sh <PR 번호> <보고한 커밋 SHA7> "<plan 요약>" [--dry-run] [--local-tf]
#     --local-tf : Atlantis 대상이 아닌 로컬 apply 계층(_bootstrap, gcp 루트 bootstrap)의 .tf 변경을 정책 PR 로 merge
#   예) tools/chat-approve.sh 10 f1fab56 "생성 0/변경 0/삭제 0 (출력값 2개), 감시 ✅"
# 저장소: 현재 폴더의 git origin (windevase/infra-terraform 또는 windevase/gcp). BOT_PR_REPO 로 덮어쓸 수 있음
# 하는 일:
#   1. PR HEAD 가 보고한 SHA 와 같은지 확인 — 다르면 승인하지 않고 종료(승인 사이 새 커밋)
#   2. 유형 판단: terraform(그 커밋에 Atlantis plan 상태가 있음) / policy(그 밖)
#      plan 상태가 없는데 *.tf·*.tf.json·lock 이 바뀌었으면 거부 — 로컬 apply 계층이면 --local-tf 로 명시(모듈 변경이 apply 없이 merge 되는 것 방지)
#      terraform PR 에 보호 경로(감시·워크플로·도구·지침 — policy-audit.sh PROTECTED)가 섞이면 거부 → 정책 PR 로 따로
#      terraform: Atlantis plan 이 모두 success·Atlantis 가동·봇 작성 PR 인지 확인 → GitHub Approve → apply-on-approve 가 apply·automerge
#      policy   : 필수 CI(code-guard/ci) success 확인 → 기록 → 바로 merge(--match-head-commit — 확인한 커밋이 아니면 merge 거부)
#                 무료 GitHub — 브랜치 보호 대신 policy-audit 가 사후 확인
#   3. KST 시각을 UTC+9 계산으로 만든다 — Windows Git Bash 는 TZ=Asia/Seoul 을 무시해 UTC 가 찍힘(2026-11-14 gcp#10)
#   4. 기록: 남이 만든 PR 은 GitHub Approve, 본인이 만든 PR(본인 승인 불가)은 봇 이름 댓글 — 둘 다 `<!-- chat-approval: {SHA} -->` 포함
# 원본: windevase/infra-terraform — windevase/gcp 에 같은 내용 사본(CI 가 동일 여부 확인, 원본을 먼저 고친다)
set -euo pipefail

num=${1:?usage: chat-approve.sh <PR> <SHA7> "<plan 요약>" [--dry-run] [--local-tf]}
sha=${2:?보고한 커밋 SHA7}
summary=${3:?plan 요약}
dry="" local_tf=0
for a in "${@:4}"; do
  case $a in
    --dry-run) dry=--dry-run ;;
    --local-tf) local_tf=1 ;;
    "") ;;
    *) echo "알 수 없는 옵션: $a" >&2; exit 2 ;;
  esac
done

GH=gh
command -v gh >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"

repo=${BOT_PR_REPO:-$(git remote get-url origin | sed -E 's#(https://github.com/|git@github.com:)##; s#\.git$##')}
info=$("$GH" pr view "$num" -R "$repo" --json headRefOid,state,author,files \
  --jq '.state + " " + .headRefOid + " " + .author.login + " " + ([.files[].path] | join(","))')
read -r state head author files <<<"$info"
me=$("$GH" api user --jq .login)

# 보호 경로 — policy-audit.sh 의 PROTECTED 와 같게 유지 (테스트가 일치 여부를 확인)
PROTECTED='^(\.github/|\.forgejo/|\.claude/|tools/|CLAUDE\.md$|atlantis\.yaml$|_bootstrap/|k8s/atlantis/|k8s/apps/|k8s/argocd/|k8s/forgejo/|k8s/monitoring/offhours/|k8s/monitoring/budget-guard/)'

# 유형: Atlantis 가 이 커밋을 plan 했으면 terraform (Atlantis 가 대상 여부를 판단 — 모듈·하위 폴더 변경 포함)
# plan 이 없는데 terraform 코드가 바뀌었으면: 로컬 apply 계층(--local-tf)만 정책 PR 로, 아니면 거부
# (2026-11-14 검토: 프로젝트 최상위만 보던 판단은 modules/* 변경을 정책 PR 로 보고 apply 없이 merge 할 수 있었음)
mode=policy
# 프로젝트별 상태(atlantis/plan: <프로젝트>)만 센다 — Atlantis 는 terraform 이 없는 PR 에도 "atlantis/plan 0/0 projects" 를 남긴다
planned=$("$GH" api "repos/$repo/commits/$head/status" --jq '[.statuses[] | select(.context | startswith("atlantis/plan: "))] | length' 2>/dev/null || echo 0)
tf_changed=$(tr ',' '\n' <<<"$files" | grep -E '(\.tf|\.tf\.json|\.terraform\.lock\.hcl)$' || true)
if ((${planned:-0} > 0)); then
  mode=terraform
elif [[ -n $tf_changed ]] && ((!local_tf)); then
  echo "🔴 terraform 코드가 바뀌었는데 커밋 ${head:0:7} 에 Atlantis plan 이 없음 — 승인하지 않음" >&2
  echo "   Atlantis 대상이면: Atlantis 를 켜고 plan 후 다시 / 로컬 apply 계층(_bootstrap·gcp 루트)이면: --local-tf" >&2
  exit 1
fi

if [[ $state != OPEN ]]; then
  echo "🔴 PR #$num 상태 $state — 승인하지 않음" >&2
  exit 1
fi
if [[ ${head:0:${#sha}} != "$sha" || ${#sha} -lt 7 ]]; then
  echo "🔴 PR HEAD ${head:0:7} ≠ 보고한 커밋 ${sha} — 승인하지 않음. 최신 커밋으로 다시 보고할 것" >&2
  exit 1
fi

if [[ $mode == terraform ]]; then
  # 보호 경로가 섞인 terraform PR 은 거부 — Atlantis 가 apply 후 automerge 하면 정책 변경이 terraform 승인에 묻어 들어간다
  mixed=$(tr ',' '\n' <<<"$files" | grep -E "$PROTECTED" || true)
  if [[ -n $mixed ]]; then
    echo "🔴 terraform PR 에 보호 경로 변경이 섞여 있음 — 승인하지 않음 (정책 PR 로 분리):" >&2
    sed 's/^/   - /' <<<"$mixed" >&2
    exit 1
  fi
  # plan 상태: 하나 이상 있고 전부 success 여야 한다 (pending·failure·없음이면 거부)
  plans=$("$GH" api "repos/$repo/commits/$head/status" \
    --jq '[.statuses[] | select(.context | startswith("atlantis/plan")) | "\(.context)=\(.state)"] | join(" ")')
  if [[ -z $plans ]]; then
    echo "🔴 커밋 ${head:0:7} 에 Atlantis plan 결과가 없음 — 승인하지 않음 (Atlantis 켜고 'atlantis plan' 후 다시 보고)" >&2
    exit 1
  fi
  if [[ $plans == *"=pending"* || $plans == *"=failure"* || $plans == *"=error"* ]]; then
    echo "🔴 plan 이 성공 상태가 아님: $plans — 승인하지 않음" >&2
    exit 1
  fi
  # Atlantis 가동 여부 (kubectl 이 있을 때만 확인 — 꺼져 있으면 승인해도 apply 가 안 됨)
  if command -v kubectl >/dev/null 2>&1; then
    ready=$(kubectl get statefulset atlantis -n atlantis -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)
    if [[ ${ready:-0} -lt 1 ]]; then
      echo "🔴 Atlantis 가 꺼져 있음 — 승인하지 않음 (C:\\gcp\\tools\\atlantis.ps1 on 후 다시)" >&2
      exit 1
    fi
  fi
  if [[ $author == "$me" ]]; then
    echo "🔴 terraform PR 은 봇이 작성해야 한다(본인 PR 은 Atlantis 승인 조건을 못 채움) — tools/bot-pr.sh 로 다시" >&2
    exit 1
  fi
else
  # 정책 변경 PR: 저장소 필수 CI(code-guard 또는 ci)가 이 커밋에서 성공했는지
  ci=$("$GH" api "repos/$repo/actions/runs?head_sha=$head&per_page=50" \
    --jq '[.workflow_runs[] | select(.name == "code-guard" or .name == "ci")] | sort_by(.created_at) | last | (.status + "/" + (.conclusion // ""))')
  if [[ $ci != completed/success ]]; then
    echo "🔴 필수 CI 결과 '${ci:-없음}' — 성공 후 다시 (승인하지 않음)" >&2
    exit 1
  fi
fi

# KST = UTC + 9시간 (TZ 데이터베이스에 의존하지 않음)
kst=$(date -u -d '+9 hours' '+%Y-%m-%d %H:%M KST')
body="채팅 승인 — 승인자: ${me}(채팅), 일시: ${kst}, 커밋: ${head:0:7}, 유형: ${mode}, 내용: ${summary}
<!-- chat-approval: ${head} -->"

if [[ $dry == --dry-run ]]; then
  echo "[dry-run] $repo PR #$num (유형 $mode, 작성자 $author)"
  echo "$body"
  exit 0
fi

# 기록: 남이 만든 PR(봇 PR 포함) → GitHub Approve / 본인 PR(정책 변경) → 봇 이름 채팅 승인 댓글(App 키 필요, 위조 어려움)
if [[ $author != "$me" ]]; then
  "$GH" pr review "$num" -R "$repo" --approve --body "$body"
else
  BOT_PR_REPO=$repo bash "$(dirname "$0")/bot-pr.sh" comment "$num" "$body" >/dev/null
fi
echo "✅ 채팅 승인 기록: $repo PR #$num @ ${head:0:7} ($kst, $mode)"

# 정책 변경 PR 은 Atlantis automerge 가 없으므로 기록 직후 merge (policy-audit 가 사후에 기록을 확인)
if [[ $mode == policy ]]; then
  "$GH" pr merge "$num" -R "$repo" --merge --delete-branch --match-head-commit "$head" >/dev/null && echo "✅ merge: $repo PR #$num"
fi
