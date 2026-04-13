#!/usr/bin/env python3 

import os
import sys
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        raise SystemExit(1)
    return value

def check_queue_via_db():
    """
    Since Push subscriptions cannot be directly pulled/peeked via the Pub/Sub API,
    we can query the Firestore database to see exactly which jobs are currently 
    stuck in the backlog (either Queued or endlessly Processing).
    """
    project_id = get_required_env("PROJECT_ID")
    db = firestore.Client(project=project_id)

    print(f"Querying Firestore for active jobs in project: {project_id}...\n")

    try:
        # Check for Queued jobs (in queue, worker hasn't started them yet)
        pending_docs = list(db.collection("amr_jobs").where(filter=FieldFilter("status", "==", "Queued")).stream())
        print(f"--- PENDING JOBS (Waiting in queue) ---")
        if not pending_docs:
             print("None.")
        else:
             for doc in pending_docs:
                  data = doc.to_dict()
                  print(f"Job ID: {doc.id} | Created: {data.get('created_at')} | Size: {data.get('total_file_size_bytes')} bytes")
        
        print("\n")

        # Check for Processing jobs (worker picked them up, but might be stuck in a retry loop)
        processing_docs = list(db.collection("amr_jobs").where(filter=FieldFilter("status", "==", "Processing")).stream())
        print(f"--- PROCESSING JOBS (Currently running or stuck in 10-min timeout loop) ---")
        if not processing_docs:
             print("None.")
        else:
             for doc in processing_docs:
                  data = doc.to_dict()
                  print(f"Job ID: {doc.id} | Created: {data.get('created_at')} | Size: {data.get('total_file_size_bytes')} bytes")

    except Exception as e:
        print(f"Error querying Firestore database: {e}")

if __name__ == "__main__":
    check_queue_via_db()
