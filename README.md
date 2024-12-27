# Flask Web App Starter

A Flask starter template as per [these docs](https://flask.palletsprojects.com/en/3.0.x/quickstart/#a-minimal-application).

Dev environment in https://idx.google.com/webamr-5534387

## Getting Started

Previews should run automatically when starting a workspace.

# Installing AMRFinderPlus

```
git clone https://github.com/ncbi/amr.git
cd amr
git submodule update --init
make -j -O
./amrfinder -u
```

Testing from commandline
```
python -m flask --app main run -p 9003
```
In another shell
```
curl -X POST -F "file=@test_dna.fa" http://localhost:9002/analyze
```
