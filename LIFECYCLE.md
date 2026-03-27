# WebAMR Job Lifecycle

This document describes the lifecycle of a job in WebAMR, from the moment a user submits an analysis request to the final result display.

## Job Status Definitions

The `status` field in the Firestore `amr_jobs` collection tracks the progress of each job:

| Status | Description |
| :--- | :--- |
| **`Queued`** | The job record has been created in Firestore, and a message has been sent to Pub/Sub to trigger the worker. |
| **`Processing`** | The Cloud Run worker has received the job and is currently running the `amrfinder` analysis. |
| **`Completed`** | The analysis finished successfully. Results and logs are available in Cloud Storage. |
| **`Failed`** | An error occurred during processing. A descriptive error message is recorded in the `error_message` field. |

---

## Detailed Lifecycle Flow

### 1. Submission (Frontend)
- **File Upload**: The user selects genomic files (FASTA/Protein) and optional parameters on the frontend.
- **Initialization**: The frontend saves the files to temporary storage and then uploads them to the **Input Cloud Storage Bucket**.
- **Firestore Entry**: A new document is created in the `amr_jobs` collection with the initial status set to **`Queued`**.
- **Trigger**: A message containing the `job_id` and file locations is published to the **Pub/Sub topic**.

### 2. Processing (Worker)
- **Trigger**: Pub/Sub pushes the message to the **Worker Cloud Run service**.
- **Status Update**: The worker immediately updates the job status in Firestore to **`Processing`**.
- **Environment Setup**: The worker downloads the input files from GCS to its local `/tmp` directory.
- **Execution**: The worker executes the `amrfinder` binary with the user's provided parameters.
- **Data Capture**: Both the result TSV (stdout) and the execution logs (stderr) are captured.

### 3. Finalization (Worker)
- **Successful Run**:
    - Results and logs are uploaded to the **Output Cloud Storage Bucket**.
    - The Firestore status is updated to **`Completed`**.
- **Failed Run**:
    - The error message is captured.
    - Status is updated to **`Failed`** in Firestore, and the `error_message` field is populated.

### 4. Viewing Results (Frontend)
- **Polling**: While the user is on the results page, the frontend polls Firestore for status updates.
- **Display**: 
    - If **`Completed`**, the frontend downloads the TSV results from GCS and renders them as an interactive table.
    - If **`Failed`**, the frontend displays the specific error message to help the user troubleshoot.

---

## Data Retention and Cleanup
- **Cloud Storage**: Both input and output files are automatically deleted after **10 days** via GCS lifecycle rules.
- **Firestore**: Job metadata is automatically purged **90 days** after creation via a TTL (Time-to-Live) policy on the `expire_at` field.
