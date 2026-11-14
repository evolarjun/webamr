"""
Unit tests for the Flask frontend (frontend/main.py).
All GCP clients (storage, pubsub, firestore) are patched before import so
no real credentials are needed. Tests use Flask's built-in test client.
"""
import io
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from google.cloud.exceptions import NotFound

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
main = None
client = None
_ORIGINAL_CWD = None


def setup_module(module):
    """Set up frontend unit test module with isolated GCP client patches."""
    global main, client, _ORIGINAL_CWD

    for p in patchers:
        p.start()

    # main.py reads taxgroup.tsv from cwd during request handling; set cwd for import.
    _ORIGINAL_CWD = os.getcwd()
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
    if frontend_dir not in sys.path:
        sys.path.insert(0, frontend_dir)
    os.chdir(frontend_dir)

    main = importlib.import_module("main")
    main._storage_client = MOCK_STORAGE
    main._firestore_client = MOCK_FIRESTORE
    main._publisher = MOCK_PUBLISHER

    client = main.app.test_client()
    main.app.config["TESTING"] = True
    main.limiter.enabled = False  # reliably disable limiter globally for unit tests


def teardown_module(module):
    """Stop global patches to avoid leaking mocks into other test modules."""
    # Reset singleton clients and caches that were set to mocks in this module.
    if main is not None:
        main._storage_client = None
        main._firestore_client = None
        main._publisher = None
        main.cached_db_version = None
        main.cached_software_version = None
    # Ensure later imports (for integration tests) get a fresh module instance.
    sys.modules.pop("main", None)
    for p in patchers:
        p.stop()
    os.chdir(_ORIGINAL_CWD)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_blob(exists=True, content=b"", size=1024, name="mock_blob"):
    """Return a mock GCS blob."""
    b = MagicMock()
    b.name = name
    b.exists.return_value = exists
    if exists:
        b.download_as_string.return_value = content
        b.download_as_bytes.return_value = content
    else:
        b.download_as_string.side_effect = NotFound("Blob not found")
        b.download_as_bytes.side_effect = NotFound("Blob not found")
    b.size = size
    return b


def _fasta_file(name="sample.fasta", content=b">seq1\nATCG\n"):
    """Return a FileStorage-compatible tuple for multipart upload."""
    return (io.BytesIO(content), name)


# ---------------------------------------------------------------------------
# Tests: Error Handlers
# ---------------------------------------------------------------------------

class TestPageNotFound:
    def test_page_not_found_renders_404(self):
        resp = client.get("/this-page-does-not-exist")
        assert resp.status_code == 404
        assert b"Woah, 404 - Not Found" in resp.data

    def test_404_contains_github_issues_link(self):
        resp = client.get("/this-page-does-not-exist")
        assert b"Report issues on" in resp.data
        assert b"https://github.com/evolarjun/webamr/issues" in resp.data

