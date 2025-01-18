FROM python:3.11-slim-bookworm

    RUN apt-get update && apt-get install -y hmmer ncbi-blast+ libcurl4-openssl-dev curl

    # For debugging
    # RUN apt-get install -y python3.11 python3-pip

# First Install AMRFinderPlus 
    ARG SOFTWARE_VERSION
    # Set the working directory for AMRFinderPlus
    ARG BINARY_URL

    # Install AMRFinderPlus
    WORKDIR /app/bin
    SHELL ["/bin/bash", "-o", "pipefail", "-c"]
    RUN curl --silent -L ${BINARY_URL} | tar xvfz -

    ARG DB_VERSION

    RUN /app/bin/amrfinder -u
    RUN /app/bin/amrfinder -h
    RUN /app/bin/amrfinder --database_version | grep -v 'directory: ' > /app/bin/amrfinder_version.txt

    WORKDIR /app

    COPY requirements.txt .

    RUN pip3 install --no-cache-dir -r requirements.txt
    # For debugging
#    RUN pip3 install --no-cache-dir -r requirements.txt

    COPY backend.py /app/backend/backend.py    
    RUN chmod +x /app/backend/backend.py
 
    RUN pwd
    RUN ls -l /app/backend/backend.py
#    RUN ls -l /app/bin  # Temporary debugging step
    RUN ls -l .
    RUN find .
    RUN pwd

    # Configure entrypoint and setup
#   ENTRYPOINT ["/app/backend/backend.py",  "backend-cloud-run-cloud-storage", "backend-cloud-run-backend-cloud-storage"]
    
    # For deubgging    

# for deubgging
    EXPOSE 8080
    # EXPOSE 80
    #CMD ["gunicorn", "--error-logfile", "-", "--timeout", "900", "--bind", "0.0.0.0:8080", "main:app"]
    
# docker run -p 8080:8080 webamr    # CMD ["gunicorn", "--error-logfile", "-", "--timeout", "900", "--bind", "0.0.0.0:80", "main:app"]
    # CMD ["/app/backend/backend.py", "backend-cloud-run-cloud-storage", "backend-cloud-run-backend-cloud-storage"]
    # For deubgging
    # CMD ["/bin/bash", "-c", "echo 'Backend is running'", "&&", "sleep", "infinity"]
