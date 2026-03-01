# Use a slim Python 3.9 image
FROM python:3.9-slim

# Install system dependencies required for AMRFinderPlus (NCBI BLAST+, hmmer, libcurl)
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    ncbi-blast+ \
    hmmer \
    libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Download and install latest AMRFinderPlus binary from NCBI
RUN cd /tmp && \
    curl -LO https://github.com/ncbi/amr/releases/latest/download/amrfinder_linux_bin.tar.gz && \
    tar -zxvf amrfinder_linux_bin.tar.gz && \
    mv amrfinder /usr/local/bin/ && \
    mv amrfinder_update /usr/local/bin/ && \
    rm -r /tmp/*

# Create working app directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the worker python script
COPY worker.py .

# Update database at image build time to bake modern databases directly in the layer
RUN amrfinder_update

# Command to run the worker process on container start
# -u provides unbuffered python output for Cloud run / Compute engine logs
CMD ["python", "-u", "worker.py"]
