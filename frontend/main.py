import os
import io
import traceback
from flask import Flask, send_file, request, jsonify, render_template, send_from_directory
from markupsafe import escape
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
import re
import uuid
import json
import shutil
from datetime import datetime, timedelta
from google.cloud import storage, pubsub_v1, firestore
from google.cloud.exceptions import NotFound
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

try:
    with open('VERSION.txt', 'r') as f:
        APP_VERSION = f.read().strip()
except FileNotFoundError:
    APP_VERSION = "unknown"

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
    with open(filename, 'r') as file:
        contents = file.read()
    return contents

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
        html += f'<th>{escape(header)}</th>\n'
    html += '</tr></thead><tbody>\n'
    for row in rows:
        html += '<tr>'
        for i, cell in enumerate(row):
            # Only link if it's the Hierarchy node column and has a valid-looking value
            if i == hierarchy_node_idx and cell.strip() and cell.strip().lower() != "n/a":
                node_id = cell.strip()
                escaped_node_id = escape(node_id)
                content = f'<a href="https://www.ncbi.nlm.nih.gov/pathogens/genehierarchy/#node_id:{escaped_node_id}" target="_blank">{escaped_node_id}</a>'
            else:
                content = escape(cell)
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

@app.context_processor
def inject_version():
    return dict(frontend_version=APP_VERSION)

@app.route('/version')
def version_info():
    return jsonify({"frontend_version": APP_VERSION})

@app.route("/")
def index():
    # print("Inside index!")
    global cached_db_version, cached_software_version
    organism_select_options = organism_select()
    
    if not cached_db_version or cached_software_version:
        try:
            storage_client = get_storage_client()
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            try:
                blob = bucket.blob("config/database_version.txt")
                cached_db_version = blob.download_as_string().decode('utf-8').strip()
                db_v = cached_db_version
                blob = bucket.blob("config/software_version.txt")
                cached_software_version = blob.download_as_string().decode('utf-8').strip()
                soft_v = cached_software_version
            except NotFound:
                return render_template('index.html', 
                                       organism_select=organism_select_options, 
                    database_version="Run job to refresh", 
                    software_version="Run job to refresh")
        except Exception as e:
            print(f"Error fetching DB version: {e}")
            # Don't cache error/unknown so we can retry
            db_v = "Error retrieving version"
            soft_v = "Error retrieving version"
    else:
        db_v = cached_db_version
        soft_v = cached_software_version
    return render_template('index.html', organism_select=organism_select_options, 
        database_version=db_v, software_version=soft_v)

@app.route("/docs")
def documentation():
    return render_template("documentation.html")

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', url=request.url), 404

def _validate_job_submission(request):
    """Validates the uploaded files and form data."""
    if 'nuc_file' not in request.files and 'prot_file' not in request.files:
        return ({'error': 'No nucleotide or protein file provided.'}, 400), None
    nuc_file = request.files.get('nuc_file')
    prot_file = request.files.get('prot_file')
    gff_file = request.files.get('gff_file')

    if nuc_file and nuc_file.filename == '':
        return ({'error': 'No nucleotide file selected'}, 400), None
    if prot_file and prot_file.filename == '':
        return ({'error': 'No protein file selected'}, 400), None

    if nuc_file and nuc_file.filename != '' and prot_file and prot_file.filename != '':
        if not gff_file or gff_file.filename == '':
            return ({'error': 'A GFF file is required when providing both nucleotide and protein files.'}, 400), None

    if gff_file and gff_file.filename != '':
        if not prot_file or prot_file.filename == '':
            return ({'error': 'A protein file is required when providing a GFF file.'}, 400), None

    raw_job_name = request.form.get("job_name", "")
    job_name = raw_job_name.strip()
    if job_name:
        if len(job_name) > 100:
            return ({'error': 'Job name must be 100 characters or fewer.'}, 400), None
        if not re.fullmatch(r"[A-Za-z0-9 _-]+", job_name):
            return ({'error': 'Job name can only contain letters, numbers, spaces, underscores, and hyphens.'}, 400), None

    return None, {
        'nuc_file': nuc_file,
        'prot_file': prot_file,
        'gff_file': gff_file,
        'job_name': job_name if job_name else None
    }

def _save_and_upload_files(upload_folder, user_id, nuc_file, prot_file, gff_file):
    """Saves files locally, calculates their sizes, and uploads them to GCS."""
    sizes = {'nuc_size': 0, 'prot_size': 0, 'gff_size': 0}
    
    files_to_process = [
        (nuc_file, 'nuc_size'),
        (prot_file, 'prot_size'),
        (gff_file, 'gff_size')
    ]
    
    for file_obj, size_key in files_to_process:
        if file_obj and file_obj.filename:
            filename = secure_filename(file_obj.filename)
            filepath = os.path.join(upload_folder, filename)
            file_obj.save(filepath)
            sizes[size_key] = os.path.getsize(filepath)
            
            # Upload to GCS
            destination_path = os.path.join(user_id, filename)
            upload_to_gcs(BUCKET_NAME, filepath, destination_path)
            
    return sizes

