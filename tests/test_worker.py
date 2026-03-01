"""
Unit tests for the Pub/Sub push worker (worker/worker.py).
All GCP clients and subprocess calls are patched so no real GCP or
amrfinder binary is needed.
"""
import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch GCP client constructors BEFORE importing worker so module-level
# instantiation doesn't try to hit real GCP.
# ---------------------------------------------------------------------------
patchers = [
    patch("google.cloud.storage.Client", return_value=MagicMock()),
    patch("google.cloud.firestore.Client", return_value=MagicMock()),
]
for p in patchers:
    p.start()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
import worker  # noqa: E402

# Flask test client
flask_client = worker.app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_push_body(job_id="job-abc", gcs_uri="gs://bucket/uploads/in.fasta", params=None):
    """Build a Pub/Sub push envelope as Cloud Run would receive it."""
    payload = json.dumps({
        "job_id": job_id,
        "gcs_uri": gcs_uri,
        "parameters": params or {},
    }).encode("utf-8")
    return {
        "message": {
            "data": base64.b64encode(payload).decode("utf-8"),
            "messageId": "test-msg-id",
        },
        "subscription": "projects/test-project/subscriptions/amr-jobs-sub",
    }


# ---------------------------------------------------------------------------
# Tests: download_blob
# ---------------------------------------------------------------------------

class TestDownloadBlob:
    def test_parses_gcs_uri_correctly(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.storage_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        worker.download_blob("gs://my-bucket/path/to/file.fasta", "/tmp/file.fasta")

        worker.storage_client.bucket.assert_called_once_with("my-bucket")
        mock_bucket.blob.assert_called_once_with("path/to/file.fasta")
        mock_blob.download_to_filename.assert_called_once_with("/tmp/file.fasta")

    def test_nested_path_in_uri(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.storage_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        worker.download_blob("gs://bucket/a/b/c/file.fasta", "/tmp/out.fasta")
        mock_bucket.blob.assert_called_once_with("a/b/c/file.fasta")


# ---------------------------------------------------------------------------
# Tests: upload_blob
# ---------------------------------------------------------------------------

class TestUploadBlob:
    def test_uploads_and_returns_gcs_uri(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.storage_client.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        result = worker.upload_blob("/tmp/results.tsv", "results/job-123.tsv")

        mock_blob.upload_from_filename.assert_called_once_with("/tmp/results.tsv")
        assert result == f"gs://{worker.OUTPUT_BUCKET}/results/job-123.tsv"


# ---------------------------------------------------------------------------
# Tests: run_amrfinder
# ---------------------------------------------------------------------------

class TestRunAmrfinder:
    @patch("worker.subprocess.run")
    def test_basic_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="col1\tcol2\n", stderr="")
        worker.run_amrfinder("/tmp/in.fasta", "/tmp/out.tsv", {})
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["amrfinder", "-n", "/tmp/in.fasta"]
        assert "-o" in cmd
        assert "/tmp/out.tsv" in cmd

    @patch("worker.subprocess.run")
    def test_plus_flag_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder("/tmp/in.fasta", "/tmp/out.tsv", {"plus_flag": True})
        cmd = mock_run.call_args[0][0]
        assert "--plus" in cmd

    @patch("worker.subprocess.run")
    def test_organism_flag_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder("/tmp/in.fasta", "/tmp/out.tsv", {"organism": "Salmonella"})
        cmd = mock_run.call_args[0][0]
        assert "-O" in cmd
        assert "Salmonella" in cmd

    @patch("worker.subprocess.run")
    def test_ident_min_and_coverage_min(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder("/tmp/in.fasta", "/tmp/out.tsv", {
            "ident_min": 0.9,
            "coverage_min": 0.75,
        })
        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd
        assert "0.9" in cmd
        assert "-c" in cmd
        assert "0.75" in cmd

    @patch("worker.subprocess.run")
    def test_nonzero_returncode_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Database error")
        with pytest.raises(Exception, match="AMRFinderPlus failed"):
            worker.run_amrfinder("/tmp/in.fasta", "/tmp/out.tsv", {})


# ---------------------------------------------------------------------------
# Tests: handle_pubsub_push (the Cloud Run HTTP endpoint)
# ---------------------------------------------------------------------------

class TestHandlePubsubPush:
    def setup_method(self):
        worker.db.collection.return_value.document.return_value = MagicMock()

    def test_missing_envelope_returns_400(self):
        resp = flask_client.post("/", json={})
        assert resp.status_code == 400

    def test_missing_message_key_returns_400(self):
        resp = flask_client.post("/", json={"subscription": "projects/x/subscriptions/y"})
        assert resp.status_code == 400

    @patch("worker.upload_blob", return_value="gs://output/results/job-abc.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_successful_job_returns_200(self, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        resp = flask_client.post("/", json=_make_push_body())
        assert resp.status_code == 200

    @patch("worker.upload_blob", return_value="gs://output/results/job-abc.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_successful_job_updates_status_to_completed(self, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        mock_doc = MagicMock()
        worker.db.collection.return_value.document.return_value = mock_doc

        flask_client.post("/", json=_make_push_body(job_id="job-xyz"))

        update_calls = [str(c) for c in mock_doc.update.call_args_list]
        assert any("Completed" in s for s in update_calls)

    @patch("worker.run_amrfinder", side_effect=Exception("amrfinder crashed"))
    @patch("worker.download_blob")
    def test_failed_job_updates_status_to_failed(self, mock_dl, mock_run):
        mock_doc = MagicMock()
        worker.db.collection.return_value.document.return_value = mock_doc

        flask_client.post("/", json=_make_push_body())

        update_calls = [str(c) for c in mock_doc.update.call_args_list]
        assert any("Failed" in s for s in update_calls)

    @patch("worker.run_amrfinder", side_effect=Exception("crash"))
    @patch("worker.download_blob")
    def test_failed_job_still_returns_200(self, mock_dl, mock_run):
        """
        Even on AMRFinderPlus failure we return HTTP 200.
        Returning non-200 would cause Pub/Sub to redeliver infinitely.
        The error is recorded in Firestore instead.
        """
        resp = flask_client.post("/", json=_make_push_body())
        assert resp.status_code == 200

    @patch("worker.upload_blob", return_value="gs://output/results/job-abc.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    @patch("worker.os.path.exists", return_value=True)
    @patch("worker.os.remove")
    def test_tmp_files_cleaned_up(self, mock_remove, mock_exists, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        flask_client.post("/", json=_make_push_body(job_id="job-cleanup"))

        removed_paths = [c[0][0] for c in mock_remove.call_args_list]
        assert any("job-cleanup" in p for p in removed_paths)
