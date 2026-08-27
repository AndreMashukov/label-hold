# terraform/modules/bff-service
#
# One Cloud Run v2 service + one Firestore database + the IAM +
# Eventarc + Pub/Sub subscription wiring for a single BFF.
# Instantiated 3x in envs/<env>/main.tf (app-bff, redirect-bff,
# analytics-bff).
#
# Per BRAINSTORM §3, §6:
#   - One Firestore database per BFF (per-BFF bulkhead)
#   - Two service accounts: one for the Cloud Run runtime, one for
#     Eventarc triggers (the Eventarc SA needs the eventReceiver +
#     run.invoker roles on the destination service)
#   - Per BFF: list of event types the BFF subscribes to (drives the
#     Pub/Sub subscription that the Eventarc transport pushes to)
#   - app-bff has an additional Firestore→bus Eventarc trigger
#     (the "sole producer" of mapping.created)

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ----------------------------- inputs -----------------------------

variable "project_id" {
  type        = string
  description = "GCP project id"
}

variable "region" {
  type    = string
  default = "asia-southeast1"
}

variable "service_name" {
  type        = string
  description = "Cloud Run v2 service name (e.g. app-bff, redirect-bff, analytics-bff)"
}

variable "image" {
  type        = string
  description = "Container image in Artifact Registry (region-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG)"
}

variable "container_port" {
  type    = number
  default = 8080
}

variable "min_instance_count" {
  type    = number
  default = 0
}

variable "max_instance_count" {
  type    = number
  default = 3
}

variable "cpu_limit" {
  type    = string
  default = "1"
}

variable "memory_limit" {
  type    = string
  default = "512Mi"
}

variable "allow_unauthenticated" {
  type        = bool
  default     = false
  description = "If true, roles/run.invoker is granted to allUsers (JWT/auth still enforced in the app handler for app-bff/analytics-bff)"
}

variable "invoker_members" {
  type        = list(string)
  default     = []
  description = "Specific IAM members allowed to invoke (e.g. user:foo@bar). Ignored if allow_unauthenticated=true"
}

variable "env_vars" {
  type    = map(string)
  default = {}
}

variable "secret_env_vars" {
  type = map(object({
    secret_name = string
    version     = string
  }))
  default = {}
}

# Firestore
variable "firestore_database_id" {
  type        = string
  description = "Firestore database id (one per BFF per bulkhead)"
}

variable "firestore_location" {
  type    = string
  default = "asia-southeast1"
}

# Pub/Sub bus wiring
variable "events_topic_id" {
  type        = string
  description = "Full topic id (projects/.../topics/...) to subscribe to"
}

variable "events_topic_name" {
  type        = string
  description = "Short topic name (used in subscription name)"
}

# Bus consumer toggle: does this BFF consume `mapping.created` and/or
# `click.recorded` from the bus?
variable "subscribes_to_event_types" {
  type        = list(string)
  default     = []
  description = "List of event type strings this BFF listens for. Drives the Pub/Sub subscription + filter."
}

# Sole-producer trigger: is this BFF the app-bff that owns the
# Firestore->bus Eventarc trigger?
variable "enable_firestore_trigger" {
  type        = bool
  default     = false
  description = "If true, create the Firestore→Pub/Sub Eventarc trigger (only app-bff)"
}

variable "firestore_trigger_path_pattern" {
  type        = string
  default     = "pastes/{pasteId}"
  description = "Path pattern for the Firestore trigger (author-bff: pastes/{pasteId})"
}

variable "firestore_trigger_event_types" {
  type = list(string)
  default = [
    "google.cloud.firestore.document.v1.created",
    "google.cloud.firestore.document.v1.deleted",
  ]
  description = "Firestore Eventarc event types to route to this service"
}

variable "create_firestore_database" {
  type        = bool
  default     = true
  description = "If false, reuse an existing Firestore database id without creating it"
}

variable "content_bucket_name" {
  type        = string
  default     = ""
  description = "Optional GCS bucket for paste content; grants objectAdmin to runtime SA"
}

