#!/usr/bin/env bash
# 정책 변경 사후 감사 — 무료 GitHub(브랜치 보호 없음)에서 "승인 없는 보호 경로 변경"을 드러낸다
# 원본: windevase/infra-terraform — windevase/gcp 에 같은 내용 사본(CI 가 동일 여부 확인, 원본을 먼저 고친다)
#
# 모드 (AUDIT_MODE)
#   push(기본) — main push 때(.github/workflows/policy-audit.yml, 변경 전 커밋의 이 스크립트로 실행):
#     1. BEFORE..AFTER 로 바뀐 파일 중 보호 경로가 있는지 · fast-forward 가 아니면(force-push) 위반
#     2. 있으면 범위 안 커밋(first-parent)마다, 보호 경로를 바꾼 커밋을 merge 한 PR 의 최종 커밋(head)에 채팅 승인 기록이 있는지
#        (한 번의 push 에 PR 여러 개·직접 커밋이 섞여도 하나씩 확인)
#        - GitHub Approve 리뷰(commit_id = head) 또는 봇(AUDIT_BOT) 댓글 `<!-- chat-approval: {head} -->`
#     3. 없으면(PR 없이 직접 push 포함) 경고 이슈 + 되돌림 브랜치 `revert/{sha7}`(PR 은 사람·Claude 가 열고 채팅 승인 후 merge)
#   peer — 매일(schedule) 상대 저장소(AUDIT_PEER)를 API 로 교차 감사. 한쪽 감사를 무력화해도 다른 쪽이 잡는다:
#     1. 상대의 감사 파일(이 스크립트·워크플로)이 이 저장소와 같은지(양쪽 마지막 변경 후 PEER_GRACE_HOURS 지나도 다르면 위반)
#     2. 상대 main 의 최근 SINCE_HOURS 커밋 중 보호 경로를 바꾼 PR 에 승인 기록이 있는지, PR 없는 직접 push 가 없는지
#     위반은 이 저장소에 이슈(같은 건은 한 번만)
# env: GH_TOKEN REPO
#      push: BEFORE AFTER / peer: AUDIT_PEER PEER_TOKEN [SINCE_HOURS=48] [PEER_GRACE_HOURS=12]
#      [AUDIT_BOT] [AUDIT_DRY=1 → 판정만, 이슈·브랜치 생략(로컬 시험용)]
set -uo pipefail

BOT=${AUDIT_BOT:-windevase-atlantis[bot]}
# gcp: k8s/apps/(Argo 앱 정의 — main 에 들어오면 무엇이든 자동 배포)·k8s/argocd/(권한)·budget-guard(예산 차단 장치)도 보호 (2026-11-14)
PROTECTED='^(\.github/|\.claude/|tools/|CLAUDE\.md$|atlantis\.yaml$|_bootstrap/|k8s/atlantis/|k8s/apps/|k8s/argocd/|k8s/monitoring/offhours/|k8s/monitoring/budget-guard/)'
AUDIT_FILES=(.github/scripts/policy-audit.sh .github/workflows/policy-audit.yml)
# 규칙 시행 시점(infra-terraform#18 merge, 2026-11-14 02:35 KST) — 그 전 변경은 교차 감사하지 않는다
RULE_START='2026-11-13T17:30:00Z'
violations=0

# approved <gh 함수> <repo> <PR> <head> — 승인 기록이 있으면 0. 건수는 stdout
approved() {
  local g=$1 repo=$2 num=$3 head=$4 rv cm
  rv=$($g api "repos/$repo/pulls/$num/reviews?per_page=100" \
    --jq "[.[] | select(.state == \"APPROVED\" and .commit_id == \"$head\")] | length")
  cm=$($g api "repos/$repo/issues/$num/comments?per_page=100" \
    --jq "[.[] | select(.user.login == \"$BOT\" and (.body | contains(\"chat-approval: $head\")))] | length")
  echo "Approve ${rv:-0} / 채팅 승인 댓글 ${cm:-0}"
  ((${rv:-0} > 0 || ${cm:-0} > 0))
}

# report <중복 방지 키> <제목> <본문> — 이 저장소에 policy-violation 이슈(같은 키는 한 번만)
report() {
  local key=$1 title=$2 body=$3 n
  violations=$((violations + 1))
  echo "::error::$title"
  if [[ -n ${AUDIT_DRY:-} ]]; then
    echo "(AUDIT_DRY — 이슈 생략) 키: $key"
    return
  fi
  gh label create policy-violation --color B60205 --description "승인 없는 보호 경로 변경" -R "$REPO" >/dev/null 2>&1 || true
  n=$(gh issue list -R "$REPO" --label policy-violation --state all -L 300 --json title \
    --jq "[.[] | select(.title | contains(\"[$key]\"))] | length" 2>/dev/null || echo 0)
  if ((${n:-0} > 0)); then
    echo "이미 이슈 있음: [$key]"
    return
  fi
  gh issue create -R "$REPO" --title "⚠️ $title [$key]" --body "$body" --label policy-violation >/dev/null && echo "이슈 생성: [$key]"
}

