FROM python:3.12-slim

# Set working directory back to /app
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY static ./static
COPY templates ./templates
COPY database_version.txt .
COPY taxgroup.tsv .
COPY main.py .

# Set the entrypoint to execute
EXPOSE 80
CMD exec gunicorn --bind :80 --workers 1 --threads 8 --timeout 0 main:app


