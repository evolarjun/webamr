#!/bin/bash
set -e

if [ -z "${PROJECT_ID}" ] || [ -z "${BUCKET_NAME}" ] || [ -z "${OUTPUT_BUCKET}" ]; then
  echo "Error: PROJECT_ID, BUCKET_NAME, and OUTPUT_BUCKET must be set."
  echo "Run: source set_variables.sh  before running this script."
  exit 1
fi

# Configuration
# PROJECT_ID=""
# REGION=""
# OUTPUT_BUCKET=""

START_TIME=$SECONDS

echo "---------------------------------------------------------"
echo "Building worker image for project: ${PROJECT_ID}"
echo "Usually takes about 8 minutes"
echo "---------------------------------------------------------"

cp ../VERSION.txt .

# Cloud Build submits the current directory and pushes to Artifact Registry
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker \
  .

echo "---------------------------------------------------------"
echo "Deploying amr-worker to Cloud Run..."
echo "---------------------------------------------------------"

gcloud run deploy amr-worker \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-worker \
  --region $REGION \
  --no-allow-unauthenticated \
  --timeout 1200 \
  --memory 4Gi \
  --cpu 2 \
  --set-env-vars PROJECT_ID=$PROJECT_ID,OUTPUT_BUCKET=$OUTPUT_BUCKET \
  --concurrency 2 \
  --max-instances 2  # Change this number to limit total simultaneous jobs


ELAPSED=$((SECONDS - START_TIME))
DUR_MIN=$((ELAPSED / 60))
DUR_SEC=$((ELAPSED % 60))

echo "---------------------------------------------------------"
echo "Worker status:"
gcloud run services describe amr-worker --region $REGION --format='value(status.url)'
printf "Worker deployed successfully in %02d:%02d!\n" $DUR_MIN $DUR_SEC
echo "---------------------------------------------------------"
