#!/usr/bin/env python3
"""
debug_job.py — Show Firestore document and GCS files for a given job ID.

Usage:
    source set_variables.sh
    python debug_job.py <job_id>
"""

import os
import sys
from datetime import timezone


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        raise SystemExit(1)
    return value


def main():
    if len(sys.argv) != 2:
        print("Usage: python debug_job.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    project_id = get_required_env("PROJECT_ID")
    input_bucket = get_required_env("BUCKET_NAME")
    output_bucket = get_required_env("OUTPUT_BUCKET")

    # ------------------------------------------------------------------
    # 1. Firestore
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  FIRESTORE  —  amr_jobs/{job_id}")
    print(f"{'='*60}")
    try:
        from google.cloud import firestore
        db = firestore.Client(project=project_id)
        doc = db.collection("amr_jobs").document(job_id).get()
        if doc.exists:
            data = doc.to_dict()
            for key, value in sorted(data.items()):
                # Convert UTC datetimes to local-friendly string
                if hasattr(value, "tzinfo"):
                    value = value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                print(f"  {key:<30} {value}")
        else:
            print("  (no document found)")
    except Exception as e:
        print(f"  ERROR reading Firestore: {e}")

    # ------------------------------------------------------------------
    # 2. GCS buckets
    # ------------------------------------------------------------------
    from google.cloud import storage
    gcs = storage.Client(project=project_id)

    for label, bucket_name, prefix in [
        ("INPUT  BUCKET", input_bucket,  f"{job_id}/"),
        ("OUTPUT BUCKET", output_bucket, f"results/{job_id}"),
    ]:
        print(f"\n{'='*60}")
        print(f"  {label}  —  gs://{bucket_name}/{prefix}")
        print(f"{'='*60}")
        try:
            bucket = gcs.bucket(bucket_name)
            blobs = list(bucket.list_blobs(prefix=prefix))
            if blobs:
                for blob in blobs:
                    size_kb = blob.size / 1024 if blob.size else 0
                    updated = blob.updated.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if blob.updated else "?"
                    print(f"  {blob.name:<55} {size_kb:>8.1f} KB   {updated}")
            else:
                print("  (no files found)")
        except Exception as e:
            print(f"  ERROR reading bucket: {e}")

    print()


if __name__ == "__main__":
    main()
