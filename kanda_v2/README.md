# KANDA v2

Embodied AI robot — event-driven, non-blocking architecture.

## Quick Start

```bash
# 1. Copy config
cp .env.example .env
# Edit .env with your API keys

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python3 main.py
```

## Architecture

- **asyncio event loop** — nothing blocks
- **Event bus** — all communication via typed events
- **3 input channels**: Microphone (wake word), Telegram, Web UI
- **HAL layer** — hardware abstraction (serial, motors, camera, speaker)
- **Brain** — Groq LLM for intent + planning, NVIDIA NIM for vision
- **Navigator** — frontier-based search with grid spatial memory

## Web UI

Access at `http://<pi-ip>:8080` for:
- D-pad motor controls
- Natural language commands (text + voice)
- Live sensor readout
- State indicator

## Deploy to Pi

```bash
./deploy.sh
```

## ESP32 Firmware

Flash `firmware/kanda_v2.ino` via Arduino IDE. Same wiring as phase4 — no changes needed.

## Project Structure

```
kanda_v2/
├── main.py          # Entry point
├── config.py        # Pydantic settings
├── event_bus.py     # Async event dispatcher
├── state_machine.py # State transitions
├── brain/           # LLM intent + planner
├── navigator/       # Search + spatial memory
├── hal/             # Hardware abstraction
├── inputs/          # Mic, Telegram, Web
├── outputs/         # Notifications, OLED
├── web_ui/          # HTML frontend
└── firmware/        # ESP32 Arduino sketch
```
