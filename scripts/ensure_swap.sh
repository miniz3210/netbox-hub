#!/usr/bin/env bash
set -e
SWAP_SIZE=$(free -m | awk '/Swap:/ {print $2}')
if [ "$SWAP_SIZE" -lt 2048 ]; then
    echo "[SWAP SETUP] Insufficient swap detected ($SWAP_SIZE MB). Creating 4GB swapfile..."
    sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    fi
    echo "[SWAP SETUP] 4GB Swap successfully activated."
else
    echo "[SWAP SETUP] Swap already configured ($SWAP_SIZE MB). OK."
fi
