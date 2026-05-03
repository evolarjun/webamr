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
import subprocess
import threading
from flask import Flask, request, jsonify
from google.cloud import storage, firestore

try:
    with open('VERSION.txt', 'r') as f:
        APP_VERSION = f.read().strip()
except FileNotFoundError:
    APP_VERSION = "unknown"

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "my-gcp-project")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "amr-output-bucket")

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


def upload_versions():
    """Uploads the local AMRFinder database and software versions to GCS configuration path."""
    storage_client = get_storage_client()
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    
    # Upload Database Version
    version_file = "/etc/amrfinder_db_version.txt"
    if os.path.exists(version_file):
        print(f"Uploading AMRFinder DB version to gs://{OUTPUT_BUCKET}/config/database_version.txt")
        blob = bucket.blob("config/database_version.txt")
        blob.upload_from_filename(version_file)
    else:
        print("WARNING: Could not find /etc/amrfinder_db_version.txt!")

    # Upload Software Version
    try:
        print(f"Uploading AMRFinder software version to gs://{OUTPUT_BUCKET}/config/software_version.txt")
        result = subprocess.run(["amrfinder", "--version"], capture_output=True, text=True, check=True)
        software_version = result.stdout.strip()
        software_blob = bucket.blob("config/software_version.txt")
        software_blob.upload_from_string(software_version)
    except Exception as e:
        print(f"Failed to get/upload software version: {e}")

# Upload config once on container cold-start in a background thread to avoid blocking Gunicorn startup
try:
    threading.Thread(target=upload_versions, daemon=True).start()
    print("Background thread started for version upload.")
except Exception as e:
    print(f"Failed to start version upload thread: {e}")


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


