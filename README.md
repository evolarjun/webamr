# AMRFinderPlus Web Interface (WebAMR)

> [!WARNING]
> This application is currently under heavy development and is not yet functional. Please do not attempt to use it in production.

This application provides a web-based UI and scalable cloud-native backend for running [AMRFinderPlus](https://github.com/ncbi/amr), a tool by the NCBI for identifying antimicrobial resistance (AMR) genes in sequence data.

## Project Structure

WebAMR is built using a decoupled Cloud-Native architecture on Google Cloud Platform:

*   **`frontend/`**: A Flask-based web interface that allows users to upload nucleotide/protein/GFF files, select target organisms, and view real-time analysis results. It handles interactions directly with Google Cloud services: generating GCS uploads, managing job submissions, recording status in Cloud Firestore, and publishing jobs to Pub/Sub.
*   **`worker/`**: A containerized Cloud Run service that receives job messages from Pub/Sub via push subscriptions. It downloads files from GCS, executes the AMRFinderPlus binary, and uploads the results back to GCS.

## Architecture & Data Flow

Uses all google serverless technologies (for fun). Code runs on Google Cloud Run with two images, one for the front-end and one "worker" that runs AMRFinderPlus. Parameters and status for the runs are stored in Firestore, Input and output files are stored in Google Cloud Storage buckets, and the "worker" job is triggered by a Pub/Sub message.

For a detailed view of the infrastructure and data flow (including Google Cloud Storage, Pub/Sub, Firestore, and Cloud Run), please refer to the **[ARCHITECTURE.md](ARCHITECTURE.md)** file.

## Deployment

The application is containerized and designed for deployment on Google Cloud Platform. 

For complete instructions on setting up the GCP infrastructure, local testing with emulators, and deploying the frontend and workers to Cloud Run, please see the **[DEPLOYMENT.md](DEPLOYMENT.md)** guide.

## Development Status

This project is actively developed. Current features include:
*   Direct-to-GCS browser uploads for large files.
*   Asynchronous job processing with Pub/Sub.
*   Scalable zero-to-N worker instances on Cloud Run.
*   Job status tracking via Cloud Firestore.
