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

def _make_push_body(job_id="job-abc", gcs_uri="gs://bucket/uploads/in.fasta", params=None, nuc_filename=None, prot_filename=None, gff_filename=None):
    """Build a Pub/Sub push envelope as Cloud Run would receive it."""
    payload_dict = {
        "job_id": job_id,
        "gcs_uri": gcs_uri,
        "parameters": params or {},
    }
    if nuc_filename is not None:
        payload_dict["nuc_filename"] = nuc_filename
    if prot_filename is not None:
        payload_dict["prot_filename"] = prot_filename
    if gff_filename is not None:
        payload_dict["gff_filename"] = gff_filename

    payload = json.dumps(payload_dict).encode("utf-8")
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
        with pytest.raises(Exception) as exc_info:
            worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={})
        err_msg = str(exc_info.value)
        assert "AMRFinderPlus failed:" in err_msg
        assert "--- Standard Output ---" in err_msg
        assert "--- Standard Error ---" in err_msg
        assert "Database error" in err_msg

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_nonzero_returncode_includes_both_stdout_and_stderr(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="Read 100 contigs\n", stderr="BLAST failure\n")
        with pytest.raises(Exception) as exc_info:
            worker.run_amrfinder(nuc_input="/tmp/in.fasta", prot_input=None, gff_input=None, output_tsv="/tmp/out.tsv", stderr_path="/tmp/stderr.txt", nucleotide_path="/tmp/nuc.fna", protein_path="/tmp/prot.faa", params={})
        err_msg = str(exc_info.value)
        assert "AMRFinderPlus failed:" in err_msg
        assert "--- Standard Output ---\nRead 100 contigs" in err_msg
        assert "--- Standard Error ---\nBLAST failure" in err_msg

    @patch("worker.subprocess.run")
    def test_run_amrfinder_writes_stdout_and_stderr_sections_to_file(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="amrfinder stdout text\n", stderr="amrfinder stderr text\n")
        stderr_file = tmp_path / "test_amrfinder_log.txt"
        worker.run_amrfinder(
            nuc_input="/tmp/in.fasta",
            prot_input=None,
            gff_input=None,
            output_tsv="/tmp/out.tsv",
            stderr_path=str(stderr_file),
            nucleotide_path="/tmp/nuc.fna",
            protein_path="/tmp/prot.faa",
            params={},
        )
        content = stderr_file.read_text()
        assert "=== AMRFinderPlus Log ===" in content
        assert "--- Standard Output ---\namrfinder stdout text" in content
        assert "--- Standard Error ---\namrfinder stderr text" in content

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

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_amrfinder_includes_name_flag_when_job_name_provided(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(
            nuc_input="/tmp/in.fasta",
            prot_input=None,
            gff_input=None,
            output_tsv="/tmp/out.tsv",
            stderr_path="/tmp/stderr.txt",
            nucleotide_path="/tmp/nuc.fna",
            protein_path="/tmp/prot.faa",
            params={},
            job_name="Sample-01",
        )
        cmd = mock_run.call_args[0][0]
        assert "--name" in cmd
        assert "Sample-01" in cmd

    @patch("builtins.open", mock_open())
    @patch("worker.subprocess.run")
    def test_amrfinder_omits_name_flag_when_no_job_name(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        worker.run_amrfinder(
            nuc_input="/tmp/in.fasta",
            prot_input=None,
            gff_input=None,
            output_tsv="/tmp/out.tsv",
            stderr_path="/tmp/stderr.txt",
            nucleotide_path="/tmp/nuc.fna",
            protein_path="/tmp/prot.faa",
            params={},
            job_name=None,
        )
        cmd = mock_run.call_args[0][0]
        assert "--name" not in cmd


class TestDiagnoseAmrfinderFailure:
    def test_detects_fastq_in_nucleotide_slot(self, tmp_path):
        nuc_file = tmp_path / "reads.fastq"
        nuc_file.write_text("@SEQ_ID\nACGTACGT\n+\nIIIIIIII\n")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
            nuc_input=str(nuc_file),
            filenames={"nuc": "reads.fastq"},
        )
        assert "FASTQ" in summary["title"]
        assert "reads.fastq" in summary["explanation"]
        assert "FASTA" in summary["suggestion"]

    def test_detects_genbank_in_fasta_slot(self, tmp_path):
        nuc_file = tmp_path / "genome.gbk"
        nuc_file.write_text("LOCUS       NC_000913               4641652 bp    DNA     circular\n")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
            nuc_input=str(nuc_file),
            filenames={"nuc": "genome.gbk"},
        )
        assert "GenBank" in summary["title"]
        assert "genome.gbk" in summary["explanation"]

    def test_detects_html_in_fasta_slot(self, tmp_path):
        nuc_file = tmp_path / "page.fasta"
        nuc_file.write_text("<!DOCTYPE html><html><body>Not found</body></html>\n")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
            nuc_input=str(nuc_file),
            filenames={"nuc": "page.fasta"},
        )
        assert "HTML" in summary["title"]
        assert "page.fasta" in summary["explanation"]

    def test_detects_gff_in_fasta_slot(self, tmp_path):
        nuc_file = tmp_path / "wrong.fasta"
        nuc_file.write_text("##gff-version 3\nctg1\tProkka\tCDS\t1\t900\t.\t+\t0\tID=cds1\n")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
            nuc_input=str(nuc_file),
            filenames={"nuc": "wrong.fasta"},
        )
        assert "GFF" in summary["title"]
        assert "wrong.fasta" in summary["explanation"]

    def test_detects_empty_file_on_disk_and_from_error(self, tmp_path):
        empty_file = tmp_path / "empty.fa"
        empty_file.write_text("")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nfasta_check.cpp: Empty file",
            returncode=1,
            nuc_input=str(empty_file),
            filenames={"nuc": "empty.fa"},
        )
        assert "Empty" in summary["title"]
        assert "empty.fa" in summary["explanation"]

        summary_no_file = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nEmpty file",
            returncode=1,
        )
        assert "Empty" in summary_no_file["title"]

    def test_detects_missing_fasta_header_without_disk_file(self):
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
        )
        assert "Invalid FASTA format" in summary["title"]
        assert "'>'" in summary["explanation"]

    def test_detects_protein_in_nucleotide_slot(self, tmp_path):
        nuc_file = tmp_path / "proteins.faa"
        nuc_file.write_text(">prot1\nMKKLLIIFFPPQQEE*VLLIIFFPPQQEE\n")
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nfasta_check.cpp: Wrong nucleotide character 'M'",
            returncode=1,
            nuc_input=str(nuc_file),
            filenames={"nuc": "proteins.faa"},
        )
        assert "Protein" in summary["title"]
        assert "proteins.faa" in summary["explanation"]
        assert "appears to be a protein FASTA rather than a nucleotide FASTA." in summary["explanation"]
        assert "Protein FASTA" in summary["suggestion"]

    def test_detects_nucleotide_in_protein_slot_from_amrfinder_message(self):
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nfasta_check.cpp: Protein sequences looks like a nucleotide sequences",
            returncode=1,
        )
        assert "Incorrect sequence type" in summary["title"]
        assert "Assembled nucleotide FASTA" in summary["suggestion"]

    def test_detects_gff_protein_id_mismatch_and_mentions_annotation_format(self):
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr='*** ERROR ***\ngff_check.cpp: Protein id "gnl|Prokka|ABC_00001" is not in the .gff-file',
            returncode=1,
            params={"annotation_format": "standard"},
        )
        assert "mismatch" in summary["title"].lower()
        assert "gnl|Prokka|ABC_00001" in summary["explanation"]
        assert "standard" in summary["suggestion"]
        assert "prokka" in summary["suggestion"]

    def test_detects_malformed_gff_and_embedded_fasta_section(self, tmp_path):
        gff_file = tmp_path / "prokka.gff"
        gff_file.write_text(
            "##gff-version 3\n"
            "ctg1\tProkka\tCDS\t1\t900\t.\t+\t0\tID=cds1\n"
            "##FASTA\n"
            ">ctg1\n"
            "ATGATGATG\n"
        )
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\ngff.cpp: line 4: 9 fields are expected in each line",
            returncode=1,
            gff_input=str(gff_file),
            filenames={"gff": "prokka.gff"},
        )
        assert "FASTA" in summary["title"]
        assert "prokka.gff" in summary["explanation"]
        assert "##FASTA" in summary["suggestion"]

        summary_no_file = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nline 10: 9 fields are expected in each line",
            returncode=1,
        )
        assert "GFF3" in summary_no_file["title"]
        assert "9 tab-separated" in summary_no_file["explanation"]
        assert "tab-delimited" in summary_no_file["suggestion"]

    def test_detects_nucleotide_in_protein_slot_on_disk(self, tmp_path):
        prot_file = tmp_path / "contigs.fna"
        prot_file.write_text(">contig1\n" + ("ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT\n"))
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nWrong amino acid character",
            returncode=1,
            prot_input=str(prot_file),
            filenames={"prot": "contigs.fna"},
        )
        assert "Nucleotide" in summary["title"]
        assert "contigs.fna" in summary["explanation"]

    def test_detects_binary_file_and_header_only_fasta(self, tmp_path):
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4\x00binarycontent")
        summary_bin = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nFASTA should start with '>'",
            returncode=1,
            nuc_input=str(pdf_file),
            filenames={"nuc": "doc.pdf"},
        )
        assert "Binary" in summary_bin["title"]
        assert "doc.pdf" in summary_bin["explanation"]

        hdr_only = tmp_path / "hdr_only.fa"
        hdr_only.write_text(">seq1\n>seq2\n")
        summary_hdr = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nEmpty file",
            returncode=1,
            nuc_input=str(hdr_only),
            filenames={"nuc": "hdr_only.fa"},
        )
        assert "Empty" in summary_hdr["title"]
        assert "hdr_only.fa" in summary_hdr["explanation"]

    def test_detects_negative_returncode_signal(self):
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="",
            returncode=-9,
        )
        assert "terminated" in summary["title"].lower()
        assert "-9" in summary["explanation"]
        assert "memory" in summary["explanation"].lower()
        expected_suffix = (
            "If you think there is a problem with the software, please report in a "
            "[GitHub issue](https://github.com/evolarjun/webamr/issues/). "
            "Please include the Job ID in your report to make it easier for us to reproduce the bug."
        )
        assert expected_suffix in summary["suggestion"]

    def test_general_fallbacks_include_github_issue_bug_report_guidance(self):
        expected_suffix = (
            "If you think there is a problem with the software, please report in a "
            "[GitHub issue](https://github.com/evolarjun/webamr/issues/). "
            "Please include the Job ID in your report to make it easier for us to reproduce the bug."
        )
        other_err = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="*** ERROR ***\nUnexpected internal condition",
            returncode=1,
        )
        assert other_err["title"] == "AMRFinderPlus reported an error"
        assert expected_suffix in other_err["suggestion"]

        generic_err = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr="Segmentation fault",
            returncode=1,
        )
        assert generic_err["title"] == "AMRFinderPlus execution failed"
        assert expected_suffix in generic_err["suggestion"]

    def test_sanitizes_internal_tmp_paths(self):
        summary = worker.diagnose_amrfinder_failure(
            stdout="",
            stderr=(
                "*** ERROR ***\n"
                "Cannot open /tmp/job-123/inputs/my_reads.fastq for /tmp/job-123/outputs/amrfinder.tsv"
            ),
            returncode=1,
            nuc_input="/tmp/job-123/inputs/my_reads.fastq",
            filenames={"nuc": "original_reads.fastq"},
        )
        assert "/tmp/job-123" not in summary["explanation"]
        assert "original_reads.fastq" in summary["explanation"]
        assert "amrfinder.tsv" in summary["explanation"]


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
    def test_path_traversal_prevention(self, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        # Inject path traversal payload
        body = _make_push_body(
            job_id="job-sec",
            gcs_uri="gs://bucket/uploads/in.fasta",
            nuc_filename="../../../etc/passwd"
        )
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200

        # Check what was passed to download_blob for the local_path
        # Should be sanitized to just 'etc_passwd' preventing writing outside /tmp
        # werkzeug secure_filename turns '../../../etc/passwd' into 'etc_passwd'
        expected_local_path = "/tmp/job-sec/inputs/etc_passwd"

        # Look through the calls to download_blob to find the one matching our nuc_filename
        download_calls = mock_dl.call_args_list
        found = False
        for call in download_calls:
            gcs_uri, local_path = call.args
            if gcs_uri.endswith("../../../etc/passwd"):
                assert local_path == expected_local_path, f"Path was not sanitized! Expected {expected_local_path}, got {local_path}"
                found = True
                break

        assert found, "download_blob was not called with the expected GCS URI"

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
    @patch("worker.shutil.rmtree")
    def test_tmp_files_cleaned_up(self, mock_rmtree, mock_dl, mock_run, mock_ul):
        mock_run.return_value = ""
        flask_client.post("/", json=_make_push_body(job_id="job-cleanup"))

        mock_rmtree.assert_called_once_with("/tmp/job-cleanup")

    @patch("worker.upload_blob", return_value="gs://output/results/job-collision/results.tsv")
    @patch("worker.run_amrfinder")
    @patch("worker.download_blob")
    def test_input_and_output_filename_collision_avoidance(self, mock_dl, mock_run, mock_ul):
        """
        Regression test: When a user uploads input files named protein.faa or
        nucleotide.fna, the input paths and the amrfinder output paths (--protein_output,
        --nucleotide_output) must not collide.
        """
        mock_run.return_value = ""
        payload = {
            "job_id": "job-collision",
            "gcs_uri": "gs://bucket/uploads/in.fasta",
            "nuc_filename": "nucleotide.fna",
            "prot_filename": "protein.faa",
            "gff_filename": "genomic.gff",
            "parameters": {
                "has_protein": True,
                "has_nucleotide": True,
            },
        }
        body = _make_raw_push_body(json.dumps(payload).encode("utf-8"))
        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200

        kwargs = mock_run.call_args.kwargs
        assert kwargs["prot_input"] == "/tmp/job-collision/inputs/protein.faa"
        assert kwargs["protein_path"] == "/tmp/job-collision/outputs/protein.faa"
        assert kwargs["prot_input"] != kwargs["protein_path"]

        assert kwargs["nuc_input"] == "/tmp/job-collision/inputs/nucleotide.fna"
        assert kwargs["nucleotide_path"] == "/tmp/job-collision/outputs/nucleotide.fna"
        assert kwargs["nuc_input"] != kwargs["nucleotide_path"]

        assert kwargs["gff_input"] == "/tmp/job-collision/inputs/genomic.gff"
        assert kwargs["output_tsv"] == "/tmp/job-collision/outputs/amrfinder.tsv"

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
        mock_exists.side_effect = lambda path: "amrrules_genome_summary.tsv" in path or "results.tsv" in path
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

    @patch("worker.upload_blob")
    @patch("worker.run_amrrules", return_value="amrrules success")
    @patch("worker.run_amrfinder", return_value="success")
    @patch("worker.download_blob")
    @patch("worker.os.path.exists", return_value=False)
    def test_amrrules_called_with_job_name_as_sample_id(self, mock_exists, mock_dl, mock_run_af, mock_rules, mock_ul):
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        params = {"amrrules_organism": "s__Escherichia coli"}
        payload = {
            "job_id": "job-amr-name-test",
            "job_name": "Sample Job 100",
            "gcs_uri": "gs://b/f.fa",
            "parameters": params,
        }
        body = _make_raw_push_body(json.dumps(payload).encode("utf-8"))

        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        mock_rules.assert_called_once()
        assert mock_rules.call_args[1]["sample_id"] == "Sample Job 100"
        assert mock_run_af.call_args[1]["job_name"] == "Sample Job 100"

    @patch("worker.upload_blob")
    @patch("worker.run_amrfinder", return_value="success")
    @patch("worker.download_blob")
    @patch("worker.os.path.exists", return_value=False)
    def test_amrfinder_called_without_job_name_when_omitted(self, mock_exists, mock_dl, mock_run_af, mock_ul):
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        payload = {
            "job_id": "job-amr-no-name",
            "gcs_uri": "gs://b/f.fa",
            "parameters": {},
        }
        body = _make_raw_push_body(json.dumps(payload).encode("utf-8"))

        resp = flask_client.post("/", json=body)
        assert resp.status_code == 200
        assert mock_run_af.call_args[1]["job_name"] is None

    def test_corrupt_gzip_file_updates_failed_with_clear_error_summary(self):
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc

        def write_corrupt_gz(gcs_uri, local_path):
            with open(local_path, "wb") as f:
                f.write(b"not-a-valid-gzip-stream")

        with patch("worker.download_blob", side_effect=write_corrupt_gz):
            body = _make_push_body(
                job_id="job-bad-gz",
                gcs_uri="gs://bucket/uploads/sample.fasta.gz",
                nuc_filename="sample.fasta.gz",
            )
            resp = flask_client.post("/", json=body)

        assert resp.status_code == 200
        update_calls = [c[0][0] for c in mock_doc.update.call_args_list if c[0]]
        failed_call = next(c for c in update_calls if c.get("status") == "Failed")
        assert "sample.fasta.gz" in failed_call["error_message"]
        assert failed_call["error_summary"]["title"] == "Corrupt or invalid .gz archive"
        assert "sample.fasta.gz" in failed_call["error_summary"]["explanation"]

    @patch("worker.upload_blob", return_value="gs://output/results/job-fail-sum/stderr.txt")
    @patch("worker.download_blob")
    @patch("worker.subprocess.run")
    def test_failed_amrfinder_stores_error_summary_and_keeps_raw_stderr_log(self, mock_subproc, mock_dl, mock_ul):
        mock_doc = MagicMock()
        worker.get_firestore_client().collection.return_value.document.return_value = mock_doc
        raw_stderr = "*** ERROR ***\nFASTA should start with '>'\n"
        mock_subproc.return_value = MagicMock(returncode=1, stdout="Running: amrfinder\n", stderr=raw_stderr)

        captured_stderr_log = {}

        def fake_upload(local_path, dest):
            if dest.endswith("stderr.txt"):
                with open(local_path, "r") as f:
                    captured_stderr_log["content"] = f.read()
            return f"gs://output/{dest}"

        mock_ul.side_effect = fake_upload
        body = _make_push_body(
            job_id="job-fail-sum",
            gcs_uri="gs://bucket/uploads/bad.fa",
            nuc_filename="bad.fa",
        )
        resp = flask_client.post("/", json=body)

        assert resp.status_code == 200
        update_calls = [c[0][0] for c in mock_doc.update.call_args_list if c[0]]
        failed_call = next(c for c in update_calls if c.get("status") == "Failed")
        assert failed_call["error_summary"]["title"] == "Invalid FASTA format"
        assert "AMRFinderPlus failed: Invalid FASTA format" in failed_call["error_message"]
        assert "--- Standard Error ---" in failed_call["error_message"]
        assert "Cause:" not in captured_stderr_log["content"]
        assert "FASTA should start with '>'" in captured_stderr_log["content"]



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
        assert "Command: amrrules --input /tmp/out.tsv --output-prefix /tmp/prefix --organism 's__Escherichia coli' --sample-id job-123" in content
        assert "amrrules stderr log" in content
        assert "amrrules stdout log" in content

    @patch("worker.subprocess.run")
    def test_amrrules_uses_sample_id_when_provided(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
        worker.run_amrrules(
            amrfp_output_tsv="/tmp/out.tsv",
            amrrules_organism="s__Escherichia coli",
            output_prefix="/tmp/prefix",
            sample_id="My-Sample_Name-01",
        )
        cmd = mock_run.call_args[0][0]
        assert "--sample-id" in cmd
        assert "My-Sample_Name-01" in cmd

    @patch("worker.subprocess.run")
    def test_amrrules_logs_command_before_execution_even_if_it_fails(self, mock_run, tmp_path):
        mock_run.side_effect = RuntimeError("Process execution failed")
        stderr_file = tmp_path / "test_stderr.txt"
        stderr_file.write_text("=== AMRFinderPlus Log ===\nAMRFinder output\n")

        import pytest
        with pytest.raises(RuntimeError):
            worker.run_amrrules(
                amrfp_output_tsv="/tmp/out.tsv",
                amrrules_organism="s__Escherichia coli",
                output_prefix="/tmp/prefix",
                job_id="job-123",
                stderr_path=str(stderr_file),
            )

        content = stderr_file.read_text()
        assert "=== AMRrules Log ===" in content
        assert "Command: amrrules --input /tmp/out.tsv --output-prefix /tmp/prefix --organism 's__Escherichia coli' --sample-id job-123" in content

    @patch("worker.subprocess.run")
    def test_amrrules_nonzero_returncode_raises_with_amrfinder_output_and_cmd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="amrrules stdout log\n", stderr="amrrules stderr log\n")
        with pytest.raises(Exception) as exc_info:
            worker.run_amrrules(
                amrfp_output_tsv="/tmp/out.tsv",
                amrrules_organism="s__Escherichia coli",
                output_prefix="/tmp/prefix",
                sample_id="job-123",
                amrfp_stdout="amrfp stdout info\n",
                amrfp_stderr="amrfp stderr warning\n",
            )
        msg = str(exc_info.value)
        assert "=== AMRFinderPlus Output ===" in msg
        assert "--- Standard Output ---\namrfp stdout info" in msg
        assert "--- Standard Error ---\namrfp stderr warning" in msg
        assert "=== AMRrules Output ===" in msg
        assert "Command: amrrules" in msg
        assert "--- Standard Output ---\namrrules stdout log" in msg
        assert "--- Standard Error ---\namrrules stderr log" in msg


class TestUploadVersions:
    def setup_method(self):
        if hasattr(worker.get_installed_versions, "cache_clear"):
            worker.get_installed_versions.cache_clear()

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
    def setup_method(self):
        if hasattr(worker.get_installed_versions, "cache_clear"):
            worker.get_installed_versions.cache_clear()

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

    @patch("importlib.metadata.version", return_value="0.3.1")
    @patch("worker.subprocess.run")
    def test_get_installed_versions_caches_result(self, mock_run, mock_pkg_ver):
        mock_output = (
            "Software version: 4.2.7\n"
            "Database version: 2026-03-24.1\n"
        )
        mock_run.return_value = MagicMock(stdout=mock_output)

        first = worker.get_installed_versions()
        second = worker.get_installed_versions()
        assert first == second
        assert mock_run.call_count == 1
        assert mock_pkg_ver.call_count == 1


