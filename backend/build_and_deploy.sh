#!/bin/sh
if [ "$1" == "" ]
then
    echo "Usage: build_and_deploy.sh <version>"
    echo "Will increment the version automatically"
    exit 1
else
    version="$1"
fi

# Extract major, minor, and patch versions
IFS='.' read -r major minor patch <<< "$version"

# Increment the patch version
patch=$((patch + 1))

# Construct the new version number
VERSION="$major.$minor.$patch"

docker build -t webamr-backend -t webamr-backend:$VERSION \
    -t us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION \
    . 2>&1 | tee docker.out

docker push us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend:$VERSION

gcloud run deploy webamr-backend \
  --image us-east1-docker.pkg.dev/amrfinder/webamr-backend/webamr-backend \
  --region=us-east1 --platform=managed --project=amrfinder \
  --allow-unauthenticated

echo gcloud run complete for version $VERSION
