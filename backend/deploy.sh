#!/bin/sh

./dockerbuild.sh

docker push us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend

gcloud run deploy webamr-backend \
  --image us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend \
  --region=us-east1 --platform=managed --project=amrfinder \
  --allow-unauthenticated

echo gcloud run deploy complete
