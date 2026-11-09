#!/bin/bash
# =============================================================================
# run_local.sh - Run WebAMR locally with Colima/Docker worker and Flask frontend
# =============================================================================
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "  Starting WebAMR Local Development Environment"
echo "============================================================"

# 1. Source project environment variables
if [ -f "set_variables.sh" ]; then
    echo "[1/6] Loading environment variables from set_variables.sh..."
    source set_variables.sh
else
    echo "ERROR: set_variables.sh not found in $PROJECT_ROOT."
    exit 1
fi

# Ensure .venv is activated
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "ERROR: .venv not found. Please set up the Python virtual environment first."
    exit 1
fi

# 2. Check Application Default Credentials
ADC_PATH="$HOME/.config/gcloud/application_default_credentials.json"
echo "[2/6] Checking GCP Application Default Credentials..."
if [ ! -f "$ADC_PATH" ]; then
    echo "WARNING: $ADC_PATH does not exist."
    echo "Run 'gcloud auth application-default login' before continuing if GCP access fails."
fi

# 3. Check and start Colima if needed
echo "[3/6] Checking Colima / Docker status..."
if command -v colima >/dev/null 2>&1; then
    if ! colima status >/dev/null 2>&1; then
        echo "Colima is not running. Starting Colima..."
        colima start --vm-type vz --mount-type virtiofs --cpu 4 --memory 8
    else
        echo "Colima is already running."
    fi
else
    echo "Note: colima command not found. Assuming Docker is running."
fi

if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not reachable. Please start Colima or Docker and retry."
    exit 1
fi

# 4. Build Worker Image
IMAGE_NAME="amr-worker-local"
CONTAINER_NAME="webamr-local-worker"

echo "[4/6] Building worker Docker image ($IMAGE_NAME)..."
cp VERSION.txt worker/VERSION.txt
trap 'rm -f worker/VERSION.txt' EXIT
docker build -t "$IMAGE_NAME" worker/
rm -f worker/VERSION.txt
trap - EXIT

# 5. Launch Worker Container
WORKER_PORT=8081
echo "[5/6] Starting worker container ($CONTAINER_NAME) on port $WORKER_PORT..."
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Stopping existing $CONTAINER_NAME container..."
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

cleanup() {
    echo ""
    echo "Shutting down local worker container and frontend..."
    if [ -n "$FLASK_PID" ]; then
        kill "$FLASK_PID" >/dev/null 2>&1 || true
    fi
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    echo "Shutdown complete."
}
trap cleanup EXIT INT TERM HUP

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  -p ${WORKER_PORT}:8080 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/adc.json \
  -e PROJECT_ID="${PROJECT_ID}" \
  -e OUTPUT_BUCKET="${OUTPUT_BUCKET}" \
  -v "$ADC_PATH":/tmp/keys/adc.json:ro \
  "$IMAGE_NAME"

echo "Waiting for worker container to initialize..."
for i in {1..15}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:${WORKER_PORT}/ >/dev/null 2>&1; then
        echo "Worker container is up and responding."
        break
    fi
    sleep 1
done

# 6. Start Flask Frontend
export WORKER_URL="http://localhost:${WORKER_PORT}"
export FLASK_DEBUG=1

echo ""
echo "============================================================"
echo "  WebAMR is ready for testing!"
echo "  Open your browser and navigate to: http://localhost:5000"
echo "  Press Ctrl+C to stop both frontend and worker."
echo "============================================================"
echo ""

python -m flask --app frontend/main run --port 5000 &
FLASK_PID=$!
wait $FLASK_PID
