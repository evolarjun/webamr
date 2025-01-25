FROM python:3.11-slim-buster

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt gunicorn

COPY main.py .

RUN pwd
#    RUN ls -l /app/bin  # Temporary debugging step
RUN ls -l .
RUN pwd

    # Configure entrypoint and setup
#   ENTRYPOINT ["/app/backend/backend.py",  "backend-cloud-run-cloud-storage", "backend-cloud-run-backend-cloud-storage"]
    EXPOSE 80
    CMD ["gunicorn", "--bind", "0.0.0.0:80", "main:app"]

# docker run -p 8080:8080 webamr    # CMD ["gunicorn", "--error-logfile", "-", "--timeout", "900", "--bind", "0.0.0.0:80", "main:app"]
