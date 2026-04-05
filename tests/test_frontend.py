"""
Unit tests for the Flask frontend (frontend/main.py).
All GCP clients (storage, pubsub, firestore) are patched before import so
no real credentials are needed. Tests use Flask's built-in test client.
"""
import io
import json
import os
import sys
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

# main.py reads taxgroup.tsv at startup via organism_select() on the first
# request; point it at the real file so the import doesn't fail.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "frontend"))
os.chdir(os.path.join(os.path.dirname(__file__), "..", "frontend"))

import main  # noqa: E402 (imported after patches)

main._storage_client = MOCK_STORAGE
main._firestore_client = MOCK_FIRESTORE
main._publisher = MOCK_PUBLISHER

client = main.app.test_client()
main.app.config["TESTING"] = True
main.limiter.enabled = False  # reliably disable limiter globally for unit tests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blob(exists=True, content=b"", size=1024):
    """Return a mock GCS blob."""
    b = MagicMock()
    b.exists.return_value = exists
    b.download_as_string.return_value = content
    b.download_as_bytes.return_value = content
    b.size = size
    return b


def _fasta_file(name="sample.fasta", content=b">seq1\nATCG\n"):
    """Return a FileStorage-compatible tuple for multipart upload."""
    return (io.BytesIO(content), name)


# ---------------------------------------------------------------------------
# Tests: GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def setup_method(self):
        # Reset cached versions so each test starts clean
        main.cached_db_version = None
        main.cached_software_version = None

        # Default: both version blobs exist
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")
        sw_blob = _make_blob(exists=True, content=b"4.2.7")
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            db_blob if "database_version" in name else sw_blob
        )

    def test_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_renders_html(self):
        resp = client.get("/")
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()

    def test_shows_database_version(self):
        resp = client.get("/")
        assert b"2024-01-01.2" in resp.data

    def test_shows_software_version(self):
        resp = client.get("/")
        assert b"4.2.7" in resp.data

    def test_pending_when_db_blob_missing(self):
        main.cached_db_version = None
        main.cached_software_version = None
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Queued" in resp.data

    def test_pending_when_software_blob_missing(self):
        main.cached_db_version = None
        main.cached_software_version = None
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")
        sw_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            db_blob if "database_version" in name else sw_blob
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Queued" in resp.data

    def test_unknown_when_db_fetch_raises_exception(self):
        main.cached_db_version = None
        main.cached_software_version = None
        MOCK_STORAGE.bucket.return_value.blob.side_effect = Exception("Storage error")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Unknown" in resp.data

    def test_unknown_when_software_fetch_raises_exception(self):
        main.cached_db_version = None
        main.cached_software_version = None
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")

        def side_effect(name):
            if "database_version" in name:
                return db_blob
            raise Exception("Storage error")

        MOCK_STORAGE.bucket.return_value.blob.side_effect = side_effect
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Unknown" in resp.data


# ---------------------------------------------------------------------------
# Tests: POST /analyze
# ---------------------------------------------------------------------------

