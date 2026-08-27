variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type    = string
  default = "asia-southeast1"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "artifact_registry_repo" {
  type        = string
  description = "Artifact Registry repo name"
}

variable "adk_runtime_image" {
  type        = string
  description = "Digest-pinned image for adk-runtime"
}

variable "leanview_image" {
  type        = string
  description = "Digest-pinned image for lean-view-consumer"
}

variable "dashboard_image" {
  type        = string
  description = "Digest-pinned image for dashboard"
}

variable "common_env_vars" {
  type    = map(string)
  default = {}
}

variable "smoke_test_key" {
  type        = string
  default     = ""
  description = "Dev-only. If non-empty, agents accept X-Smoke-Test matching this value."
}