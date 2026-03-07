#!/bin/sh

./dockerbuild.sh

docker push us-central1-docker.pkg.dev/amrfinder/amr-repo/amr-backend

gcloud run deploy amr-backend \
  --image us-central1-docker.pkg.dev/amrfinder/amr-repo/amr-backend \
  --region=us-central1 --platform=managed --project=amrfinder \
  --allow-unauthenticated \
  --set-env-vars "PROJECT_ID=amrfinder,INPUT_BUCKET=amr-input-bucket-amrfinder,TOPIC_ID=amr-jobs-topic,ALLOWED_ORIGINS=*,API_KEY=${API_KEY:-one-super-secret-production-random-api-key}"

echo gcloud run deploy complete
