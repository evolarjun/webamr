#!/bin/bash

get_tarball_url() {
    curl --silent "https://api.github.com/repos/$1/releases/latest" |
        fgrep '"browser_download_url":' |
        cut -d '"' -f 4
}
URL=$(get_tarball_url ncbi/amr)

cd /home/user/webamr/frontend/bin

    curl --silent -L -O $URL
    tarball_name=$(echo $URL | perl -pe 's#^.*/(.*)#\1#')
    tar xfz $tarball_name
    rm $tarball_name
    ./amrfinder --force_update
    ./amrfinder --database_version | grep -v 'directory: ' > amrfinder_version.txt


