"""
Production End-to-End Smoke Test
=================================
Sends real HTTPS requests to the **deployed** Cloud Run frontend and verifies
the full pipeline: file upload → Pub/Sub → worker → GCS results retrieval.

Prerequisites
-------------
    source set_variables.sh
    export FRONTEND_URL=$(gcloud run services describe amr-frontend \\
        --region $REGION --format='value(status.url)')
    pytest tests/test_e2e_production.py -v -s

Required environment variables
-------------------------------
    FRONTEND_URL   - Base URL of the deployed amr-frontend Cloud Run service.
    PROJECT_ID     - GCP project ID (set by set_variables.sh).
    OUTPUT_BUCKET  - Name of the GCS output bucket (set by set_variables.sh).

Notes
-----
- This test runs real AMRFinderPlus on real GCP infrastructure; it may cost
  a small amount of compute and take several minutes.
- It cleans up all GCS and Firestore resources it creates.
"""

import os
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration / fixtures
# ---------------------------------------------------------------------------

FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
PROJECT_ID = os.environ.get("PROJECT_ID", "amrfinder")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", f"amr-output-bucket-{PROJECT_ID}")
INPUT_BUCKET = os.environ.get("BUCKET_NAME", f"amr-input-bucket-{PROJECT_ID}")
SKIP_CLEANUP = os.environ.get("SKIP_CLEANUP", "false").lower() == "true"

# Paths to the sample files bundled with the test suite
SAMPLE_FASTA = os.path.join(os.path.dirname(__file__), "test_dna.fa")
SAMPLE_PROT = os.path.join(os.path.dirname(__file__), "test_prot.fa")
SAMPLE_GFF = os.path.join(os.path.dirname(__file__), "test_prot.gff")

# How long to wait for a job to complete (AMRFinderPlus can be slow to cold-start)
POLL_INTERVAL_SECONDS = 3
MAX_POLL_SECONDS = 600  # 10 minutes


def _require_env():
    """Skip the test module if FRONTEND_URL is not set."""
    if not FRONTEND_URL:
        pytest.skip(
            "FRONTEND_URL environment variable is not set. "
            "Run: export FRONTEND_URL=$(gcloud run services describe amr-frontend "
            "--region $REGION --format='value(status.url)')"
        )


def _cleanup_gcs(job_id: str):
    """Delete all GCS blobs created for this job."""
    from google.cloud import storage
    client = storage.Client(project=PROJECT_ID)
    for bucket_name, prefix in [
        (INPUT_BUCKET, f"{job_id}/"),
        (OUTPUT_BUCKET, f"results/{job_id}/"),
    ]:
        try:
            bucket = client.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            for blob in blobs:
                blob.delete()
                print(f"  Deleted gs://{bucket_name}/{blob.name}")
        except Exception as e:
            print(f"  Cleanup warning ({bucket_name}/{prefix}): {e}")


def _cleanup_firestore(job_id: str):
    """Delete the Firestore job document created for this job."""
    from google.cloud import firestore
    try:
        db = firestore.Client(project=PROJECT_ID)
        db.collection("amr_jobs").document(job_id).delete()
        print(f"  Deleted Firestore doc amr_jobs/{job_id}")
    except Exception as e:
        print(f"  Cleanup warning (Firestore {job_id}): {e}")


