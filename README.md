## Application Overview

The application's purpose is to identify antimicrobial resistance (AMR) genes using the AMRFinderPlus software. It consists of two main components: a web frontend and a backend processing service.

### Frontend

The frontend is responsible for user interaction and managing the submission process. Its key responsibilities include:

*   Allowing users to upload sequence data, which can be nucleotide or protein files. Optionally, users can also upload GFF files.
*   Allowing users to select the target organism for the analysis.
*   Submitting the uploaded data to a designated Google Cloud Storage (GCS) bucket (Input Bucket).
*   Triggering the backend processing by publishing a message to a Pub/Sub topic.
*   Polling for the analysis results from a separate GCS bucket (Output Bucket) and displaying them to the user.

### Backend

The backend service, typically running on Cloud Run, handles the core analysis tasks. Its main responsibilities are:

*   Listening for new messages on a specific Pub/Sub subscription, which indicate new data submissions.
*   Retrieving the submitted sequence data from the GCS Input Bucket.
*   Executing the AMRFinderPlus software on the retrieved data.
*   Storing the analysis results (output from AMRFinderPlus) back into the GCS Output Bucket.

### Simplified Data Flow

The overall data flow can be summarized as follows:

1.  **User:** Interacts with the Frontend UI to upload data and specify parameters.
2.  **Frontend UI:** Uploads the data to the **GCS (Input Bucket)**.
3.  **Frontend UI:** Sends a **Pub/Sub notification** to a topic.
4.  **Backend Service:** Receives the notification from its Pub/Sub subscription.
5.  **Backend Service:** Retrieves data from the **GCS (Input Bucket)**.
6.  **Backend Service:** Executes AMRFinderPlus and stores results in the **GCS (Output Bucket)**.
7.  **Frontend UI (Results Display):** Polls the **GCS (Output Bucket)** and displays the results to the user.


# Current status

- Front end uploads the files to cloud storage in `webamr/<userid>/`
- Backend stub created by gemini in backend
    - I haven't tried to get it working or figure out how to deploy
- Need to create service accounts (one for both back-end and front-end) and give them the proper permissions to write to cloud storage
- Need to create dockerfile for back-end
- Need to figure out how to deploy front end as a cloud function instead of in a docker container
- 

# Revised project structure
    
    ├── backend/                    # backend service
    ├── frontend
    │   ├── bin	                    # Directory for AMRFinderPlus
    │   │   ├── amrfinder
    │   ├── dockerbuild.sh	     	# Script to build docker container
    │   ├── Dockerfile			    # Dockerfile for flask app
    │   ├── install_amrfinder.sh    # Script to install AMRFinderPlus
    │   ├── main.py				    # flask app
    │   ├── requirements.txt        # python requriements for flask app
    │   ├── static
    │   │   ├── css
    │   │   │   └── style.css       # only CSS used in flask app
    │   │   └── favicon.ico			
    │   ├── templates
    │   │   ├── 404.html
    │   │   └── index.html          # web interface for flask app
    ├── README.md                   # Docs

# Installing AMRFinderPlus

```
./install_amrfinder.sh
```

For command-line testing you need to create a virtualenv if you don't have one already
```
python3 -m venv .venv
pip install -r requirements.txt
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

## Docker 
```
./dockerbuild.sh && docker run -p 8080:80 webamr
```

### Create container registry repository
```
gcloud artifacts repositories create webamr-backend \
    --repository-format=docker \
    --location=us-east1 \
    --description="AMRFinderPlus web interface experiments"
```
```
gcloud auth configure-docker us-east1-docker.pkg.dev
```

### Push to conatiner registry
This is done in dockerbuild.sh
```
# done by dockerbuild.sh
docker build --build-arg VERSION=${VERSION} --build-arg DB_VERSION=${DB_VERSION} \
    --build-arg SOFTWARE_VERSION=${SOFTWARE_VERSION} \
    --build-arg BINARY_URL=${BINARY_URL} \
    -t $IMAGE \
    -t us-east1-docker.pkg.dev/amrfinder/webamr-backend/$IMAGE:$VERSION \
    .

docker push us-east1-docker.pkg.dev/amrfinder/webamr-backend/$IMAGE:$VERSION
```

# Cloud run

## New way to deploy webamr-backend from command-line for version 0.4+

```
# gcloud run deploy webamr-backend --image us-east1-docker.pkg.dev/amrfinder/ webamr-backend/webamr-backend:0.4.1 --region=us-east1 --platform=managed\

gcloud run deploy webamr-trigger \
    --image gcr.io/amrfinder/webamr-backend \
    --region us-east1 \
    --allow-unauthenticated # Only for testing; remove in production
    --set-env-vars PUBSUB_SUBSCRIPTION=webamr-submitted,PUBSUB_PROJECT=amrfinder
