#!/bin/bash
set -e

# Configuration
# PROJECT_ID=""
# REGION=""
# OUTPUT_BUCKET=""

START_TIME=$SECONDS

echo "---------------------------------------------------------"
echo "Building worker image for project: ${PROJECT_ID}"
echo "---------------------------------------------------------"

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
  --max-instances 1  # Only allow one job to run at a time


ELAPSED=$((SECONDS - START_TIME))
DUR_MIN=$((ELAPSED / 60))
DUR_SEC=$((ELAPSED % 60))

echo "---------------------------------------------------------"
echo "Worker status:"
gcloud run services describe amr-worker --region $REGION --format='value(status.url)'
printf "Worker deployed successfully in %02d:%02d!\n" $DUR_MIN $DUR_SEC
echo "---------------------------------------------------------"
