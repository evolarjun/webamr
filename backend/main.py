import os
import uuid
import json
from datetime import timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.cloud import storage, pubsub_v1, firestore
from google.api_core.exceptions import NotFound, BadRequest

app = FastAPI(title="AMRFinderPlus API")

# Setup CORS for the frontend application
allowed_origins_str = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173,http://localhost:8080")
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins, # Restricted via environment variables
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from Environment Variables
PROJECT_ID = os.environ.get("PROJECT_ID", "my-gcp-project")
INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "amr-input-bucket")
TOPIC_ID = os.environ.get("TOPIC_ID", "amr-jobs-topic")
API_KEY = os.environ.get("API_KEY", "dev-secret-key") # Must be overridden in production

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Security
api_key_header = APIKeyHeader(name="x-api-key", auto_error=True)

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")
    return api_key

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
def generate_upload_url(req: UploadUrlRequest, api_key: str = Security(verify_api_key)):
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
        "object_name": blob_name,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }

@app.post("/api/submit-job")
def submit_job(req: JobSubmitRequest, api_key: str = Security(verify_api_key)):
    """Submits the job to Pub/Sub and records it in Firestore."""
    # Verify the uploaded file size before doing anything else.
    if not req.gcs_uri.startswith("gs://"):
        raise HTTPException(status_code=400, detail="Invalid GCS URI: must start with 'gs://'")
    parts = req.gcs_uri.removeprefix("gs://").split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1].strip():
        raise HTTPException(status_code=400, detail="Invalid GCS URI: must be in the format 'gs://bucket/object'")
    try:
        blob = storage_client.bucket(parts[0]).blob(parts[1])
        blob.reload()  # Fetches metadata (including blob.size) from GCS
    except NotFound:
        raise HTTPException(status_code=404, detail="File not found in GCS")
    except BadRequest as e:
        raise HTTPException(status_code=400, detail=f"Invalid GCS request: {e}")
    if blob.size > MAX_UPLOAD_BYTES:
        blob.delete()  # Remove the oversized file so it doesn't linger
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {blob.size} bytes. Maximum allowed size is {MAX_UPLOAD_BYTES} bytes (10 MB)."
        )

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