```
*   `gcloud run deploy`: This command creates a Cloud Run service.
*   `--image`: Specifies the Docker image to use.
*   `webamr-backend`: The image you pushed to Artifact Registry.
*   `--region`: Choose a region. (us-east1 is an example).
* `--platform=managed` : Specifies the platform.
*   `--platform=managed`  : Specifies the platform.



## Eventarc trigger
This is what listens for a cloud storage event and triggers the cloud run

```
gcloud eventarc triggers create --destination-run-service=webamr-backend --event-type=google.cloud.storage.object.v1.finalized --location=us-central1 --trigger-provider=google.cloud.storage
```
*   `gcloud eventarc triggers create`: This command creates a trigger
*   `--destination-run-service`: Specifies that Cloud Run is the target for the event.
*   `webamr-backend`: The name of your cloud run service.
*   `--event-type=google.cloud.storage.object.v1.finalized`: This specifies the trigger event type.
*   `--location=us-east1`: Chose the location, as before.
*   `--trigger-provider=google.cloud.storage`: Choose the cloud storage provider.








# Old way for version 0.3
I used the console to create a new cloud run service and added the domain amr.arjunp.net to it. I'm not sure how that's going to work in the longer run.

I also need to figure out how to deploy new versions from the commandline instead of going to the cloud run console, clicking the *webamr* service and *edit and deploy a new revision*. From there you select the most recent version in google artifact registry and click deploy.

I haven't gotten the custom domain tested and working yet (I had to add a cname entry for amr.arjunp.net to squarespace to point to ghs.googlehosted.com.). 

Still to do:

1. Add some safety checking for max size of uploaded files
2. Sanity checking for the uploaded file name
3. Monitor current disk space (?)
4. Split the processing part out into 
    1. another Cloud Run service that monitors a cloud storage disk and runs AMRFinderPlus
    2. A cloud function flask app to upload files to that cloud storage disk and download results when they're done
5. Alter the page so it shows what version of AMRFinderPlus software and database are being run (or was run).



-------------------------
### Notes from Gemini on refactoring then modified

Refactoring your application to use a separate Cloud Run worker for processing and Cloud Storage for data persistence is a good approach for scalability and efficiency. Here’s a breakdown of the refactoring process, along with recommendations:

1. Cloud Storage Setup:

Three buckets

    gs://webamr         # where the uploaded files go
    gs://webamr-trigger # a new file appearing here triggers the back-end
    gs://webamr-output  #  


uploads/<user_id>/<filename>
results/<usre_id>/<filename>
uploads/<user_id>/settings.txt


2. Flask App Modifications:

Remove Processing Logic: Remove the AMRFinderPlus execution code from your Flask app. The Flask app's job will now be to receive the uploaded file and store it in the Cloud Storage input folder.
Cloud Storage Upload: Use the Google Cloud Storage client library to upload the file to your bucket. You can either upload the file directly or use a temporary file if you have concerns about handling large files.
Return Task ID: Instead of returning the results directly, your Flask app should return a unique task ID (e.g., the user_id). The front-end should then use this ID to periodically check the Cloud Storage output folder for the results.
Error Handling: Implement comprehensive error handling during the upload process. If the upload fails, return an appropriate error message to the client.
3. Cloud Run Worker:

Cloud Storage Trigger: Configure your Cloud Run service to be triggered by changes in the Cloud Storage input bucket. This will automatically invoke the worker whenever a new file is uploaded.
Download the file: Download the input file from the Cloud Storage input folder using the cloud storage client library.
Process the file: Run AMRFinderPlus on the downloaded file.
Upload the results: Upload the output file to the Cloud Storage output folder.
4. Front-End Updates:

Display Task ID: The front-end should display the task ID after a successful upload.
Periodic Check: Implement a mechanism (e.g., using JavaScript's setInterval or a similar approach) to periodically poll the Cloud Storage output folder for the results file associated with the task ID. Once the file is available, display the results to the user.
Code Example Snippets (Conceptual):

Flask App (Python):

from google.cloud import storage

# ... other imports and setup

@app.route('/analyze', methods=['POST'])
def analyze_file():
    # ... (handle file upload, generate user_id) ...
    
    # Upload to Cloud Storage
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(f'uploads/input/{user_id}/{nuc_file.filename}')
    blob.upload_from_file(nuc_file)

    return jsonify({'task_id': user_id})


Cloud Run (Python):

from google.cloud import storage, functions

@functions.CloudEvent("google.cloud.storage.object.v1.finalized")
def process_file(cloud_event):
    # ... (get file name and user ID from CloudEvent)
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)

    # download file
    input_blob = bucket.blob(input_file_path)
    input_blob.download_to_filename("downloaded_file.fa") # Replace with appropriate temporary file

    # process file
    # Run AMRFinderPlus on downloaded_file.fa

    # Upload results
    output_blob = bucket.blob(output_file_path)
    output_blob.upload_from_filename("output.amrfinder") # Replace with your output file name


Key Considerations:

Error Handling: Robust error handling is crucial in both the Flask app and Cloud Run worker. Consider logging errors to Cloud Logging.
Security: Properly configure IAM permissions to restrict access to your Cloud Storage bucket and Cloud Run service.
Scalability: Cloud Run scales automatically based on demand, so you don’t need to worry about managing server instances.
Cost: Cloud Storage and Cloud Run have associated costs. Monitor your usage and billing.
This refactoring will make your application more scalable, reliable, and cost-effective for handling large file uploads. Let me know if you'd like assistance implementing any of these steps.


---------------------
# Flask Web App Starter from idx.google.com

A Flask starter template as per [these docs](https://flask.palletsprojects.com/en/3.0.x/quickstart/#a-minimal-application).

Dev environment in https://idx.google.com/webamr-5534387

See link in upper right of preview window to run in another browser. Can also change the port to 9003 in the URL to view the one running on the command-line (so you can see debugging output).

## Getting Started

Previews should run automatically when starting a workspace.


# Prompts used

How would I create a cloud run application triggered by a cloud pub/sub message that creates a file on google cloud storage? This application should be run using Google Cloud Run and also have a Dockerfile.

This app should copy the message from the subscription webamr-submitted --project=amrfinder to a file named for the message_id in the bucket gs://webamr-trigger

2024-Jan-25 Back-end redo
===============

Back-end redo, working on using pubsub message to trigger processing.

Work in `backend/` done.

### Deploy backend

Now just use backend/build_and_deploy.sh

```
VERSION=0.4.7
docker build -t webamr-backend -t webamr-backend:$VERSION \
    -t us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION \
    . 2>&1 | tee docker.out
