#!/bin/sh
set -e

if [ -z "${PROJECT_ID}" ] || [ -z "${BUCKET_NAME}" ] || [ -z "${OUTPUT_BUCKET}" ]; then
  echo "Error: PROJECT_ID, BUCKET_NAME, and OUTPUT_BUCKET must be set."
  echo "Run: source set_variables.sh  before running this script."
  exit 1
fi
# Increment this to build from base including dependencies
VERSION=1

IMAGE=webamr-frontend

curl -s -o database_version.txt https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/version.txt
curl -s -O https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/taxgroup.tsv

echo "Running docker build..."
docker build --build-arg VERSION=${VERSION} \
    -t $IMAGE \
    -t us-east1-docker.pkg.dev/amrfinder/webamr-frontend/$IMAGE:$VERSION \
    .
    
docker push us-east1-docker.pkg.dev/amrfinder/webamr-frontend/$IMAGE:$VERSION

