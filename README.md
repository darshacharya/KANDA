# KANDA — Embodied AI Robot Agent

**KANDA** — Knowledge-driven Autonomous Navigation and Decision-making Agent

A mobile robot where **Gemini is the brain and the ESP32 is the body**. You speak to it naturally, it plans and acts using its camera, sensors, and motors.

---

## What KANDA can do

- **"Hey Kanda, go forward 2 seconds, turn left 5 seconds, reverse 3 seconds"** → executes the timed sequence
- **"Hey Kanda, find my water bottle"** → searches the room autonomously, reports when found
- **"Hey Kanda, what do you see?"** → describes the camera view out loud
- **"Hey Kanda, go near the door"** → plans and navigates
- **"Hey Kanda, stop"** → cancels anything immediately
- **Any natural language instruction** → Gemini plans the physical response using full body context (sensors + camera + history)

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           Raspberry Pi 4 (Brain)         │
                    │                                           │
  Earphone Mic ──── │ Wake Word → VAD Mic → Gemini ASR         │
                    │                   ↓                       │
  OV5647 Camera ─── │ Camera → VLM → Body Context              │
                    │                   ↓                       │
                    │ Task Agent → Gemini Planner               │
                    │    (intent: COMMAND / QUESTION / TASK)    │
                    │                   ↓                       │
                    │ Plan Executor → JSON commands             │
                    │                   ↓                       │
                    │ espeak-ng → Bluetooth Speaker             │
                    └────────────────┬────────────────────────-─┘
                                     │ USB Serial (JSON)
                    ┌────────────────▼─────────────────────────┐
                    │              ESP32 (Body)                  │
                    │                                           │
                    │  Motors ← TB6612FNG                       │
                    │  Sensors ← HC-SR04 × 3                    │
                    │  OLED ← Face animations per state         │
                    │  Safety stop ← obstacle < 15cm            │
                    └───────────────────────────────────────────┘
```

**States:** `IDLE` → `LISTENING` → `THINKING` → `ACTING / SEARCHING` → `SPEAKING` → `REPORTING` → `IDLE`

---

## Hardware

| Component | Purpose |
|-----------|---------|
| Raspberry Pi 4 (2GB+) | Brain — runs AI, camera, voice |
| ESP32 DevKit | Body — motors, sensors, OLED |
| RPi Camera Module v2.1 | Vision input (CSI ribbon) |
| Wired earphone with mic | Voice input (3.5mm jack) |
| Bluetooth speaker | Voice output |
| HC-SR04 × 3 | Front / Left / Right ultrasonic |
| SSD1306 OLED (128×64) | Animated face display |
| TB6612FNG | Dual motor driver |
| 2× LiPo + BMS | Motor power |
| Power bank (5V 2A+) | Raspberry Pi power |

---

## Quick Start

### Step 1 — Get API key (only one needed)

| Key | Where |
|-----|-------|
| **Gemini** | [aistudio.google.com](https://aistudio.google.com) → Get API key — free |

**Wake word uses openWakeWord — no account, no API key, fully offline.** Default wake phrase is **"Hey Jarvis"**. To use a different phrase, see the Wake Word section below.

> **No mic?** Set `KANDA_WAKE_WORD=0` and press **Enter** in the terminal to wake KANDA.

---

### Step 2 — Copy files to Raspberry Pi

From your Mac/PC:

```bash
# Replace raspberrypi.local with your Pi's IP if mDNS doesn't work
rsync -av kanda/vision_module/ pi@raspberrypi.local:~/kanda/
```

Or download the zip from GitHub and extract on the Pi.

---

### Step 3 — Run setup on the Pi

SSH into the Pi, then:

```bash
cd ~/kanda
chmod +x setup.sh
./setup.sh
```

This installs all system packages and Python dependencies automatically.

---

### Step 4 — Flash ESP32

**File to flash:** `vision_module/firmware_phase4.ino`

#### 4a — Install Arduino IDE

Download from [arduino.cc/en/software](https://www.arduino.cc/en/software) (v2.x recommended).

#### 4b — Add ESP32 board support

1. Open Arduino IDE → **File → Preferences**
2. Paste this URL into **"Additional boards manager URLs"**:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
3. Click OK
4. Go to **Tools → Board → Boards Manager**
5. Search **esp32**, install **"esp32 by Espressif Systems"** (v2.x)

#### 4c — Install required libraries

Go to **Tools → Manage Libraries**, search and install each:

| Library | Search term |
|---------|-------------|
| Adafruit GFX Library | `Adafruit GFX` |
| Adafruit SSD1306 | `Adafruit SSD1306` |
| ArduinoJson | `ArduinoJson` (by Benoit Blanchon) |

#### 4d — Board settings

Connect your ESP32 to your **Mac/PC** via USB, then set:

| Setting | Value |
|---------|-------|
| **Board** | `ESP32 Dev Module` |
| **Port** | the port that appears when ESP32 is plugged in (e.g. `/dev/cu.usbserial-0001` on Mac, `COM3` on Windows) |
| **Upload Speed** | `115200` |
| **Partition Scheme** | `Default 4MB with spiffs` |

#### 4e — Open and upload

1. Open `vision_module/firmware_phase4.ino` in Arduino IDE
2. Click **Upload** (→ arrow button)
3. Watch the console — you should see `Done uploading`
4. Open **Serial Monitor** (baud: `115200`) — you should see:
   ```
   KANDA ready — waiting for Pi commands
   F:30.5 L:21.8 R:16.4 -> STOP
   F:30.5 L:21.8 R:16.4 -> STOP
   ...
   ```
   > If you see sensor readings streaming — the firmware is working.

5. Close Arduino IDE, then connect ESP32 to Pi via USB cable

---

### Step 5 — Run KANDA

```bash
cd ~/kanda

