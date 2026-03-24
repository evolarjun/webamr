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
        queued_docs = list(db.collection("amr_jobs").where("status", "==", "Queued").stream())
        if not queued_docs:
             print("No queued jobs to clear.")
        else:
             for doc in queued_docs:
                  doc.reference.update({
                      "status": "Failed",
                      "error_message": "Job timed out and was cleared from the system queue by the administrator."
                  })
                  print(f"Updated job {doc.id} to Failed.")
        
        processing_docs = list(db.collection("amr_jobs").where("status", "==", "Processing").stream())
        if processing_docs:
             for doc in processing_docs:
                  doc.reference.update({
                      "status": "Failed",
                      "error_message": "Job timed out and was cleared from the system queue by the administrator."
                  })
                  print(f"Updated job {doc.id} to Failed.")

    except Exception as e:
        print(f"Error updating Firestore database: {e}")

if __name__ == "__main__":
    clear_queued_jobs()
