import os
import json
import base64
import datetime
import random
import string
from typing import Optional
from google.cloud import storage, pubsub_v1
from flask import Flask, request

app = Flask(__name__)

subscription_path = 'projects/amrfinder/topics/eventarc-us-east1-webamr-trigger-838'
timestamp_filename = 'processing_started_at'
input_bucket = 'webamr'
output_bucket = 'webamr-output'
RETRY_TIME = 900 

subscriber = pubsub_v1.SubscriberClient()
# 900 seconds = 15 minutes. If an existing job was started > this time ago, try again

def generate_random_string(length=5):
  """Generates a random string of specified length."""
  letters = string.ascii_lowercase
  return ''.join(random.choice(letters) for i in range(length))


def get_file_from_gcs(bucket_name: str, blob_name: str) -> Optional[str]:
    """
    Retrieves the content of a file from Google Cloud Storage.

    Args:
        bucket_name: The name of the bucket.
        blob_name: The name of the blob (file) within the bucket.

    Returns:
        The content of the file as a string if the file exists, otherwise None.
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    if blob.exists():
        return blob.download_as_text()
    return None

def log_message(prefix, message):
    """Logs a message to a cloud storage file."""
    # I can't figure out the logging, so I'm going to try logging to a cloud storage file
    now = datetime.datetime.now()
    formatted_date_time = now.strftime("%Y-%m-%d-%H:%M:%S")
    print(formatted_date_time + f'Logging to {prefix}-')
    bucket = 'webamr-trigger'
    blob_name = f'{prefix}-{formatted_date_time}-' + generate_random_string()
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(message)

def read_file(file_path):
  """Reads the contents of a file into a variable.

  Args:
    file_path: The path to the file.

  Returns:
    The contents of the file as a string.
  """
  try:
    with open(file_path, 'r') as file:
      file_contents = file.read()
    return file_contents
  except FileNotFoundError:
    print(f"File not found: {file_path}")
    return None  # Or handle the error as needed


def copy_from_gcs(userid):
  """Copies files from a GCS bucket (webamr) to a local directory (uploads/)."""

  client = storage.Client()
  bucket_name = 'webamr'
  source_blob_name = userid  # The folder in the bucket
  destination_directory = f'uploads/{userid}'

  bucket = client.bucket(bucket_name)
  blobs = bucket.list_blobs(prefix=source_blob_name)  # Get all files in the folder

  for blob in blobs:
    destination_file_name = blob.name.replace(source_blob_name, destination_directory, 1) 
    blob.download_to_filename(destination_file_name) 
    print(f'Copied gs://{bucket_name}/{blob.name} to {destination_file_name}')

def copy_to_gcs(userid):
  """Copies files from the local uploads/ directory to the GCS bucket webamr-output"""
  client = storage.Client()
  bucket_name = 'webamr-output'
  source_directory = f'uploads/{userid}'
  destination_directory = userid  # The folder in the bucket

  bucket = client.bucket(bucket_name)
  # get a list of files in the source directory
  #files_to_copy = [f for f in os.listdir(source_directory) if os.path.isfile(os.path.join(source_directory, f))]
  files_to_copy = [
    os.path.join(source_directory, 'output.amrfinder'),
    os.path.join(source_directory, 'command.txt')]
  print(f'Copy {files_to_copy} to gs://{bucket_name}/{destination_directory}')
  # copy each file to the destination directory
  for file in files_to_copy:
    #destination_blob_name = blob.name.replace(source_directory, destination_blob_name, 1)
    destination = file.replace(source_directory, userid, 1)
    blob = bucket.blob(destination)
    print(f'Copied {file} to gs://{bucket_name}/{destination}')
    blob.upload_from_filename(file)

def started_processing_at(submission_id):
  """
  Check for an already processing run
    File with timestamp created in
    gs://webamr-results/submission_id/processing_started_at contains the
    time.time() value.
  If one does not exist return 0
  """
  blob_name = f'{submission_id}/{timestamp_filename}'
  last_run = get_file_from_gcs(output_bucket, blob_name)
  if (last_run is None):
    return 0
  else:
    return(float(last_run))

def run_amrfinder(data):
    """
    Grabs data from gs://webamr/<userid>, runs amrfinder on that data, then
    copies the output to gs://webamr-output/<userid>
    """
    # get userid
    print(data)
    data = json.loads(data)
    userid = data.get("submission_id")
    # write utime to file to indicate we've started processing
    now = datetime.datetime.now().timestamp()
    storage_client = storage.Client()
    bucket = storage_client.bucket(output_bucket)
    blob = bucket.blob(f'{userid}/{timestamp_filename}')
    blob.upload_from_string(datetime.datetime.now().timestamp())

    # make local tmp data directory if it doesn't exist
    os.system(f'mkdir -p uploads/{userid}')
    # copy from cloud storage to tmp directory
    copy_from_gcs(userid)
    # run AMRFinderPlus
    command = "bin/" + read_file(f'uploads/{userid}/command.txt') 
    print(f'### Run {command}')
    rv = os.system(command)
    print("### AMRFinderPlus run completed with exit value: " + str(rv))
    copy_to_gcs(userid)


app.route('/', methods=['POST','GET'])
def hello_pubsub():
    """
    Cloud Run application triggered by a Cloud Pub/Sub message that creates a file on Google Cloud Storage.
    Copies the message from the subscription "webamr-submitted" in the project "amrfinder" to a file 
    named for the message_id in the bucket "gs://webamr-trigger".
    """

    # Added to handle GET requests because maybe that's why cloud run is failing?
    if request.method == 'GET':
        return "This is a GET request"
    try:
        print("Got here request-method is POST")
        envelope = request.get_json()
 #       log_message('envelope', "POST received with\n" + str(request.data) + "\n\n"
 #           + "envelope=" + str() + "\n\n")
        if not envelope:
            msg = 'no Pub/Sub message received'
            print(f'error: {msg}')
            return f'Bad Request: {msg}', 400
        if not isinstance(envelope, dict) or 'message' not in envelope:
            msg = 'invalid Pub/Sub message format'
            print(f'error: {msg}')
            return f'Bad Request: {msg}', 400
        pubsub_message = envelope['message']
        data = pubsub_message.get('data', '')
        if not data:
            msg = 'empty Pub/Sub message'
            print(f'error: {msg}')
            return f'Bad Request: {msg}', 400
        print("Got data from pubsub message")
        data = base64.b64decode(data).decode('utf-8')
        log_message('data', str(request.data) + "\n\ndecoded data=" + data)
        #print(f'we spawn a new thread and return so the message gets acknowledged')
        #thread = threading.Thread(target=run_amrfinder, args=(data,))
        #thread.start()
        started_time = started_processing_at(data.get("submission_id"))
        if (current_time - started_time < RETRY_TIME):
            return("Already processing", 200)
        else:
            run_amrfinder(data)
        log_message('after-amr', "Ran AMRFinderPlus in thread and returned from subroutine")
        return ('', 204)
    except Exception as e:
        print(f"An error occurred: {e}")
        return f"Internal Server Error: {e}", 500

### End
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