# Required
export GEMINI_API_KEY=your_key_here

# Optional — skip for keyboard fallback
export PORCUPINE_ACCESS_KEY=your_key_here

python3 main.py
```

**Without ESP32** (Pi only, for testing):
```bash
KANDA_NO_UART=1 python3 main.py
```

---

### Step 6 — Talk to it

1. Say **"Hey Kanda"** (or press Enter if no Porcupine key)
2. KANDA says **"Yes?"** and opens its eyes on the OLED
3. Speak your command — recording stops automatically when you pause
4. KANDA thinks, plans, and acts

**Example commands:**
```
"go forward"
"go forward 3 seconds then turn left 2 seconds"
"find my water bottle"
"what do you see?"
"go near the table"
"dance"
"patrol the room"
"stop"
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | **Required.** Google AI Studio key |
| `KANDA_NO_UART` | `0` | Set to `1` to run without ESP32 |
| `KANDA_SERIAL_PORT` | `/dev/ttyUSB0` | ESP32 USB serial port |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Gemini model to use |
| `KANDA_WAKE_WORD` | `1` | Set to `0` to force keyboard fallback |
| `KANDA_WAKE_WORD_MODEL` | `hey_jarvis` | Wake phrase model (`hey_jarvis`, `alexa`, `hey_mycroft`, or path to `.onnx`) |
| `KANDA_WAKE_SENSITIVITY` | `0.5` | Wake word detection threshold (0.0–1.0) |
| `KANDA_VLM_INTERVAL` | `10.0` | Seconds between background scene descriptions |
| `KANDA_VAD_SILENCE` | `1.5` | Silence seconds before recording stops |
| `KANDA_VAD_MAX` | `8.0` | Max recording length in seconds |
| `KANDA_SEARCH_MAX_STEPS` | `20` | Max steps in a find-task search loop |
| `KANDA_GEMINI_TIMEOUT` | `15` | Seconds before Gemini call is abandoned |

---

## Wake Word

KANDA uses **openWakeWord** — fully open source, runs offline on Pi, no account needed.

### Built-in wake phrases (work immediately, no training)

| Say this | Set `KANDA_WAKE_WORD_MODEL` to |
|----------|-------------------------------|
| **"Hey Jarvis"** | `hey_jarvis` (default) |
| **"Alexa"** | `alexa` |
| **"Hey Mycroft"** | `hey_mycroft` |

### Train a custom "Hey Kanda" model (~10 minutes, optional)

```bash
pip install openwakeword[train]
oww-train --phrase "hey kanda" --output hey_kanda.onnx
export KANDA_WAKE_WORD_MODEL=hey_kanda.onnx
```

### Keyboard fallback (no mic needed)

