import os
import json
import subprocess
from google.cloud import storage, pubsub_v1, firestore

PROJECT_ID = os.environ.get("PROJECT_ID", "my-gcp-project")
SUBSCRIPTION_ID = os.environ.get("SUBSCRIPTION_ID", "amr-jobs-sub")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "amr-output-bucket")

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)
storage_client = storage.Client(project=PROJECT_ID)
db = firestore.Client(project=PROJECT_ID)

def download_blob(gcs_uri, local_path):
    # Parses gs://bucket_name/path/to/blob
    parts = gcs_uri.replace("gs://", "").split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1]
    
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.download_to_filename(local_path)

def upload_blob(local_path, destination_blob_name):
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(local_path)
    return f"gs://{OUTPUT_BUCKET}/{destination_blob_name}"

def run_amrfinder(input_fasta, output_tsv, params):
    cmd = ["amrfinder", "-n", input_fasta, "-o", output_tsv]
    
    if params.get("plus_flag"):
        cmd.append("--plus")
    
    organism = params.get("organism")
    if organism:
        cmd.extend(["-O", organism])
        
    ident_min = params.get("ident_min")
    if ident_min is not None:
        cmd.extend(["-i", str(ident_min)])
        
    coverage_min = params.get("coverage_min")
    if coverage_min is not None:
        cmd.extend(["-c", str(coverage_min)])
        
    print(f"Executing: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"AMRFinderPlus failed: {result.stderr}")
        
    return result.stdout

def callback(message):
    data = json.loads(message.data.decode("utf-8"))
    job_id = data["job_id"]
    gcs_uri = data["gcs_uri"]
    params = data["parameters"]
    
    print(f"Received job {job_id}. Processing...")
    doc_ref = db.collection("amr_jobs").document(job_id)
    doc_ref.update({"status": "Processing"})
    
    local_input = f"/tmp/{job_id}_input.fasta"
    local_output = f"/tmp/{job_id}_output.tsv"
    
    try:
        # Download input from GCS
        download_blob(gcs_uri, local_input)
        
        # Execute AMRFinderPlus Subprocess
        run_amrfinder(local_input, local_output, params)
        
        # Upload results to GCS Output Bucket
        result_blob_name = f"results/{job_id}.tsv"
        result_uri = upload_blob(local_output, result_blob_name)
        
        # Update Firestore Status
        doc_ref.update({
            "status": "Completed", 
            "result_uri": result_uri
        })
        print(f"Job {job_id} Completed successfully.")
        
    except Exception as e:
        print(f"Job {job_id} Failed: {str(e)}")
        doc_ref.update({
            "status": "Failed",
            "error_message": str(e)
        })
    finally:
        # Cleanup container ephemeral storage
        if os.path.exists(local_input):
            os.remove(local_input)
        if os.path.exists(local_output):
            os.remove(local_output)
            
    # Acknowledge the message so it's not redelivered
    message.ack()

def start_worker():
    # Keep the databases updated right when worker spins up
    print("Running amrfinder_update before accepting jobs...")
    subprocess.run(["amrfinder_update"], check=True)
    
    print(f"Listening for messages on {subscription_path}..\n")
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    
    with subscriber:
        try:
            streaming_pull_future.result()
        except TimeoutError:
            streaming_pull_future.cancel()
            streaming_pull_future.result()

if __name__ == "__main__":
    start_worker()