bullets() { sed 's/^/  - `/; s/$/`/'; }

# ---------------------------------------------------------------- push 모드
push_audit() {
  local changed detail="" num head r
  if [[ $BEFORE =~ ^0+$ ]]; then
    echo "새 브랜치 push — 건너뜀"
    return 0
  fi
  changed=$(git diff --name-only "$BEFORE" "$AFTER" | grep -E "$PROTECTED" || true)
  if [[ -z $changed ]] && git merge-base --is-ancestor "$BEFORE" "$AFTER" 2>/dev/null; then
    echo "보호 경로 변경 없음"
    return 0
  fi
  echo "보호 경로 변경:"
  sed 's/^/  - /' <<<"$changed"

  local reasons=() c cprot cprs
  local -A seen_pr=()
  if ! git merge-base --is-ancestor "$BEFORE" "$AFTER" 2>/dev/null; then
    reasons+=("fast-forward 가 아님 — main 이 force-push 로 바뀜(이전 커밋 ${BEFORE:0:7} 이 새 이력에 없음)")
  fi
  for c in $(git rev-list --first-parent --reverse "$BEFORE..$AFTER" 2>/dev/null); do
    cprot=$(git diff --name-only "$c^1" "$c" 2>/dev/null | grep -E "$PROTECTED" || true)
    [[ -z $cprot ]] && continue
    cprs=$(gh api "repos/$REPO/commits/$c/pulls" --jq '.[] | select(.merged_at != null) | "\(.number) \(.head.sha)"')
    if [[ -z $cprs ]]; then
      reasons+=("커밋 ${c:0:7} — PR 없이 main 에 직접 push 됨")
      continue
    fi
    while read -r num head; do
      [[ -n ${seen_pr[$num]:-} ]] && continue
      if r=$(approved gh "$REPO" "$num" "$head"); then
        echo "✅ PR #$num — 최종 커밋 ${head:0:7} 승인 기록 있음 ($r)"
        seen_pr[$num]=1
      else
        reasons+=("PR #$num 의 최종 커밋 ${head:0:7} 에 채팅 승인 기록(Approve 또는 봇 댓글)이 없음")
      fi
    done <<<"$cprs"
  done
  if ((${#reasons[@]} == 0)); then
    return 0
  fi
  detail=$(printf '%s; ' "${reasons[@]}")
  detail=${detail%; }
  echo "사유: $detail"

  # 되돌림 브랜치 (merge 커밋이면 -m 1, 아니면 범위 되돌림). 워크플로 파일이 포함되면 GITHUB_TOKEN 으로 push 불가 → 이슈에만 안내
  local br="revert/${AFTER:0:7}" ok=0 revert_msg="되돌림 브랜치 생성 실패 — 수동으로 되돌릴 것"
  if [[ -z ${AUDIT_DRY:-} ]]; then
    git config user.name "policy-audit"
    git config user.email "policy-audit@users.noreply.github.com"
    if [[ $(git rev-list --parents -n1 "$AFTER" | wc -w) -gt 2 && $(git rev-parse "$AFTER^1") == $(git rev-parse "$BEFORE") ]]; then
      git revert --no-edit -m 1 "$AFTER" >/dev/null 2>&1 && ok=1
    else
      git revert --no-edit --no-commit "$BEFORE..$AFTER" >/dev/null 2>&1 &&
        git commit -q -m "되돌림: 승인 없는 보호 경로 변경 ${BEFORE:0:7}..${AFTER:0:7}" && ok=1
    fi
    if ((ok)) && git push -q origin "HEAD:refs/heads/$br" 2>/dev/null; then
      revert_msg="되돌림 브랜치: \`$br\` → https://github.com/$REPO/compare/main...$br (PR 을 열고 채팅 승인 후 merge)"
    fi
  fi

  report "push ${AFTER:0:7}" "승인 없는 보호 경로 변경: ${AFTER:0:7}" "**승인 없는 보호 경로 변경을 감지했습니다** (policy-audit)

- push: \`${BEFORE:0:7}..${AFTER:0:7}\`
- 사유: $detail
- 바뀐 보호 경로:
$(bullets <<<"$changed")
- $revert_msg

의도한 변경이면: 내용을 확인한 뒤 이 이슈에 경위를 남기고 닫는다(다음부터는 채팅 승인 → \`tools/chat-approve.sh\` 로 merge).
의도하지 않은 변경이면: 되돌림 브랜치로 PR 을 열어 채팅 승인 후 merge 한다."
}

# ---------------------------------------------------------------- peer 모드
pgh() { GH_TOKEN=$PEER_TOKEN gh "$@"; }

peer_audit() {
  local now since f own_t peer_t sha prs num head prot r
  local -A seen=()
  now=$(date -u +%s)
  since=$(date -u -d "-${SINCE_HOURS:-48} hours" +%Y-%m-%dT%H:%M:%SZ)
  [[ $since < "$RULE_START" ]] && since=$RULE_START
  echo "교차 감사: $PEER (since $since)"

  # 1. 감사 파일이 같은지 — 한쪽에서 감사를 지우거나 "항상 통과"로 바꾸면 여기서 드러난다
  for f in "${AUDIT_FILES[@]}"; do
    if ! pgh api -H "Accept: application/vnd.github.raw" "repos/$PEER/contents/$f?ref=main" >"${TMPDIR:-/tmp}/peer-audit-file" 2>/dev/null; then
      report "peer $PEER missing $f" "교차 감사: $PEER 에 $f 없음" "$PEER main 에 감사 파일 \`$f\` 가 없습니다(삭제됨?). 승인 없이 지워졌다면 되돌리고, 의도한 변경이면 양쪽을 같은 PR 순서(원본 → 사본)로 맞춘다."
      continue
    fi
    if cmp -s "$f" "${TMPDIR:-/tmp}/peer-audit-file"; then
      echo "✅ $f = $PEER"
      continue
    fi
    own_t=$(git log -1 --format=%ct origin/main -- "$f" 2>/dev/null || git log -1 --format=%ct -- "$f")
    peer_t=$(pgh api "repos/$PEER/commits?sha=main&path=$f&per_page=1" --jq '.[0].commit.committer.date' 2>/dev/null)
    peer_t=$(date -u -d "${peer_t:-@0}" +%s 2>/dev/null || echo 0)
    local last=$((own_t > peer_t ? own_t : peer_t))
    if ((now - last < ${PEER_GRACE_HOURS:-12} * 3600)); then
      echo "⏳ $f 가 $PEER 와 다르지만 최근 변경(${PEER_GRACE_HOURS:-12}시간 이내) — 사본 갱신 대기로 봄"
      continue
    fi
    report "peer $PEER diff $f@${peer_t}" "교차 감사: $PEER 의 $f 가 이 저장소와 다름" "$PEER main 의 \`$f\` 가 이 저장소($REPO)와 ${PEER_GRACE_HOURS:-12}시간 넘게 다릅니다.
감사를 무력화하는 변경이 아닌지 확인한다 — 의도한 변경이면 원본 → 사본 순서로 양쪽을 같게 맞춘다(각각 채팅 승인)."
  done

  # 2. 상대 main 의 최근 변경 — 보호 경로를 바꾼 PR 의 승인 기록, PR 없는 직접 push
  while read -r sha; do
    [[ -z $sha ]] && continue
    prs=$(pgh api "repos/$PEER/commits/$sha/pulls" \
      --jq '.[] | select(.merged_at != null and .base.ref == "main") | "\(.number) \(.head.sha)"' 2>/dev/null)
    if [[ -z $prs ]]; then
      prot=$(pgh api "repos/$PEER/commits/$sha" --jq '.files[].filename' 2>/dev/null | grep -E "$PROTECTED" || true)
      [[ -z $prot ]] && continue
      report "peer $PEER ${sha:0:7}" "교차 감사: $PEER 에 PR 없는 보호 경로 변경 ${sha:0:7}" "$PEER main 에 PR 없이 들어온 커밋 \`${sha:0:7}\` 이 보호 경로를 바꿨습니다:
$(bullets <<<"$prot")

https://github.com/$PEER/commit/$sha — 의도한 변경인지 확인하고, 아니면 그 저장소에서 되돌림 PR(채팅 승인)."
      continue
    fi
    while read -r num head; do
      [[ -n ${seen[$num]:-} ]] && continue
      seen[$num]=1
      prot=$(pgh api "repos/$PEER/pulls/$num/files?per_page=100" --jq '.[].filename' 2>/dev/null | grep -E "$PROTECTED" || true)
      [[ -z $prot ]] && continue
      if r=$(approved pgh "$PEER" "$num" "$head"); then
        echo "✅ $PEER PR #$num — 최종 커밋 ${head:0:7} 승인 기록 있음 ($r)"
        continue
      fi
      report "peer $PEER PR#$num ${head:0:7}" "교차 감사: $PEER PR #$num 승인 기록 없음" "$PEER PR #$num 의 최종 커밋 \`${head:0:7}\` 에 채팅 승인 기록이 없는데 보호 경로가 바뀌었습니다:
$(bullets <<<"$prot")

https://github.com/$PEER/pull/$num — 의도한 변경인지 확인하고, 아니면 그 저장소에서 되돌림 PR(채팅 승인)."
    done <<<"$prs"
  done < <(pgh api "repos/$PEER/commits?sha=main&since=$since&per_page=100" --jq '.[].sha')
}

case ${AUDIT_MODE:-push} in
  push) push_audit ;;
  peer)
    PEER=${AUDIT_PEER:?AUDIT_PEER(상대 저장소) 필요}
    if [[ -z ${PEER_TOKEN:-} ]]; then
      echo "::error::PEER_TOKEN(SYNC_READ_TOKEN) 없음 — 교차 감사 불가"
      exit 1
    fi
    peer_audit
    ;;
  *)
    echo "알 수 없는 AUDIT_MODE: $AUDIT_MODE" >&2
    exit 2
    ;;
esac
((violations == 0)) || exit 1
