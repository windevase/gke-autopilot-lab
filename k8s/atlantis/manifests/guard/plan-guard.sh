#!/usr/bin/env bash
# plan JSON 을 검사해 위험한 변경을 차단한다.
#   plan-guard.sh PLAN_JSON LABELS_CSV OUT_MD
# 종료 코드: 0 통과 / 1 차단. OUT_MD 에 요약(PR 댓글·Step Summary 용)을 쓴다.
#
# 규칙(차단 → 해제 라벨). 해제 라벨이 없는 규칙은 라벨로도 풀 수 없다.
#   destroy        삭제·교체(replace)            → allow-destroy
#   force-destroy  S3 force_destroy = true       → allow-force-destroy
#   public-s3      S3 공개 접근 허용·public ACL  → (해제 불가)
#   open-ingress   0.0.0.0/0, ::/0 인바운드      → allow-open-ingress
#   iam-wildcard   IAM Allow Action=* Resource=* → allow-iam-wildcard
#   admin-policy   AdministratorAccess 등 부착   → allow-admin-policy
#   access-key     IAM 액세스 키 생성            → (해제 불가, state 에 비밀값 저장)
# GCP (2026-11-14 추가)
#   open-ingress   방화벽 INGRESS source 0.0.0.0/0·::/0         → allow-open-ingress
#   public-gcs     버킷 IAM allUsers·allAuthenticatedUsers, public_access_prevention 해제 → (해제 불가)
#   admin-policy   프로젝트 IAM roles/owner·roles/editor·IAM 관리 역할 부여 → allow-admin-policy
#   force-destroy  GCS force_destroy = true                     → allow-force-destroy
#   access-key     서비스 계정 키 생성                          → (해제 불가)
#   cluster-guard  GKE 클러스터 deletion_protection 해제        → (해제 불가, bootstrap 은 로컬에서만)
# 원본: windevase/infra-terraform — Atlantis 는 windevase/gcp k8s/atlantis/manifests/guard/plan-guard.sh 사본 사용(CI 가 동일 여부 확인)
set -euo pipefail

plan=$1
labels=",${2:-},"
out=$3

declare -A unlock=(
  [destroy]=allow-destroy
  [force-destroy]=allow-force-destroy
  [open-ingress]=allow-open-ingress
  [iam-wildcard]=allow-iam-wildcard
  [admin-policy]=allow-admin-policy
)

