#!/usr/bin/env bash
set -e

echo "Starting CortexForge Development Environment..."

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Please run: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[all]'"
    exit 1
fi

source .venv/bin/activate

echo "Running system diagnostics..."
cortex doctor

echo "Launching CortexForge REST API on http://127.0.0.1:8000..."
cortex serve --host 127.0.0.1 --port 8000
