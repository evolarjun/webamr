import os
import subprocess
import shutil
from flask import Flask, send_file, request, jsonify, render_template, send_from_directory
import logging
import sys
import re
import uuid
import time
from datetime import datetime, timedelta
from google.cloud import storage, pubsub_v1
from werkzeug.utils import secure_filename

# set values to environment variables else listed here
UPLOAD_FOLDER_BASE = os.environ.get('UPLOAD_FOLDER_BASE', 'uploads')
RESULTS_FOLDER_BASE = os.environ.get('RESULTS_FOLDER_BASE', 'results')
# base bucket name for app
BUCKET_NAME = os.environ.get('BUCKET_NAME', 'webamr')
PROJECT_ID = os.environ.get('PROJECT_ID', 'amrfinder')
TOPIC_ID = os.environ.get('TOPIC_ID', 'eventarc-us-east1-webamr-trigger-838')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', 'webamr-output')

#app_dir = os.path.dirname(os.path.abspath(__file__))
#amrfinder_path = os.path.join(app_dir, 'bin', 'amrfinder')
amrfinder_path = 'amrfinder'

app = Flask(__name__, static_folder='static')
app.config['UPLOAD_FOLDER_BASE'] = UPLOAD_FOLDER_BASE
logging.basicConfig(level=logging.INFO)

def generate_user_id():
    return str(uuid.uuid4())

def read_file(filename):
  """Reads the contents of a file.

  Args:
    filename: The name of the file to read.

  Returns:
    The contents of the file as a string.
  """
#   try:
  with open(filename, 'r') as file:  # 'r' mode for reading
    contents = file.read()
  return contents
#   except FileNotFoundError:
#     return "File not found."

def tabulize(tab_delimited):
    """Converts a tab-delimited string into an HTML table.

    Args: 
        tab_delimited: A string containing the tab-delimited data.

    Returns:
        A string containing the HTML table.
    """
    lines = tab_delimited.decode('utf-8').strip().split('\n')
    headers = lines[0].split('\t')
    rows = [line.split('\t') for line in lines[1:]]
    html = '<table><thead><tr>'
    for header in headers:
        html += f'<th>{header}</th>\n' 
    html += '</tr></thead><tbody>\n'
    for row in rows:
        html += '<tr>'
        for cell in row:
            html += f'<td>{cell}</td>'
        html += '</tr>\n'
    html += '</tbody></table>'
    return html
   
def organism_select():
    """Reads the file taxgroup.tsv and returns select element text of the first column from this file

    Returns:
        A string containing the HTML select element.
    """
    taxgroup_file = read_file("taxgroup.tsv")
    lines = taxgroup_file.strip().split('\n')
    #print(lines)
    options = [f'<option value="{line.split()[0]}">{line.split()[0]}</option>' for line in lines[1:]]
    return '\n'.join(options)

def upload_to_gcs(bucket_name, source_file_name, destination_blob_name):
    """Uploads a file to the bucket."""
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(source_file_name)

def send_pubsub_message(message):
    """Sends a message to the Pub/Sub topic"""
    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

    data = message.encode("utf-8")
    future = publisher.publish(topic_path, data)
    try:
        print(f"Published message ID: {future.result()}")
    except Exception as e:
        print(f"Error publishing message: {e}")

# Cache the database version to avoid fetching it on every page load
cached_db_version = None

@app.route("/")
def index():
    global cached_db_version
    organism_select_options = organism_select()
    
    if not cached_db_version:
        try:
            storage_client = storage.Client(project=PROJECT_ID)
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            blob = bucket.blob("config/database_version.txt")
            if blob.exists():
                cached_db_version = blob.download_as_string().decode('utf-8').strip()
            else:
                cached_db_version = "Pending (Worker starting up...)"
        except Exception as e:
            print(f"Error fetching DB version: {e}")
            cached_db_version = "Unknown"

    return render_template('index.html', organism_select=organism_select_options, 
        database_version=cached_db_version)

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', url=request.url), 404