# --image us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:0.4.5 \
docker push us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION 
gcloud run deploy webamr-backend \
  --image us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION \
  --region=us-east1 --platform=managed --project=amrfinder \
  --allow-unauthenticated \
  --cpu 2 \
  --memory 4Gi 
```
Set up trigger (?)
```
# gcloud eventarc triggers create webamr-trigger --location=us-east1 --configuration=eventarc_trigger.yaml

# Creates a trigger and pubsub topic
gcloud eventarc triggers create webamr-trigger \
    --destination-run-service=webamr-backend \
    --destination-run-region=us-east1 \
    --location=us-east1 \
    --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished"

# list triggers
gcloud eventarc triggers list --location=us-east1

# get the pubsub topic for the trigger
export RUN_TOPIC=$(gcloud eventarc triggers describe webamr-trigger \
    --format='value(transport.pubsub.topic)' --location=us-east1)
# send a message
gcloud pubsub topics publish $RUN_TOPIC --message "Hello World!"

# delete trigger
gcloud eventarc triggers delete events-pubsub-trigger --location=us-east1
```
### For debugging and testing
```
# version should be the last published version
build_and_deploy.sh
 
# check in bucket webamr-trigger for log files / messages
```
You Can test locally by copying the contents of the data-XXXX file message into
test.json (making sure that the files pointed to by submission_id exist)
I also create test.txt just so I know what I'm looking for.
```

curl -v -X POST -H "Content-type: application/json" -d @test.json https://webamr-backend-901977498675.us-east1.run.app
```

Note that unsuccessfull runs of the backend will keep trying Clear out all messages by using the clearout subscripton and clicking PURGE MESSAGES

### Commandline pubsub
```
gcloud pubsub topics publish webamr-trigger --project amrfinder --message="hello"
```
```
gcloud pubsub subscriptions pull webamr-submitted --project=amrfinder --auto-ack
```

2025-Jan-25 Front end
========================
Initial testing
```
python -m venv .venv && source .venv/bin/activate
python -m flask --app main run -p 8080

# with docker
docker run -p 8080:8080 webamr-backend
curl -X POST -H "Content-type: application/json" -d @test.json http://localhost:8080
```

Have not yet deployed this I'm currently using the test server to send the pubsub messages and write to the cloud storage buckets. 

## Current status as of Jan-25

Back-end seems to work when running as a test, but it is failing when deployed. I need to confirm it's working locally then see if I can debug why it's failing when deployed. (Possibly RAM or some other issue is killing it)

## Current status as of Jan 26

1. Back-end works, but I still have the problem of pubsub messages timing out before they're acknowledged by the finishing of the cloud run job. I'm not sure how to handle that. I upped the acknowledgement timeout to its mask (600 secs / 10 minutes), but I'm not sure that worked. If I can't figure it out I should have it detect that the job is complete and just return success quickly.

It means everything gets run at least twice, but I can set the retry to max and maybe that's ok? Or I could have it detect how long it has been in process? Not sure still. Maybe use a database? This trigger thing isn't working very well. I could also create a cloud function shim(?).

2. Front-end is partially working locally. Still needs quite a bit of polish, but it waited until a job was done then put the HTML table at the bottom.

## Current status as of Jan 29

1. Revised back-end to create a timestamp file when it starts processing and skip running if the timestamp is too recent.

2. Has not been tested at all...

3. Front end still needs work

## Current status as of March 16

1. Not working, backend not triggered, consider swithcing to a persistant server that spawns cloud run jobs
    - Test backend using `backend/test_local.sh`
    - Added test for 
2. Do more testing
3. Need to document how it works because I keep forgetting
4. Need to create a service account to limit security risks
5. Added automatic deletion of files > 14-days old, could reduce number
6. Fix front-end
7. Figure out logging and limit resource use
8. Figure out billing and set billing limit notifications
9. Clean up code and make improvements