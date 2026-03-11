# Deployment Guide for AMRFinderPlus Cloud-Native Architecture

This guide assumes you have the Google Cloud CLI (`gcloud`) installed and authenticated, and a GCP project ready.

> **Command Frequency Legend:**
> - 🟢 **[One-Time Setup]**: Commands only needed the very first time you set up the environment.
> - 🔄 **[Run Every Update]**: Commands to re-run whenever you update the codebase and deploy a new version.
> - 🛠️ **[Session Setup]**: Commands to set variables whenever you open a new terminal session for deployment.

## 1. Version Control (GitHub)

The local code has been initialized as a Git repository. To push it to GitHub:

1. Create a new repository on GitHub.
2. Link your local repository to GitHub and push:

🟢 **[One-Time Setup]**
```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git branch -M main
```

🔄 **[Run Every Update]**
```bash
git add .
git commit -m "Your commit message"
git push -u origin main
```

## 2. GCP Infrastructure Setup

Set your project variables and application environment variables:

🛠️ **[Session Setup]**
```bash
# General GCP Variables
PROJECT_ID="your-project-id"
REGION="us-central1"

# Application Setup Variables (Environment Variables)
BUCKET_NAME="amr-input-bucket-${PROJECT_ID}"
OUTPUT_BUCKET="amr-output-bucket-${PROJECT_ID}"
TOPIC_ID="amr-jobs-topic"

gcloud config set project $PROJECT_ID
```

### Enable APIs
Enable the necessary GCP APIs:

🟢 **[One-Time Setup]**
```bash
gcloud services enable \
  run.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  storage-component.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

### 2a. Firestore Time-To-Live (TTL)

This tells Firestore to automatically delete jobs 90 days after they were created. The application codebase adds an `expire_at` field (90 days in the future) to every new job document.

```bash
gcloud firestore fields ttls update expire_at \
  --collection-group=amr_jobs \
  --enable-ttl
```

### 2b. Cloud Storage Lifecycle Policy

This tells Cloud Storage to delete files older than 10 days from both buckets.

1. Create a `lifecycle.json` file:
```json
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 10}
    }
  ]
}
```
Apply the lifecycle policy to both buckets:

🟢 **[One-Time Setup]**
```bash
gsutil lifecycle set lifecycle.json gs://amr-input-bucket-${PROJECT_ID}
gsutil lifecycle set lifecycle.json gs://amr-output-bucket-${PROJECT_ID}
```

### Storage Buckets
Create the input and output Cloud Storage buckets:

🟢 **[One-Time Setup]**
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

🟢 **[One-Time Setup]**
```bash
gsutil cors set cors.json gs://amr-input-bucket-${PROJECT_ID}
```

### Pub/Sub
Create the Pub/Sub topic for the job queue:

🟢 **[One-Time Setup]**
```bash
gcloud pubsub topics create amr-jobs-topic
```
*(The push subscription pointing at the worker will be created in [Step 3c](#3c-create-a-pubsub-push-subscription-pointing-at-the-worker) once the worker URL is known.)*

### Firestore
Initialize Firestore in Native mode. You can do this through the GCP Console (Firestore section) or via CLI:

🟢 **[One-Time Setup]**
```bash
gcloud firestore databases create --location=$REGION
```

## 3. Deploying the Worker

The worker is a Flask HTTP service. Rather than pulling from Pub/Sub itself, it
receives job messages as **HTTP POST requests pushed by Pub/Sub** directly to
its Cloud Run URL. Cloud Run scales up an instance per job and back to zero
when idle — no VM needed.

### 3a. Build and push the Docker image

🟢 **[One-Time Setup]**
```bash
gcloud artifacts repositories create amr-repo --repository-format=docker --location=$REGION
```

🔄 **[Run Every Update]** *(whenever worker code changes)*
```bash
# Cloud Build submits the worker/ directory and pushes to Artifact Registry
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker \
  ./worker
```

### 3b. Deploy as a Cloud Run service

🔄 **[Run Every Update]**
```bash
gcloud run deploy amr-worker \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker \
  --region $REGION \
  --no-allow-unauthenticated \
  --timeout 1200 \
  --memory 4Gi \
  --cpu 2 \
  --set-env-vars PROJECT_ID=$PROJECT_ID,OUTPUT_BUCKET=$OUTPUT_BUCKET \
  --concurrency 1 \
  --max-instances 1  # Change this number to limit total simultaneous jobs

