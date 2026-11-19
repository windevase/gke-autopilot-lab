#!/usr/bin/env bash
# Atlantis 감시 단계 (서버 측 ConfigMap — PR 이 수정할 수 없음)
#   atlantis-guard.sh fmt    terraform fmt -check (plan 전)
#   atlantis-guard.sh plan   plan 감시 결과를 PR 댓글에 보고 (차단 대상이어도 plan 은 유지)
#   atlantis-guard.sh apply  apply 직전 재검사 — 아래 중 하나라도 해당하면 apply 중단
#                            · plan 감시 위반(해제 라벨 없음) · 필수 CI(GUARD_REQUIRED_WORKFLOW, 기본 code-guard) 미통과 · GitHub 조회 실패
#                            · 마지막 승인 리뷰의 커밋 ≠ apply 대상 커밋 (승인 뒤 변경)
#                            · 마지막 승인자·해제 라벨(allow-*) 부착자가 허용 목록(GUARD_APPROVERS, 기본 windevase)에 없음
#                            · PR 에 보호 경로(감시·워크플로·도구·지침 — policy-audit.sh PROTECTED 와 같게) 변경이 섞임
# Atlantis 제공 env: PLANFILE PULL_NUM BASE_REPO_OWNER BASE_REPO_NAME HEAD_COMMIT ATLANTIS_TERRAFORM_VERSION
# Secret env       : GUARD_GH_TOKEN (감시용 fine-grained PAT — infra-terraform·gcp 읽기 전용: Actions·Contents·Issues·Pull requests)
set -euo pipefail

mode=${1:?usage: atlantis-guard.sh fmt|plan|apply}

# run 단계 PATH 는 이미지 기본 terraform 이 앞 → Atlantis 가 받은 프로젝트 버전을 우선 사용
tf=$(command -v "terraform${ATLANTIS_TERRAFORM_VERSION:-}" || command -v terraform)

if [[ $mode == fmt ]]; then
  "$tf" fmt -check -diff -no-color
  exit 0
fi

api="https://api.github.com/repos/${BASE_REPO_OWNER}/${BASE_REPO_NAME}"
gh_get() {
  curl -fsS --max-time 20 \
    -H "Authorization: Bearer ${GUARD_GH_TOKEN:?GUARD_GH_TOKEN 없음}" \
    -H "Accept: application/vnd.github+json" "$api/$1"
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
"$tf" show -json "$PLANFILE" >"$work/plan.json"

labels=""
gh_ok=1
if ! labels=$(gh_get "issues/${PULL_NUM}/labels" | jq -r '[.[].name] | join(",")'); then
  labels=""
  gh_ok=0
fi

set +e
bash /opt/guard/plan-guard.sh "$work/plan.json" "$labels" "$work/guard.md"
rc=$?
set -e
cat "$work/guard.md"

[[ $mode == apply ]] || exit 0

fail=0
if ((!gh_ok)); then
  echo "🔴 GitHub 라벨 조회 실패 (GUARD_GH_TOKEN 확인) — apply 중단"
  fail=1
fi
if ((rc != 0)); then
  echo "🔴 plan 감시 위반 — apply 중단 (의도한 변경이면 담당자가 해제 라벨을 붙인 뒤 다시 apply)"
  fail=1
fi
if ((gh_ok)); then
  # 저장소별 필수 CI (workflow env 단계로 지정 — infra-terraform: code-guard / gcp: ci)
  wf=${GUARD_REQUIRED_WORKFLOW:-code-guard}
  cg=$(gh_get "actions/runs?head_sha=${HEAD_COMMIT}&per_page=50" |
    jq -r --arg wf "$wf" '[.workflow_runs[] | select(.name == $wf)] | sort_by(.created_at) | last | .conclusion // "none"') || cg="조회 실패"
  if [[ $cg != success ]]; then
    echo "🔴 필수 CI(${wf}) 결과: ${cg} — apply 중단"
    fail=1
  fi

  # 승인 커밋 고정: 마지막 승인(APPROVED) 리뷰가 지금 apply 할 커밋에 대한 것이어야 한다.
  # (채팅 승인 → Claude 가 그 시점 HEAD 를 승인. 승인 뒤 새 커밋이 오면 사람이 보지 않은 plan → 재승인 필요)
  approvers=" ${GUARD_APPROVERS:-windevase} "
  last_approval=$(gh_get "pulls/${PULL_NUM}/reviews?per_page=100" |
    jq -r '[.[] | select(.state == "APPROVED")] | last | "\(.commit_id // "none") \(.user.login // "none")"') || last_approval="조회실패 조회실패"
  read -r approved_sha approver <<<"$last_approval"
  if [[ $approved_sha != "$HEAD_COMMIT" ]]; then
    echo "🔴 승인 커밋(${approved_sha:0:7}) ≠ apply 대상 커밋(${HEAD_COMMIT:0:7}) — 승인 뒤 변경됨, 다시 승인 필요 · apply 중단"
    fail=1
  fi
  # 승인자 허용 목록 — 담당자 계정의 승인(채팅 승인 기록)만 인정 (봇·다른 계정의 Approve 는 무효)
  if [[ $approvers != *" $approver "* ]]; then
    echo "🔴 마지막 승인자(${approver})가 허용 목록(${approvers# })에 없음 — apply 중단"
    fail=1
  fi

  # 해제 라벨(allow-*)은 담당자가 붙인 것만 인정 — 라벨마다 마지막 'labeled' 이벤트의 계정 확인
  if [[ $labels == *allow-* ]]; then
    if events=$(gh_get "issues/${PULL_NUM}/events?per_page=100"); then
      for l in ${labels//,/ }; do
        [[ $l == allow-* ]] || continue
        actor=$(jq -r --arg l "$l" '[.[] | select(.event == "labeled" and .label.name == $l)] | last | .actor.login // "none"' <<<"$events")
        if [[ $approvers != *" $actor "* ]]; then
          echo "🔴 해제 라벨 ${l} 을 붙인 계정(${actor})이 허용 목록에 없음 — apply 중단"
          fail=1
        fi
      done
    else
      echo "🔴 라벨 이벤트 조회 실패 — apply 중단"
      fail=1
    fi
  fi

  # 보호 경로가 섞인 PR 은 apply 하지 않는다 — apply 후 automerge 로 정책 변경이 terraform 승인에 묻어 들어가는 것 방지
  # policy-audit.sh 의 PROTECTED 와 같게 유지
  protected='^(\.github/|\.forgejo/|\.claude/|tools/|CLAUDE\.md$|atlantis\.yaml$|_bootstrap/|k8s/atlantis/|k8s/apps/|k8s/argocd/|k8s/forgejo/|k8s/monitoring/offhours/|k8s/monitoring/budget-guard/)'
  if files=$(gh_get "pulls/${PULL_NUM}/files?per_page=100" | jq -r '.[].filename'); then
    mixed=$(grep -E "$protected" <<<"$files" || true)
    if [[ -n $mixed ]]; then
      echo "🔴 보호 경로 변경이 섞인 PR — apply 중단 (정책 PR 로 분리):"
      sed 's/^/   - /' <<<"$mixed"
      fail=1
    fi
  else
    echo "🔴 PR 파일 목록 조회 실패 — apply 중단"
    fail=1
  fi
fi
((fail == 0)) && echo "✅ apply 전 감시 통과"
exit "$fail"
