#!/usr/bin/env bash
# 봇(GitHub App windevase-atlantis) 이름으로 커밋·push·PR 생성
# 원본: windevase/infra-terraform — windevase/gcp 에 같은 내용 사본(CI 가 동일 여부 확인, 원본을 먼저 고친다)
# - PR 작성자가 봇이어야 담당자(windevase)가 승인(Approve)할 수 있다 → Atlantis apply_requirements: approved
# - 인증: App 개인키로 JWT 서명 → 설치 토큰(1시간, infra-terraform 한정, contents·pull_requests·issues 쓰기)
#   토큰은 화면·파일·git 설정에 남기지 않는다 (push 는 일회성 http 헤더)
#
# 사용 (저장소 루트에서, Git Bash):
#   tools/bot-pr.sh commit "<메시지>"                    스테이징된 변경을 봇 author 로 커밋
#   tools/bot-pr.sh pr <브랜치> "<제목>" <본문 파일>      브랜치 push + PR 생성(main 대상)
#   tools/bot-pr.sh comment <PR 번호> "<내용>"            봇 이름으로 PR 댓글
# 환경 변수: ATLANTIS_APP_KEY (기본 /c/gcp/secret/atlantis-github-app.pem), BOT_PR_REPO (기본: 현재 폴더 git origin)
set -euo pipefail

APP_ID=5730280
APP_SLUG=windevase-atlantis
REPO=${BOT_PR_REPO:-$(git remote get-url origin | sed -E 's#(https://github.com/|git@github.com:)##; s#\.git$##')} # 기본: 현재 폴더의 origin
KEY=${ATLANTIS_APP_KEY:-/c/gcp/secret/atlantis-github-app.pem}

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
json_get() { python -I -c "import json,sys; print(json.load(sys.stdin)$1)"; }

app_jwt() {
  local now header payload sig
  now=$(date +%s)
  header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
  payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$APP_ID" | b64url)
  sig=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign "$KEY" | b64url)
  printf '%s.%s.%s' "$header" "$payload" "$sig"
}

install_token() {
  local jwt inst
  jwt=$(app_jwt)
  inst=$(curl -fsS -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/installation" | json_get "['id']")
  curl -fsS -X POST -H "Authorization: Bearer $jwt" -H "Accept: application/vnd.github+json" \
    -d "{\"repositories\":[\"${REPO#*/}\"],\"permissions\":{\"contents\":\"write\",\"pull_requests\":\"write\",\"issues\":\"write\"}}" \
    "https://api.github.com/app/installations/$inst/access_tokens" | json_get "['token']"
}

bot_identity() {
  local id
  id=$(curl -fsS "https://api.github.com/users/${APP_SLUG}%5Bbot%5D" | json_get "['id']")
  printf '%s[bot]\n%s+%s[bot]@users.noreply.github.com\n' "$APP_SLUG" "$id" "$APP_SLUG"
}

cmd=${1:?usage: bot-pr.sh commit|pr|comment ...}
shift
case $cmd in
  commit)
    msg=${1:?commit message}
    { read -r name; read -r email; } < <(bot_identity)
    git -c user.name="$name" -c user.email="$email" commit -q -m "$msg" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    git log -1 --format='%h %an: %s'
    ;;
  pr)
    branch=${1:?branch}
    title=${2:?title}
    body=${3:?body file}
    tok=$(install_token)
    auth=$(printf 'x-access-token:%s' "$tok" | openssl base64 -A)
    git -c credential.helper= -c http.extraHeader="Authorization: Basic $auth" \
      push -q "https://github.com/$REPO.git" "$branch:refs/heads/$branch"
    GH_TOKEN=$tok gh pr create -R "$REPO" --base main --head "$branch" --title "$title" --body-file "$body"
    ;;
  comment)
    num=${1:?PR number}
    text=${2:?comment}
    tok=$(install_token)
    GH_TOKEN=$tok gh pr comment "$num" -R "$REPO" --body "$text"
    ;;
  *)
    echo "알 수 없는 명령: $cmd" >&2
    exit 2
    ;;
esac