# ---------------------------------------------------------------------------
# Tests: GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def setup_method(self):
        # Reset cached versions so each test starts clean
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None

        # Default: all version blobs exist
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")
        sw_blob = _make_blob(exists=True, content=b"4.2.7")
        amr_blob = _make_blob(exists=True, content=b"1.2.3")
        
        def get_blob(name):
            if "database_version" in name:
                return db_blob
            elif "software_version" in name:
                return sw_blob
            return amr_blob

        MOCK_STORAGE.bucket.return_value.blob.side_effect = get_blob

    def test_returns_200(self):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_renders_html(self):
        resp = client.get("/")
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()

    def test_shows_database_version(self):
        resp = client.get("/")
        assert b"2024-01-01.2" in resp.data

    def test_shows_database_version_link(self):
        resp = client.get("/")
        expected = (
            b'<a id="database-version-link" '
            b'href="https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/changelogs/2024-01-01.2.txt" '
            b'target="_blank" rel="noopener"><code>2024-01-01.2</code></a>'
        )
        assert expected in resp.data

    def test_shows_software_version(self):
        resp = client.get("/")
        assert b"4.2.7" in resp.data

    def test_shows_amrrules_version(self):
        resp = client.get("/")
        assert b"1.2.3" in resp.data

    def test_pending_when_db_blob_missing(self):
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=False)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Run job to refresh" in resp.data
        assert b"database-version-link" not in resp.data

    def test_pending_when_software_blob_missing(self):
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")
        sw_blob = _make_blob(exists=False)
        amr_blob = _make_blob(exists=True, content=b"1.2.3")
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            db_blob if "database_version" in name else (sw_blob if "software_version" in name else amr_blob)
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Run job to refresh" in resp.data

    def test_pending_when_amrrules_blob_missing(self):
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")
        sw_blob = _make_blob(exists=True, content=b"4.2.7")
        amr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            db_blob if "database_version" in name else (sw_blob if "software_version" in name else amr_blob)
        )
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Run job to refresh" in resp.data

    def test_unknown_when_db_fetch_raises_exception(self):
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None
        MOCK_STORAGE.bucket.return_value.blob.side_effect = Exception("Storage error")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Error retrieving version" in resp.data

    def test_unknown_when_software_fetch_raises_exception(self):
        main.cached_db_version = None
        main.cached_software_version = None
        main.cached_amrrules_version = None
        db_blob = _make_blob(exists=True, content=b"2024-01-01.2")

        def side_effect(name):
            if "database_version" in name:
                return db_blob
            raise Exception("Storage error")

        MOCK_STORAGE.bucket.return_value.blob.side_effect = side_effect
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Error retrieving version" in resp.data

    def test_index_contains_job_name_helper_text(self):
        """Index page should render the job name hint span."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"job-name-helper" in resp.data

    def test_index_job_name_helper_describes_requirements(self):
        """Job name hint span should describe the character requirements."""
        resp = client.get("/")
        assert b"underscores" in resp.data
        assert b"hyphens" in resp.data

    def test_index_contains_github_issues_link(self):
        resp = client.get("/")
        assert b"Report issues" in resp.data
        assert b"https://github.com/evolarjun/webamr/issues" in resp.data

    def test_index_contains_meta_and_open_graph_tags(self):
        resp = client.get("/")
        assert b'<meta name="description" content="Web interface for NCBI AMRFinderPlus and AMRrules.' in resp.data
        assert b'<meta property="og:title" content="AMRFinderPlus Web Interface"' in resp.data
        assert b'<meta property="og:description" content="Web interface for NCBI AMRFinderPlus and AMRrules.' in resp.data
        assert b'<meta property="og:url" content="https://webamr.net/"' in resp.data
        assert b'<meta property="og:type" content="website"' in resp.data

    def test_index_contains_file_warning_spans(self):
        """Index page should include warning spans for nucleotide, protein, and GFF inputs."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'id="nuc-file-warning"' in resp.data
        assert b'id="prot-file-warning"' in resp.data
        assert b'id="gff-file-warning"' in resp.data

    def test_style_css_warning_empty_and_compact_grid(self):
        """style.css should hide empty warning helpers and use compact grid row-gap."""
        resp = client.get("/static/css/style.css")
        assert resp.status_code == 200
        assert b".warning-text:empty" in resp.data
        assert b"row-gap: 13px;" in resp.data

    def test_index_contains_client_side_format_checking_script(self):
        """Index page should include the client-side format sniffing and decompression script."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"checkFileFormat" in resp.data
        assert b"validateFileInput" in resp.data
        assert b"DecompressionStream" in resp.data
        assert b"FASTQ" in resp.data
        assert b"GenBank" in resp.data
        assert b"##gff" in resp.data


# ---------------------------------------------------------------------------
# Tests: GET /docs
# ---------------------------------------------------------------------------

class TestDocsPage:
    def test_docs_page_returns_200(self):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_docs_job_name_section_exists(self):
        resp = client.get("/docs")
        assert b'id="job-name"' in resp.data

    def test_docs_job_name_describes_character_requirements(self):
        """Docs page should explain the allowed characters for job name."""
        resp = client.get("/docs")
        assert b"underscores" in resp.data
        assert b"hyphens" in resp.data
        assert b"100" in resp.data

    def test_docs_contains_github_issues_link(self):
        resp = client.get("/docs")
        assert b"https://github.com/evolarjun/webamr/issues" in resp.data

    def test_docs_header_renders_site_header_and_home_link(self):
        resp = client.get("/docs")
        assert b'<header class="site-header">' in resp.data
        assert b'Back to Home' in resp.data
        assert b'Documentation' in resp.data

    def test_docs_contains_meta_and_open_graph_tags(self):
        resp = client.get("/docs")
        assert b'<meta name="description" content="Documentation, input file specifications, parameters, and interpretation guide for WebAMR, AMRFinderPlus, and AMRrules.">' in resp.data
        assert b'<meta property="og:title" content="Documentation -- Web interface for AMRFinderPlus">' in resp.data
        assert b'<meta property="og:description" content="Documentation, input file specifications, parameters, and interpretation guide for WebAMR, AMRFinderPlus, and AMRrules.">' in resp.data
        assert b'<meta property="og:url" content="https://webamr.net/docs">' in resp.data
        assert b'<meta property="og:type" content="website">' in resp.data


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

    def test_filenames_included_in_pubsub_message(self):
        MOCK_PUBLISHER.publish.reset_mock()
        self._post_analyze(nuc_file=True, prot_file=True, extra_data={"gff_file": _fasta_file("sample.gff")})
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message.get("nuc_filename") == "sample.fasta"
        assert message.get("prot_filename") == "sample_prot.fasta"
        assert message.get("gff_filename") == "sample.gff"

    def test_filenames_with_special_characters_are_secured(self):
        MOCK_PUBLISHER.publish.reset_mock()
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        self._post_analyze(
            nuc_file=False,
            prot_file=False,
            extra_data={
                "nuc_file": _fasta_file("sequence(1).fasta"),
                "prot_file": _fasta_file("protein (2).fasta"),
                "gff_file": _fasta_file("anno-1!@#.gff")
            }
        )
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message.get("nuc_filename") == "sequence1.fasta"
        assert message.get("prot_filename") == "protein_2.fasta"
        assert message.get("gff_filename") == "anno-1.gff"

        mock_doc.set.assert_called_once()
        set_data = mock_doc.set.call_args[0][0]
        assert set_data["nuc_filename"] == "sequence1.fasta"
        assert set_data["prot_filename"] == "protein_2.fasta"
        assert set_data["gff_filename"] == "anno-1.gff"

    def test_firestore_doc_includes_upload_duration_seconds(self):
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        self._post_analyze(nuc_file=True)
        set_data = mock_doc.set.call_args[0][0]
        assert "upload_duration_seconds" in set_data
        assert isinstance(set_data["upload_duration_seconds"], float)

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

    def test_firestore_doc_includes_client_ip_from_header(self):
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        headers = {"X-Forwarded-For": "203.0.113.195, 74.125.19.1"}
        client.post("/analyze", data={"nuc_file": _fasta_file("sample.fasta")}, content_type="multipart/form-data", headers=headers)
        set_data = mock_doc.set.call_args[0][0]
        assert set_data["ip_address"] == "203.0.113.195"

    def test_firestore_doc_includes_client_ip_fallback(self):
        mock_doc = MagicMock()
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        client.post("/analyze", data={"nuc_file": _fasta_file("sample.fasta")}, content_type="multipart/form-data")
        set_data = mock_doc.set.call_args[0][0]
        assert set_data["ip_address"] == "127.0.0.1"

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

    def test_missing_gff_when_both_nuc_and_prot_returns_400(self):
        resp = self._post_analyze(nuc_file=True, prot_file=True)
        assert resp.status_code == 400
        assert "A GFF file is required" in resp.get_json()["error"]

    def test_pubsub_failure_returns_500(self):
        MOCK_PUBLISHER.publish.side_effect = Exception("Pub/Sub down")
        try:
            resp = self._post_analyze()
            assert resp.status_code == 500
        finally:
            MOCK_PUBLISHER.publish.side_effect = None

    def test_prot_and_gff_success(self):
        MOCK_PUBLISHER.publish.reset_mock()
        resp = self._post_analyze(nuc_file=False, prot_file=True, extra_data={"gff_file": _fasta_file("sample.gff")})
        assert resp.status_code == 200
        MOCK_PUBLISHER.publish.assert_called_once()
        call_args = MOCK_PUBLISHER.publish.call_args
        message = json.loads(call_args[0][1].decode("utf-8"))
        assert message.get("prot_filename") == "sample_prot.fasta"
        assert message.get("gff_filename") == "sample.gff"
        assert message.get("nuc_filename") is None
        assert message["parameters"]["has_protein"] is True
        assert message["parameters"]["has_nucleotide"] is False

    def test_nuc_and_gff_fails(self):
        MOCK_PUBLISHER.publish.reset_mock()
        resp = self._post_analyze(nuc_file=True, prot_file=False, extra_data={"gff_file": _fasta_file("sample.gff")})
        assert resp.status_code == 400
        assert "A protein file is required" in resp.get_json()["error"]


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

    def test_returns_pending_status_when_results_missing(self):
        blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = blob

        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {"status": "Queued"}
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/get-results/test-job-id")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Queued"

    def test_returns_processing_status_even_if_results_tsv_exists(self):
        blob = _make_blob(exists=True, content=self.TSV_CONTENT)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = blob

        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {"status": "Processing"}
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/get-results/test-job-id")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Processing"
        assert "result" not in resp.get_json()

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

    def test_returns_500_when_job_failed_includes_error_summary(self):
        blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = blob

        summary = {
            "title": "Invalid FASTA format (detected FASTQ)",
            "explanation": "The file starts with '@' instead of '>'.",
            "suggestion": "Assemble reads into FASTA format before submitting.",
        }
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "AMRFinderPlus failed: Invalid FASTA format (detected FASTQ)\n--- Standard Error ---\nFASTA should start with '>'",
            "error_summary": summary,
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc
        resp = client.get("/get-results/failed-job-summary")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error_summary"] == summary
        assert "Invalid FASTA format (detected FASTQ)" in data["error"]

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
            b"seq2\tNA\n"
        )
        blob = _make_blob(exists=True, content=content)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-na-job").get_json()
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/' not in body["result"]
        assert '<td>N/A</td>' in body["result"]
        assert '<td>NA</td>' in body["result"]

    def test_closest_reference_accession_is_linked(self):
        content = (
            b"Protein identifier\tClosest reference accession\n"
            b"seq1\tNG_050218.1\n"
            b"seq2\tNG_050218.1:1-861\n"
            b"seq3\tN/A\n"
            b"seq4\tNA\n"
        )
        blob = _make_blob(exists=True, content=content)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-closest-ref-job").get_json()
        
        # Test regular accession link
        assert '<a href="https://www.ncbi.nlm.nih.gov/pathogens/refgene/#NG_050218.1" target="_blank">NG_050218.1</a>' in body["result"]
        
        # Test accession with colon link (only accession portion is linked, suffix is plain text outside anchor)
        assert '<a href="https://www.ncbi.nlm.nih.gov/pathogens/refgene/#NG_050218.1" target="_blank">NG_050218.1</a>:1-861' in body["result"]
        
        # Test N/A is not linked
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/refgene/' not in body["result"].split('</tr>')[3]  # checks third data row
        assert '<td>N/A</td>' in body["result"]

        # Test NA is not linked
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/refgene/' not in body["result"].split('</tr>')[4]  # checks fourth data row
        assert '<td>NA</td>' in body["result"]

    def test_hmm_accession_is_linked(self):
        content = (
            b"Protein identifier\tHMM accession\n"
            b"seq1\tNF012345.1\n"
            b"seq2\tN/A\n"
            b"seq3\tNA\n"
        )
        blob = _make_blob(exists=True, content=content)
        stderr_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.side_effect = lambda name: (
            blob if "results/" in name and name.endswith("results.tsv") else stderr_blob
        )
        body = client.get("/get-results/test-hmm-job").get_json()
        
        # Test regular accession link
        assert '<a href="https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/#NF012345.1" target="_blank">NF012345.1</a>' in body["result"]
        
        # Test N/A is not linked
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/' not in body["result"].split('</tr>')[2]
        assert '<td>N/A</td>' in body["result"]

        # Test NA is not linked
        assert 'https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/' not in body["result"].split('</tr>')[3]
        assert '<td>NA</td>' in body["result"]


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
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.mimetype == "text/plain"
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

    def test_returns_nuc_inline_when_file_exists(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b">nuc\nATCG\n"
        )
        resp = client.get("/nucleotide/test-job-id")
        assert resp.status_code == 200
        # No longer an attachment; opens in browser
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.mimetype == "text/plain"

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

    def test_returns_prot_inline_when_file_exists(self):
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b">prot\nMAGY\n"
        )
        resp = client.get("/protein/test-job-id")
        assert resp.status_code == 200
        # No longer an attachment; opens in browser
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
        assert resp.mimetype == "text/plain"

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
        doc.to_dict.return_value = {
            "job_id": "test-job-id",
            "status": "Queued",
            "job_name": "Demo Job",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        }
        return doc

    def _failed_firestore(self):
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "fail-job-id",
            "status": "Failed",
            "error_message": "amrfinder crashed",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
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
            "result_uri": "gs://bucket/results.tsv",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
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
        assert b'title="Copy link"' in resp.data
        assert b">Copy link</button>" in resp.data

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

    def test_results_page_shows_queued_status_badge(self):
        """When job status is Queued, status badge renders Queued."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._pending_firestore()
        )
        resp = client.get("/results/test-job-id")
        assert b'<span id="job-status-badge">Queued</span>' in resp.data

    def test_results_page_shows_running_when_status_is_processing(self):
        """When job status is Processing in Firestore, status badge and message render Running."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "proc-job-id",
            "status": "Processing",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/proc-job-id")
        assert b'<span id="job-status-badge">Running</span>' in resp.data
        assert b"Status: <strong>Running</strong>" in resp.data

    def test_results_page_shows_completed_status_badge(self):
        """When job status is Completed and no amrrules_error, status badge renders Completed."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "completed-job-id",
            "status": "Completed",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
            "amrrules_error": None,
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/results/completed-job-id")
        assert b'<span id="job-status-badge">Completed</span>' in resp.data

    def test_results_page_shows_completed_amrrules_failed_status_badge(self):
        """When job status is Completed but amrrules_error exists, status badge renders Completed (AMRrules failed)."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "amrrules-fail-id",
            "status": "Completed",
            "amrrules_error": "AMRrules exited with non-zero status",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/results/amrrules-fail-id")
        assert b'<span id="job-status-badge">Completed (AMRrules failed)</span>' in resp.data

    def test_results_page_shows_error_for_failed_job(self):
        """When the job status is Failed, the page contains an error message."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = (
            self._failed_firestore()
        )
        resp = client.get("/results/fail-job-id")
        assert resp.status_code == 200
        assert b"amrfinder crashed" in resp.data or b"error" in resp.data.lower()

    def test_results_page_failed_job_renders_pre_and_run_log(self):
        """When job failed and stderr.txt exists, show <pre> and Run log link."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "AMRFinderPlus failed:\n--- Standard Output ---\n--- Standard Error ---\nblast error",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/fail-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<pre class="error-output">' in html
        assert "blast error" in html
        assert '<a href="/stderr/fail-job-id"' in html
        assert "Run log" in html

    def test_results_page_failed_job_renders_error_summary_and_raw_output(self):
        """When job failed with error_summary, show summary card and raw output below by default."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "AMRFinderPlus failed: Invalid FASTA format\n--- Standard Output ---\n--- Standard Error ---\nFASTA should start with '>'",
            "error_summary": {
                "title": "Invalid FASTA format (detected FASTQ)",
                "explanation": "The file starts with '@' instead of '>'.",
                "suggestion": "Assemble reads into FASTA contigs before submitting.",
            },
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/fail-summary-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Job failed: Invalid FASTA format (detected FASTQ)" in html
        assert 'class="error-summary-box"' in html
        assert "What went wrong:" in html
        assert "How to fix it:" in html
        assert '<pre class="error-output">' in html
        assert "<details" not in html.split("error-output")[0]
        assert html.index("error-summary-box") < html.index("error-output")

    def test_results_page_failed_job_without_error_summary_legacy_fallback(self):
        """Legacy failed jobs without error_summary still render raw output without summary box."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "Legacy failure message",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/legacy-fail-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'div class="error-summary-box"' not in html
        assert '<pre class="error-output">Legacy failure message</pre>' in html

    def test_results_page_polling_script_handles_error_summary(self):
        """Queued results page JS polling handles data.error_summary and keeps raw error-output."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Queued",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/queued-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "data.error_summary" in html
        assert "error-summary-box" in html
        assert "What went wrong: " in html
        assert "How to fix it: " in html
        assert "https://github.com/evolarjun/webamr/issues/" in html

    def test_results_page_renders_github_issue_link_in_fallback_suggestion_and_escapes_text(self):
        """Markdown GitHub issue token renders as clickable link while surrounding text stays escaped."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Failed",
            "error_message": "AMRFinderPlus failed",
            "error_summary": {
                "title": "AMRFinderPlus execution failed",
                "explanation": "Unexpected failure <script>alert(1)</script>",
                "suggestion": (
                    "Check <input> files. If you think there is a problem with the software, "
                    "please report in a [GitHub issue](https://github.com/evolarjun/webamr/issues/). "
                    "Please include the Job ID in your report to make it easier for us to reproduce the bug."
                ),
            },
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/fallback-link-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<a href="https://github.com/evolarjun/webamr/issues/">GitHub issue</a>' in html
        assert "Please include the Job ID in your report to make it easier for us to reproduce the bug." in html
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "Check &lt;input&gt; files." in html

    def test_results_page_completed_job_renders_amrrules_error_in_pre(self):
        """When completed job has amrrules_error, show <pre class="error-output">."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "status": "Completed",
            "amrrules_error": "=== AMRFinderPlus Output ===\n=== AMRrules Output ===\nCommand: amrrules ...",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = b"Header1\tHeader2\nval1\tval2\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        MOCK_STORAGE.bucket.return_value.list_blobs.return_value = []

        resp = client.get("/results/completed-amrrules-error")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "AMRrules notice:" in html
        assert '<pre class="error-output">' in html
        assert "=== AMRFinderPlus Output ===" in html

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
        assert "Download AMRFinderPlus" in html

    def test_results_page_retention_date_is_calculated_and_shown(self):
        """The results page shows data will be retained until 7 days after created_at."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = self._completed_firestore()
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = b"Protein identifier\tContig id\nseq1\tcontig01\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/completed-job-id")
        assert resp.status_code == 200
        assert b"server until 2026-06-05" in resp.data

        # Now test when the results file is missing (expired status)
        mock_blob.download_as_bytes.side_effect = NotFound("File not found in GCS")
        resp_expired = client.get("/results/completed-job-id")
        assert resp_expired.status_code == 200
        assert b"Output files were retained on the server until 2026-06-05." in resp_expired.data
        # Restore mock side effect
        mock_blob.download_as_bytes.side_effect = None

    def test_results_page_firestore_exception_returns_404(self):
        """GET /results/<id> returns 404 when Firestore raises an exception."""
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.side_effect = Exception("Firestore error")
        try:
            resp = client.get("/results/error-job")
            assert resp.status_code == 404
            assert b"Woah, 404 - Not Found" in resp.data
        finally:
            # Clean up side effect so other tests aren't broken
            MOCK_FIRESTORE.collection.return_value.document.return_value.get.side_effect = None

    def test_results_page_contains_github_issues_link(self):
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = self._pending_firestore()
        resp = client.get("/results/pending-job-id")
        assert resp.status_code == 200
        assert b"Report issues on" in resp.data
        assert b"https://github.com/evolarjun/webamr/issues" in resp.data

    def test_results_page_shows_gff_annotation_format_when_non_standard(self):
        """Non-standard annotation_format is shown inline with GFF filename."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "gff-job-id",
            "status": "Queued",
            "nuc_filename": "genome.fna",
            "prot_filename": "proteins.faa",
            "gff_filename": "genes.gff",
            "parameters": {"annotation_format": "prokka"},
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/gff-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'GFF: <a href="/input/gff-job-id/genes.gff">genes.gff</a> (prokka)' in html

    def test_results_page_omits_gff_annotation_format_when_standard(self):
        """Standard annotation_format is omitted even when a GFF file is provided."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "gff-std-id",
            "status": "Queued",
            "prot_filename": "proteins.faa",
            "gff_filename": "genes.gff",
            "parameters": {"annotation_format": "standard"},
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/gff-std-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'GFF: <a href="/input/gff-std-id/genes.gff">genes.gff</a>' in html
        assert "(standard)" not in html

    def test_results_page_omits_annotation_format_when_no_gff(self):
        """Annotation format is not shown if no GFF file was uploaded."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "no-gff-id",
            "status": "Queued",
            "nuc_filename": "genome.fna",
            "parameters": {"annotation_format": "prokka"},
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/no-gff-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "prokka" not in html

    def test_results_page_shows_organism_and_amrrules_organism(self):
        """Organism and AMRrules organism are shown pipe-separated on the Inputs line."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "org-job-id",
            "status": "Queued",
            "nuc_filename": "genome.fna",
            "parameters": {
                "annotation_format": "standard",
                "organism": "Escherichia",
                "amrrules_organism": "s__Escherichia coli",
            },
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/org-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "<strong>Inputs:</strong>" in html
        assert "<span>Organism: Escherichia</span>" in html
        assert "<span>AMRrules organism: s__Escherichia coli</span>" in html
        assert " | " in html

    def test_results_page_omits_unset_organisms(self):
        """Unset organism options are omitted from the Inputs line."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "no-org-id",
            "status": "Queued",
            "nuc_filename": "genome.fna",
            "parameters": {"annotation_format": "standard"},
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/no-org-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Organism:" not in html
        assert "AMRrules organism:" not in html

    def test_results_page_shows_options_when_filenames_absent(self):
        """Inputs line still renders organism options without leading pipe if filenames are absent."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "opts-only-id",
            "status": "Queued",
            "parameters": {
                "organism": "Salmonella",
                "amrrules_organism": "s__Salmonella enterica",
            },
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/opts-only-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "<strong>Inputs:</strong>" in html
        assert "<span>Organism: Salmonella</span>" in html
        assert "<span>AMRrules organism: s__Salmonella enterica</span>" in html
        inputs_section = html.split("<strong>Inputs:</strong>", 1)[1].split("</div>", 1)[0]
        assert not inputs_section.lstrip().startswith("|")

    def test_results_page_handles_none_parameters(self):
        """Results page handles parameters=None gracefully."""
        doc = MagicMock(exists=True)
        doc.to_dict.return_value = {
            "job_id": "none-params-id",
            "status": "Queued",
            "nuc_filename": "genome.fna",
            "parameters": None,
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        resp = client.get("/results/none-params-id")
        assert resp.status_code == 200
        assert b"Nucleotide:" in resp.data


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


# ---------------------------------------------------------------------------
# Tests: AMRrules Integration
# ---------------------------------------------------------------------------

class TestAMRrulesIntegration:
    """Verify AMRrules parameter processing, mapping resolution, and download routes."""

    def setup_method(self):
        MOCK_STORAGE.bucket.return_value.blob.side_effect = None
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-1"
        MOCK_PUBLISHER.publish.return_value = mock_future
        MOCK_PUBLISHER.topic_path.return_value = "projects/amrfinder/topics/amr-jobs-topic"
        MOCK_FIRESTORE.collection.return_value.document.return_value = MagicMock()

    def test_index_includes_escherichia_organism_mapping(self):
        """GET / renders index template with organismMapping containing Escherichia -> s__Escherichia coli."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'"Escherichia": "s__Escherichia coli"' in resp.data

    def test_analyze_auto_amrrules_mapped_organism(self):
        """Selecting Escherichia with auto AMRrules includes s__Escherichia coli in Pub/Sub params."""
        data = {"organism": "Escherichia", "amrrules_organism": "auto", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        msg = json.loads(message_bytes.decode("utf-8"))
        assert msg["parameters"].get("amrrules_organism") == "s__Escherichia coli"

    def test_analyze_auto_amrrules_unmapped_organism(self):
        """Selecting Vibrio_cholerae with auto AMRrules omits amrrules_organism from Pub/Sub params."""
        data = {"organism": "Vibrio_cholerae", "amrrules_organism": "auto", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        msg = json.loads(message_bytes.decode("utf-8"))
        assert "amrrules_organism" not in msg["parameters"]

    def test_analyze_amrrules_none(self):
        """Selecting amrrules_organism=none omits amrrules_organism from Pub/Sub params."""
        data = {"organism": "Escherichia", "amrrules_organism": "none", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        msg = json.loads(message_bytes.decode("utf-8"))
        assert "amrrules_organism" not in msg["parameters"]

    def test_analyze_amrrules_manual_override(self):
        """Selecting an explicit AMRrules target species overrides auto-mapping."""
        data = {"organism": "Vibrio_cholerae", "amrrules_organism": "s__Enterobacter hormaechei", "nuc_file": _fasta_file()}
        resp = client.post("/analyze", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        call_args = MOCK_PUBLISHER.publish.call_args
        message_bytes = call_args[0][1]
        msg = json.loads(message_bytes.decode("utf-8"))
        assert msg["parameters"].get("amrrules_organism") == "s__Enterobacter hormaechei"

    def test_amrrules_summary_table_view_route(self):
        """GET /amrrules-summary/<id> returns HTML table view with download button."""
        mock_blob = _make_blob(exists=True, content=b"gene\tdrug\tinterpretation\ngyrA\tCiprofloxacin\tResistant\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/amrrules-summary/job-123")
        assert resp.status_code == 200
        assert b"AMRrules Genome Summary" in resp.data
        assert b"<table" in resp.data
        assert b"Download amrrules_genome_summary.tsv" in resp.data
        assert b"Back to Results" in resp.data
        assert b"gyrA" in resp.data
        assert b"Ciprofloxacin" in resp.data

    def test_amrrules_summary_download_route(self):
        """GET /amrrules-summary/<id>?download=true returns attachment when file exists."""
        mock_blob = _make_blob(exists=True, content=b"gene\tdrug\tinterpretation\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/amrrules-summary/job-123?download=true")
        assert resp.status_code == 200
        assert b"gene\tdrug" in resp.data
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_amrrules_interpreted_table_view_route(self):
        """GET /amrrules-interpreted/<id> returns HTML table view with download button."""
        mock_blob = _make_blob(exists=True, content=b"gene\tdrug\tinterpretation\nblaCTX-M\tCefotaxime\tResistant\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/amrrules-interpreted/job-123")
        assert resp.status_code == 200
        assert b"AMRrules Interpreted Results" in resp.data
        assert b"<table" in resp.data
        assert b"Download amrrules_interpreted.tsv" in resp.data
        assert b"Back to Results" in resp.data
        assert b"blaCTX-M" in resp.data

    def test_amrrules_interpreted_download_route(self):
        """GET /amrrules-interpreted/<id>?download=true returns attachment when file exists."""
        mock_blob = _make_blob(exists=True, content=b"gene\tdrug\tinterpretation\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/amrrules-interpreted/job-123?download=true")
        assert resp.status_code == 200
        assert b"gene\tdrug" in resp.data
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_amrrules_table_view_404_when_missing(self):
        """GET /amrrules-summary/<id> returns 404 when file does not exist."""
        mock_blob = _make_blob(exists=False)
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        resp = client.get("/amrrules-summary/job-missing")
        assert resp.status_code == 404

    def test_amrrules_table_view_contains_github_issues_link(self):
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"job_id": "job-123"}
        MOCK_FIRESTORE.collection.return_value.document.return_value = mock_doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/amrrules-summary/job-123")
        assert resp.status_code == 200
        assert b"Report issues on" in resp.data
        assert b"https://github.com/evolarjun/webamr/issues" in resp.data

    def test_results_page_renders_amrrules_summary_and_interpreted_links(self):
        """Completed results page renders amrrules links in the same tab and displays versions."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "amr-job-123",
            "status": "Completed",
            "result_uri": "gs://bucket/results.tsv",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob
        mock_blob_stderr = _make_blob(name="results/amr-job-123/stderr.txt")
        mock_blob_nuc = _make_blob(name="results/amr-job-123/nucleotide.fna")
        mock_blob_prot = _make_blob(name="results/amr-job-123/protein.faa")
        mock_blob_sum = _make_blob(name="results/amr-job-123/amrrules_genome_summary.tsv")
        mock_blob_int = _make_blob(name="results/amr-job-123/amrrules_interpreted.tsv")
        MOCK_STORAGE.bucket.return_value.list_blobs.return_value = [mock_blob_stderr, mock_blob_nuc, mock_blob_prot, mock_blob_sum, mock_blob_int]

        resp = client.get("/results/amr-job-123")
        assert resp.status_code == 200
        assert b"amrrules_genome_summary.tsv" in resp.data
        assert b"amrrules_interpreted.tsv" in resp.data
        assert b'<a href="/amrrules-summary/amr-job-123">amrrules_genome_summary.tsv</a>' in resp.data
        assert b'<a href="/amrrules-interpreted/amr-job-123">amrrules_interpreted.tsv</a>' in resp.data
        assert b'<a href="/stderr/amr-job-123" target="_blank">Run log</a>' in resp.data
        assert b"AMRFinderPlus" in resp.data
        assert b"database version" in resp.data

    def test_results_page_renders_stored_job_versions(self):
        """Results page prioritizes versions recorded in Firestore over global config versions."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "amr-job-legacy",
            "status": "Completed",
            "result_uri": "gs://bucket/results.tsv",
            "software_version": "3.10.5",
            "database_version": "2023-08-08.1",
            "amrrules_version": "0.1.2",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/amr-job-legacy")
        assert resp.status_code == 200
        assert b"3.10.5" in resp.data
        assert b"2023-08-08.1" in resp.data
        expected_db_link = (
            b'<a id="database-version-link" '
            b'href="https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/changelogs/2023-08-08.1.txt" '
            b'target="_blank" rel="noopener">'
        )
        assert expected_db_link in resp.data
        assert b"0.1.2" in resp.data

    def test_results_page_omits_database_version_link_when_unknown(self):
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "amr-job-unknown-db",
            "status": "Completed",
            "result_uri": "gs://bucket/results.tsv",
            "software_version": "3.10.5",
            "database_version": "unknown",
            "amrrules_version": "0.1.2",
            "created_at": datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Gene\tDrug\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/results/amr-job-unknown-db")
        assert resp.status_code == 200
        assert b"database-version-link" not in resp.data

    def test_get_results_returns_recorded_versions(self):
        """GET /get-results/<id> returns recorded software, db, and amrrules versions."""
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {
            "job_id": "amr-job-789",
            "status": "Completed",
            "software_version": "3.12.0",
            "database_version": "2024-05-02.1",
            "amrrules_version": "0.3.1",
            "worker_version": "0.2.16",
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = doc
        mock_blob = _make_blob(exists=True, content=b"Header1\tHeader2\nVal1\tVal2\n")
        MOCK_STORAGE.bucket.return_value.blob.return_value = mock_blob

        resp = client.get("/get-results/amr-job-789")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["software_version"] == "3.12.0"
        assert data["database_version"] == "2024-05-02.1"
        assert data["amrrules_version"] == "0.3.1"
        assert data["worker_version"] == "0.2.16"


# ---------------------------------------------------------------------------
# Tests: FASTA Paste Drawer and Client/Backend Handling
# ---------------------------------------------------------------------------

class TestFastaPasteSupport:
    def test_index_page_contains_paste_drawers(self):
        """Index page should include details drawers and reset controls for pasting FASTA sequences."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'id="nuc-paste-details"' in resp.data
        assert b'id="nuc_paste"' in resp.data
        assert b'id="nuc-paste-status"' in resp.data
        assert b'id="nuc-field-reset"' in resp.data
        assert b'id="prot-paste-details"' in resp.data
        assert b'id="prot_paste"' in resp.data
        assert b'id="prot-paste-status"' in resp.data
        assert b'id="prot-field-reset"' in resp.data
        assert b"prepareFastaContent" in resp.data
        assert b"updatePasteStatus" in resp.data
        assert b"resetField" in resp.data
        assert b"handleFileSelection" in resp.data
        assert b"handlePasteInput" in resp.data

    def test_analyze_pasted_nucleotide_without_defline_adds_defline(self):
        """Pasting nucleotide sequence without defline adds >pasted_nucleotide and succeeds."""
        resp = client.post("/analyze", data={
            "nuc_paste": "ATGAAACAAAGCACTATT",
            "organism": ""
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "user_id" in data
        assert "results_url" in data

    def test_analyze_pasted_nucleotide_with_existing_defline(self):
        """Pasting nucleotide sequence with existing defline preserves it and succeeds."""
        resp = client.post("/analyze", data={
            "nuc_paste": ">my_contig_1\nATGAAACAAAGCACTATT\n",
            "organism": ""
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "user_id" in data

    def test_analyze_pasted_protein_without_defline_adds_defline(self):
        """Pasting protein sequence without defline adds >pasted_protein and succeeds."""
        resp = client.post("/analyze", data={
            "prot_paste": "MKQSTIALALLPLLFTPVTKA",
            "organism": ""
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "user_id" in data

    def test_analyze_rejects_both_nuc_file_and_nuc_paste(self):
        """Submitting both a nucleotide file and pasted nucleotide text returns 400 error."""
        resp = client.post("/analyze", data={
            "nuc_file": _fasta_file("test.fasta", b">seq\nATCG\n"),
            "nuc_paste": "ATGAAACAAAGCACTATT",
            "organism": ""
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Provide either a nucleotide file or pasted sequence, not both." in data["error"]

    def test_analyze_rejects_both_prot_file_and_prot_paste(self):
        """Submitting both a protein file and pasted protein text returns 400 error."""
        resp = client.post("/analyze", data={
            "prot_file": _fasta_file("test.faa", b">prot\nMKQS\n"),
            "prot_paste": "MKQSTIALALLPLLFTPVTKA",
            "organism": ""
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert "Provide either a protein file or pasted sequence, not both." in data["error"]

    def test_analyze_pasted_nuc_and_prot_requires_gff(self):
        """Providing both pasted nucleotide and pasted protein requires a GFF file."""
        resp = client.post("/analyze", data={
            "nuc_paste": "ATGAAACAAAGCACTATT",
            "prot_paste": "MKQSTIALALLPLLFTPVTKA",
            "organism": ""
        })
        assert resp.status_code == 400
        data = resp.get_json()
        assert "A GFF file is required when providing both nucleotide and protein files." in data["error"]


# ---------------------------------------------------------------------------
# Tests: Non-JS / Text Browser (Lynx) Progressive Enhancement Support
# ---------------------------------------------------------------------------

class TestNonJsSupport:
    """Tests verifying WebAMR works without JavaScript or in text-based browsers."""

    def test_analyze_non_js_validation_error_renders_html_400(self):
        """When Accept prefers HTML, validation failure returns 400 with re-rendered HTML."""
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        resp = client.post("/analyze", data={}, headers=headers)
        assert resp.status_code == 400
        assert resp.mimetype == "text/html"
        html = resp.data.decode("utf-8")
        assert "Error:" in html
        assert "No nucleotide or protein file provided." in html

    def test_analyze_non_js_validation_error_preserves_form_fields(self):
        """Non-JS re-render on error retains user-entered inputs across fields."""
        headers = {"Accept": "text/html"}
        data = {
            "job_name": "TestNonJsJob",
            "nuc_paste": "ATGAAACAAAGCACTATT",
            "annotation_format": "bakta",
            "organism": "Campylobacter",
            "amrrules_organism": "s__Campylobacter jejuni",
            # Invalid job name characters to trigger validation error
            "job_name": "Invalid@Name!",
        }
        resp = client.post("/analyze", data=data, headers=headers)
        assert resp.status_code == 400
        html = resp.data.decode("utf-8")
        assert "Job name can only contain letters, numbers, spaces, underscores, and hyphens." in html
        assert 'value="Invalid@Name!"' in html
        assert "ATGAAACAAAGCACTATT" in html
        assert 'id="nuc-paste-details" class="paste-details" open' in html
        assert '<option value="bakta" selected>bakta</option>' in html
        assert '<option value="s__Campylobacter jejuni" selected>' in html

    def test_analyze_non_js_success_redirects_303(self):
        """When Accept prefers HTML, successful submission returns 303 redirect to /results/<id>."""
        headers = {"Accept": "text/html"}
        data = {
            "nuc_file": _fasta_file("test.fasta", b">seq1\nATCG\n"),
            "job_name": "ValidJob",
        }
        resp = client.post("/analyze", data=data, headers=headers)
        assert resp.status_code == 303
        location = resp.headers.get("Location", "")
        assert location.startswith("/results/")

    def test_analyze_ajax_returns_json_on_success(self):
        """When X-Requested-With or Accept: application/json is present, return JSON 200."""
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        }
        data = {
            "nuc_file": _fasta_file("test.fasta", b">seq1\nATCG\n"),
            "job_name": "AjaxJob",
        }
        resp = client.post("/analyze", data=data, headers=headers)
        assert resp.status_code == 200
        assert resp.mimetype == "application/json"
        body = resp.get_json()
        assert "results_url" in body
        assert "user_id" in body

    def test_analyze_ajax_returns_json_on_error(self):
        """When Accept is JSON or AJAX, validation error returns JSON 400."""
        headers = {"Accept": "application/json"}
        resp = client.post("/analyze", data={}, headers=headers)
        assert resp.status_code == 400
        assert resp.mimetype == "application/json"
        body = resp.get_json()
        assert "error" in body

    def test_results_page_includes_noscript_refresh_when_queued(self):
        """When a job is Queued, results page includes noscript 5-second meta refresh."""
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "job_id": "test-refresh-job",
            "status": "Queued",
            "created_at": datetime.now(timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/results/test-refresh-job")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<noscript>' in html
        assert '<meta http-equiv="refresh" content="5">' in html
        assert 'Refreshing every 5 seconds until analysis completes' in html

    def test_results_page_includes_noscript_refresh_when_processing(self):
        """When a job is Processing, results page includes noscript 5-second meta refresh."""
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "job_id": "test-refresh-job",
            "status": "Processing",
            "created_at": datetime.now(timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/results/test-refresh-job")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<meta http-equiv="refresh" content="5">' in html

    def test_results_page_omits_noscript_refresh_when_completed(self):
        """When a job is Completed, results page omits meta refresh."""
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "job_id": "completed-job",
            "status": "Completed",
            "created_at": datetime.now(timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc
        tsv = b"col1\tcol2\nval1\tval2\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=True, content=tsv)
        MOCK_STORAGE.bucket.return_value.list_blobs.return_value = []

        resp = client.get("/results/completed-job")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<meta http-equiv="refresh" content="5">' not in html

    def test_results_page_omits_noscript_refresh_when_failed(self):
        """When a job is Failed, results page omits meta refresh."""
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "job_id": "failed-job",
            "status": "Failed",
            "error_message": "Some worker error",
            "created_at": datetime.now(timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/results/failed-job")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<meta http-equiv="refresh" content="5">' not in html

    def test_results_page_renders_native_download_link(self):
        """Results page renders a native <a> link for downloading results without JS."""
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {
            "job_id": "dl-job-id",
            "status": "Completed",
            "created_at": datetime.now(timezone.utc),
        }
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc
        tsv = b"col1\tcol2\nval1\tval2\n"
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(exists=True, content=tsv)
        MOCK_STORAGE.bucket.return_value.list_blobs.return_value = []

        resp = client.get("/results/dl-job-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        expected_link = '<a href="/output/dl-job-id" class="action-btn-primary" download="amr_results.tsv">Download AMRFinderPlus results</a>'
        assert expected_link in html

    def test_table_view_renders_native_download_link(self):
        """Table view renders a native <a> download link instead of JS-only button."""
        MOCK_STORAGE.bucket.return_value.blob.return_value = _make_blob(
            exists=True, content=b"Gene\tClass\ngeneA\tBeta-lactam\n"
        )
        mock_doc = MagicMock(exists=True)
        mock_doc.to_dict.return_value = {"job_name": "Demo"}
        MOCK_FIRESTORE.collection.return_value.document.return_value.get.return_value = mock_doc

        resp = client.get("/amrrules-summary/test-user-id")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert '<a href="?download=true" class="action-btn-primary" download=' in html


class TestLocalWorkerDispatch:
    """Tests for direct local worker dispatch via WORKER_URL."""

    def test_dispatch_to_local_worker_sends_post_envelope(self):
        """Verifies dispatch_to_local_worker constructs a valid Pub/Sub envelope and posts to worker URL."""
        import base64
        import urllib.request
        from unittest.mock import patch, MagicMock

        mock_resp = MagicMock()
        mock_resp.status = 200

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            message_payload = json.dumps({"job_id": "job-local-999", "gcs_uri": "gs://bucket/file.fa"})
            main.dispatch_to_local_worker("http://localhost:8080", message_payload)

            assert mock_urlopen.call_count == 1
            req = mock_urlopen.call_args[0][0]
            assert req.full_url == "http://localhost:8080/"
            assert req.headers["Content-type"] == "application/json"
            assert req.get_method() == "POST"

            body = json.loads(req.data.decode("utf-8"))
            assert "message" in body
            assert "data" in body["message"]

            decoded_str = base64.b64decode(body["message"]["data"]).decode("utf-8")
            assert json.loads(decoded_str) == {"job_id": "job-local-999", "gcs_uri": "gs://bucket/file.fa"}

    def test_dispatch_to_local_worker_handles_connection_error(self):
        """Verifies dispatch_to_local_worker handles exceptions without propagating crashes."""
        from unittest.mock import patch

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            # Should not raise exception
            main.dispatch_to_local_worker("http://localhost:8080", '{"test": 1}')

    def test_send_pubsub_message_uses_local_worker_when_set(self, monkeypatch):
        """When WORKER_URL is set, send_pubsub_message dispatches locally instead of calling GCP Pub/Sub."""
        from unittest.mock import patch, MagicMock
        monkeypatch.setenv("WORKER_URL", "http://localhost:8080")

        MOCK_PUBLISHER.publish.reset_mock()

        with patch("main.dispatch_to_local_worker") as mock_dispatch:
            main.send_pubsub_message('{"job_id": "direct-local-1"}')

            # Wait briefly for daemon thread if needed
            import time
            time.sleep(0.1)

            assert MOCK_PUBLISHER.publish.call_count == 0
            mock_dispatch.assert_called_once_with("http://localhost:8080", '{"job_id": "direct-local-1"}')

    def test_send_pubsub_message_publishes_to_gcp_when_worker_url_unset(self, monkeypatch):
        """When WORKER_URL is not set, send_pubsub_message uses GCP Pub/Sub."""
        monkeypatch.delenv("WORKER_URL", raising=False)
        MOCK_PUBLISHER.publish.reset_mock()
        mock_future = MagicMock()
        mock_future.result.return_value = "msg-id-123"
        MOCK_PUBLISHER.publish.return_value = mock_future
        MOCK_PUBLISHER.topic_path.return_value = "projects/amrfinder/topics/amr-jobs-topic"

        main.send_pubsub_message('{"job_id": "gcp-job-1"}')

        MOCK_PUBLISHER.publish.assert_called_once()

    def test_read_file_resolves_relative_to_frontend_directory(self, monkeypatch):
        """read_file resolves files relative to frontend directory even if cwd is elsewhere."""
        # Change cwd to root or a temp directory
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        monkeypatch.chdir(root_dir)
        content = main.read_file("taxgroup.tsv")
        assert len(content) > 0
        assert "Escherichia" in content






