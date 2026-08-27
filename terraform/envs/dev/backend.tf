terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Bootstrap once per project, then set your bucket/prefix here.
  backend "gcs" {
    bucket = "serverless-503308-tfstate"
    prefix = "label-hold/dev/terraform.tfstate"
  }
}