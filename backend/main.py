import os
import logging
from google.cloud import storage

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set environment variables (replace with your actual bucket names)
INPUT_BUCKET_NAME = os.environ.get("INPUT_BUCKET_NAME", "webamr-trigger")
OUTPUT_BUCKET_NAME = os.environ.get("OUTPUT_BUCKET_NAME", "webamr-output")


def process_fasta(data):
    # Replace this with your actual FASTA processing logic
    processed_data = f"Processed FASTA data:\n{data}"  
    return processed_data

def main(event, context):
    try:
        file = event
        bucket_name = file['bucket']
        file_name = file['name']

        # Check if the event is for a new file in the correct bucket.
        if bucket_name != INPUT_BUCKET_NAME:
            logging.info(f"Skipping file {file_name} in bucket {bucket_name} - not the input bucket.")
            return

        logging.info(f"Processing file {file_name} from bucket {bucket_name}.")

        # Initialize Google Cloud Storage client
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)

        # Download FASTA file content
        fasta_content = blob.download_as_string().decode("utf-8")

        # Process FASTA data
        results = process_fasta(fasta_content)

        # Write output to results bucket
        output_file_name = file_name.replace(".fa", "_results.txt") # Assume .fa
        output_bucket = storage_client.bucket(OUTPUT_BUCKET_NAME)
        output_blob = output_bucket.blob(output_file_name)
        output_blob.upload_from_string(results)

        logging.info(f"Results written to file {output_file_name} in bucket {OUTPUT_BUCKET_NAME}")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        # Consider using a dedicated error reporting system