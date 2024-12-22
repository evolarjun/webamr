import os

from flask import Flask, send_file, request, jsonify, render_template


UPLOAD_FOLDER = 'uploads'

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
        
        # Perform analysis here (replace with your logic)
        # ...
        
        return jsonify({'message': 'File uploaded successfully'})


def main():
    app.run(port=int(os.environ.get('PORT', 80)))

if __name__ == "__main__":
    main()
