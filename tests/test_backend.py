"""
Unit tests for the FastAPI backend (backend/main.py).
GCP clients (storage, pubsub, firestore) are patched so no real GCP credentials are needed.
The default API key ('dev-secret-key') is sent via the x-api-key header in every request.
"""

API_KEY_HEADERS = {"x-api-key": "dev-secret-key"}
import json
import sys
import os
from unittest.mock import MagicMock, patch
from google.api_core.exceptions import NotFound, BadRequest

import pytest

# ---------------------------------------------------------------------------
# Patch GCP client constructors BEFORE importing main so module-level
# instantiation doesn't try to hit real GCP.
# ---------------------------------------------------------------------------
MOCK_STORAGE = MagicMock()
MOCK_PUBLISHER = MagicMock()
MOCK_FIRESTORE = MagicMock()

patchers = [
    patch("google.cloud.storage.Client", return_value=MOCK_STORAGE),
    patch("google.cloud.pubsub_v1.PublisherClient", return_value=MOCK_PUBLISHER),
    patch("google.cloud.firestore.Client", return_value=MOCK_FIRESTORE),
]
for p in patchers:
    p.start()

# Now safe to import main
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import main  # noqa: E402  (imported after patches)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signed_url_blob():
    """Return a mock blob that produces a plausible signed URL."""
    mock_blob = MagicMock()
    mock_blob.generate_signed_url.return_value = (
        "https://storage.googleapis.com/amr-input-bucket/uploads/test-uuid-file.fasta"
        "?X-Goog-Signature=fakesig"
    )
    return mock_blob


def _make_submit_blob(size_bytes=1024):
    """Return a mock blob for submit-job, with a configurable size attribute."""
    mock_blob = MagicMock()
    mock_blob.size = size_bytes
    return mock_blob


# ---------------------------------------------------------------------------
# Tests: /api/upload-url
# ---------------------------------------------------------------------------

