import os
import subprocess
import shutil
from flask import Flask, send_file, request, jsonify, render_template
import logging
import sys
import re
import uuid
import time
from datetime import datetime, timedelta

UPLOAD_FOLDER_BASE = os.environ.get('UPLOAD_FOLDER_BASE', 'uploads')
app_dir = os.path.dirname(os.path.abspath(__file__))
amrfinder_path = os.path.join(app_dir, 'bin', 'amrfinder')

app = Flask(__name__)
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
    lines = tab_delimited.strip().split('\n')
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
    """Reads the file src/data/taxgroup.tab and returns select element text of the first column from this file

    Returns:
        A string containing the HTML select element.
    """
    taxgroup_file = read_file("bin/data/latest/taxgroup.tsv")
    lines = taxgroup_file.strip().split('\n')
    #print(lines)
    options = [f'<option value="{line.split()[0]}">{line.split()[0]}</option>' for line in lines[1:]]
    return '\n'.join(options)


@app.route("/")
#@app.route("/", methods=["post"])
def index():
    #organism_select = organism_select()

    organism_select_options = organism_select()
    amrfinder_version = read_file('bin/amrfinder_version.txt')
    # print("options: " + organism_select_options + "\n\n")
    return render_template('index.html', organism_select=organism_select_options, 
        amrfinder_version = amrfinder_version)

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

    command = [amrfinder_path, "-o", upload_folder + "/output.amrfinder"]  # Basic command structure

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

    sys.stderr.write(f"Directory created: {upload_folder}\n")  # New print statement

    # save files
    if nuc_file: 
        filepath = os.path.join(upload_folder, nuc_file.filename)
        nuc_file.save(filepath)
        command.extend(["-n", filepath])
    if prot_file:
        filepath = os.path.join(upload_folder, prot_file.filename)
        prot_file.save(filepath)
        command.extend(["-p", filepath])
    if gff_file:
        filepath = os.path.join(upload_folder, gff_file.filename)
        gff_file.save(filepath)
        command.extend(["-g", filepath])

    # Run the program (should have more error checking here)
    if nuc_file or prot_file:
        # print("ready to run")
        command.extend(['--plus'])
        # Execute amrfinder command
        print(f"Executing command: {' '.join(command)}")  # Print the full command
        # print(f"File path: {upload_folder}/output.amrfinder")  # Print the file path
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            # print(result.stderr)
            # print(result.stdout) 
        except subprocess.CalledProcessError as e:
            error_message = f"amrfinder execution failed with return code {e.returncode}: \n{e.stderr}"
            print(error_message)  # Print error to console
            return jsonify({'result': "error: " + error_message}), 500
        except Exception as e:
            error_message = f"An error occurred: {e}"
            print(error_message)  # Print error to console
            return jsonify({'result': "error: " + error_message}), 500
        
        message = "Files analyzed successfully with command:<br />\n<pre>" + ' '.join(command) + "</pre><br />\n"
        output_filepath = os.path.join(upload_folder, "output.amrfinder")
        # print(message)
        message += tabulize(read_file(output_filepath))
        return jsonify({'result': message, 'user_id': user_id}), 200  # Include user_id in response
    else:
        return jsonify({'result': 'error: Requires a nucleotide or protein file.', 'user_id': user_id}), 400 # Include user_id in response

@app.route('/output/<user_id>')
def output(user_id):
    output_filepath = os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id, "output.amrfinder")
    # if the file doesn't exist return an error
    if not os.path.exists(output_filepath):
        return jsonify({'error': 'AMRFinderPlus output file is no longer available.'}), 404
    return send_file(output_filepath, as_attachment=True), 200
    #shutil.rmtree(os.path.join(app.config['UPLOAD_FOLDER_BASE'], user_id), ignore_errors=True)  # Remove user directory

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
    app.run(port=int(os.environ.get('PORT', 80)))

if __name__ == "__main__":
    main()
