#!/bin/bash
set -e

# 1. Start Flask API background daemon on port 5000
python /app/api_service.py &

# 2. Start Streamlit worker on port 8502
streamlit run /app/app.py \
    --server.port 8502 \
    --server.address 127.0.0.1 \
    --server.headless true \
    --browser.gatherUsageStats false &

# 3. Start Nginx gateway on port 8501 (foreground)
nginx -g 'daemon off;'