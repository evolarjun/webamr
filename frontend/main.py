import os
import io
import traceback
from flask import Flask, send_file, request, jsonify, render_template, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
import re
import uuid
import json
from datetime import datetime, timedelta
from google.cloud import storage, pubsub_v1, firestore
from werkzeug.utils import secure_filename
from datetime import timezone

# set values to environment variables else listed here
UPLOAD_FOLDER_BASE = os.environ.get('UPLOAD_FOLDER_BASE', 'uploads')
RESULTS_FOLDER_BASE = os.environ.get('RESULTS_FOLDER_BASE', 'results')
# base bucket name for app
PROJECT_ID = os.environ.get('PROJECT_ID', 'amrfinder')
BUCKET_NAME = os.environ.get('BUCKET_NAME', f'amr-input-bucket-{PROJECT_ID}')
TOPIC_ID = os.environ.get('TOPIC_ID', 'amr-jobs-topic')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', f'amr-output-bucket-{PROJECT_ID}')

#app_dir = os.path.dirname(os.path.abspath(__file__))
#amrfinder_path = os.path.join(app_dir, 'bin', 'amrfinder')
amrfinder_path = 'amrfinder'

_storage_client = None
_firestore_client = None
_publisher = None

def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client

def get_firestore_client():
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=PROJECT_ID)
    return _firestore_client

def get_publisher():
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher

app = Flask(__name__, static_folder='static')
app.config['UPLOAD_FOLDER_BASE'] = UPLOAD_FOLDER_BASE
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB limit
logging.basicConfig(level=logging.INFO)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],      # no global limit — only apply where decorated
    storage_uri="memory://",
)

@app.errorhandler(429)
def ratelimit_error(e):
    return jsonify(error="Rate limit exceeded. Please wait a minute before submitting another job."), 429

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
    if not tab_delimited:
        return ""
    lines = tab_delimited.decode('utf-8').strip().split('\n')
    if not lines or not lines[0]:
        return ""
        
    headers = lines[0].split('\t')
    
    # Identify Hierarchy node column for special linking
    hierarchy_node_idx = -1
    for i, h in enumerate(headers):
        if h.strip().lower() == "hierarchy node":
            hierarchy_node_idx = i
            break

    rows = [line.split('\t') for line in lines[1:]]
    html = '<table><thead><tr>'
    for header in headers:
        html += f'<th>{header}</th>\n' 
    html += '</tr></thead><tbody>\n'
    for row in rows:
        html += '<tr>'
        for i, cell in enumerate(row):
            content = cell
            # Only link if it's the Hierarchy node column and has a valid-looking value
            if i == hierarchy_node_idx and cell.strip() and cell.strip().lower() != "n/a":
                node_id = cell.strip()
                content = f'<a href="https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/#node_id:{node_id}" target="_blank">{node_id}</a>'
            html += f'<td>{content}</td>'
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
    storage_client = get_storage_client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_filename(source_file_name)

def send_pubsub_message(message):
    """Sends a message to the Pub/Sub topic"""
    publisher = get_publisher()
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

    data = message.encode("utf-8")
    future = publisher.publish(topic_path, data)
    try:
        print(f"Published message ID: {future.result()}")
    except Exception as e:
        print(f"Error publishing message: {e}")

# Cache the database version to avoid fetching it on every page load
cached_db_version = None
cached_software_version = None

@app.route("/")
def index():
    # print("Inside index!")
    global cached_db_version, cached_software_version
    organism_select_options = organism_select()
    
    if not cached_db_version:
        try:
            storage_client = get_storage_client()
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            blob = bucket.blob("config/database_version.txt")
            if blob.exists():
                cached_db_version = blob.download_as_string().decode('utf-8').strip()
            else:
                return render_template('index.html', organism_select=organism_select_options, 
                    database_version="Queued (Worker starting up...)", 
                    software_version=cached_software_version or "Queued (Worker starting up...)")
        except Exception as e:
            print(f"Error fetching DB version: {e}")
            # Don't cache error/unknown so we can retry
            db_v = "Unknown"
        else:
            db_v = cached_db_version
    else:
        db_v = cached_db_version

    if not cached_software_version:
        try:
            storage_client = get_storage_client()
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            blob = bucket.blob("config/software_version.txt")
            if blob.exists():
                cached_software_version = blob.download_as_string().decode('utf-8').strip()
            else:
                return render_template('index.html', organism_select=organism_select_options, 
                    database_version=db_v, 
                    software_version="Queued (Worker starting up...)")
        except Exception as e:
            print(f"Error fetching software version: {e}")
            soft_v = "Unknown"
        else:
            soft_v = cached_software_version
    else:
        soft_v = cached_software_version

    return render_template('index.html', organism_select=organism_select_options, 
        database_version=db_v, software_version=soft_v)

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', url=request.url), 404

