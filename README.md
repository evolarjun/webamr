# AMRFinderPlus Web Interface (WebAMR)

> [!WARNING]
> This application is currently under heavy development and is not yet functional. Please do not attempt to use it in production.

This application provides a web-based UI and scalable cloud-native backend for running [AMRFinderPlus](https://github.com/ncbi/amr), a tool by the NCBI for identifying antimicrobial resistance (AMR) genes in sequence data.

## Project Structure

WebAMR is built using a modern, decoupled Cloud-Native architecture on Google Cloud Platform:

*   **`frontend/`**: A Flask-based web interface that allows users to upload nucleotide/protein/GFF files, select target organisms, and view real-time analysis results. It communicates with the backend API.
*   **`backend/`**: A FastAPI service running on Cloud Run. It generates signed GCS upload URLs, manages job submissions, records status in Cloud Firestore, and publishes jobs to Pub/Sub.
*   **`worker/`**: A containerized Cloud Run service that receives job messages from Pub/Sub via push subscriptions. It downloads files from GCS, executes the AMRFinderPlus binary, and uploads the results back to GCS.

## Architecture & Data Flow

For a detailed view of the infrastructure and data flow (including Google Cloud Storage, Pub/Sub, Firestore, and Cloud Run), please refer to the **[ARCHITECTURE.md](ARCHITECTURE.md)** file.

## Deployment

The application is fully containerized and designed for deployment on Google Cloud Platform. 

For complete instructions on setting up the GCP infrastructure, local testing with emulators, and deploying the frontend, backend, and workers to Cloud Run, please see the **[DEPLOYMENT.md](DEPLOYMENT.md)** guide.

## Development Status

This project is actively developed. Current features include:
*   Direct-to-GCS browser uploads using signed URLs for large files.
*   Asynchronous job processing with Pub/Sub.
*   Scalable zero-to-N worker instances on Cloud Run.
*   Job status tracking via Cloud Firestore.
