# Deployment Guide for AMRFinderPlus Cloud-Native Architecture

This guide assumes you have the Google Cloud CLI (`gcloud`) installed and authenticated, and a GCP project ready.

## 1. Version Control (GitHub)

The local code has been initialized as a Git repository. To push it to GitHub:

1. Create a new repository on GitHub.
2. Link your local repository to GitHub and push:
```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git branch -M main
git push -u origin main
```

## 2. GCP Infrastructure Setup

Set your project variables:
```bash
PROJECT_ID="your-project-id"
REGION="us-central1"
gcloud config set project $PROJECT_ID
```

### Enable APIs
Enable the necessary GCP APIs:
```bash
gcloud services enable \
  run.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  storage-component.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### Storage Buckets
Create the input and output Cloud Storage buckets:
```bash
gsutil mb -l $REGION gs://amr-input-bucket-${PROJECT_ID}
gsutil mb -l $REGION gs://amr-output-bucket-${PROJECT_ID}
```

Configure CORS on the input bucket to allow direct uploads from the browser. Create a `cors.json` file:
```json
[
  {
    "origin": ["*"],
    "method": ["PUT", "OPTIONS"],
    "responseHeader": ["Content-Type"],
    "maxAgeSeconds": 3600
  }
]
```
Apply the CORS policy:
```bash
gsutil cors set cors.json gs://amr-input-bucket-${PROJECT_ID}
```

### Pub/Sub
Create the topic and subscription for the job queue:
```bash
gcloud pubsub topics create amr-jobs-topic
gcloud pubsub subscriptions create amr-jobs-sub --topic=amr-jobs-topic --ack-deadline=600
```
*(Note: Increase `--ack-deadline` if AMRFinderPlus jobs take longer than 10 minutes).*

### Firestore
Initialize Firestore in Native mode. You can do this through the GCP Console (Firestore section) or via CLI:
```bash
gcloud firestore databases create --location=$REGION
```

## 3. Deploying the Backend (FastAPI)

We will deploy the backend to Google Cloud Run. First, create a `Dockerfile` for the backend:

**`backend/Dockerfile`**:
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Deploy to Cloud Run:
```bash
cd backend
gcloud run deploy amr-backend \
  --source . \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,INPUT_BUCKET=amr-input-bucket-${PROJECT_ID},TOPIC_ID=amr-jobs-topic
```

## 4. Deploying the Worker

The worker runs as a continuous background process. We can deploy it to a Compute Engine VM, or a background Cloud Run service, or Google Kubernetes Engine (GKE).

For simplicity, let's deploy it to a background Cloud Run Service (or a small Compute Engine instance). Let's use Artifact Registry to build and store the Docker image:

```bash
gcloud artifacts repositories create amr-repo --repository-format=docker --location=$REGION
gcloud builds submit --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker ./worker
```

*Option A: Compute Engine VM (Recommended for long-running bioinformatics jobs)*
Create a VM with container support:
```bash
gcloud compute instances create-with-container amr-worker-vm \
  --zone=${REGION}-a \
  --machine-type=e2-standard-4 \
  --scopes=cloud-platform \
  --container-image=${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker \
  --container-env=PROJECT_ID=$PROJECT_ID,SUBSCRIPTION_ID=amr-jobs-sub,OUTPUT_BUCKET=amr-output-bucket-${PROJECT_ID}
```

## 5. Frontend Setup

Move into the frontend folder, set up your preferred framework (e.g., Vite/React), and embed the `AMRFinderPlusComponent.tsx`.

Remember to update the API URLs in the component from `http://localhost:8000` to your deployed Cloud Run backend URL!
