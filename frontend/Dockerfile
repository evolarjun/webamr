--- a/requirements.txt
+ gunicorn
google-cloud-core
google-cloud-logging
google-cloud-storage
requests
python-dotenv

--- a/Dockerfile
++ b/Dockerfile


    # Install system dependencies
    RUN apt-get update && apt-get install -y \
        hmmer \
        ncbi-blast+ \
        libcurl4-openssl-dev \
        curl

    # Set the working directory for AMRFinderPlus
    ARG BINARY_URL
    WORKDIR /app/bin
    SHELL ["/bin/bash", "-o", "pipefail", "-c"]

    # Download and extract AMRFinderPlus
    RUN curl --silent -L ${BINARY_URL} | tar xvfz -

    # Update the AMRFinderPlus database
    RUN /app/bin/amrfinder -u
    RUN /app/bin/amrfinder -h
    RUN /app/bin/amrfinder --database_version | grep -v 'directory: ' > /app/bin/amrfinder_version.txt

    # Set working directory back to /app
    WORKDIR /app

    # Copy requirements and install them
    COPY requirements.txt .
    RUN pip3 install --no-cache-dir -r requirements.txt

    # Install Google Cloud Storage and other dependencies
    RUN pip3 install --no-cache-dir \
        google-cloud-storage \
        google-cloud-core \
        google-cloud-logging

    # Copy the backend script
    COPY backend /app/backend
    COPY backend/backend.py /app/backend/backend.py
    COPY function.py /app

    # Set the entrypoint to execute the backend
    ENTRYPOINT ["python3", "/app/backend/backend.py"]
    EXPOSE 80

    CMD ["python3", "/app/backend/backend.py"]