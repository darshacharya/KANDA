#!/bin/bash
# KANDA Phase 4 — Raspberry Pi Setup Script
# Run this ONCE on the Pi after transferring the files.
# Usage: chmod +x setup.sh && ./setup.sh

set -e

echo "=================================================="
echo "  KANDA Phase 4 — Pi Setup"
echo "=================================================="
echo ""

# ── System packages ───────────────────────────────────
echo "[1/5] Installing system packages..."
sudo apt update -qq
sudo apt install -y \
    python3-picamera2 \
    python3-pyaudio \
    python3-pip \
    espeak-ng \
    portaudio19-dev \
    pulseaudio-module-bluetooth \
    python3-serial \
    git

# ── Python packages ───────────────────────────────────
echo ""
echo "[2/5] Installing Python packages..."
pip install --break-system-packages -r requirements.txt

# ── Serial port permission (ESP32 USB) ────────────────
echo ""
echo "[3/5] Setting up serial port access..."
sudo usermod -aG dialout "$USER"
# Immediate permission for this session
sudo chmod 666 /dev/ttyUSB0 2>/dev/null || true
echo "  Serial port ready (reconnect SSH if 'permission denied' persists)"

# ── Verify camera ─────────────────────────────────────
echo ""
echo "[4/5] Checking camera..."
if libcamera-hello --list-cameras 2>/dev/null | grep -q "Available"; then
    echo "  Camera detected OK"
else
    echo "  Camera not detected — check CSI ribbon cable"
fi

# ── List audio devices ────────────────────────────────
echo ""
echo "[5/5] Checking audio devices..."
python3 -c "
import pyaudio
pa = pyaudio.PyAudio()
print('  Input devices found:')
found = False
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info['maxInputChannels'] > 0:
        print(f'    [{i}] {info[\"name\"]}')
        found = True
if not found:
    print('    (none — plug in earphone and rerun)')
pa.terminate()
" 2>/dev/null || echo "  Could not list devices yet"

# ── Done ──────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  Setup complete!"
echo "=================================================="
echo ""
echo "BEFORE RUNNING — set your API keys:"
echo ""
echo "  export GROQ_API_KEY=your_key_here"
echo "  export NVIDIA_API_KEY=your_key_here"
echo "  # (or put them in .env file — already there if you copied it)"
echo ""
echo "TEST each module individually first:"
echo "  python3 camera.py        → saves test_capture.jpg"
echo "  python3 mic.py           → saves test_vad.wav"
echo "  python3 speaker.py       → plays test phrases"
echo "  python3 vlm.py           → describes a camera frame"
echo "  python3 voice_command.py → transcription test"
echo "  python3 wake_word.py     → wake word test"
echo ""
echo "RUN the full system:"
echo "  python3 main.py                     # with ESP32 via USB"
echo "  KANDA_NO_UART=1 python3 main.py     # no ESP32 (Pi only)"
echo ""
echo "Wake KANDA: say 'Hey Kanda' OR press Enter (keyboard fallback)"
