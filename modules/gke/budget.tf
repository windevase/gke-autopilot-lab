data "google_project" "this" {}

# 프로모션 크레딧 차감 전(무료 티어·할인은 차감 후) 사용액 기준 알림 → 월 크레딧(약 ₩13~15k)을 넘기기 전에 인지
resource "google_billing_budget" "monthly" {
  count = var.enable_budget ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "${var.cluster_name}-monthly-budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
    # 프로모션(Developer Program) 크레딧만 빼고 나머지 크레딧·할인은 모두 차감
    # → "Developer Program 크레딧을 얼마나 소진 중인지"를 기준으로 알림/차단
    # (2026-11-09) FREE_TIER만 차감하던 때는 GKE 관리비(Autopilot Kubernetes Clusters, 하루 ~₩3,400)가
    #  보고서상 전액 상쇄되는데도 예산에 그대로 잡혀 → 실제 청구 0원인데 3일 뒤 kill switch가 작동할 상황이었음
    credit_types_treatment = "INCLUDE_SPECIFIED_CREDITS"
    credit_types = [
      "FREE_TIER",
      "DISCOUNT",
      "SUBSCRIPTION_BENEFIT",
      "SUSTAINED_USAGE_DISCOUNT",
      "COMMITTED_USAGE_DISCOUNT",
      "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE",
    ] # FEE_UTILIZATION_OFFSET은 Budget API가 거부(400 not a valid credit type)
  }

  amount {
    specified_amount {
      currency_code = "KRW"
      units         = tostring(var.budget_amount_krw)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.9
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  # 비용 갱신마다 Pub/Sub로 발행 → 클러스터 budget-guard CronJob이 100% 이상이면 워크로드 차단
  # (기본 이메일 알림은 그대로 유지)
  dynamic "all_updates_rule" {
    for_each = var.budget_pubsub_topic == null ? [] : [1]
    content {
      pubsub_topic   = var.budget_pubsub_topic
      schema_version = "1.0"
    }
  }

  depends_on = [google_project_service.services]
}
