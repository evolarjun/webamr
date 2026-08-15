"""
Unit tests for the Pub/Sub push worker (worker/worker.py).
All GCP clients and subprocess calls are patched so no real GCP or
amrfinder binary is needed.
"""
import base64
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch, mock_open

import pytest

# ---------------------------------------------------------------------------
# Patch GCP client constructors BEFORE importing worker so module-level
# instantiation doesn't try to hit real GCP.
# ---------------------------------------------------------------------------
patchers = [
    patch("google.cloud.storage.Client", return_value=MagicMock()),
    patch("google.cloud.firestore.Client", return_value=MagicMock()),
]
worker = None
flask_client = None


def setup_module(module):
    """Import worker with patched clients so tests are isolated from other modules."""
    global worker, flask_client

    for p in patchers:
        p.start()

    # Support both local dev (../worker) and flattened Docker structure (..)
    test_dir = os.path.dirname(__file__)
    worker_dir = os.path.join(test_dir, "..", "worker")
    if not os.path.exists(worker_dir):
        worker_dir = os.path.join(test_dir, "..")

    worker_dir = os.path.abspath(worker_dir)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)

    # Ensure we do not reuse a previously imported worker module.
    sys.modules.pop("worker", None)
    worker = importlib.import_module("worker")
    flask_client = worker.app.test_client()


def teardown_module(module):
    """Stop patches and remove worker module to prevent cross-test contamination."""
    sys.modules.pop("worker", None)
    for p in patchers:
        p.stop()


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


def _make_raw_push_body(raw_bytes):
    """Build a Pub/Sub push envelope with arbitrary base64-encoded bytes."""
    return {
        "message": {
            "data": base64.b64encode(raw_bytes).decode("utf-8"),
            "messageId": "test-msg-id",
        },
        "subscription": "projects/test-project/subscriptions/amr-jobs-sub",
    }


# ---------------------------------------------------------------------------
# Tests: download_blob
# ---------------------------------------------------------------------------

