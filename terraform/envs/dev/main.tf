# terraform/envs/dev/main.tf
#
# Three Cloud Run v2 services:
#   - adk-runtime        (Python, ADK Control Service)
#   - leanview-consumer (Python, Pub/Sub subscriber, CPCQ Consume leg)
#   - dashboard          (static HTML, BFF to QA actor)
#
# Plus:
#   - One Pub/Sub topic (label-hold-events) + push subscription to leanview-consumer
#   - One Secret Manager secret (gemini-api-key) mounted on adk-runtime
#   - Two Firestore databases: lots-db (system of record) and lean-db (read model)
#
# We use raw Pub/Sub push for the bus, not Eventarc. The bff-service module
# requires three Eventarc-related variables (events_topic_id, events_topic_name,
# subscribes_to_event_types) for its own internal logic; we pass empty
# subscribes_to_event_types so the module skips Eventarc trigger creation
# entirely, and we wire the raw Pub/Sub subscription outside the module.

data "google_project" "this" {
  project_id = var.project_id
}

# ----------------------------- required APIs -------------------------------

resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "cloudscheduler.googleapis.com",
    "identitytoolkit.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])
  project = var.project_id
  service = each.value

  disable_on_destroy = false
}

# ----------------------------- Artifact Registry ----------------------------

resource "google_artifact_registry_repository" "apps" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  description   = "Container images for label-hold services"

  depends_on = [google_project_service.required]
}

# ----------------------------- Pub/Sub event hub ----------------------------

module "event_hub" {
  source         = "../../modules/event-hub"
  project_id     = var.project_id
  region         = var.region
  topic_name     = "label-hold-events"
  dlq_topic_name = "label-hold-events-dlq"

  depends_on = [google_project_service.required]
}

# ----------------------------- Secret Manager -------------------------------

resource "google_secret_manager_secret" "gemini_api_key" {
  project   = var.project_id
  secret_id = "gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

locals {
  smoke_test_secret_env = {
    SMOKE_TEST_KEY = {
      secret_name = "smoke-test-key"
      version     = "latest"
    }
  }
  common_service_env = merge(var.common_env_vars, {
    GCP_PROJECT_ID = var.project_id
    GCP_REGION     = var.region
    EVENTHUB_TOPIC = module.event_hub.topic_name
  })
}

# ----------------------------- Cloud Run services ---------------------------

module "adk_runtime" {
  source         = "../../modules/bff-service"
  project_id     = var.project_id
  region         = var.region
  project_number = data.google_project.this.number

  service_name           = "adk-runtime"
  service_account_prefix = "lh"
  image                  = var.adk_runtime_image

  # The Module-level event_hub wiring stays required for compatibility.
  # The adk-runtime service does NOT consume bus events — it produces them
  # via the Firestore CDC trigger below.
  events_topic_id           = module.event_hub.topic_id
  events_topic_name         = module.event_hub.topic_name
  subscribes_to_event_types = []

  allow_unauthenticated = true
  container_port        = 8080
  min_instance_count    = 0
  max_instance_count    = 3

  firestore_database_id    = "lots-db"
  firestore_location       = var.region
  # CDC: Firestore Eventarc trigger on lots/{lot_id} is the sole producer
  # of lot.* events on the bus. The trigger delivers CloudEvents to
  # /__eventarc/publish on this same service, which then publishes to
  # label-hold-events. See apps/adk-runtime/label_hold/cdc.py.
  enable_firestore_trigger       = true
  firestore_trigger_path_pattern  = "lots/{lot_id}"
  firestore_trigger_event_types   = [
    "google.cloud.firestore.document.v1.created",
    "google.cloud.firestore.document.v1.updated",
  ]
  # The runtime SA still needs roles/pubsub.publisher on the topic to
  # publish from /__eventarc/publish. The bff-service module grants the
  # project-level role; the per-topic binding is added below.
  enable_pubsub_publisher  = true

  env_vars = merge(local.common_service_env, {
    SERVICE_NAME       = "adk-runtime"
    FIRESTORE_DATABASE = "lots-db"
    FIRESTORE_COLLECTION = "lots"
    EVENTHUB_TOPIC_ID  = module.event_hub.topic_id
  })
  secret_env_vars = merge(local.smoke_test_secret_env, {
    GEMINI_API_KEY = {
      secret_name = google_secret_manager_secret.gemini_api_key.secret_id
      version     = "latest"
    }
  })

  deletion_protection = false
}

module "leanview_consumer" {
  source         = "../../modules/bff-service"
  project_id     = var.project_id
  region         = var.region
  project_number = data.google_project.this.number

  service_name           = "leanview-consumer"
  service_account_prefix = "lh"
  image                  = var.leanview_image

  events_topic_id           = module.event_hub.topic_id
  events_topic_name         = module.event_hub.topic_name
  subscribes_to_event_types = []

  allow_unauthenticated        = false
  container_port               = 8080
  min_instance_count           = 0
  max_instance_count           = 3
  enable_bus_push_subscription = true

  firestore_database_id    = "lean-db"
  firestore_location       = var.region
  enable_firestore_trigger = false

  env_vars = merge(local.common_service_env, {
    SERVICE_NAME = "leanview-consumer"
  })
  secret_env_vars = local.smoke_test_secret_env

  deletion_protection = false
}

module "dashboard" {
  source         = "../../modules/bff-service"
  project_id     = var.project_id
  region         = var.region
  project_number = data.google_project.this.number

  service_name           = "dashboard"
  service_account_prefix = "lh"
  image                  = var.dashboard_image

  events_topic_id           = module.event_hub.topic_id
  events_topic_name         = module.event_hub.topic_name
  subscribes_to_event_types = []

  allow_unauthenticated = true
  container_port        = 8080
  min_instance_count    = 0
  max_instance_count    = 2

  firestore_database_id    = "lean-db"
  firestore_location       = var.region
  enable_firestore_trigger = false
  create_firestore_database = false # leanview_consumer is the real owner

  env_vars = merge(local.common_service_env, {
    SERVICE_NAME         = "dashboard"
    ADK_RUNTIME_URL      = module.adk_runtime.service_url
    FIRESTORE_DATABASE   = "lean-db"
  })
  secret_env_vars = local.smoke_test_secret_env

  deletion_protection = false
}

# ----------------------------- Pub/Sub subscription -------------------------

resource "google_pubsub_subscription" "lean_view" {
  project = var.project_id
  name    = "label-hold-lean-view-sub"
  topic   = module.event_hub.topic_id

  ack_deadline_seconds       = 30
  message_retention_duration = "604800s" # 7 days

  push_config {
    push_endpoint = "${module.leanview_consumer.service_url}/pubsub/push"
    oidc_token {
      service_account_email = module.leanview_consumer.service_account_email
      audience              = module.leanview_consumer.service_url
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = module.event_hub.dlq_topic_id
    max_delivery_attempts = 5
  }

  depends_on = [google_project_service.required]
}

# Grant adk-runtime's runtime SA permission to publish to the bus topic.
# The event_hub module creates a separate "pastebin-eventhub-publisher" SA, but
# our app publishes from adk-runtime. Pub/Sub requires topic-level IAM (or
# publisher in the same project as the topic) — project-level roles/pubsub.publisher
# does NOT grant per-topic publish rights.
resource "google_pubsub_topic_iam_member" "adk_runtime_publisher" {
  project = var.project_id
  topic   = module.event_hub.topic_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${module.adk_runtime.service_account_email}"
}