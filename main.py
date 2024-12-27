import os
import subprocess
import shutil
from flask import Flask, send_file, request, jsonify, render_template
import logging

UPLOAD_FOLDER = 'uploads'
app_dir = os.path.dirname(os.path.abspath(__file__))
amrfinder_path = os.path.join(app_dir, 'bin', 'amrfinder')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
logging.basicConfig(level=logging.INFO)

def read_file(filename):
  """Reads the contents of a file.

  Args:
    filename: The name of the file to read.

  Returns:
    The contents of the file as a string.
  """
  try:
    with open(filename, 'r') as file:  # 'r' mode for reading
      contents = file.read()
    return contents
  except FileNotFoundError:
    return "File not found."

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
   


@app.route("/")
#@app.route("/", methods=["post"])
def index():
    return send_file('src/index.html')

@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html', url=request.url), 404

@app.route('/analyze', methods=['POST'])
def analyze_file():
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        app.logger.info(f"Directory created: {app.config['UPLOAD_FOLDER']}")  # New print statement
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        print("got here")
        # Execute amrfinder command
        command = [amrfinder_path, "-n", filepath, "-o", "uploads/output"]
        print(f"Executing command: {command}")  # Print the full command
        print(f"File path: {filepath}")  # Print the file path
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            error_message = f"amrfinder execution failed with return code {e.returncode}: {e.stderr}"
            print(error_message)  # Print error to console
            return jsonify({'error': error_message}), 500
        
        #shutil.rmtree(filepath, ignore_errors=True) 
        #return jsonify({'message': 'File uploaded successfully'})
        message = "File analyzed successfully<br />"
        message += tabulize(read_file("uploads/output"))
        return message

def main():
    app.run(port=int(os.environ.get('PORT', 80)))

if __name__ == "__main__":
    main()