@app.route('/analyze', methods=['POST'])
@limiter.limit("5 per minute")
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
    command = [amrfinder_path, "--plus", "--print_node", "-o", upload_folder + "/output.amrfinder"]

    # Valid annotation formats as per amrfinder -h
    ALLOWED_ANNOTATION_FORMATS = {
        "bakta", "genbank", "microscope", "patric", "pgap", "prodigal",
        "prokka", "pseudomonasdb", "rast", "standard"
    }

    # Check for organism value
    if 'organism' in request.form:
        organism_value = request.form['organism']
        if organism_value != "" and organism_value != 'None':  
            # Strict sanitization: alphanumeric and underscores only
            organism_value = re.sub(r'[^A-Za-z0-9_]', '', organism_value)
            print(f"Organism selected: {organism_value}")
            command.extend(["--organism", organism_value])  
        else:
            print("No organism selected.")
            organism_value = None
    else:
        print("Organism not found in form data.")
        organism_value = None

    annotation_format = request.form.get('annotation_format', 'standard').strip()
    if annotation_format not in ALLOWED_ANNOTATION_FORMATS:
        print(f"Invalid annotation format: {annotation_format}. Defaulting to standard.")
        annotation_format = "standard"
    
    command.extend(["--annotation_format", annotation_format])
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
    try:
        print("Now uploading files to bucket")
        # Upload files and command to GCS
        for filename in os.listdir(upload_folder):
            source_path = os.path.join(upload_folder, filename)
            destination_path = os.path.join(user_id, filename)  # Use user_id as prefix in GCS
            upload_to_gcs(BUCKET_NAME, source_path, destination_path)
            print(f"Uploaded {source_path} to gs://{BUCKET_NAME}/{destination_path}")

        print ("Now sending pubsub message")
        
        # Determine the primary upload file for the worker's processing
        main_filename = ""
        if nuc_file:
            main_filename = secure_filename(nuc_file.filename)
        elif prot_file:
            main_filename = secure_filename(prot_file.filename)
            
        gcs_uri = f"gs://{BUCKET_NAME}/{user_id}/{main_filename}"
        
        params = {"print_node": True, "plus_flag": True, "annotation_format": annotation_format}
        if organism_value:
            params["organism"] = organism_value

        # Calculate file sizes to write to DB
        nuc_size = os.path.getsize(os.path.join(upload_folder, secure_filename(nuc_file.filename))) if nuc_file else 0
        prot_size = os.path.getsize(os.path.join(upload_folder, secure_filename(prot_file.filename))) if prot_file else 0
        gff_size = os.path.getsize(os.path.join(upload_folder, secure_filename(gff_file.filename))) if gff_file else 0

        total_file_size_bytes = nuc_size + prot_size + gff_size

        # Capture IP address for analytics
        client_ip = get_remote_address()

        # 1. Update DB state to queued
        db = get_firestore_client()
        doc_ref = db.collection("amr_jobs").document(user_id)
        doc_ref.set({
            "job_id": user_id,
            "status": "Queued",
            "gcs_uri": gcs_uri,
            "parameters": params,
            "result_uri": None,
            "error_message": None,
            "created_at": datetime.now(timezone.utc),
            "expire_at": datetime.now(timezone.utc) + timedelta(days=90),
            "total_file_size_bytes": total_file_size_bytes,
            "nuc_file_size_bytes": nuc_size,
            "prot_file_size_bytes": prot_size,
            "gff_file_size_bytes": gff_size,
            "ip_address": client_ip
        })

        # 2. Trigger analysis via pubsub message (matching worker's payload expectations)
        message_data = {
            "job_id": user_id,
            "gcs_uri": gcs_uri,
            "parameters": params
        }
        send_pubsub_message(json.dumps(message_data))

        results_url = f"/results/{user_id}"
        # Return success with user_id and a shareable results URL
        return jsonify({
            'result': "Files uploaded successfully. Analysis will begin shortly.",
            'user_id': user_id,
            'results_url': results_url,
        }), 200
    except Exception as e:
        print(f"Server Error in analyze_file: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': f"Failed to submit job: {str(e)}"}), 500


@app.route('/results/<job_id>')
def results_page(job_id):
    """Shareable results page for a specific job."""
    try:
        db = get_firestore_client()
        doc = db.collection("amr_jobs").document(job_id).get()
    except Exception as e:
        print(f"Error fetching job from Firestore: {e}")
        return render_template('404.html', url=request.url), 404

    if not doc.exists:
        return render_template('404.html', url=request.url), 404

    job_data = doc.to_dict()
    status = job_data.get("status", "Unknown")
    error_message = job_data.get("error_message", "")
    
    result_html = None
    stderr_available = False
    
    if status == "Completed":
        try:
            storage_client = get_storage_client()
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            blob = bucket.blob(f'results/{job_id}.tsv')
            if blob.exists():
                result_html = tabulize(blob.download_as_bytes())
            stderr_available = bucket.blob(f'results/{job_id}_stderr.txt').exists()
        except Exception as e:
            print(f"Error fetching results from GCS for completed job {job_id}: {e}")

    return render_template(
        'results.html',
        job_id=job_id,
        status=status,
        error_message=error_message,
        result_html=result_html,
        stderr_available=stderr_available,
        created_at=job_data.get("created_at").isoformat() if job_data.get("created_at") else None
    )


@app.route('/get-results/<user_id>', methods=['GET'])
def return_results(user_id):
    """Returns the results of the analysis if they're available"""
    storage_client = get_storage_client()
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(f'results/{user_id}.tsv')
    stderr_available = bool(bucket.blob(f'results/{user_id}_stderr.txt').exists())
    if blob.exists():
        print("File exists")
        results = tabulize(blob.download_as_bytes())
        return jsonify({'result': results, 'user_id': user_id, 'stderr_available': stderr_available}), 200
    else:
        # Check if the job failed in Firestore
        try:
            db = get_firestore_client()
            doc = db.collection("amr_jobs").document(user_id).get()
            if doc.exists and doc.to_dict().get("status") == "Failed":
                error_msg = doc.to_dict().get("error_message", "Unknown error")
                return jsonify({'error': f"Analysis failed: {error_msg}", 'stderr_available': stderr_available}), 500
        except Exception as e:
            print(f"Error checking Firestore: {e}")

        return '', 204




@app.route('/output/<user_id>')
def output(user_id):
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(OUTPUT_BUCKET)
        blob = bucket.blob(f'results/{user_id}.tsv')
        
        if not blob.exists():
            return jsonify({'error': 'AMRFinderPlus output file is no longer available.'}), 404
            
        file_bytes = blob.download_as_bytes()
        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=f"amrfinder_{user_id}.tsv",
            mimetype="text/tab-separated-values"
        ), 200
    except Exception as e:
        print(f"Error serving output: {e}")
        return jsonify({'error': 'Failed to retrieve output file.'}), 500


@app.route('/stderr/<user_id>')
def stderr_output(user_id):
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(OUTPUT_BUCKET)
        blob = bucket.blob(f'results/{user_id}_stderr.txt')

        if not blob.exists():
            return jsonify({'error': 'AMRFinderPlus stderr log is no longer available.'}), 404

        file_bytes = blob.download_as_bytes()
        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=f"amrfinder_{user_id}_stderr.txt",
            mimetype="text/plain"
        ), 200
    except Exception as e:
        print(f"Error serving stderr: {e}")
        return jsonify({'error': 'Failed to retrieve stderr log.'}), 500

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