def _create_firestore_record(user_id, job_name, gcs_uri, params, sizes, files, client_ip):
    """Creates the initial queued job record in Firestore."""
    db = get_firestore_client()
    doc_ref = db.collection("amr_jobs").document(user_id)
    
    total_size = sizes['nuc_size'] + sizes['prot_size'] + sizes['gff_size']
    
    doc_ref.set({
        "job_id": user_id,
        "job_name": job_name,
        "status": "Queued",
        "gcs_uri": gcs_uri,
        "parameters": params,
        "result_uri": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc),
        "expire_at": datetime.now(timezone.utc) + timedelta(days=90),
        "total_file_size_bytes": total_size,
        "nuc_file_size_bytes": sizes['nuc_size'],
        "prot_file_size_bytes": sizes['prot_size'],
        "gff_file_size_bytes": sizes['gff_size'],
        "nuc_filename": files['nuc_file'].filename if files['nuc_file'] else None,
        "prot_filename": files['prot_file'].filename if files['prot_file'] else None,
        "gff_filename": files['gff_file'].filename if files['gff_file'] else None,
        "ip_address": client_ip
    })


@app.route('/analyze', methods=['POST'])
@limiter.limit("5 per minute")
def analyze_file():
    """Endpoint to handle new AMRFinderPlus job submissions."""
    error_response, validated_data = _validate_job_submission(request)
    if error_response:
        return jsonify(error_response[0]), error_response[1]

    nuc_file = validated_data['nuc_file']
    prot_file = validated_data['prot_file']
    gff_file = validated_data['gff_file']
    job_name = validated_data['job_name']

    user_id = generate_user_id()
    upload_folder = os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id)
    os.makedirs(upload_folder, exist_ok=True)

    try:
        ALLOWED_ANNOTATION_FORMATS = {
            "bakta", "genbank", "microscope", "patric", "pgap", "prodigal",
            "prokka", "pseudomonasdb", "rast", "standard"
        }

        organism_value = None
        if 'organism' in request.form:
            form_org = request.form['organism']
            if form_org and form_org != 'None':  
                organism_value = re.sub(r'[^A-Za-z0-9_]', '', form_org)

        annotation_format = request.form.get('annotation_format', 'standard').strip()
        if annotation_format not in ALLOWED_ANNOTATION_FORMATS:
            annotation_format = "standard"

        sizes = _save_and_upload_files(upload_folder, user_id, nuc_file, prot_file, gff_file)

        main_filename = ""
        if nuc_file:
            main_filename = secure_filename(nuc_file.filename)
        elif prot_file:
            main_filename = secure_filename(prot_file.filename)
            
        gcs_uri = f"gs://{BUCKET_NAME}/{user_id}/{main_filename}"
        
        params = {
            "print_node": True, 
            "plus_flag": True, 
            "annotation_format": annotation_format,
            "has_nucleotide": bool(nuc_file),
            "has_protein": bool(prot_file)
        }
        if organism_value:
            params["organism"] = organism_value

        client_ip = get_remote_address()

        files_dict = {'nuc_file': nuc_file, 'prot_file': prot_file, 'gff_file': gff_file}
        _create_firestore_record(user_id, job_name, gcs_uri, params, sizes, files_dict, client_ip)

        message_data = {
            "job_id": user_id,
            "gcs_uri": gcs_uri,
            "parameters": params,
            "job_name": job_name,
            "nuc_filename": nuc_file.filename if nuc_file else None,
            "prot_filename": prot_file.filename if prot_file else None,
            "gff_filename": gff_file.filename if gff_file else None
        }
        send_pubsub_message(json.dumps(message_data))

        return jsonify({
            'result': "Files uploaded successfully. Analysis will begin shortly.",
            'user_id': user_id,
            'results_url': f"/results/{user_id}",
        }), 200
        
    except Exception as e:
        logging.error(f"Server Error in analyze_file: {str(e)}", exc_info=True)
        return jsonify({'error': f"Failed to submit job: {str(e)}"}), 500
        
    finally:
        if os.path.exists(upload_folder):
            try:
                shutil.rmtree(upload_folder, ignore_errors=True)
            except Exception as e:
                logging.error(f"Failed to clean up local upload folder {upload_folder}: {e}")


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
    nucleotide_available = False
    protein_available = False
    
    if status == "Completed":
        try:
            storage_client = get_storage_client()
            bucket = storage_client.bucket(OUTPUT_BUCKET)
            blob = bucket.blob(f'results/{job_id}/results.tsv')
            try:
                result_html = tabulize(blob.download_as_bytes())
            except NotFound:
                # Job completed but result files have been cleaned up
                status = "Expired"
            stderr_available = bucket.blob(f'results/{job_id}/stderr.txt').exists()
            nucleotide_available = bucket.blob(f'results/{job_id}/nucleotide.fna').exists()
            protein_available = bucket.blob(f'results/{job_id}/protein.faa').exists()
        except Exception as e:
            print(f"Error fetching results from GCS for completed job {job_id}: {e}")

    return render_template(
        'results.html',
        job_id=job_id,
        job_name=job_data.get("job_name"),
        status=status,
        error_message=error_message,
        nuc_filename=job_data.get("nuc_filename"),
        prot_filename=job_data.get("prot_filename"),
        gff_filename=job_data.get("gff_filename"),
        result_html=result_html,
        stderr_available=stderr_available,
        nucleotide_available=nucleotide_available,
        protein_available=protein_available,
        created_at=job_data.get("created_at").isoformat() if job_data.get("created_at") else None,
        worker_version=job_data.get("worker_version", "unknown")
    )


