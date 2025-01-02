FROM python:3.11-slim-buster
#FROM python:3.11

    RUN apt-get update && apt-get install -y hmmer ncbi-blast+ libcurl4-openssl-dev curl

    # For debugging
    # RUN apt-get install -y python3.11 python3-pip

# First Install AMRFinderPlus 
    ARG SOFTWARE_VERSION

    ARG BINARY_URL

    # Install AMRFinderPlus
    WORKDIR /app/bin
    SHELL ["/bin/bash", "-o", "pipefail", "-c"]
    RUN curl --silent -L ${BINARY_URL} | tar xvfz -

    ARG DB_VERSION

    RUN /app/bin/amrfinder -u
    RUN /app/bin/amrfinder -h

    WORKDIR /app

    COPY requirements.txt .
    RUN pip3 install --no-cache-dir -r requirements.txt
    # For debugging
#    RUN pip3 install --no-cache-dir -r requirements.txt

    COPY static /app/static
    COPY templates /app/templates
    COPY main.py /app
    COPY README.md /app
    COPY .venv /app/.venv


#    COPY . /app
#    RUN chmod +x /app/bin/amrfinder
#    RUN chmod +x /app/bin/amrfinder_index
#    RUN chmod +x /app/bin/amrfinder_update
#    RUN chmod +x /app/bin/amr_report
#    RUN chmod +x /app/bin/dna_mutation
#    RUN chmod +x /app/bin/fasta_check
#    RUN chmod +x /app/bin/fasta_extract
#    RUN chmod +x /app/bin/fasta2parts
#    RUN chmod +x /app/bin/gff_check
#    RUN chmod +x /app/bin/mutate
#    RUN chmod +x /app/bin/stxtyper
#    RUN chmod +x /app/bin/stx/stxtyper

 
#    RUN ls -l /app/bin  # Temporary debugging step
#    RUN ls -l .
#    RUN /app/bin/amrfinder -h

    EXPOSE 8080
# For debugging
    CMD ["gunicorn", "--error-logfile", "-", "--timeout", "900", "--bind", "0.0.0.0:8080", "main:app"]

# docker run -p 8080:8080 webamr