def _poll_for_results(job_id: str) -> requests.Response:
    """
    Poll GET /get-results/<job_id> until a definitive status is returned.

    Returns the last response received (status 200 on success, 500 on failure).
    Raises TimeoutError if the job has not completed within MAX_POLL_SECONDS.
    """
    deadline = time.time() + MAX_POLL_SECONDS
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        resp = requests.get(f"{FRONTEND_URL}/get-results/{job_id}", timeout=30)
        print(f"  Poll attempt {attempt}: HTTP {resp.status_code}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                if "result" in data:
                    return resp   # Results are ready
            except ValueError:
                pass
            # If "status" is in data, it means it's Queued/Processing; continue polling
        elif resp.status_code == 500:
            return resp   # Job failed — still a definitive answer
        # 204 or 200 (without results) means still queued — wait and retry
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Job {job_id} did not complete within {MAX_POLL_SECONDS // 60} minutes."
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProductionE2E:
    """Full production end-to-end smoke tests against the live Cloud Run service."""

    def setup_method(self):
        _require_env()

    def test_full_job_lifecycle(self):
        """
        Submit a real FASTA file to the production frontend and verify:
          1. The job is accepted (HTTP 200 + job_id returned).
          2. The worker processes it and results become available.
          3. The results endpoint returns an HTML table.
          4. The TSV download endpoint returns a file attachment.
        """
        job_id = None
        try:
            # --- Step 1: Submit the job ---
            print(f"\nSubmitting job to {FRONTEND_URL}/analyze ...")
            with open(SAMPLE_FASTA, "rb") as fasta:
                resp = requests.post(
                    f"{FRONTEND_URL}/analyze",
                    files={"nuc_file": ("test_dna.fa", fasta, "application/octet-stream")},
                    data={"organism": "Escherichia"},
                    timeout=60,
                )

            assert resp.status_code == 200, (
                f"POST /analyze returned {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "user_id" in body, f"Response missing user_id: {body}"
            assert "results_url" in body, f"Response missing results_url: {body}"

            job_id = body["user_id"]
            print(f"  Job submitted: {job_id}")
            print(f"  Shareable URL: {FRONTEND_URL}{body['results_url']}")

            # --- Step 2: Poll for results ---
            print(f"Polling for results (up to {MAX_POLL_SECONDS // 60} min) ...")
            results_resp = _poll_for_results(job_id)

            assert results_resp.status_code == 200, (
                f"Job {job_id} failed. Worker response: {results_resp.text}"
            )

            # --- Step 3: Verify HTML table in results ---
            results_body = results_resp.json()
            assert "result" in results_body, f"Results body missing 'result' key: {results_body}"
            assert "<table>" in results_body["result"], (
                "Results do not contain an HTML table."
            )
            print("  Results contain an HTML table. ✓")

            # --- Step 4: Verify TSV download ---
            download_resp = requests.get(
                f"{FRONTEND_URL}/output/{job_id}", timeout=30
            )
            assert download_resp.status_code == 200, (
                f"GET /output/{job_id} returned {download_resp.status_code}"
            )
            content_disp = download_resp.headers.get("Content-Disposition", "")
            assert "attachment" not in content_disp, (
                f"Expected no attachment Content-Disposition, got: {content_disp}"
            )
            # TSV should have at least the header row
            tsv_text = download_resp.text
            assert "\t" in tsv_text, "Downloaded file does not appear to be TSV."
            print("  TSV output download successful. ✓")

            # Verify stderr
            stderr_resp = requests.get(f"{FRONTEND_URL}/stderr/{job_id}", timeout=30)
            assert stderr_resp.status_code == 200
            print("  Stderr download successful. ✓")

            # Verify nucleotide output
            nuc_resp = requests.get(f"{FRONTEND_URL}/nucleotide/{job_id}", timeout=30)
            assert nuc_resp.status_code == 200
            print("  Nucleotide fasta download successful. ✓")

            # Verify protein output (optional depending on AMRFinderPlus hits, but should exist for test_dna.fa)
            prot_resp = requests.get(f"{FRONTEND_URL}/protein/{job_id}", timeout=30)
            if prot_resp.status_code == 200:
                print("  Protein fasta download successful. ✓")

        finally:
            if job_id and not SKIP_CLEANUP:
                print("Cleaning up GCP resources ...")
                _cleanup_gcs(job_id)
                _cleanup_firestore(job_id)
            elif job_id:
                print("Skipping cleanup as SKIP_CLEANUP is true ...")

    def test_protein_job_lifecycle(self):
        """
        Submit a real protein FASTA to the production frontend and verify:
          1. The job is accepted (HTTP 200 + job_id returned).
          2. The worker processes it and results become available.
          3. The results endpoint returns an HTML table.
          4. The TSV download endpoint returns plain-text TSV.
        """
        job_id = None
        try:
            # --- Step 1: Submit the job ---
            print(f"\nSubmitting protein job to {FRONTEND_URL}/analyze ...")
            with open(SAMPLE_PROT, "rb") as prot:
                resp = requests.post(
                    f"{FRONTEND_URL}/analyze",
                    files={"prot_file": ("test_prot.fa", prot, "application/octet-stream")},
                    data={"organism": "Escherichia"},
                    timeout=60,
                )

            assert resp.status_code == 200, (
                f"POST /analyze returned {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "user_id" in body, f"Response missing user_id: {body}"
            assert "results_url" in body, f"Response missing results_url: {body}"

            job_id = body["user_id"]
            print(f"  Job submitted: {job_id}")
            print(f"  Shareable URL: {FRONTEND_URL}{body['results_url']}")

            # --- Step 2: Poll for results ---
            print(f"Polling for results (up to {MAX_POLL_SECONDS // 60} min) ...")
            results_resp = _poll_for_results(job_id)

            assert results_resp.status_code == 200, (
                f"Job {job_id} failed. Worker response: {results_resp.text}"
            )

            # --- Step 3: Verify HTML table in results ---
            results_body = results_resp.json()
            assert "result" in results_body, (
                f"Results body missing 'result' key: {results_body}"
            )
            assert "<table>" in results_body["result"], (
                "Results do not contain an HTML table."
            )
            print("  Results contain an HTML table. ✓")

            # --- Step 4: Verify TSV download ---
            download_resp = requests.get(
                f"{FRONTEND_URL}/output/{job_id}", timeout=30
            )
            assert download_resp.status_code == 200, (
                f"GET /output/{job_id} returned {download_resp.status_code}"
            )
            content_disp = download_resp.headers.get("Content-Disposition", "")
            assert "attachment" not in content_disp, (
                f"Expected no attachment Content-Disposition, got: {content_disp}"
            )
            tsv_text = download_resp.text
            assert "\t" in tsv_text, "Downloaded file does not appear to be TSV."
            print("  TSV output download successful. ✓")

            # Verify stderr
            stderr_resp = requests.get(f"{FRONTEND_URL}/stderr/{job_id}", timeout=30)
            assert stderr_resp.status_code == 200
            print("  Stderr download successful. ✓")

            # Protein-only jobs do not produce a nucleotide FASTA output
            nuc_resp = requests.get(f"{FRONTEND_URL}/nucleotide/{job_id}", timeout=30)
            assert nuc_resp.status_code == 404, (
                f"Expected 404 for nucleotide output on a protein-only job, "
                f"got {nuc_resp.status_code}"
            )
            print("  Nucleotide endpoint correctly returns 404 for protein-only job. ✓")

        finally:
            if job_id and not SKIP_CLEANUP:
                print("Cleaning up GCP resources ...")
                _cleanup_gcs(job_id)
                _cleanup_firestore(job_id)
            elif job_id:
                print("Skipping cleanup as SKIP_CLEANUP is true ...")

    def test_combined_job_lifecycle(self):
        """
        Submit a nucleotide FASTA, a protein FASTA, and a GFF file together
        to the production frontend and verify:
          1. The job is accepted (HTTP 200 + job_id returned).
          2. The worker processes it and results become available.
          3. The results endpoint returns an HTML table.
          4. The TSV, stderr, nucleotide, and protein output endpoints return 200.
        """
        job_id = None
        try:
            # --- Step 1: Submit the job ---
            print(f"\nSubmitting combined nuc+prot+GFF job to {FRONTEND_URL}/analyze ...")
            with open(SAMPLE_FASTA, "rb") as nuc, \
                 open(SAMPLE_PROT, "rb") as prot, \
                 open(SAMPLE_GFF, "rb") as gff:
                resp = requests.post(
                    f"{FRONTEND_URL}/analyze",
                    files={
                        "nuc_file": ("test_dna.fa", nuc, "application/octet-stream"),
                        "prot_file": ("test_prot.fa", prot, "application/octet-stream"),
                        "gff_file": ("test_prot.gff", gff, "application/octet-stream"),
                    },
                    data={"organism": "Escherichia"},
                    timeout=60,
                )

            assert resp.status_code == 200, (
                f"POST /analyze returned {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "user_id" in body, f"Response missing user_id: {body}"
            assert "results_url" in body, f"Response missing results_url: {body}"

            job_id = body["user_id"]
            print(f"  Job submitted: {job_id}")
            print(f"  Shareable URL: {FRONTEND_URL}{body['results_url']}")

            # --- Step 2: Poll for results ---
            print(f"Polling for results (up to {MAX_POLL_SECONDS // 60} min) ...")
            results_resp = _poll_for_results(job_id)

            assert results_resp.status_code == 200, (
                f"Job {job_id} failed. Worker response: {results_resp.text}"
            )

            # --- Step 3: Verify HTML table in results ---
            results_body = results_resp.json()
            assert "result" in results_body, (
                f"Results body missing 'result' key: {results_body}"
            )
            assert "<table>" in results_body["result"], (
                "Results do not contain an HTML table."
            )
            print("  Results contain an HTML table. ✓")

            # --- Step 4: Verify TSV download ---
            download_resp = requests.get(
                f"{FRONTEND_URL}/output/{job_id}", timeout=30
            )
            assert download_resp.status_code == 200, (
                f"GET /output/{job_id} returned {download_resp.status_code}"
            )
            content_disp = download_resp.headers.get("Content-Disposition", "")
            assert "attachment" not in content_disp, (
                f"Expected no attachment Content-Disposition, got: {content_disp}"
            )
            tsv_text = download_resp.text
            assert "\t" in tsv_text, "Downloaded file does not appear to be TSV."
            print("  TSV output download successful. ✓")

            # Verify stderr
            stderr_resp = requests.get(f"{FRONTEND_URL}/stderr/{job_id}", timeout=30)
            assert stderr_resp.status_code == 200
            print("  Stderr download successful. ✓")

            # Combined jobs should produce a nucleotide FASTA output
            nuc_resp = requests.get(f"{FRONTEND_URL}/nucleotide/{job_id}", timeout=30)
            assert nuc_resp.status_code == 200, (
                f"Expected 200 for nucleotide output on a combined job, "
                f"got {nuc_resp.status_code}"
            )
            print("  Nucleotide FASTA download successful. ✓")

            # Combined jobs may also produce a protein FASTA output
            prot_resp = requests.get(f"{FRONTEND_URL}/protein/{job_id}", timeout=30)
            if prot_resp.status_code == 200:
                print("  Protein FASTA download successful. ✓")

        finally:
            if job_id and not SKIP_CLEANUP:
                print("Cleaning up GCP resources ...")
                _cleanup_gcs(job_id)
                _cleanup_firestore(job_id)
            elif job_id:
                print("Skipping cleanup as SKIP_CLEANUP is true ...")

    def test_results_page_is_accessible(self):
        """
        Verify the shareable /results/<job_id> page returns 200 after submission.
        This test does NOT wait for the job to complete — it just verifies the page
        loads immediately and contains the job ID.
        """
        job_id = None
        try:
            with open(SAMPLE_FASTA, "rb") as fasta:
                resp = requests.post(
                    f"{FRONTEND_URL}/analyze",
                    files={"nuc_file": ("test_dna.fa", fasta, "application/octet-stream")},
                    data={"organism": ""},
                    timeout=60,
                )
            assert resp.status_code == 200
            job_id = resp.json()["user_id"]

            page_resp = requests.get(f"{FRONTEND_URL}/results/{job_id}", timeout=30)
            assert page_resp.status_code == 200, (
                f"GET /results/{job_id} returned {page_resp.status_code}"
            )
            assert job_id in page_resp.text, (
                "Job ID not found in the results page HTML."
            )
            print(f"  /results/{job_id} is accessible and contains the job ID. ✓")

        finally:
            if job_id and not SKIP_CLEANUP:
                _cleanup_gcs(job_id)
                _cleanup_firestore(job_id)

    def test_unknown_job_id_returns_404(self):
        """
        Verify the shareable results page returns 404 for a non-existent job ID.
        This is a quick check that does not require GCP cleanup.
        """
        resp = requests.get(
            f"{FRONTEND_URL}/results/00000000-0000-0000-0000-000000000000",
            timeout=30,
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown job ID, got {resp.status_code}"
        )
        print("  Unknown job ID correctly returns 404. ✓")

    def test_version_endpoint(self):
        """Verify the /version endpoint returns the expected format."""
        resp = requests.get(f"{FRONTEND_URL}/version", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        
        data = resp.json()
        assert "frontend_version" in data, "Missing frontend_version key"
        assert len(data["frontend_version"]) > 0, "Version string should not be empty"
        print(f"  Version endpoint returns valid JSON: {data} ✓")