@app.route('/get-results/<user_id>', methods=['GET'])
def return_results(user_id):
    """Returns the results of the analysis if they're available"""
    storage_client = get_storage_client()
    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(f'results/{user_id}/results.tsv')
    stderr_available = bool(bucket.blob(f'results/{user_id}/stderr.txt').exists())
    nucleotide_available = bool(bucket.blob(f'results/{user_id}/nucleotide.fna').exists())
    protein_available = bool(bucket.blob(f'results/{user_id}/protein.faa').exists())
    try:
        results = tabulize(blob.download_as_bytes())
        
        # Fetch job metadata for additional status fields
        db = get_firestore_client()
        doc = db.collection("amr_jobs").document(user_id).get()
        job = doc.to_dict() if doc.exists else {}
        
        worker_version = job.get('worker_version', 'unknown')

        return jsonify({
            'result': results, 
            'user_id': user_id, 
            'stderr_available': stderr_available, 
            'nucleotide_available': nucleotide_available, 
            'protein_available': protein_available,
            'worker_version': worker_version
        }), 200
    except NotFound:
        # Check job status in Firestore
        try:
            db = get_firestore_client()
            doc = db.collection("amr_jobs").document(user_id).get()
            if doc.exists:
                job_data = doc.to_dict()
                status = job_data.get("status", "Queued")
                if status == "Failed":
                    error_msg = job_data.get("error_message", "Unknown error")
                    return jsonify({'error': f"Analysis failed: {error_msg}", 'stderr_available': stderr_available}), 500
                if status == "Completed":
                    # Job completed but result files have been cleaned up
                    return jsonify({'status': 'Expired'}), 200
                # Job is still pending (Queued or Processing)
                return jsonify({'status': status}), 200
        except Exception as e:
            print(f"Error checking Firestore in return_results: {e}")

        return '', 204




def _serve_gcs_result_file(user_id, filename, mimetype, as_attachment=False):
    """Helper to serve files from the GCS output bucket."""
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(OUTPUT_BUCKET)
        blob = bucket.blob(f'results/{user_id}/{filename}')

        try:
            file_bytes = blob.download_as_bytes()
        except NotFound:
            return jsonify({'error': f'AMRFinderPlus {filename} is no longer available.'}), 404

        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=as_attachment,
            download_name=secure_filename(filename) if as_attachment else None,
            mimetype=mimetype
        ), 200
    except Exception as e:
        logging.error(f"Error serving {filename}: {e}", exc_info=True)
        return jsonify({'error': f'Failed to retrieve {filename}.'}), 500

@app.route('/output/<user_id>')
def output(user_id):
    """Serves the results TSV file."""
    return _serve_gcs_result_file(user_id, 'results.tsv', 'text/plain')

@app.route('/stderr/<user_id>')
def stderr_output(user_id):
    """Serves the stderr text log."""
    return _serve_gcs_result_file(user_id, 'stderr.txt', 'text/plain')

@app.route('/nucleotide/<user_id>')
def nucleotide_output(user_id):
    """Serves the nucleotide FASTA output file."""
    return _serve_gcs_result_file(user_id, 'nucleotide.fna', 'text/plain')

@app.route('/protein/<user_id>')
def protein_output(user_id):
    """Serves the protein FASTA output file."""
    return _serve_gcs_result_file(user_id, 'protein.faa', 'text/plain')

@app.route('/input/<job_id>/<filename>')
def input_file(job_id, filename):
    """Serve an input file from the GCS input bucket."""
    safe_name = secure_filename(filename)
    if not safe_name:
        return jsonify({'error': 'Invalid filename.'}), 400
    try:
        storage_client = get_storage_client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(f'{job_id}/{safe_name}')

        try:
            file_bytes = blob.download_as_bytes()
        except NotFound:
            return jsonify({'error': 'Input file is no longer available.'}), 404

        return send_file(
            io.BytesIO(file_bytes),
            as_attachment=True,
            download_name=safe_name,
            mimetype="application/octet-stream"
        ), 200
    except Exception as e:
        print(f"Error serving input file: {e}")
        return jsonify({'error': 'Failed to retrieve input file.'}), 500

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.ico', mimetype='image/vnd.microsoft.icon')


def main():
    app.debug = True
    app.run(port=int(os.environ.get('PORT', 8080)))

if __name__ == "__main__":
    main()

# For testing:
# python -m venv .venv && source .venv/bin/activate
# python -m flask --app main run -p 9003
