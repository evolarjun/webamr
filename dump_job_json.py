#!/usr/bin/env python3
"""
dump_job_json.py — Dump a Firestore document for a given job ID as JSON.

Usage:
    source set_variables.sh
    python dump_job_json.py <job_id>
"""

import os
import sys
import json
from datetime import datetime
from google.cloud import firestore

class FirestoreJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Firestore-specific types like datetime."""
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        # Add other type handlers here if needed (e.g., DocumentReference, GeoPoint)
        return super().default(obj)

def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        sys.exit(1)
    return value

def main():
    if len(sys.argv) != 2:
        print("Usage: python dump_job_json.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    project_id = get_required_env("PROJECT_ID")

    try:
        db = firestore.Client(project=project_id)
        doc_ref = db.collection("amr_jobs").document(job_id)
        doc = doc_ref.get()

        if doc.exists:
            data = doc.to_dict()
            # Inject the document ID if not already present in the data
            if "job_id" not in data:
                data["_id"] = doc.id
            
            print(json.dumps(data, indent=2, cls=FirestoreJSONEncoder))
        else:
            print(f"Error: No document found for job_id '{job_id}' in collection 'amr_jobs'.", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
