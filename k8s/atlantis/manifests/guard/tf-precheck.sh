#!/usr/bin/env bash
# terraform 사전 검사 — init·plan 전에, PR 코드가 승인 없이 임의 코드를 실행하거나 파드의 비밀을 읽는 경로를 막는다
# 원본: windevase/infra-terraform .github/scripts/tf-precheck.sh
# 사본: windevase/gcp k8s/atlantis/manifests/guard/tf-precheck.sh (내용 동일, 원본 먼저 merge)
# 쓰는 곳: Atlantis 서버 측 workflow plan 첫 단계(PR 이 우회 불가) · code-guard(infra) · ci(gcp)
#   Atlantis 는 PR 이 열리면 승인 전에 autoplan 을 돌린다 — plan 중 실행되는 코드는 감시·승인보다 먼저 실행되므로
#   plan-guard(plan 결과 검사)·code-guard(Actions, plan 과 동시 실행)로는 막을 수 없다
# 사용: tf-precheck.sh [저장소 루트 — 기본: git 최상위]   (위반이면 종료 코드 1)
# 규칙 (해제 불가 — 정말 필요하면 이 스크립트를 정책 PR 로 고친다):
#   1 provisioner (local-exec·remote-exec·file)            plan/apply 중 셸 실행
#   2 data "external" / data "http"                         plan 중 임의 명령 실행 · 외부로 전송
#   3 provider 가 허용 목록 밖 (.terraform.lock.hcl, required_providers)
#                                                           init 때 받은 바이너리가 plan 중 실행. Atlantis init 은 -lockfile=readonly
#   4 module source 가 로컬 경로(./ ../)가 아님              원격 코드
#   5 파일 함수 인자가 리터럴 상대경로("${path.module}/…", "./…", "x/…")가 아님, abspath·pathexpand
#                                                           파드의 토큰·Secret 마운트를 읽어 plan 출력으로 노출 (변수·함수로 만든 경로도 차단)
#   6 파드 비밀 경로 문자열(/etc/aws, /var/run/secrets, /etc/gcp-platform, /atlantis-data)
#   7 provider 인증 인자(shared_config_files·shared_credentials_files·assume_role·web_identity·access/secret key 등)
#                                                           plan 읽기 역할 대신 쓰기 역할 설정(/etc/aws/config)으로 바꿔 타는 경로
#   8 .tf.json 파일 · 심볼릭 링크                              검사를 피하는 형식 / 링크로 비밀 파일을 상대경로처럼 읽기
set -uo pipefail

ALLOWED_PROVIDERS='^(registry\.terraform\.io/)?(hashicorp/(aws|google|google-beta|random|time)|cloudflare/cloudflare)$'

root=${1:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}
mapfile -t tf_files < <(find "$root" -type d \( -name .terraform -o -name .git \) -prune -o -type f -name '*.tf' -print | sort)
mapfile -t json_files < <(find "$root" -type d \( -name .terraform -o -name .git \) -prune -o -name '*.tf.json' -print | sort)
mapfile -t links < <(find "$root" -type d \( -name .terraform -o -name .git \) -prune -o -type l -print | sort)
mapfile -t lock_files < <(find "$root" -type d \( -name .terraform -o -name .git \) -prune -o -type f -name '.terraform.lock.hcl' -print | sort)

violations=()
add() { violations+=("$1"); }
rel() { echo "${1#"$root"/}"; }

# 주석 줄(#, //)을 뺀 일치 줄 — "파일:줄: 내용"
scan() { # scan <정규식>
  local f
  for f in "${tf_files[@]}"; do
    grep -nE "$1" "$f" 2>/dev/null | grep -vE '^[0-9]+:[[:space:]]*(#|//)' | sed "s|^|$(rel "$f"):|"
  done
}