findings=$(jq -r '
  def after: (.change.after // {});
  def acts: .change.actions;
  def deleting: (acts | index("delete")) != null;
  def open_cidr: . == "0.0.0.0/0" or . == "::/0";
  def stmts: (try (fromjson | .Statement | if type == "array" then . else [.] end) catch []);
  def star: if type == "array" then any(.[]; . == "*") else . == "*" end;

  .resource_changes[]? | select(.mode == "managed") | . as $r |
  (
    (if deleting then "destroy\t\($r.address)\t\(acts | join("+"))" else empty end),

    (if .type == "aws_s3_bucket" and after.force_destroy == true
      then "force-destroy\t\($r.address)\tforce_destroy = true" else empty end),

    (if .type == "aws_s3_bucket_public_access_block" and (deleting | not)
        and ([after.block_public_acls, after.block_public_policy, after.ignore_public_acls, after.restrict_public_buckets] | any(. == false))
      then "public-s3\t\($r.address)\tpublic access block 일부 해제" else empty end),

    (if .type == "aws_s3_bucket_acl" and ((after.acl // "") | test("public"))
      then "public-s3\t\($r.address)\tacl = \(after.acl)" else empty end),

    (if .type == "aws_security_group"
      then (after.ingress // [])[]
        | select(((.cidr_blocks // []) + (.ipv6_cidr_blocks // [])) | any(open_cidr))
        | "open-ingress\t\($r.address)\t\(.protocol) \(.from_port)-\(.to_port)"
      else empty end),

    (if .type == "aws_vpc_security_group_ingress_rule"
        and (((after.cidr_ipv4 // "") | open_cidr) or ((after.cidr_ipv6 // "") | open_cidr))
      then "open-ingress\t\($r.address)\t\(after.ip_protocol) \(after.from_port)-\(after.to_port)" else empty end),

    (if .type == "aws_security_group_rule" and after.type == "ingress"
        and (((after.cidr_blocks // []) + (after.ipv6_cidr_blocks // [])) | any(open_cidr))
      then "open-ingress\t\($r.address)\t\(after.protocol) \(after.from_port)-\(after.to_port)" else empty end),

    (if (.type | test("^aws_iam_(role_|user_|group_)?policy$")) and ((after.policy | type) == "string")
      then after.policy | stmts[]
        | select(.Effect == "Allow" and ((.Action // "") | star) and ((.Resource // "") | star))
        | "iam-wildcard\t\($r.address)\tAllow Action=* Resource=*"
      else empty end),

    (if (.type | test("^aws_iam_(role|user|group)_policy_attachment$|^aws_iam_policy_attachment$"))
        and ((after.policy_arn // "") | test(":policy/(AdministratorAccess|IAMFullAccess)$"))
      then "admin-policy\t\($r.address)\t\(after.policy_arn)" else empty end),

    (if .type == "aws_iam_access_key" and (acts | index("create"))
      then "access-key\t\($r.address)\tIAM 액세스 키 생성" else empty end),

    # ── GCP ──
    (if .type == "google_compute_firewall" and (deleting | not) and ((after.direction // "INGRESS") == "INGRESS")
        and ((after.source_ranges // []) | any(open_cidr))
      then "open-ingress\t\($r.address)\tsource_ranges \(after.source_ranges | join(","))" else empty end),

    (if (.type | test("^google_storage_bucket_iam_(member|binding)$")) and (deleting | not)
        and ([after.member // empty, (after.members // [])[]] | any(. == "allUsers" or . == "allAuthenticatedUsers"))
      then "public-gcs\t\($r.address)\t\(after.role) → 전체 공개" else empty end),

    (if .type == "google_storage_bucket" and (deleting | not) and ((after.public_access_prevention // "enforced") != "enforced")
      then "public-gcs\t\($r.address)\tpublic_access_prevention = \(after.public_access_prevention)" else empty end),

    (if .type == "google_storage_bucket" and after.force_destroy == true
      then "force-destroy\t\($r.address)\tforce_destroy = true" else empty end),

    (if (.type | test("^google_(project|folder|organization)_iam_(member|binding)$")) and (deleting | not)
        and ((after.role // "") | test("^roles/(owner|editor|resourcemanager\\.(project|folder|organization)IamAdmin|iam\\.(securityAdmin|serviceAccountAdmin|serviceAccountKeyAdmin|serviceAccountTokenCreator|workloadIdentityPoolAdmin))$"))
      then "admin-policy\t\($r.address)\t\(after.role)" else empty end),

    (if .type == "google_service_account_key" and (acts | index("create"))
      then "access-key\t\($r.address)\t서비스 계정 키 생성" else empty end),

    (if .type == "google_container_cluster" and (deleting | not) and (after.deletion_protection == false)
      then "cluster-guard\t\($r.address)\tdeletion_protection = false" else empty end)
  )
' "$plan")

# 변경 건수 요약
read -r n_create n_update n_replace n_delete < <(jq -r '
  [.resource_changes[]? | select(.mode == "managed") | .change.actions] as $a |
  [ ($a | map(select(. == ["create"])) | length),
    ($a | map(select(. == ["update"])) | length),
    ($a | map(select(index("create") and index("delete"))) | length),
    ($a | map(select(. == ["delete"])) | length) ] | @tsv' "$plan")

blocked=0
rows=""
while IFS=$'\t' read -r rule addr detail; do
  [[ -z ${rule:-} ]] && continue
  label=${unlock[$rule]:-}
  if [[ -n $label && $labels == *",$label,"* ]]; then
    state="⚪ 허용(\`$label\`)"
  else
    state="🔴 차단"
    blocked=1
  fi
  rows+="| $state | \`$rule\` | \`$addr\` | $detail | ${label:+\`$label\`} |"$'\n'
done <<<"$findings"

{
  echo "| 생성 | 변경 | 교체 | 삭제 |"
  echo "|---|---|---|---|"
  echo "| $n_create | $n_update | $n_replace | $n_delete |"
  echo
  if [[ -n $rows ]]; then
    echo "### 감시 결과"
    echo "| 결과 | 규칙 | 리소스 | 내용 | 해제 라벨 |"
    echo "|---|---|---|---|---|"
    printf '%s' "$rows"
    echo
    if (( blocked )); then
      echo "> 🔴 **차단됨** — 요청 범위의 변경이 맞는지 확인하고, 의도한 경우에만 담당자가 해제 라벨을 붙인 뒤 재실행한다."
    fi
  else
    echo "✅ 감시 규칙 위반 없음"
  fi
  echo
  echo "변경 리소스:"
  jq -r '.resource_changes[]? | select(.mode == "managed" and .change.actions != ["no-op"] and .change.actions != ["read"])
         | "- `\(.change.actions | join("+"))` \(.address)"' "$plan" | head -50
} >"$out"

exit "$blocked"
