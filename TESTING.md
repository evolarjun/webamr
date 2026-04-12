# Testing

This project has two levels of tests: **unit tests** (no external dependencies) and **integration tests** (require GCP credentials).

## Prerequisites

Activate the virtual environment before running any tests:

```bash
cd /path/to/webamr
source .venv/bin/activate
pip install pytest flask werkzeug  # if not already installed
```

---

## Unit Tests

Unit tests mock all GCP services (Firestore, Cloud Storage, Pub/Sub) and the `amrfinder` binary. No real credentials or Docker image are required.

### Run all unit tests

```bash
pytest tests/test_worker.py tests/test_frontend.py -v
```

### Test files

| File | What it tests |
|---|---|
| `tests/test_worker.py` | Worker Flask endpoint (`/`), GCS `download_blob`/`upload_blob` helpers, `run_amrfinder` command construction |
| `tests/test_frontend.py` | Frontend Flask routes: `GET /`, `POST /analyze`, `GET /get-results/<id>`, `GET /output/<id>`, `GET /stderr/<id>` |

### Run a single test file

```bash
pytest tests/test_worker.py -v
pytest tests/test_frontend.py -v
```

### Run a specific test class or test

```bash
pytest tests/test_worker.py::TestRunAmrfinder -v
pytest tests/test_frontend.py::TestAnalyze::test_pubsub_publish_called -v
```

---

## Integration Tests

Integration tests wire the **frontend and worker Flask apps together** in a single pytest process. Pub/Sub is intercepted (not real), but **GCS and Firestore are real** — tests create temporary files/documents and clean them up afterward.

`amrfinder` is **not required locally** — it is mocked with fake TSV output so these tests work on any machine with valid GCP credentials.

### Prerequisites

1. Valid GCP credentials:
   ```bash
   gcloud auth application-default login
   ```

2. Set environment variables:
   ```bash
   source set_variables.sh
   ```

### Run the integration tests

```bash
source set_variables.sh
pytest tests/test_integration.py -v
```

### What is tested

| Test | Description |
|---|---|
| `test_submit_and_process_job` | Full flow: upload file via frontend → intercept Pub/Sub message → forward to worker → verify results retrievable from frontend |
| `test_submit_with_organism` | Same flow with an organism parameter, verifies it flows through to the worker payload |
| `test_failed_job_reports_error` | Worker fails → verify frontend reports the error via Firestore |
| `test_download_output_file` | After processing, verify the TSV output is downloadable via `GET /output/<id>` |

---

## Run Everything

```bash
# Unit tests (no Docker or GCP credentials needed)
pytest tests/test_worker.py tests/test_frontend.py -v

# Integration tests (requires GCP credentials + source set_variables.sh)
source set_variables.sh
pytest tests/test_integration.py -v

# All tests at once
source set_variables.sh
pytest tests/ -v
```

---

## Production E2E Tests

These tests make real HTTPS requests to the **deployed** Cloud Run frontend service,
running the full pipeline end-to-end with actual AMRFinderPlus compute.

> **Note**: These tests consume real GCP resources (Cloud Run, GCS, Firestore, Pub/Sub)
> and may take up to 10 minutes per run. They clean up after themselves.

### Prerequisites

1. Valid GCP credentials and environment variables:
   ```bash
   gcloud auth application-default login
   source set_variables.sh
   ```

2. Export the deployed frontend URL:
   ```bash
   export FRONTEND_URL=$(gcloud run services describe amr-frontend \
     --region $REGION --format='value(status.url)')
   ```

### Run the production E2E tests

```bash
source set_variables.sh
export FRONTEND_URL=$(gcloud run services describe amr-frontend --region $REGION --format='value(status.url)')
pytest tests/test_e2e_production.py -v -s
```

### What is tested

| Test | Description |
|---|---|
| `test_full_job_lifecycle` | Submits a real FASTA file, polls until complete, verifies the HTML results table and TSV download |
| `test_results_page_is_accessible` | Submits a job and immediately verifies the `/results/<id>` shareable page loads |
| `test_unknown_job_id_returns_404` | Verifies that an unknown job ID returns a 404 response (quick, no GCP compute) |

