# KANDA — Multimodal Embodied Robot Agent

**KANDA** stands for **Knowledge-driven Autonomous Navigation and Decision-making Agent**.

A hardware-grounded robotic agent being built in phases, evolving from a
rule-based autonomous controller into a fully LLM-driven embodied AI system.

---

## Project Phases

### Phase 1 — Conceptual Design ✅
- Problem formulation and literature review
- Identified gap: existing systems are rule-based with no AI reasoning
- Proposed architecture: multimodal robot + LLM + closed-loop decision making

### Phase 2 — Embodiment Layer (Hardware + Control) ✅ ← Current
- ESP32 + TB6612FNG + 3× HC-SR04 + SSD1306 OLED
- Stable power architecture (battery → BMS → buck → ESP32, direct to motors)
- Voltage dividers on ECHO pins (5V → ~2.5V)
- Clean pin mapping (no conflicts, no strapping pins)
- Obstacle avoidance: Sense → Decide → Act → Display loop
- Real-time OLED feedback

### Phase 3 — Intelligence Layer (LLM Integration) 🔜
- Add Raspberry Pi as the brain (ESP32 remains the body/executor)
- Add camera, microphone, speaker
- Raspberry Pi → LLM API → structured JSON command → ESP32 serial
- Pipeline: Input → Context → LLM Reasoning → Safety Check → Action

### Phase 4 — Multimodal Agent 🔜
- Vision input (camera frames)
- Audio input/output (mic + speaker)
- Full embodied agent loop with memory and task planning

---

## Current Architecture (Phase 2)

```
HC-SR04 (×3)
    │ distances
    ▼
  ESP32  ──────→  TB6612FNG  ──→  Motors
    │
    ▼
 SSD1306 OLED
```

**Loop:** Sense → Decide (rule-based) → Act → Display

---

## Target Architecture (Phase 3+)

```
Camera / Mic / Sensors
        │
        ▼
   Raspberry Pi
        │  context
        ▼
   LLM (API call)
        │  JSON command
        ▼
   Safety Validator
        │
        ▼
      ESP32  ──→  Motors / Actuators
```

**ESP32 = Body (execution)**
**Raspberry Pi = Brain (reasoning)**

---

## Repository Structure

```
kanda/
├── firmware/
│   └── phase2_obstacle_avoidance/
│       └── phase2_obstacle_avoidance.ino   ← current working firmware
├── docs/
│   └── pin_config.md                       ← full pin mapping + power diagram
├── ai_layer/                               ← Raspberry Pi: Gemini + UART bridge
└── README.md
```

### AI layer (Raspberry Pi)

- Default LLM: **Gemini 2.5 Flash** (`gemini-2.5-flash`). Override with `GEMINI_MODEL` if needed.
- Requires `GEMINI_API_KEY` and serial to the ESP32 (`KANDA_SERIAL_PORT`, default `/dev/ttyS0`).
- **Alignment:** prompts describe hardware limits; outputs are JSON-only and pass through a validator before motors move.

---

## Hardware Components

| Component         | Role                          |
|-------------------|-------------------------------|
| ESP32 DevKit      | Microcontroller / executor    |
| TB6612FNG         | Dual motor driver             |
| HC-SR04 × 3       | Ultrasonic distance sensors   |
| SSD1306 OLED      | Real-time display             |
| LiPo + BMS        | Power supply                  |
| Buck Converter    | 5V regulated supply for ESP32 |

---

## Key Engineering Decisions

| Decision | Reasoning |
|----------|-----------|
| Input-only pins (34, 35) for ECHO | Avoid GPIO conflicts, input-only = clean signal |
| 1kΩ+1kΩ voltage divider on ECHO | No 2kΩ available; 2.5V output is safe and functional |
| Motors NOT through buck converter | Buck can't handle motor current; direct battery feed |
| `ledcAttach()` not `ledcSetup()` | ESP32 Arduino core v3 API change |
| `Wire.begin(21, 22)` explicit call | Required for I2C to initialize correctly on ESP32 |

---

## Current Limitations

- No AI reasoning — purely reactive rule-based logic
- No vision or audio input
- Cannot adapt to novel situations beyond if/else thresholds
- This is a **foundation**, not the final system

---

## Next Step

**Raspberry Pi integration** — serial bridge between Pi and ESP32,
enabling LLM-generated commands to drive physical movement.