variable "enable_pubsub_publisher" {
  type        = bool
  default     = false
  description = "Grant roles/pubsub.publisher to the runtime service account"
}

variable "enable_bus_push_subscription" {
  type        = bool
  default     = false
  description = "Self-bind roles/iam.serviceAccountTokenCreator so Pub/Sub push can mint OIDC tokens for this service"
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "project_number" {
  type        = string
  description = "GCP project number (numeric). Used to construct the Google-managed service agent emails."
}

# ----------------------------- locals -----------------------------

variable "service_account_prefix" {
  type        = string
  default     = "pastebin"
  description = "Prefix for generated service-account account_ids. Must be short enough to keep total SA name <= 30 chars."
}

locals {
  labels = {
    app  = "pastebin"
    role = var.service_name
  }
  # The Eventarc Pub/Sub subscription name convention.
  bus_subscription_name = "${var.service_name}-events"
  # The Cloud Run service runtime SA
  runtime_sa_id = "${var.service_account_prefix}-${var.service_name}-runtime"
  # The Eventarc trigger SA
  eventarc_sa_id = "${var.service_account_prefix}-${var.service_name}-eventarc"
  # Google-managed service agents
  eventarc_service_agent = "service-${var.project_number}@gcp-sa-eventarc.iam.gserviceaccount.com"
  pubsub_service_agent   = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

# --------------------- Google-managed service agent IAM ---------------------
#
# Eventarc Firestore triggers need several project-level IAM bindings that
# Terraform's google_eventarc_trigger does NOT create on its own:
#
#   1. The Google-managed Eventarc service agent needs
#      roles/eventarc.eventReceiver on the project so it can receive
#      Firestore events.
#   2. The per-BFF Eventarc SA needs the same role so the trigger can
#      hand off the event to Cloud Run (and so the trigger itself is
#      authorized to exist).
#   3. The Google-managed Pub/Sub service agent needs
#      roles/pubsub.publisher on the project so it can publish to
#      Eventarc's internal transport topic (a project-level topic GCP
#      uses to wire Firestore events into Eventarc).
#
# These are per-BFF resources (count=1 only when this BFF has a
# Firestore trigger) and they are created before the trigger via
# depends_on in google_eventarc_trigger.firestore.
# See DIFFICULTIES.md §10 for the failure history.

resource "google_project_iam_member" "eventarc_sa_receiver_project" {
  count   = var.enable_firestore_trigger ? 1 : 0
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${local.eventarc_service_agent}"
}

resource "google_project_iam_member" "bff_eventarc_sa_receiver_project" {
  count   = var.enable_firestore_trigger ? 1 : 0
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = google_service_account.eventarc.member
}

resource "google_project_iam_member" "pubsub_sa_publisher_project" {
  count   = var.enable_firestore_trigger ? 1 : 0
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${local.pubsub_service_agent}"
}

# The per-BFF Eventarc SA needs to be able to mint OIDC tokens for
# ITSELF in order to push to the destination Cloud Run service via
# OIDC. Without this, Eventarc's transport subscription pushes
# arrive with an empty Authorization header and Cloud Run returns
# 401. This is `roles/iam.serviceAccountTokenCreator` granted on
# the SA itself (actAs itself).
# See DIFFICULTIES.md §10 (extended history) for the failure path.
resource "google_service_account_iam_member" "eventarc_sa_token_creator" {
  count              = var.enable_firestore_trigger ? 1 : 0
  service_account_id = google_service_account.eventarc.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.eventarc.member
}

# The bus-trigger Eventarc SA also needs the same self-token-creator
# role so it can OIDC-push to redirect-bff / analytics-bff.
resource "google_service_account_iam_member" "eventarc_sa_token_creator_bus" {
  count              = length(var.subscribes_to_event_types) > 0 ? 1 : 0
  service_account_id = google_service_account.eventarc.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.eventarc.member
}

# --------------------- Firestore database ---------------------

resource "google_firestore_database" "this" {
  count       = var.create_firestore_database ? 1 : 0
  project     = var.project_id
  name        = var.firestore_database_id
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"

  deletion_policy = var.deletion_protection ? "ABANDON" : "DELETE"
  depends_on      = []
}

# --------------------- service accounts ---------------------

# Runtime SA — the Cloud Run service runs as this.
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = local.runtime_sa_id
  display_name = "Pastebin ${var.service_name} runtime"
}

# Firestore data access
resource "google_project_iam_member" "runtime_datastore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = google_service_account.runtime.member
}

