#!/bin/sh

if [ -z "${PROJECT_ID}" ] || [ -z "${BUCKET_NAME}" ] || [ -z "${OUTPUT_BUCKET}" ]; then
  echo "Error: PROJECT_ID, BUCKET_NAME, and OUTPUT_BUCKET must be set."
  echo "Run: source set_variables.sh  before running this script."
  exit 1
fi

python -m venv .venv && source .venv/bin/activate
python -m flask --app main run -p 8080

