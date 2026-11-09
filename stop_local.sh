#!/bin/bash
# =============================================================================
# stop_local.sh - Stop all local WebAMR services (Worker container & Flask)
# =============================================================================

echo "============================================================"
echo "  Shutting down WebAMR Local Development Environment"
echo "============================================================"

# 1. Stop Docker container
CONTAINER_NAME="webamr-local-worker"
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "[1/2] Stopping worker container ($CONTAINER_NAME)..."
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    echo "Worker container stopped."
else
    echo "[1/2] No running worker container ($CONTAINER_NAME) found."
fi

# 2. Stop any Flask processes running WebAMR
PIDS=$(ps aux | grep -i -E "python.*flask.*main" | grep -v grep | awk '{print $2}')
if [ -n "$PIDS" ]; then
    echo "[2/2] Stopping Flask frontend processes (PIDs: $PIDS)..."
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    echo "Flask frontend processes terminated."
else
    echo "[2/2] No lingering Flask frontend processes found."
fi

echo "============================================================"
echo "  WebAMR shutdown complete."
echo "============================================================"