# Secret access (for secret_env_vars)
resource "google_project_iam_member" "runtime_secret_accessor" {
  count   = length(var.secret_env_vars) > 0 ? 1 : 0
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.runtime.member
}

# Pub/Sub publisher on the runtime SA:
#   - redirect-bff: click.recorded from the HTTP handler (documented exception)
#   - app-bff: mapping.created from the Firestore Eventarc trigger leg only
#     (POST /__eventarc/publish; /shorten never publishes)
# Analytics-bff never publishes.
resource "google_project_iam_member" "runtime_pubsub_publisher" {
  count   = var.enable_pubsub_publisher ? 1 : 0
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = google_service_account.runtime.member
}

# Pub/Sub push subscription: the runtime SA must be able to mint an OIDC
# token for itself so the Pub/Sub push can authenticate to the destination.
# Without this self-binding, the push arrives with "Empty Authorization
# header value" and 401s.
resource "google_service_account_iam_member" "runtime_token_creator" {
  count              = var.enable_bus_push_subscription ? 1 : 0
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.runtime.member
}

resource "google_storage_bucket_iam_member" "runtime_content_bucket" {
  count  = var.content_bucket_name != "" ? 1 : 0
  bucket = var.content_bucket_name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.runtime.member
}

# Eventarc SA — used by the Eventarc triggers (Firestore and Pub/Sub
# sources). Needs eventReceiver on the Eventarc trigger and run.invoker
# on the destination Cloud Run service.
resource "google_service_account" "eventarc" {
  project      = var.project_id
  account_id   = local.eventarc_sa_id
  display_name = "Pastebin ${var.service_name} Eventarc"
}

# Allow the Eventarc SA to invoke the Cloud Run service
resource "google_cloud_run_v2_service_iam_member" "eventarc_invoker" {
  count    = (var.enable_firestore_trigger || length(var.subscribes_to_event_types) > 0) ? 1 : 0
  project  = var.project_id
  name     = google_cloud_run_v2_service.this.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.eventarc.member
}

# Pub/Sub raw push subscription: the runtime SA needs run.invoker on its own
# Cloud Run service so the Pub/Sub push (which authenticates as the runtime SA
# via the OIDC token it mints for itself) can reach the handler.
resource "google_cloud_run_v2_service_iam_member" "runtime_invoker" {
  count    = var.enable_bus_push_subscription ? 1 : 0
  project  = var.project_id
  name     = google_cloud_run_v2_service.this.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.runtime.member
}

# The Eventarc SA also needs the eventReceiver role for the
# trigger's own IAM. We do that per-trigger below.

# --------------------- Cloud Run v2 service ---------------------

resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    containers {
      image = var.image
      ports {
        container_port = var.container_port
      }
      resources {
        limits = {
          cpu    = var.cpu_limit
          memory = var.memory_limit
        }
      }
      dynamic "env" {
        for_each = var.env_vars
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.secret_env_vars
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret_name
              version = env.value.version
            }
          }
        }
      }
    }
    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }
    service_account = google_service_account.runtime.email
  }

  labels = local.labels

  # Ignore changes to labels, scaling blocks, and deletion_protection
  # because Cloud Run revisions can flip these back and forth without
  # affecting the service identity. Manually drift-correction is
  # left to humans.
  lifecycle {
    ignore_changes = [
      labels,
      scaling,
      deletion_protection,
      template[0].scaling,
    ]
  }

  depends_on = [
    google_firestore_database.this,
  ]
}

