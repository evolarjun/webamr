# WebAMR AI Agent Instructions

Welcome! If you are an AI coding assistant interacting with this project, please follow these guidelines to understand the architecture, run tests, and maintain code quality.

## Project Overview
WebAMR is a cloud-native web interface for the NCBI AMRFinderPlus tool. It allows users to upload genomic files (FASTA, Protein, GFF) and runs the computationally heavy AMRFinderPlus analysis asynchronously.

## Architecture & Tech Stack
This project uses a decoupled, event-driven architecture on Google Cloud Platform (GCP):

1. **Frontend (`frontend/`)**: A Flask application deployed to Cloud Run. It serves the UI (`index.html`, `results.html`), handles file uploads to Cloud Storage, creates a job record in Firestore, and publishes a message to Pub/Sub.
2. **Pub/Sub (`amr-jobs-topic`)**: Decouples the frontend from the worker.
3. **Worker (`worker/`)**: A Flask HTTP service deployed to Cloud Run (triggered by Pub/Sub push subscriptions). It downloads the files from GCS, runs the local `amrfinder` binary, uploads the TSV results back to GCS, and updates the Firestore job status.
4. **Data Layers**:
   - **Cloud Storage**: Stores input files (`amr-input-bucket`) and output TSV/Stderr files (`amr-output-bucket`). *Note: Files are automatically deleted after 10 days via a lifecycle rule.*
   - **Firestore**: Stores job metadata (`amr_jobs` collection). *Note: Documents are automatically purged 90 days after creation via a TTL on the `expire_at` field.*

## Development Conventions

*   **Language**: Python 3.12+
*   **Web Framework**: Flask
*   **Frontend UI**: Vanilla JavaScript, HTML, and CSS (no React/Vue/complex frameworks). Focus on clean, responsive, and simple UI.
*   **Asynchronous Polling**: The frontend uses JS polling (`/get-results/<job_id>`) to check Firestore for job completion instead of WebSockets.
*   **Error Handling**: Both the frontend and worker should gracefully handle errors, write them to the `error_message` field in Firestore, and ensure the user sees exactly what went wrong.
*   **Security**: Never commit GCP credentials, `set_variables.sh`, or `gcp_setup_commands.txt` if they contain real project IDs or sensitive information.

## Testing Strategy (IMPORTANT)

This project strictly follows TDD (Test-Driven Development). **Always write or update tests when modifying functionality. Make sure to run tests before declaring done.**

### Unit Tests (Fast, No GCP/Docker required)
Unit tests use `unittest.mock` to mock all GCP services (Firestore, GCS, Pub/Sub) and the `amrfinder` binary.
```bash
source .venv/bin/activate
pytest tests/test_frontend.py tests/test_worker.py -v
```

### Integration Tests (Slower, Requires GCP Credentials)
Integration tests spin up the Flask apps and hit real GCS and Firestore (but mock Pub/Sub and `amrfinder`). They verify the end-to-end flow.
```bash
source .venv/bin/activate
source set_variables.sh  # Requires valid GCP project variables
pytest tests/test_integration.py -v
```

## Common Operations

*   **Versioning**: This project reads `VERSION.txt` at the root. You MUST increment the semantic version in `VERSION.txt` whenever you make functionality changes, bug fixes, or architecture updates, to ensure the frontend and worker are running the expected matching code.
*   **To test rate limits**: We use `Flask-Limiter`. It is disabled during testing via `main.limiter.enabled = False`.
*   **To update dependencies**: Update `requirements.txt` in both `frontend/` and `worker/` directories.
*   **To view exact configurations**: Check `DEPLOYMENT.md` for the exact `gcloud` and `gsutil` commands used to construct this environment.

## Output
- No em dashes, smart quotes, or Unicode. ASCII only.
- Be concise. If unsure, say so. Never guess.

## Override Rule
User instructions always override this file.
