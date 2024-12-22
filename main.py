import os
import subprocess
import shutil
from flask import Flask, send_file, request, jsonify, render_template


UPLOAD_FOLDER = 'uploads'
app_dir = os.path.dirname(os.path.abspath(__file__))
amrfinder_path = os.path.join(app_dir, 'bin', 'amrfinder')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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
        print(f"Directory created: {app.config['UPLOAD_FOLDER']}")  # New print statement
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
        return jsonify({'message': 'File uploaded successfully'})


def main():
    app.run(port=int(os.environ.get('PORT', 80)))

if __name__ == "__main__":
    main()
