import os
import sys
from google.cloud import firestore


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        print("Run: source set_variables.sh", file=sys.stderr)
        raise SystemExit(1)
    return value

def clear_queued_jobs():
    project_id = get_required_env("PROJECT_ID")
    db = firestore.Client(project=project_id)
    print(f"Clearing queued jobs in Firestore for project: {project_id}...\n")

    try:
        # Collect both Queued and Processing jobs
        queued_docs = list(db.collection("amr_jobs").where("status", "==", "Queued").stream())
        processing_docs = list(db.collection("amr_jobs").where("status", "==", "Processing").stream())

        all_docs = queued_docs + processing_docs

        if not all_docs:
             print("No queued or processing jobs to clear.")
             return

        # Firestore batches are limited to 500 operations
        BATCH_SIZE = 500
        for i in range(0, len(all_docs), BATCH_SIZE):
            chunk = all_docs[i:i + BATCH_SIZE]
            batch = db.batch()
            for doc in chunk:
                batch.update(doc.reference, {
                    "status": "Failed",
                    "error_message": "Job timed out and was cleared from the system queue by the administrator."
                })
            batch.commit()
            for doc in chunk:
                print(f"Updated job {doc.id} to Failed.")

    except Exception as e:
        print(f"Error updating Firestore database: {e}")

if __name__ == "__main__":
    clear_queued_jobs()
