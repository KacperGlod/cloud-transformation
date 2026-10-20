"""Persistent job states shared by the API and worker."""

RUNNING_STATUSES = ("processing", "normalizing", "converting", "segmenting", "transforming", "removing")
ACTIVE_STATUSES = ("uploading", "queued", *RUNNING_STATUSES, "cancelling")
RETRYABLE_STATUSES = ("failed", "cancelled")
