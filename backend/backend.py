import os
import logging
from google.cloud import storage
import subprocess
import shutil
import tempfile

# Configure logging
logging.basicConfig(level=logging.INFO)

# Set environment variables (replace with your actual bucket names)
TRIGGER_BUCKET_NAME = os.environ.get("TRIGGER_BUCKET_NAME", "webamr-trigger")
INPUT_BUCKET_NAME = os.environ.get("INPUT_BUCKET_NAME", "webamr")
OUTPUT_BUCKET_NAME = os.environ.get("OUTPUT_BUCKET_NAME", "webamr-output")


def process_file(filename):
    # Replace this with your actual FASTA processing logic
    with open(filename, 'r') as file:
        data = file.read()
    processed_data = f"Processed the data from {filename}:\n{data}"
    return processed_data

def main(event, context):
    try:
        file = event
        bucket_name = file['bucket']
        file_name = file['name']

        # Check if the event is for a new file in the correct bucket.
        if bucket_name != TRIGGER_BUCKET_NAME:
            logging.info(f"Skipping file {file_name} in bucket {bucket_name} - not the input bucket.")
            return

        logging.info(f"Processing file {file_name} from bucket {bucket_name}.")

        # Initialize Google Cloud Storage client
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_name)

        # Create a temporary file to store the downloaded FASTA content
        with tempfile.NamedTemporaryFile(mode="w+b", delete=False) as temp_file:
            # Download FASTA file to the temporary file
            blob.download_to_filename(temp_file.name)
            temp_file.flush()  # Ensure data is written to disk
            temp_file_path = temp_file.name

        # Process FASTA data (replace with your actual processing logic)
        results = process_file(temp_file_path) # Pass the temporary file path

        # Write output to results bucket
        output_file_name = file_name.replace(".fa", "_results.txt") # Assume .fa
        output_bucket = storage_client.bucket(OUTPUT_BUCKET_NAME)
        output_blob = output_bucket.blob(output_file_name)
        output_blob.upload_from_string(results)

        logging.info(f"Results written to file {output_file_name} in bucket {OUTPUT_BUCKET_NAME}")
        
        # Remove temporary file
        os.remove(temp_file_path)

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        # Consider using a dedicated error reporting system