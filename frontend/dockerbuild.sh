#!/bin/sh
set -e
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

