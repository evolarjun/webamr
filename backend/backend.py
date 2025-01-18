import os
import logging
from flask import Flask
from google.cloud import storage
from google.cloud.storage import Client, Blob
import tempfile

# Configure logging (where does this go?)
logging.basicConfig(level=logging.INFO)

# Set environment variables for buckets
TRIGGER_BUCKET_NAME = os.environ.get("TRIGGER_BUCKET_NAME", "webamr-trigger")
INPUT_BUCKET_NAME = os.environ.get("INPUT_BUCKET_NAME", "webamr")
OUTPUT_BUCKET_NAME = os.environ.get("OUTPUT_BUCKET_NAME", "webamr-output")


def process_file(filename):
    # Replace this with your actual FASTA processing logic
    with open(filename, 'r') as file:
        data = file.read()
    processed_data = f"Processed the data from {filename}:\n{data}"
    return processed_data

def list_files_in_bucket(bucket_name, directory_name):
    """Lists all files in the specified directory of the bucket."""
    storage_client = Client()
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=directory_name)
    file_names = [blob.name for blob in blobs]
    return file_names

def main(event=None, context=None):
    """
    Processes a new file in the trigger bucket and lists files from the input bucket.
    Writes the list of files to the output bucket.
    """
    try:
        logging.info("Function triggered by new file in bucket: {}".format(TRIGGER_BUCKET_NAME))
        if event and context:
            file = event
            bucket_name = file['bucket']
            file_name = file['name']
        else:
            # Assume no event, it's a cloud run instance, so it will be called on start-up
            # In this case, we get a list of files in the bucket
            logging.info("Function triggered by request or Cloud Run instance, not by a new file event.")
            
            # Initialize Google Cloud Storage client
            storage_client = storage.Client()
            
            #list files in the input bucket
            file_list = list_files_in_bucket(INPUT_BUCKET_NAME, "path/to/directory")
            logging.info(f"List of files from directory: {file_list}")
            # Create a temporary file to store the downloaded content
            with tempfile.NamedTemporaryFile(mode="w+b", delete=False) as temp_file:
                # Download FASTA file to the temporary file
                storage_client = storage.Client()
                output_bucket = storage_client.bucket(OUTPUT_BUCKET_NAME)
                output_blob = output_bucket.blob("file_list.txt")
                output_blob.upload_from_string(str(file_list))
                logging.info(f"Results written to file: file_list.txt in bucket {OUTPUT_BUCKET_NAME}")
        

        if file_name:
            # Process triggered by a new file in the trigger bucket
            # Check if the event is for a new file in the correct bucket.
            if bucket_name != TRIGGER_BUCKET_NAME:
                logging.info(f"Skipping file {file_name} in bucket {bucket_name} - not the input bucket.")
                return
            
            logging.info(f"Processing file {file_name} from bucket {bucket_name}.")
            
            # Initialize Google Cloud Storage client
            storage_client = storage.Client()
            trigger_bucket = storage_client.bucket(TRIGGER_BUCKET_NAME)
            blob = trigger_bucket.blob(file_name)
            
            # Create a temporary file to store the downloaded content
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

    except Exception as err:
        logging.error(f"An error occurred: {err}") 

if __name__ == "__main__":
    # Run the main function if this file is the entry point, using flask
    app = Flask(__name__)
    with app.app_context():
        #the main is run when app is started
        main()
    app.run(host="0.0.0.0", port=8080, debug=False)