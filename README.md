# Flask Web App Starter

A Flask starter template as per [these docs](https://flask.palletsprojects.com/en/3.0.x/quickstart/#a-minimal-application).

Dev environment in https://idx.google.com/webamr-5534387

See link in upper right of preview window to run in another browser. Can also change the port to 9003 in the URL to view the one running on the command-line (so you can see debugging output).

## Getting Started

Previews should run automatically when starting a workspace.

# Installing AMRFinderPlus

```
cd /
git clone https://github.com/ncbi/amr.git
cd amr
git submodule update --init
make -j -O
make install INSTALL_DIR=~/webamr/bin
cd ~/webamr/bin
./amrfinder -u
```

Testing from commandline
```
python -m venv .venv && source .venv/bin/activate
python -m flask --app main run -p 9003
```
In another shell
```
curl -X POST -F "nuc_file=@test_dna.fa" http://localhost:9003/analyze
```

# For deployment (?)
To deploy your application to Google Cloud Run, follow these steps:

1. Project Setup: If you don't already have one, create a Google Cloud project and enable billing.

Requirements File: Ensure you have a requirements.txt file in your project root directory. This file lists all the project's dependencies. If you don't have one, you can create it using:

pip freeze > requirements.txt



2. Cloud Run Deployment (using gcloud):
Authentication: Authenticate with your Google Cloud project using the gcloud command-line tool.
gcloud auth application-default login



3.   **Deployment:** Navigate to your project's root directory in the terminal and run:


    gcloud run deploy webamr \
            --image gcr.io/[PROJECT_ID]/webamr \
            --region [REGION] \
            --platform managed \
            --allow-unauthenticated  \
            --set-env-vars UPLOAD_FOLDER_BASE=/tmp/uploads



    Replace the following:
    *   `[PROJECT_ID]`: Your Google Cloud project ID.
    *   `[REGION]`: Your preferred Google Cloud region.  Choose a region that's geographically close to your users for better performance.


4. Dockerfile: While not strictly required for a simple Python application, creating a Dockerfile is recommended for better control over your deployment environment. Create a file named Dockerfile in your project's root directory with the following content:

FROM python:3.11-slim-buster

    WORKDIR /app

    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt

    COPY . .

    EXPOSE 8080

    CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]


5.   Then build the container image and push it to Google Container Registry (GCR):


    docker build -t gcr.io/[PROJECT_ID]/webamr .
        docker push gcr.io/[PROJECT_ID]/webamr



Environment Variables: The UPLOAD_FOLDER_BASE environment variable is crucial, so make sure it's correctly set. Cloud Run has a /tmp directory that is suitable for this purpose.
After successful deployment, Cloud Run will provide you with a URL to access your application.

Important Considerations:

Scaling: Cloud Run automatically scales your application based on demand.
Security: The --allow-unauthenticated flag is included for testing but should be removed for production. You should implement proper authentication and authorization mechanisms in your application and configure appropriate security rules in your Google Cloud project.
Persistence: The /tmp directory is not persistent; data stored there will be lost if your container restarts. If you need persistent storage, consider using Cloud Storage or a Cloud SQL database.
Let me know if you have any other questions.




Enter a prompt or '/' for commands
