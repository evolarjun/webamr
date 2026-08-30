# AMRFinderPlus Web Interface (WebAMR)

> [!WARNING]
> This is an experimental side project. No warranty express or implied. Not endorsed or supported by my employer or anyone else.

This application provides a web-based UI and serverless backend for running [AMRFinderPlus](https://github.com/ncbi/amr/wiki), a tool identifying antimicrobial resistance (AMR) genes and point mutations plus some virulence and biocide/stress resistance genes in assembled nucleotide and/or protein sequences. Currently running at https://amr.arjunp.net.

User documentation at https://amr.arjunp.net/docs

## Project Structure

WebAMR is built using a decoupled serverless architecture on Google Cloud Platform:

*   **`frontend/`**: A Flask-based web interface that allows users to upload nucleotide/protein/GFF files, select target organisms, and view analysis results. It handles interactions directly with Google Cloud services: generating GCS uploads, managing job submissions, recording status in Cloud Firestore, and publishing jobs to Pub/Sub.
*   **`worker/`**: A containerized Cloud Run service that receives job messages from Pub/Sub via push subscriptions. It downloads files from GCS, executes the AMRFinderPlus and optional AMRrules pipelines, and uploads the results back to GCS.

For a detailed view of the infrastructure and data flow (including Google Cloud Storage, Pub/Sub, Firestore, and Cloud Run), please refer to the **[ARCHITECTURE.md](ARCHITECTURE.md)** file.

## Deployment

The application is containerized and designed for deployment on Google Cloud Platform. 

For instructions on setting up the GCP infrastructure, local testing with emulators, and deploying the frontend and workers to Cloud Run, please see the **[DEPLOYMENT.md](DEPLOYMENT.md)** guide and **[TESTING.md](TESTING.md)**.

## Development Status

This project is actively developed and is now mostly vibe-coded. Current features include:
*   File uploads (nucleotide FASTA, protein FASTA, and GFF).
*   Optional AMRrules analysis.
*   Asynchronous job processing with Pub/Sub.
*   Scalable zero-to-N frontend and worker instances on Cloud Run.
*   Job status tracking via Cloud Firestore.
*   Shareable result pages and TSV download.

Bugs and feature requests to [GitHub](https://github.com/evolarjun/webamr/issues)
