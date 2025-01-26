#!/bin/sh

python -m venv .venv && source .venv/bin/activate
python -m flask --app main run -p 8080