# Save the deployed URL for the next step
WORKER_URL=$(gcloud run services describe amr-worker \
  --region $REGION --format='value(status.url)')
```

`--no-allow-unauthenticated` restricts the endpoint so only Pub/Sub (via its
service account) can invoke it — not arbitrary HTTP clients.

### 3c. Create a Pub/Sub push subscription pointing at the worker

🟢 **[One-Time Setup]**
```bash
# Create a dedicated service account for Pub/Sub to authenticate with Cloud Run
gcloud iam service-accounts create amr-pubsub-invoker \
  --display-name="AMR Pub/Sub Invoker"

# Grant it permission to invoke the Cloud Run worker
gcloud run services add-iam-policy-binding amr-worker \
  --region $REGION \
  --member="serviceAccount:amr-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# Create the push subscription — Pub/Sub will POST each job message to /
gcloud pubsub subscriptions create amr-jobs-sub \
  --topic amr-jobs-topic \
  --push-endpoint=${WORKER_URL}/ \
  --push-auth-service-account=amr-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com \
  --ack-deadline=600 \
  --min-retry-delay=10s \
  --max-retry-delay=600s
```

*(Note: if you already created `amr-jobs-sub` as a pull subscription, delete it first: `gcloud pubsub subscriptions delete amr-jobs-sub`)*

## 4. Frontend Setup

The frontend is a Flask application located in `frontend/`. It serves the HTML UI from `frontend/templates/index.html` and handles file uploads and job submission directly to GCP.

### 4a. Build and push the Frontend Docker image

From the `frontend` directory, download the required AMRFinderPlus resource files and submit the build:

🔄 **[Run Every Update]** *(whenever frontend code changes)*
```bash
cd frontend

# Download required datatables for the frontend UI
curl -s -o database_version.txt https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/version.txt
curl -s -O https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/taxgroup.tsv

# Build and push the docker image
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-frontend \
  .
```

### 4b. Deploy Frontend as a Cloud Run service

🔄 **[Run Every Update]**
```bash
gcloud run deploy amr-frontend \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-frontend \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,BUCKET_NAME=$BUCKET_NAME,TOPIC_ID=$TOPIC_ID,OUTPUT_BUCKET=$OUTPUT_BUCKET \
  --max-instances 1
```
*(Note: the container listens on port 80 as defined in its Dockerfile)*

### Running Local Frontend

To run locally (e.g. for testing UI modifications before pushing):
```bash
cd frontend
source .venv/bin/activate
python -m flask --app main run -p 8080
```

## Local Testing & Development

You can test this architecture locally before deploying to GCP! We will use the Google Cloud SDK local emulators and Docker.

### 1. Start the GCP Emulators

GCP provides a Pub/Sub emulator for local development.

```bash
# Install emulators if you don't have them
gcloud components install pubsub-emulator

# Start PubSub emulator
gcloud beta emulators pubsub start --project=$PROJECT_ID --port=8085
```

In a **new terminal window**, set the environment variable so python clients use the emulator:
```bash
export PUBSUB_EMULATOR_HOST=localhost:8085
```

### 2. Create the Local Topics (Requires a quick python script)
Write a quick script `setup_local_pubsub.py` to create the topic and subscription on the emulator, and run it.

### 3. Hybrid Testing (Local AMRFinderPlus Worker with Real GCP)

To test the full cloud architecture without paying for a Compute Engine VM, run the computationally heavy AMRFinderPlus worker locally on your own machine using Docker. 

This connects your local container natively to the real Google Cloud Pub/Sub queue, Cloud Storage, and Firestore.

1.  Make sure you are authenticated locally with GCP so Docker can borrow your credentials:
    ```bash
    gcloud auth application-default login --project="your-project-id"
    ```

2.  Build and run the worker container:
    ```bash
    cd worker
    docker build -t amr-worker-local .

    # Run the docker container locally, injecting your GCP credentials so it can talk to your live project
    docker run -it \
      -e PROJECT_ID="your-project-id" \
      -e SUBSCRIPTION_ID="amr-jobs-sub" \
      -e OUTPUT_BUCKET="amr-output-bucket-your-project-id" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/key.json \
      -v ~/.config/gcloud/application_default_credentials.json:/tmp/keys/key.json \
      amr-worker-local
    ```

### 4. Run the Frontend

```bash
cd frontend
source .venv/bin/activate
python -m flask --app main run -p 8080
```

The Flask app will be available at `http://localhost:8080/`.
