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
python -m flask --app main run -p 9003
```
In another shell
```
curl -X POST -F "nuc_file=@test_dna.fa" http://localhost:9003/analyze
```
