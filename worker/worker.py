"""
AMRFinderPlus Worker — Pub/Sub Push Handler
============================================
Runs as a Cloud Run service. Pub/Sub is configured to PUSH each job message
as an HTTP POST to this service's root URL. Cloud Run spins up an instance on
demand per job, runs AMRFinderPlus, then scales back to zero.

No streaming pull loop is needed; Pub/Sub handles delivery and retries.
"""
import os
import json
import base64
import subprocess
from flask import Flask, request, jsonify
from google.cloud import storage, firestore

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "my-gcp-project")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "amr-output-bucket")

storage_client = storage.Client(project=PROJECT_ID)
db = firestore.Client(project=PROJECT_ID)


def upload_db_version():
    """Uploads the local AMRFinder database version to GCS configuration path."""
    version_file = "/etc/amrfinder_db_version.txt"
    if os.path.exists(version_file):
        print(f"Uploading AMRFinder DB version to gs://{OUTPUT_BUCKET}/config/database_version.txt")
        bucket = storage_client.bucket(OUTPUT_BUCKET)
        blob = bucket.blob("config/database_version.txt")
        blob.upload_from_filename(version_file)
    else:
        print("WARNING: Could not find /etc/amrfinder_db_version.txt!")

# Upload config once on container cold-start
try:
    upload_db_version()
except Exception as e:
    print(f"Failed to upload DB version on startup: {e}")


def download_blob(gcs_uri, local_path):
    """Download a file from GCS given a gs:// URI."""
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(local_path)


def upload_blob(local_path, destination_blob_name):
    """Upload a local file to the output GCS bucket."""
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{OUTPUT_BUCKET}/{destination_blob_name}"


def run_amrfinder(input_fasta, output_tsv, params):
    """Build and execute the amrfinder command."""
    cmd = ["amrfinder", "-n", input_fasta, "-o", output_tsv]

    if params.get("plus_flag"):
        cmd.append("--plus")

    organism = params.get("organism")
    if organism:
        cmd.extend(["-O", organism])

    ident_min = params.get("ident_min")
    if ident_min is not None:
        cmd.extend(["-i", str(ident_min)])

    coverage_min = params.get("coverage_min")
    if coverage_min is not None:
        cmd.extend(["-c", str(coverage_min)])

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
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
        return jsonify({"error": "Invalid Pub/Sub envelope"}), 400

    # Decode the base64-encoded payload
    raw = envelope["message"].get("data", "")
    payload = json.loads(base64.b64decode(raw).decode("utf-8"))

    job_id = payload["job_id"]
    gcs_uri = payload["gcs_uri"]
    params = payload.get("parameters", {})

    print(f"Received job {job_id}. Processing...")
    doc_ref = db.collection("amr_jobs").document(job_id)
    doc_ref.update({"status": "Processing"})

    local_input = f"/tmp/{job_id}_input.fasta"
    local_output = f"/tmp/{job_id}_output.tsv"

    try:
        download_blob(gcs_uri, local_input)
        run_amrfinder(local_input, local_output, params)

        result_uri = upload_blob(local_output, f"results/{job_id}.tsv")
        doc_ref.update({"status": "Completed", "result_uri": result_uri})
        print(f"Job {job_id} completed successfully.")

    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        doc_ref.update({"status": "Failed", "error_message": str(e)})

    finally:
        if os.path.exists(local_input):
            os.remove(local_input)
        if os.path.exists(local_output):
            os.remove(local_output)

    # Always return 200 so Pub/Sub acks the message.
    return jsonify({"job_id": job_id}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