while IFS= read -r hit; do [[ -n $hit ]] && add "provisioner(셸 실행) — $hit"; done < <(scan 'provisioner[[:space:]]+"')
while IFS= read -r hit; do [[ -n $hit ]] && add "data external/http(plan 중 실행·전송) — $hit"; done < <(scan 'data[[:space:]]+"(external|http)"')
# 파일 함수: 첫 인자가 리터럴 상대경로일 때만 허용 — "${path.module}/…" 또는 / ~ ../ ${ 로 시작하지 않는 문자열, ".." 없음
FILE_FN='(file|filebase64|filemd5|filesha1|filesha256|filesha512|filebase64sha256|filebase64sha512|templatefile|fileexists|fileset)'
SAFE_ARG='"(\$\{path\.(module|root)\}/)?[^"$/~.][^"]*"'
while IFS= read -r hit; do
  [[ -z $hit ]] && continue
  # 이 줄의 파일 함수 호출마다 첫 인자를 확인
  bad=0
  while IFS= read -r call; do
    [[ -z $call ]] && continue
    arg=$(sed -E "s/^${FILE_FN}[[:space:]]*\([[:space:]]*//" <<<"$call")
    if [[ ! $arg =~ ^$SAFE_ARG || $arg == *..* ]]; then bad=1; fi
  done < <(grep -oE "${FILE_FN}[[:space:]]*\([[:space:]]*[^,)]*" <<<"${hit#*:*:}")
  ((bad)) && add "파일 함수 인자가 리터럴 상대경로가 아님(비밀 읽기 우회) — $hit"
done < <(scan "\b${FILE_FN}[[:space:]]*\(")
while IFS= read -r hit; do [[ -n $hit ]] && add "abspath·pathexpand(절대 경로) — $hit"; done < <(scan '\b(abspath|pathexpand)[[:space:]]*\(')
while IFS= read -r hit; do [[ -n $hit ]] && add "파드 비밀 경로 — $hit"; done < <(scan '/etc/aws|/var/run/secrets|/etc/gcp-platform|/atlantis-data|/home/atlantis')
while IFS= read -r hit; do [[ -n $hit ]] && add "provider 인증 인자(역할 바꿔 타기) — $hit"; done < <(
  scan '^[[:space:]]*(shared_config_files|shared_credentials_files|assume_role|assume_role_with_web_identity|web_identity_token_file|web_identity_token|role_arn|access_key|secret_key|token|credentials|access_token|impersonate_service_account|api_token|api_key)[[:space:]]*(=|\{)'
)
for f in "${json_files[@]}"; do [[ -n $f ]] && add ".tf.json 파일(검사 우회 형식, 쓰지 않음) — $(rel "$f")"; done
for f in "${links[@]}"; do [[ -n $f ]] && add "심볼릭 링크(비밀 파일 가리키기) — $(rel "$f") -> $(readlink "$f")"; done

# source = "..." — 로컬(./ ../)은 모듈, ns/name 은 provider(허용 목록), 그 밖(git::, https://, 레지스트리 모듈 등)은 원격 모듈
while IFS= read -r hit; do
  [[ -z $hit ]] && continue
  src=$(sed -nE 's/.*source[[:space:]]*=[[:space:]]*"([^"]*)".*/\1/p' <<<"$hit")
  if [[ $src == ./* || $src == ../* ]]; then continue
  elif [[ $src =~ ^([a-z0-9.-]+/)?[a-z0-9-]+/[a-z0-9-]+$ ]]; then
    [[ $src =~ $ALLOWED_PROVIDERS ]] || add "허용하지 않은 provider '$src' — $hit"
  else
    add "원격 module source '$src' — $hit"
  fi
done < <(scan 'source[[:space:]]*=[[:space:]]*"')

for f in "${lock_files[@]}"; do
  while IFS= read -r p; do
    [[ -z $p ]] && continue
    [[ $p =~ $ALLOWED_PROVIDERS ]] || add "lock 파일의 허용하지 않은 provider '$p' — $(rel "$f")"
  done < <(sed -nE 's/^provider[[:space:]]+"([^"]+)".*/\1/p' "$f")
done

if ((${#violations[@]})); then
  echo "🔴 terraform 사전 검사 위반 ${#violations[@]}건 — init·plan 을 하지 않음 (tf-precheck.sh 규칙, 해제 불가)"
  printf '  - %s\n' "${violations[@]}"
  exit 1
fi
echo "✅ terraform 사전 검사 통과 (.tf ${#tf_files[@]}개, lock ${#lock_files[@]}개)"
