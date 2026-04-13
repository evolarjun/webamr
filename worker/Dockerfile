# Stage 1: Build AMRFinderPlus + HMMER3 from source 
FROM ubuntu:24.04 AS builder

RUN apt-get update && apt-get install -y \
  git \
  libcurl4-openssl-dev \
  build-essential \
  wget \
  && rm -rf /var/lib/apt/lists/*

# Install HMMER3 from source (not in arm64 apt repos)
ENV HMMER_VERSION=3.4
RUN wget -q http://eddylab.org/software/hmmer/hmmer-${HMMER_VERSION}.tar.gz \
  && tar xzf hmmer-${HMMER_VERSION}.tar.gz \
  && cd hmmer-${HMMER_VERSION} \
  && ./configure --prefix=/usr/local \
  && make -j$(nproc) -O \
  && make install \
  && cd .. && rm -rf hmmer-${HMMER_VERSION}*

# Compile AMRFinderPlus from source
RUN git clone https://github.com/ncbi/amr.git \
  && cd amr \
  && git submodule update --init \
  && make -j$(nproc) -O \
  && make install INSTALL_DIR=/amrfinder

# Stage 2: Runtime image 
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
  libcurl4-openssl-dev \
  curl \
  libgomp1 \
  python3 \
  python3-venv \
  python3-pip \
  && rm -rf /var/lib/apt/lists/*

# Copy HMMER3 binaries from builder
COPY --from=builder /usr/local/bin/hmm* /usr/local/bin/

# Install BLAST+ pre-built binary uses correct arch (aarch64 for arm, x64 for amd64)
ENV BLAST_VERSION=2.17.0
RUN ARCH=$(uname -m) \
  && if [ "$ARCH" = "aarch64" ]; then BLAST_ARCH="aarch64-linux"; else BLAST_ARCH="x64-linux"; fi \
  && curl -L -O https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/${BLAST_VERSION}/ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && tar -zxf ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && cp -r ncbi-blast-${BLAST_VERSION}+/bin/* /usr/local/bin/ \
  && rm -rf ncbi-blast-${BLAST_VERSION}+*

# Copy AMRFinderPlus binaries
COPY --from=builder /amrfinder/ /usr/local/bin/

# Download AMRFinderPlus databases at build time
RUN amrfinder -u

# Extract exact database version into a configuration file
RUN amrfinder --database_version | awk -F ': ' '/Database version/ {print $2}' > /etc/amrfinder_db_version.txt

WORKDIR /app

# Ensure logs are not buffered and appear immediately in Cloud Run
ENV PYTHONUNBUFFERED=1

# Install Python worker dependencies
COPY requirements.txt .
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip3 install --no-cache-dir -r requirements.txt

COPY worker.py .
COPY VERSION.txt .

# gunicorn timeout must exceed the longest expected AMRFinderPlus run.
# 3600s (1 hour) matches the Cloud Run max request timeout.
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "1200", "--workers", "1", "worker:app"]
