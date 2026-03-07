FROM python:3.11-slim-buster

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
CMD ["gunicorn", "--bind", "0.0.0.0:80", "main:app"]