class TestUploadUrl:
    def setup_method(self):
        # Reset storage mock for each test
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_signed_url_blob()

    def test_returns_signed_url(self):
        resp = client.post("/api/upload-url", json={"filename": "sample.fasta"}, headers=API_KEY_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "signed_url" in data
        assert data["signed_url"].startswith("https://storage.googleapis.com")

    def test_returns_gcs_uri(self):
        resp = client.post("/api/upload-url", json={"filename": "sample.fasta"}, headers=API_KEY_HEADERS)
        data = resp.json()
        assert "gcs_uri" in data
        assert data["gcs_uri"].startswith("gs://")

    def test_returns_max_upload_bytes(self):
        resp = client.post("/api/upload-url", json={"filename": "sample.fasta"}, headers=API_KEY_HEADERS)
        data = resp.json()
        assert data["max_upload_bytes"] == 10 * 1024 * 1024

    def test_missing_filename_returns_422(self):
        """Pydantic validation should reject a body with no filename."""
        resp = client.post("/api/upload-url", json={}, headers=API_KEY_HEADERS)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: /api/submit-job
# ---------------------------------------------------------------------------

class TestSubmitJob:
    def setup_method(self):
        # Mock Firestore document reference
        MOCK_FIRESTORE.collection.return_value.document.return_value = MagicMock()
        # Mock Pub/Sub publish future
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-123"
        MOCK_PUBLISHER.publish.return_value = mock_future
        # Default: a small, valid file (1 KB)
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_submit_blob(size_bytes=1024)

    def _post_job(self, **extra):
        payload = {
            "gcs_uri": "gs://amr-input-bucket/uploads/test-uuid-sample.fasta",
            **extra,
        }
        return client.post("/api/submit-job", json=payload, headers=API_KEY_HEADERS)

    def test_returns_job_id_and_pending_status(self):
        resp = self._post_job()
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "Pending"

    def test_job_id_is_uuid_format(self):
        import uuid
        resp = self._post_job()
        job_id = resp.json()["job_id"]
        # Should not raise
        uuid.UUID(job_id)

    def test_pubsub_publish_called_once(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_job(plus_flag=True, organism="Salmonella")
        MOCK_PUBLISHER.publish.assert_called_once()

    def test_published_message_contains_job_id(self):
        MOCK_PUBLISHER.publish.reset_mock()
        resp = self._post_job(plus_flag=True)
        job_id = resp.json()["job_id"]
        call_args = MOCK_PUBLISHER.publish.call_args
        # Second positional arg is the encoded message bytes
        message_bytes = call_args[0][1]
        message_dict = json.loads(message_bytes.decode("utf-8"))
        assert message_dict["job_id"] == job_id

    def test_with_optional_params(self):
        resp = self._post_job(
            plus_flag=True,
            organism="Escherichia",
            ident_min=0.9,
            coverage_min=0.8,
        )
        assert resp.status_code == 200

    def test_missing_gcs_uri_returns_422(self):
        resp = client.post("/api/submit-job", json={}, headers=API_KEY_HEADERS)
        assert resp.status_code == 422

    def test_pubsub_failure_returns_500(self):
        MOCK_PUBLISHER.publish.side_effect = Exception("PubSub unavailable")
        resp = self._post_job()
        assert resp.status_code == 500
        # Reset for future tests
        MOCK_PUBLISHER.publish.side_effect = None

    def test_oversized_file_returns_413(self):
        """Files exceeding 10 MB must be rejected before publishing to Pub/Sub."""
        oversized_blob = _make_submit_blob(size_bytes=11 * 1024 * 1024)  # 11 MB
        MOCK_STORAGE.bucket.return_value.blob.return_value = oversized_blob
        resp = self._post_job()
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_oversized_file_deletes_blob(self):
        """The oversized blob must be deleted from GCS, not left in the bucket."""
        oversized_blob = _make_submit_blob(size_bytes=11 * 1024 * 1024)
        MOCK_STORAGE.bucket.return_value.blob.return_value = oversized_blob
        self._post_job()
        oversized_blob.delete.assert_called_once()

    def test_oversized_file_does_not_publish(self):
        """Pub/Sub must not receive a message for an oversized file."""
        MOCK_PUBLISHER.publish.reset_mock()
        oversized_blob = _make_submit_blob(size_bytes=11 * 1024 * 1024)
        MOCK_STORAGE.bucket.return_value.blob.return_value = oversized_blob
        self._post_job()
        MOCK_PUBLISHER.publish.assert_not_called()

    def test_invalid_uri_no_gs_prefix_returns_400(self):
        """A URI without gs:// prefix must return 400."""
        resp = client.post(
            "/api/submit-job",
            json={"gcs_uri": "s3://amr-input-bucket/uploads/file.fasta"},
            headers=API_KEY_HEADERS,
        )
        assert resp.status_code == 400
        assert "gs://" in resp.json()["detail"]

    def test_invalid_uri_missing_object_returns_400(self):
        """A URI with no object path (only bucket) must return 400."""
        resp = client.post(
            "/api/submit-job",
            json={"gcs_uri": "gs://amr-input-bucket"},
            headers=API_KEY_HEADERS,
        )
        assert resp.status_code == 400
        assert "gs://bucket/object" in resp.json()["detail"]

    def test_gcs_not_found_returns_404(self):
        """When GCS blob.reload() raises NotFound, the endpoint must return 404."""
        not_found_blob = MagicMock()
        not_found_blob.reload.side_effect = NotFound("blob not found")
        MOCK_STORAGE.bucket.return_value.blob.return_value = not_found_blob
        resp = self._post_job()
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_gcs_bad_request_returns_400(self):
        """When GCS blob.reload() raises BadRequest, the endpoint must return 400."""
        bad_req_blob = MagicMock()
        bad_req_blob.reload.side_effect = BadRequest("bad bucket name")
        MOCK_STORAGE.bucket.return_value.blob.return_value = bad_req_blob
        resp = self._post_job()
        assert resp.status_code == 400
        assert "invalid gcs request" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: /api/status/{job_id}
# ---------------------------------------------------------------------------

class TestJobStatus:
    def _make_doc(self, exists=True, data=None):
        mock_doc = MagicMock()
        mock_doc.exists = exists
        mock_doc.to_dict.return_value = data or {
            "job_id": "test-job-id",
            "status": "Processing",
            "result_uri": None,
            "error_message": None,
        }
        return mock_doc

    def setup_method(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._make_doc()
        )

    def test_returns_status_for_existing_job(self):
        resp = client.get("/api/status/test-job-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "Processing"

    def test_completed_job_includes_result_uri(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._make_doc(data={
                "job_id": "done-job",
                "status": "Completed",
                "result_uri": "gs://amr-output-bucket/results/done-job.tsv",
                "error_message": None,
            })
        )
        resp = client.get("/api/status/done-job")
        assert resp.json()["result_uri"].startswith("gs://")

    def test_missing_job_returns_404(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._make_doc(exists=False)
        )
        resp = client.get("/api/status/nonexistent-job")
        assert resp.status_code == 404
