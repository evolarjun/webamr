#!/usr/bin/env python3
"""
retrigger_job.py — Manually re-queue a failed job by re-publishing its payload to Pub/Sub.

Usage:
    source set_variables.sh
    python retrigger_job.py <job_id>
"""

import os
import sys
import json
from google.cloud import storage, firestore, pubsub_v1


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        raise SystemExit(1)
    return value


def main():
    if len(sys.argv) != 2:
        print("Usage: python retrigger_job.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    project_id = get_required_env("PROJECT_ID")
    input_bucket_name = get_required_env("BUCKET_NAME")
    topic_id = get_required_env("TOPIC_ID")

    db = firestore.Client(project=project_id)
    gcs = storage.Client(project=project_id)
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_id)

    print(f"--- Retriggering Job: {job_id} ---")

    # 1. Fetch metadata from Firestore
    doc_ref = db.collection("amr_jobs").document(job_id)
    doc = doc_ref.get()
    if not doc.exists:
        print(f"Error: Job {job_id} not found in Firestore.")
        sys.exit(1)

    job_data = doc.to_dict()
    params = job_data.get("parameters", {})
    gcs_uri = job_data.get("gcs_uri")
    job_name = job_data.get("job_name")

    # 2. Verify GCS files existence
    input_bucket = gcs.bucket(input_bucket_name)
    required_files = []
    
    if job_data.get("nuc_filename"):
        required_files.append(job_data["nuc_filename"])
    if job_data.get("prot_filename"):
        required_files.append(job_data["prot_filename"])
    if job_data.get("gff_filename"):
        required_files.append(job_data["gff_filename"])

    if not required_files:
        print("Warning: No input filenames found in Firestore record. Using gcs_uri for validation.")
        # Fallback to checking the filename in the gcs_uri
        parts = gcs_uri.replace("gs://", "").split("/", 1)
        if len(parts) == 2:
            required_files.append(parts[1].split("/")[-1])

    missing_files = []
    for filename in required_files:
        blob_path = f"{job_id}/{filename}"
        blob = input_bucket.blob(blob_path)
        if not blob.exists():
            missing_files.append(f"gs://{input_bucket_name}/{blob_path}")

    if missing_files:
        print("\nERROR: Cannot retrigger job. The following required input files are missing from GCS:")
        for missing in missing_files:
            print(f"  - {missing}")
        print("\nNote: Files may have been deleted by a GCS lifecycle rule (10 days).")
        sys.exit(1)

    print("Success: All required input files found in GCS.")

    # 3. Reset Firestore status
    print("Resetting Firestore status to 'Queued'...")
    doc_ref.update({
        "status": "Queued",
        "error_message": None,
        "stderr_uri": None
    })

    # 4. Publish to Pub/Sub
    message_data = {
        "job_id": job_id,
        "gcs_uri": gcs_uri,
        "parameters": params,
        "job_name": job_name
    }
    
    print(f"Publishing to {topic_path}...")
    data_str = json.dumps(message_data).encode("utf-8")
    future = publisher.publish(topic_path, data_str)
    message_id = future.result()
    
    print(f"Done! Job re-triggered successfully. Pub/Sub Message ID: {message_id}")


if __name__ == "__main__":
    main()