def run_amrfinder(nuc_input, prot_input, gff_input, output_tsv, stderr_path, nucleotide_path, protein_path, params):
    """Build and execute the amrfinder command."""
    cmd = ["amrfinder"]

    if nuc_input:
        cmd.extend(["--nucleotide", nuc_input])
    if prot_input:
        cmd.extend(["--protein", prot_input])
    if gff_input:
        cmd.extend(["--gff", gff_input])

    cmd.extend(["--output", output_tsv])

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

    # Always save stderr to a file so it can be uploaded and reviewed
    with open(stderr_path, "w") as f:
        f.write(result.stderr)

    if result.returncode != 0:
        raise Exception(f"AMRFinderPlus failed: {result.stderr}")

    return result.stdout


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
    is intentional — the error is recorded in Firestore instead.
    """
    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        print(f"Invalid Pub/Sub envelope: {envelope}")
        return jsonify({"error": "Invalid Pub/Sub envelope (Worker v{APP_VERSION})"}), 400

    # Decode the base64-encoded payload
    raw = envelope["message"].get("data", "")
    try:
        payload = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as e:
        print(f"Failed to decode message data: {e}, raw={raw[:200]}")
        # Ack the message (200) so Pub/Sub stops retrying a broken message
        return jsonify({"error": "Could not decode message"}), 200

    print(f"Decoded payload: {payload}")

    # Validate that the decoded payload is a JSON object (dict), not an array or scalar
    if not isinstance(payload, dict):
        print(f"Malformed message — payload is not a JSON object. Type: {type(payload).__name__}")
        return jsonify({"error": "Malformed message, payload must be a JSON object"}), 200

    if "job_id" not in payload or "gcs_uri" not in payload:
        print(f"Malformed message — missing job_id or gcs_uri. Payload: {payload}")
        # Ack the message (200) so Pub/Sub stops retrying it
        return jsonify({"error": "Malformed message, missing required fields"}), 200

    job_id = payload["job_id"]
    gcs_uri = payload["gcs_uri"]
    params = payload.get("parameters", {})

    # Validate that job_id and gcs_uri are non-empty strings
    if not isinstance(job_id, str):
        print(f"Malformed message — job_id must be a string. Got: {job_id!r}")
        return jsonify({"error": "Malformed message, job_id must be a string"}), 200

    if not job_id:
        print(f"Malformed message — job_id must not be empty.")
        return jsonify({"error": "Malformed message, job_id must be a non-empty string"}), 200

    if not re.fullmatch(r"[a-zA-Z0-9-]+", job_id):
        print(f"Malformed message — job_id contains invalid characters. Got: {job_id!r}")
        return jsonify({"error": "Malformed message, job_id must be alphanumeric and hyphens only"}), 200

    if not isinstance(gcs_uri, str) or not gcs_uri.startswith("gs://"):
        print(f"Malformed message — gcs_uri must start with 'gs://'. Got: {gcs_uri!r}")
        return jsonify({"error": "Malformed message, gcs_uri must start with gs://"}), 200

    # Ensure parameters is a dict; fall back to empty dict if malformed
    if not isinstance(params, dict):
        print(f"Malformed message — parameters must be a JSON object. Type: {type(params).__name__}. Defaulting to empty.")
        params = {}

    nuc_filename = payload.get("nuc_filename")
    prot_filename = payload.get("prot_filename")
    gff_filename = payload.get("gff_filename")
    
    base_gcs_uri = gcs_uri.rsplit("/", 1)[0]

    print(f"[{job_id}] Received job. Fetching Firestore document...")
    db = get_firestore_client()
    doc_ref = db.collection("amr_jobs").document(job_id)

    local_nuc_input = None
    local_prot_input = None
    local_gff_input = None

    local_output = f"/tmp/{job_id}_output.tsv"
    local_stderr = f"/tmp/{job_id}_stderr.txt"
    local_nuc = f"/tmp/{job_id}_nucleotide.fna"
    local_prot = f"/tmp/{job_id}_protein.faa"

    cleanup_paths = [local_output, local_stderr, local_nuc, local_prot]

    try:
        print(f"[{job_id}] Updating status to Processing...")
        doc_ref.update({"status": "Processing"})

        if nuc_filename:
            local_nuc_input = f"/tmp/{job_id}_{nuc_filename}"
            download_blob(f"{base_gcs_uri}/{nuc_filename}", local_nuc_input)
            cleanup_paths.append(local_nuc_input)

        if prot_filename:
            local_prot_input = f"/tmp/{job_id}_{prot_filename}"
            download_blob(f"{base_gcs_uri}/{prot_filename}", local_prot_input)
            cleanup_paths.append(local_prot_input)

        if gff_filename:
            local_gff_input = f"/tmp/{job_id}_{gff_filename}"
            download_blob(f"{base_gcs_uri}/{gff_filename}", local_gff_input)
            cleanup_paths.append(local_gff_input)

        if not local_nuc_input and not local_prot_input:
            if params.get("has_protein") and not params.get("has_nucleotide"):
                local_prot_input = f"/tmp/{job_id}_input.fasta"
                download_blob(gcs_uri, local_prot_input)
                cleanup_paths.append(local_prot_input)
            else:
                local_nuc_input = f"/tmp/{job_id}_input.fasta"
                download_blob(gcs_uri, local_nuc_input)
                cleanup_paths.append(local_nuc_input)

        run_amrfinder(local_nuc_input, local_prot_input, local_gff_input, local_output, local_stderr, local_nuc, local_prot, params)

        upload_blob(local_output, f"results/{job_id}/results.tsv")
        upload_blob(local_stderr, f"results/{job_id}/stderr.txt")

        if os.path.exists(local_nuc):
            upload_blob(local_nuc, f"results/{job_id}/nucleotide.fna")
        if os.path.exists(local_prot):
            upload_blob(local_prot, f"results/{job_id}/protein.faa")

        # Mark job as completed
        doc_ref.update({
            "status": "Completed", 
            "result_uri": f"gs://{OUTPUT_BUCKET}/results/{job_id}/results.tsv",
            "worker_version": APP_VERSION
        })
        print(f"[{job_id}] Successfully processed and updated Firestore.")

    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        # Upload stderr even on failure if the file was written
        stderr_uri = None
        if os.path.exists(local_stderr):
            try:
                stderr_uri = upload_blob(local_stderr, f"results/{job_id}/stderr.txt")
            except Exception as upload_err:
                print(f"Failed to upload stderr: {upload_err}")
                
        try:
            doc_ref.update({"status": "Failed", "error_message": str(e), "stderr_uri": stderr_uri})
        except Exception as db_err:
            print(f"Failed to update firestore with error status: {db_err}")

    finally:
        for path in cleanup_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as cleanup_err:
                    print(f"Failed to remove {path}: {cleanup_err}")

    # Always return 200 so Pub/Sub acks the message.
    return jsonify({"job_id": job_id}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
