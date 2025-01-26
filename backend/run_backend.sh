#!/bin/sh -x

echo "This just runs it locally so you can trigger it with a local curl command"

# export RUN_TOPIC=$(gcloud eventarc triggers describe webamr-trigger \
#     --format='value(transport.pubsub.topic)' --location=us-east1)
python -m venv .venv && source .venv/bin/activate
echo "# Try hitting it with example data with"
echo "curl -X POST -H \"Content-type: application/json\" -d @test.json http://localhost:8080"
echo 
echo

python -m flask --app main run -p 8080



