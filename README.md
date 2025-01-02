# Flask Web App Starter

A Flask starter template as per [these docs](https://flask.palletsprojects.com/en/3.0.x/quickstart/#a-minimal-application).

Dev environment in https://idx.google.com/webamr-5534387

See link in upper right of preview window to run in another browser. Can also change the port to 9003 in the URL to view the one running on the command-line (so you can see debugging output).

## Getting Started

Previews should run automatically when starting a workspace.

# Installing AMRFinderPlus

```
cd /
git clone https://github.com/ncbi/amr.git
cd amr
git submodule update --init
make -j -O
make install INSTALL_DIR=~/webamr/bin
cd ~/webamr/bin
./amrfinder -u
```

Testing from commandline
```
python -m venv .venv && source .venv/bin/activate
python -m flask --app main run -p 9003
```
In another shell
```
curl -X POST -F "nuc_file=@test_dna.fa" http://localhost:9003/analyze
```
## Docker 
```
./dockerbuild.sh &&  docker run -p 8080:8080 webamr
```

### Create container registry repository
```
gcloud artifacts repositories create webamr \
    --repository-format=docker \
    --location=us-east1 \
    --description="AMRFinderPlus web interface experiments"
```
```
gcloud auth configure-docker us-east1-docker.pkg.dev
```


### Push to conatiner registry
```
# done by dockerbuild.sh
docker build --build-arg VERSION=${VERSION} --build-arg DB_VERSION=${DB_VERSION} \
    --build-arg SOFTWARE_VERSION=${SOFTWARE_VERSION} \
    --build-arg BINARY_URL=${BINARY_URL} \
    -t $IMAGE \
    -t us-east1-docker.pkg.dev/amrfinder/webamr/$IMAGE:$VERSION \
    .

docker push us-east1-docker.pkg.dev/amrfinder/webamr/$IMAGE:$VERSION
```

# Cloud run

I used the console to create a new cloud run service and added the domain amr.arjunp.net to it. I'm not sure how that's going to work in the longer run.

I also need to figure out how to deploy new versions from the commandline instead of going to the cloud run console, clicking the *webamr* service and *edit and deploy a new revision*. From there you select the most recent version in google artifact registry and click deploy.

I haven't gotten the custom domain tested and working yet (I had to add a cname entry for amr.arjunp.net to squarespace to point to ghs.googlehosted.com.). 

Still to do:

1. Add some safety checking for max size of uploaded files
2. Sanity checking for the uploaded file name
3. Monitor current disk space (?)
4. Split the processing part out into 
    1. another Cloud Run service that monitors a cloud storage disk and runs AMRFinderPlus
    2. A cloud function flask app to upload files to that cloud storage disk and download results when they're done
5. Alter the page so it shows what version of AMRFinderPlus software and database are being run (or was run).