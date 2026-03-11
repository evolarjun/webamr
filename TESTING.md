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