```bash
KANDA_WAKE_WORD=0 python3 main.py
# Press Enter in terminal to wake KANDA
```

---

## Repository Structure

```
kanda/
├── vision_module/          ← All Pi code (copy this to Pi)
│   ├── main.py             ← Entry point — 7-state machine
│   ├── config.py           ← All settings and State enum
│   ├── body_context.py     ← Robot body awareness (sensors + scene + history)
│   ├── task_agent.py       ← Intent parser, planner, ReAct search loop
│   ├── plan_executor.py    ← Executes Gemini JSON plans step by step
│   ├── voice_command.py    ← Audio transcription (Gemini)
│   ├── wake_word.py        ← Porcupine wake word / keyboard fallback
│   ├── mic.py              ← VAD microphone recording
│   ├── speaker.py          ← TTS via espeak-ng → Bluetooth
│   ├── camera.py           ← RPi Camera v2.1 capture
│   ├── vlm.py              ← Vision-Language Model (Gemini)
│   ├── firmware_phase4.ino ← ESP32 firmware (flash via Arduino IDE)
│   ├── setup.sh            ← Pi setup script
│   └── requirements.txt    ← Python dependencies
├── ai_layer/               ← Earlier prototype (reference only)
├── firmware/               ← Phase 2 ESP32 firmware (reference)
└── docs/                   ← Pin config and wiring diagrams
```

---

## How it works — under the hood

Every time KANDA gets a voice command, Gemini receives the **full body context**:

```
ROBOT CAPABILITIES: forward/backward/left/right, camera, sensors, speaker...
CURRENT SENSORS:    front=30cm  left=45cm  right=22cm
CURRENT SCENE:      A wooden table with a laptop on it
RECENT ACTIONS:     forward 2s, left 0.8s, stop
USER INSTRUCTION:   "go forward 2 seconds then turn left 5 seconds"
```

Gemini responds with a **JSON plan**:

```json
[
  {"action": "forward",  "speed": 120, "duration_ms": 2000},
  {"action": "speak",    "text": "Moving forward"},
  {"action": "left",     "speed": 100, "duration_ms": 5000},
  {"action": "stop",     "speed": 0,   "duration_ms": 0}
]
```

The Pi executes each step, checking sensors and the cancel flag between steps. The ESP32 only ever receives simple one-action commands — all intelligence stays on the Pi.

---

## Troubleshooting

**No audio input detected**
```bash
python3 -c "import pyaudio; pa=pyaudio.PyAudio(); [print(i, pa.get_device_info_by_index(i)['name']) for i in range(pa.get_device_count())]"
```
Find your earphone mic index and set `KANDA_MIC_INDEX=N` (add to config if needed).

**ESP32 not found**
```bash
ls /dev/ttyUSB* /dev/ttyACM*
sudo chmod 666 /dev/ttyUSB0
```

**Camera not working**
```bash
libcamera-hello --timeout 2000
```

**espeak not producing sound**
```bash
pactl list sinks short   # check Bluetooth sink appears
espeak-ng "hello"        # test directly
```

**Test each module standalone**
```bash
python3 camera.py        # captures test_capture.jpg
python3 vlm.py           # describes a frame
python3 mic.py           # records test_vad.wav
python3 speaker.py       # speaks test phrases
python3 wake_word.py     # tests wake detection
python3 voice_command.py # transcription test
```

---

## OLED Face States

| What KANDA is doing | Face |
|---------------------|------|
| Idle / waiting | Two eyes, slow blink every 3s |
| Listening | Wide eyes, raised eyebrows |
| Thinking | Eyes scanning left and right |
| Acting (single command) | Squinted determined eyes |
| Searching (find task) | One eye squinted, one wide |
| Speaking | Eyes open, mouth opens/closes |
| Found it | Big smile |
| Couldn't find it | Sad face |
| Obstacle blocked | Wide eyes + ! |

---

## Project Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Conceptual Design | ✅ Done | Problem formulation, architecture proposal |
| 2 — Embodiment Layer | ✅ Done | ESP32 + motors + sensors + obstacle avoidance |
| 3 — Intelligence Layer | ✅ Done | Raspberry Pi + Gemini LLM + serial bridge |
| 4 — Multimodal Agent | ✅ Done | Vision + voice + wake word + task planning |

---

## License

MIT