class TestDownloadBlob:
    def setup_method(self):
        # upload_versions() runs in a background thread and may have already 
        # called storage_client.bucket(); reset the mock before each test.
        worker.get_storage_client().bucket.reset_mock()

    def test_parses_gcs_uri_correctly(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.get_storage_client().bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        worker.download_blob("gs://my-bucket/path/to/file.fasta", "/tmp/file.fasta")

        worker.get_storage_client().bucket.assert_called_once_with("my-bucket")
        mock_bucket.blob.assert_called_once_with("path/to/file.fasta")
        mock_blob.download_to_filename.assert_called_once_with("/tmp/file.fasta")

    def test_nested_path_in_uri(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.get_storage_client().bucket.return_value = mock_bucket
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
        worker.get_storage_client().bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        result = worker.upload_blob("/tmp/results.tsv", "results/job-123/results.tsv")

        mock_blob.upload_from_filename.assert_called_once_with("/tmp/results.tsv")
        assert result == f"gs://{worker.OUTPUT_BUCKET}/results/job-123/results.tsv"


# ---------------------------------------------------------------------------
# Tests: run_amrfinder
# ---------------------------------------------------------------------------

class TestRunAmrfinder:
    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_basic_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="col1\tcol2\n", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={})
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["amrfinder", "--nucleotide", "/tmp/in.fasta"]
        assert "--output" in cmd
        assert "/tmp/out.tsv" in cmd
        assert "--nucleotide_output" not in cmd
        assert "--protein_output" not in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_has_nucleotide_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={"has_nucleotide": True})
        cmd = mock_run.call_args[0][0]
        assert "--nucleotide_output" in cmd
        assert "/tmp/nuc.fna" in cmd
        assert "--protein_output" not in cmd
        assert "--nucleotide" in cmd
        assert "--protein" not in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_has_protein_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input=None, prot_input="/tmp/in.fasta", gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={"has_protein": True})
        cmd = mock_run.call_args[0][0]
        assert "--protein_output" in cmd
        assert "/tmp/prot.faa" in cmd
        assert "--nucleotide_output" not in cmd
        assert "--protein" in cmd
        assert "--nucleotide" not in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_plus_flag_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={"plus_flag": True})
        cmd = mock_run.call_args[0][0]
        assert "--plus" in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_organism_flag_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={"organism": "Salmonella"})
        cmd = mock_run.call_args[0][0]
        assert "-O" in cmd
        assert "Salmonella" in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_ident_min_and_coverage_min(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={
            "ident_min": 0.9,
            "coverage_min": 0.75,
        })
        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd
        assert "0.9" in cmd
        assert "-c" in cmd
        assert "0.75" in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_annotation_format_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(
            nuc_input="/tmp/in.fasta",
            prot_input=None,
            gff_input=None,
            output_tsv="/tmp/out.tsv",
            stderr_path="/tmp/stderr.txt",
            nucleotide_path="/tmp/nuc.fna",
            protein_path="/tmp/prot.faa",
            params={"annotation_format": "prokka"},
        )
        cmd = mock_run.call_args[0][0]
        assert "--annotation_format" in cmd
        assert "prokka" in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_nonzero_returncode_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Database error")
        with pytest.raises(Exception, match="AMRFinderPlus failed"):
            worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={})

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_multiple_files_added(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(nuc_input="/tmp/nuc.fasta", prot_input="/tmp/prot.fasta", gff_input="/tmp/prot.gff", output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={})
        cmd = mock_run.call_args[0][0]
        assert "--nucleotide" in cmd
        assert "/tmp/nuc.fasta" in cmd
        assert "--protein" in cmd
        assert "/tmp/prot.fasta" in cmd
        assert "--gff" in cmd
        assert "/tmp/prot.gff" in cmd


# ---------------------------------------------------------------------------
# Tests: handle_pubsub_push (the Cloud Run HTTP endpoint)
# ---------------------------------------------------------------------------

class TestHandlePubsubPush:
    def setup_method(self):
        worker.get_firestore_client().collection.return_value.document.return_value = MagicMock()

    def test_missing_envelope_returns_400(self):
        resp = flask_client.post("/", json={})
        assert resp.status_code == 400

    def test_missing_message_key_returns_400(self):
        resp = flask_client.post("/", json={"subscription": "projects/x/subscriptions/y"})
        assert resp.status_code == 400

    def test_invalid_base64_payload_returns_200(self):
        """An invalid base64 payload should be ACK'd (200) and return an error message."""
        envelope = {
            "message": {
                "data": "not-valid-base64!",
                "messageId": "test-msg-id"
            },
            "subscription": "projects/test/subscriptions/sub"
        }
        resp = flask_client.post("/", json=envelope)
        assert resp.status_code == 200
        assert b"Could not decode message" in resp.data

    def test_non_dict_payload_returns_200(self):
        """A JSON array payload is not a valid job message; should be ACK'd (200)."""
        body = _make_raw_push_body(json.dumps(["job1", "job2"]).encode("utf-8"))
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert b"JSON object" in resp.data

    def test_empty_job_id_returns_200(self):
        """An empty job_id should be ACK'd (200) to prevent infinite retries."""
        body = _make_push_body(job_id="")
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert b"job_id" in resp.data

    def test_non_string_job_id_returns_200(self):
        """A numeric job_id is invalid; should be ACK'd (200)."""
        body = _make_raw_push_body(
            json.dumps({"job_id": 12345, "gcs_uri": "gs://bucket/file.fasta", "parameters": {}}).encode("utf-8")
        )
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert b"job_id" in resp.data

    def test_gcs_uri_missing_scheme_returns_200(self):
        """A gcs_uri without gs:// prefix is invalid; should be ACK'd (200)."""
        body = _make_push_body(gcs_uri="bucket/path/file.fasta")
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert b"gcs_uri" in resp.data

    def test_gcs_uri_http_scheme_returns_200(self):
        """A gcs_uri with http:// instead of gs:// is invalid; should be ACK'd (200)."""
        body = _make_push_body(gcs_uri="http://bucket/path/file.fasta")
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert b"gcs_uri" in resp.data

    @patch("worker.upload_blob", return_value="gs://output/results/job-params/results.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_non_dict_parameters_defaults_to_empty(self, mock_dl, mock_run, mock_ul):
        """Non-dict parameters should be silently replaced with {} and job should succeed."""
        mock_run.return_value = ""
        body = _make_raw_push_body(
            json.dumps({"job_id": "job-params", "gcs_uri": "gs://bucket/file.fasta", "parameters": "bad"}).encode("utf-8")
        )
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        # run_amrfinder is keyword-only; verify params was coerced to an empty dict
        assert mock_run.call_args.kwargs["params"] == {}

    @patch("worker.upload_blob", return_value="gs://output/results/job-abc/results.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_successful_job_returns_200(self, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        resp = flask_client.post("/", json=_make_push_body())
        assert resp.status_code == 200

    @patch("worker.get_installed_versions", return_value={"software_version": "4.0.1", "database_version": "2024-01-01.1", "amrrules_version": "0.1.0"})
    @patch("worker.upload_blob", return_value="gs://output/results/job-abc/results.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_successful_job_updates_status_to_completed(self, mock_dl, mock_run, mock_ul, mock_ver):
        mock_run.return_value = ""
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        flask_client.post("/", json=_make_push_body(job_id="job-xyz"))

        update_calls = [c[0][0] for c in mock_doc.update.call_args_list if c[0]]
        completed_call = next(c for c in update_calls if c.get("status") == "Completed")
        assert completed_call["software_version"] == "4.0.1"
        assert completed_call["database_version"] == "2024-01-01.1"
        assert completed_call["amrrules_version"] == "0.1.0"
        assert completed_call["worker_version"] == worker.APP_VERSION

    @patch("worker.run_amrfinder", side_effect=Exception("amrfinder crashed"))
    @patch("worker.download_blob")
    def test_failed_job_updates_status_to_failed(self, mock_dl, mock_run):
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

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

    @patch("worker.upload_blob", return_value="gs://output/results/job-abc/results.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    @patch("worker.os.path.exists", return_value=True)
    @patch("worker.os.remove")
    def test_tmp_files_cleaned_up(self, mock_remove, mock_exists, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        flask_client.post("/", json=_make_push_body(job_id="job-cleanup"))

        removed_paths = [c[0][0] for c in mock_remove.call_args_list]
        assert any("job-cleanup" in p for p in removed_paths)

    @patch("builtins.open", mock_open())
    @patch("worker.upload_blob", return_value="gs://output/results/job-abc/results.tsv")
    @patch("worker.run_amrrules", side_effect=Exception("amrrules error"))
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_amrrules_soft_failure_does_not_fail_job(self, mock_dl, mock_run, mock_rules, mock_ul):
        """AMRrules exception records amrrules_error in Firestore but job completes successfully."""
        mock_run.return_value = ""
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        params = {"amrrules_organism": "s__Escherichia coli"}
        payload = {"job_id": "job-soft-fail", "gcs_uri": "gs://b/f.fa", "parameters": params}
        body = _make_raw_push_body(json.dumps(payload).encode("utf-8"))

        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200

        update_calls = [c[0][0] for c in mock_doc.update.call_args_list]
        completed_update = [u for u in update_calls if u.get("status") == "Completed"]
        assert len(completed_update) == 1
        assert completed_update[0].get("amrrules_error") == "amrrules error"

    @patch("builtins.open", mock_open())
    @patch("worker.upload_blob", return_value="gs://output/results/job-abc/results.tsv")
    @patch("worker.run_amrrules", return_value="amrrules success")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    @patch("worker.os.path.exists")
    def test_amrrules_genome_summary_uploaded(self, mock_exists, mock_dl, mock_run_af, mock_rules, mock_ul):
        """Worker uploads amrrules_genome_summary.tsv to GCS when produced by amrrules."""
        mock_exists.side_effect = lambda path: "_amrrules_genome_summary.tsv" in path or "results.tsv" in path
        mock_run_af.return_value = ""
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        params = {"amrrules_organism": "s__Escherichia coli"}
        payload = {"job_id": "job-amr-success", "gcs_uri": "gs://b/f.fa", "parameters": params}
        body = _make_raw_push_body(json.dumps(payload).encode("utf-8"))

        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200

        upload_destinations = [c[0][1] for c in mock_ul.call_args_list]
        assert "results/job-amr-success/amrrules_genome_summary.tsv" in upload_destinations



class TestRunAmrrules:
    """Verify run_amrrules helper function command execution."""

    @patch("worker.subprocess.run")
    def test_basic_amrrules_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
        worker.run_amrrules(
            amrfp_output_tsv="/tmp/out.tsv",
            amrrules_organism="s__Escherichia coli",
            output_prefix="/tmp/prefix",
            job_id="job-123",
        )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "amrrules"
        assert "--input" in cmd
        assert "/tmp/out.tsv" in cmd
        assert "--organism" in cmd
        assert "s__Escherichia coli" in cmd
        assert "--sample-id" in cmd
        assert "job-123" in cmd

    @patch("worker.subprocess.run")
    def test_amrrules_appends_to_stderr_path(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="amrrules stdout log", stderr="amrrules stderr log")
        stderr_file = tmp_path / "test_stderr.txt"
        stderr_file.write_text("=== AMRFinderPlus Log ===\nAMRFinder output\n")

        worker.run_amrrules(
            amrfp_output_tsv="/tmp/out.tsv",
            amrrules_organism="s__Escherichia coli",
            output_prefix="/tmp/prefix",
            job_id="job-123",
            stderr_path=str(stderr_file),
        )

        content = stderr_file.read_text()
        assert "=== AMRFinderPlus Log ===" in content
        assert "=== AMRrules Log ===" in content
        assert "amrrules stderr log" in content
        assert "amrrules stdout log" in content


class TestUploadVersions:
    @patch("importlib.metadata.version", return_value="1.2.3")
    @patch("worker.subprocess.run")
    @patch("worker.os.path.exists", return_value=True)
    def test_upload_versions_uploads_amrrules_version(self, mock_exists, mock_run, mock_pkg_ver):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        worker.get_storage_client().bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob
        mock_run.return_value = MagicMock(stdout="4.2.7")

        worker.upload_versions()

        blob_calls = [call[0][0] for call in mock_bucket.blob.call_args_list]
        assert "config/amrrules_version.txt" in blob_calls
        mock_blob.upload_from_string.assert_any_call("1.2.3")

    @patch("importlib.metadata.version", side_effect=Exception("Package not found"))
    @patch("worker.subprocess.run")
    @patch("worker.os.path.exists", return_value=False)
    def test_upload_versions_handles_exception_gracefully(self, mock_exists, mock_run, mock_pkg_ver):
        mock_bucket = MagicMock()
        worker.get_storage_client().bucket.return_value = mock_bucket
        mock_run.side_effect = Exception("Subprocess error")

        # Should not raise exception
        worker.upload_versions()


class TestGetInstalledVersions:
    @patch("importlib.metadata.version", return_value="0.3.1")
    @patch("worker.subprocess.run")
    def test_get_installed_versions_success_from_amrfinder_V(self, mock_run, mock_pkg_ver):
        mock_output = (
            "Software directory: '/usr/local/bin/'\n"
            "Software version: 4.2.7\n"
            "Database directory: '/usr/local/share/data/2026-03-24.1'\n"
            "Database version: 2026-03-24.1\n"
        )
        mock_run.return_value = MagicMock(stdout=mock_output)

        versions = worker.get_installed_versions()
        assert versions["database_version"] == "2026-03-24.1"
        assert versions["software_version"] == "4.2.7"
        assert versions["amrrules_version"] == "0.3.1"

    @patch("importlib.metadata.version", side_effect=Exception("Not found"))
    @patch("worker.subprocess.run", side_effect=Exception("CLI error"))
    def test_get_installed_versions_handles_cli_failure(self, mock_run, mock_pkg_ver):
        versions = worker.get_installed_versions()
        assert versions["database_version"] is None
        assert versions["software_version"] is None
        assert versions["amrrules_version"] is None

    @patch("importlib.metadata.version", return_value="0.3.1")
    @patch("worker.subprocess.run")
    def test_get_installed_versions_strips_directories(self, mock_run, mock_pkg_ver):
        mock_output = (
            "Software directory: '/usr/local/bin/'\n"
            "Software version: 3.12.8\n"
            "Database directory: '/usr/local/share/data/2024-05-02.1/'\n"
            "Database version: 2024-05-02.1\n"
        )
        mock_run.return_value = MagicMock(stdout=mock_output)

        versions = worker.get_installed_versions()
        assert versions["database_version"] == "2024-05-02.1"
        assert versions["software_version"] == "3.12.8"
        assert versions["amrrules_version"] == "0.3.1"