@app.route('/analyze', methods=['POST'])
def analyze_file():
    if 'nuc_file' not in request.files and 'prot_file' not in request.files:
        return jsonify({'error': 'No nucleotide or protein file provided.'}), 400
    nuc_file = request.files.get('nuc_file')
    prot_file = request.files.get('prot_file')
    gff_file = request.files.get('gff_file')

    user_id = generate_user_id()
    upload_folder = os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id)
    os.makedirs(upload_folder, exist_ok=True)

    if nuc_file and nuc_file.filename == '':
        return jsonify({'error': 'No nucleotide file selected'}), 400
    if prot_file and prot_file.filename == '':
        return jsonify({'error': 'No protein file selected'}), 400

    # Basic command structure
    command = [amrfinder_path, "-o", upload_folder + "/output.amrfinder"]  

    # Check for organism value
    if 'organism' in request.form:
        organism_value = request.form['organism']
        if organism_value != "" and organism_value != 'None':  
            organism_value = re.sub(r'[^A-Za-z0-9_]', '', organism_value)
            print(f"Organism selected: {organism_value}")
            command.extend(["--organism", organism_value])  # Add -t option if organism is selected
        else:
            print("No organism selected.")
    else:
        print("Organism not found in form data.")
    print("Now saving files")
    # save files
    if nuc_file: 
        filename = secure_filename(nuc_file.filename)
        filepath = os.path.join(upload_folder, filename)
        nuc_file.save(filepath)
        command.extend(["-n", filepath])
    if prot_file:
        filename = secure_filename(prot_file.filename)
        filepath = os.path.join(upload_folder, filename)
        prot_file.save(filepath)
        command.extend(["-p", filepath])
    if gff_file:
        filename = secure_filename(gff_file.filename)
        filepath = os.path.join(upload_folder, filename)
        gff_file.save(filepath)
        command.extend(["-g", filepath])

    # Write the command to a text file
    command_file_path = os.path.join(upload_folder, "command.txt")
    with open(command_file_path, "w") as command_file:
        command_file.write(" ".join(command))
    print("Now uploading files to bucket")
    # Upload files and command to GCS
    for filename in os.listdir(upload_folder):
        source_path = os.path.join(upload_folder, filename)
        destination_path = os.path.join(user_id, filename)  # Use user_id as prefix in GCS
        upload_to_gcs(BUCKET_NAME, source_path, destination_path)
        print(f"Uploaded {source_path} to gs://{BUCKET_NAME}/{destination_path}")

    print ("Now sending pubsub message")
    # Trigger analysis via pubsub message
    send_pubsub_message('{"submission_id":"' + user_id + '"}')
        # Now you can trigger your analysis on GCS using the uploaded files
        # and the command.txt file.

    # For now, return success and user_id
    return jsonify({'result': "Files uploaded successfully. Analysis will begin shortly.", 'user_id': user_id}), 200

@app.route('/get-results/<user_id>', methods=['GET'])
def return_results(user_id):
    """Returns the results of the analysis if they're availble"""
    # check for availability of the files in the cloud storage bucket webamr-output
    storage_client = storage.Client(project=PROJECT_ID)
    bucket = storage_client.bucket(OUTPUT_BUCKET_NAME)
    blob = bucket.blob(f'{user_id}/output.amrfinder')
    if blob.exists():
        print("File exists")
        # grab the output for the web page
        results = tabulize(blob.download_as_string())

        return jsonify({'result': results, 'user_id': user_id}), 200
    else:
        return '', 204


def run_amrfinder(command):
    """ Runs amrfinder and returns the output"""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        message = "Files analyzed successfully with command:<br />\n<pre>" + ' '.join(command) + "</pre><br />\n"
        output_filepath = os.path.join(upload_folder, "output.amrfinder")
        # print(message)
        message += tabulize(read_file(output_filepath))
        return jsonify({'result': message, 'user_id': user_id}), 200  # Include user_id in response
    except:
        return jsonify({'result': 'error: Requires a nucleotide or protein file.', 'user_id': user_id}), 400 # Include user_id in response

@app.route('/output/<user_id>')
def output(user_id):
    output_filepath = os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id, "output.amrfinder")
    # if the file doesn't exist return an error
    if not os.path.exists(output_filepath):
        return jsonify({'error': 'AMRFinderPlus output file is no longer available.'}), 404
    return send_file(output_filepath, as_attachment=True), 200
    #shutil.rmtree(os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id), ignore_errors=True)  # Remove user directory

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

# currently this does not run ever. Mostly created by AI and left for future reference.
# may not work
def cleanup_uploads():
    """Deletes files and directories older than 24 hours from the uploads directory."""
    now = datetime.now()
    upload_dir = app.config['UPLOAD_FOLDER_BASE']
    for filename in os.listdir(upload_dir):
        filepath = os.path.join(upload_dir, filename)
        if os.path.isdir(filepath):  # Check if it's a directory
            try:
                creation_time = datetime.fromtimestamp(os.path.getctime(filepath))
                if now - creation_time > timedelta(hours=24):
                    shutil.rmtree(filepath, ignore_errors=True)
                    logging.info(f"Deleted directory: {filepath}")
            except OSError as e:
                logging.error(f"Error deleting directory {filepath}: {e}")
        elif os.path.isfile(filepath):
            # (Optional) Add file deletion logic if needed.
            pass


def main():
    app.debug = True
    app.run(port=int(os.environ.get('PORT', 8080)))

if __name__ == "__main__":
    main()

# For testing:
# python -m venv .venv && source .venv/bin/activate
# python -m flask --app main run -p 9003
