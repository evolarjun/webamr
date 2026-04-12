# WebAMR AI Agent Instructions

## MANDATORY FORMATTING RULES
- **Character Set**: STRICT ASCII ONLY. No em dashes (—), smart quotes (“ ”), or non-ASCII Unicode.
- **No Emojis**: Never use emojis in code, comments, or responses.
- **Tone**: Be concise. If unsure, say so. Never guess.

## Project Context
WebAMR is a cloud-native interface for the **NCBI AMRFinderPlus** tool. It handles genomic files (FASTA, Protein, GFF) and runs analysis via Cloud Run and Pub/Sub.

## Architecture & File Mapping
- **Frontend** (`frontend/`): Flask app serving UI and handling GCS uploads.
- **Worker** (`worker/`): Flask HTTP service that runs the `amrfinder` binary.
- **Database**: Firestore (`amr_jobs` collection).
- **Storage**: GCS (`amr-input-bucket`, `amr-output-bucket`).

## Development & Security Standards
- **Python Version**: 3.12+.
- **Frontend**: Vanilla JS/HTML/CSS only. **NO REACT/VUE**.
- **Secrets**: Never commit `set_variables.sh` or any GCP credentials.

## Use Red Green TDD (STRICT)
You MUST write/update tests before declaring a task complete.

Before running tests:
```bash
source set_variables.sh  # Requires valid GCP project variables
```

| Action | Command |
| :--- | :--- |
| **Unit Tests** | `pytest tests/test_frontend.py tests/test_worker.py -v` |
| **Integration**| `source set_variables.sh && pytest tests/test_integration.py -v` |

**Rule**: If modifying `frontend/main.py`, you MUST run `tests/test_frontend.py`. If modifying `worker/worker.py` you MUST run `tests/test_worker.py`.

## Common Operations
- **Rate Limiting**: `Flask-Limiter` is used; disable via `main.limiter.enabled = False` for tests.
- **Dependencies**: Update `requirements.txt` in BOTH `frontend/` and `worker/` if adding libraries.
- **To view exact configurations**: Check `DEPLOYMENT.md` for the exact `gcloud` and `gsutil` commands used to construct this environment.


## Override Rule
User instructions always override this file.
