output "topic_id" {
  value = google_pubsub_topic.budget.id
}

output "subscription_name" {
  value = google_pubsub_subscription.guard.name
}
