#!/bin/sh

# python -m venv .venv && source .venv/bin/activate
# python -m flask --app main run -p 8080
#
gsutil -m cp -r test/49cfcbd7-fce0-4a63-b840-e5c3ef3fc0dc/* gs://webamr/49cfcbd7-fce0-4a63-b840-e5c3ef3fc0dc/

curl -X POST -H "Content-type: application/json" -d @test/test.json http://localhost:8080
