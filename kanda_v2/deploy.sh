#!/bin/bash
# KANDA v2 deployment script — push code to Raspberry Pi and restart
set -e

PI_HOST="${PI_HOST:-rasp@10.239.115.73}"
PI_DIR="${PI_DIR:-~/kanda_v2}"

echo "=== KANDA v2 Deploy ==="
echo "Target: $PI_HOST:$PI_DIR"

# Sync code (excluding .env, __pycache__, .git)
echo "→ Syncing code..."
rsync -az --delete \
    --exclude='.env' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='firmware/' \
    "$(dirname "$0")/" "$PI_HOST:$PI_DIR/"

# Install deps if requirements changed
echo "→ Checking dependencies..."
ssh "$PI_HOST" "cd $PI_DIR && pip install -q -r requirements.txt 2>/dev/null || pip3 install -q -r requirements.txt"

# Stop existing process
echo "→ Stopping existing KANDA..."
ssh "$PI_HOST" "pkill -f 'python3 main.py' 2>/dev/null || true"
sleep 2

# Start fresh
echo "→ Starting KANDA v2..."
ssh "$PI_HOST" "cd $PI_DIR && nohup python3 main.py > /tmp/kanda_v2.log 2>&1 &"
sleep 3

# Verify
if ssh "$PI_HOST" "pgrep -f 'python3 main.py' > /dev/null"; then
    echo "✓ KANDA v2 running"
    echo "  Logs: ssh $PI_HOST 'tail -f /tmp/kanda_v2.log'"
    echo "  Web:  http://$(echo $PI_HOST | cut -d@ -f2):8080"
else
    echo "✗ KANDA v2 failed to start!"
    ssh "$PI_HOST" "tail -20 /tmp/kanda_v2.log"
    exit 1
fi
