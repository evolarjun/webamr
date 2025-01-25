FROM python:3.11-slim-buster

# increment to rebuild from scratch
ARG VERSION

RUN apt-get update && apt-get install -y hmmer ncbi-blast+ libcurl4-openssl-dev curl

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

# Install AMRFinderPlus
ARG SOFTWARE_VERSION
ARG BINARY_URL
WORKDIR /app/bin
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl --silent -L ${BINARY_URL} | tar xvfz -
RUN /app/bin/amrfinder -h

# Get database
RUN /app/bin/amrfinder -u
RUN /app/bin/amrfinder --database_version | grep -v 'directory: ' > /app/bin/amrfinder_version.txt

WORKDIR /app

COPY main.py .

RUN pwd
#    RUN ls -l /app/bin  # Temporary debugging step
RUN ls -l .
RUN pwd

    # Configure entrypoint and setup
#   ENTRYPOINT ["/app/backend/backend.py",  "backend-cloud-run-cloud-storage", "backend-cloud-run-backend-cloud-storage"]
    EXPOSE 8080
    CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]

# docker run -p 8080:8080 webamr    # CMD ["gunicorn", "--error-logfile", "-", "--timeout", "900", "--bind", "0.0.0.0:80", "main:app"]
