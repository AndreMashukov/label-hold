output "adk_runtime_url" {
  value = module.adk_runtime.service_url
}

output "leanview_consumer_url" {
  value = module.leanview_consumer.service_url
}

output "dashboard_url" {
  value = module.dashboard.service_url
}

output "event_hub_topic" {
  value = module.event_hub.topic_name
}

output "artifact_registry_repo" {
  value = google_artifact_registry_repository.apps.name
}

output "gemini_secret_name" {
  value = google_secret_manager_secret.gemini_api_key.secret_id
}