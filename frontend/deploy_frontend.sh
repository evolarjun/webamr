#!/bin/bash
# deploy_frontend.sh — Deploys the AMRFinderPlus frontend to Cloud Run
set -e

# Configuration
PROJECT_ID="amrfinder"
REGION="us-central1"
BUCKET_NAME="amr-input-bucket-${PROJECT_ID}"
OUTPUT_BUCKET="amr-output-bucket-${PROJECT_ID}"
TOPIC_ID="amr-jobs-topic"

START_TIME=$SECONDS

echo "---------------------------------------------------------"
echo "Building frontend image for project: ${PROJECT_ID}"
echo "---------------------------------------------------------"

# Cloud Build submits the current directory and pushes to Artifact Registry
gcloud builds submit \
  --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-frontend \
  .

echo "---------------------------------------------------------"
echo "Deploying amr-frontend to Cloud Run..."
echo "---------------------------------------------------------"

gcloud run deploy amr-frontend \
  --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/amr-repo/amr-frontend \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars PROJECT_ID=$PROJECT_ID,BUCKET_NAME=$BUCKET_NAME,TOPIC_ID=$TOPIC_ID,OUTPUT_BUCKET=$OUTPUT_BUCKET

ELAPSED=$((SECONDS - START_TIME))
DUR_MIN=$((ELAPSED / 60))
DUR_SEC=$((ELAPSED % 60))

echo "---------------------------------------------------------"
echo "Frontend status:"
gcloud run services describe amr-frontend --region $REGION --format='value(status.url)'
printf "Frontend deployed successfully in %02d:%02d!\n" $DUR_MIN $DUR_SEC
echo "---------------------------------------------------------"