# IAM: who can invoke the service.
# Use iam_member (additive) only — never mix with iam_policy (authoritative),
# or Eventarc/specific invoker bindings get wiped when allow_unauthenticated flips.
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  name     = google_cloud_run_v2_service.this.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "specific_invokers" {
  for_each = var.allow_unauthenticated ? toset([]) : toset(var.invoker_members)
  project  = var.project_id
  name     = google_cloud_run_v2_service.this.name
  location = var.region
  role     = "roles/run.invoker"
  member   = each.value
}

# --------------------- Eventarc: bus (Pub/Sub) → this BFF ---------------------
# One trigger per (BFF, event-type) pair. Eventarc's `matching_criteria`
# is restricted to a small set of values for Pub/Sub sources
# (specifically: type=google.cloud.pubsub.topic.v1.messagePublished),
# so we cannot filter by our custom `type` attribute at the Eventarc
# layer. Instead, the BFF HTTP handler inspects the CloudEvent's
# `type` attribute and ignores events it doesn't care about.
#
# This is a deliberate trade-off: every BFF that subscribes to the
# bus receives every event, but the per-BFF cost is one trigger per
# event-type and a fast in-process filter on the CloudEvent. For v1
# volumes this is well within Cloud Run's free tier.
resource "google_eventarc_trigger" "bus" {
  count    = length(var.subscribes_to_event_types) > 0 ? 1 : 0
  project  = var.project_id
  name     = "${var.service_name}-bus-trigger"
  location = var.region

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = var.events_topic_id
    }
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.this.name
      region  = var.region
    }
  }

  service_account = google_service_account.eventarc.email

  labels = local.labels

  depends_on = [
    google_cloud_run_v2_service_iam_member.eventarc_invoker,
    google_service_account_iam_member.eventarc_sa_token_creator_bus,
  ]
}

# --------------------- Eventarc: Firestore → Pub/Sub bus ---------------------
# ONLY for app-bff. This is the "sole producer" of mapping.created.
# The Eventarc Firestore trigger pushes DocumentEventData (protobuf)
# to a Cloud Run *publisher* service. For v1 we wire that publisher
# inline in the app-bff service: the trigger invokes the app-bff
# with a special "publish" path. The app-bff then calls
# libs/pubsub.publishEvent to the bus topic.
#
# NOTE: This trigger goes to the same app-bff service, but with a
# different path-filter so the HTTP router knows to publish.
# We use the `path-pattern` filter on the Firestore document.

resource "google_eventarc_trigger" "firestore" {
  for_each = var.enable_firestore_trigger ? toset(var.firestore_trigger_event_types) : toset([])
  project  = var.project_id
  name     = "${var.service_name}-firestore-${replace(each.value, ".", "-")}"
  location = var.firestore_location

  matching_criteria {
    attribute = "type"
    value     = each.value
  }
  matching_criteria {
    attribute = "database"
    value     = var.firestore_database_id
  }
  matching_criteria {
    attribute = "document"
    operator  = "match-path-pattern"
    value     = var.firestore_trigger_path_pattern
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.this.name
      region  = var.region
    }
  }

  service_account         = google_service_account.eventarc.email
  event_data_content_type = "application/protobuf"

  labels = local.labels

  depends_on = [
    google_cloud_run_v2_service_iam_member.eventarc_invoker,
    google_project_iam_member.eventarc_sa_receiver_project,
    google_project_iam_member.bff_eventarc_sa_receiver_project,
    google_project_iam_member.pubsub_sa_publisher_project,
    google_service_account_iam_member.eventarc_sa_token_creator,
  ]
}

# --------------------- outputs ---------------------

output "service_name" {
  value = google_cloud_run_v2_service.this.name
}

output "service_url" {
  value = google_cloud_run_v2_service.this.uri
}

output "service_account_email" {
  value = google_service_account.runtime.email
}

output "eventarc_service_account_email" {
  value = google_service_account.eventarc.email
}

output "firestore_database" {
  value = var.firestore_database_id
}

output "bus_trigger_names" {
  description = "Names of the bus Eventarc triggers (one per BFF that subscribes)"
  value       = length(var.subscribes_to_event_types) > 0 ? [google_eventarc_trigger.bus[0].name] : []
}
