#!/bin/sh
set -e
# Increment this to build from base including dependencies
VERSION=0.3.2

get_tarball_url() {
    curl --silent "https://api.github.com/repos/$1/releases/latest" |
        fgrep '"browser_download_url":' |
        cut -d '"' -f 4
}

IMAGE=webamr

echo -n "Getting latest software version... "
SOFTWARE_VERSION=`curl --silent https://raw.githubusercontent.com/ncbi/amr/master/version.txt`
echo "$SOFTWARE_VERSION"

echo -n "Getting latest database version... "
DB_VERSION=`curl --silent https://ftp.ncbi.nlm.nih.gov/pathogen/Antimicrobial_resistance/AMRFinderPlus/database/latest/version.txt`
echo "$DB_VERSION"

BINARY_URL=$(get_tarball_url ncbi/amr)
VERSION_TAG="${SOFTWARE_VERSION}-$DB_VERSION"

echo Downloading $BINARY_URL

echo "Running docker build..."
docker build --build-arg VERSION=${VERSION} --build-arg DB_VERSION=${DB_VERSION} \
    --build-arg SOFTWARE_VERSION=${SOFTWARE_VERSION} \
    --build-arg BINARY_URL=${BINARY_URL} \
    -t $IMAGE \
    -t us-east1-docker.pkg.dev/amrfinder/webamr/$IMAGE:$VERSION \
    .

docker push us-east1-docker.pkg.dev/amrfinder/webamr/$IMAGE:$VERSION

# Run some tests of AMRFinderPlus

