import os
import uuid
import json
from datetime import timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.cloud import storage, pubsub_v1, firestore

app = FastAPI(title="AMRFinderPlus API")

# Setup CORS for the frontend application
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Should be restricted in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from Environment Variables
PROJECT_ID = os.environ.get("PROJECT_ID", "my-gcp-project")
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "amr-input-bucket")
TOPIC_ID = os.environ.get("TOPIC_ID", "amr-jobs-topic")

# GCP Clients
storage_client = storage.Client(project=PROJECT_ID)
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)
db = firestore.Client(project=PROJECT_ID)

class UploadUrlRequest(BaseModel):
    filename: str

class JobSubmitRequest(BaseModel):
    gcs_uri: str
    plus_flag: bool = False
    organism: Optional[str] = None
    ident_min: Optional[float] = None
    coverage_min: Optional[float] = None

@app.post("/api/upload-url")
def generate_upload_url(req: UploadUrlRequest):
    """Generates a v4 signed URL for uploading to GCS directly from the browser."""
    # Generate a unique object name to prevent collisions
    blob_name = f"uploads/{uuid.uuid4()}-{req.filename}"
    bucket = storage_client.bucket(INPUT_BUCKET)
    blob = bucket.blob(blob_name)

    # Note: Service account needs Service Account Token Creator role for this to work
    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(minutes=15),
        method="PUT",
        content_type="application/octet-stream",
    )
    return {
        "signed_url": url,
        "gcs_uri": f"gs://{INPUT_BUCKET}/{blob_name}",
        "object_name": blob_name
    }

@app.post("/api/submit-job")
def submit_job(req: JobSubmitRequest):
    """Submits the job to Pub/Sub and records it in Firestore."""
    job_id = str(uuid.uuid4())

    # 1. Update DB state to pending
    doc_ref = db.collection("amr_jobs").document(job_id)
    doc_ref.set({
        "job_id": job_id,
        "status": "Pending",
        "gcs_uri": req.gcs_uri,
        "parameters": req.model_dump(),
        "result_uri": None,
        "error_message": None
    })

    # 2. Publish to Pub/Sub
    message_data = {
        "job_id": job_id,
        "gcs_uri": req.gcs_uri,
        "parameters": req.model_dump()
    }
    data_str = json.dumps(message_data).encode("utf-8")

    try:
        publisher.publish(topic_path, data_str)
    except Exception as e:
        doc_ref.update({"status": "Failed", "error_message": str(e)})
        raise HTTPException(status_code=500, detail="Failed to publish job.")

    return {"job_id": job_id, "status": "Pending"}

@app.get("/api/status/{job_id}")
def get_job_status(job_id: str):
    """Checks the status of a specific job_id."""
    doc_ref = db.collection("amr_jobs").document(job_id)
    doc = doc_ref.get()

    if not doc.exists:
        raise HTTPException(status_code=404, detail="Job not found")

    return doc.to_dict()
