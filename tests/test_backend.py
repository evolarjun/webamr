"""
Unit tests for the FastAPI backend (backend/main.py).
GCP clients (storage, pubsub, firestore) are patched so no real GCP credentials are needed.
"""
import json
import sys
import os
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Tests: /api/upload-url
# ---------------------------------------------------------------------------

class TestUploadUrl:
    def setup_method(self):
        # Reset storage mock for each test
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_signed_url_blob()

    def test_returns_signed_url(self):
        resp = client.post("/api/upload-url", json={"filename": "sample.fasta"})
        assert resp.status_code == 200
        data = resp.json()
        assert "signed_url" in data
        assert data["signed_url"].startswith("https://storage.googleapis.com")

    def test_returns_gcs_uri(self):
        resp = client.post("/api/upload-url", json={"filename": "sample.fasta"})
        data = resp.json()
        assert "gcs_uri" in data
        assert data["gcs_uri"].startswith("gs://")

    def test_missing_filename_returns_422(self):
        """Pydantic validation should reject a body with no filename."""
        resp = client.post("/api/upload-url", json={})
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

    def _post_job(self, **extra):
        payload = {
            "gcs_uri": "gs://amr-input-bucket/uploads/test-uuid-sample.fasta",
            **extra,
        }
        return client.post("/api/submit-job", json=payload)

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
        resp = client.post("/api/submit-job", json={})
        assert resp.status_code == 422

    def test_pubsub_failure_returns_500(self):
        MOCK_PUBLISHER.publish.side_effect = Exception("PubSub unavailable")
        resp = self._post_job()
        assert resp.status_code == 500
        # Reset for future tests
        MOCK_PUBLISHER.publish.side_effect = None


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