class TestAnalyze:
    def setup_method(self):
        # GCS upload succeeds silently
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob()

        # Pub/Sub publish succeeds
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-1"
        MOCK_PUBLISHER.publish.return_value = mock_future
        MOCK_PUBLISHER.topic_path.return_value = "projects/amrfinder/topics/amr-jobs-topic"

        # Firestore set succeeds
        MOCK_FIRESTORE.collection.return_value.document.return_value = MagicMock()

    def _post_analyze(self, nuc_file=True, prot_file=False, organism="", annotation_format="standard", job_name="", extra_data=None):
        data = {"organism": organism, "annotation_format": annotation_format, "job_name": job_name}
        if extra_data:
            data.update(extra_data)
        if nuc_file:
            data["nuc_file"] = _fasta_file("sample.fasta")
        if prot_file:
            data["prot_file"] = _fasta_file("sample_prot.fasta")
        return client.post("/analyze", data=data, content_type="multipart/form-data")

    def test_returns_200_with_user_id(self):
        resp = self._post_analyze()
        assert resp.status_code == 200
        body = resp.get_json()
        assert "user_id" in body

    def test_user_id_is_uuid_format(self):
        import uuid
        resp = self._post_analyze()
        user_id = resp.get_json()["user_id"]
        uuid.UUID(user_id)  # raises if not a valid UUID

    def test_pubsub_publish_called(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze()
        MOCK_PUBLISHER.publish.assert_called_once()

    def test_pubsub_message_contains_job_id(self):
        MOCK_PUBLISHER.publish.reset_mock()
        resp = self._post_analyze()
        user_id = resp.get_json()["user_id"]
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        message = json.loads(message_bytes.decode("utf-8"))
        assert message["job_id"] == user_id

    def test_pubsub_message_contains_gcs_uri(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze()
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        message = json.loads(message_bytes.decode("utf-8"))
        assert message["gcs_uri"].startswith("gs://")

    def test_organism_included_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze(organism="Salmonella")
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message["parameters"].get("organism") == "Salmonella"

    def test_no_organism_not_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze(organism="")
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert "organism" not in message["parameters"]

    def test_default_annotation_format_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze()
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message["parameters"].get("annotation_format") == "standard"

    def test_custom_annotation_format_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze(annotation_format="prokka")
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message["parameters"].get("annotation_format") == "prokka"

    def test_job_name_included_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze(job_name="Sample Job_1")
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message.get("job_name") == "Sample Job_1"

    def test_firestore_doc_set_to_pending(self):
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        self._post_analyze()
        mock_doc.set.assert_called_once()
        set_data = mock_doc.set.call_args[0][0]
        assert set_data["status"] == "Queued"
        assert "created_at" in set_data
        assert "expire_at" in set_data
        # Ensure expire_at is roughly 90 days after created_at
        delta = set_data["expire_at"] - set_data["created_at"]
        assert 89 < delta.days <= 90

    def test_firestore_doc_includes_job_name(self):
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        self._post_analyze(job_name="My Run-01")
        set_data = mock_doc.set.call_args[0][0]
        assert set_data["job_name"] == "My Run-01"

    def test_invalid_job_name_characters_return_400(self):
        resp = self._post_analyze(job_name="bad@name")
        assert resp.status_code == 400
        assert "Job name can only contain" in resp.get_json()["error"]

    def test_job_name_too_long_returns_400(self):
        resp = self._post_analyze(job_name=("a" * 101))
        assert resp.status_code == 400
        assert "100 characters" in resp.get_json()["error"]

    def test_no_file_returns_400(self):
        resp = client.post("/analyze", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_pubsub_failure_returns_500(self):
        MOCK_PUBLISHER.publish.side_effect = Exception("Pub/Sub down")
        try:
            resp = self._post_analyze()
            assert resp.status_code == 500
        finally:
            MOCK_PUBLISHER.publish.side_effect = None


# ---------------------------------------------------------------------------
# Tests: Rate limiting on /analyze
# ---------------------------------------------------------------------------

class TestRateLimit:
    """Verify the 5/minute rate limit on /analyze returns 429 when exceeded."""

    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-1"
        MOCK_PUBLISHER.publish.return_value = mock_future
        MOCK_PUBLISHER.topic_path.return_value = "projects/amrfinder/topics/amr-jobs-topic"
        MOCK_FIRESTORE.collection.return_value.document.return_value = MagicMock()

    def test_sixth_request_returns_429(self):
        main.limiter.enabled = True
        main.limiter.reset()
        try:
            for _ in range(5):
                resp = client.post(
                    "/analyze",
                    data={"organism": "", "nuc_file": _fasta_file()},
                    content_type="multipart/form-data",
                )
                assert resp.status_code == 200, f"Expected 200 on attempt, got {resp.status_code}"
            # 6th request in same minute should be rate-limited
            resp = client.post(
                "/analyze",
                data={"organism": "", "nuc_file": _fasta_file()},
                content_type="multipart/form-data",
            )
            assert resp.status_code == 429
            body = resp.get_json()
            assert "rate limit" in body["error"].lower()
        finally:
            main.limiter.enabled = False
            main.limiter.reset()


# ---------------------------------------------------------------------------
# Tests: GET /get-results/<user_id>
# ---------------------------------------------------------------------------

class TestGetResults:
    TSV_CONTENT = (
        b"Protein identifier\tContig id\tElement symbol\tMethod\t"
        b"% Identity to reference\tType\n"
        b"seq1\tcontig01\tblaTEM-156\tBLASTP\t99.5\tAMR\n"
    )

    def setup_method(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            MagicMock(exists=False)
        )

    def test_returns_200_when_results_ready(self):
        blob = _make_blob(exists=True, content=self.TSV_CONTENT)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/test-job-id/results.tsv" in name else stderr_blob
        )
        resp = client.get("/get-results/test-job-id")
        assert resp.status_code == 200

    def test_result_contains_html_table(self):
        blob = _make_blob(exists=True, content=self.TSV_CONTENT)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-job-id").get_json()
        assert "<table>" in body["result"]

    def test_returns_204_when_results_pending(self):
        blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = blob
        resp = client.get("/get-results/test-job-id")
        assert resp.status_code == 204

    def test_returns_500_when_job_failed(self):
        blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = blob

        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "amrfinder crashed",
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            mock_doc
        )
        resp = client.get("/get-results/failed-job")
        assert resp.status_code == 500
        assert "failed" in resp.get_json()["error"].lower()

    def test_hierarchy_node_is_linked(self):
        content = (
            b"Protein identifier\tHierarchy node\n"
            b"seq1\tWP_000000001.1\n"
        )
        blob = _make_blob(exists=True, content=content)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-node-job").get_json()
        expected_link = 'https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/#node_id:WP_000000001.1'
        assert expected_link in body["result"]
        assert 'target="_blank"' in body["result"]

    def test_hierarchy_node_na_is_not_linked(self):
        content = (
            b"Protein identifier\tHierarchy node\n"
            b"seq1\tN/A\n"
        )
        blob = _make_blob(exists=True, content=content)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-na-job").get_json()
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/' not in body["result"]
        assert '<td>N/A</td>' in body["result"]


# ---------------------------------------------------------------------------
# Tests: GET /output/<user_id>
# ---------------------------------------------------------------------------

class TestOutput:
    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None

    def test_returns_tsv_attachment_when_file_exists(self):
        tsv = b"col1\tcol2\nval1\tval2\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=tsv
        )
        resp = client.get("/output/test-job-id")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert b"col1" in resp.data

    def test_returns_404_when_file_missing(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/output/nonexistent-job")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /stderr/<user_id>
# ---------------------------------------------------------------------------

class TestStderrOutput:
    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None

    def test_returns_stderr_in_browser_when_file_exists(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b"some stderr log\n"
        )
        resp = client.get("/stderr/test-job-id")
        assert resp.status_code == 200
        # No longer an attachment; opens in browser
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.mimetype == "text/plain"

    def test_returns_404_when_stderr_missing(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/stderr/nonexistent-job")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /nucleotide/<user_id>
# ---------------------------------------------------------------------------

class TestNucleotideOutput:
    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None

    def test_returns_nuc_attachment_when_file_exists(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b">nuc\nATCG\n"
        )
        resp = client.get("/nucleotide/test-job-id")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_returns_404_when_nuc_missing(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/nucleotide/nonexistent-job")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /protein/<user_id>
# ---------------------------------------------------------------------------

class TestProteinOutput:
    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None

    def test_returns_prot_attachment_when_file_exists(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b">prot\nMAGY\n"
        )
        resp = client.get("/protein/test-job-id")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_returns_404_when_prot_missing(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/protein/nonexistent-job")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /results/<job_id>  (shareable results page)
# ---------------------------------------------------------------------------

class TestResultsPage:
    """Tests for the shareable /results/<job_id> route."""

    def _pending_firestore(self):
        """Return a mock Firestore doc representing a queued job."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"job_id": "test-job-id", "status": "Queued", "job_name": "Demo Job"}
        return doc

    def _failed_firestore(self):
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "fail-job-id",
            "status": "Failed",
            "error_message": "amrfinder crashed",
        }
        return doc

    def _missing_firestore(self):
        doc = MagicMock()
        doc.exists = False
        return doc

    def _completed_firestore(self):
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "completed-job-id",
            "status": "Completed",
            "result_uri": "gs://bucket/results.tsv"
        }
        return doc
    def test_results_page_200_pending_job(self):
        """GET /results/<id> returns 200 when the job exists in Firestore."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert resp.status_code == 200

    def test_results_page_contains_shareable_link(self):
        """Response HTML includes a link that contains the job ID."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b"test-job-id" in resp.data
        # The page should advertise a shareable URL pointing back to itself
        assert b"/results/test-job-id" in resp.data

    def test_results_page_heading_includes_job_name_when_present(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b"Job Results: Demo Job" in resp.data

    def test_results_page_job_id_is_link_to_results_page(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b'<a id="share-link" href="/results/test-job-id"><code id="job-id">test-job-id</code></a>' in resp.data

    def test_results_page_copy_button_title_is_copy_sharable_link(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b'title="Copy sharable link"' in resp.data
        assert b">Copy sharable link</button>" in resp.data

    def test_results_page_shows_job_name_when_present(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b"Job name:" in resp.data
        assert b"Demo Job" in resp.data

    def test_results_page_omits_job_name_when_absent(self):
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"job_id": "test-job-id", "status": "Queued"}
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/test-job-id")
        assert b"Job name:" not in resp.data

    def test_results_page_shows_pending_state(self):
        """While job is Queued, the page indicates it is not yet complete."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        data_lower = resp.data.lower()
        assert b"queued" in data_lower or b"running" in data_lower or b"polling" in data_lower

    def test_results_page_shows_error_for_failed_job(self):
        """When the job status is Failed, the page contains an error message."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._failed_firestore()
        )
        resp = client.get("/results/fail-job-id")
        assert resp.status_code == 200
        assert b"amrfinder crashed" in resp.data or b"error" in resp.data.lower()

    def test_results_page_404_unknown_job(self):
        """GET /results/<id> returns 404 when the job ID is not in Firestore."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._missing_firestore()
        )
        resp = client.get("/results/no-such-job")
        assert resp.status_code == 404

    def test_results_page_returns_results_immediately_if_completed(self):
        """If the job is Completed, the page should fetch and tabulize the TSV immediately instead of showing polling."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = self._completed_firestore()
        
        # Mock the GCS blob download (which it should call)
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = b"Protein identifier\tContig id\nseq1\tcontig01\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/completed-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        
        # Should NOT have the polling text
        assert "polling" not in html.lower()
        
        # Should HAVE the HTML table generated by `tabulize`
        assert "<table>" in html
        assert "<td>seq1</td>" in html
        assert "Download Results" in html


# ---------------------------------------------------------------------------
# Tests: /analyze response includes results_url
# ---------------------------------------------------------------------------

class TestAnalyzeResultsUrl:
    """Verify /analyze returns a shareable results_url in its JSON response."""

    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-1"
        MOCK_PUBLISHER.publish.return_value = mock_future
        MOCK_PUBLISHER.topic_path.return_value = "projects/amrfinder/topics/amr-jobs-topic"
        MOCK_FIRESTORE.collection.return_value.document.return_value = MagicMock()

    def test_analyze_response_includes_results_url(self):
        """POST /analyze JSON body must include a results_url key."""
        data = {"organism": "", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "results_url" in body

    def test_analyze_results_url_contains_user_id(self):
        """The results_url must embed the user_id returned in the same response."""
        data = {"organism": "", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        body = resp.get_json()
        user_id = body["user_id"]
        assert user_id in body["results_url"]
