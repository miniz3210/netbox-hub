#!/bin/bash
set -e

mkdir -p /app/data/catalog_cache /app/.streamlit /opt/netbox-hub/data/catalog_cache

echo "Starting Flask API Service..."
python api_service.py &

echo "Starting Streamlit UI on port 8501..."
exec streamlit run app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.enableCORS false \
    --server.enableXsrfProtection false
