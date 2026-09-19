#!/bin/bash
# Quick diagnostic script for NetBox Hub Docker deployment

echo "======================================"
echo "NetBox Hub Diagnostic"
echo "======================================"
echo ""

echo "1. Checking if code changes are present..."
if grep -q "save_azure_csv_upload" /opt/netbox-hub/ui/tabs/azure_tab.py; then
    echo "✅ Code changes detected in azure_tab.py"
else
    echo "❌ Code changes NOT found - rebuild may not have included changes"
fi

if [ -f /opt/netbox-hub/core/azure_csv_manager.py ]; then
    echo "✅ azure_csv_manager.py exists"
else
    echo "❌ azure_csv_manager.py missing"
fi

echo ""
echo "2. Checking Python processes..."
ps aux | grep -E "streamlit|python" | grep -v grep || echo "❌ No Python/Streamlit processes running"

echo ""
echo "3. Checking port 8501..."
netstat -tlnp 2>/dev/null | grep 8501 || ss -tlnp 2>/dev/null | grep 8501 || echo "❌ Port 8501 not listening"

echo ""
echo "4. Checking database table..."
if command -v sqlite3 &> /dev/null; then
    if [ -f /opt/netbox-hub/data/netbox_hub.db ]; then
        echo "Database exists, checking for table..."
        sqlite3 /opt/netbox-hub/data/netbox_hub.db "SELECT name FROM sqlite_master WHERE type='table' AND name='azure_csv_uploads';" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "✅ azure_csv_uploads table exists"
        else
            echo "⚠️  Could not verify table"
        fi
    else
        echo "⚠️  Database file not found yet"
    fi
else
    echo "⚠️  sqlite3 not available for verification"
fi

echo ""
echo "======================================"
echo "Next steps:"
echo "1. Exit this container: exit"
echo "2. From Docker host, restart: docker restart netbox-hub"
echo "3. Watch logs: docker logs -f netbox-hub"
echo "======================================"
