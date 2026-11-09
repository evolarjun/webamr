# Stage 1: Build AMRFinderPlus, HMMER3, BLAST+, and Python venv
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
  git \
  libcurl4-openssl-dev \
  build-essential \
  wget \
  curl \
  ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install HMMER3 from source and strip required binaries
ENV HMMER_VERSION=3.4
RUN wget -q http://eddylab.org/software/hmmer/hmmer-${HMMER_VERSION}.tar.gz \
  && tar xzf hmmer-${HMMER_VERSION}.tar.gz \
  && cd hmmer-${HMMER_VERSION} \
  && ./configure --prefix=/usr/local \
  && make -j$(nproc) \
  && make install \
  && strip --strip-unneeded /usr/local/bin/hmmsearch /usr/local/bin/hmmpress \
  && cd .. && rm -rf hmmer-${HMMER_VERSION}*

# Compile AMRFinderPlus from source (shallow clone) and strip binaries
RUN git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/ncbi/amr.git \
  && cd amr \
  && make -j$(nproc) -O \
  && make install INSTALL_DIR=/amrfinder \
  && (find /amrfinder -type f -executable -exec strip --strip-unneeded {} + 2>/dev/null || true) \
  && cd .. && rm -rf amr

# Download and strip ONLY necessary BLAST+ binaries
ENV BLAST_VERSION=2.17.0
RUN mkdir -p /blast_bin \
  && ARCH=$(uname -m) \
  && if [ "$ARCH" = "aarch64" ]; then BLAST_ARCH="aarch64-linux"; else BLAST_ARCH="x64-linux"; fi \
  && curl -L -O https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/${BLAST_VERSION}/ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && tar -zxf ncbi-blast-${BLAST_VERSION}+-${BLAST_ARCH}.tar.gz \
  && cd ncbi-blast-${BLAST_VERSION}+/bin/ \
  && cp blastp blastn blastx makeblastdb tblastn /blast_bin/ \
  && strip --strip-unneeded /blast_bin/* \
  && cd / && rm -rf /ncbi-blast-${BLAST_VERSION}+*

# Build Python virtual environment and install dependencies + AMRrules
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip3 install --no-cache-dir -r requirements.txt

# Install AMRrules from source (shallow clone; requires copy_rules.py to populate rules subpackage)
RUN git clone --depth 1 https://github.com/AMRverse/AMRrules.git /tmp/AMRrules \
  && cd /tmp/AMRrules \
  && pip3 install --no-cache-dir . \
  && rm -rf /tmp/AMRrules

# Download AMRrules resource files and rules database (create target resources directory first)
RUN python3 -c "from pathlib import Path; import amrrules; (Path(amrrules.__file__).parent / 'resources').mkdir(parents=True, exist_ok=True); from amrrules.resources import ResourceManager; ResourceManager().setup_all_resources()" \
  && amrrules --download-resources rules

# Stage 2: Runtime image
FROM python:3.12-slim

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
  libcurl4 \
  libgomp1 \
  ca-certificates \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Copy stripped HMMER3, BLAST+, and AMRFinderPlus binaries from builder
COPY --from=builder /usr/local/bin/hmmsearch /usr/local/bin/hmmpress /usr/local/bin/
COPY --from=builder /blast_bin/ /usr/local/bin/
COPY --from=builder /amrfinder/ /usr/local/bin/

# Download AMRFinderPlus databases at build time
RUN amrfinder -u

WORKDIR /app

# Ensure logs are not buffered and appear immediately in Cloud Run
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

# Copy pre-built virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Verify AMRrules installation and rules database
RUN amrrules --list-organisms

COPY worker.py .
COPY VERSION.txt .

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "1200", "--workers", "1", "worker:app"]
