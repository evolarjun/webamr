"""
AMRFinderPlus Worker — Pub/Sub Push Handler
============================================
Runs as a Cloud Run service. Pub/Sub is configured to PUSH each job message
as an HTTP POST to this service's root URL. Cloud Run spins up an instance on
demand per job, runs AMRFinderPlus, then scales back to zero.

No streaming pull loop is needed; Pub/Sub handles delivery and retries.
"""
import os
import re
import json
import base64
import functools
import gzip
import shutil
import shlex
import subprocess
import threading
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from google.cloud import storage, firestore

try:
    with open('VERSION.txt', 'r') as f:
        APP_VERSION = f.read().strip()
except FileNotFoundError:
    APP_VERSION = "unknown"

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "amrfinder")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", f"amr-output-bucket-{PROJECT_ID}")

_storage_client = None
_firestore_client = None

def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client

def get_firestore_client():
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=PROJECT_ID)
    return _firestore_client


def _parse_software_version(output_str):
    """Extract software version number from amrfinder output."""
    if not output_str:
        return None
    match = re.search(r'Software\s+version:\s*([^\s\r\n\'"]+)', output_str, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("'\"")
    return output_str.strip().strip("'\"")


def _parse_database_version(output_str):
    """Extract database version string from amrfinder output."""
    if not output_str:
        return None
    match = re.search(r'Database\s+version:\s*([^\s\r\n\'"]+)', output_str, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip("'\"")
    return output_str.strip().strip("'\"")


@functools.lru_cache(maxsize=1)
def get_installed_versions():
    """Returns a dict of the currently installed tool and database versions."""
    db_version = None
    software_version = None

    try:
        result = subprocess.run(["amrfinder", "-V"], capture_output=True, text=True, check=True)
        db_version = _parse_database_version(result.stdout)
        software_version = _parse_software_version(result.stdout)
    except Exception as e:
        print(f"Failed to get AMRFinderPlus versions from amrfinder -V: {e}")

    amrrules_version = None
    try:
        import importlib.metadata
        amrrules_version = importlib.metadata.version("amrrules")
    except Exception as e:
        print(f"Failed to get amrrules version: {e}")

    return {
        "database_version": db_version,
        "software_version": software_version,
        "amrrules_version": amrrules_version,
    }


def upload_versions():
    """Uploads the local AMRFinder database, software, and AMRrules versions to GCS configuration path."""
    storage_client = get_storage_client()
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    versions = get_installed_versions()

    # Upload Database Version
    if versions.get("database_version"):
        try:
            print(f"Uploading AMRFinder DB version to gs://{OUTPUT_BUCKET}/config/database_version.txt")
            blob = bucket.blob("config/database_version.txt")
            blob.upload_from_string(versions["database_version"])
        except Exception as e:
            print(f"Failed to upload DB version: {e}")

    # Upload Software Version
    if versions.get("software_version"):
        try:
            print(f"Uploading AMRFinder software version to gs://{OUTPUT_BUCKET}/config/software_version.txt")
            software_blob = bucket.blob("config/software_version.txt")
            software_blob.upload_from_string(versions["software_version"])
        except Exception as e:
            print(f"Failed to upload software version: {e}")

    # Upload AMRrules Version
    if versions.get("amrrules_version"):
        try:
            print(f"Uploading AMRrules version to gs://{OUTPUT_BUCKET}/config/amrrules_version.txt")
            amrrules_blob = bucket.blob("config/amrrules_version.txt")
            amrrules_blob.upload_from_string(versions["amrrules_version"])
        except Exception as e:
            print(f"Failed to upload amrrules version: {e}")

# Upload config once on container cold-start in a background thread to avoid blocking Gunicorn startup
try:
    threading.Thread(target=upload_versions, daemon=True).start()
    print("Background thread started for version upload.")
except Exception as e:
    print(f"Failed to start version upload thread: {e}")


def _log(job_id, msg):
    """Print a log line prefixed with the job ID."""
    print(f"[{job_id}] {msg}")


def download_blob(gcs_uri, local_path):
    """Download a file from GCS given a gs:// URI."""
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]
    storage_client = get_storage_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(local_path)


def upload_blob(local_path, destination_blob_name):
    """Upload a local file to the output GCS bucket."""
    storage_client = get_storage_client()
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{OUTPUT_BUCKET}/{destination_blob_name}"


class AMRResult(str):
    """String subclass representing process stdout that also holds stderr."""
    def __new__(cls, stdout="", stderr=""):
        obj = str.__new__(cls, stdout)
        obj.stdout = stdout
        obj.stderr = stderr
        return obj


_AMRFINDER_BOILERPLATE_PREFIXES = (
    "Running:",
    "Software directory:",
    "Software version:",
    "Database directory:",
    "Database version:",
)

_FALLBACK_BUG_REPORT_SUFFIX = (
    " If you think there is a problem with the software, please report in a "
    "[GitHub issue](https://github.com/evolarjun/webamr/issues/). "
    "Please include the Job ID in your report to make it easier for us to reproduce the bug."
)

_NUC_VALID_CHARS = set("ACGTUacgtuBDHKMNRSVWYbdhkmnrsvwy-")
_AA_ONLY_CHARS = set("EFILPQZefilpqz*")
_ACGTN_CHARS = set("ACGTNacgtn")


def _sanitize_error_paths(text, path_map=None):
    """Replace internal /tmp worker paths with user-facing filenames."""
    if not text:
        return ""
    sanitized = text
    if path_map:
        for local_path, display_name in sorted(
            path_map.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if local_path and display_name:
                sanitized = sanitized.replace(local_path, display_name)
    sanitized = re.sub(
        r"/tmp/[A-Za-z0-9_-]+/(?:inputs|outputs)/([^\s'\"`]+)",
        r"\1",
        sanitized,
    )
    return sanitized


def _extract_amrfinder_error_text(stdout="", stderr=""):
    """Extract the core error message from AMRFinderPlus stdout/stderr."""
    for stream in (stderr or "", stdout or ""):
        if "*** ERROR ***" in stream:
            after = stream.split("*** ERROR ***", 1)[1].strip()
            if after:
                return after

    meaningful_lines = []
    for stream in (stderr or "", stdout or ""):
        for raw_line in stream.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(line.startswith(prefix) for prefix in _AMRFINDER_BOILERPLATE_PREFIXES):
                continue
            meaningful_lines.append(line)
        if meaningful_lines:
            break
    return "\n".join(meaningful_lines)


def _format_summary(title, explanation, suggestion):
    """Build a structured error summary dict."""
    return {
        "title": title,
        "explanation": explanation,
        "suggestion": suggestion,
    }


def _inspect_input_file(file_path, expected_role, display_name=None):
    """Inspect an uploaded input file after failure to detect format or content mismatches."""
    if not file_path:
        return None
    try:
        if not os.path.isfile(file_path):
            return None
        with open(file_path, "rb") as f:
            raw = f.read(65536)
    except Exception:
        return None

    if not isinstance(raw, (bytes, bytearray)):
        return None

    label = display_name or os.path.basename(file_path)
    slot_label = {
        "nucleotide": "Assembled nucleotide FASTA",
        "protein": "Protein FASTA",
        "gff": "GFF annotation file",
    }.get(expected_role, expected_role)

    if len(raw) == 0:
        return _format_summary(
            "Empty input file",
            f"The {slot_label} file '{label}' is empty (0 bytes).",
            "Verify that your uploaded file is not blank and contains valid sequences before resubmitting.",
        )

    if raw[:2] == b"\x1f\x8b":
        return _format_summary(
            "Compressed file in unexpected format",
            f"The {slot_label} file '{label}' appears to be gzip-compressed, which could not be read directly as plain text (for example, it may be missing a .gz extension or be double-compressed).",
            "Upload an uncompressed plain-text FASTA/GFF file, or a single-compressed file with a .gz extension.",
        )

    if b"\x00" in raw or raw[:4] == b"%PDF" or raw[:2] == b"PK":
        return _format_summary(
            "Binary file instead of plain-text FASTA/GFF",
            f"The {slot_label} file '{label}' appears to be a binary file or archive rather than plain-text FASTA/GFF data.",
            "Export or convert your sequences to plain-text FASTA (or GFF3 for annotations) before uploading.",
        )

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    first_non_empty = next((ln.strip() for ln in lines if ln.strip()), "")
    if not first_non_empty:
        return _format_summary(
            "Empty input file",
            f"The {slot_label} file '{label}' contains only whitespace and no sequence or annotation data.",
            "Verify that your uploaded file is not blank and contains valid data before resubmitting.",
        )

    if expected_role in ("nucleotide", "protein"):
        if first_non_empty.startswith("@"):
            return _format_summary(
                "Invalid FASTA format (detected FASTQ)",
                f"The {slot_label} file '{label}' starts with '@', which indicates FASTQ raw sequencing reads rather than a FASTA file.",
                "Assemble your raw reads into contigs (FASTA format starting with '>') before submitting to AMRFinderPlus.",
            )
        if first_non_empty.startswith("LOCUS"):
            return _format_summary(
                "Invalid FASTA format (detected GenBank)",
                f"The {slot_label} file '{label}' appears to be in GenBank format (starts with 'LOCUS') instead of FASTA format.",
                "Convert or export the sequences to FASTA format (header lines starting with '>') and resubmit.",
            )
        if first_non_empty.startswith("<"):
            return _format_summary(
                "Invalid FASTA format (detected HTML/XML)",
                f"The {slot_label} file '{label}' starts with '<', which indicates an HTML or XML document instead of a FASTA sequence file.",
                "Check that you downloaded the raw sequence file rather than a web page, then resubmit.",
            )
        if first_non_empty.lower().startswith("##gff"):
            return _format_summary(
                "Wrong file type in FASTA field (detected GFF)",
                f"The {slot_label} file '{label}' starts with '##gff', which is a GFF annotation file rather than a FASTA sequence file.",
                "Upload GFF annotation files in the 'GFF file' field and provide a FASTA file here.",
            )
        if not first_non_empty.startswith(">"):
            return _format_summary(
                "Invalid FASTA format (missing '>' header)",
                f"The {slot_label} file '{label}' does not start with a '>' FASTA header line.",
                "Ensure the file is in valid FASTA format with each sequence preceded by a '>' header line.",
            )

        seq_chars = []
        for ln in lines:
            s = ln.strip()
            if not s or s.startswith(">"):
                continue
            seq_chars.append(s)
        joined_seq = "".join(seq_chars)
        if not joined_seq:
            return _format_summary(
                "Empty FASTA sequence",
                f"The {slot_label} file '{label}' has FASTA header line(s) starting with '>', but contains no sequence data.",
                "Verify that your FASTA file includes nucleotide or amino acid sequences under each '>' header.",
            )

        residues = [c for c in joined_seq if not c.isspace()]
        if expected_role == "nucleotide" and len(residues) >= 10:
            aa_only_count = sum(1 for c in residues if c in _AA_ONLY_CHARS)
            if aa_only_count / len(residues) >= 0.05:
                return _format_summary(
                    "Protein sequence uploaded in nucleotide field",
                    f"The {slot_label} file '{label}' contains amino acid residues (such as E, F, I, L, P, Q, or *) and appears to be a protein FASTA rather than a nucleotide FASTA.",
                    "Upload protein sequences under 'Protein FASTA' instead of 'Assembled nucleotide FASTA'.",
                )
        elif expected_role == "protein" and len(residues) >= 50:
            acgtn_count = sum(1 for c in residues if c in _ACGTN_CHARS)
            if acgtn_count / len(residues) >= 0.95:
                return _format_summary(
                    "Nucleotide sequence uploaded in protein field",
                    f"The {slot_label} file '{label}' consists almost entirely of A, C, G, T, and N characters and appears to be a nucleotide FASTA rather than amino acid sequences.",
                    "Upload DNA/contig sequences under 'Assembled nucleotide FASTA' instead of 'Protein FASTA'.",
                )

    elif expected_role == "gff":
        if first_non_empty.startswith(">"):
            return _format_summary(
                "Wrong file type in GFF field (detected FASTA)",
                f"The {slot_label} '{label}' starts with '>', which indicates a FASTA sequence file rather than a GFF3 annotation file.",
                "Upload a GFF3 annotation file here and place FASTA files in the nucleotide or protein fields.",
            )
        if first_non_empty.startswith("@"):
            return _format_summary(
                "Wrong file type in GFF field (detected FASTQ)",
                f"The {slot_label} '{label}' starts with '@', which indicates FASTQ raw reads rather than a GFF3 annotation file.",
                "Upload a valid GFF3 annotation file here.",
            )
        if first_non_empty.startswith("LOCUS"):
            return _format_summary(
                "Wrong file type in GFF field (detected GenBank)",
                f"The {slot_label} '{label}' starts with 'LOCUS', which indicates a GenBank file rather than a GFF3 annotation file.",
                "Convert your annotations to GFF3 format before uploading.",
            )
        if first_non_empty.startswith("<"):
            return _format_summary(
                "Wrong file type in GFF field (detected HTML/XML)",
                f"The {slot_label} '{label}' starts with '<', which indicates an HTML or XML document rather than a GFF3 annotation file.",
                "Check that you downloaded the raw GFF3 file rather than a web page, then resubmit.",
            )

        complete_lines = lines[:-1] if len(raw) == 65536 else lines
        has_embedded_fasta = any(ln.strip().upper() == "##FASTA" for ln in complete_lines)
        if has_embedded_fasta:
            return _format_summary(
                "GFF file contains embedded FASTA sequences",
                f"The {slot_label} '{label}' includes an embedded '##FASTA' sequence section, which AMRFinderPlus cannot parse as GFF3 annotation rows.",
                "Remove the '##FASTA' section and everything after it from your GFF file (for example, Prokka GFF files often append contigs at the end), and verify that 'Annotation format' matches your annotation tool.",
            )

        for ln in complete_lines:
            stripped = ln.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if len(ln.split("\t")) != 9:
                return _format_summary(
                    "Invalid GFF3 annotation file format",
                    f"The {slot_label} '{label}' contains feature lines that do not have 9 tab-separated GFF3 columns.",
                    "Provide a valid tab-delimited GFF3 annotation file (9 columns per feature line) without trailing '##FASTA' sequences, and ensure 'Annotation format' matches your annotation pipeline.",
                )

    return None


def _gff_contains_embedded_fasta(gff_input):
    """Check an entire GFF file on disk for an embedded ##FASTA directive."""
    if not gff_input:
        return False
    try:
        if not os.path.isfile(gff_input):
            return False
        with open(gff_input, "rb") as f:
            for line in f:
                if line.strip().upper() == b"##FASTA":
                    return True
    except Exception:
        return False
    return False


def diagnose_amrfinder_failure(
    stdout="",
    stderr="",
    returncode=1,
    nuc_input=None,
    prot_input=None,
    gff_input=None,
    params=None,
    filenames=None,
):
    """Classify an AMRFinderPlus failure into a clear title, explanation, and suggestion."""
    params = params or {}
    filenames = filenames or {}
    path_map = {}
    for role, local_path in (("nuc", nuc_input), ("prot", prot_input), ("gff", gff_input)):
        if local_path:
            path_map[local_path] = filenames.get(role) or os.path.basename(local_path)

    raw_combined = f"{stdout or ''}\n{stderr or ''}"
    sanitized_error = _sanitize_error_paths(
        _extract_amrfinder_error_text(stdout, stderr), path_map
    )
    lower_combined = raw_combined.lower()

    file_checks = []
    for role, path, fn_key in (
        ("nucleotide", nuc_input, "nuc"),
        ("protein", prot_input, "prot"),
        ("gff", gff_input, "gff"),
    ):
        if path:
            diag = _inspect_input_file(path, role, display_name=filenames.get(fn_key))
            if diag is not None:
                file_checks.append(diag)

    if "empty file" in lower_combined:
        for diag in file_checks:
            if "Empty" in diag["title"]:
                return diag
        return _format_summary(
            "Empty input file",
            "AMRFinderPlus reported that an uploaded input file is empty or contains no sequences.",
            "Verify that your uploaded file is not blank and contains valid sequences before resubmitting.",
        )

    if "fasta should start with '>'" in lower_combined or "no sequences found" in lower_combined:
        if file_checks:
            return file_checks[0]
        return _format_summary(
            "Invalid FASTA format",
            "AMRFinderPlus could not read the input FASTA file because it does not start with a '>' FASTA header line.",
            "Ensure your nucleotide or protein input is in uncompressed or gzip-compressed FASTA format (not FASTQ, GenBank, or GFF).",
        )

    if "protein sequences looks like a nucleotide sequences" in lower_combined:
        return _format_summary(
            "Incorrect sequence type for input field",
            "Nucleotide DNA sequences appear to have been uploaded in the Protein FASTA field.",
            "Upload DNA/contig sequences under 'Assembled nucleotide FASTA' and amino acid sequences under 'Protein FASTA'.",
        )

    if (
        "wrong nucleotide character" in lower_combined
        or "wrong amino acid character" in lower_combined
        or "invalid character" in lower_combined
    ):
        for diag in file_checks:
            return diag
        detail = sanitized_error or "An unexpected sequence character was encountered."
        return _format_summary(
            "Invalid character in FASTA sequence",
            f"AMRFinderPlus rejected a sequence character ({detail}).",
            "Check that nucleotide files contain only standard IUPAC DNA bases and protein files contain only standard amino acid letters, and verify you did not upload a protein file in the nucleotide field (or vice versa).",
        )

    if (
        "is not in the .gff-file" in lower_combined
        or "gff_check.cpp" in lower_combined
        or "not found in gff" in lower_combined
        or "locus tag" in lower_combined
        or "locus_tag" in lower_combined
    ):
        annotation_format = params.get("annotation_format") or "standard"
        detail = sanitized_error or "Protein or sequence IDs did not match the GFF file."
        return _format_summary(
            "Protein or sequence ID mismatch between GFF and FASTA",
            f"AMRFinderPlus could not match identifiers between your GFF and FASTA files ({detail}). Current annotation format: '{annotation_format}'.",
            f"Make sure the GFF file and FASTA file(s) came from the same genome annotation run, and verify that the 'Annotation format' setting (currently '{annotation_format}') matches the tool that generated your files (for example: prokka, bakta, pgap, or standard).",
        )

    if (
        "9 fields are expected" in lower_combined
        or "at least 5 fields are expected" in lower_combined
        or "gff.cpp" in lower_combined
    ):
        for diag in file_checks:
            return diag
        if _gff_contains_embedded_fasta(gff_input):
            gff_label = filenames.get("gff") or (os.path.basename(gff_input) if gff_input else "GFF file")
            return _format_summary(
                "GFF file contains embedded FASTA sequences",
                f"The GFF annotation file '{gff_label}' includes an embedded '##FASTA' sequence section, which AMRFinderPlus cannot parse as GFF3 annotation rows.",
                "Remove the '##FASTA' section and everything after it from your GFF file (for example, Prokka GFF files often append contigs at the end), and verify that 'Annotation format' matches your annotation tool.",
            )
        detail = sanitized_error or "Malformed GFF3 line."
        return _format_summary(
            "Invalid GFF3 annotation file format",
            f"AMRFinderPlus could not parse the GFF annotation file ({detail}). GFF3 requires 9 tab-separated columns per feature line.",
            "Provide a valid tab-delimited GFF3 annotation file (9 columns per feature line) without trailing '##FASTA' sequences, and ensure the 'Annotation format' matches your annotation pipeline.",
        )

    if (
        "empty identifier" in lower_combined
        or "duplicate" in lower_combined
        or ("identifier" in lower_combined and "fasta_check" in lower_combined)
    ):
        detail = sanitized_error or "Invalid or duplicate FASTA header identifier."
        return _format_summary(
            "Invalid or duplicate FASTA sequence identifier",
            f"AMRFinderPlus found a problem with a FASTA header identifier ({detail}).",
            "Ensure every '>' header line in your FASTA file starts with a unique, non-empty sequence ID without illegal characters.",
        )

    if returncode is not None and returncode < 0:
        return _format_summary(
            "AMRFinderPlus process terminated unexpectedly",
            f"The analysis process was terminated by the system (exit code {returncode}), which typically happens when an input file exceeds available memory.",
            "Try splitting very large inputs or removing unneeded files and resubmit." + _FALLBACK_BUG_REPORT_SUFFIX,
        )

    if file_checks:
        return file_checks[0]

    if "*** ERROR ***" in raw_combined and sanitized_error:
        return _format_summary(
            "AMRFinderPlus reported an error",
            sanitized_error,
            "Review the error message and input files, or check the Run log for full details." + _FALLBACK_BUG_REPORT_SUFFIX,
        )

    fallback_line = ""
    if sanitized_error:
        fallback_line = sanitized_error.splitlines()[0].strip()
    explanation = (
        fallback_line
        if fallback_line
        else f"AMRFinderPlus exited with non-zero status code {returncode}."
    )
    return _format_summary(
        "AMRFinderPlus execution failed",
        explanation,
        "Check your input files and parameters, and review the raw output below or the Run log for details." + _FALLBACK_BUG_REPORT_SUFFIX,
    )


def run_amrfinder(
    *,
    nuc_input,
    prot_input,
    gff_input,
    output_tsv,
    stderr_path,
    nucleotide_path,
    protein_path,
    params,
    job_name=None,
    filenames=None,
):
    """Build and execute the amrfinder command."""
    cmd = ["amrfinder"]

    if nuc_input:
        cmd.extend(["--nucleotide", nuc_input])
    if prot_input:
        cmd.extend(["--protein", prot_input])
    if gff_input:
        cmd.extend(["--gff", gff_input])

    cmd.extend(["--output", output_tsv])

    name = job_name or params.get("job_name") or params.get("name")
    if name:
        cmd.extend(["--name", str(name)])

    if nuc_input and params.get("has_nucleotide"):
        cmd.extend(["--nucleotide_output", nucleotide_path])
    if prot_input and params.get("has_protein"):
        cmd.extend(["--protein_output", protein_path])

    if params.get("plus_flag"):
        cmd.append("--plus")

    if params.get("print_node"):
        cmd.append("--print_node")

    organism = params.get("organism")
    if organism:
        cmd.extend(["-O", organism])

    ident_min = params.get("ident_min")
    if ident_min is not None:
        cmd.extend(["-i", str(ident_min)])

    coverage_min = params.get("coverage_min")
    if coverage_min is not None:
        cmd.extend(["-c", str(coverage_min)])

    annotation_format = params.get("annotation_format")
    if annotation_format:
        cmd.extend(["--annotation_format", str(annotation_format)])

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    # Always save stdout and stderr to a file so it can be uploaded and reviewed
    with open(stderr_path, "w") as f:
        f.write("=== AMRFinderPlus Log ===\n")
        f.write("--- Standard Output ---\n")
        if result.stdout:
            f.write(result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
        f.write("--- Standard Error ---\n")
        if result.stderr:
            f.write(result.stderr if result.stderr.endswith("\n") else result.stderr + "\n")

    if result.returncode != 0:
        stdout_part = result.stdout or ""
        stderr_part = result.stderr or ""
        if stdout_part and not stdout_part.endswith("\n"):
            stdout_part += "\n"
        if stderr_part and not stderr_part.endswith("\n"):
            stderr_part += "\n"
        summary = diagnose_amrfinder_failure(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            returncode=result.returncode,
            nuc_input=nuc_input,
            prot_input=prot_input,
            gff_input=gff_input,
            params=params,
            filenames=filenames,
        )
        error_details = (
            f"AMRFinderPlus failed: {summary['title']}\n"
            f"Cause: {summary['explanation']}\n"
            f"Suggestion: {summary['suggestion']}\n\n"
            "--- Standard Output ---\n"
            f"{stdout_part}"
            "--- Standard Error ---\n"
            f"{stderr_part}"
        ).rstrip("\n")
        exc = Exception(error_details)
        exc.error_summary = summary
        raise exc

    return AMRResult(result.stdout, result.stderr)


def run_amrrules(
    *,
    amrfp_output_tsv,
    amrrules_organism,
    output_prefix,
    sample_id=None,
    job_id=None,
    stderr_path=None,
    amrfp_stdout="",
    amrfp_stderr="",
):
    """Build and execute the amrrules command on AMRFinderPlus output."""
    sid = sample_id or job_id
    cmd = [
        "amrrules",
        "--input", amrfp_output_tsv,
        "--output-prefix", output_prefix,
        "--organism", amrrules_organism,
        "--sample-id", str(sid),
    ]

    cmd_str = shlex.join(cmd)
    print(f"Executing AMRrules: {cmd_str}")

    if stderr_path and os.path.exists(stderr_path):
        try:
            with open(stderr_path, "a") as f:
                f.write(f"\n\n=== AMRrules Log ===\nCommand: {cmd_str}\n\n")
        except Exception as log_err:
            print(f"Failed to append AMRrules command to stderr_path: {log_err}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if stderr_path and os.path.exists(stderr_path):
        try:
            with open(stderr_path, "a") as f:
                f.write("--- Standard Output ---\n")
                if result.stdout:
                    f.write(result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
                f.write("--- Standard Error ---\n")
                if result.stderr:
                    f.write(result.stderr if result.stderr.endswith("\n") else result.stderr + "\n")
        except Exception as log_err:
            print(f"Failed to append AMRrules log to stderr_path: {log_err}")

    if result.returncode != 0:
        af_out = amrfp_stdout or ""
        af_err = amrfp_stderr or ""
        if af_out and not af_out.endswith("\n"):
            af_out += "\n"
        if af_err and not af_err.endswith("\n"):
            af_err += "\n"

        ar_out = result.stdout or ""
        ar_err = result.stderr or ""
        if ar_out and not ar_out.endswith("\n"):
            ar_out += "\n"
        if ar_err and not ar_err.endswith("\n"):
            ar_err += "\n"

        err_msg = (
            "=== AMRFinderPlus Output ===\n"
            "--- Standard Output ---\n"
            f"{af_out}"
            "--- Standard Error ---\n"
            f"{af_err}\n"
            "=== AMRrules Output ===\n"
            f"Command: {cmd_str}\n"
            "--- Standard Output ---\n"
            f"{ar_out}"
            "--- Standard Error ---\n"
            f"{ar_err}"
        ).rstrip("\n")
        raise Exception(err_msg)

    return result.stdout


def _validate_payload(envelope):
    """
    Parse and validate a Pub/Sub push envelope.

    Returns (payload_dict, None) on success,
    or (None, flask_response_tuple) on failure.

    Returning a 200 response on most errors is intentional: it tells Pub/Sub
    to ACK (not retry) messages that are permanently malformed.
    """
    if not envelope or "message" not in envelope:
        print(f"Invalid Pub/Sub envelope: {envelope}")
        return None, (jsonify({"error": f"Invalid Pub/Sub envelope (Worker v{APP_VERSION})"}), 400)

    # Decode the base64-encoded payload
    raw = envelope["message"].get("data", "")
    try:
        payload = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as e:
        print(f"Failed to decode message data: {e}, raw={raw[:200]}")
        # Ack the message (200) so Pub/Sub stops retrying a broken message
        return None, (jsonify({"error": "Could not decode message"}), 200)

    print(f"Decoded payload: {payload}")

    # Validate that the decoded payload is a JSON object (dict), not an array or scalar
    if not isinstance(payload, dict):
        print(f"Malformed message - payload is not a JSON object. Type: {type(payload).__name__}")
        return None, (jsonify({"error": "Malformed message, payload must be a JSON object"}), 200)

    if "job_id" not in payload or "gcs_uri" not in payload:
        print(f"Malformed message - missing job_id or gcs_uri. Payload: {payload}")
        # Ack the message (200) so Pub/Sub stops retrying it
        return None, (jsonify({"error": "Malformed message, missing required fields"}), 200)

    job_id = payload["job_id"]
    gcs_uri = payload["gcs_uri"]

    # Validate that job_id and gcs_uri are non-empty strings
    if not isinstance(job_id, str):
        print(f"Malformed message - job_id must be a string. Got: {job_id!r}")
        return None, (jsonify({"error": "Malformed message, job_id must be a string"}), 200)

    if not job_id:
        print(f"Malformed message - job_id must not be empty.")
        return None, (jsonify({"error": "Malformed message, job_id must be a non-empty string"}), 200)

    if not re.fullmatch(r"[a-zA-Z0-9-]+", job_id):
        print(f"Malformed message - job_id contains invalid characters. Got: {job_id!r}")
        return None, (jsonify({"error": "Malformed message, job_id must be alphanumeric and hyphens only"}), 200)

    if not isinstance(gcs_uri, str) or not gcs_uri.startswith("gs://"):
        print(f"Malformed message - gcs_uri must start with 'gs://'. Got: {gcs_uri!r}")
        return None, (jsonify({"error": "Malformed message, gcs_uri must start with gs://"}), 200)

    job_name = payload.get("job_name")
    if job_name is not None:
        if not isinstance(job_name, str):
            print(f"Malformed message - job_name must be a string. Got: {job_name!r}")
            return None, (jsonify({"error": "Malformed message, job_name must be a string"}), 200)
        clean_job_name = job_name.strip()
        if len(clean_job_name) > 100 or (clean_job_name and not re.fullmatch(r"[A-Za-z0-9 _-]+", clean_job_name)):
            print(f"Malformed message - job_name contains invalid characters or exceeds 100 characters. Got: {job_name!r}")
            return None, (jsonify({"error": "Malformed message, job_name contains invalid characters or exceeds 100 characters"}), 200)

    # Ensure parameters is a dict; fall back to empty dict if malformed
    params = payload.get("parameters", {})
    if not isinstance(params, dict):
        print(f"Malformed message - parameters must be a JSON object. Type: {type(params).__name__}. Defaulting to empty.")
        params = {}

    payload["parameters"] = params
    return payload, None


@app.route("/", methods=["POST"])
def handle_pubsub_push():
    """
    Receives a Pub/Sub push message and runs AMRFinderPlus.

    Pub/Sub wraps the original message in an envelope like:
      {
        "message": {
          "data": "<base64-encoded JSON payload>",
          "messageId": "...",
        },
        "subscription": "projects/.../subscriptions/..."
      }

    Returning HTTP 200 tells Pub/Sub the message was successfully processed
    and should not be redelivered. Returning 200 even on AMRFinderPlus failure
    is intentional -- the error is recorded in Firestore instead.
    """
    payload, error = _validate_payload(request.get_json(silent=True))
    if error:
        return error

    job_id = payload["job_id"]
    gcs_uri = payload["gcs_uri"]
    params = payload["parameters"]
    nuc_filename = payload.get("nuc_filename")
    prot_filename = payload.get("prot_filename")
    gff_filename = payload.get("gff_filename")

    base_gcs_uri = gcs_uri.rsplit("/", 1)[0]

    _log(job_id, "Received job. Fetching Firestore document...")
    db = get_firestore_client()
    doc_ref = db.collection("amr_jobs").document(job_id)

    job_dir = f"/tmp/{job_id}"
    inputs_dir = os.path.join(job_dir, "inputs")
    outputs_dir = os.path.join(job_dir, "outputs")
    os.makedirs(inputs_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)

    local_nuc_input = None
    local_prot_input = None
    local_gff_input = None

    local_output = os.path.join(outputs_dir, "amrfinder.tsv")
    local_stderr = os.path.join(outputs_dir, "stderr.txt")
    local_nuc = os.path.join(outputs_dir, "nucleotide.fna")
    local_prot = os.path.join(outputs_dir, "protein.faa")

    try:
        _log(job_id, "Updating status to Processing...")
        doc_ref.update({"status": "Processing"})

        def _decompress_if_gzipped(local_path, display_name=None):
            if local_path and local_path.endswith(".gz"):
                uncompressed = local_path[:-3]
                label = display_name or os.path.basename(local_path)
                try:
                    with gzip.open(local_path, "rb") as f_in:
                        with open(uncompressed, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                except (gzip.BadGzipFile, EOFError, OSError) as gz_err:
                    summary = _format_summary(
                        "Corrupt or invalid .gz archive",
                        (
                            f"The uploaded file '{label}' has a .gz extension, "
                            "but could not be decompressed as a valid gzip archive."
                        ),
                        (
                            "Verify that the .gz file is not truncated or corrupted, "
                            "or upload the uncompressed plain-text file instead."
                        ),
                    )
                    exc = ValueError(
                        f"Input file decompression failed: {summary['title']}\n"
                        f"Cause: {summary['explanation']}\n"
                        f"Suggestion: {summary['suggestion']}\n"
                        f"Details: {gz_err}"
                    )
                    exc.error_summary = summary
                    raise exc from gz_err
                return uncompressed
            return local_path

        # Download each named input file (nucleotide, protein, GFF) if provided
        for attr, filename in [("nuc", nuc_filename), ("prot", prot_filename), ("gff", gff_filename)]:
            if not filename:
                continue
            safe_filename = secure_filename(filename)
            local_path = os.path.join(inputs_dir, safe_filename)
            download_blob(f"{base_gcs_uri}/{filename}", local_path)
            decompressed_path = _decompress_if_gzipped(local_path, display_name=filename)
            if attr == "nuc":
                local_nuc_input = decompressed_path
            elif attr == "prot":
                local_prot_input = decompressed_path
            elif attr == "gff":
                local_gff_input = decompressed_path

        # Legacy fallback: older Pub/Sub messages send a single gcs_uri
        # without separate nuc_filename/prot_filename fields.
        # Determine input type from the has_protein/has_nucleotide params.
        if not local_nuc_input and not local_prot_input:
            if params.get("has_protein") and not params.get("has_nucleotide"):
                local_prot_input = os.path.join(inputs_dir, "input.fasta")
                download_blob(gcs_uri, local_prot_input)
                local_prot_input = _decompress_if_gzipped(local_prot_input)
            else:
                local_nuc_input = os.path.join(inputs_dir, "input.fasta")
                download_blob(gcs_uri, local_nuc_input)
                local_nuc_input = _decompress_if_gzipped(local_nuc_input)

        job_name = payload.get("job_name")
        clean_job_name = None
        if job_name and isinstance(job_name, str):
            clean = job_name.strip()
            if clean and len(clean) <= 100 and re.fullmatch(r"[A-Za-z0-9 _-]+", clean):
                clean_job_name = clean

        amrfp_res = run_amrfinder(
            nuc_input=local_nuc_input,
            prot_input=local_prot_input,
            gff_input=local_gff_input,
            output_tsv=local_output,
            stderr_path=local_stderr,
            nucleotide_path=local_nuc,
            protein_path=local_prot,
            params=params,
            job_name=clean_job_name,
            filenames={"nuc": nuc_filename, "prot": prot_filename, "gff": gff_filename},
        )

        upload_blob(local_output, f"results/{job_id}/results.tsv")

        if os.path.exists(local_nuc):
            upload_blob(local_nuc, f"results/{job_id}/nucleotide.fna")
        if os.path.exists(local_prot):
            upload_blob(local_prot, f"results/{job_id}/protein.faa")

        amrrules_organism = params.get("amrrules_organism")
        amrrules_error_msg = None
        if amrrules_organism:
            amrrules_prefix = os.path.join(outputs_dir, "amrrules")
            local_genome_summary = f"{amrrules_prefix}_genome_summary.tsv"
            local_summary = f"{amrrules_prefix}_summary.tsv"
            local_interpreted = f"{amrrules_prefix}_interpreted.tsv"
            try:
                sample_id = clean_job_name if clean_job_name else job_id

                _log(job_id, f"Running AMRrules for organism '{amrrules_organism}' with sample ID '{sample_id}'...")
                af_stdout = getattr(amrfp_res, "stdout", "") or (amrfp_res if isinstance(amrfp_res, str) else "")
                af_stderr = getattr(amrfp_res, "stderr", "")
                run_amrrules(
                    amrfp_output_tsv=local_output,
                    amrrules_organism=amrrules_organism,
                    output_prefix=amrrules_prefix,
                    sample_id=sample_id,
                    stderr_path=local_stderr,
                    amrfp_stdout=af_stdout,
                    amrfp_stderr=af_stderr,
                )
                if os.path.exists(local_genome_summary):
                    upload_blob(local_genome_summary, f"results/{job_id}/amrrules_genome_summary.tsv")
                    upload_blob(local_genome_summary, f"results/{job_id}/amrrules_summary.tsv")
                elif os.path.exists(local_summary):
                    upload_blob(local_summary, f"results/{job_id}/amrrules_genome_summary.tsv")
                    upload_blob(local_summary, f"results/{job_id}/amrrules_summary.tsv")
                if os.path.exists(local_interpreted):
                    upload_blob(local_interpreted, f"results/{job_id}/amrrules_interpreted.tsv")
            except Exception as amr_err:
                _log(job_id, f"AMRrules soft failure: {amr_err}")
                amrrules_error_msg = str(amr_err)

        if os.path.exists(local_stderr):
            upload_blob(local_stderr, f"results/{job_id}/stderr.txt")

        # Mark job as completed
        versions = get_installed_versions()
        update_data = {
            "status": "Completed",
            "result_uri": f"gs://{OUTPUT_BUCKET}/results/{job_id}/results.tsv",
            "worker_version": APP_VERSION,
            "software_version": versions.get("software_version"),
            "database_version": versions.get("database_version"),
            "amrrules_version": versions.get("amrrules_version"),
            "error_message": None,
            "error_summary": None,
            "amrrules_error": amrrules_error_msg,
        }

        doc_ref.update(update_data)
        _log(job_id, "Successfully processed and updated Firestore.")

    except Exception as e:
        _log(job_id, f"Job failed: {e}")
        # Upload stderr even on failure if the file was written
        stderr_uri = None
        if os.path.exists(local_stderr):
            try:
                stderr_uri = upload_blob(local_stderr, f"results/{job_id}/stderr.txt")
            except Exception as upload_err:
                _log(job_id, f"Failed to upload stderr: {upload_err}")

        error_summary = getattr(e, "error_summary", None)
        try:
            doc_ref.update({
                "status": "Failed",
                "error_message": str(e),
                "error_summary": error_summary,
                "stderr_uri": stderr_uri,
            })
        except Exception as db_err:
            _log(job_id, f"Failed to update Firestore with error status: {db_err}")

    finally:
        if os.path.exists(job_dir):
            try:
                shutil.rmtree(job_dir)
            except Exception as cleanup_err:
                print(f"Failed to remove {job_dir}: {cleanup_err}")

    # Always return 200 so Pub/Sub acks the message.
    return jsonify({"job_id": job_id}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