---

## Manual Local Testing

You can use a "hybrid testing" approach to run both the frontend UI and the backend worker locally on your machine while still connecting to your real GCP resources (Cloud Storage, Firestore). This allows you to test the full processing pipeline (including AMRFinderPlus and AMRrules binary execution) without deploying to Cloud Run.

### 1. Authenticate locally

Ensure your local `gcloud` CLI is authenticated so your local apps can securely access your GCP resources without needing explicit service account keys.

```bash
gcloud auth application-default login --project="your-project-id"
```

### 2. Run the Frontend (Terminal 1)

The frontend is a standard Flask app. You'll need to set the required environment variables.

```bash
# From the root of the project
cd frontend
source .venv/bin/activate

# Source your environment variables (like PROJECT_ID, OUTPUT_BUCKET, etc.)
source ../set_variables.sh 

# OVERRIDE the Pub/Sub topic to prevent the frontend from sending jobs to your production worker!
export TOPIC_ID="local-testing-only"

# Start the Flask development server on port 8080
python -m flask --app main run -p 8080
```
The frontend will now be available at `http://localhost:8080/`.

### 3. Run the Backend Worker (Terminal 2)

The worker requires the heavy `amrfinder` and `amrrules` binaries, so it needs to be run inside its Docker container. We map your local Google Cloud credentials into the container so the worker can securely read from and write to Firestore and Cloud Storage. We also map port `8080` from the container to `8081` on your host machine to avoid conflicting with the frontend.

```bash
# Open a new terminal
cd worker

# Build the worker container
docker build -t amr-worker-local .

# Run the container (replace '$PROJECT_ID' with your actual GCP project ID)
docker run -it \
  -e PROJECT_ID="$PROJECT_ID" \
  -e OUTPUT_BUCKET="amr-output-bucket-$PROJECT_ID" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/key.json \
  -v ~/.config/gcloud/application_default_credentials.json:/tmp/keys/key.json \
  -p 8081:8080 \
  amr-worker-local
```

### 4. Triggering Jobs Locally

Normally, Google Cloud Pub/Sub actively *pushes* messages to a public Cloud Run URL. Because your worker is now running securely on `localhost:8081` behind your router, the live Pub/Sub queue cannot push to it. 

To execute jobs locally, you submit a job via the local frontend UI (`http://localhost:8080/`), and then manually push the resulting Pub/Sub payload to your local docker container.

1. Submit a job via the local frontend UI.
2. Watch the terminal output of the frontend server. It will print a JSON structure that it sent to Pub/Sub (or you can copy the job ID and GCS URI).
3. Use the following script in a third terminal to format the payload as a Pub/Sub push notification and send it to your local worker:

```bash
# 1. Define the job data (replace job_id with the actual job_id and gcs_uri from the firestore record)
JOB_JSON='{
  "job_id": "local-test-001",
  "gcs_uri": "gs://amr-input-bucket-your-project-id/uploads/test.fasta",
  "job_name": "My Local Test",
  "parameters": {
    "organism": "Escherichia",
    "annotation_format": "standard",
    "has_nucleotide": true,
    "plus_flag": true,
    "print_node": true,
    "amrrules_organism": "s__Escherichia coli",
    "no_rule_interpretation": "none"
  }
}'

# 2. Base64 encode the payload (using -w 0 to ensure it stays on one line on macOS/Linux)
B64_PAYLOAD=$(echo -n "$JOB_JSON" | base64 | tr -d '\n')

# 3. Build the Pub/Sub envelope
PUBSUB_ENVELOPE='{
  "message": {
    "data": "'$B64_PAYLOAD'",
    "messageId": "mock-local-1234"
  },
  "subscription": "projects/local/subscriptions/amr-jobs-sub"
}'

# 4. Send the POST request to the local worker container
curl -X POST http://localhost:8081/ \
  -H "Content-Type: application/json" \
  -d "$PUBSUB_ENVELOPE"
```

If successful, you will see your local worker acknowledge the job, execute AMRFinderPlus and AMRrules, and upload the results. The frontend UI will automatically update from "Processing" to displaying the results table!

