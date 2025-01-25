import os
import json
import base64
import datetime
import random
import string
from google.cloud import storage

from flask import Flask, request

app = Flask(__name__)

def generate_random_string(length=5):
  """Generates a random string of specified length."""
  letters = string.ascii_lowercase
  return ''.join(random.choice(letters) for i in range(length))

def log_message(prefix, message):
    """Logs a message to a cloud storage file."""
    # I can't figure out the logging, so I'm going to try logging to a cloud storage file
    now = datetime.datetime.now()
    formatted_date_time = now.strftime("%Y-%m-%d-%H:%M:%S")
    print(formatted_date_time)        
    bucket = 'webamr-trigger'
    blob_name = f'{prefix}-{formatted_date_time}-' + generate_random_string()
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(message)


@app.route('/', methods=['POST','GET'])
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
#        log_message('data', str(request.data) + "\n\ndata=" + data
#            + "\n\n" + str(base64.b64decode(data)))

        data = base64.b64decode(data).decode('utf-8')
        log_message('data2', str(request.data) + "\n\ndecoded data=" + data)
        
        return ('', 204)
    except Exception as e:
        print(f"An error occurred: {e}")
        return f"Internal Server Error: {e}", 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


