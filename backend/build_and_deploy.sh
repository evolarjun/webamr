#!/bin/sh
# if [ "$1" == "" ]
# then
#     echo "Usage: build_and_deploy.sh <version>"
#     echo "Will increment the version automatically"
#     exit 1
# else
#     version="$1"
# fi
# 
# # Extract major, minor, and patch versions
# IFS='.' read -r major minor patch <<< "$version"
# 
# # Increment the patch version
# patch=$((patch + 1))
# 
# # Construct the new version number
# VERSION="$major.$minor.$patch"
# 
./dockerbuild.sh

# docker build -t webamr-backend -t webamr-backend:$VERSION \
#     -t us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION \
#     . 2>&1 | tee docker.out

docker push us-central1-docker.pkg.dev/amrfinder/amr-repo/amr-backend

gcloud run deploy amr-backend \
  --image us-central1-docker.pkg.dev/amrfinder/amr-repo/amr-backend \
  --region=us-central1 --platform=managed --project=amrfinder \
  --allow-unauthenticated --max-instances=2 --concurrency=2 \
  --cpu 2 --memory 4Gi \
  --set-env-vars "PROJECT_ID=amrfinder,INPUT_BUCKET=amr-input-bucket-amrfinder,TOPIC_ID=amr-jobs-topic,ALLOWED_ORIGINS=*,API_KEY=${API_KEY:-one-super-secret-production-random-api-key}"

# echo gcloud run complete for version $VERSION
