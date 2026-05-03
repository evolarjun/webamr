"""
Integration tests: Frontend + Worker (Approach A — In-Process)
==============================================================
Wire the frontend and worker Flask apps together in a single pytest process.
Pub/Sub is intercepted (the frontend's send_pubsub_message is captured, and
the payload is forwarded directly to the worker's HTTP endpoint).

GCS and Firestore are REAL — tests use the GCP project configured in
environment variables (source set_variables.sh first).

amrfinder is NOT required locally — run_amrfinder is mocked with fake TSV
output so these tests work on any machine with valid GCP credentials.

Prerequisites:
    source .venv/bin/activate
    source set_variables.sh
    gcloud auth application-default login   # if not already done
    pytest tests/test_integration.py -v
"""
import base64
import io
import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure env vars are set BEFORE importing modules (they read them at module
# level). If set_variables.sh was sourced, these are already present; provide
# sensible defaults for the "amrfinder" project otherwise.
# ---------------------------------------------------------------------------
_PROJECT_ID = os.environ.setdefault("PROJECT_ID", "amrfinder")
os.environ.setdefault("BUCKET_NAME", f"amr-input-bucket-{_PROJECT_ID}")
os.environ.setdefault("OUTPUT_BUCKET", f"amr-output-bucket-{_PROJECT_ID}")
os.environ.setdefault("TOPIC_ID", "amr-jobs-topic")

# ---------------------------------------------------------------------------
# Import frontend and worker apps
# ---------------------------------------------------------------------------
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
WORKER_DIR = os.path.join(os.path.dirname(__file__), "..", "worker")

sys.path.insert(0, FRONTEND_DIR)
sys.path.insert(0, WORKER_DIR)

# Save the original cwd and switch to frontend dir for the import (taxgroup.tsv, templates)
_original_cwd = os.getcwd()
os.chdir(FRONTEND_DIR)

import main as frontend_main  # noqa: E402

os.chdir(_original_cwd)
frontend_main.limiter.enabled = False

# The worker calls upload_versions() at module level (uploads db/software
# version to GCS on cold-start). Patch it to a no-op for tests since amrfinder
# isn't installed locally.
with patch("worker.subprocess.run", return_value=MagicMock(returncode=0, stdout="4.2.7", stderr="")):
    import worker as worker_main  # noqa: E402

# Test clients
frontend_client = frontend_main.app.test_client()
worker_client = worker_main.app.test_client()

# A fake AMRFinderPlus TSV result to use when mocking run_amrfinder
FAKE_AMR_TSV = (
    "Protein identifier\tContig id\tStart\tStop\tStrand\tElement symbol\t"
    "Element name\tScope\tType\tSubtype\tClass\tSubclass\tMethod\t"
    "Target length\tReference sequence length\t% Coverage of reference\t"
    "% Identity to reference\tAlignment length\tAccession of closest sequence\t"
    "Name of closest sequence\tHMM id\tHMM description\n"
    "NA\tcontig01\t1\t861\t+\tblaTEM-156\tblaTEM family class A beta-lactamase TEM-156\t"
    "core\tAMR\tAMR\tBETA-LACTAM\tBETA-LACTAM\tBLASTX\t"
    "286\t286\t100.00\t99.65\t286\tWP_061158039.1\t"
    "class A beta-lactamase TEM-156\tNA\tNA\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _submit_job(organism="", annotation_format="standard", job_name=""):
    """
    Submit a FASTA file via the frontend, intercepting the Pub/Sub message.
    Returns (user_id, captured_pubsub_payload_dict).
    """
    captured_messages = []

    def fake_send_pubsub(message_str):
        captured_messages.append(json.loads(message_str))

    fasta_content = b">seq1\nATCGATCGATCGATCGATCGATCG\n"
    data = {
        "organism": organism,
        "annotation_format": annotation_format,
        "job_name": job_name,
        "nuc_file": (io.BytesIO(fasta_content), "test_sample.fasta"),
    }

    with patch.object(frontend_main, "send_pubsub_message", side_effect=fake_send_pubsub):
        resp = frontend_client.post("/analyze", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200, f"Frontend /analyze failed: {resp.get_json()}"
    body = resp.get_json()
    user_id = body["user_id"]

    assert len(captured_messages) == 1, f"Expected 1 Pub/Sub message, got {len(captured_messages)}"
    return user_id, captured_messages[0]


def _forward_to_worker(pubsub_payload, mock_amr_output=FAKE_AMR_TSV):
    """
    Format the captured Pub/Sub payload as a push envelope and POST it to
    the worker, with run_amrfinder mocked to produce fake output.
    """
    # Build the Pub/Sub push envelope that Cloud Run would receive
    encoded = base64.b64encode(json.dumps(pubsub_payload).encode("utf-8")).decode("utf-8")
    envelope = {
        "message": {
            "data": encoded,
            "messageId": "integration-test-msg",
        },
        "subscription": "projects/test/subscriptions/test-sub",
    }

    def fake_run_amrfinder(*, nuc_input, prot_input, gff_input, output_tsv, stderr_path, nucleotide_path, protein_path, params):
        """Write fake TSV output the same way the real amrfinder would."""
        with open(output_tsv, "w") as f:
            f.write(mock_amr_output)
        with open(stderr_path, "w") as f:
            f.write("AMRFinderPlus mock - integration test\n")
        return mock_amr_output

    with patch.object(worker_main, "run_amrfinder", side_effect=fake_run_amrfinder):
        resp = worker_client.post("/", json=envelope)

    return resp


def _poll_results(user_id, max_attempts=3, delay=0.5):
    """Poll the frontend's get-results endpoint until results are available."""
    for _ in range(max_attempts):
        resp = frontend_client.get(f"/get-results/{user_id}")
        if resp.status_code == 200:
            return resp
        time.sleep(delay)
    return resp


def _cleanup_gcs(user_id):
    """Delete test blobs from GCS input and output buckets."""
    from google.cloud import storage as gcs
    project_id = os.environ.get("PROJECT_ID", "amrfinder")
    client = gcs.Client(project=project_id)

    input_bucket_name = os.environ.get("BUCKET_NAME", f"amr-input-bucket-{project_id}")
    output_bucket_name = os.environ.get("OUTPUT_BUCKET", f"amr-output-bucket-{project_id}")

    for bucket_name, prefix in [
        (input_bucket_name, f"{user_id}/"),
        (output_bucket_name, f"results/{user_id}/"),
    ]:
        try:
            bucket = client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            for blob in blobs:
                blob.delete()
        except Exception as e:
            print(f"Cleanup warning ({bucket_name}/{prefix}): {e}")


def _cleanup_firestore(user_id):
    """Delete test job document from Firestore."""
    from google.cloud import firestore as fs
    project_id = os.environ.get("PROJECT_ID", "amrfinder")
    try:
        db = fs.Client(project=project_id)
        db.collection("amr_jobs").document(user_id).delete()
    except Exception as e:
        print(f"Cleanup warning (Firestore {user_id}): {e}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """Full flow: frontend submit → worker process → frontend retrieve results."""

    def test_submit_and_process_job(self):
        """Submit a file, process it via the worker, and retrieve results."""
        user_id, payload = _submit_job()
        try:
            # Forward the message to the worker
            worker_resp = _forward_to_worker(payload)
            assert worker_resp.status_code == 200, f"Worker failed: {worker_resp.data}"

            # Poll the frontend for results
            results_resp = _poll_results(user_id)
            assert results_resp.status_code == 200, (
                f"Expected results for {user_id}, got {results_resp.status_code}"
            )
            body = results_resp.get_json()
            assert "result" in body
            assert "blaTEM" in body["result"]
            assert "<table>" in body["result"]
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)

    def test_submit_with_organism(self):
        """Submit with an organism parameter and verify it flows through."""
        user_id, payload = _submit_job(organism="Salmonella")
        try:
            assert payload["parameters"].get("organism") == "Salmonella"

            worker_resp = _forward_to_worker(payload)
            assert worker_resp.status_code == 200

            results_resp = _poll_results(user_id)
            assert results_resp.status_code == 200
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)

    def test_submit_with_annotation_format(self):
        """Submit with annotation_format and verify it flows to the worker payload."""
        user_id, payload = _submit_job(annotation_format="prokka")
        try:
            assert payload["parameters"].get("annotation_format") == "prokka"
            worker_resp = _forward_to_worker(payload)
            assert worker_resp.status_code == 200
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)

    def test_submit_with_job_name(self):
        """Submit with job_name and verify it is included in the payload."""
        user_id, payload = _submit_job(job_name="Integration Job_1")
        try:
            assert payload.get("job_name") == "Integration Job_1"
            worker_resp = _forward_to_worker(payload)
            assert worker_resp.status_code == 200
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)

    def test_failed_job_reports_error(self):
        """When the worker fails, the frontend should report the error."""
        user_id, payload = _submit_job()
        try:
            # Make run_amrfinder raise an exception
            def failing_amrfinder(*, nuc_input, prot_input, gff_input, output_tsv, stderr_path, nucleotide_path, protein_path, params):
                with open(stderr_path, "w") as f:
                    f.write("FATAL: database not found\n")
                raise Exception("AMRFinderPlus failed: database not found")

            encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")
            envelope = {
                "message": {"data": encoded, "messageId": "fail-test-msg"},
                "subscription": "projects/test/subscriptions/test-sub",
            }

            with patch.object(worker_main, "run_amrfinder", side_effect=failing_amrfinder):
                worker_resp = worker_client.post("/", json=envelope)

            # Worker should still return 200 (ack the message)
            assert worker_resp.status_code == 200

            # Frontend should report the failure
            results_resp = frontend_client.get(f"/get-results/{user_id}")
            assert results_resp.status_code == 500
            body = results_resp.get_json()
            assert "failed" in body["error"].lower() or "error" in body["error"].lower()
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)

    def test_download_output_file(self):
        """After processing, the output TSV should be downloadable."""
        user_id, payload = _submit_job()
        try:
            _forward_to_worker(payload)

            resp = frontend_client.get(f"/output/{user_id}")
            assert resp.status_code == 200
            assert "attachment" not in resp.headers.get("Content-Disposition", "")
            assert resp.mimetype == "text/plain"
            assert b"blaTEM" in resp.data
        finally:
            _cleanup_gcs(user_id)
            _cleanup_firestore(user_id)
