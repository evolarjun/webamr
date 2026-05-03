# Stage 1: Build AMRFinderPlus + HMMER3 from source 
FROM ubuntu:24.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
  git \
  libcurl4-openssl-dev \
  build-essential \
  wget \
  ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install HMMER3 from source (not in arm64 apt repos)
ENV HMMER_VERSION=3.4
RUN wget -q http://eddylab.org/software/hmmer/hmmer-${HMMER_VERSION}.tar.gz \
  && tar xzf hmmer-${HMMER_VERSION}.tar.gz \
  && cd hmmer-${HMMER_VERSION} \
  && ./configure --prefix=/usr/local \
  && make -j$(nproc) \
  && make install \
  && cd .. && rm -rf hmmer-${HMMER_VERSION}*

# Compile AMRFinderPlus from source
RUN git clone https://github.com/ncbi/amr.git \
  && cd amr \
  && git submodule update --init --recursive \
  && make -j$(nproc) -O \
  && make install INSTALL_DIR=/amrfinder

# Stage 2: Runtime image 
FROM python:3.12-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
  libcurl4 \
  curl \
  libgomp1 \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Copy HMMER3 binaries from builder
COPY --from=builder /usr/local/bin/hmm* /usr/local/bin/

# Install ONLY necessary BLAST+ binaries
ENV BLAST_VERSION=2.17.0
RUN ARCH=$(uname -m) \
  && if [ "$ARCH" = "aarch64" ]; then BLAST_ARCH="aarch64-linux"; else BLAST_ARCH="x64-linux"; fi \
  && curl -L -O https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/${BLAST_VERSION}/ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && tar -zxf ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && cd ncbi-blast-${BLAST_VERSION}+/bin/ \
  && cp blastp blastn blastx makeblastdb tblastn /usr/local/bin/ \
  && cd / && rm -rf /ncbi-blast-${BLAST_VERSION}+*

# Copy AMRFinderPlus binaries
COPY --from=builder /amrfinder/ /usr/local/bin/

# Download AMRFinderPlus databases at build time and extract version
RUN amrfinder -u \
  && amrfinder --database_version | awk -F ': ' '/Database version/ {print $2}' > /etc/amrfinder_db_version.txt

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

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "1200", "--workers", "1", "worker:app"]
